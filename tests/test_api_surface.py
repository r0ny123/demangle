"""The API shapes a Python caller expects to find, and used not to.

Three of them, and each had a working implementation already -- inside the command, or
inside a private helper -- which is the difference between a library and a library with a
CLI stapled on.

* The word-scanning filter, so an objdump listing or a crash log can be demangled without
  shelling out to our own command. rustc-demangle ships `demangle_stream` as a crate
  function rather than only inside `rustfilt`; the precedent is clear.
* The tree as data: `to_dict()`, `__match_args__`, and a published list of the `kind`
  strings to switch on. `swift-demangle` prints `kind=` dumps and libiberty publishes a
  hundred `DEMANGLE_COMPONENT_*` enumerators, because "returns a walkable tree" is not an
  interface until the vocabulary is written down.
* Where in a longer string a symbol was, which `microsoftDemangle`'s `n_read`
  out-parameter and `llvm-undname --warn-trailing` answer in C. `find_symbols` answers it
  here, and answers it for every scheme at once.
"""

import io
import itertools
import json
import pathlib
import time

import pytest

import demangle
from demangle.core.ast import Builtin, Function, Name, Pointer

from .conftest import load_corpus

VECTOR = "_ZNSt6vectorIiSaIiEE9push_backERKi"
VECTOR_SPELLED = "std::vector<int, std::allocator<int>>::push_back(int const&)"


class TestTheStreamFilter:
    def test_a_symbol_inside_a_line_of_other_text(self):
        assert demangle.demangle_text("0000000000001139 T _ZN3foo3barEv") == "0000000000001139 T foo::bar()"

    def test_text_with_no_symbols_comes_back_identical(self):
        """Identical, not merely equal: nothing is rebuilt when nothing changed."""
        text = "the quick brown fox\n"
        assert demangle.demangle_text(text) is text

    def test_an_ordinary_word_that_is_a_valid_type_is_left_alone(self):
        """`demumble`'s warning: `I like Pi` must not become `I like int*`."""
        assert demangle.demangle_text("I like Pi") == "I like Pi"

    def test_several_schemes_in_one_line(self):
        line = f"{VECTOR} and ?f@@YAXH@Z and _RNvC6_123foo3bar"
        assert demangle.demangle_text(line) == f"{VECTOR_SPELLED} and void __cdecl f(int) and 123foo::bar"

    def test_it_never_raises_whatever_the_text_holds(self):
        for text in ("", "\x00\x01", "_Z" + "N" * 400, "?" * 300, "\ud800"):
            assert isinstance(demangle.demangle_text(text), str)

    def test_the_stream_form_is_the_text_form_a_line_at_a_time(self):
        source = f"a {VECTOR} b\nc\n\nd ?f@@YAXH@Z\n"
        out = io.StringIO()
        demangle.demangle_stream(io.StringIO(source), out)
        assert out.getvalue() == "".join(demangle.demangle_text(line) for line in source.splitlines(keepends=True))

    def test_the_style_reaches_it(self):
        assert demangle.demangle_text(VECTOR, style="gnu").endswith("std::allocator<int> >::push_back(int const&)")

    def test_the_command_and_the_library_scan_alike(self):
        """One tokenizer. Two would drift, and the drift would be silent."""
        from demangle import cli
        from demangle import filter as filter_module

        assert cli._TOKEN is filter_module.TOKEN
        assert cli._TOKEN_MUST_HOLD is filter_module.TOKEN_MUST_HOLD


class TestFindingWhereASymbolWas:
    def test_the_spans_locate_the_names(self):
        text = f"x {VECTOR} y ?f@@YAXH@Z"
        found = list(demangle.find_symbols(text))
        assert [text[item.start : item.end] for item in found] == [VECTOR, "?f@@YAXH@Z"]
        assert [item.mangled for item in found] == [VECTOR, "?f@@YAXH@Z"]
        assert found[0].demangled == VECTOR_SPELLED

    def test_a_word_that_does_not_demangle_is_not_a_symbol(self):
        """It looks symbol-shaped and it is not one, so reporting it would be a lie."""
        assert list(demangle.find_symbols("_not_a_symbol __also_not")) == []

    def test_the_spans_are_in_order_and_do_not_overlap(self):
        text = " ".join([VECTOR, "?f@@YAXH@Z", "_ZN3foo3barEv"])
        found = list(demangle.find_symbols(text))
        assert len(found) == 3
        assert all(a.end <= b.start for a, b in itertools.pairwise(found))

    def test_rebuilding_around_the_spans_is_what_the_filter_does(self):
        text = f"lead {VECTOR} tail"
        pieces, end = [], 0
        for item in demangle.find_symbols(text):
            pieces += [text[end : item.start], item.demangled]
            end = item.end
        pieces.append(text[end:])
        assert "".join(pieces) == demangle.demangle_text(text)


