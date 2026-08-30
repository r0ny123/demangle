"""Rust mangled names: the rules that decide what is *refused*.

The spellings are pinned by the corpora in `test_conformance.py`; what is pinned here is
the boundary, which no corpus of real symbols exercises because a compiler does not emit
malformed names. Every expectation below was measured against `rustc-demangle` 0.1.28
itself -- the crate `rustfilt` is built from -- driven one whole line at a time, rather
than against `rustfilt`, whose command line scans a line for something that looks like a
symbol and so answers a different question.
"""

from typing import ClassVar

import pytest

import demangle
from demangle.core.errors import DemanglingError

from .conftest import load_corpus

#: rustc-demangle's own vectors, read through the shared loader.
UPSTREAM = load_corpus("rustc-upstream.txt")

#: The four vectors whose expected column this does not produce, held by name so the
#: count below cannot drift into hiding a real failure. Each spelling here was checked
#: against `rustfilt` -- rustc-demangle's *own* command-line front end -- rather than
#: against the library's `#[test]` assertion, because on two of the four the tool and the
#: assertion do not agree with each other and the tool is what a user meets.
KNOWN_DIFFERENCES = {
    # Detection, not spelling. rustc's hash is `17h` and sixteen hex digits; neither of
    # these is that shape, so this plugin declines to read them as Rust at all and the
    # C++ demangler reads them instead -- correctly, since `foo::h05af` is a name C++ can
    # have. rustc-demangle can afford the wider rule because it is only ever handed names
    # a caller has already decided are Rust's; see `rust.detect`.
    "_ZN3foo20h05af221e174051e9abcE": "foo::h05af221e174051e9abc",
    "_ZN3foo5h05afE": "foo::h05af",
    # Not a difference from the tool at all: `rustfilt` prints `foo@@16` for this too.
    # The vector records the library's own `Display`, which reports what follows the
    # symbol separately rather than printing it.
    "_RC3foo.llvm.9D1C9369@@16": "foo@@16",
    # A legacy name with the leading underscore stripped *and* no hash and no `$...$`
    # escape. rustc-demangle claims it for the reason above; this plugin is offered every
    # symbol in a binary, where the same rule would claim any C identifier starting `ZN`.
    # `rustfilt` echoes it back unread, exactly as this does.
    "ZN4testE": "ZN4testE",
}


class TestAgainstRustcDemanglesOwnVectors:
    """The vectors rustc-demangle asserts against in its own `#[test]` blocks.

    Pinned in both directions, like libcxxabi's and Swift's: the score can only go up,
    and it cannot quietly stop being accurate.
    """

    def test_the_corpus_is_the_whole_vector_list(self):
        assert len(UPSTREAM) == 51

    def test_every_vector_matches_or_is_a_recorded_difference(self):
        for mangled, expected in UPSTREAM:
            spelled = demangle.demangle(mangled)
            if mangled in KNOWN_DIFFERENCES:
                assert spelled == KNOWN_DIFFERENCES[mangled]
            else:
                assert spelled == expected, mangled

    def test_the_recorded_differences_are_all_still_differences(self):
        """So one that gets fixed has to be taken off the list rather than left to rot."""
        for mangled, expected in UPSTREAM:
            if mangled in KNOWN_DIFFERENCES:
                assert demangle.demangle(mangled) != expected


class TestNonAsciiIsRefused:
    """The reference reads a symbol as *bytes* and refuses it if any has bit 7 set.

    Written as a per-character `ord(c) & 0x80` test that is not the same rule: U+0100 is
    one character whose value has bit 7 clear, so it passed and its identifier was
    printed where the reference echoes the symbol back unread. Neither mangling ever
    carries a non-ASCII character literally -- v0 spells one in punycode and the legacy
    scheme writes `$u0100$` -- so anything that does is not a Rust symbol.
    """

    def test_v0_refuses_a_literal_non_ascii_identifier(self):
        assert demangle.demangle("_RC1Ā") == "_RC1Ā"

    def test_legacy_refuses_a_literal_non_ascii_identifier(self):
        # Named rather than detected: legacy Rust mangling *is* Itanium mangling, so
        # detection hands this one to the C++ scheme and what comes back is that
        # scheme's answer, not this one's.
        mangled = "_ZN1a2ĀbE"
        assert demangle.demangle(mangled, language="rust") == mangled

    def test_the_ascii_names_either_side_of_it_still_read(self):
        assert demangle.demangle("_RC2ab") == "ab"
        assert demangle.demangle("_ZN1a1b17h0123456789abcdefE") == "a::b"


