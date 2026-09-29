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
ITANIUM_LLVM_TOTAL, ITANIUM_LLVM_EXACT = 318, 318
ITANIUM_GNU_TOTAL, ITANIUM_GNU_EXACT = 311, 311
MSVC_TOTAL, MSVC_EXACT = 609, 609

#: Real compiler output for the MS ABI: `tools/corpus_sources/msvc/msvc.cpp` through
#: `clang++ --target=x86_64-pc-windows-msvc` at four standards and two optimisation
#: levels, scored against `llvm-undname`. `msvc-llvm-corpus.txt` is LLVM's own *test*
#: file -- hand-written vectors -- so this is the first MSVC corpus here that a compiler
#: wrote. Six defects came out of its first two runs: a member function's qualifiers
#: written past what its return type wraps, a dynamic initialiser for a qualified
#: variable refused outright, `operator<=>` and `operator co_await` missing from the
#: table, the `_E` that ends a `noexcept` signature where a `Z` ends every other one, and
#: a deduced return type written as a back reference to an earlier one.
MSVC_CLANG_TOTAL, MSVC_CLANG_EXACT = 161, 161

#: The names in that run `llvm-undname` reads wrongly: `_L` and `_M`, which are
#: `__int128` and `unsigned __int128` and which `demanglePrimitiveType` has no case for,
#: and a qualifier in front of a deduced return type, which `CustomTypeNode::outputPre`
#: drops. Their expected column is the declaration in the source, as for the Itanium file
#: above. Then three from Boost 1.84's MSVC build: `?A_P` and `?A_T`, `auto` and
#: `decltype(auto)` as a return type, which the release refuses and LLVM's main branch
#: reads; the expected column is the STL declaration, and main's spelling agrees. And two
#: where the pointee of a pointer to member carries `__restrict` or `__unaligned`, which
#: the reference prints for the same pointer outside a member pointer and drops inside
#: one -- so `int __unaligned *__restrict ns::Ext::*` and `int *ns::Ext::*`, two
#: declarations, come back from it as the second.
MSVC_REFERENCE_DEFECTS_TOTAL, MSVC_REFERENCE_DEFECTS_EXACT = 9, 9

#: Boost 1.84's twenty-nine `boost_*-vc143` NuGet packages, x64 and x86: every twentieth
#: of the 122,162 names `llvm-undname` 18 reads, every vcall thunk, and ten catch-block
#: variables inside MD5-hashed functions. Two shapes the LLVM release's x64 build never
#: wrote.
MSVC_BOOST_TOTAL, MSVC_BOOST_EXACT = 5843, 5843
LIBSTDCXX_TOTAL, LIBSTDCXX_EXACT = 5913, 5913
REGRESSIONS_TOTAL, REGRESSIONS_EXACT = 30, 30

#: Names on which a reference demangler is *wrong*, so the expected column is derived
#: from the declaration rather than recorded from a demangler. See the file's header, and
#: `tools/corpus_sources/reference_defects/` for the sources. Four of these came out of
#: corpora recorded from llvm-cxxfilt, whose answer turned out to be the defect rather
#: than the reference; `tools/generate_corpus.py` reads this file and excludes them, so a
#: regeneration cannot quietly record the wrong answer again.
#:
#: The tenth is here because that protection was not covering it. `templateTemplate` sat
#: in `itanium-real-world.txt` with the corrected spelling and was absent from this file,
#: so the next regeneration re-recorded llvm-cxxfilt 18's answer -- which is what the
#: first regeneration in a while did.
#:
#: The fifteenth and sixteenth are printing defects rather than substitution ones:
#: llvm-cxxfilt 18 and 20 print `new T()` as `new T`, and `(sizeof(T) + 1) / 2` as
#: `sizeof (int) + 1 / 2`, and each pair is two different expressions. The seventeenth
#: is the closure defect again, in libstdc++ 13's `<format>`: a name every program that
#: calls `std::format` carries. The eighteenth is the same header compiled by Clang,
#: where the entry resolves to `double` and the parameter comes out
#: `basic_string<double>&`, a type libstdc++ does not instantiate.
#:
#: The last two are a different defect, and about the *count* of entries rather than what
#: one of them holds: `<template-template-param> <template-args>` is two components and
#: 5.1.10 makes each a candidate, so `T_ I ... E` contributes two entries. llvm-cxxfilt
#: 18 records only the second, so it refuses `_Z1fI1AiEvT_IT0_ES3_S3_` and answers
#: `char<int>` for `_Z1gI1AcEvT_IT0_ES1_IiE`; GNU c++filt 2.42 agrees with the source.
#:
#: The six after those are a third defect, and the one whose two sides each refuse the
#: other's output: an inheriting constructor's `<base class type>` is a <type> and so a
#: candidate, g++ 13.3 enters it and clang++ 18.1.3 does not, so `_ZN1DCI21CENS0_4KindE`
#: and `_ZN1DCI21CEN1C4KindE` are the same declaration written under two numberings.
#: llvm-cxxfilt 18 reads clang's and refuses g++'s; c++filt 2.42 reads both parameter
#: lists and names the constructor `D::C`. See
#: `ItaniumOptions.inherited_constructor_substitution`.
REFERENCE_DEFECTS_TOTAL, REFERENCE_DEFECTS_EXACT = 37, 37

