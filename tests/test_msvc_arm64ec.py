"""ARM64EC: the hybrid ABI's decorated names.

A function compiled for ARM64EC carries `$$h` in its decorated name, inserted
immediately after the fully qualified name and before the type encoding. Nothing reads
it: `llvm-undname` 18.1.3 refuses `?func@@$$hYAXXZ` outright, and so does current
upstream -- there is no `$$h` anywhere in `MicrosoftDemangle.cpp`. So there is no
reference *spelling* to copy, and inventing one is how a demangler starts inventing.

What there is instead is a normative *rule*. LLVM's `getArm64ECDemangledFunctionName` in
`lib/IR/Mangler.cpp` says what an ARM64EC name is the hybrid form of, and it is what the
compiler emits an `EXPORTAS` directive against -- so its answer is the name the linker
resolves. The rule is short: an MD5 name loses a trailing `$$h@`, and any other loses the
first `$$h` wherever it stands. This reads the name that leaves.

That makes the conformance check exact without a reference binary: every name in
`msvc-llvm-corpus.txt` with the marker inserted where LLVM's *mangler*
(`getArm64ECMangledFunctionName`) puts it must demangle to what the name without it
demangles to -- and that column came from `llvm-undname`.
"""

import demangle

from .conftest import load_corpus
from .test_conformance import MSVC_ARM64EC_EXACT, MSVC_ARM64EC_TOTAL


class TestTheHybridMarker:
    def test_the_corpus_is_the_whole_of_it(self):
        assert len(load_corpus("msvc-arm64ec.txt")) == MSVC_ARM64EC_TOTAL

    def test_every_vector_matches(self):
        for mangled, expected in load_corpus("msvc-arm64ec.txt"):
            assert demangle.demangle(mangled) == expected, mangled

    def test_the_pinned_number_is_still_accurate(self):
        score = sum(
            1 for mangled, expected in load_corpus("msvc-arm64ec.txt") if demangle.demangle(mangled) == expected
        )
        assert score == MSVC_ARM64EC_EXACT

    def test_a_hybrid_name_says_what_the_plain_one_says(self):
        """The equality is the specification, so it is asserted as one."""
        for mangled, _ in load_corpus("msvc-arm64ec.txt"):
            plain = mangled.replace("$$h", "", 1) if not mangled.startswith("??@") else mangled[:-4]
            assert demangle.demangle(mangled) == demangle.demangle(plain), mangled

    def test_the_md5_form_loses_its_marker_from_the_end(self):
        """`??@<hash>@$$h@`, which the rule handles separately from every other name."""
        hashed = "??@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa@"
        assert demangle.demangle(hashed + "$$h@") == hashed

    def test_it_is_a_fallback_and_not_a_first_step(self):
        """A name that already reads is never rewritten, whatever characters it holds."""
        for mangled, expected in load_corpus("msvc-llvm-corpus.txt"):
            assert demangle.demangle(mangled) == expected, mangled
        # A `$$h` inside an identifier or an MD5 hash is not the marker; `llvm-undname`
        # reads both as part of the name.
        assert demangle.demangle("?foo$$hbar@@YAXXZ") == "void __cdecl foo$$hbar(void)"
        assert demangle.demangle("?foo$$hbar@@3HA") == "int foo$$hbar"
        assert demangle.demangle("??@$$hYAXP6AXQEAH@Z1@Z") == "??@$$hYAXP6AXQEAH@"
        # A `$$h` in the middle of a local type's name; llvm-undname glues `?` to
        # `FTypeWithQuals`, this keeps the space.
        assert (
            demangle.demangle("?b@FTypeWithQuals@@3U?@YAHXZ@$$h4U<unnamed-type-v>@?1??1@YAHXZ@A")
            == "struct `int __cdecl FTypeWithQuals(void)'::`2'::$$h4U<unnamed-type-v>::YAHXZ::? FTypeWithQuals::b"
        )

    def test_the_rule_removes_one_marker_and_not_a_run_of_them(self):
        """`getArm64ECMangledFunctionName` inserts one marker into a name that has none.

        So a name carrying two is not one it can produce, and
        `getArm64ECDemangledFunctionName` -- which removes the *first* and no more --
        leaves a name that still does not read. Stripping them one at a time until
        none is left would turn `?f@@$$h$$hYAXXZ` into `void __cdecl f(void)`.
        """
        for name in ("?f@@$$h$$hYAXXZ", "?priv_stat_foo@S@@$$h$$hYA?CHXZ", "?f@@$$hYAX$$hXZ"):
            assert demangle.demangle(name) == name, name
        assert demangle.demangle("?f@@$$hYAXXZ") == "void __cdecl f(void)"


class TestWhatIsNotClaimed:
    def test_the_marker_alone_is_not_a_name(self):
        for name in ("$$hYAXXZ", "$$h", "?$$h", "??@$$h@"):
            assert demangle.demangle(name) == name, name

    def test_the_hash_form_is_recognised_and_deliberately_not_read(self):
        """`#name` is the same marker for a symbol that is not a C++ name at all.

        Reading it would mean claiming every string that opens with a `#` in order to
        strip one character, and `demangle()` is offered every symbol in a binary. LLVM
        applies its own rule only to objects it has already established are ARM64EC; this
        has no such context. Recorded as a decision rather than left as a silence.
        """
        for name in ("#foo", "#memcpy", "#"):
            assert demangle.demangle(name) == name

    def test_a_name_that_does_not_read_without_the_marker_does_not_read_with_it(self):
        assert demangle.demangle("?$$hQQQ") == "?$$hQQQ"
