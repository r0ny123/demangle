"""Replay the checked-in corpora.

Pass counts are pinned as exact numbers rather than floors. A floor lets a fix that
silently breaks something else slip through as long as the net stays positive; an exact
number means every change in either direction has to be an explicit edit to this file,
with a reason in the commit that makes it.

These tests need neither a compiler nor the reference demanglers: the expected output is
recorded in the corpus files. Regenerate them with tools/generate_corpus.py.
"""

import pytest

import demangle
from demangle.core.errors import DemanglingError

from .conftest import CONFORMANCE, load_corpus

# Measured against llvm-cxxfilt 18.1.3 and GNU c++filt 2.42 on the checked-in corpora.
ITANIUM_LLVM_TOTAL, ITANIUM_LLVM_EXACT = 280, 280
ITANIUM_GNU_TOTAL, ITANIUM_GNU_EXACT = 300, 298
MSVC_TOTAL, MSVC_EXACT = 609, 609
LIBSTDCXX_TOTAL, LIBSTDCXX_EXACT = 5913, 5913
REGRESSIONS_TOTAL, REGRESSIONS_EXACT = 31, 31

#: Bare `<type>` encodings -- `Pi`, `PKFvRiE` -- read by `demangle_type()` rather than by
#: `demangle()`, which refuses every one of them on purpose. The same 1,076 encodings are
#: scored against *both* references, one corpus each, because the two spell the same
#: types differently. Replayed and asserted by tests/test_types.py.
TYPES_TOTAL, TYPES_LLVM_EXACT, TYPES_GNU_EXACT = 1076, 1076, 1073

#: What each of `llvm-undname`'s five suppression flags changes, over the 609 names of
#: `msvc-llvm-corpus.txt`. Only the names a flag actually changes are recorded, so this
#: counts *differences* the reference makes and not names. Replayed by tests/test_msvc.py.
MSVC_SUPPRESSIONS_TOTAL, MSVC_SUPPRESSIONS_EXACT = 1253, 1250

#: RTTI type descriptor *names* -- a `.` and a bare type encoding, which is how the linker
#: spells the string a `type_info` points at -- and the descriptor objects beside them.
#: Both against `llvm-undname`. Replayed by tests/test_msvc_descriptors.py.
MSVC_DESCRIPTORS_TOTAL, MSVC_DESCRIPTORS_EXACT = 106, 106

#: ARM64EC hybrid names: the 609 of `msvc-llvm-corpus.txt` with the `$$h` marker inserted
#: where LLVM's mangler puts it, expecting what the name without it demangles to. Three
#: are absent because LLVM's mangler tags functions and those three are not functions.
#: Replayed by tests/test_msvc_arm64ec.py.
MSVC_ARM64EC_TOTAL, MSVC_ARM64EC_EXACT = 606, 606

#: Swift's own `simplified-manglings.txt`, scored under `SwiftOptions.simplified()`.
#: What `swift-demangle --simplified` prints, which is what Xcode and LLDB show a user.
#: Replayed by tests/test_swift_simplified.py.
SWIFT_SIMPLIFIED_TOTAL, SWIFT_SIMPLIFIED_EXACT = 217, 217
RUST_TOTAL, RUST_EXACT = 5316, 5316
RUST_TOOLCHAIN_TOTAL, RUST_TOOLCHAIN_EXACT = 394, 394

#: What the CLI's `-p` prints, against `c++filt -p`, over the libstdc++ and GNU-style
#: corpora. The differences are deliberate and are enumerated in tests/test_signature.py.
NO_PARAMS_TOTAL, NO_PARAMS_AGREE = 6213, 6132

#: JNI, pinned like Go and Nim -- there is no reference demangler for these at all, so
#: the expected column is this library's own reading and the count alone would be a
#: record of agreeing with itself. What makes it mean something is the round-trip
#: property in tests/test_jni.py: re-encoding a reading reproduces the symbol.
JNI_TOTAL, JNI_EXACT = 50, 50