#: Bare `<type>` encodings -- `Pi`, `PKFvRiE` -- read by `demangle_type()` rather than by
#: `demangle()`, which refuses every one of them on purpose. The same 1,076 encodings are
#: scored against *both* references, one corpus each, because the two spell the same
#: types differently. Replayed and asserted by tests/test_types.py.
TYPES_TOTAL, TYPES_LLVM_EXACT, TYPES_GNU_EXACT = 1076, 1076, 1076

#: What each of `llvm-undname`'s five suppression flags changes, over the 609 names of
#: `msvc-llvm-corpus.txt`. Only the names a flag actually changes are recorded, so this
#: counts *differences* the reference makes and not names. Replayed by tests/test_msvc.py.
MSVC_SUPPRESSIONS_TOTAL, MSVC_SUPPRESSIONS_EXACT = 1253, 1250

#: The same, for the four `UnDecorateSymbolName` mask bits `llvm-undname` has no flag for,
#: against `dbghelp.dll` 10.0.26100.8328. Recorded over the 478 of those 609 names the two
#: references already spell alike, because `dbghelp` prints `__ptr64` and spaces a
#: declaration differently and a corpus cannot ask for both houses at once; the rest are
#: left out rather than guessed at. Replayed by tests/test_msvc_options.py.
MSVC_DBGHELP_TOTAL, MSVC_DBGHELP_EXACT = 1064, 1064

#: How `signature().qualified_name` compares with `UNDNAME_NAME_ONLY` over those same 478
#: names, in four groups: agreeing outright, agreeing once the elaborated type specifiers
#: go, differing because the reference discards a vftable's `const` and its base path, and
#: differing because it reduces a symbol *nested* in the name that this spells in full.
#: There is no `MsvcOptions` field for that bit; tests/test_msvc_options.py says why.
MSVC_NAME_ONLY_TOTAL = 478
MSVC_NAME_ONLY_AGREE = 423
MSVC_NAME_ONLY_WITHOUT_TAGS = 4
MSVC_NAME_ONLY_LOSES_THE_BASE_PATH = 10
MSVC_NAME_ONLY_REDUCES_A_NESTED_SYMBOL = 41

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
NO_PARAMS_TOTAL, NO_PARAMS_AGREE = 6224, 6138

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
GO_TOTAL, GO_EXACT = 1517, 1517

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

#: Swift's own `test/Demangle/Inputs/manglings.txt`. Not scored here -- 70 of its rows
#: are names the reference refuses, recorded as `mangled\tmangled`, and `_score` reads a
#: refusal as a miss. tests/test_swift.py scores it, in both directions, against this
#: number; it lives here because that is where tests/test_readme.py looks for the
#: README's counts.
SWIFT_UPSTREAM_TOTAL, SWIFT_UPSTREAM_EXACT = 514, 514

