"""Demangling a bare *type* encoding rather than a whole symbol.

`c++filt -t`, libiberty's `DMGL_TYPES`, `UnDecorateSymbolName`'s `UNDNAME_TYPE_ONLY`
and Swift's `demangleTypeAsString` all read one, and every one of them makes the caller
ask for it rather than detecting. That is not an interface wart: a whole symbol
announces its scheme -- `_Z`, `?`, `$s` -- and a type encoding announces nothing at all.
`i` is a valid Itanium type, a valid Swift type and an ordinary C identifier, so there
is no evidence to detect on and `language` has to be given.

The Itanium expectations here are `c++filt -t` (binutils 2.42) verbatim, recorded in
`conformance/itanium-types.txt`. LLVM ships no type-only *tool* -- `llvm-cxxfilt` has no
`-t`, and llvm-undname 18.1.3 has no `--types` -- so for the LLVM style and for MSVC the
standing-in property is agreement with the symbol path: whatever `f(T)` spells between
its brackets is what `demangle_type` must spell for `T` alone.
"""

import pytest

import demangle
from demangle.core.errors import DemanglingError, NotMangledError, ParseError

from .conftest import load_corpus


class TestTheItaniumTypeGrammar:
    #: Raised as gaps close, never lowered silently.
    EXPECTED_EXACT = 1073

    def _score(self):
        return sum(
            1 for enc, expected in load_corpus("itanium-types.txt") if _spelled(enc, "itanium", "gnu") == expected
        )

    def test_the_score_has_not_gone_backwards(self):
        total = len(load_corpus("itanium-types.txt"))
        assert total > 1000, "corpus did not load; this test would prove nothing"
        assert self._score() >= self.EXPECTED_EXACT

    def test_the_pinned_number_is_still_accurate(self):
        assert self._score() == self.EXPECTED_EXACT

    def test_the_shortfall_is_only_the_known_divergence(self):
        """The three misses are `KK`, a doubled cv-qualifier, and are all of them.

        GNU folds a repeated qualifier away and prints `int const`; LLVM keeps both and
        prints `int const const`, which is what this follows everywhere else. No
        compiler emits `KK` -- the ABI writes one `<CV-qualifiers>` group per type -- so
        the two references disagree only about input neither of them will ever be given.
        """
        missed = [
            enc for enc, expected in load_corpus("itanium-types.txt") if _spelled(enc, "itanium", "gnu") != expected
        ]
        assert missed == ["KKDv4_i", "PKKDv4_i", "RKKDv4_i"]

    @pytest.mark.parametrize("style", ["llvm", "gnu"])
    def test_a_type_spells_what_the_same_type_spells_inside_a_symbol(self, style):
        """`demangle_type(T)` and the parameter list of `_Z1f<T>` are the same question."""
        for enc, _ in load_corpus("itanium-types.txt"):
            whole = demangle.demangle(f"_Z1f{enc}", style=style)
            if whole == f"_Z1f{enc}" or not whole.startswith("f("):
                continue
            if whole == "f()":
                # `v` alone is the type `void`; `v` as a parameter list is *no*
                # parameters. Both spellings are right and they are not the same string.
                continue
            assert _spelled(enc, "itanium", style) == whole[len("f(") : -1], enc

    def test_the_tree_spells_what_the_text_spells(self):
        for enc, _ in load_corpus("itanium-types.txt"):
            tree = demangle.parse_type(enc, language="itanium")
            assert tree.spell() == demangle.demangle_type(enc, language="itanium"), enc


def _spelled(encoding, language, style):
    try:
        return demangle.demangle_type(encoding, language=language, style=style)
    except DemanglingError:
        return None


class TestWhatIsRefused:
    def test_an_empty_encoding_is_not_a_type(self):
        with pytest.raises(NotMangledError):
            demangle.demangle_type("", language="itanium")

    def test_trailing_input_is_refused_rather_than_ignored(self):
        """`iX` is not `int`. A type entry point that stops early invents a reading."""
        with pytest.raises(ParseError):
            demangle.demangle_type("iX", language="itanium")

    def test_a_template_parameter_has_nothing_to_bind_it(self):
        """`T_` is `auto` in a symbol, where arguments may follow, and nothing here.

        A whole symbol can encode a return type ahead of the arguments that bind it, so
        an unresolved `T_` there is spelled `auto` -- which is what both references do,
        and what generic lambdas rely on. A bare type has no enclosing template and can
        never acquire one, so `auto` would be a type that is not in the encoding.
        `c++filt -t` refuses these too.
        """
        for encoding in ("T_", "PT_", "AT__i"):
            with pytest.raises(ParseError):
                demangle.demangle_type(encoding, language="itanium")

    def test_a_whole_symbol_is_not_a_type(self):
        with pytest.raises(DemanglingError):
            demangle.demangle_type("_Z1fv", language="itanium")

    def test_bytes_are_a_mistake_in_the_calling_code(self):
        with pytest.raises(TypeError):
            demangle.demangle_type(b"Pi", language="itanium")  # ty: ignore[invalid-argument-type]