#: Go is pinned like the rest, but what it is pinned *against* is different: there is no
#: reference demangler for Go, so the expected column is this library's own decoding and
#: the count alone would be a record of agreeing with itself. What makes it mean
#: something is the round-trip property in tests/test_go.py -- re-escaping a decoded path
#: must reproduce the bytes Go's own PathToPrefix wrote. The count pins the corpus; the
#: property is the correctness argument.
GO_TOTAL, GO_EXACT = 1498, 1498

#: D, against GNU binutils' D demangler over the shipped libgphobos and libgdruntime.
D_TOTAL, D_EXACT = 1257, 1257

#: Free Pascal. Like Go and Nim there is no reference demangler, so the expected column
#: is this library's own reading and the count alone would be a record of agreeing with
#: itself. What makes it mean something is the re-assembly property in
#: tests/test_pascal.py -- checked over all 236,570 readable symbols in the shipped
#: runtime, not just this sample -- and the independent check against `ppudump`.
PASCAL_TOTAL, PASCAL_EXACT = 3899, 3899

#: Nim, against the name the compiler itself recorded in its `.ndi` debug-mapping files.
#: Unlike the others this is not a reference demangler -- Nim has none -- so the count is
#: agreement with the compiler's own record, and the correctness *property* lives in
#: tests/test_nim.py: what this reads must re-mangle to the symbol.
NIM_TOTAL, NIM_EXACT = 2115, 2115

#: Swift, against `swift-demangle` 5.10.1. The corpus is a stratified sample of the
#: shipped runtime and Foundation -- up to four symbols per distinct set of
#: demangling-tree node kinds -- plus every current-mangling case from the compiler's own
#: test/Demangle/Inputs/manglings.txt that the reference itself can read, in both the
#: current mangling and Swift 3's.
SWIFT_TOTAL, SWIFT_EXACT = 8494, 8494

#: Objective-C. No reference demangler exists, so the expected column is what the
#: *declaration* said: every symbol here was emitted by clang 18.1.3 for Objective-C this
#: package wrote, or read out of the shipped GCC runtime, libobjc.a. Three ABIs are
#: represented. The mangling's own losses are named rather than counted, in
#: tests/conformance/objc-lossy.txt.
OBJC_TOTAL, OBJC_EXACT = 2665, 2665

#: Delphi/C++Builder. No compiler on this platform, so the reference is a recorded one:
#: `tests/conformance/delphi-tdump.txt` is a dump of the real `tdump.exe -q -um` over the
#: export tables of real BPLs and C++Builder DLLs, and its expected column is what
#: Embarcadero's own unmangler printed. Both files are replayed here -- the sample
#: because it is the one a reader will open, the whole table because the sample is not
#: the measurement. The 10 MSVC `@name@N` decorations sitting in the same tables are
#: refused, and are pinned in `tests/conformance/delphi-refusals.txt`.
DELPHI_TOTAL, DELPHI_EXACT = 68, 68
DELPHI_TABLE_TOTAL, DELPHI_TABLE_EXACT = 11363, 11363
#: The constructs those export tables never produce -- a quarter of the parser, which the
#: whole dump leaves unexercised. Hand-built, and corroborated against an independent
#: implementation rather than against TDUMP, which is weaker evidence and kept in its own
#: file so it cannot be mistaken for the row above. See the file's header.
DELPHI_CONSTRUCT_TOTAL, DELPHI_CONSTRUCT_EXACT = 53, 53

#: Pre-Itanium C++, against libiberty's own `demangle-expected` at GCC 8.3.0: the 662
#: cases it marks `--format=gnu`, `--format=lucid`, `--format=arm` or `--format=hp`,
#: scored under both settings of `DMGL_PARAMS`. The style is a *column* in that corpus
#: rather than something detection can work out, so it is replayed by tests/test_gnuv2.py
#: rather than here or by tools/differential.py.
GNUV2_TOTAL, GNUV2_EXACT = 1324, 1324