#: Swift names the reference reads wrongly. One defect, in `NodePrinter`'s extended
#: existential shape case, which reads the node one child too high and spells the type as
#: `<null node pointer>`. The expected column is what the demangling tree says instead;
#: see the file's own header.
SWIFT_REFERENCE_DEFECTS_TOTAL, SWIFT_REFERENCE_DEFECTS_EXACT = 5, 5

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

#: Pre-Itanium C++ from shipped binaries, all gcc 2.95's output for Debian woody: every
#: fourth defined dynamic symbol of kdelibs3 2.2.2 and libstdc++ 2.10, every eighth of
#: kdebase 2.2.2, omniORB 3.0.4, kchart 1.1.1, gtkmm 1.2.10, libsigc++ 1.0.4 and
#: libxml++ 1.0.4, plus every thunk, against libiberty's `cplus_demangle` under the
#: `gnu` style. The 56,347 names those libraries define agree with the reference on all
#: but 396, every one a thunk with a positive delta, `__thunk_n8_...`, which gcc 2.95
#: wrote with an `n` and libiberty reads as a method named `n8_setInstance`;
#: tests/test_gnuv2.py pins the form against the compiler's own `make_thunk`, and the
#: corpus leaves those out.
GNUV2_REAL_WORLD_TOTAL, GNUV2_REAL_WORLD_EXACT = 12661, 12661

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

#: Real compiler output for Ada/GNAT: symbols from the shipped libgnat and libgnarl,
#: scored against `c++filt --format=gnat`. Sampled by tools/generate_ada_corpus.py.
ADA_REAL_WORLD_TOTAL, ADA_REAL_WORLD_EXACT = 1438, 1438

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

#: Where the two MSVC references disagree about a *flag* both of them have.
#:
#: `tools/generate_msvc_dbghelp_corpus.py --report` asks `dbghelp.dll` for the mask bit
#: that means what each `llvm-undname` flag means and compares it with what
#: `msvc-suppressions.txt` recorded, over the 473 names the two spell alike unflagged.
#: Neither is wrong; they answer a question the flags do not settle, which is *how far a
#: flag reaches*. Measured against dbghelp.dll 10.0.26100.8328, and named here because a
#: number alone would let one of these be traded for a new defect.
#:
#: One example each. This library follows `llvm-undname` throughout, which is what
#: `msvc-suppressions.txt` scores it against; `tests/test_msvc_options.py` asserts the
#: behaviour these names show.
UNDNAME_REACH_DIVERGENCES = {
    # `--no-calling-convention` and `--no-return-type` reach *inward*, into a function
    # type written as a template argument; `UNDNAME_NO_ALLOCATION_LANGUAGE` and
    # `UNDNAME_NO_FUNCTION_RETURNS` stop at the declaration. 41 names each.
    "?j@FTypeWithQuals@@3U?$S@$$A6AHXZ@1@A": "llvm reaches into a template argument, dbghelp does not",
    # `UNDNAME_NO_ACCESS_SPECIFIERS` reaches *outward*, into the symbol a local name is
    # scoped by; `--no-access-specifier` leaves the scope's spelling alone. 9 names.
    "?NS@?1??SN@?$NS@H@0@QEAAHXZ@4HA": "dbghelp reaches into the enclosing symbol, llvm does not",
    # `--no-member-type` groups `extern "C" ` with `static` and `virtual` and drops all
    # three; `UNDNAME_NO_MEMBER_TYPE` keeps it. 1 name.
    "?overloaded_fn@@$$J0YAXXZ": 'llvm drops `extern "C" ` with the member type, dbghelp keeps it',
}

#: The mask bits that changed nothing anywhere in `msvc-llvm-corpus.txt`, so no field was
#: added for them: there was nothing to score one against. Recorded rather than omitted,
#: because "not implemented" and "the reference does nothing with it" are different
#: claims and only the second one is true here. Re-derive with the tool's `--report`.
#:
#: `UNDNAME_NO_MS_THISTYPE` and `UNDNAME_NO_CV_THISTYPE` are the halves of
#: `UNDNAME_NO_THISTYPE`, and this `dbghelp` honours only the pair -- neither half alone
#: changes a spelling. `UNDNAME_NO_ARGUMENTS` is worse than inert: it refuses 600 of the
#: 605 names it is given and answers the other five with text that is not a declaration
#: of anything, so there is nothing there to follow.
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

