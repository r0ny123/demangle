"""Rust mangled names: the rules that decide what is *refused*.

The spellings are pinned by the corpora in `test_conformance.py`; what is pinned here is
the boundary, which no corpus of real symbols exercises because a compiler does not emit
malformed names. Every expectation below was measured against `rustc-demangle` 0.1.28
itself -- the crate `rustfilt` is built from -- driven one whole line at a time, rather
than against `rustfilt`, whose command line scans a line for something that looks like a
symbol and so answers a different question.
"""

import sys
import threading
import time
from dataclasses import replace
from typing import ClassVar

import pytest

import demangle
from demangle.core.errors import DemanglingError

from .conftest import load_corpus

#: rustc-demangle's own vectors.
UPSTREAM = load_corpus("rustc-upstream.txt")

#: Vectors whose expected column this does not produce, held by name.
KNOWN_DIFFERENCES = {
    # Detection, not spelling: not rustc's `17h` + sixteen hex digits hash shape, so the
    # C++ scheme reads it. rustc-demangle is only handed names already known to be Rust;
    # asked for Rust by name, this prints `foo` as it does.
    "_ZN3foo20h05af221e174051e9abcE": "foo::h05af221e174051e9abc",
    "_ZN3foo5h05afE": "foo::h05af",
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

    A per-character `ord(c) & 0x80` test is not the same rule: U+0100 is one character
    whose value has bit 7 clear, but the reference sees its bytes and echoes the symbol
    back unread. Neither mangling ever carries a non-ASCII character literally -- v0
    spells one in punycode and the legacy scheme writes `$u0100$` -- so anything that
    does is not a Rust symbol.
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
    the parse. A non-digit is not a length of *zero*: reading it as one would leave the
    character in place for the `_` that separates a length from its text to swallow,
    and nothing would be left over for the residual check to refuse.

    The result would be a name with an empty component, spelled as though the component
    were there and blank. `_RNvC_1f` is a function `f` in a crate with no name, and
    `_RNvC1CC_` has an instantiating crate that is not a path at all; both are refused.
    `llvm-cxxfilt` 18.1 hands back every name in `REFUSED` unchanged.
    """

    REFUSED = ("_RC1CC_", "_RNvC_1f", "_RNvC1C_", "_RNvC1CC_", "_RNvNtC_1a1b", "_RCa", "_RC_")

    @pytest.mark.parametrize("mangled", REFUSED)
    def test_a_non_digit_where_the_length_belongs_ends_the_parse(self, mangled):
        assert demangle.demangle(mangled) == mangled

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # A length written `0` stays legal: the test is "not a digit", not "falsy".
            ("_RNvC0_1f", "::f"),
            ("_RC1C", "C"),
            ("_RNvC1C1f", "C::f"),
            # An instantiating crate that is a real path is still skipped, not refused.
            ("_RNvC1C1fC1D", "C::f"),
        ],
    )
    def test_what_it_still_reads(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="rust") == expected


class TestBoundaryRulesOfTheReference:
    """Two rules, both measured against `rustc-demangle` 0.1.28."""

    def test_a_bound_lifetime_runs_to_z_before_it_starts_counting(self):
        """`'a` through `'z`, then `'_26` and up.

        `print_lifetime_from_index` takes `depth = bound_lifetime_depth - lt` -- a
        `checked_sub`, so an index past the depth is the invalid name -- and writes
        `'a' + depth` while `depth < 26`. The twenty-sixth lifetime is therefore `'z`
        and the numbering resumes after it, so an implementation whose `depth` is one
        larger and undone at the letter agrees for the first twenty-five and differs
        for the rest. It takes a `for<>` binding twenty-six lifetimes to reach.
        """
        # `G<n>_` binds n+1 lifetimes; `Z_` is 61, so this binds sixty-three of them.
        spelled = demangle.demangle_strict("_RMC0FGZ_Eu", language="rust")
        assert "'x, 'y, 'z, '_26, '_27" in spelled
        assert spelled.endswith("'_60, '_61, '_62> fn()>")

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # The leading `.` is part of the marker.
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
    emits comes near the edge; a mutated symbol can reach it by gaining a twelfth digit.

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


class TestTheRecordedDifferences:
    """The recorded differences from the upstream vectors, measured against the reference.

    Measured with `tools/rustc-demangle-reference`, which hands the whole name to
    `try_demangle`. `rustfilt` is not the measure here: it scans a line for `_ZN` or `_R`
    and a run of symbol characters, so it neither sees `ZN4testE` nor the `@@16` inside
    `.llvm.9D1C9369@@16`.
    """

    REFERENCE: ClassVar = {
        "_ZN3foo20h05af221e174051e9abcE": "foo",
        "_ZN3foo5h05afE": "foo",
        "_RC3foo.llvm.9D1C9369@@16": "foo",
        "ZN4testE": "test",
    }

    def test_two_of_them_are_what_this_prints_too(self):
        agree = [name for name, spelled in self.REFERENCE.items() if demangle.demangle(name) == spelled]
        assert sorted(agree) == ["ZN4testE", "_RC3foo.llvm.9D1C9369@@16"]

    def test_all_four_are_what_this_prints_when_rust_is_asked_for(self):
        for name, spelled in self.REFERENCE.items():
            assert demangle.demangle(name, language="rust") == spelled

    def test_the_other_two_are_the_c_plus_plus_reading_of_an_ambiguous_name(self):
        """`foo::h05af` is a name C++ can have, and nothing in the symbol says which it is.

        rustc only ever writes `17h` and sixteen hex digits, so a shorter or longer run is
        not evidence of Rust -- and claiming one would read C++'s `a::hbad` as `a`.
        """
        for name in ("_ZN3foo20h05af221e174051e9abcE", "_ZN3foo5h05afE", "_ZN1a4hbadE"):
            assert demangle.detect(name) == "itanium"
            assert demangle.demangle(name) == demangle.demangle(name, language="itanium")

    def test_the_hash_rustc_actually_emits_is_still_read_as_rust(self):
        """Sixteen hex digits behind `17h`, which is every hash in a real binary."""
        assert demangle.detect("_ZN3foo17h05af221e174051e9E") == "rust"
        assert demangle.demangle("_ZN3foo17h05af221e174051e9E") == "foo"

    def test_a_short_hash_is_kept_when_the_hash_is_kept(self):
        assert demangle.demangle("_ZN3foo5h05afE", language="rust", style=TestKeepingTheHash.KEEP) == "foo::h05af"


class TestAnLlvmHashCarriesTheElfVersion:
    """rustc-demangle drops `.llvm.<hash>` first, and its hash alphabet includes `@`.

    So a version written after the hash goes with it. `core` splits the ELF version off
    before a scheme sees the name, which would leave `foo@@16`; the scheme splits it
    itself, after the reference's rule. Expectations are `tools/rustc-demangle-reference`'s.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_RC3foo.llvm.9D1C9369@@16", "foo"),
            ("_RNvCs1_1a1f.llvm.0123ABCD@", "a::f"),
            ("_ZN3foo17h05af221e174051e9E.llvm.9D1C9369@@16", "foo"),
        ],
    )
    def test_the_version_inside_the_hash_goes_with_it(self, mangled, expected):
        assert demangle.demangle(mangled) == expected
        assert demangle.demangle(mangled, language="rust") == expected
        assert demangle.parse(mangled).spell() == expected

    def test_a_legacy_name_asked_for_as_rust(self):
        assert demangle.demangle("_ZN3foo3barE.llvm.9D1C9369@@16", language="rust") == "foo::bar"

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # Not a hash (`V`), so the version is a decoration as for any ELF symbol.
            ("_RNvC1a1f@@V1", "a::f@@V1"),
            ("_ZN3foo17h05af221e174051e9E@@GLIBC_2.17", "foo@@GLIBC_2.17"),
            ("_RC3foo@@V.llvm.ABC", "foo@@V.llvm.ABC"),
        ],
    )
    def test_a_version_on_its_own_is_still_passed_through(self, mangled, expected):
        assert demangle.detect(mangled) == "rust"
        assert demangle.demangle(mangled) == expected


