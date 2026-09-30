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

    def test_the_cache_holds_a_large_librarys_symbol_table(self):
        """One generation, half of `max_size`, holds it whole, so a second pass over it
        hits on every name; libLLVM exports 56k names."""
        assert demangle.cache_stats()["max_size"] // 2 >= 65536

    def test_long_names_cannot_grow_the_cache_past_its_weight(self, monkeypatch):
        """Bounded in characters as well as entries, whatever `Limits` lets through."""
        from demangle import api
        from demangle.core.cache import BoundedCache

        cache = BoundedCache(max_size=1000, max_weight=10_000, weigh=api._CACHE._weigh)
        monkeypatch.setattr(api, "_CACHE", cache)
        for index in range(200):
            demangle.demangle("_ZN" + "3foo" * 100 + f"{len(str(index)) + 1}x{index}" + "Ev")
        held = sum(len(key[0]) + len(value) for part in (cache._young, cache._old) for key, value in part.items())
        assert held <= 10_000 + 2 * 2_000
        assert len(cache) < 20

    @pytest.mark.parametrize("name", ["_Z1fv", "_ZN" + "3foo" * 400 + "Ev"])
    def test_a_bad_argument_is_refused_whether_or_not_the_call_is_cached(self, name):
        with pytest.raises(ValueError, match="Limits instance"):
            demangle.demangle(name, limits={"max_depth": 1})  # ty: ignore[invalid-argument-type]
        with pytest.raises(ValueError, match="unknown language"):
            demangle.demangle(name, language=["itanium"])  # ty: ignore[invalid-argument-type]

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
    @pytest.mark.parametrize(
        "call",
        [
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
        ],
    )
    def test_every_entry_point_refuses_limits_that_are_not_a_limits(self, call, limits):
        with pytest.raises(ValueError, match="Limits instance"):
            call(limits)

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


def _schemes_imported_by(script):
    """The scheme packages imported after `script` runs in a fresh interpreter."""
    source = pathlib.Path(__file__).resolve().parent.parent / "src"
    script += "\nprint(*sorted(m.split('.')[2] for m in sys.modules if m.count('.') == 2 and '.schemes.' in m))\n"
    run = subprocess.run(
        [sys.executable, "-c", "import sys, demangle\n" + script],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONPATH": str(source)},
    )
    return run.stdout.split()


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


class TestIntrospection:
    def test_languages_lists_the_built_ins(self):
        assert set(demangle.languages()) >= {"itanium", "msvc", "rust"}

    def test_styles_lists_the_built_ins(self):
        assert set(demangle.styles()) >= {"llvm", "gnu"}