GNU_DIVERGENCES = [
    # Empty. The last name here was `modern::measured`, whose template argument list
    # carries a requires-clause: c++filt prints it after the parameters and this printed
    # nothing for it. Inside the clause llvm-cxxfilt spells the template parameter
    # symbolically, `T`, and GNU spells the argument bound to it; each style follows
    # its own reference, and both are exact.
]

# One name left this list rather than being traded away:
# `_ZZN6modern13genericLambdaEvENKUlTyT_E_clIiEEDaS0_`, a generic lambda's `operator()`.
# It was read as GNU reads it once a recorded `<template-param>` stopped being frozen to
# the argument bound to it where the entry was made. What the declaration says -- g++
# emits the same shape for `[](auto x){}` called with an `int` -- is pinned in
# `tests/conformance/itanium-reference-defects.txt`.


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


def test_gnuv2_matches_libiberty_on_kde2():
    """See `GNUV2_REAL_WORLD_TOTAL` for what this does and does not establish."""
    total, exact = _score("gnuv2-real-world.txt", "llvm", language="gnuv2")
    assert (total, exact) == (GNUV2_REAL_WORLD_TOTAL, GNUV2_REAL_WORLD_EXACT)


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


def test_swift_reference_defect_corpus():
    """Names where following `swift-demangle` would mean printing `<null node pointer>`.

    See `SWIFT_REFERENCE_DEFECTS_TOTAL` and the file's own header.
    """
    total, exact = _score("swift-reference-defects.txt", "llvm", language="swift")
    assert (total, exact) == (SWIFT_REFERENCE_DEFECTS_TOTAL, SWIFT_REFERENCE_DEFECTS_EXACT)


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


def test_msvc_matches_llvm_undname_on_real_compiler_output():
    """See `MSVC_CLANG_TOTAL`. Names a compiler wrote, not vectors somebody chose."""
    total, exact = _score("msvc-clang.txt", "llvm", language="msvc")
    assert (total, exact) == (MSVC_CLANG_TOTAL, MSVC_CLANG_EXACT)


def test_msvc_matches_llvm_undname_on_boost():
    """See `MSVC_BOOST_TOTAL`."""
    total, exact = _score("msvc-boost.txt", "llvm", language="msvc")
    assert (total, exact) == (MSVC_BOOST_TOTAL, MSVC_BOOST_EXACT)


def test_msvc_reads_what_its_reference_cannot():
    """See `MSVC_REFERENCE_DEFECTS_TOTAL`. The expected column is the declaration."""
    total, exact = _score("msvc-reference-defects.txt", "llvm", language="msvc")
    assert (total, exact) == (MSVC_REFERENCE_DEFECTS_TOTAL, MSVC_REFERENCE_DEFECTS_EXACT)


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


def test_reference_defect_corpus():
    """Names where following a reference would mean printing a type the source disproves.

    The one corpus here whose expected column is not a reference demangler's output. See
    `REFERENCE_DEFECTS_TOTAL` and the file's own header.
    """
    total, exact = _score("itanium-reference-defects.txt", "llvm")
    assert (total, exact) == (REFERENCE_DEFECTS_TOTAL, REFERENCE_DEFECTS_EXACT)


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


def test_ada_matches_gnu_gnat_demangler():
    """See `ADA_REAL_WORLD_TOTAL`."""
    total, exact = _score("ada-real-world.txt", "llvm")
    assert (total, exact) == (ADA_REAL_WORLD_TOTAL, ADA_REAL_WORLD_EXACT)


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


def _every_corpus():
    """Every conformance file, so a corpus added later is covered without an edit."""
    names = {path.name for path in CONFORMANCE.glob("*.txt")}
    names.update(path.name.removesuffix(".gz") for path in CONFORMANCE.glob("*.txt.gz"))
    return sorted(names)


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
    # The property that matters, independent of spelling: one symbol, one meaning. Adding
    # a base path element must change the answer, or the demangler is losing the element.
    family = ["??_7A@B@@6BC@D@@@", "??_7A@B@@6BC@D@@E@F@@@", "??_7A@B@@6BC@D@@E@F@@G@H@@@"]
    assert len({demangle.demangle(name) for name in family}) == len(family)


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


