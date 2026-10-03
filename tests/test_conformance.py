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

from .conftest import CONFORMANCE, corpus_files, load_corpus

REPORTED = CONFORMANCE / "reported"

# Measured against llvm-cxxfilt 18.1.3 and GNU c++filt 2.42; CONFORMANCE.md describes
# each corpus below and what its reference is.
ITANIUM_LLVM_TOTAL, ITANIUM_LLVM_EXACT = 318, 318
ITANIUM_GNU_TOTAL, ITANIUM_GNU_EXACT = 311, 311
MSVC_TOTAL, MSVC_EXACT = 609, 609
MSVC_CLANG_TOTAL, MSVC_CLANG_EXACT = 161, 161

#: Expected column is the declaration in the source, not `llvm-undname`'s reading.
MSVC_REFERENCE_DEFECTS_TOTAL, MSVC_REFERENCE_DEFECTS_EXACT = 9, 9
MSVC_BOOST_TOTAL, MSVC_BOOST_EXACT = 5843, 5843
LIBSTDCXX_TOTAL, LIBSTDCXX_EXACT = 5913, 5913

#: Expected column is derived from the declaration. `tools/generate_corpus.py` excludes
#: these names, so a regeneration cannot re-record a reference's wrong answer.
REFERENCE_DEFECTS_TOTAL, REFERENCE_DEFECTS_EXACT = 37, 37

#: Replayed and asserted by tests/test_types.py.
TYPES_TOTAL, TYPES_LLVM_EXACT, TYPES_GNU_EXACT = 1076, 1076, 1076

#: Counts flag-induced *differences*, not names. Replayed by tests/test_msvc.py.
MSVC_SUPPRESSIONS_TOTAL, MSVC_SUPPRESSIONS_EXACT = 1253, 1250

#: Replayed by tests/test_msvc_options.py.
MSVC_DBGHELP_TOTAL, MSVC_DBGHELP_EXACT = 1064, 1064

#: `signature().qualified_name` against `UNDNAME_NAME_ONLY`; tests/test_msvc_options.py
#: says why there is no `MsvcOptions` field for that bit.
MSVC_NAME_ONLY_TOTAL = 478
MSVC_NAME_ONLY_AGREE = 423
MSVC_NAME_ONLY_WITHOUT_TAGS = 4
MSVC_NAME_ONLY_LOSES_THE_BASE_PATH = 10
MSVC_NAME_ONLY_REDUCES_A_NESTED_SYMBOL = 41

#: Replayed by tests/test_msvc_descriptors.py.
MSVC_DESCRIPTORS_TOTAL, MSVC_DESCRIPTORS_EXACT = 106, 106

#: Three of the 609 are absent: LLVM's mangler only tags functions. Replayed by
#: tests/test_msvc_arm64ec.py.
MSVC_ARM64EC_TOTAL, MSVC_ARM64EC_EXACT = 606, 606

#: Replayed by tests/test_swift_simplified.py.
SWIFT_SIMPLIFIED_TOTAL, SWIFT_SIMPLIFIED_EXACT = 217, 217
RUST_TOTAL, RUST_EXACT = 5316, 5316
RUST_TOOLCHAIN_TOTAL, RUST_TOOLCHAIN_EXACT = 394, 394

#: The deliberate differences from `c++filt -p` are enumerated in tests/test_signature.py.
NO_PARAMS_TOTAL, NO_PARAMS_AGREE = 6224, 6138

#: No reference demangler exists, so this count only records agreeing with ourselves;
#: the correctness argument is the round-trip property in tests/test_jni.py.
JNI_TOTAL, JNI_EXACT = 50, 50

#: No reference demangler exists; the correctness argument is the round-trip property in
#: tests/test_go.py (re-escaping must reproduce Go's own PathToPrefix bytes).
GO_TOTAL, GO_EXACT = 1517, 1517
D_TOTAL, D_EXACT = 1257, 1257

