"""Rust mangled names: the rules that decide what is *refused*.

The spellings are pinned by the corpora in `test_conformance.py`; what is pinned here is
the boundary, which no corpus of real symbols exercises because a compiler does not emit
malformed names. Every expectation below was measured against `rustc-demangle` 0.1.28
itself -- the crate `rustfilt` is built from -- driven one whole line at a time, rather
than against `rustfilt`, whose command line scans a line for something that looks like a
symbol and so answers a different question.
"""

import demangle

from .conftest import load_corpus

#: rustc-demangle's own vectors, read through the shared loader.
UPSTREAM = load_corpus("rustc-upstream.txt")

#: The three names whose expected column is rustc-demangle's `{:#}` -- its "no hash"
#: mode -- rather than the `{}` this library prints, and the one it claims that this
#: does not. Held by name so the count below cannot drift into hiding a real failure.
KNOWN_DIFFERENCES = {
    # `{:#}` drops the trailing hash element; the default `{}` keeps it, and so do we.
    "_ZN3foo20h05af221e174051e9abcE": "foo::h05af221e174051e9abc",
    "_ZN3foo5h05afE": "foo::h05af",
    # `{:#}` also drops what LLVM's internaliser appended; the default keeps it.
    "_RC3foo.llvm.9D1C9369@@16": "foo@@16",
    # A legacy name with the leading underscore stripped *and* no hash and no `$...$`
    # escape. rustc-demangle claims it because it is only ever handed names a caller has
    # already decided are Rust's; this plugin is offered every symbol in a binary, where
    # the same rule would claim any C identifier that happens to start `ZN`.
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