@pytest.mark.parametrize("corpus", _every_corpus())
def test_a_style_does_not_decide_whether_a_name_parses(corpus):
    """A style is a spelling policy. It must not change what the grammar accepts.

    It did, for twelve names -- eleven of libcxxabi's own C++20 vectors and `std::pair`'s
    constrained constructor, which is what GCC 13 emits for the real `std::pair`. GNU
    c++filt substitutes the argument bound to a `<template-param>` inside a
    requires-clause where llvm-cxxfilt spells the parameter symbolically, so the GNU
    style resolves one -- and a clause names parameters of enclosing templates that are
    not all in scope. Failing to resolve refused the whole name, so `--style llvm` read
    it and `--style gnu` handed the symbol back mangled. What cannot be substituted now
    falls back to the spelling the other style uses.

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


#: How many demangled spellings the text filter rewrites again, over every corpus. Pinned
#: rather than driven to zero: a filter over prose cannot be a fixed point in general,
#: because a demangled name can contain a word that really is symbol-shaped. All seven
#: are pre-Itanium, where `T5__pt__11_PFiPPdPv_i` -- a component of a name libiberty
#: spells with the mangling still in it -- reads as a name of its own.
FILTER_REWRITES_AGAIN = 7


@pytest.mark.parametrize("corpus", _every_corpus())
def test_the_filter_does_not_rewrite_what_this_library_printed(corpus):
    """`demangle_text` over a spelling this library produced should leave it alone.

    It is the second pass a user gets by accident -- a log file that already went through
    the filter, a demangled name pasted into a report -- and it was corrupting output:
    `@escaping`, `@autoclosure` and `@Swift.MainActor` lost their `@` to Delphi's
    unit-scope routine, and `@GLIBCXX_3.4` and `@@CXXABI_FLOAT128`, which this library
    prints on every versioned symbol, lost theirs to the same rule. 89 of the first 20,000
    corpus names were affected. `FILTER_REWRITES_AGAIN` is what is left.
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


#: How many readable corpus names the text filter reports as *pieces* rather than whole,
#: by corpus. Every one is a name whose token the filter's alphabet cannot hold: MSVC
#: writes `<lambda_1>` and CodeWarrior a template argument list in angle brackets, which
#: the token leaves out on purpose -- objdump spells a call target
#: `call 1050 <_ZN3foo3barEv>`, and a token that takes the brackets in is one no scheme
#: reads. Go's six carry a `,` inside `[...]`, which the token also stops at, or a struct
#: shape spelled with spaces and quoted tags, and there the piece plus the text after it
#: still spells the whole name.
#:
#: Pinned because the number was 172 before `?` stopped being a character a Delphi
#: identifier may hold, and 2,983 before `%` and `#` became characters a token may. The
#: corpora that score one name per flag list the same name more than once, which is why
#: `msvc-dbghelp.txt` counts three: they are three rows of
#: `??R<lambda_1>@x@A@PR31197@@QBE@XZ`, the name `msvc-llvm-corpus.txt` already carries.
FILTER_REPORTS_PIECES = {
    "codewarrior-cwdemangle.txt": 2,
    "go-real-world.txt": 6,
    "msvc-dbghelp.txt": 3,
    "msvc-llvm-corpus.txt": 2,
    "msvc-name-only.txt": 1,
    "msvc-suppressions.txt": 4,
}