#: No reference demangler exists; the correctness argument is the re-assembly property in
#: tests/test_pascal.py over the whole runtime, plus the check against `ppudump`.
PASCAL_TOTAL, PASCAL_EXACT = 3899, 3899

#: Scored against the compiler's own `.ndi` record, not a demangler; the re-mangling
#: property lives in tests/test_nim.py.
NIM_TOTAL, NIM_EXACT = 2115, 2115
SWIFT_TOTAL, SWIFT_EXACT = 8494, 8494

#: Not scored here: 71 rows are reference refusals, which `_misses` counts as misses.
#: tests/test_swift.py scores it; it lives here for tests/test_readme.py.
SWIFT_UPSTREAM_TOTAL, SWIFT_UPSTREAM_EXACT = 514, 514
SWIFT_REFERENCE_DEFECTS_TOTAL, SWIFT_REFERENCE_DEFECTS_EXACT = 5, 5
OBJC_TOTAL, OBJC_EXACT = 2665, 2665

#: The sample and the whole TDUMP table are both replayed: the sample is what a reader
#: opens, the table is the measurement.
DELPHI_TOTAL, DELPHI_EXACT = 68, 68
DELPHI_TABLE_TOTAL, DELPHI_TABLE_EXACT = 11363, 11363
#: Hand-built and kept apart from the TDUMP rows, since its evidence is weaker.
DELPHI_CONSTRUCT_TOTAL, DELPHI_CONSTRUCT_EXACT = 53, 53

#: The style is a column in this corpus, so tests/test_gnuv2.py replays it, not this file.
GNUV2_TOTAL, GNUV2_EXACT = 1324, 1324
GNUV2_REAL_WORLD_TOTAL, GNUV2_REAL_WORLD_EXACT = 12661, 12661

#: The options are a column, so tests/test_codewarrior.py replays it, not this file.
CODEWARRIOR_TOTAL, CODEWARRIOR_EXACT = 47, 47

#: Read by language, not detection: detection deliberately declines names with no
#: GNAT-specific encoding. Replayed by tests/test_ada.py.
ADA_TOTAL, ADA_EXACT = 34, 34

#: The 4 unclaimed vectors are indistinguishable from ordinary C names; see
#: tests/test_ada.py.
ADA_AUTODETECTED = 29
ADA_REAL_WORLD_TOTAL, ADA_REAL_WORLD_EXACT = 1438, 1438

#: `llvm-undname` keeps only the first element of a vftable's base path, spelling
#: distinct vtables identically. We keep the whole path.
UNDNAME_DIVERGENCES = [
    "??_7A@B@@6BC@D@@E@F@@@",
    "??_7A@B@@6BC@D@@E@F@@G@H@@@",
    "??_7A@@6BB@@C@@@",
    "??_7A@@6BB@@C@@D@@@",
]

#: Where `llvm-undname` and `dbghelp.dll` disagree about how far a flag reaches, one
#: example each. We follow `llvm-undname`; tests/test_msvc_options.py asserts it.
UNDNAME_REACH_DIVERGENCES = {
    "?j@FTypeWithQuals@@3U?$S@$$A6AHXZ@1@A": "llvm reaches into a template argument, dbghelp does not",
    "?NS@?1??SN@?$NS@H@0@QEAAHXZ@4HA": "dbghelp reaches into the enclosing symbol, llvm does not",
    "?overloaded_fn@@$$J0YAXXZ": 'llvm drops `extern "C" ` with the member type, dbghelp keeps it',
}

