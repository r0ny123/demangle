"""The public API contract.

These are the promises callers build on, so they are tested as promises rather than
through whatever happens to exercise them.
"""

import io
import pathlib
import subprocess
import sys
from typing import Any

import pytest

import demangle

from .conftest import corpus_files, load_corpus


class TestBestEffortContract:
    """`demangle()` never raises and never loses information."""

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "memcpy",
            "main",
            "_Z",
            "_ZZZZZ",
            "?",
            "??",
            "_R",
            "not a symbol at all",
            "_Z1fPKcE\x00trailing",
            "\u00ff\u00fe\u00fd",
            "_" * 500,
            "_Z" + "P" * 300 + "i",
        ],
    )
    def test_never_raises(self, value):
        assert isinstance(demangle.demangle(value), str)

    def test_unreadable_names_come_back_unchanged(self):
        # A wrong expansion is worse than a mangled name: it matches neither the symbol
        # nor the declaration, so every downstream lookup that trusted it breaks.
        for value in ["memcpy", "not_mangled", "_Znonsense"]:
            assert demangle.demangle(value) == value

    def test_empty_input_is_returned_not_rejected(self):
        assert demangle.demangle("") == ""


class TestStrictContract:
    def test_raises_for_unmangled_names(self):
        with pytest.raises(demangle.NotMangledError):
            demangle.demangle_strict("memcpy")

    def test_raises_for_malformed_names(self):
        with pytest.raises(demangle.DemanglingError):
            demangle.demangle_strict("_ZN3Foo")

    def test_every_error_is_a_demangling_error(self):
        for value in ["memcpy", "_ZN3Foo", "?nonsense@@"]:
            with pytest.raises(demangle.DemanglingError):
                demangle.demangle_strict(value)


class TestDetection:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("_ZNSt6vectorIiSaIiEE9push_backERKi", "itanium"),
            ("?f@@YAXH@Z", "msvc"),
            ("_RNvC6_123foo3bar", "rust"),
            ("_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E", "rust"),
            ("memcpy", None),
            ("", None),
            # Itanium `parse` does not read GNU's `_GLOBAL__` names, so `detect` must not
            # claim them.
            ("_GLOBAL__sub_I_main", None),
        ],
    )
    def test_detect(self, name, expected):
        assert demangle.detect(name) == expected

    def test_legacy_rust_is_not_claimed_by_itanium(self):
        """Legacy Rust mangling *is* Itanium mangling, so ordering decides."""
        rust = "_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E"
        assert demangle.detect(rust) == "rust"
        assert demangle.demangle(rust) == "core::fmt::Formatter::pad"

    def test_c_plus_plus_symbols_are_not_claimed_by_rust(self):
        assert demangle.detect("_ZN3Foo3barEv") == "itanium"

    @pytest.mark.parametrize(
        "name", [".refptr.foo", ".text", ".L123", ".constprop.0", "/Users/me/build/foo.o", "/home/me/x/y.F"]
    )
    def test_a_section_label_or_file_is_not_claimed(self, name):
        assert demangle.detect(name) is None
        assert demangle.demangle(name) == name

    @pytest.mark.sweep
    def test_tightening_msvc_and_go_changed_no_corpus_name_s_answer(self, monkeypatch):
        """Each claim was narrowed by a test of its own; with that test made to pass every
        name, as it did before, every corpus name is answered as it is now."""
        from demangle.core.registry import get

        names = [mangled for corpus in corpus_files() for mangled, _ in load_corpus(corpus)]
        assert len(names) > 90_000
        now = [demangle.detect(name) for name in names]
        get("msvc"), get("go")
        monkeypatch.setattr("demangle.schemes.msvc._opens_a_type", lambda name: True)
        monkeypatch.setattr("demangle.schemes.go._is_absolute", lambda name: False)
        before = [demangle.detect(name) for name in names]
        assert [
            (name, was, answer) for name, was, answer in zip(names, before, now, strict=True) if was != answer
        ] == []


