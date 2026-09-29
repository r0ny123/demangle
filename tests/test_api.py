"""The public API contract.

These are the promises callers build on, so they are tested as promises rather than
through whatever happens to exercise them.
"""

import pathlib
import subprocess
import sys
from typing import Any

import pytest

import demangle


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
        """A list where a name belongs used to surface as `TypeError: unhashable type`
        from inside the cache, which names the mechanism rather than the mistake."""
        unhashable: Any = ["a list"]
        with pytest.raises(ValueError, match="unknown language"):
            demangle.demangle("_Z1fv", language=unhashable)
        with pytest.raises(ValueError, match="unknown style"):
            demangle.demangle("_Z1fv", style=unhashable)
        with pytest.raises(ValueError, match="unhashable limits"):
            demangle.demangle("_Z1fv", limits=unhashable)

    def test_a_style_subclass_with_the_same_name_is_not_served_from_the_cache(self):
        """The cache is keyed on a style's *name*, so two objects sharing one must both
        stay out of it -- a subclass included, which used to be keyed like a name."""
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

    Only covered incidentally through the libstdc++ corpus before, which meant the
    splitting rules -- the part that has broken twice -- had no direct test.
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
        """Anchoring the hash to the end lost every one of these to the C++ parser."""
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
    """`Style` and `register_style` are public and were entirely untested."""

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
        """The mapping form always checked; the object form quietly added dead options
        under a name nothing reads."""
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

    def test_the_very_first_call_in_a_process_counts_as_a_miss(self):
        """Loading the registry clears the cache, statistics included. The first call
        used to look the name up, *then* load, and lose the miss it had just recorded."""
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


class TestIntrospection:
    def test_languages_lists_the_built_ins(self):
        assert set(demangle.languages()) >= {"itanium", "msvc", "rust"}

    def test_styles_lists_the_built_ins(self):
        assert set(demangle.styles()) >= {"llvm", "gnu"}