#: Mask bits that change nothing over `msvc-llvm-corpus.txt`, so no field was added.
#: Recorded because "the reference does nothing with it" differs from "not implemented".
UNDNAME_INERT_BITS = {
    "UNDNAME_NO_ALLOCATION_MODEL": 0x00008,
    "UNDNAME_NO_MS_THISTYPE": 0x00020,
    "UNDNAME_NO_CV_THISTYPE": 0x00040,
    "UNDNAME_NO_THROW_SIGNATURES": 0x00100,
    "UNDNAME_NO_RETURN_UDT_MODEL": 0x00400,
    "UNDNAME_32_BIT_DECODE": 0x00800,
    "UNDNAME_NO_SPECIAL_SYMS": 0x04000,
    "UNDNAME_NO_IDENT_CHAR_CHECK": 0x10000,
    "UNDNAME_NO_PTR64": 0x20000,
}

#: Names on which GNU c++filt and llvm-cxxfilt disagree about the substitution table;
#: pinned by name so a count cannot hide a traded defect.
GNU_DIVERGENCES = []


def _misses(corpus, style, language=None):
    """The corpus's size, and `(name, want, got)` for every row that does not read as
    recorded."""
    pairs = load_corpus(corpus)
    missed = []
    for mangled, expected in pairs:
        try:
            got = demangle.demangle_strict(mangled, style=style, language=language)
        except (DemanglingError, RecursionError) as error:
            got = f"<{type(error).__name__}>"
        if got != expected:
            missed.append((mangled, expected, got))
    return len(pairs), missed


def _describe(corpus, style, language, missed, shown=10):
    """The first few misses and the command that lists them all."""
    lines = [f"{len(missed)} row(s) of {corpus} do not read as recorded:"]
    for mangled, expected, got in missed[:shown]:
        lines += [f"  name {mangled}", f"  want {expected}", f"  got  {got}"]
    if len(missed) > shown:
        lines.append(f"  ... and {len(missed) - shown} more")
    stored = corpus if (CONFORMANCE / corpus).exists() else f"{corpus}.gz"
    command = f"python tools/differential.py --corpus tests/conformance/{stored} --style {style}"
    lines.append(f"reproduce: {command}{f' --language {language}' if language else ''}")
    return "\n".join(lines)


def _assert_pinned(corpus, style, language, pinned_total, pinned_exact):
    total, missed = _misses(corpus, style, language)
    exact = total - len(missed)
    assert (total, exact) == (pinned_total, pinned_exact), (
        f"pinned {pinned_exact} / {pinned_total}, measured {exact} / {total}; a change that "
        f"moves the count updates the pin with it.\n{_describe(corpus, style, language, missed)}"
    )


def test_itanium_matches_llvm_cxxfilt():
    _assert_pinned("itanium-real-world.txt", "llvm", None, ITANIUM_LLVM_TOTAL, ITANIUM_LLVM_EXACT)


def test_itanium_matches_gnu_cxxfilt():
    _assert_pinned("itanium-real-world-gnu.txt", "gnu", None, ITANIUM_GNU_TOTAL, ITANIUM_GNU_EXACT)


def test_gnuv2_matches_libiberty_on_kde2():
    """See `GNUV2_REAL_WORLD_TOTAL` for what this does and does not establish."""
    _assert_pinned("gnuv2-real-world.txt", "llvm", "gnuv2", GNUV2_REAL_WORLD_TOTAL, GNUV2_REAL_WORLD_EXACT)


def test_jni_corpus():
    """See `JNI_TOTAL` for what this does and does not establish."""
    _assert_pinned("jni-real-world.txt", "llvm", "jni", JNI_TOTAL, JNI_EXACT)


def test_go_corpus():
    """See `GO_TOTAL` for what this does and does not establish."""
    _assert_pinned("go-real-world.txt", "llvm", "go", GO_TOTAL, GO_EXACT)


def test_d_matches_gnu_dlang_demangler():
    _assert_pinned("d-real-world.txt", "llvm", "d", D_TOTAL, D_EXACT)


def test_swift_matches_swift_demangle():
    _assert_pinned("swift-real-world.txt", "llvm", "swift", SWIFT_TOTAL, SWIFT_EXACT)