class TestStrictDetection:
    """`detect(name, strict=True)`: which scheme reads the name, not which claims it."""

    @pytest.mark.parametrize(
        "name,claimed",
        [("_ZN3Foo", "itanium"), ("?nonsense@@", "msvc"), ("_D88", "d"), ("_TtZZ", "swift")],
    )
    def test_a_name_claimed_but_unreadable_is_none(self, name, claimed):
        assert demangle.detect(name) == claimed
        assert demangle.detect(name, strict=True) is None
        assert demangle.detectb(name.encode(), strict=True) is None

    @pytest.mark.parametrize("name", ["_ZN3foo3barEv", "?f@@YAXH@Z", "$s10Foundation4DataV5countSivg", ".PEAX"])
    def test_a_readable_name_is_its_reader_s(self, name):
        assert demangle.detect(name, strict=True) == demangle.detect(name)

    def test_it_reads_under_the_allow_list(self):
        rust = "_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E"
        assert demangle.detect(rust, strict=True) == "rust"
        assert demangle.detect(rust, language=["itanium"], strict=True) == "itanium"
        assert demangle.detect(rust, language=["swift", "msvc"], strict=True) is None

    def test_one_name_forces_as_it_does_for_demangle(self):
        assert demangle.detect("PyInit__lldb", language="gnuv2") is None
        assert demangle.detect("PyInit__lldb", language="gnuv2", strict=True) == "gnuv2"
        assert demangle.detect("PyInit__lldb", language=("gnuv2",), strict=True) is None

    def test_it_never_raises_over_the_name(self):
        for value in (None, b"_Z1fv", "", "_" * 500, "_Z" + "P" * 300 + "i", "_ZN" * 100_000):
            assert demangle.detect(value, strict=True) in (None, "itanium")  # ty: ignore[invalid-argument-type]

    @pytest.mark.sweep
    @pytest.mark.parametrize("corpus", corpus_files())
    def test_it_names_the_scheme_demangle_reads_with(self, corpus):
        for mangled, _ in load_corpus(corpus):
            reader = demangle.detect(mangled, strict=True)
            if reader is None:
                assert demangle.demangle(mangled) == mangled, mangled
            else:
                assert demangle.demangle(mangled, language=reader) == demangle.demangle(mangled), mangled


class TestStyles:
    def test_llvm_and_gnu_differ_where_the_references_differ(self):
        name = "_ZNSt6vectorIiSaIiEE9push_backERKi"
        assert demangle.demangle(name, style="llvm").count("> >") == 0
        assert demangle.demangle(name, style="gnu").count("> >") == 1

    def test_unknown_style_is_rejected_clearly(self):
        with pytest.raises(ValueError, match="unknown style"):
            demangle.demangle("_Z1fv", style="nonexistent")

    def test_unknown_language_is_rejected_clearly(self):
        with pytest.raises(ValueError, match="unknown language"):
            demangle.demangle("_Z1fv", language="cobol")

    def test_an_argument_that_cannot_be_hashed_is_reported_as_a_bad_argument(self):
        """A list where a name belongs is a `ValueError` about the argument, not a
        `TypeError: unhashable type` from inside the cache, which names the mechanism."""
        unhashable: Any = ["a list"]
        with pytest.raises(ValueError, match="unknown language"):
            demangle.demangle("_Z1fv", language=unhashable)
        with pytest.raises(ValueError, match="unknown style"):
            demangle.demangle("_Z1fv", style=unhashable)
        with pytest.raises(ValueError, match="Limits instance"):
            demangle.demangle("_Z1fv", limits=unhashable)

    def test_a_style_subclass_with_the_same_name_is_not_served_from_the_cache(self):
        """The cache is keyed on a style's *name*, so two objects sharing one must both
        stay out of it -- a subclass included."""
        from demangle.core.spelling import SPELLING_BUILDER, SpellingBuilder
        from demangle.core.style import Style

        class Sneaky(Style):
            pass

        name = "_ZNSt6vectorIiSaIiEE9push_backERKi"
        tight = Sneaky(name="llvm", spelling_builder=SPELLING_BUILDER)
        spaced = Sneaky(name="llvm", spelling_builder=SpellingBuilder(legacy_angle_spacing=True))
        demangle.cache_clear()
        assert demangle.demangle(name, style=tight).count("> >") == 0
        assert demangle.demangle(name, style=spaced).count("> >") == 1


class TestForcedLanguage:
    def test_forcing_the_wrong_language_degrades_gracefully(self):
        assert demangle.demangle("_Z1fv", language="msvc") == "_Z1fv"

    def test_forcing_the_right_language_skips_detection(self):
        assert demangle.demangle("?f@@YAXH@Z", language="msvc") == "void __cdecl f(int)"


#: Every entry point that takes `language=` for a whole name, as a call of one argument.
_LANGUAGE_ENTRY_POINTS = [
    lambda language: demangle.demangle("_Z1gv", language=language),
    lambda language: demangle.demangle("_Z1gv", style=demangle.style(), language=language),
    lambda language: demangle.demangle_strict("_Z1gv", language=language),
    lambda language: demangle.parse("_Z1gv", language=language).spell(),
    lambda language: demangle.signature("_Z1gv", language=language).demangled,
    lambda language: next(demangle.demangle_all(["_Z1gv"], language=language)),
    lambda language: demangle.demangleb(b"_Z1gv", language=language).decode(),
    lambda language: demangle.demangleb_strict(b"_Z1gv", language=language).decode(),
    lambda language: demangle.parseb(b"_Z1gv", language=language).spell(),
    lambda language: demangle.signatureb(b"_Z1gv", language=language).demangled,
    lambda language: demangle.demangle_text("_Z1gv", language=language),
    lambda language: next(demangle.find_symbols("_Z1gv", language=language)).demangled,
]