#: Metrowerks CodeWarrior, against `encounter/cwdemangle`'s own test module. libiberty
#: never read this mangling, so that tool -- the one decompilation projects for GameCube
#: and Wii titles run -- is the reference there is. The options are a column, so it is
#: replayed by tests/test_codewarrior.py rather than here or by tools/differential.py.
CODEWARRIOR_TOTAL, CODEWARRIOR_EXACT = 47, 47

#: Ada, against libiberty's own `demangle-expected` at GCC 8.3.0: the 34 cases it marks
#: `--format=gnat`. One is a name the reference declines -- it prints `<x_E>` -- and is
#: recorded as the name unchanged, which is what `demangle()` answers for one it will not
#: claim. Read by language rather than by detection, because the *point* of this scheme's
#: detection is that it declines a name carrying no GNAT-specific encoding; the count that
#: do auto-detect is pinned separately below. Replayed by tests/test_ada.py.
ADA_TOTAL, ADA_EXACT = 34, 34

#: How many of the 33 readable vectors detection claims on its own. The 4 it does not are
#: `yz__qrs`, `x__m1`, `x__m3` and `x__y__j`: lower-case identifiers joined by `__` and
#: nothing else, which is to say names indistinguishable from ordinary C ones. Claiming
#: those would mean claiming 6,764 real symbols from this machine's libraries -- measured,
#: see tests/test_ada.py -- so they are read on request and not by guess.
ADA_AUTODETECTED = 29

# The GNU shortfalls are not ours to fix: in each, the two references disagree about
# what goes in the substitution table, not about how to spell it. Matching both would
# mean two incompatible parses of the same bytes, so we follow LLVM and pin the
# disagreements by name -- a count alone would let one be traded for a new defect.
#: Names where `llvm-undname` discards part of the symbol, so following it would mean
#: spelling distinct symbols identically. It reads the first element of a vftable's base
#: path and drops the rest, mapping `??_7A@B@@6BC@D@@@`, `??_7A@B@@6BC@D@@E@F@@@` and
#: `??_7A@B@@6BC@D@@E@F@@G@H@@@` -- three different vtables -- onto one spelling. Checked
#: against llvm-undname 16, 18 and 20; all three lose it identically. We keep the whole
#: path, joined the way Microsoft spells one.
UNDNAME_DIVERGENCES = [
    "??_7A@B@@6BC@D@@E@F@@@",
    "??_7A@B@@6BC@D@@E@F@@G@H@@@",
    "??_7A@@6BB@@C@@@",
    "??_7A@@6BB@@C@@D@@@",
]

GNU_DIVERGENCES = [
    # Inside a requires-clause, llvm records the template parameter symbolically (`T`)
    # and GNU records the argument bound to it.
    "_ZN6modern8measuredINSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEEQ5SizedIT_EEEmRKS7_",
    # A generic lambda's `operator()`: llvm resolves the back-reference to the lambda's
    # declared parameter, GNU to the argument `operator()` was instantiated with.
    "_ZZN6modern13genericLambdaEvENKUlTyT_E_clIiEEDaS0_",
]


def _score(corpus, style, language=None):
    pairs = load_corpus(corpus)
    exact = 0
    for mangled, expected in pairs:
        try:
            got = demangle.demangle_strict(mangled, style=style, language=language)
        except (DemanglingError, RecursionError):
            continue
        exact += got == expected
    return len(pairs), exact


def test_itanium_matches_llvm_cxxfilt():
    total, exact = _score("itanium-real-world.txt", "llvm")
    assert (total, exact) == (ITANIUM_LLVM_TOTAL, ITANIUM_LLVM_EXACT)


def test_itanium_matches_gnu_cxxfilt():
    total, exact = _score("itanium-real-world-gnu.txt", "gnu")
    assert (total, exact) == (ITANIUM_GNU_TOTAL, ITANIUM_GNU_EXACT)