class TestABareZnIsReadWhenItParses:
    """rustc-demangle takes `ZN...E` as it takes `_ZN...E`: dbghelp strips the underscore.

    Nothing else claims a name without it, so the grammar is the evidence: `ZN`, a
    digit, and a path the legacy reader consumes to its `E`. None of the 652,000 symbols
    in this box's libraries and binaries starts `ZN`.
    """

    def test_a_name_past_the_input_bound_is_not_read_to_decide(self):
        """Detection reads the whole name here, so it is held to the parse's input bound."""
        name = "ZN" + "1a" * 200_000 + "E"
        started = time.perf_counter()
        assert demangle.detect(name) is None
        assert demangle.demangle(name) == name
        assert time.perf_counter() - started < 1

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("ZN4testE", "test"),
            ("ZN3foo3barE", "foo::bar"),
            ("ZN4testE.llvm.9D1C9369", "test"),
            ("ZN4testE@@V1", "test@@V1"),
        ],
    )
    def test_it_is_claimed_and_read(self, mangled, expected):
        assert demangle.detect(mangled) == "rust"
        assert demangle.demangle(mangled) == expected

    @pytest.mark.parametrize(
        "mangled",
        ["ZN", "ZNE", "ZN4test", "ZN4testEv", "ZN3foo3barEv", "ZNK3fooE", "ZNSt3fooE", "ZNever"],
    )
    def test_what_does_not_parse_is_left_alone(self, mangled):
        assert demangle.detect(mangled) is None
        assert demangle.demangle(mangled) == mangled


