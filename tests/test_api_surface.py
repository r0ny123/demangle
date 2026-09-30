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
from typing import Any

import pytest

import demangle
from demangle.core.ast import Builtin, Function, Name, Pointer
from demangle.core.errors import DemanglingError

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

    def test_a_bad_language_or_style_is_refused_before_any_symbol_is_seen(self):
        """Plain prose never reaches `demangle()`, so a typo'd argument must be refused
        up front, not pass silently until the first line that happens to hold a symbol."""
        bad: list[dict[str, Any]] = [{"language": "cobol"}, {"style": "nonexistent"}]
        for arguments in bad:
            with pytest.raises(ValueError):
                demangle.demangle_text("hello world", **arguments)
            with pytest.raises(ValueError):
                demangle.find_symbols("hello world", **arguments)
            with pytest.raises(ValueError):
                demangle.demangle_stream(io.StringIO("hello world\n"), io.StringIO(), **arguments)

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


class TestAReadingThatSaysOnlyWhatTheWordSays:
    """`@Override` is not a symbol, whatever a symbol table would make of it.

    `@Name` is a real Delphi symbol -- a unit-scope routine, which Embarcadero's own
    `tdump -um` reads as `Name` -- and `@@Name` is a runtime linker procedure, read as
    `__linkproc__ Name`. 39 of them are in the Delphi corpora, so `demangle()` reads
    them: there the caller has said the word is a name. `find_symbols` is guessing, and
    it is run over whole files. `@Override public void f()` came back
    `Override public void f()`; a Swift signature this library had *just printed* came
    back with its `@escaping` and `@autoclosure` shaved off; and
    `typeinfo for X const*@@CXXABI_FLOAT128` came back with `__linkproc__ CXXABI_FLOAT128`
    where the ELF version had been.

    What those readings have in common is that the identifier survives them whole -- all
    they add is the marker's name, or nothing at all. The caller can see the identifier
    already and cannot see whether it was an annotation, so the filter declines them. A
    reading that says more is untouched, which is why the 475 `._OBJC_CLASS_*` names in
    the corpus are still found.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "@Override public void f()",
            "use @property here",
            "swift @escaping closure",
            "@safe pure nothrow",
            "see @param x",
            "func(@autoclosure () -> Int)",
            "0000000000001139 T @AddCustomAttrib",
            ".text and .rodata",
            "@@AsClass is a linker procedure",
            "std::cout const*@@CXXABI_FLOAT128",
            "void f()@GLIBCXX_3.4",
            "(@Swift.MainActor () -> A)?",
        ],
    )
    def test_the_filter_leaves_it_alone(self, text):
        assert demangle.demangle_text(text) == text
        assert list(demangle.find_symbols(text)) == []

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [("@AddCustomAttrib", "AddCustomAttrib"), ("@@AsClass", "__linkproc__ AsClass")],
    )
    def test_demangle_still_reads_it_when_asked(self, mangled, expected):
        """The caller has said it is a symbol; the filter had to decide for itself."""
        assert demangle.demangle(mangled) == expected

    def test_a_reading_that_says_more_is_still_found(self):
        assert demangle.demangle_text("lead ._OBJC_CLASS_Alpha319 tail") == ("lead Objective-C class Alpha319 tail")

    def test_a_delphi_name_that_says_more_than_the_marker_is_still_found(self):
        name = "@%TAutoDriver$24Shdocvw_tlb@IWebBrowser2%@$bnot$xqv"
        assert demangle.demangle_text(f"lead {name} tail") == (
            "lead TAutoDriver<Shdocvw_tlb::IWebBrowser2>::operator !() const tail"
        )


class TestTheCharactersAMangledNameMayHold:
    """The token is as wide as real names need and no wider, measured both ways round.

    `%` and `#` are Delphi's -- a template argument list is `%...%` and a
    virtual-method-table flag is `#$cf$`. Without them the tokeniser cut 638 readable
    names of the corpora in half and handed back the pieces that happened to read, which
    is worse than handing back nothing: `@$bnot$xqv` out of the middle of a name reads as
    `operator !() const`, a real declaration belonging to a class the fragment no longer
    names.

    `<` and `>` are not in it, though MSVC writes `<unnamed-type-a>` and `<lambda_0>`.
    objdump spells a call target `call 1050 <_ZN3foo3barEv>`; a token that takes the
    brackets in is one no scheme reads, so admitting them to recover 99 corpus names
    would lose the symbol in the listing this module exists to filter.
    """

    DELPHI = "@%TAutoDriver$24Shdocvw_tlb@IWebBrowser2%@$bnot$xqv"

    def test_a_delphi_name_is_one_token(self):
        found = list(demangle.find_symbols(f"lead {self.DELPHI} tail"))
        assert [item.mangled for item in found] == [self.DELPHI]
        assert found[0].demangled == "TAutoDriver<Shdocvw_tlb::IWebBrowser2>::operator !() const"

    def test_a_delphi_flag_is_one_token(self):
        name = "@f@#$cf$@bar"
        found = list(demangle.find_symbols(f"lead {name} tail"))
        assert [item.mangled for item in found] == [name]
        assert found[0].demangled == "f::__vdflg__ bar"

    def test_an_objdump_call_target_is_still_found_inside_its_brackets(self):
        line = "  4011a6:\tcall   1050 <_ZN3foo3barEv>"
        assert demangle.demangle_text(line) == "  4011a6:\tcall   1050 <foo::bar()>"


class TestTokenisingANameWithASpaceInIt:
    """An Objective-C method is one name, not two words.

    `+[Alpha copy_it:]` is a class and a selector with a space between them, and the
    token pattern is otherwise a run of word characters -- so it was offered as `+[Alpha`
    and `copy_it:]`, which is the wrong reading of one name rather than the right reading
    of two. What a symbol *table* holds is the mangled form, which has no space and has
    always been found; this is about a listing that already carries readable ones.
    """

    def test_a_method_name_is_one_token(self):
        from demangle.filter import TOKEN

        text = "call +[Alpha copy_it:] here"
        assert [m.group() for m in TOKEN.finditer(text)] == ["call", "+[Alpha copy_it:]", "here"]

    def test_a_readable_method_name_is_left_alone_whole(self):
        # It spells itself, so it is not a symbol `find_symbols` reports -- but it must be
        # declined as one name, and `demangle_text` must copy it through unchanged.
        text = "call +[A_B209(Store247) andThen:do:] here"
        assert list(demangle.find_symbols(text)) == []
        assert demangle.demangle_text(text) == text

    def test_the_mangled_form_is_still_what_gets_found(self):
        text = "see -[Foo bar:] and _i_Alpha319_copy_it_ ok"
        found = list(demangle.find_symbols(text))
        assert [item.mangled for item in found] == ["_i_Alpha319_copy_it_"]
        assert found[0].demangled == "-[Alpha319(copy) it:]"

    def test_prose_is_not_swallowed_by_the_bracket_form(self):
        # The alternative needs `+[` or `-[` with nothing between, so arithmetic and
        # ordinary indexing do not form one.
        for text in ("a[i] - b[j]", "x = y - [1]", "count += [n]"):
            assert demangle.demangle_text(text) == text


class TestAStyleComposedForOneCall:
    """`parse()` must accept the style objects `demangle()` accepts.

    The tree builder was held by style *name*. A style composed with
    `demangle.style(...)` keeps the name it was based on, so it was silently served the
    registered style's builder; and a style whose name is not registered at all made
    `parse()` raise `unknown style` from inside the parser, for an object `demangle()`
    was perfectly happy with.
    """

    def test_a_style_whose_name_is_not_registered_still_parses(self):
        from demangle.core.style import SPELLING_BUILDER, Style

        # `_ZNK1AcviEv` is a conversion operator: the parser flattens the type it names
        # to text while building the tree, which is what reaches for the style.
        unregistered = Style(name="not-registered-anywhere", spelling_builder=SPELLING_BUILDER)
        tree = demangle.parse("_ZNK1AcviEv", style=unregistered)
        assert tree.spell(style=unregistered) == "A::operator int() const"
        assert demangle.demangle("_ZNK1AcviEv", style=unregistered) == "A::operator int() const"

    def test_a_composed_style_is_not_served_the_registered_one_s_builder(self):
        narrow = demangle.style("llvm", msvc={"calling_convention": False})
        assert narrow.name == "llvm"  # the composition keeps the base's name
        assert demangle.parse("?f@@YAXH@Z", style=narrow).spell(style=narrow) == "void f(int)"
        assert demangle.parse("?f@@YAXH@Z").spell() == "void __cdecl f(int)"

    def test_the_common_path_still_shares_one_builder(self):
        from demangle.core.ast import builder_for

        assert builder_for("llvm") is builder_for("llvm")
        assert builder_for(None) is builder_for(None)


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
                        # `cv` says these are cv-qualifiers rather than `_Complex` or
                        # `_Imaginary`, which arrive through the same node and do not
                        # collapse when repeated. A consumer rebuilding the tree needs
                        # it, so it is in the dict.
                        "inner": {"kind": "builtin", "spelling": "char"},
                        "qualifiers": ["const"],
                        "cv": True,
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


@pytest.mark.sweep
class TestEveryNodeAnswersTheNodeProtocol:
    """`walk()` hands a caller nodes, and every one of them has to be usable.

    The methods a caller reaches for on a node are `spell()`, `render()` and
    `to_dict()`, and the trees come from fourteen schemes with their own node classes.
    Checked on every node of every corpus tree rather than on a sample of kinds, because
    what goes wrong here is a class nobody thought to check -- and one did.

    `render()` is declared by `Node` and defined for every node class. The
    schemes whose spelling does not fit C++ declarator syntax carry their fragments as
    text and render by concatenating them, so their own tests use it. A Rust tree with an
    ELF version suffix wraps it in a `Decorated`, which must answer too, or walking a
    tree and asking each node for its text raises `AttributeError` partway through.
    This pins that `render()` agrees with `spell()` everywhere, in both styles, over all
    the nodes of the corpora.
    """

    def _sampled(self, step):
        directory = pathlib.Path(__file__).parent / "conformance"
        names = []
        for path in sorted([*directory.glob("*.txt"), *directory.glob("reported/*.txt")]):
            names.extend(name for name, _ in load_corpus(path.relative_to(directory).as_posix())[::step])
        return names

    @pytest.mark.parametrize("style", ["llvm", "gnu"])
    def test_spell_render_and_to_dict_work_on_every_node(self, style, subtests):
        names = self._sampled(5)
        assert len(names) > 5000, "corpora did not load; this test would prove nothing"
        checked = 0
        for mangled in names:
            try:
                tree = demangle.parse(mangled, style=style)
            except DemanglingError:
                continue
            with subtests.test(name=mangled):
                for node in tree.walk():
                    checked += 1
                    assert isinstance(node.spell(), str)
                    assert isinstance(node.render(), str)
                    assert node.render() == node.spell()
                    node.to_dict()
        assert checked > 50_000, checked


class TestThePublishedVocabulary:
    @pytest.mark.sweep
    def test_every_kind_a_corpus_produces_is_published(self):
        """The list cannot rot: the corpora are what keep it honest."""
        directory = pathlib.Path(__file__).parent / "conformance"
        for path in sorted([*directory.glob("*.txt"), *directory.glob("reported/*.txt")]):
            for mangled, _ in load_corpus(path.relative_to(directory).as_posix()):
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