def test_swift_reference_defect_corpus():
    """Names where following `swift-demangle` would mean printing `<null node pointer>`.

    See `SWIFT_REFERENCE_DEFECTS_TOTAL` and the file's own header.
    """
    _assert_pinned(
        "swift-reference-defects.txt", "llvm", "swift", SWIFT_REFERENCE_DEFECTS_TOTAL, SWIFT_REFERENCE_DEFECTS_EXACT
    )


def test_pascal_corpus():
    """See `PASCAL_TOTAL` for what this does and does not establish."""
    _assert_pinned("pascal-real-world.txt", "llvm", "pascal", PASCAL_TOTAL, PASCAL_EXACT)


def test_nim_matches_the_compilers_own_record():
    """See `NIM_TOTAL` for what this does and does not establish."""
    _assert_pinned("nim-real-world.txt", "llvm", "nim", NIM_TOTAL, NIM_EXACT)


def test_msvc_matches_llvm_undname():
    _assert_pinned("msvc-llvm-corpus.txt", "llvm", "msvc", MSVC_TOTAL, MSVC_EXACT)


def test_msvc_matches_llvm_undname_on_real_compiler_output():
    """See `MSVC_CLANG_TOTAL`. Names a compiler wrote, not vectors somebody chose."""
    _assert_pinned("msvc-clang.txt", "llvm", "msvc", MSVC_CLANG_TOTAL, MSVC_CLANG_EXACT)


def test_msvc_matches_llvm_undname_on_boost():
    """See `MSVC_BOOST_TOTAL`."""
    _assert_pinned("msvc-boost.txt", "llvm", "msvc", MSVC_BOOST_TOTAL, MSVC_BOOST_EXACT)


def test_msvc_reads_what_its_reference_cannot():
    """See `MSVC_REFERENCE_DEFECTS_TOTAL`. The expected column is the declaration."""
    _assert_pinned(
        "msvc-reference-defects.txt", "llvm", "msvc", MSVC_REFERENCE_DEFECTS_TOTAL, MSVC_REFERENCE_DEFECTS_EXACT
    )


def test_matches_llvm_cxxfilt_on_the_system_libstdcxx():
    """Every mangled symbol the shipped libstdc++ exports.

    Real released C++ rather than something compiled for the test, which is the point:
    it carries what only a real standard library produces.
    """
    _assert_pinned("itanium-libstdcxx.txt", "llvm", None, LIBSTDCXX_TOTAL, LIBSTDCXX_EXACT)


def test_reference_defect_corpus():
    """Names where following a reference would mean printing a type the source disproves.

    The Itanium corpus whose expected column is not a reference demangler's output. See
    `REFERENCE_DEFECTS_TOTAL` and the file's own header.
    """
    _assert_pinned("itanium-reference-defects.txt", "llvm", None, REFERENCE_DEFECTS_TOTAL, REFERENCE_DEFECTS_EXACT)


def test_rust_matches_rustc_demangle():
    """Auto-detected, not forced.

    Forcing `language="rust"` would skip detection, which is the part most likely to be
    wrong: legacy Rust mangling *is* Itanium mangling, so a detection miss hands the
    name to the C++ parser and yields a plausible but quite wrong spelling rather than
    an error.
    """
    _assert_pinned("rust-real-world.txt", "llvm", None, RUST_TOTAL, RUST_EXACT)


def test_rust_matches_the_shipped_toolchain():
    """Symbols from rustc's own libraries, including ELF-versioned ones."""
    _assert_pinned("rust-toolchain.txt", "llvm", None, RUST_TOOLCHAIN_TOTAL, RUST_TOOLCHAIN_EXACT)


def test_objc_matches_what_the_declaration_said():
    """Auto-detected, not forced.

    The forms that matter here are the ones shaped like ordinary C identifiers -- the
    `_i_`/`_c_` method mangling above all -- so what has to hold is that detection
    reaches them without claiming anything else.
    """
    _assert_pinned("objc-real-world.txt", "llvm", None, OBJC_TOTAL, OBJC_EXACT)


