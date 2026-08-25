"""The public API contract.

These are the promises callers build on, so they are tested as promises rather than
through whatever happens to exercise them.
"""

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


class TestIntrospection:
    def test_languages_lists_the_built_ins(self):
        assert set(demangle.languages()) >= {"itanium", "msvc", "rust"}

    def test_styles_lists_the_built_ins(self):
        assert set(demangle.styles()) >= {"llvm", "gnu"}