class TestAllowList:
    """A sequence of names: detect, but only among those schemes."""

    def test_any_sequence_will_do(self):
        import collections

        for allowed in (collections.deque(["itanium"]), ["itanium"], ("itanium",)):
            assert demangle.demangle("_Z3foov", language=allowed) == "foo()"
            assert demangle.demangleb(b"_Z3foov", language=allowed) == b"foo()"
            assert demangle.detect("_Z3foov", language=allowed) == "itanium"

    def test_a_scheme_left_out_does_not_read_the_name(self):
        allowed = ("itanium", "swift")
        assert demangle.demangle("_OBJC_CLASS_$_NSData", language=allowed) == "_OBJC_CLASS_$_NSData"
        assert demangle.demangle("example.com/m.(*T).Method", language=allowed) == "example.com/m.(*T).Method"
        assert demangle.demangle("_ZN3foo3barEv", language=allowed) == "foo::bar()"
        assert demangle.demangle("$s10Foundation4DataV5countSivg", language=allowed) == (
            "Foundation.Data.count.getter : Swift.Int"
        )

    @pytest.mark.parametrize("call", _LANGUAGE_ENTRY_POINTS)
    def test_every_entry_point_takes_one(self, call):
        assert call(("swift", "itanium")) == "g()"
        assert call(["itanium"]) == "g()"

    def test_the_order_is_the_registry_s_not_the_sequence_s(self):
        """A legacy Rust name is an Itanium name too; Rust is asked first, as always."""
        rust = "_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E"
        assert demangle.demangle(rust, language=("itanium", "rust")) == "core::fmt::Formatter::pad"
        assert demangle.detect(rust, language=("itanium", "rust")) == "rust"
        assert demangle.detect(rust, language=("itanium",)) == "itanium"
        assert demangle.demangle(rust, language=("itanium",)) == demangle.demangle(rust, language="itanium")

    def test_a_single_name_detects_where_a_string_forces(self):
        """`PyInit__lldb` is a C name pre-Itanium detection declines; forced, it reads."""
        assert demangle.demangle("PyInit__lldb", language=("gnuv2",)) == "PyInit__lldb"
        assert demangle.demangle("PyInit__lldb", language="gnuv2") != "PyInit__lldb"
        with pytest.raises(demangle.NotMangledError):
            demangle.demangle_strict("PyInit__lldb", language=["gnuv2"])

    def test_an_alias_names_its_scheme(self):
        assert demangle.demangle("_ZN3foo3barEv", language=("gnu", "objective-c")) == "foo::bar()"
        assert demangle.detect("_ZN3foo3barEv", language=["c++"]) == "itanium"

    def test_a_list_is_keyed_as_the_tuple_it_names(self):
        demangle.cache_clear()
        assert demangle.demangle("_ZN3foo3barEv", language=["itanium", "swift"]) == "foo::bar()"
        assert demangle.demangle("_ZN3foo3barEv", language=("itanium", "swift")) == "foo::bar()"
        stats = demangle.cache_stats()
        assert (stats["hits"], stats["misses"]) == (1, 1)

    def test_an_answer_under_one_allow_list_is_not_served_to_another(self):
        demangle.cache_clear()
        assert demangle.demangle("_OBJC_CLASS_$_NSData", language=("itanium",)) == "_OBJC_CLASS_$_NSData"
        assert demangle.demangle("_OBJC_CLASS_$_NSData", language=("itanium", "objc")) != "_OBJC_CLASS_$_NSData"

    @pytest.mark.parametrize(
        "language,match",
        [
            ((), "names no scheme"),
            ([], "names no scheme"),
            (("itanium", "cobol"), "unknown language 'cobol'"),
            (["itanium", 5], "unknown language 5"),
            (["itanium", ["swift"]], "unknown language"),
            ({"itanium"}, "unknown language"),
            (b"itanium", "unknown language"),
        ],
    )
    @pytest.mark.parametrize(
        "call",
        [
            *_LANGUAGE_ENTRY_POINTS,
            lambda language: demangle.detect("_Z1gv", language=language),
            lambda language: demangle.detectb(b"_Z1gv", language=language),
            lambda language: demangle.detect(None, language=language),  # ty: ignore[invalid-argument-type]
        ],
    )
    def test_a_sequence_that_names_nothing_is_refused(self, call, language, match):
        with pytest.raises(ValueError, match=match):
            call(language)

    def test_detect_answers_the_first_that_claims_or_none(self):
        assert demangle.detect("?f@@YAXH@Z", language=("itanium", "msvc")) == "msvc"
        assert demangle.detect("?f@@YAXH@Z", language=("itanium", "swift")) is None
        assert demangle.detect("?f@@YAXH@Z", language="msvc") == "msvc"
        assert demangle.detect("?f@@YAXH@Z", language="itanium") is None
        assert demangle.detectb(b"?f@@YAXH@Z", language=["msvc"]) == "msvc"

    def test_allowing_a_scheme_imports_none_until_a_name_reaches_it(self):
        loaded = _schemes_imported_by(
            "assert demangle.demangle('main', language=('msvc', 'swift', 'go')) == 'main'\n"
            "assert demangle.detect('?f@@YAXH@Z', language=['swift', 'msvc']) == 'msvc'"
        )
        assert loaded == ["msvc"]