def test_delphi_matches_embarcadero_unmangle():
    """See `DELPHI_TOTAL` for what this does and does not establish."""
    _assert_pinned("delphi-real-world.txt", "llvm", None, DELPHI_TOTAL, DELPHI_EXACT)


def test_delphi_whole_export_tables_match_the_dump():
    """The sample is not the measurement. The measurement is the whole TDUMP dump."""
    _assert_pinned("delphi-tdump.txt", "llvm", None, DELPHI_TABLE_TOTAL, DELPHI_TABLE_EXACT)


def test_delphi_constructs_absent_from_the_export_tables():
    """See `DELPHI_CONSTRUCT_TOTAL` for what this does and does not establish."""
    _assert_pinned("delphi-constructs.txt", "llvm", None, DELPHI_CONSTRUCT_TOTAL, DELPHI_CONSTRUCT_EXACT)


def test_ada_matches_gnu_gnat_demangler():
    """See `ADA_REAL_WORLD_TOTAL`."""
    _assert_pinned("ada-real-world.txt", "llvm", None, ADA_REAL_WORLD_TOTAL, ADA_REAL_WORLD_EXACT)


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


def _reported():
    return sorted(f"{REPORTED.name}/{path.name}" for path in REPORTED.glob("*.txt"))


@pytest.mark.parametrize("corpus", _reported())
def test_every_reported_name_reads_as_recorded(corpus):
    """Names somebody reported. No count: every row must hold.

    `<scheme>.txt` is read under that language in the llvm style, `<scheme>-<style>.txt`
    in the style it names, so detection is not what is under test.
    """
    language, _, style = corpus.removeprefix(f"{REPORTED.name}/").removesuffix(".txt").partition("-")
    style = style or "llvm"
    assert language in demangle.languages(), f"{corpus}: no scheme is called {language!r}"
    assert style in demangle.styles(), f"{corpus}: no style is called {style!r}"
    names = [mangled for mangled, _ in load_corpus(corpus)]
    assert names, f"{corpus} has no rows"
    assert len(set(names)) == len(names), f"{corpus} lists a name twice"
    _, missed = _misses(corpus, style, language)
    assert missed == [], _describe(corpus, style, language, missed)


def _every_corpus():
    """Every conformance file, so a corpus added later is covered without an edit."""
    return corpus_files()


@pytest.mark.sweep
@pytest.mark.parametrize("corpus", _every_corpus())
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
    # Independent of spelling: each added base path element must change the answer.
    family = ["??_7A@B@@6BC@D@@@", "??_7A@B@@6BC@D@@E@F@@@", "??_7A@B@@6BC@D@@E@F@@G@H@@@"]
    assert len({demangle.demangle(name) for name in family}) == len(family)


@pytest.mark.sweep
@pytest.mark.parametrize("style", demangle.styles())
@pytest.mark.parametrize("corpus", _every_corpus())
def test_the_tree_spells_what_the_fast_path_spells(corpus, style):
    """The two builders must never disagree, over every name recorded here.

    `tests/test_api.py` checks this on five hand-picked names, and five names cannot
    find a disagreement that needs a particular shape to appear. One such shape is a
    declarator applied to a pack with no members, which renders to nothing: the tree
    builder measures a subtree rather than rendering it, so it would report a width for
    a parameter that had dropped out and leave the separator behind:

        f(std::launch, std::function<void ()>&&, )

    One name in the checked-in corpora has it, which is why the property runs over all
    of them.

    Under every style, not just the default: the tree builder must flatten a
    conversion operator's type with the style the tree is being built under, not the
    default, or under `gnu` a name would read `operator std::vector<int,
    std::allocator<int>>` inside a spelling that writes `> >` everywhere else.
    """
    for mangled, _ in load_corpus(corpus):
        try:
            tree = demangle.parse(mangled, style=style)
        except demangle.DemanglingError:
            continue
        assert tree.spell(style=style) == demangle.demangle(mangled, style=style), mangled