def test_jni_corpus():
    """See `JNI_TOTAL` for what this does and does not establish."""
    total, exact = _score("jni-real-world.txt", "llvm", language="jni")
    assert (total, exact) == (JNI_TOTAL, JNI_EXACT)


def test_go_corpus():
    """See `GO_TOTAL` for what this does and does not establish."""
    total, exact = _score("go-real-world.txt", "llvm", language="go")
    assert (total, exact) == (GO_TOTAL, GO_EXACT)


def test_d_matches_gnu_dlang_demangler():
    total, exact = _score("d-real-world.txt", "llvm", language="d")
    assert (total, exact) == (D_TOTAL, D_EXACT)


def test_swift_matches_swift_demangle():
    total, exact = _score("swift-real-world.txt", "llvm", language="swift")
    assert (total, exact) == (SWIFT_TOTAL, SWIFT_EXACT)


def test_pascal_corpus():
    """See `PASCAL_TOTAL` for what this does and does not establish."""
    total, exact = _score("pascal-real-world.txt", "llvm", language="pascal")
    assert (total, exact) == (PASCAL_TOTAL, PASCAL_EXACT)


def test_nim_matches_the_compilers_own_record():
    """See `NIM_TOTAL` for what this does and does not establish."""
    total, exact = _score("nim-real-world.txt", "llvm", language="nim")
    assert (total, exact) == (NIM_TOTAL, NIM_EXACT)


def test_msvc_matches_llvm_undname():
    total, exact = _score("msvc-llvm-corpus.txt", "llvm", language="msvc")
    assert (total, exact) == (MSVC_TOTAL, MSVC_EXACT)


def test_matches_llvm_cxxfilt_on_the_system_libstdcxx():
    """Every mangled symbol the shipped libstdc++ exports.

    Real released C++ rather than something compiled for the test, which is the point:
    it carries what only a real standard library produces.
    """
    total, exact = _score("itanium-libstdcxx.txt", "llvm")
    assert (total, exact) == (LIBSTDCXX_TOTAL, LIBSTDCXX_EXACT)


def test_regression_corpus():
    """Names that each exposed a distinct defect. Every one must stay fixed."""
    total, exact = _score("itanium-regressions.txt", "llvm")
    assert (total, exact) == (REGRESSIONS_TOTAL, REGRESSIONS_EXACT)


def test_rust_matches_rustc_demangle():
    """Auto-detected, not forced.

    Forcing `language="rust"` would skip detection, which is the part most likely to be
    wrong: legacy Rust mangling *is* Itanium mangling, so a detection miss hands the
    name to the C++ parser and yields a plausible but quite wrong spelling rather than
    an error.
    """
    total, exact = _score("rust-real-world.txt", "llvm")
    assert (total, exact) == (RUST_TOTAL, RUST_EXACT)


def test_rust_matches_the_shipped_toolchain():
    """Symbols from rustc's own libraries, including ELF-versioned ones."""
    total, exact = _score("rust-toolchain.txt", "llvm")
    assert (total, exact) == (RUST_TOOLCHAIN_TOTAL, RUST_TOOLCHAIN_EXACT)


def test_objc_matches_what_the_declaration_said():
    """Auto-detected, not forced.

    The forms that matter here are the ones shaped like ordinary C identifiers -- the
    `_i_`/`_c_` method mangling above all -- so what has to hold is that detection
    reaches them without claiming anything else.
    """
    total, exact = _score("objc-real-world.txt", "llvm")
    assert (total, exact) == (OBJC_TOTAL, OBJC_EXACT)


def test_delphi_matches_embarcadero_unmangle():
    """See `DELPHI_TOTAL` for what this does and does not establish."""
    total, exact = _score("delphi-real-world.txt", "llvm")
    assert (total, exact) == (DELPHI_TOTAL, DELPHI_EXACT)