@pytest.mark.parametrize("corpus", _every_corpus())
def test_the_filter_reports_no_piece_of_a_name_it_reads_whole(corpus):
    """A fragment that happens to demangle is a reading of something that is not there.

    `@?0??define_lambda@@YAHXZ@QBE@XZ` -- what the token had left of an MSVC symbol after
    the angle bracket it cannot hold -- came back as
    `?0??define_lambda::__linkproc__ YAHXZ::QBE::XZ`, a Delphi declaration built out of
    half somebody else's name. This is the guard that found it.
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
    demangler is the code behind `llvm-cxxfilt`. 29,928 pairs, an order of magnitude more
    than anything this project had assembled, and the flagship scheme's real score
    against it.

    Checked in gzipped and pinned, so the number can only go up and cannot quietly stop
    being accurate. What still fails is named below rather than left as one number.
    """

    #: Raised as gaps close; never lowered silently. A drop means a vector that used to
    #: pass has stopped, which is a regression whatever the total.
    #:
    #: It was lowered once, from 29923, and the reason is in
    #: `test_the_shortfall_is_fifteen_names_and_this_says_which`: nine vectors record
    #: llvm-cxxfilt's own reading of a `<template-param>` recorded as a substitution
    #: candidate, which `tests/conformance/itanium-reference-defects.txt` establishes
    #: against four compilers' output is wrong. This corpus *is* that demangler's test
    #: file, so where it and the declaration disagree it is the corpus that is the
    #: record of a defect. And once more, from 29914, for a tenth such vector: a name
    #: numbered by GCC 12's closure-prefix rule, which the vector reads by the ABI's.
    EXPECTED_EXACT = 29913

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

    def test_the_shortfall_is_fifteen_names_and_this_says_which(self):
        """Fifteen left, in four groups, and none of them is a name read wrongly.

        **Four bare types.** `i`, `PKFvRiE`, `PVFvRmOE` and `PFvRmOE` are `<type>`
        manglings with no `_Z`. They are refused *as symbols* on purpose, as
        `llvm-cxxfilt` refuses them, because a demangler offered every symbol in a binary
        and willing to read `i` as `int` will rename half a C library. `demangle_type()`
        reads all four; see `tests/test_types.py`.

        **One self-referential conversion operator.** `_Zcv1BIRT_EIS1_E` is
        `operator B<T_&><B<T_&>>`, a cycle. `llvm-cxxfilt` guards against printing one by
        printing *nothing* for the second visit, so it answers `operator B<><>`. This
        used to read the type a second time once the arguments were bound and answer
        `operator B<auto&><auto&>`; neither is the declaration, because there is no
        declaration -- the mangling is self-referential and no compiler emits one.

        It is refused now, which is what `c++filt` 2.42 does with it. The rule that
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
        value is that it is the reference measuring itself. The ten are named here
        instead, so that one of them starting to pass is as visible as one of them
        starting to fail.
        """
        missed = [
            mangled
            for mangled, expected in load_corpus("itanium-libcxxabi.txt")
            if demangle.demangle(mangled) != expected
        ]
        assert sorted(missed) == [
            # bare types, refused as symbols
            "PFvRmOE",
            "PKFvRiE",
            "PVFvRmOE",
            # the reference's model of a recorded <template-param>
            "_Z1h1XIJZ1fIiEDaOT_E1AZ1gIdEDaS2_E1BEE",
            "_ZN1XIZ1fIiEvOT_EUlS2_DpT0_E_EclIJEEEvDpT_",
            # GCC 12's closure-prefix numbering, read by the ABI's rule
            "_ZNK1xMUlTyT_E_clIiEEDaS_",
            "_ZZ11inline_funcvENKUlTyTyT_T0_E_clIiiEEDaS_S0_",
            "_ZZ11inline_funcvENKUlTyTyT_T1_T0_E_clIiiiEEDaS_S0_S1_",
            "_ZZ18test_assign_throwsI20small_throws_on_copyLb0EEvvENKUlRNSt3__13anyEOT_E_clIRS0_EEDaS3_S5_",
            "_ZZN5test71fIiEEvvENKUlTyQaa1CIT_E1CITL0__ET0_E0_clIiiEEDaS3_Qaa1CIDtfp_EELb1E",
            "_ZZN5test71fIiEEvvENKUlTyQaa1CIT_E1CITL0__ET0_E1_clIiiEEDaS3_Q1CIDtfp_EE",
            "_ZZN5test71fIiEEvvENKUlTyQaa1CIT_E1CITL0__ET0_E_clIiiEEDaS3_Q1CIDtfp_EE",
            "_ZZN5test71fIiEEvvENKUlTyT0_E_clIiiEEDaS1_",
            # a self-referential conversion operator
            "_Zcv1BIRT_EIS1_E",
            # a bare type again
            "i",
        ]

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