@pytest.mark.sweep
@pytest.mark.parametrize("corpus", _every_corpus())
def test_a_style_does_not_decide_whether_a_name_parses(corpus):
    """A style is a spelling policy. It must not change what the grammar accepts.

    The pressure point is the requires-clause. GNU c++filt substitutes the argument
    bound to a `<template-param>` inside one where llvm-cxxfilt spells the parameter
    symbolically, so the GNU style resolves it -- and a clause names parameters of
    enclosing templates that are not all in scope. Failing to resolve must not refuse
    the whole name, or `--style llvm` would read it and `--style gnu` hand the symbol
    back mangled; what cannot be substituted falls back to the spelling the other style
    uses. Twelve names exercise this: eleven of libcxxabi's own C++20 vectors and
    `std::pair`'s constrained constructor, which is what GCC 13 emits for the real
    `std::pair`.

    Checked over every corpus rather than over the C++ ones, because the invariant
    belongs to the library and not to one scheme.
    """
    differ = []
    for mangled, _ in load_corpus(corpus):
        read = {}
        for style in demangle.styles():
            try:
                demangle.demangle_strict(mangled, style=style)
                read[style] = True
            except demangle.DemanglingError:
                read[style] = False
        if len(set(read.values())) > 1:
            differ.append((mangled, read))
    assert differ == []


#: Not zero because a demangled name can contain a genuinely symbol-shaped word: all
#: seven are pre-Itanium names libiberty spells with a mangled component still in them.
FILTER_REWRITES_AGAIN = 7


@pytest.mark.sweep
@pytest.mark.parametrize("corpus", _every_corpus())
def test_the_filter_does_not_rewrite_what_this_library_printed(corpus):
    """`demangle_text` over a spelling this library produced should leave it alone.

    It is the second pass a user gets by accident -- a log file that already went through
    the filter, a demangled name pasted into a report -- and it must not corrupt the
    output: `@escaping`, `@autoclosure` and `@Swift.MainActor` keep their `@` rather than
    read as Delphi's unit-scope routine, and so do `@GLIBCXX_3.4` and
    `@@CXXABI_FLOAT128`, which this library prints on every versioned symbol.
    `FILTER_REWRITES_AGAIN` holds the names that remain.
    """
    rewritten = []
    for mangled, _ in load_corpus(corpus):
        try:
            spelled = demangle.demangle_strict(mangled)
        except demangle.DemanglingError:
            continue
        if demangle.demangle_text(spelled) != spelled:
            rewritten.append(mangled)
    if corpus == "gnuv2-libiberty.txt":
        assert len(rewritten) == FILTER_REWRITES_AGAIN, rewritten
    else:
        assert rewritten == []


#: Names the filter's token alphabet cannot hold whole: angle brackets are excluded on
#: purpose (objdump writes `call 1050 <_ZN3foo3barEv>`), and Go's stop at a `,` or space.
#: Per-flag corpora repeat a name, hence three rows in `msvc-dbghelp.txt`.
FILTER_REPORTS_PIECES = {
    "codewarrior-cwdemangle.txt": 2,
    "go-real-world.txt": 6,
    "msvc-dbghelp.txt": 3,
    "msvc-llvm-corpus.txt": 2,
    "msvc-name-only.txt": 1,
    "msvc-suppressions.txt": 4,
}