class TestAnIdentifierLengthHasToBeADigit:
    """`<identifier>` opens with a decimal length, and the reference requires one.

    rustc-demangle reads it as `self.digit_10()?`, so anything that is not a digit ends
    the parse. This read a non-digit as a length of *zero* and left the character where
    it was -- and the `_` that separates a length from its text then swallowed it, so
    nothing was left over for the residual check to refuse.

    The result was a name with an empty component in it, spelled as though the component
    were there and blank. `_RNvC_1f` is a function `f` in a crate with no name, which
    came back as `::f`; `_RNvC1CC_` has an instantiating crate that is not a path at
    all, and came back as `C`. `llvm-cxxfilt` 18.1 hands back every one of these.

    Found by enumerating every `_R` name up to eight characters over a grammar-shaped
    alphabet and asking `llvm-cxxfilt` about each one this reads.
    """

    REFUSED = ("_RC1CC_", "_RNvC_1f", "_RNvC1C_", "_RNvC1CC_", "_RNvNtC_1a1b", "_RCa", "_RC_")

    @pytest.mark.parametrize("mangled", REFUSED)
    def test_a_non_digit_where_the_length_belongs_ends_the_parse(self, mangled):
        assert demangle.demangle(mangled) == mangled

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # A length written `0` is a different thing and stays legal: the reference
            # spells this `::f` too. So the test is "not a digit", not "falsy".
            ("_RNvC0_1f", "::f"),
            ("_RC1C", "C"),
            ("_RNvC1C1f", "C::f"),
            # An instantiating crate that is a real path is still skipped, not refused.
            ("_RNvC1C1fC1D", "C::f"),
        ],
    )
    def test_what_it_still_reads(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="rust") == expected


class TestWhatMutatingRealSymbolsFound:
    """Two the mutation fuzzer reached, both measured against `rustc-demangle` 0.1.28."""

    def test_a_bound_lifetime_runs_to_z_before_it_starts_counting(self):
        """`'a` through `'z`, then `'_26` and up.

        `print_lifetime_from_index` takes `depth = bound_lifetime_depth - lt` -- a
        `checked_sub`, so an index past the depth is the invalid name -- and writes
        `'a' + depth` while `depth < 26`. This carried a `depth` one larger and undid it
        at the letter, which is the same answer for the first twenty-five and not for the
        rest: the twenty-sixth came out `'_26` where the reference writes `'z`, and every
        one after it was numbered one too high. It takes a `for<>` binding twenty-six
        lifetimes to reach.
        """
        # `G<n>_` binds n+1 lifetimes; `Z_` is 61, so this binds sixty-three of them.
        spelled = demangle.demangle_strict("_RMC0FGZ_Eu", language="rust")
        assert "'x, 'y, 'z, '_26, '_27" in spelled
        assert spelled.endswith("'_60, '_61, '_62> fn()>")

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # The leading `.` is part of the marker. Searching for `llvm.` without it
            # deleted text that belongs to the symbol.
            ("_RNvCs1_1a1f.llvm.123", "a::f"),
            ("_RNvCs1_1a1fllvm.123", "_RNvCs1_1a1fllvm.123"),
            ("_RNvCs1_1a1fB2_llvm.123", "_RNvCs1_1a1fB2_llvm.123"),
            # A hash that is not one keeps the suffix, which is a vendor suffix and legal.
            ("_RNvCs1_1a1f.llvm.abc", "a::f.llvm.abc"),
        ],
    )
    def test_the_llvm_marker_includes_its_leading_dot(self, mangled, expected):
        assert demangle.demangle(mangled) == expected