def test_delphi_whole_export_tables_match_the_dump():
    """The sample is not the measurement. The measurement is the whole TDUMP dump.

    This replays it. The assertion it replaced compared a constant to itself, so it
    could not fail and never read the dump at all -- while the parser it was standing
    for diverged from that reference on 692 of these names.
    """
    total, exact = _score("delphi-tdump.txt", "llvm")
    assert (total, exact) == (DELPHI_TABLE_TOTAL, DELPHI_TABLE_EXACT)


def test_delphi_constructs_absent_from_the_export_tables():
    """See `DELPHI_CONSTRUCT_TOTAL` for what this does and does not establish."""
    total, exact = _score("delphi-constructs.txt", "llvm")
    assert (total, exact) == (DELPHI_CONSTRUCT_TOTAL, DELPHI_CONSTRUCT_EXACT)


def test_gnu_shortfalls_are_only_the_known_reference_divergences():
    """Pin *which* names fail under GNU, not merely how many."""
    failing = []
    for mangled, expected in load_corpus("itanium-real-world-gnu.txt"):
        try:
            got = demangle.demangle_strict(mangled, style="gnu")
        except (DemanglingError, RecursionError):
            failing.append(mangled)
            continue
        if got != expected:
            failing.append(mangled)
    assert failing == GNU_DIVERGENCES


@pytest.mark.parametrize(
    "corpus",
    [
        "itanium-real-world.txt",
        "msvc-llvm-corpus.txt",
        "itanium-libstdcxx.txt",
        "rust-real-world.txt",
        "rust-toolchain.txt",
        "objc-real-world.txt",
    ],
)
def test_best_effort_never_raises_on_any_corpus_name(corpus):
    """Whatever the corpus holds, `demangle()` answers rather than raising."""
    for mangled, _ in load_corpus(corpus):
        assert isinstance(demangle.demangle(mangled), str)


def test_llvm_undname_loses_a_vftable_base_path_and_we_do_not():
    """The divergence is information loss on their side, not a spelling preference.

    Pinned as a behaviour rather than a note: if this library ever starts agreeing with
    `llvm-undname` here, it has started throwing the same information away.
    """
    assert demangle.demangle("??_7A@B@@6BC@D@@E@F@@@") == "const B::A::`vftable'{for `D::C's `F::E'}"
    assert demangle.demangle("??_7A@B@@6BC@D@@E@F@@G@H@@@") == "const B::A::`vftable'{for `D::C's `F::E's `H::G'}"
    # The property that matters, independent of spelling: one symbol, one meaning. Adding
    # a base path element must change the answer, or the demangler is losing the element.
    family = ["??_7A@B@@6BC@D@@@", "??_7A@B@@6BC@D@@E@F@@@", "??_7A@B@@6BC@D@@E@F@@G@H@@@"]
    assert len({demangle.demangle(name) for name in family}) == len(family)


def _every_corpus():
    """Every conformance file, so a corpus added later is covered without an edit."""
    return sorted(path.name for path in CONFORMANCE.glob("*.txt"))


@pytest.mark.parametrize("style", demangle.styles())
@pytest.mark.parametrize("corpus", _every_corpus())
def test_the_tree_spells_what_the_fast_path_spells(corpus, style):
    """The two builders must never disagree, over every name recorded here.

    `tests/test_api.py` checks this on five hand-picked names, and five names cannot
    find a disagreement that needs a particular shape to appear. One did: a declarator
    applied to a pack with no members renders to nothing, and the tree builder measures
    a subtree rather than rendering it -- so it reported a width for a parameter that
    had dropped out, and the separator was left behind:

        f(std::launch, std::function<void ()>&&, )

    One name, in one corpus, out of 44,556. That is the size of corpus the property
    needs; running it costs a few seconds.

    Under every style, not just the default. The second one it found needed that: the
    tree builder flattened a conversion operator's type with whatever style was default
    rather than the one the tree was being built under, so under `gnu` a name came back
    as `operator std::vector<int, std::allocator<int>>` inside a spelling that wrote
    `> >` everywhere else.
    """
    for mangled, _ in load_corpus(corpus):
        try:
            tree = demangle.parse(mangled, style=style)
        except demangle.DemanglingError:
            continue
        assert tree.spell(style=style) == demangle.demangle(mangled, style=style), mangled