@pytest.mark.sweep
@pytest.mark.parametrize("corpus", _every_corpus())
def test_the_filter_reports_no_piece_of_a_name_it_reads_whole(corpus):
    """A fragment that happens to demangle is a reading of something that is not there.

    `@?0??define_lambda@@YAHXZ@QBE@XZ` -- what the token has left of an MSVC symbol after
    the angle bracket it cannot hold -- must not read as
    `?0??define_lambda::__linkproc__ YAHXZ::QBE::XZ`, a Delphi declaration built out of
    half somebody else's name. This is the guard against it.
    """
    pieces = []
    for mangled, _ in load_corpus(corpus):
        try:
            spelled = demangle.demangle_strict(mangled)
        except demangle.DemanglingError:
            continue
        if spelled == mangled:
            continue
        found = list(demangle.find_symbols(mangled))
        if found and (len(found) > 1 or found[0].mangled != mangled):
            pieces.append(mangled)
    assert len(pieces) == FILTER_REPORTS_PIECES.get(corpus, 0), pieces


class TestAgainstLibcxxabisOwnCorpus:
    """LLVM's own Itanium vectors -- the reference measuring itself.

    `DemangleTestCases.inc` is what `libcxxabi`'s demangler is tested against, and that
    demangler is the code behind `llvm-cxxfilt`. 29,928 pairs, and the flagship scheme's
    real score against it.

    Checked in gzipped and pinned, so the number cannot quietly stop being accurate.
    What fails is named below rather than left as one number.
    """

    #: Never lowered silently; the named shortfall below says why it is not 29,928.
    EXPECTED_EXACT = 29913

    def _missed(self):
        return [
            (mangled, expected, got)
            for mangled, expected in load_corpus("itanium-libcxxabi.txt")
            if (got := demangle.demangle(mangled)) != expected
        ]

    def test_the_score_has_not_gone_backwards(self):
        total = len(load_corpus("itanium-libcxxabi.txt"))
        assert total > 29000, "corpus did not load; this test would prove nothing"
        missed = self._missed()
        assert total - len(missed) >= self.EXPECTED_EXACT, _describe("itanium-libcxxabi.txt", "llvm", None, missed)

    def test_the_pinned_number_is_still_accurate(self):
        total = len(load_corpus("itanium-libcxxabi.txt"))
        missed = self._missed()
        assert total - len(missed) == self.EXPECTED_EXACT, _describe("itanium-libcxxabi.txt", "llvm", None, missed)

    def test_the_shortfall_is_fifteen_names_and_this_says_which(self):
        """Fifteen fail, in four groups, and none of them is a name read wrongly.

        **Four bare types.** `i`, `PKFvRiE`, `PVFvRmOE` and `PFvRmOE` are `<type>`
        manglings with no `_Z`. They are refused *as symbols* on purpose, as
        `llvm-cxxfilt` refuses them, because a demangler offered every symbol in a binary
        and willing to read `i` as `int` will rename half a C library. `demangle_type()`
        reads all four; see `tests/test_types.py`.

        **One self-referential conversion operator.** `_Zcv1BIRT_EIS1_E` is
        `operator B<T_&><B<T_&>>`, a cycle. `llvm-cxxfilt` guards against printing one by
        printing *nothing* for the second visit, so it answers `operator B<><>`. Reading
        the type a second time once the arguments are bound would answer
        `operator B<auto&><auto&>`; neither is the declaration, because there is no
        declaration -- the mangling is self-referential and no compiler emits one.

        It is refused, which is what `c++filt` 2.42 does with it. The rule that
        refuses it is the general one: a `<template-param>` resolves to `auto` only in
        the two readings where nothing is bound on purpose -- a generic lambda's invented
        parameters, and a conversion operator's type read ahead of its arguments -- and
        the `T_` here is in neither, because the arguments it would bind against are the
        ones this very type is inside. Of the three answers, declining to read a name
        that has no declaration is the one that claims least.

        **Nine where this corpus records llvm-cxxfilt's own defect.** A
        `<template-param>` recorded as a substitution candidate -- or a component built
        over one -- is the parameter, not the argument bound to it where the entry was
        made, and a generic lambda's `operator()<int>` therefore takes `int` and not
        `auto`. Established against g++ 13.3.0 and clang++ 18.1.3 over sources in
        `tools/corpus_sources/reference_defects/`, and pinned in
        `tests/conformance/itanium-reference-defects.txt`. GNU c++filt 2.42 agrees with
        the corrected reading on the six of the nine it can read, except where its own
        reference-collapsing rewrite freezes the same entry -- which is
        `_Z1h1XIJZ1fIiEDaOT_E1AZ1gIdEDaS2_E1BEE`, the same shape as
        `std::once_flag::_Prepare_execution` in the shipped libstdc++, where that
        library's own header settles it against both references.

        **One numbered by GCC 12.** `_ZNK1xMUlTyT_E_clIiEEDaS_` is a lambda in the
        initializer of a variable `x`, and the vector expects `operator()<int>(x)`: the
        variable's name as the parameter type. GCC 12 and earlier left the closure
        prefix `x` out of the substitution table, so its `S_` is the lambda's own `$T`,
        bound to `int`; see `ItaniumOptions.closure_prefix_substitution` and
        `tests/test_itanium_closure_prefix.py`. No type is a variable's name, and this
        answers `operator()<int>(int)`.

        This corpus is llvm-cxxfilt's test file, so it cannot be corrected in place: its
        value is that it is the reference measuring itself. The fifteen are named here
        instead, so that one of them starting to pass is as visible as one of them
        starting to fail.
        """
        missed = [
            mangled
            for mangled, expected in load_corpus("itanium-libcxxabi.txt")
            if demangle.demangle(mangled) != expected
        ]
        assert sorted(missed) == sorted(
            [
                # bare types, refused as symbols
                "i",
                "PFvRmOE",
                "PKFvRiE",
                "PVFvRmOE",
                # the reference's model of a recorded <template-param>
                "_Z1h1XIJZ1fIiEDaOT_E1AZ1gIdEDaS2_E1BEE",
                "_ZN1XIZ1fIiEvOT_EUlS2_DpT0_E_EclIJEEEvDpT_",
                "_ZZ11inline_funcvENKUlTyTyT_T0_E_clIiiEEDaS_S0_",
                "_ZZ11inline_funcvENKUlTyTyT_T1_T0_E_clIiiiEEDaS_S0_S1_",
                "_ZZ18test_assign_throwsI20small_throws_on_copyLb0EEvvENKUlRNSt3__13anyEOT_E_clIRS0_EEDaS3_S5_",
                "_ZZN5test71fIiEEvvENKUlTyQaa1CIT_E1CITL0__ET0_E0_clIiiEEDaS3_Qaa1CIDtfp_EELb1E",
                "_ZZN5test71fIiEEvvENKUlTyQaa1CIT_E1CITL0__ET0_E1_clIiiEEDaS3_Q1CIDtfp_EE",
                "_ZZN5test71fIiEEvvENKUlTyQaa1CIT_E1CITL0__ET0_E_clIiiEEDaS3_Q1CIDtfp_EE",
                "_ZZN5test71fIiEEvvENKUlTyT0_E_clIiiEEDaS1_",
                # GCC 12's closure-prefix numbering, read by the ABI's rule
                "_ZNK1xMUlTyT_E_clIiEEDaS_",
                # a self-referential conversion operator
                "_Zcv1BIRT_EIS1_E",
            ]
        )

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

    @pytest.mark.sweep
    @pytest.mark.parametrize("style", demangle.styles())
    def test_the_tree_agrees_with_the_text_throughout(self, style):
        """29,928 names is the size at which declarator placement disagreements show up."""
        for mangled, _ in load_corpus("itanium-libcxxabi.txt"):
            try:
                tree = demangle.parse(mangled, style=style)
            except demangle.DemanglingError:
                continue
            assert tree.spell(style=style) == demangle.demangle(mangled, style=style), mangled
