"""Rust mangled names: the rules that decide what is *refused*.

The spellings are pinned by the corpora in `test_conformance.py`; what is pinned here is
the boundary, which no corpus of real symbols exercises because a compiler does not emit
malformed names. Every expectation below was measured against `rustc-demangle` 0.1.28
itself -- the crate `rustfilt` is built from -- driven one whole line at a time, rather
than against `rustfilt`, whose command line scans a line for something that looks like a
symbol and so answers a different question.
"""

from typing import ClassVar

import demangle

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