class TestWhatOpensAVZeroName:
    """`_R` is not on its own enough to claim a name.

    `<symbol-name> ::= _R <path> [<instantiating-crate>]`, and every `<path>` production
    opens with one of seven letters. Without that second character the plugin would claim
    CodeWarrior's `__RTTI__40TObjOwnerDerivedFromIObj<12CStringTable>`, which is in this
    package's own corpus: `demangle` would still fall through to the scheme that owns it,
    but `detect` -- a public answer of its own, and the only one a caller labelling a
    symbol table gets -- would name the wrong scheme.
    """

    @pytest.mark.parametrize(
        "mangled",
        ["_RNvC1a1f", "__RNvC1a1f", "_RC1a", "_RB0_", "_RIC1aE", "_RMC1aC1b", "_RXC1aC1bC1c", "_RYC1aC1b"],
    )
    def test_every_path_production_is_still_claimed(self, mangled):
        assert demangle.detect(mangled) == "rust"

    @pytest.mark.parametrize("mangled", ["_R", "__R", "_RT", "_Rv", "_R$", "__RTTI__3Foo"])
    def test_a_name_that_opens_no_path_is_left_alone(self, mangled):
        assert demangle.detect(mangled) != "rust"

    def test_the_codewarrior_symbol_goes_to_codewarrior(self):
        mangled = "__RTTI__40TObjOwnerDerivedFromIObj<12CStringTable>"
        assert demangle.detect(mangled) == "codewarrior"
        assert demangle.demangle(mangled) == "TObjOwnerDerivedFromIObj<CStringTable>::__RTTI"