class TestABaseSixtyTwoNumberIsSixtyFourBitsWide:
    """`<base-62-number> = {<0-9a-zA-Z>} "_"`, and RFC 2603 puts no width on it.

    rustc-demangle does. `integer_62` accumulates into a `u64` through `checked_mul` and
    `checked_add` and returns `x + 1` through one more, so a field whose digits overrun
    64 bits is a parse error there and in LLVM's port of it; binutils, whose Rust reader
    is neither, reads such a field by wrapping and answers `a[aa303280a73f210e]::f`.

    Python has no ceiling of its own to hit, so this one is imposed on purpose: the
    scheme is a port of rustc-demangle, and refusing where it refuses is the contract.
    rustc writes a disambiguator that is a truncated 64-bit hash, so nothing a compiler
    emits comes near the edge -- and this was found by mutating symbols that a compiler
    did emit, which is how a twelfth digit gets into one.

    The boundary is the reference's exactly, checked against
    `tools/rustc-demangle-reference/`: the digits of a crate disambiguator spell `x`,
    `integer_62` answers `x + 1`, and `opt_integer_62` adds the last one, so the widest
    that still fits is `x = 2**64 - 3`.
    """

    #: `lYGhA16ahyc` is 2**64 - 4 in base 62, `...d` is 2**64 - 3, `...e` is 2**64 - 2.
    READ = ("_RNvCslYGhA16ahyc_1a1f", "_RNvCslYGhA16ahyd_1a1f")
    REFUSED = ("_RNvCslYGhA16ahye_1a1f", "_RNvCslYGhA16ahyf_1a1f", "_RNvCsAAAAAAAAAAA_1a1f")

    @pytest.mark.parametrize("mangled", READ)
    def test_the_widest_that_fits_is_read(self, mangled):
        assert demangle.demangle_strict(mangled, language="rust") == "a::f"

    @pytest.mark.parametrize("mangled", REFUSED)
    def test_one_wider_is_refused(self, mangled):
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="rust")
        assert demangle.demangle(mangled) == mangled

    def test_an_empty_field_is_still_zero(self):
        """The bound is on the value, not on the digits: `_` alone is 0 and stays legal."""
        assert demangle.demangle_strict("_RNvCs_1a1f", language="rust") == "a::f"


class TestTheRecordedDifferencesAgainstTheTool:
    """The four are checked against `rustfilt`, not against the library's assertion.

    `rustfilt` is rustc-demangle's own command-line front end, and on two of the four it
    prints what this prints -- so calling those four "shortfalls" would be wrong twice
    over. The expectations are recorded here rather than run, because `rustfilt` is not a
    dependency of the test suite; `tools/differential.py --live --tool rustfilt` re-checks
    them wherever it is installed.
    """

    RUSTFILT: ClassVar = {
        "_ZN3foo20h05af221e174051e9abcE": "foo",
        "_ZN3foo5h05afE": "foo",
        "_RC3foo.llvm.9D1C9369@@16": "foo@@16",
        "ZN4testE": "ZN4testE",
    }

    def test_two_of_them_are_what_this_prints_too(self):
        agree = [name for name, spelled in self.RUSTFILT.items() if demangle.demangle(name) == spelled]
        assert sorted(agree) == ["ZN4testE", "_RC3foo.llvm.9D1C9369@@16"]

    def test_the_other_two_are_the_c_plus_plus_reading_of_an_ambiguous_name(self):
        """`foo::h05af` is a name C++ can have, and nothing in the symbol says which it is."""
        for name in ("_ZN3foo20h05af221e174051e9abcE", "_ZN3foo5h05afE"):
            assert demangle.detect(name) == "itanium"
            assert demangle.demangle(name) == demangle.demangle(name, language="itanium")

    def test_the_hash_rustc_actually_emits_is_still_read_as_rust(self):
        """Sixteen hex digits behind `17h`, which is every hash in a real binary."""
        assert demangle.detect("_ZN3foo17h05af221e174051e9E") == "rust"
        assert demangle.demangle("_ZN3foo17h05af221e174051e9E") == "foo"