class TestStructuredOutput:
    def test_parse_returns_a_walkable_tree(self):
        tree = demangle.parse("_ZNK3Foo3barIiEEvPKc")
        kinds = {node.kind for node in tree.walk()}
        assert "function" in kinds
        assert [node.text for node in tree.find("name")] == ["Foo", "bar"]

    def test_tree_spells_identically_to_the_fast_path(self):
        """The two builders must never disagree; this is what the architecture buys."""
        for name in [
            "_ZNSt6vectorIiSaIiEE9push_backERKi",
            "_Z1fPFicE",
            "_ZNK3Foo3barIiEEvPKc",
            "_Z1fM3FooFivE",
            "_Z1fA10_i",
        ]:
            assert demangle.parse(name).spell() == demangle.demangle_strict(name)

    def test_parameters_are_reachable_without_reparsing_text(self):
        tree = demangle.parse("_Z1fiPKcRi")
        function = next(tree.find("function"))
        assert len(function.parameters) == 3


class TestBatch:
    def test_demangle_all_preserves_order_and_length(self):
        names = ["_Z1fv", "memcpy", "?f@@YAXH@Z", ""]
        results = list(demangle.demangle_all(names))
        assert len(results) == len(names)
        assert results[1] == "memcpy"

    def test_cache_is_used(self):
        demangle.cache_clear()
        name = "_ZNSt6vectorIiSaIiEE9push_backERKi"
        demangle.demangle(name)
        demangle.demangle(name)
        assert demangle.cache_stats()["hits"] >= 1


class TestDecorations:
    """Symbol-table decorations: what the linker and compiler append to a name.

    The splitting rules are the part most easily broken, so they are tested directly
    rather than incidentally through the libstdc++ corpus.
    """

    @pytest.mark.parametrize(
        "name,expected",
        [
            (
                "_ZGVNSt10moneypunctIcLb0EE2idE@@GLIBCXX_3.4",
                "guard variable for std::moneypunct<char, false>::id@@GLIBCXX_3.4",
            ),
            ("_ZN3Foo3barEv@GLIBCXX_3.4", "Foo::bar()@GLIBCXX_3.4"),
            ("_ZN3Foo3barEv.cold", "Foo::bar() (.cold)"),
            ("_ZN3Foo3barEv.part.0", "Foo::bar() (.part.0)"),
            ("_ZN3Foo3barEv.llvm.12345", "Foo::bar() (.llvm.12345)"),
        ],
    )
    def test_decorations_are_carried_through(self, name, expected):
        assert demangle.demangle(name) == expected

    def test_gnu_spells_each_clone_separately(self):
        assert demangle.demangle("_ZN3Foo3barEv.actor.cold", style="gnu") == ("Foo::bar() [clone .actor] [clone .cold]")

    def test_detection_sees_through_a_decoration(self):
        assert demangle.detect("_ZN3Foo3barEv@@GLIBCXX_3.4") == "itanium"

    def test_msvc_names_are_never_split_on_at(self):
        """`@` is MSVC's own scope separator, so it must not be treated as a version."""
        assert demangle.demangle("?f@@YAXH@Z") == "void __cdecl f(int)"

    def test_rust_names_are_never_split_on_dot(self):
        """`.` is Rust grammar: `..` is `::` and shims are spelled `{{vtable.shim}}`."""
        name = "_ZN100_$LT$core..iter..adapters..skip..Skip$LT$I$GT$$u20$as$u20$core..iter..traits..iterator..Iterator$GT$4next17h69f836d14d783a6fE"
        assert demangle.demangle(name).startswith("<core::iter::adapters::skip::Skip<I>")

    def test_a_rust_symbol_with_a_trailing_suffix_is_still_rust(self):
        """A hash anchored to the end of the name would hand every one of these to the
        C++ parser."""
        name = "_ZN3std2io5stdio19OUTPUT_CAPTURE_USED17hb12710559afcc79aE.0"
        assert demangle.detect(name) == "rust"
        assert demangle.demangle(name) == "std::io::stdio::OUTPUT_CAPTURE_USED.0"

    def test_the_ast_keeps_the_decoration_as_structure(self):
        from demangle.core.ast import Decorated

        tree = demangle.parse("_ZN3Foo3barEv.cold")
        assert isinstance(tree, Decorated)
        assert tree.decoration == ".cold"
        assert tree.inner.spell() == "Foo::bar()"