class TestSevenEdgesSettledAgainstTheReference:
    """Each of these pins one rule of rustc-demangle, read line by line.

    Every expectation is the reference's own answer, from
    `tools/rustc-demangle-reference`. Asked with `language="rust"` throughout: several
    are names the detector rightly leaves to C++ -- a bare `h` with no digits behind it
    is a name C++ can have -- and the rule under test is the reader's, not the detector's.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # A path with no closing `E` is not a path.
            ("_ZN3std", "_ZN3std"),
            # An escape the reference does not know is printed as it stands.
            ("_ZN11test$XX$fooE", "test$XX$foo"),
            # `$u..$` takes lowercase hex only, and a control character stays literal.
            ("_ZN14test$u00ab$fooE", "test«foo"),
            ("_ZN14test$u00AB$fooE", "test$u00AB$foo"),
            ("_ZN14test$u0000$fooE", "test$u0000$foo"),
            # `char::from_u32` refuses a surrogate; Python's `chr` does not.
            ("_ZN14test$uD800$fooE", "test$uD800$foo"),
            # A bare `h` is the hash marker with no digits, so the hash is empty.
            ("_ZN4test1hE", "test"),
            # The hash may hold uppercase hex; an uppercase `H` is not the marker.
            ("_ZN4test17h0123456789ABCDEFE", "test"),
            ("_ZN4test17H0123456789ABCDEFE", "test::H0123456789ABCDEF"),
            # A punycode body spelling a surrogate falls back to the raw form.
            ("_RCu4_2d9b", "punycode{2d9b}"),
            # A bare `R`, the underscore stripped by a symbol table, when Rust is asked for.
            ("RNvC1a1f", "a::f"),
        ],
    )
    def test_what_the_reference_prints(self, mangled, expected):
        assert demangle.demangle(mangled, language="rust") == expected
        assert demangle.demangleb(mangled.encode(), language="rust") == expected.encode()

    def test_an_empty_hash_is_still_a_hash_when_the_hash_is_kept(self):
        """`""` is false, so `keep_hash` keeps what the reference spells `::h`."""
        assert demangle.demangle("_ZN4test1hE", language="rust", style=TestKeepingTheHash.KEEP) == "test::h"

    def test_an_uppercase_hash_is_claimed_as_the_parser_reads_it(self):
        """`detect` and the parser accept either case of hex, so both routes read the
        name as Rust."""
        assert demangle.detect("_ZN4test17h0123456789ABCDEFE") == "rust"
        assert demangle.demangle("_ZN4test17h0123456789ABCDEFE") == "test"

    def test_a_bare_r_is_not_claimed_unasked(self):
        """Too broad a claim to make about every symbol in a binary; `_R` is still needed."""
        assert demangle.detect("RNvC1a1f") is None
        assert demangle.demangle("RNvC1a1f") == "RNvC1a1f"


class TestKeepingTheHash:
    """`keep_hash`: rustc-demangle's `{}` rather than the `{:#}` this spells by default.

    One option and not two, because the reference has one bit for all of it: the same
    `alternate` flag suppresses the legacy `17h<16 hex>` component, the v0 crate
    disambiguator and the type suffix on an integer const. Scored at 5,751 of the 5,753
    corpus names the reference reads.

    What is pinned here is the invariant the spellings rest on, over every Rust name in
    every corpus: the tree spells exactly what the text path spells. Both manglings emit
    one stream of fragments whether they are building text or a tree, and this option
    adds fragments to that stream, so a divergence here would mean the two had come
    apart.
    """

    KEEP: ClassVar = demangle.style("llvm", rust={"keep_hash": True})

    @pytest.mark.parametrize(
        ("mangled", "default", "kept"),
        [
            ("_ZN4core3fmt5write17h05af221e174051e9E", "core::fmt::write", "core::fmt::write::h05af221e174051e9"),
            ("_RNvCs1_1a1f", "a::f", "a[3]::f"),
            ("_RNvCs0_1a1f", "a::f", "a[2]::f"),
            # No disambiguator written, so nothing is spelled -- not a zero.
            ("_RNvC1a1f", "a::f", "a::f"),
        ],
    )
    def test_what_the_flag_reaches(self, mangled, default, kept):
        assert demangle.demangle(mangled) == default
        assert demangle.demangle(mangled, style=self.KEEP) == kept

    def test_the_tree_still_spells_what_the_text_spells(self, subtests):
        checked = 0
        for mangled, _expected in UPSTREAM:
            try:
                text = demangle.demangle_strict(mangled, style=self.KEEP)
            except DemanglingError:
                continue
            with subtests.test(mangled=mangled):
                assert demangle.parse(mangled, style=self.KEEP).spell(style=self.KEEP) == text
            checked += 1
        assert checked, "no vector was read under the option; has the corpus moved?"


def _on_a_deep_stack(work):
    """Run `work` where the interpreter's stack is not the binding bound."""
    seen = []
    previous_limit = sys.getrecursionlimit()
    previous_size = threading.stack_size(128 << 20)
    try:
        sys.setrecursionlimit(100_000)
        thread = threading.Thread(target=lambda: seen.append(work()))
        thread.start()
        thread.join()
    finally:
        threading.stack_size(previous_size)
        sys.setrecursionlimit(previous_limit)
    assert len(seen) == 1, "the work raised"
    return seen[0]