class TestNamingTheScheme:
    def test_the_language_is_required(self):
        with pytest.raises(TypeError):
            demangle.demangle_type("Pi")  # ty: ignore[missing-argument]

    def test_an_unknown_language_says_which_are_known(self):
        with pytest.raises(ValueError, match="unknown language"):
            demangle.demangle_type("Pi", language="klingon")

    def test_a_scheme_with_no_type_grammar_says_which_have_one(self):
        """Go, Rust, Nim and the rest carry types only as text they already spell."""
        with pytest.raises(ValueError, match="itanium, msvc, swift"):
            demangle.demangle_type("Pi", language="rust")

    def test_the_same_encoding_reads_differently_under_different_schemes(self):
        """Why detection is impossible here, in one assertion."""
        assert demangle.demangle_type("Si", language="itanium") == "std::istream"
        assert demangle.demangle_type("Si", language="swift") == "Swift.Int"


class TestMsvcTypes:
    @pytest.mark.parametrize(
        ("encoding", "expected"),
        [
            ("PEAX", "void *"),
            ("H", "int"),
            ("PEAVFoo@@", "class Foo *"),
            ("V?$A@H@@", "class A<int>"),
            ("PEAPEAH", "int **"),
            ("AEAH", "int &"),
        ],
    )
    def test_a_type_reads_as_llvm_undname_reads_it(self, encoding, expected):
        assert demangle.demangle_type(encoding, language="msvc") == expected

    def test_a_leading_dot_is_the_type_descriptors_own_spelling(self):
        """The linker writes `.PEAX` on an RTTI type descriptor; the type is after it."""
        assert demangle.demangle_type(".PEAX", language="msvc") == "void *"

    def test_a_variables_type_reads_the_same_alone_as_in_its_symbol(self):
        """`?x@@3PEAXEA` is `void * x`, so `PEAX` on its own has to be `void *`."""
        assert demangle.demangle("?x@@3PEAXEA").startswith(demangle.demangle_type("PEAX", language="msvc"))

    def test_nonsense_is_refused(self):
        with pytest.raises(ParseError):
            demangle.demangle_type("QQQ", language="msvc")

    def test_a_decorated_name_is_not_a_type(self):
        with pytest.raises(DemanglingError):
            demangle.demangle_type("?f@@YAXH@Z", language="msvc")


class TestSwiftTypes:
    @pytest.mark.parametrize(
        ("encoding", "expected"),
        [
            ("Si", "Swift.Int"),
            ("SS", "Swift.String"),
            ("SaySiG", "[Swift.Int]"),
            ("Si_Sit", "(Swift.Int, Swift.Int)"),
            ("4main3FooC", "main.Foo"),
        ],
    )
    def test_a_type_reads_as_swift_demangle_reads_it(self, encoding, expected):
        assert demangle.demangle_type(encoding, language="swift") == expected

    def test_an_unreadable_type_is_refused_rather_than_narrated(self):
        """The reference wraps what it could not read in `with unmangled suffix "..."`.

        True, and not a demangling of anything, so it comes back as the failure it is.
        """
        with pytest.raises(DemanglingError):
            demangle.demangle_type("", language="swift")


class TestTheTreeForm:
    def test_parse_type_gives_a_node_the_text_form_agrees_with(self):
        tree = demangle.parse_type("PKFvRiE", language="itanium")
        assert isinstance(tree, demangle.Node)
        assert tree.spell() == demangle.demangle_type("PKFvRiE", language="itanium")

    def test_the_tree_carries_the_declarator_shape(self):
        assert demangle.parse_type("Pi", language="itanium").kind == "pointer"

    @pytest.mark.parametrize("language", ["itanium", "msvc", "swift"])
    def test_every_scheme_that_reads_a_type_gives_both_forms(self, language):
        encoding = {"itanium": "Pi", "msvc": "PEAX", "swift": "Si"}[language]
        assert isinstance(demangle.parse_type(encoding, language=language), demangle.Node)
        assert isinstance(demangle.demangle_type(encoding, language=language), str)


class TestTheLimitsApply:
    def test_an_over_long_encoding_is_refused_before_it_is_read(self):
        for language, encoding in (
            ("itanium", "P" * 400 + "i"),
            ("msvc", "PEA" * 200 + "X"),
            ("swift", "Sa" * 300 + "ySiG"),
        ):
            with pytest.raises(demangle.LimitExceeded):
                demangle.demangle_type(encoding, language=language, limits=demangle.Limits(max_input=32))

    def test_the_relaxed_limits_let_a_deeper_type_through(self):
        deep = "P" * 200 + "i"
        assert demangle.demangle_type(deep, language="itanium", limits=demangle.RELAXED_LIMITS).endswith("*" * 200)


class TestTheBytesForms:
    """A type encoding is read out of a binary as often as a symbol is."""

    def test_bytes_in_bytes_out(self):
        assert demangle.demangleb_type(b"PKFvRiE", language="itanium") == b"void (*)(int&) const"

    def test_the_tree_form_over_bytes(self):
        assert demangle.parseb_type(b"Pi", language="itanium").spell() == "int*"

    def test_undecodable_bytes_do_not_raise_a_unicode_error(self):
        """`surrogateescape`, as everywhere else: a demangling failure, not a decode one."""
        with pytest.raises(DemanglingError):
            demangle.demangleb_type(b"P\xff", language="itanium")

    def test_a_str_is_a_mistake_in_the_calling_code(self):
        with pytest.raises(TypeError):
            demangle.demangleb_type("Pi", language="itanium")  # ty: ignore[invalid-argument-type]