class TestStyleRegistration:
    """`Style` and `register_style` are public, so each is tested directly."""

    def test_a_custom_style_can_be_registered_and_used(self):
        from demangle.core.spelling import SpellingBuilder
        from demangle.core.style import _STYLES, get_style
        from demangle.schemes.itanium.options import ItaniumOptions

        house = demangle.Style(
            name="house",
            spelling_builder=SpellingBuilder(legacy_angle_spacing=True),
            language_options={"itanium": ItaniumOptions(expand_std_abbreviations=False)},
        )
        demangle.register_style(house)
        try:
            assert "house" in demangle.styles()
            assert get_style("house") is house
            assert demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi", style="house").count("> >") == 1
        finally:
            if _STYLES is not None:
                _STYLES.pop("house", None)
            demangle.cache_clear()

    def test_a_style_object_may_be_passed_directly(self):
        from demangle.core.style import get_style

        assert get_style(get_style("gnu")) is get_style("gnu")

    def test_with_options_refuses_an_unknown_language_in_either_form(self):
        """Both forms check the name: an object must not add dead options under a name
        nothing reads, any more than a mapping may."""
        from demangle.core.style import get_style
        from demangle.schemes.msvc.options import DEFAULT_OPTIONS as MSVC_OPTIONS

        base = get_style("llvm")
        with pytest.raises(ValueError, match="carries no options for 'mscv'"):
            base.with_options(mscv={"calling_convention": False})
        with pytest.raises(ValueError, match="unknown language 'mscv'"):
            base.with_options(mscv=MSVC_OPTIONS)
        assert base.with_options(msvc=MSVC_OPTIONS).options_for("msvc") is MSVC_OPTIONS


class TestBatchOptions:
    def test_demangle_all_honours_language_and_style(self):
        names = ["_ZNSt6vectorIiSaIiEE9push_backERKi"]
        assert next(iter(demangle.demangle_all(names, style="gnu"))).count("> >") == 1
        assert next(iter(demangle.demangle_all(names, language="itanium"))).startswith("std::vector")

    def test_bad_arguments_are_reported_at_the_call(self):
        """Not at the first `next()`, which is a surprise at a distance."""
        with pytest.raises(ValueError, match="unknown language"):
            demangle.demangle_all(["_Z1fv"], language="cobol")
        with pytest.raises(ValueError, match="unknown style"):
            demangle.demangle_all(["_Z1fv"], style="bogus")


class TestParseOptions:
    def test_parse_accepts_a_language(self):
        tree = demangle.parse("?f@@YAXH@Z", language="msvc")
        assert tree.spell() == "void __cdecl f(int)"

    def test_parse_accepts_a_style_and_the_tree_agrees_with_the_text(self):
        name = "_Z1fI1AIiEEvT_"
        for style in ("llvm", "gnu"):
            assert demangle.parse(name, style=style).spell(style=style) == demangle.demangle(name, style=style)

    def test_msvc_returns_a_tree(self):
        tree = demangle.parse("?f@@YAXH@Z")
        assert len(list(tree.walk())) > 1

    def test_rust_returns_a_tree(self):
        tree = demangle.parse("_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E")
        assert tree.kind == "symbol"
        assert [node.text for node in tree.find("name")] == ["core", "fmt", "Formatter", "pad"]


#: Every entry point that takes `limits`, as a call of one argument.
_LIMITS_ENTRY_POINTS = [
    lambda limits: demangle.demangle("_Z1gv", limits=limits),
    lambda limits: demangle.demangle("_Z1gv", style=demangle.style(), limits=limits),
    lambda limits: demangle.demangle("not mangled", limits=limits),
    lambda limits: demangle.demangle_strict("_Z1gv", limits=limits),
    lambda limits: demangle.parse("_Z1gv", limits=limits),
    lambda limits: demangle.demangle_type("i", language="itanium", limits=limits),
    lambda limits: demangle.parse_type("i", language="itanium", limits=limits),
    lambda limits: demangle.demangleb(b"_Z1gv", limits=limits),
    lambda limits: demangle.demangleb_strict(b"_Z1gv", limits=limits),
    lambda limits: demangle.parseb(b"_Z1gv", limits=limits),
    lambda limits: demangle.demangleb_type(b"i", language="itanium", limits=limits),
    lambda limits: demangle.parseb_type(b"i", language="itanium", limits=limits),
    lambda limits: demangle.signature("_Z1gv", limits=limits),
    lambda limits: demangle.signatureb(b"_Z1gv", limits=limits),
    lambda limits: demangle.demangle_all([], limits=limits),
    lambda limits: demangle.demangle_text("no symbols here", limits=limits),
    lambda limits: demangle.find_symbols("no symbols here", limits=limits),
    lambda limits: demangle.demangle_stream([], io.StringIO(), limits=limits),
]