class TestTheTreeAsData:
    def test_to_dict_names_the_role_of_every_child(self):
        assert demangle.parse("_Z1fPKc").to_dict() == {
            "kind": "function",
            "name": {"kind": "name", "text": "f"},
            "parameters": [
                {
                    "kind": "pointer",
                    "inner": {
                        "kind": "qualify",
                        "inner": {"kind": "builtin", "spelling": "char"},
                        "qualifiers": ["const"],
                    },
                }
            ],
            "returns": None,
            "suffix": "",
        }

    def test_it_is_json(self):
        assert json.loads(json.dumps(demangle.parse(VECTOR).to_dict()))["kind"] == "function"

    def test_a_node_reached_twice_is_written_once_and_referred_to(self):
        """`S_` is the same node in both parameters, and saying so is what terminates.

        The structure is a graph: Itanium's substitutions make one component reachable
        from several places, and a Rust node names its children twice over -- `parts`
        orders them, `base` and `arguments` say what they are. Written out in full at
        every occurrence it doubles per level, and one real Rust toolchain symbol cost
        4.7 seconds and 363MB before this.
        """
        assert demangle.parse("_Z1fPiS_").to_dict() == {
            "kind": "function",
            "name": {"kind": "name", "text": "f"},
            "parameters": [
                {"kind": "pointer", "id": 0, "inner": {"kind": "builtin", "spelling": "int"}},
                {"$ref": 0},
            ],
            "returns": None,
            "suffix": "",
        }

    def test_nothing_is_shared_when_nothing_repeats(self):
        """No `id` and no `$ref` in the ordinary case, so the common shape stays a tree."""
        text = json.dumps(demangle.parse("_Z1fPKc").to_dict())
        assert '"id"' not in text
        assert "$ref" not in text

    def test_a_symbol_whose_expansion_would_not_terminate_still_does(self):
        """Bounded by the graph rather than by its expansion. Under a second, not five."""
        mangled = next(
            m
            for m, _ in load_corpus("rust-real-world.txt")
            if m.startswith("_RINvMsg_NtNtNtCs8nPosuWgJFh_4core4iter8adapters7flatten")
        )
        start = time.perf_counter()
        json.dumps(demangle.parse(mangled).to_dict())
        assert time.perf_counter() - start < 2

    @pytest.mark.parametrize("corpus", ["itanium-real-world.txt", "msvc-llvm-corpus.txt", "rust-real-world.txt"])
    def test_every_tree_in_a_corpus_serialises(self, corpus):
        for mangled, _ in load_corpus(corpus):
            try:
                tree = demangle.parse(mangled)
            except demangle.DemanglingError:
                continue
            json.dumps(tree.to_dict())

    def test_structural_pattern_matching_over_the_nodes(self):
        """`match Pointer(Builtin(name))` is what a walkable tree means in Python now."""
        parameter = next(demangle.parse("_Z1fPi").find("pointer"))
        match parameter:
            case Pointer(Builtin(spelling)):
                assert spelling == "int"
            case _:
                pytest.fail(f"no match for {parameter!r}")

    def test_match_args_follow_the_constructor_not_the_slots(self):
        """`Array(inner, dimension)`; the slots happen to be alphabetical."""
        from demangle.core.ast import Array

        assert Array.__match_args__ == ("inner", "dimension")
        assert Function.__match_args__ == ("returns", "parameters", "suffix", "name")
        assert Name.__match_args__ == ("text",)

    def test_no_declaration_disagrees_with_the_constructor_it_names(self):
        """Written out on the core nodes for a type checker, derived on a scheme's own.

        A caller's `match Pointer(inner)` is checked statically against a class attribute,
        so a tuple computed in `__init_subclass__` is invisible where it matters -- and a
        tuple written by hand is a second copy of the constructor's parameter list. Both
        exist, and this is what keeps them the same.
        """
        from demangle.core.ast import Node, _match_args
        from demangle.core.registry import available

        list(available())  # every scheme's node classes, so subclasses() is complete

        def descendants(cls):
            for sub in cls.__subclasses__():
                yield sub
                yield from descendants(sub)

        checked = 0
        for cls in descendants(Node):
            assert cls.__match_args__ == _match_args(cls), cls
            checked += 1
        assert checked > 40, "the scheme node classes were not imported"

    def test_a_constructor_parameter_that_is_not_an_attribute_disables_matching(self):
        """Better no positional matching than positions that mean the wrong thing."""
        from demangle.core.ast import Literal

        assert Literal.__match_args__ == ("type", "value")


class TestThePublishedVocabulary:
    def test_every_kind_a_corpus_produces_is_published(self):
        """The list cannot rot: the corpora are what keep it honest."""
        directory = pathlib.Path(__file__).parent / "conformance"
        for path in sorted(directory.glob("*.txt")):
            for mangled, _ in load_corpus(path.name):
                language = demangle.detect(mangled)
                if language is None:
                    continue
                try:
                    tree = demangle.parse(mangled)
                except demangle.DemanglingError:
                    continue
                published = set(demangle.node_kinds(language))
                for node in tree.walk():
                    assert node.kind in published, f"{language}: {node.kind} from {mangled}"

    def test_every_scheme_publishes_something(self):
        for language in demangle.languages():
            assert demangle.node_kinds(language), language

    def test_the_whole_vocabulary_is_the_union(self):
        union = {kind for language in demangle.languages() for kind in demangle.node_kinds(language)}
        assert set(demangle.node_kinds()) == union

    def test_an_unknown_scheme_says_so(self):
        with pytest.raises(ValueError, match="unknown language"):
            demangle.node_kinds("klingon")