class TestAgainstLibcxxabisOwnCorpus:
    """LLVM's own Itanium vectors -- the reference measuring itself.

    `DemangleTestCases.inc` is what `libcxxabi`'s demangler is tested against, and that
    demangler is the code behind `llvm-cxxfilt`. 29,928 pairs, an order of magnitude more
    than anything this project had assembled, and the flagship scheme's real score
    against it.

    Checked in gzipped and pinned, so the number can only go up and cannot quietly stop
    being accurate. What still fails is grouped in the ROADMAP rather than left as one
    number.
    """

    #: Raised as gaps close; never lowered silently. A drop means a vector that used to
    #: pass has stopped, which is a regression whatever the total.
    EXPECTED_EXACT = 29923

    def _score(self):
        return sum(
            1 for mangled, expected in load_corpus("itanium-libcxxabi.txt") if demangle.demangle(mangled) == expected
        )

    def test_the_score_has_not_gone_backwards(self):
        total = len(load_corpus("itanium-libcxxabi.txt"))
        assert total > 29000, "corpus did not load; this test would prove nothing"
        assert self._score() >= self.EXPECTED_EXACT

    def test_the_pinned_number_is_still_accurate(self):
        assert self._score() == self.EXPECTED_EXACT

    def test_the_shortfall_is_five_names_and_this_says_which(self):
        """Five left, and four of them are not shortfalls at all.

        `i`, `PKFvRiE`, `PVFvRmOE` and `PFvRmOE` are bare `<type>` manglings with no
        `_Z`. They are refused *as symbols* on purpose, as `llvm-cxxfilt` refuses them,
        because a demangler offered every symbol in a binary and willing to read `i` as
        `int` will rename half a C library. `demangle_type()` reads all four; see
        `tests/test_types.py`.

        The fifth is a conversion operator whose type refers to the argument list that
        contains it -- `operator B<T_&><B<T_&>>`, a cycle. The reference guards against
        printing one by printing *nothing* for the second visit, so it answers
        `operator B<><>`; this reads the type a second time once the arguments are bound
        and answers `operator B<auto&><auto&>`. Neither is the declaration, because
        there is no declaration: the mangling is self-referential and no compiler emits
        one.
        """
        missed = [
            mangled
            for mangled, expected in load_corpus("itanium-libcxxabi.txt")
            if demangle.demangle(mangled) != expected
        ]
        assert sorted(missed) == sorted(["_Zcv1BIRT_EIS1_E", "i", "PKFvRiE", "PVFvRmOE", "PFvRmOE"])

    def test_the_four_bare_types_are_read_when_they_are_asked_for_as_types(self):
        """Refused as symbols, read as types. The corpus scores the symbol entry point."""
        for mangled, expected in load_corpus("itanium-libcxxabi.txt"):
            if demangle.demangle(mangled) == expected or not mangled.startswith(("i", "P")):
                continue
            assert demangle.demangle_type(mangled, language="itanium") == expected

    def test_nothing_in_it_is_answered_with_nothing(self):
        """Whatever it cannot read, it hands back -- never a blank."""
        for mangled, _ in load_corpus("itanium-libcxxabi.txt"):
            assert demangle.demangle(mangled) != ""

    @pytest.mark.parametrize("style", demangle.styles())
    def test_the_tree_agrees_with_the_text_throughout(self, style):
        """29,928 names is the size at which declarator placement disagreements show up."""
        for mangled, _ in load_corpus("itanium-libcxxabi.txt"):
            try:
                tree = demangle.parse(mangled, style=style)
            except demangle.DemanglingError:
                continue
            assert tree.spell(style=style) == demangle.demangle(mangled, style=style), mangled