class TestCacheStatistics:
    def test_every_documented_key_is_present(self):
        demangle.cache_clear()
        demangle.demangle("_Z1fv")
        demangle.demangle("_Z1fv")
        stats = demangle.cache_stats()
        assert set(stats) == {"size", "max_size", "hits", "misses", "hit_rate"}
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5
        assert stats["size"] >= 1

    def test_the_cache_holds_a_large_librarys_symbol_table(self):
        """One generation, half of `max_size`, holds it whole, so a second pass over it
        hits on every name; libLLVM exports 56k names."""
        assert demangle.cache_stats()["max_size"] // 2 >= 65536

    @pytest.mark.parametrize("letter", ["o", "\U0001f600"], ids=["ascii", "astral"])
    def test_long_names_cannot_grow_the_cache_past_its_weight(self, monkeypatch, letter):
        """Bounded in bytes as well as entries, whatever `Limits` lets through: a string
        with a character outside the BMP takes four bytes for every character."""
        from demangle import api
        from demangle.core.cache import BoundedCache

        def stored(text):
            return len(text) * (1 if max(text) < "\u0100" else 2 if max(text) < "\U00010000" else 4)

        cache = BoundedCache(max_size=1000, max_weight=20_000, weigh=api._CACHE._weigh)
        monkeypatch.setattr(api, "_CACHE", cache)
        for index in range(200):
            demangle.demangle("_ZN" + f"3fo{letter}" * 100 + f"{len(str(index)) + 1}x{index}" + "Ev")
        entries = [stored(key[0]) + stored(value) for part in (cache._young, cache._old) for key, value in part.items()]
        assert sum(entries) <= 20_000 + 2 * max(entries)
        assert len(cache) < 20

    def test_an_answer_computed_across_a_clear_is_not_stored(self, monkeypatch):
        """Registering a language or style clears the cache; a call that began parsing
        before that must not store what it read with the plugins from before."""
        from demangle.core import registry
        from demangle.core.plugin import LanguagePlugin

        parses = []

        def parse(mangled, builder, *rest):
            parses.append(mangled)
            demangle.cache_clear()
            return builder.name("read")

        plugin = LanguagePlugin(name="clears", detect=lambda name: False, parse=parse)
        monkeypatch.setattr(registry, "_plugins", {**registry._plugins, "clears": plugin})
        assert demangle.demangle("x", language="clears") == "read"
        assert demangle.demangle("x", language="clears") == "read"
        assert parses == ["x", "x"]

    @pytest.mark.parametrize("name", ["_Z1fv", "_ZN" + "3foo" * 400 + "Ev"])
    def test_a_bad_argument_is_refused_whether_or_not_the_call_is_cached(self, name):
        with pytest.raises(ValueError, match="Limits instance"):
            demangle.demangle(name, limits={"max_depth": 1})  # ty: ignore[invalid-argument-type]
        with pytest.raises(ValueError, match="unknown language"):
            demangle.demangle(name, language={"itanium"})  # ty: ignore[invalid-argument-type]

    def test_a_name_named_on_every_page_of_a_table_misses_once(self, monkeypatch):
        """A name still in use survives each turnover of the cache's generations."""
        from demangle import api
        from demangle.core.cache import BoundedCache

        monkeypatch.setattr(api, "_CACHE", BoundedCache(max_size=20))
        hot = "_ZNSaIcED1Ev"
        for index in range(200):
            demangle.demangle(f"_Z1f{index}", language="itanium")
            demangle.demangle(hot)
        stats = demangle.cache_stats()
        assert (stats["hits"], stats["misses"]) == (199, 201)

    def test_the_default_limits_and_an_equal_object_answer_alike(self):
        """The default is keyed apart from any other `Limits`, equal or not."""
        from demangle.core.limits import DEFAULT_LIMITS, Limits

        demangle.cache_clear()
        name = "_ZN3foo3barEv"
        assert demangle.demangle(name, limits=DEFAULT_LIMITS) == demangle.demangle(name, limits=Limits())
        assert demangle.demangle(name, limits=DEFAULT_LIMITS) == "foo::bar()"
        assert demangle.cache_stats()["hits"] == 1

    def test_limits_none_does_not_share_the_default_limits_cache_slot(self):
        """The default is not keyed as `None`, so `limits=None` -- which no parser can
        read -- cannot cache the name unread for later calls with the default."""
        demangle.cache_clear()
        with pytest.raises(ValueError, match="Limits instance"):
            demangle.demangle("_Z1gv", limits=None)  # ty: ignore[invalid-argument-type]
        assert demangle.demangle("_Z1gv") == "g()"

    @pytest.mark.parametrize("limits", [None, "bogus", {"max_depth": 1}, 256])
    @pytest.mark.parametrize("call", _LIMITS_ENTRY_POINTS)
    def test_every_entry_point_refuses_limits_that_are_not_a_limits(self, call, limits):
        with pytest.raises(ValueError, match="Limits instance"):
            call(limits)

    @pytest.mark.parametrize("call", _LIMITS_ENTRY_POINTS)
    def test_every_entry_point_refuses_an_unhashable_limits_at_the_call(self, call):
        """A `Limits` subclass that cannot be hashed cannot key the cache; refused where
        it is passed, not at the first `next()` of a generator or never, for a text
        with no symbol in it."""
        from demangle.core.limits import Limits

        class Unhashable(Limits):
            __hash__ = None

        with pytest.raises(ValueError, match="limits must be hashable, got an unhashable Unhashable"):
            call(Unhashable())

    def test_the_very_first_call_in_a_process_counts_as_a_miss(self):
        """Loading the registry clears the cache, statistics included. The first call
        loads before it looks the name up, so the miss it records is kept."""
        source = pathlib.Path(__file__).resolve().parent.parent / "src"
        script = (
            "import demangle\n"
            "demangle.demangle('_Z1fv')\n"
            "demangle.demangle('_Z1fv')\n"
            "s = demangle.cache_stats()\n"
            "print(s['misses'], s['hits'])\n"
        )
        run = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True, env={"PYTHONPATH": str(source)}
        )
        assert run.stdout.split() == ["1", "1"]