class TestV0NestingFollowsMaxDepth:
    """v0 reads as deep as `max_depth` says, as Itanium does.

    It takes its ceiling from the caller's limits, so `RELAXED_LIMITS` reaches deeper
    than `DEFAULT_LIMITS` and the refusal is the bound, not a parse error.
    rustc-demangle's own ceiling is a fixed 500.
    """

    DEFAULT = demangle.DEFAULT_LIMITS.max_depth

    @staticmethod
    def nested(levels):
        return "_R" + "Nv" * levels + "C1a" + "1b" * levels

    def _bound_reported(self, mangled, limits):
        with pytest.raises(demangle.LimitExceeded) as caught:
            demangle.demangle_strict(mangled, language="rust", limits=limits)
        assert caught.value.limit_name == "recursion depth"
        return caught.value.limit_value

    def test_the_default_still_stops_where_it_did(self):
        assert demangle.demangle_strict(self.nested(self.DEFAULT - 1)).endswith("::b")
        assert self._bound_reported(self.nested(self.DEFAULT), demangle.DEFAULT_LIMITS) == self.DEFAULT
        assert demangle.demangle(self.nested(self.DEFAULT)) == self.nested(self.DEFAULT)

    def test_relaxed_limits_read_past_it(self):
        spelled = demangle.demangle_strict(self.nested(400), limits=demangle.RELAXED_LIMITS)
        assert spelled == "a" + "::b" * 400

    @pytest.mark.parametrize("asked", [8, 64, 2048])
    def test_a_caller_is_told_its_own_figure(self, asked):
        limits = replace(demangle.RELAXED_LIMITS, max_depth=asked)
        assert _on_a_deep_stack(lambda: self._bound_reported(self.nested(asked + 10), limits)) == asked

    def test_just_under_a_raised_bound_is_read(self):
        limits = replace(demangle.RELAXED_LIMITS, max_depth=2048)
        spelled = _on_a_deep_stack(lambda: demangle.demangle_strict(self.nested(2047), limits=limits))
        assert spelled == "a" + "::b" * 2047

    @pytest.mark.parametrize(
        "mangled",
        [
            "_R" + "Nv" * 4000 + "C1a" + "1b" * 4000,
            "_RINvC1a1f" + "R" * 4000 + "uE",
            "_R" + "INvC1a1f" * 4000 + "u" + "E" * 4000,
            "_RIC0K" + "R" * 4000 + "h1_E",
            "_RINvC1a1f" + "F" * 4000 + "Eu" * 4000 + "E",
            "_RNvC1a1f" + "Nv" * 4000 + "C1b" + "1c" * 4000,
        ],
        ids=["path", "reference", "generic", "const", "fn-type", "instantiating-crate"],
    )
    def test_nesting_past_the_interpreters_stack_is_refused_cleanly(self, mangled):
        with pytest.raises(demangle.LimitExceeded) as caught:
            demangle.demangle_strict(mangled, language="rust", limits=demangle.RELAXED_LIMITS)
        assert caught.value.limit_name == "recursion depth"
        with pytest.raises(demangle.LimitExceeded):
            demangle.parse(mangled, language="rust", limits=demangle.RELAXED_LIMITS)
        assert demangle.demangle(mangled, limits=demangle.RELAXED_LIMITS) == mangled