def _output_of(script):
    """What `script` prints in a fresh interpreter, where no scheme has been imported."""
    source = pathlib.Path(__file__).resolve().parent.parent / "src"
    run = subprocess.run(
        [sys.executable, "-c", "import sys, demangle\n" + script],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONPATH": str(source)},
    )
    return run.stdout


def _schemes_imported_by(script):
    """The scheme packages imported after `script` runs in a fresh interpreter."""
    script += "\nprint(*sorted(m.split('.')[2] for m in sys.modules if m.count('.') == 2 and '.schemes.' in m))\n"
    return _output_of(script).split()


class TestStylesImportOnlyWhatIsUsed:
    """A style's per-language options live in each scheme's package, and building the two
    built-in styles imports only the schemes the name needs."""

    def test_a_first_call_on_an_msvc_name_imports_no_swift(self):
        assert _schemes_imported_by("assert demangle.demangle('?f@@YAXH@Z') == 'void __cdecl f(int)'") == ["msvc"]

    def test_a_first_call_on_a_plain_c_name_imports_no_scheme(self):
        assert _schemes_imported_by("assert demangle.demangle('main') == 'main'") == []

    def test_a_first_call_on_an_itanium_name_imports_only_the_schemes_asked_before_it(self):
        loaded = _schemes_imported_by("assert demangle.demangle('_ZN3foo3barEv', style='gnu') == 'foo::bar()'")
        # D, Swift and Rust also open `_` and are asked first; nothing else is imported.
        assert loaded == ["d", "itanium", "rust", "swift"]

    def test_composing_a_style_imports_only_the_language_it_changes(self):
        loaded = _schemes_imported_by(
            "narrow = demangle.style('llvm', msvc={'calling_convention': False})\n"
            "assert sorted(narrow.language_options) == sorted(demangle.core.style.get_style('gnu').language_options)"
        )
        assert loaded == ["msvc"]

    def test_listing_a_style_s_languages_imports_none_of_them(self):
        loaded = _schemes_imported_by(
            "from demangle.core.style import get_style\n"
            "assert list(get_style('llvm').language_options) == "
            "['itanium', 'msvc', 'swift', 'gnuv2', 'codewarrior', 'rust']\n"
            "assert 'swift' in get_style('gnu').language_options and len(get_style('gnu').language_options) == 6"
        )
        assert loaded == []

    def test_each_language_resolves_to_its_scheme_s_own_object(self):
        from demangle.core.style import get_style
        from demangle.schemes.itanium.options import DEFAULT_OPTIONS, GNU_OPTIONS
        from demangle.schemes.swift.options import DEFAULT_OPTIONS as SWIFT_OPTIONS

        assert get_style("llvm").options_for("itanium") is DEFAULT_OPTIONS
        assert get_style("gnu").options_for("itanium") is GNU_OPTIONS
        assert get_style("gnu").options_for("swift") is SWIFT_OPTIONS
        assert get_style("gnu").options_for("go") is None
        assert get_style("gnu").language_options["swift"] is SWIFT_OPTIONS
        with pytest.raises(KeyError):
            get_style("gnu").language_options["go"]

    def test_a_composed_style_keeps_the_rest_of_its_base(self):
        from demangle.core.style import get_style

        narrow = demangle.style("gnu", msvc={"calling_convention": False})
        assert narrow.options_for("itanium") is get_style("gnu").options_for("itanium")
        assert narrow.options_for("msvc").calling_convention is False
        assert get_style("gnu").options_for("msvc").calling_convention is True
        assert narrow != get_style("gnu")
        assert demangle.style("gnu") == get_style("gnu")

    def test_resolving_from_many_threads_at_once_gives_one_object(self):
        import threading

        from demangle.core.style import _LazyOptions, _options

        for _ in range(20):
            options = _LazyOptions({"rust": _options("rust"), "msvc": _options("msvc")})
            seen = []
            barrier = threading.Barrier(8)

            def resolve(options=options, seen=seen, barrier=barrier):
                barrier.wait()
                seen.append((options["rust"], options.get("msvc")))

            threads = [threading.Thread(target=resolve) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            assert len(set(map(id, (pair[0] for pair in seen)))) == 1
            assert len(set(map(id, (pair[1] for pair in seen)))) == 1


class TestPreload:
    """`preload()` moves the import of a scheme to start-up; it changes nothing else."""

    def test_names_import_those_schemes_and_no_other(self):
        assert _schemes_imported_by("demangle.preload('msvc', 'gnu')") == ["itanium", "msvc"]

    def test_no_names_imports_every_built_in(self):
        from demangle.core.registry import _BUILTIN_NAMES

        assert _schemes_imported_by("demangle.preload()") == sorted(_BUILTIN_NAMES)

    def test_an_unknown_name_is_refused_before_anything_is_imported(self):
        loaded = _schemes_imported_by(
            "try:\n    demangle.preload('swift', 'cobol')\nexcept ValueError as error:\n    assert 'cobol' in str(error)"
        )
        assert loaded == []

    def test_a_preloaded_scheme_answers_as_a_lazy_one_does(self):
        script = (
            "print(demangle.demangle('?f@@YAXH@Z'), demangle.detect('_RNvC6_123foo3bar'),"
            " [plugin.name for plugin in demangle.core.registry.candidates('x__$/')])\n"
        )
        lazy = _output_of(script)
        assert lazy == _output_of("demangle.preload()\n" + script)
        assert lazy.startswith("void __cdecl f(int) rust ")

    def test_a_service_s_start_up_pays_for_the_import_and_its_first_name_does_not(self):
        assert _schemes_imported_by(
            "demangle.preload('msvc')\n"
            "before = set(sys.modules)\n"
            "demangle.demangle('?f@@YAXH@Z')\n"
            "assert not [m for m in set(sys.modules) - before if '.schemes.' in m], sorted(set(sys.modules) - before)"
        ) == ["msvc"]

    def test_it_returns_nothing(self):
        assert demangle.preload("itanium") is None


class TestDocstringExamples:
    def test_every_example_in_the_api_s_docstrings_is_what_the_code_does(self):
        import doctest

        from demangle import api

        results = doctest.testmod(api)
        assert results.attempted
        assert not results.failed


class TestIntrospection:
    def test_languages_lists_the_built_ins(self):
        assert set(demangle.languages()) >= {"itanium", "msvc", "rust"}

    def test_styles_lists_the_built_ins(self):
        assert set(demangle.styles()) >= {"llvm", "gnu"}


@pytest.mark.parametrize("call", [demangle.demangle_strict, demangle.parse, demangle.signature])
def test_forced_plugin_defects_are_wrapped(call, monkeypatch):
    from dataclasses import replace

    from demangle import api
    from demangle.core.registry import get

    failure = RuntimeError("plugin defect")

    def broken(*args):
        raise failure

    plugin = replace(get("itanium"), parse=broken)
    monkeypatch.setattr(api, "_resolve", lambda language: plugin)
    with pytest.raises(demangle.ParseError, match="itanium parser failed") as caught:
        call("_Z1fv", language="itanium")
    assert caught.value.__cause__ is failure


@pytest.mark.parametrize(
    "arguments,match",
    [
        ({"language": "unknown"}, "unknown language"),
        ({"language": []}, "names no scheme"),
        ({"style": "unknown"}, "unknown style"),
        ({"limits": None}, "Limits instance"),
    ],
)
def test_empty_names_still_validate_configuration(arguments, match):
    with pytest.raises(ValueError, match=match):
        demangle.demangle("", **arguments)


@pytest.mark.parametrize("failure", [MemoryError("resource failure"), demangle.ParseError("_Z1fv")])
def test_forced_plugin_preserves_operational_and_parse_errors(monkeypatch, failure):
    from dataclasses import replace

    from demangle import api
    from demangle.core.registry import get

    def broken(*args):
        raise failure

    plugin = replace(get("itanium"), parse=broken)
    monkeypatch.setattr(api, "_resolve", lambda language: plugin)
    with pytest.raises(type(failure)) as caught:
        demangle.demangle_strict("_Z1fv", language="itanium")
    assert caught.value is failure
