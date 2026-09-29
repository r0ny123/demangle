# Conformance

Correctness is defined against the reference implementations and measured on symbols
real compilers actually emit, not on hand-picked examples.

Every number here is pinned as an exact count in `tests/test_conformance.py`, so an
improvement cannot quietly mask a regression, and `tests/test_readme.py` checks this
page against those pins.

**Contents** — [checked-in corpora](#checked-in-corpora) ·
[notes on the corpora](#notes-on-the-corpora) ·
[whole symbol tables](#whole-symbol-tables) ·
[printing the name without its signature](#printing-the-name-without-its-signature) ·
[on trusting the references](#on-trusting-the-references) ·
[schemes not covered](#schemes-not-covered)

## Checked-in corpora

Replayed by the test suite. No compiler and no reference demangler needed.

| Corpus | Reference | Exact |
|---|---|---|
| Real shipped libstdc++ | `llvm-cxxfilt` 18.1.3 | **5913 / 5913** |
| Rust, both schemes | `rustfilt` (rustc-demangle 0.1.28) | **5316 / 5316** |
| MSVC — LLVM's own test corpus | `llvm-undname` 18.1.3 | **609 / 609** |
| MSVC RTTI type descriptors, both forms | `llvm-undname` 18.1.3 | **106 / 106** |
| MSVC ARM64EC hybrid names | LLVM's own mangling rule <sup>[4](#4-arm64ec-hybrid-names)</sup> | **606 / 606** |
| MSVC — the five flags `llvm-undname` has, over the differences they make | `llvm-undname` 18.1.3 | **1250 / 1253** <sup>[2](#2-where-a-dropped-return-type-leaves-a-bracket-open)</sup> |
| MSVC — the mask bits `llvm-undname` has no flag for <sup>[1](#1-the-dbghelp-mask-bits)</sup> | `dbghelp.dll` 10.0.26100.8328 | **1064 / 1064** |
| Rust toolchain (`rustc_driver`, `libstd`) | `rustfilt` | **394 / 394** |
| Purpose-built C++, llvm style | `llvm-cxxfilt` 18.1.3 | **318 / 318** |
| Purpose-built C++, gnu style | GNU `c++filt` 2.42 | **311 / 311** |
| Regression corpus | `llvm-cxxfilt` 18.1.3 | **30 / 30** |
| Names a reference reads wrongly <sup>[17](#17-where-following-a-reference-would-be-the-defect)</sup> | the declaration | **37 / 37** |
| Bare `<type>` encodings, llvm style | `llvm-cxxfilt --types` 18.1.3 | **1076 / 1076** |
| Bare `<type>` encodings, gnu style | GNU `c++filt -t` 2.42 | **1076 / 1076** <sup>[3](#3-bare-types-under-both-references)</sup> |
| Swift runtime + the compiler's own test corpus | `swift-demangle`, built from source <sup>[6](#6-the-swift-reference-is-built-here)</sup> | **8494 / 8494** |
| Swift — the compiler's own demangler vectors | `swift-demangle`, built from source <sup>[6](#6-the-swift-reference-is-built-here)</sup> | **514 / 514** |
| Nim 1.6 and 2.2, against the compiler's own record <sup>[14](#14-nim)</sup> | `.ndi` debug mapping | **2115 / 2115** |
| Free Pascal 3.2.2 runtime and packages <sup>[5](#5-free-pascal)</sup> | re-assembly + `ppudump` | **3899 / 3899** |
| Go, from the shipped toolchain | round trip <sup>[16](#16-go)</sup> | **1517 / 1517** |
| D, from the shipped libgphobos | GNU `c++filt --format=dlang` | **1257 / 1257** |
| Objective-C, three ABIs, against the declaration <sup>[9](#9-objective-c)</sup> | clang 18.1.3 + `libobjc.a` | **2665 / 2665** |
| Swift, simplified spelling — the compiler's own vectors | `swift-demangle --simplified` <sup>[6](#6-the-swift-reference-is-built-here)</sup> | **217 / 217** |
| Swift names the reference reads wrongly <sup>[6](#6-the-swift-reference-is-built-here)</sup> | the demangling tree | **5 / 5** |
| Delphi/C++Builder, against Embarcadero's unmangler <sup>[13](#13-delphi-and-cbuilder)</sup> | recorded `tdump -um` | **11363 / 11363** |
| Pre-Itanium C++ — libiberty's own vectors, both `DMGL_PARAMS` settings <sup>[10](#10-the-pre-itanium-family)</sup> | GNU `c++filt --format=<style>` | **1324 / 1324** |
| CodeWarrior — the reference's own vectors <sup>[11](#11-codewarrior)</sup> | `cwdemangle` 1.0 | **47 / 47** |
| Ada/GNAT — libiberty's own vectors <sup>[12](#12-ada-and-gnat)</sup> | GNU `c++filt --format=gnat` | **34 / 34** |
| JNI native method names, from real `native` declarations <sup>[15](#15-jni)</sup> | round trip <sup>[15](#15-jni)</sup> | **50 / 50** |

Replayed and pinned the same way, by each scheme's own test module: more of the
references' own vectors, and more samples of what compilers shipped.

| Corpus | Reference | Exact |
|---|---|---|
| LLVM's own Itanium vectors, `DemangleTestCases.inc` <sup>[21](#21-llvms-own-itanium-vectors)</sup> | LLVM's demangler | 29913 / 29928 |
| Rust — rustc-demangle's own `#[test]` vectors <sup>[23](#23-rustc-demangles-own-vectors)</sup> | rustc-demangle's own assertions | 47 / 51 |
| MSVC — real compiler output, `clang++ --target=x86_64-pc-windows-msvc` | `llvm-undname` 18.1.3 | 161 / 161 |
| MSVC — Boost 1.84's NuGet packages, a sample <sup>[19](#19-msvc-over-whole-libraries)</sup> | `llvm-undname` 18 | 5843 / 5843 |
| MSVC names `llvm-undname` reads wrongly | the declaration | 9 / 9 |
| D — libiberty's own vectors <sup>[22](#22-libibertys-own-d-vectors)</sup> | GNU `c++filt --format=dlang` | 366 / 366 |
| Pre-Itanium C++ from gcc 2.95 binaries <sup>[20](#20-a-pre-itanium-thunk-for-a-positive-delta)</sup> | libiberty `cplus_demangle`, `gnu` style | 12661 / 12661 |
| Ada/GNAT, from the shipped libgnat and libgnarl | GNU `c++filt --format=gnat` | 1438 / 1438 |
| Delphi/C++Builder, a per-kind sample of the whole dump | recorded `tdump -um` | 68 / 68 |
| Delphi/C++Builder constructs the export tables lack, hand-built <sup>[13](#13-delphi-and-cbuilder)</sup> | an independent unmangler | 53 / 53 |

## Notes on the corpora

What the numbered marks in the tables above point at, and the ones in
[whole symbol tables](#whole-symbol-tables) below. Each says what a row is measured
against where that is not simply "a reference demangler said so", or what the names
it does not account for are.

### 1. The dbghelp mask bits

`UnDecorateSymbolName` with the mask set, driven over the same 609 names by
`tools/generate_msvc_dbghelp_corpus.py`. The two references do not spell a name alike —
`dbghelp` prints `__ptr64`, writes `char * const`, and puts no space after a comma — so
its answer goes through eight spacing rewrites before it is recorded, and each rewrite is
proved on every name it is used for: a name is in the corpus only if rewriting the
reference's *unflagged* answer reproduces the row `msvc-llvm-corpus.txt` already pins
against `llvm-undname`. 478 of the 609 qualified; the reference declines four, and the
other 127 are names where the two disagree about spelling rather than spacing —
`char *const __restrict` against `char *__restrict const` is an ordering, not a gap —
and inventing a rule to reconcile those would be inventing a spelling. Cross-checking the
four bits that *do* mean what an `llvm-undname` flag means found the two references
disagreeing about how far a flag reaches; `UNDNAME_REACH_DIVERGENCES` in
`tests/test_conformance.py` names one of each.

### 2. Where a dropped return type leaves a bracket open

The three are names where `llvm-undname --no-return-type` leaves an unclosed
bracket — `int (__cdecl * (__cdecl B::*volatile memptrtofun7)(void)` is not a declaration
of anything. This keeps the balanced spelling, which is what the reference itself prints
with no flag.

### 3. Bare types under both references

The same 1,076 encodings under both references, which spell them differently: each
style reads every row of its own reference's corpus. The last three to close each
carried a doubled `KK` cv-qualifier, which `c++filt` folds away and `llvm-cxxfilt` keeps —
left alone for a while on the reasoning that no compiler emits `KK`, which was true of
the literal spelling and false of what it means, since the same doubling arrives through
an already-qualified template argument and the shipped libLLVM has three of those.

### 4. ARM64EC hybrid names

Nothing demangles ARM64EC's `$$h` marker — `llvm-undname` refuses these and so does
current upstream — so there is no reference spelling to copy. There is a normative
*rule*: LLVM's `getArm64ECDemangledFunctionName` says what an ARM64EC name is the hybrid
form of, and it is what the compiler emits an `EXPORTAS` directive against, so its answer
is the name the linker resolves. Every name in LLVM's own corpus with the marker inserted
where LLVM's *mangler* puts it must demangle to what the name without it demangles to —
and that column came from `llvm-undname`.

### 5. Free Pascal

Free Pascal ships no demangler either. The property is re-assembly — the parts this
splits out, rejoined with the compiler's own separators, must reproduce the symbol — and
it holds for all 236,570 readable symbols in the shipped runtime, not only the sample
above. Independently, `ppudump` prints both a unit's mangled names and the names it
declares, and every name read is one the unit declares. Case is not recoverable: Pascal
is case-insensitive and the compiler upper-cases before mangling.

### 6. The Swift reference is built here

Nothing a distribution ships reads a Swift name — `llvm-cxxfilt` and `c++filt` both
decline a `$s` outright — so `tools/swift-demangle-reference/` builds swiftlang/swift's
own `lib/Demangling` at a pinned revision behind a line-per-name front end. The revision
is a commit on `main` and not a release tag, because every release through 6.3.3 refuses
part of the compiler's own vector file: 5.10.1 scores 455 of the 514 and 6.3.3 does not
carry all of them. Its README has the measurements, and the one row where following it
would be the defect — `NodePrinter` reads an extended existential shape one child too
high and spells the type as `<null node pointer>`, a path its own test corpus never
exercises — is pinned in `tests/conformance/swift-reference-defects.txt` against what
the tree says instead.

### 7. Swift symbolic references

A mangled name in Swift *metadata* can hold a one-byte marker and a four-byte offset
in place of a type the image already describes, so reading one needs the image:
`swift.demangle_symbolic` takes bytes and a resolver, and `resolve.ContextResolver` is
one. Each reference resolves to a descriptor; the check is that the symbol the *linker*
put at that address demangles to the same name, with `swift-demangle` reading both sides.
Splicing each resolved fragment back in gives a self-contained name the reference can
read, and it agrees with our spelling on 4,799 of 4,799. A shared object's *indirect*
references point at pointer slots the loader fills, so `resolve.elf_image` applies the
dynamic relocations first and answers a slot filled from another image by the symbol's
own name, which is the mangling a descriptor's symbol carries; the same symbols stand in
where the walk declines, at a type declared in an extension or an opaque type
descriptor; an anonymous context, the scope of a type declared inside a function, is
spelled `(unknown context at $<address>)` as the runtime spells it. Over the 6.1.2
runtime that resolves 7,071 typerefs: 6,811 spell what the reference spells for the
spliced form, and the other 260 are names the reference cannot be given, because a
fragment spliced into a name shifts the substitution indices and word substitutions
around it — checked one by one.

### 8. How Apple clang numbers a substitution table

Apple's clang numbers the substitution table by a rule no other compiler uses: an
undeduced `auto` return type, `Da`, is a candidate — Clang 6.0's accident, kept by
Apple's fork in every version since — so every back-reference after one is one higher
than GCC or upstream clang would write, and llvm-cxxfilt and c++filt read every such
Mach-O name by the wrong entries. Of the 17,310 names carrying a `Da` in the bottles,
2,300 overrun the table under the common rule and 6,385 come back as a plausible wrong
declaration; this reads a `__Z` name by Apple's rule, retries the other when a
back-reference overruns or names something no type can be, and takes
`ItaniumOptions.undeduced_auto_substitution` to force either. Checked against clang 18
under `-fclang-abi-compat=6`, which writes the same numbering, and against upstream clang
targeting Darwin, which does not.

Of the 12,350 names in the first batch of bottles where this and `llvm-cxxfilt -_` part,
4,690 are that rule and 7,660 the template-parameter rebinding
<sup>[17](#17-where-following-a-reference-would-be-the-defect)</sup> describes, 7,573 of
them one generic lambda in ceres. Not counted in the 12,350: 67 names neither reads,
which are `$tlv$init`, the thread-local initialiser a Mach-O linker names after its
variable. A second batch — Arrow, DuckDB, RocksDB, gRPC, Cap'n Proto, libtorrent,
Xerces-C, RE2, libomp and Boost.Python, 295,281 more names — parts from
`llvm-cxxfilt -_` on 6,134: 677 are Apple's closure-prefix rule, described below, and
every other one either the `auto` rule or the rebinding. Arrow's `VisitVoid` joins the
reference-defects corpus, checked against its header, and so do a RocksDB and a DuckDB
closure under the closure-prefix rule. Not counted in the 6,134 either: 74 more
`$tlv$init`, and 22 gRPC promise types whose spelling runs past the 64K
`Limits.max_output`, so this refuses them by default — one is 69,094 characters under
`RELAXED_LIMITS`, and the reference refuses it outright.

The other place two compilers number the same name differently is a lambda in a
variable's or a member's initialiser. Its name goes through the variable —
`ns::g3::'lambda'(...)`, written `2ns2g3M...` — and the prefix before that `M` is a
substitution candidate under the ABI, upstream clang and GCC 13; GCC 12 and every
version before it wrote the `M` and skipped the entry, and so does Apple's clang in
every version, so every later back-reference is one lower. GCC 13 still emits the old
spelling as an alias beside the new. Read by the ABI's rule, such a name's references
resolve one entry early, and llvm-cxxfilt, LLVM's main branch and c++filt all print
`operator()(ns::Box, ns::Box)` for a lambda declared over `ns::Box<int>` — a template
with no arguments, standing as a type — and, for the 663 such names in Homebrew's
bottles of Apache Arrow, DuckDB and RocksDB, `std::function`'s allocator as an allocator
of the member the lambda initialised. This reads a `__Z` name by Apple's rule and a
`_Z` name by the ABI's, and where a back-reference then runs past the table or lands on
something no type can be — a closure prefix, or a template with no arguments after it
— reads the name again under the other; `ItaniumOptions.closure_prefix_substitution`
forces either. Established against g++ 13 under `-fabi-version=17` and `18`, against
clang 18 targeting Linux and Darwin, and against the bottles' own symbols.

### 9. Objective-C

Objective-C has no reference demangler, and barely a mangling: what there is comes from
the compiler rather than the language, so the rules are transcribed from clang's
`Mangle.cpp`, `CGObjCMac.cpp` and `CGObjCGNU.cpp`. The expected column is not this
library's own output but what the *declaration* said — every symbol was emitted by clang
for Objective-C this package wrote, or read out of the shipped GCC runtime. The
GNU-family method mangling is not injective, and clang says so where it writes it:
`_i_A_B_c` is `-[A(B) c]` and `-[A_B c]` alike. Every reading that re-mangles is found and
the preferred one is flagged `ambiguous`; the 26 names where the preference differs from
the declaration are listed in `tests/conformance/objc-lossy.txt` rather than rounded off.

### 10. The pre-Itanium family

Pre-Itanium C++ is five manglings that share one demangler: g++ before 3.0, Lucid's
`lcc`, the ARM/cfront encoding, HP aCC and EDG. The reference is libiberty's
`cplus-dem.c` at GCC 8.3.0, the last release that carried it, and the corpus is the 662
cases that tree's own `demangle-expected` marks `--format=gnu`, `--format=lucid`,
`--format=arm` or `--format=hp` — scored under both settings of `DMGL_PARAMS`, which is
what makes it 1,324. Nothing in one of these names says which of the five compilers
wrote it, so the style is an option and the default is `gnu`; a caller who knows the
binary passes `demangle.style("llvm", gnuv2={"style": "arm"})`. Because a name in this
family is an ordinary C identifier with a `__` in it, this scheme is offered *last* and
its detection reads the whole name rather than a prefix: of the names in every other
scheme's checked-in corpus it claims none, which `tests/test_gnuv2.py` checks on every
run, and over 339,117 symbols from this machine's own shared libraries it claims one —
`drm_intel_gem_bo_map__wc`, where `wc` is a valid argument list and libiberty reads it
exactly the same way. That reference is built here too: binutils 2.42 no longer ships
the pre-Itanium styles and GCC 9 removed the demangler, so `tools/cplus-dem-reference/`
compiles the 8.3.0 tree's own `cplus-dem.c` behind a line-per-name front end, pinned by
tag and by checksum, and the enumeration and mutation fuzzers ask it. It reproduces the
corpus 1,324 of 1,324, and over 420,000 mutants of it this library never reads a name
libiberty refuses; where the two part, libiberty is spelling a gap round something it
should have refused, and its README has the families.

### 11. CodeWarrior

Metrowerks CodeWarrior is the other pre-Itanium C++ mangling, and the one libiberty
never read: `cplus-dem.c` has no CodeWarrior flag and `demangle-expected` has no vectors
for it, so the reference is `encounter/cwdemangle` — the tool decompilation projects for
GameCube and Wii titles run, dedicated to the public domain — and the corpus is its own
test module. Detection is held to the same bar as the pre-Itanium family above: **0**
claims over the other schemes' corpora and **0** over 339,117 real symbols.

Where the two pre-Itanium schemes overlap — and they do, the encodings being that close —
GNU v2 is offered first, because a name valid under both should go to the commoner
mangling. `AtEnd__13ivRubberGroup` parses either way and they differ only in spelling.
What is unambiguously CodeWarrior — a template argument list written literally into the
symbol, `@LOCAL@`, `$localstatic`, a `__dt` the `gnu` style does not know — GNU v2 refuses
and it falls through. `language="codewarrior"` gets the whole scheme regardless.

### 12. Ada and GNAT

Ada, as GNAT encodes it, is the last of the pre-Itanium formats libiberty still
carries — when the GNU v2, lucid, ARM and HP styles were dropped from the default,
`--format=gnat` stayed. The reference is `ada_demangle` in `cplus-dem.c`, with GCC's own
`exp_dbug.ads` documenting the encoding normatively, and the corpus is the 34 cases
`demangle-expected` marks `--format=gnat`. One of them is a name the reference declines,
printing `<x_E>`; that is recorded as the name unchanged, which is how this says the same
thing.

Detection is the whole difficulty, because an Ada symbol carries no types and no marker:
`yz__qrs` is a package and a subprogram, and it is also exactly what a C program writes.
Parsing the name and claiming whatever parses reads **6,764** real symbols from this
machine's own libraries as Ada. So a name is claimed only when it carries something GNAT
wrote and a C compiler would not — `_ada_`, an `O`-operator, a `TK` task suffix, a `P`/`N`
protected subprogram, a stream `S[RWIO]`, a controlled `D[FA]`, an `X` body-nested marker,
a `___elabb`-style special name, a `_B`/`_E` entry body, an overload number — *and* the
whole name is accounted for. Measured under that rule: **0** claims over every other
scheme's corpus, libcxxabi's included, and **0** over 339,117 real symbols.

The cost is that 4 of the 34 vectors — `yz__qrs`, `x__m1`, `x__m3`, `x__y__j`, lower-case
identifiers joined by `__` and nothing else — are not auto-detected. They demangle under
`language="ada"`. That is the same bargain the Go scheme makes: not claiming a name
returns it unchanged, which is what an unreadable name does anyway, while claiming
someone else's rewrites it into a plausible lie.

The reference has a defect of its own here, and it is a crash rather than a reading:
see [where a reference crashes](#where-a-reference-crashes).

### 13. Delphi and C++Builder

Delphi and C++Builder share one mangling, `@Unit@Class@Method$qqrv`. There is no Delphi
compiler on the platforms this is developed on, so the grammar is Embarcadero's own
`unmangle.c` — the code TDUMP, the linker and the debugger run. The reference is a
recorded one: `tests/conformance/delphi-tdump.txt` is a dump of the real `tdump.exe
-q -um` over the export tables of real BPLs and C++Builder DLLs, and its expected column
is what that unmangler printed. All 11,363 readable entries are replayed in CI, and the
10 MSVC `@name@N` decorations sitting in the same tables are refused — Microsoft's 32-bit
`__fastcall` C decoration is not this scheme. That includes what the unmangler prints
where a C++ programmer would not: `SetFlat(const const bool)` for `qqrxo`, which is
`copy_args` emitting the qualifier and the type spelling it again. Free Pascal's
`$`-delimited names are a different mangling and are read by the `pascal` scheme.

Those export tables do not contain every construct the grammar has: they exercise 75% of
the parser, and the missing quarter is C++Builder territory — pointer to member,
`char16_t`, rvalue references, `__saveregs`, the special tables, the Delphi 4 template
forms. `tests/conformance/delphi-constructs.txt` covers those with 53 hand-built names,
corroborated against an independent implementation of the same unmangler rather than
against TDUMP. That is **weaker evidence**, it is kept in its own file so it cannot be
read as part of the row above, and its header says so. A C++Builder `.map` or TDUMP dump
carrying these shapes would replace it with real evidence.

### 14. Nim

Nim has no reference demangler either, and its mangling is not injective: `mangle`
drops an underscore before a digit, so `len0_16` and `len016` are the same symbol. What
carries correctness is the same round-trip property Go uses — re-mangling what we read
must reproduce the bytes — plus agreement with the name the compiler recorded for a
debugger. Names that had an underscore before a digit cannot come back exactly, and are
left out of the corpus and listed in `tests/conformance/nim-lossy.txt` instead: seven
symbols, three of them the FarmHash helpers `len0_16`, `len17_32` and `len33_64` in
`pure/hashes.nim` and four purpose-built. Across the 5,946 routine names in the Nim 1.6
and 2.2 standard libraries eight have that shape — those three and five MySQL wrapper
routines in `wrappers/mysql.nim`, which are `importc` and so emit raw C symbols — and
they are the eight the whole-library row misses.

### 15. JNI

JNI has no reference demangler either — neither binutils, LLVM, Ghidra nor IDA reads
one — but it is the only scheme here transcribed from a *normative* specification
rather than from a reference implementation, so the check is the specification's own
rule run backwards: re-encoding a reading must reproduce the symbol the compiler
wrote, escapes and overload descriptor included.

### 16. Go

Go has no reference demangler — `go tool nm` prints symbol names with their escapes
intact and nothing in the toolchain decodes one. So the check is a property instead:
re-escaping a decoded package path must reproduce the bytes the linker wrote, where the
escaping is a transcription of Go's own `objabi.PathToPrefix`. It is verified over every
symbol in the shipped toolchain binaries, not just the recorded sample.

### 17. Where following a reference would be the defect

A `<template-param>` recorded as a substitution candidate — and any component built over
one — is the *parameter*, not the argument bound to it where the entry was made. The
mangler canonicalises a template type parameter by level and index, so it reuses one
entry across two different templates, and the two readings differ in any name that
mentions a local entity. Freezing it prints a type the source disproves:
`std::__insertion_sort<llvm::cfg::Update<llvm::BasicBlock*>*, C>` taking
`llvm::BasicBlock*`, a generic lambda's `operator()<int>` taking `auto`, or a closure
declared `[](auto x)` taking `int`.

Settled against ten reduced sources compiled by g++ 13.3.0 and clang++ 18.1.3, checked
in under `tools/corpus_sources/reference_defects/`, and pinned with five more symbols
in `tests/conformance/itanium-reference-defects.txt` — the one corpus here whose expected
column comes from the declaration rather than from a demangler. `tools/generate_corpus.py`
excludes those names, so a regeneration cannot record the wrong answer again.

The ninth source is a different defect in the same table, and about the *count* of
entries rather than what one of them holds. `T_ I ... E` — a template template parameter
applied to arguments — is two grammar components, and 5.1.10 makes each a candidate, so
it contributes two entries. `llvm-cxxfilt` 18.1.3 records only the specialisation, so
every index at or after the parameter's is one out: it refuses `_Z1fI1AiEvT_IT0_ES3_S3_`
outright, and answers `void g<A, char>(A<char>, char<int>)` for its neighbour, where
`char<int>` is not a type. Both g++ 13.3.0 and clang++ 18.1.3 emit the two names
byte-identically for an ordinary `void f(C<T>, C<T>, C<T>)`, and GNU `c++filt` 2.42
agrees with the declarations.

The tenth is a third defect again, and the only one whose two sides each refuse the
other's output. An inheriting constructor's `<base class type>` — the `1C` of `CI2 1C` —
is a `<type>` and so a candidate, and the two compilers disagree about entering it: for
`struct D : C { using C::C; }` with `C(Kind, Kind)`, g++ 13.3.0 writes
`_ZN1DCI21CENS0_4KindES1_` and clang++ 18.1.3 writes `_ZN1DCI21CEN1C4KindES1_`, one
declaration under two numberings. `llvm-cxxfilt` 18.1.3 implements clang's and refuses
g++'s outright; GNU `c++filt` 2.42 reads both parameter lists and then names the
constructor after the base, `D::C`, which is neither compiler's declaration. This reads
both, by clang's rule with a retry under g++'s, and
`ItaniumOptions.inherited_constructor_substitution` forces either.

`llvm-cxxfilt` 18.1.3 freezes both, which is why the llvm-style rows under
[whole symbol tables](#whole-symbol-tables) are not 100%: over the 217,730 distinct
Itanium symbols in every shared library a stock Ubuntu 24.04 ships, it differs from this
on 322. GNU `c++filt` 2.42 refuses 227 of those and agrees with this on 91 of the 95 it
reads. Of the four left, three are the `std::once_flag::_Prepare_execution` shape, where
libstdc++'s own header settles it against *both* references; the fourth is a generic
lambda's own parameter in libclang-cpp, which `c++filt` numbers `auto:1&` where the llvm
style writes `auto&` — the same reading spelled differently, and the gnu style spells it
as `c++filt` does.

### 18. The gnu style over whole libraries

The `gnu` style reads every one of the 44,093 names `c++filt` reads in `libLLVM.so.18.1`
and spells all of them byte for byte as it does. Over the 217,057 of the 217,730 Ubuntu
symbols that `c++filt` reads, three differ, and all three are the
`std::once_flag::_Prepare_execution` shape above, where the declaration in libstdc++'s
own header says this is right and GNU is not — so what is left is not a gap. `c++filt`
refuses 673 of those 217,730 outright, and this reads 457 of them.

Five differences used to be listed here and are now reproduced: `&A::f` inside a template
argument, which GNU prints without the parameter list the mangling carries; the
`{default arg#1}` scope of an entity declared in a default argument; a generic lambda's
`auto:1`; how GNU brackets an operand by *kind* rather than by precedence; and a `const`
applied to a type that already carries one, which the mangling really does say and which
GNU folds away because no declaration spells `const const`.

A sixth is reproduced too, though it is a slip rather than a rule. GNU omits the space
it otherwise puts between two closing angle brackets when the last template argument is
an empty pack, because libiberty decides on a field it updates on every append and does
not restore when it rewinds the separator in front of an argument that printed nothing,
so the character it tests is that separator's space. The same output shows both
spellings in one name — `f<A<B<C>>, JE>` comes out `void f<A<B<C> >>(A<B<C> >)`. The
`gnu` style exists to reproduce `c++filt`, slip and all, so it now does; the default
`llvm` style spaces neither, as `llvm-cxxfilt` does.

### 19. MSVC over whole libraries

436,546 of the 436,644 read exactly as `llvm-undname` reads them, and none reads
differently. Of the 98 left, 91 are MD5-hashed names, which both hand back as they
stand; six are local statics carrying a `.0`-style suffix, which the reference reads by
stopping where the name ends and saying nothing about the rest — it reads `?x@@3HAjunk`
as `int x` — and this refuses rather than drop; and one is a `?filt$0` exception-filter
name both hand back.
Of the 471,881 from ITK, OpenCV, Qt 5, libzmq, leveldb and restbed, 467,844 read exactly
and none differently; 151 carry the deduced return type the release refuses; 3,662 are a
debug build's run-time-check data, `$rtcFrameData`, `$rtcName$N` and `$rtcVarDesc` after
a function's whole decorated name, which the reference reads as the function and this
refuses rather than drop, as with `.0`; 155 are `$initializer$` variables the reference
misreads — `??ALL$initializer$@DataSpace@H5@@...` comes back as
`H5::DataSpace::LL$initializer$::operator[]`, the identifier's first letter taken for an
operator code — and this refuses; and 67 are MD5-hashed names. That accounts for 471,879;
the sweep's record does not break out the last two.
Of Boost's 122,162, 116,870 read exactly as `llvm-undname` reads them and none reads
differently; 722 carry a deduced return type, `?A_P` for `auto` and `?A_T` for
`decltype(auto)`, which the release refuses and LLVM's main branch reads as this does;
4,372 are MD5-hashed names both hand back; and 198 are `$initializer$` variables neither
reads.

### 20. A pre-Itanium thunk for a positive delta

All 396 are one form: a thunk gcc 2.95 wrote as `__thunk_n8_` for a positive delta, which
libiberty reads as a method named `n8_setInstance`. The compiler's own `make_thunk` is the
authority, and `tests/conformance/gnuv2-real-world.txt` carries 12,661 of the rest.

### 21. LLVM's own Itanium vectors

`libcxxabi/test/DemangleTestCases.inc` is what the demangler behind `llvm-cxxfilt` is
tested against, so this row is that reference measuring itself. Fifteen are left, none of
them a name read wrongly, and `tests/test_conformance.py` names each one:

- Four are bare `<type>` manglings with no `_Z` — `i`, `PKFvRiE` — refused *as symbols*
  on purpose, as `llvm-cxxfilt` refuses them: a demangler offered every symbol in a binary
  and willing to read `i` as `int` will rename half a C library. `demangle_type()` and
  `demangle --types` read all four.
- Nine record `llvm-cxxfilt`'s own reading of a recorded `<template-param>`, which
  note [17](#17-where-following-a-reference-would-be-the-defect) settles against the
  compilers' output.
- One is a self-referential conversion operator, `_Zcv1BIRT_EIS1_E`, from LLVM's fuzz
  corpus. It has no declaration; the reference prints `operator B<><>`, and this refuses
  it, as `c++filt` 2.42 does.
- One, `_ZNK1xMUlTyT_E_clIiEEDaS_`, is numbered by GCC 12's closure-prefix rule and the
  vector reads it by the ABI's; see note [8](#8-how-apple-clang-numbers-a-substitution-table).

### 22. libiberty's own D vectors

`d-demangle-expected` reaches further than the shipped libgphobos does, and what its last
vectors needed was not in the D ABI at all: the five characters the reference names
inside a string, the different rule for a character literal, hex float and complex
values, associative-array values written as pairs where the type says so, struct and
function-literal values, `extern(Pascal)`, the anonymous and `__S<n>` path components it
leaves out, and the malformed template instances it refuses outright. Each was derived by
running the reference over the input space, since no specification describes it.

### 23. rustc-demangle's own vectors

Of the four this does not match, two are not differences from the *tool*: `rustfilt`
prints `foo@@16` for `_RC3foo.llvm.9D1C9369@@16` and echoes `ZN4testE` back unread,
exactly as this does, where the vectors record the library's own `Display`. The other two
are detection rather than spelling: `_ZN3foo5h05afE` carries a hash that is not rustc's
`17h` and sixteen hex digits, so this reads it as the C++ `foo::h05af` it could equally
be. rustc-demangle can afford the wider rule because it is only handed names a caller has
already decided are Rust's; this is offered every symbol in a binary.

Against the crate rather than its vectors: `tools/rustc-demangle-reference/` is a front
end over `rustc-demangle` 0.1.28 itself, and over the 5,753 distinct Rust symbols in the
corpora this spells 5,752 identically. The one is the `@@16` above.

## Whole symbol tables

Run live against the reference, not replayed.

| Binary | Symbols | Agree |
|---|---|---|
| `libLLVM.so.18.1` <sup>[17](#17-where-following-a-reference-would-be-the-defect)</sup> | 44,186 | **31 differ** |
| `libclang-cpp.so` + Polly + LTO <sup>[17](#17-where-following-a-reference-would-be-the-defect)</sup> | 39,020 | **236 differ** |
| `librustc_driver`, `libstd`, `libtest` | 20,697 | **100%** |
| `libstdc++.so.6`, gnu style | 5,990 | **100%** |
| `libLLVM.so.18.1`, gnu style | 44,093 | **100%** <sup>[18](#18-the-gnu-style-over-whole-libraries)</sup> |
| Every shared library Ubuntu 24.04 ships, llvm style <sup>[17](#17-where-following-a-reference-would-be-the-defect)</sup> | 217,730 | **322 differ** |
| Swift runtime + Foundation | 48,368 | **100%** |
| Swift 6.1.2 toolchain, all 29 runtime libraries <sup>[6](#6-the-swift-reference-is-built-here)</sup> | 135,492 | **1 differ** |
| Swift 6.1.2 toolchain C++ (`swift-frontend`, `liblldb`, `libsourcekitdInProc`), names not in the rows above <sup>[17](#17-where-following-a-reference-would-be-the-defect)</sup> | 185,532 | **62 differ** |
| Nim standard library routine names <sup>[14](#14-nim)</sup> | 5,946 | **99.87%** |
| Free Pascal runtime and packages <sup>[5](#5-free-pascal)</sup> | 236,570 | **100%** |
| `libgphobos` + `libgdruntime` (D) | 16,333 | **100%** |
| `libgnat` + `libgnarl` (Ada), against `c++filt --format=gnat` | 11,237 | **100%** |
| LDC 1.40 runtime (D 2.110 frontend), the names `c++filt` reads | 15,350 | **100%** |
| LLVM 18.1.8 Windows release, every static library (MSVC) | 436,644 | **100%** <sup>[19](#19-msvc-over-whole-libraries)</sup> |
| Boost 1.84, twenty-nine `boost_*-vc143` NuGet packages (MSVC 14.3, x64 and x86) | 122,162 | **100%** <sup>[19](#19-msvc-over-whole-libraries)</sup> |
| ITK 5.0, OpenCV 5, Qt 5.9, libzmq 4.3, leveldb and restbed, NuGet packages (MSVC 2017 to 2022) | 471,881 | **100%** <sup>[19](#19-msvc-over-whole-libraries)</sup> |
| 46 C++ development packages' static libraries (libc++, Boost, Abseil, gRPC, RocksDB, Cap'n Proto...) <sup>[17](#17-where-following-a-reference-would-be-the-defect)</sup> | 109,606 | **74 differ** |
| Go toolchain (`go`, `compile`, `link`) | 30,733 | round trip <sup>[16](#16-go)</sup> |
| Objective-C, 3 ABIs + shipped `libobjc.a` <sup>[9](#9-objective-c)</sup> | 3,163 | **100%** |
| Swift metadata symbolic references <sup>[7](#7-swift-symbolic-references)</sup> | 4,528 | **100%** |
| Swift 6.1.2 runtime typerefs, resolved through the dynamic relocations and symbols <sup>[7](#7-swift-symbolic-references)</sup> | 7,071 | 6,811 agree; 260 unspliceable |
| KDE 2.2.2, omniORB 3.0.4, gtkmm 1.2 and libstdc++ 2.10, as gcc 2.95 mangled them (pre-Itanium), against libiberty | 56,347 | **396 differ** <sup>[20](#20-a-pre-itanium-thunk-for-a-positive-delta)</sup> |
| Homebrew bottles of Boost, folly, Abseil, protobuf, Poco, fmt, TBB, ceres, ICU and glog (Apple clang, Mach-O) <sup>[17](#17-where-following-a-reference-would-be-the-defect)</sup> <sup>[8](#8-how-apple-clang-numbers-a-substitution-table)</sup> | 108,839 | **12,350 differ** <sup>[17](#17-where-following-a-reference-would-be-the-defect)</sup> <sup>[8](#8-how-apple-clang-numbers-a-substitution-table)</sup> |
| Homebrew bottles of Arrow, DuckDB, RocksDB, gRPC, Cap'n Proto, libtorrent, Xerces-C, RE2, libomp and Boost.Python (Apple clang, Mach-O) <sup>[17](#17-where-following-a-reference-would-be-the-defect)</sup> <sup>[8](#8-how-apple-clang-numbers-a-substitution-table)</sup> | 295,281 | **6,134 differ** <sup>[17](#17-where-following-a-reference-would-be-the-defect)</sup> <sup>[8](#8-how-apple-clang-numbers-a-substitution-table)</sup> |
| Delphi/C++Builder BPL and DLL export tables <sup>[13](#13-delphi-and-cbuilder)</sup> | 11,363 | **100%** |

The rows add up to 2,684,162 names. Some are counted in more than one row — libLLVM is
read in both styles, and libLLVM, libclang-cpp and libstdc++ are also among the Ubuntu
row's shared libraries — so the distinct figure is lower: about 2,500,000.

Every row is exact except the ten marked and the 6.1.2 typeref row, whose 260 are names
the reference cannot be handed at all <sup>[7](#7-swift-symbolic-references)</sup>. On
the ten, every difference is accounted for in the notes above, name by name, and all but
three rows' worth are a name a reference reads wrongly
<sup>[17](#17-where-following-a-reference-would-be-the-defect)</sup>
<sup>[8](#8-how-apple-clang-numbers-a-substitution-table)</sup>. The three are their own
case: the one in the Swift runtime row
<sup>[6](#6-the-swift-reference-is-built-here)</sup> is the shape
`tests/conformance/swift-reference-defects.txt` pins; the 396
<sup>[20](#20-a-pre-itanium-thunk-for-a-positive-delta)</sup> are one form of
pre-Itanium thunk; and Nim's eight <sup>[14](#14-nim)</sup> are routine names with an
underscore before a digit, which Nim's own mangling discards, so no reading can recover
them.

The 62 in the Swift toolchain's C++, the 74 in the development packages and 7,660 of the
12,350 in the Homebrew bottles are the template-parameter rebinding
`_Prepare_execution` shows — `std::call_once`, Cap'n Proto's `kj::evalNow` and the
`ArrayRefView` lambdas — where this spells the parameter the header declares. The rest
of the Homebrew differences are Apple's own numbering rules
<sup>[8](#8-how-apple-clang-numbers-a-substitution-table)</sup>.

The purpose-built corpus reached 100% while libstdc++ was demangling *one symbol in
5,913* — the first one carried an ELF version suffix, a shape no hand-written test thinks
to include. Nearly every defect fixed here came from reading real shipped binaries.

The libstdc++ row is against GNU rather than LLVM because llvm-cxxfilt 18 reads 78 of
those 5,990 names as unreadable and echoes them back: the transaction-safe clone prefix
`_ZGTt`, and the `DF` floating-point productions. Checking against GNU over the whole
library rather than over the recorded sample is what found two real gaps here — `DF16b`
is `std::bfloat16_t` and `DF<n>x` is `_FloatNx` terminated by its own `x`, and GNU puts a
space before a template argument list whose name ends with `<`, so `operator<< <int>`.
No name in either checked-in corpus carried any of them.

The purpose-built corpus still earns its place: compiled by **both** `clang++` and `g++`
across C++11 through C++20 at two optimisation levels, it reaches constructs a released
library happens not to contain — concepts, coroutine frames, generic lambdas,
requires-clauses.

Regenerate with `tools/generate_corpus.py` and `tools/generate_rust_corpus.py`; compare
against a live reference with `tools/differential.py`.

## Printing the name without its signature

`-p` is `c++filt -p`: over the shipped libstdc++ and the GNU-style corpus the two agree
on **6138 / 6224** names. Of the 86, 85 are deliberate. `c++filt` strips the parameter
list only from the outermost declaration, so a thunk keeps its target's — `non-virtual
thunk to X::~X()` — and it drops a `[clone .cold]` suffix while keeping an
`@@GLIBCXX_3.4` one. This strips throughout and keeps both suffixes, because a filter over
a symbol table should not quietly discard part of the symbol: 72 thunks and 13 clones.
The last one is a name `c++filt` refuses and this reads.

## On trusting the references

Not blindly. `clang-cl` emits `?f@@YAX_L@Z` for `__int128` and `llvm-undname` — LLVM's
own demangler — rejects it. The two C++ references contradict each other on substitution
numbering. Both echo their input on failure, which reads exactly like success.

So the split is deliberate: the **ABI specification** governs grammar and structure, the
**references** govern spelling. Ambiguities were settled by probing both references and
accepted only where they agreed (`tools/probe_substitutions.py` makes one print its own
substitution table). Where they genuinely differ, the difference is a `style` rather than
a silent winner.

### Where a reference crashes

One reference defect is a crash rather than a reading, and it is binutils' to fix.
`c++filt` 2.42 aborts on an eight-character GNAT name:

```
$ printf 'aSO__bDF\n' | c++filt --format=gnat
*** buffer overflow detected ***: terminated
Aborted
```

Two stream-attribute expansions in one name are what overrun it: `aSO__bSO` goes the
same way and `aSO__bSR` does not, because the buffer has seven characters of slack,
`'Read` grows the name by three and `'Output` by five. `ada_demangle` sizes the buffer
`strlen (mangled) + 7 + 1` on the reasoning that the expanding cases occur only once, but
its loop comes back round for every `__`-separated component, so a long enough symbol
writes about four bytes past the end for every five characters of its own. `nm
--demangle=gnat` and `objdump --demangle=gnat` abort on an object file carrying such a
symbol too. Only the GNAT format reaches it; `c++filt` left to detect the scheme does not.
It is GCC PR 92453, rediscovered as GCC PR 103893 and binutils PR 28736, and still
unfixed on GCC master.

This reads the name as `a'Output.b.Finalize`, pinned in `tests/test_ada.py`, and
`ask_tolerantly` in `tools/mutate.py` splits a batch down to the name that crashed the
reference and leaves that one out of the comparison.

## Schemes not covered

The fourteen schemes this reads are the ones it set out to read. These were surveyed and
are deliberately not planned:

- **Zig, Erlang, Julia, V, Odin and Kotlin/Native** — not mangling schemes: they emit
  readable or unencoded names.
- **Borland C++Builder** — already covered: it is the Delphi scheme.
- **Objective-C++** — already covered: it is Itanium with Objective-C types.
- **Watcom C++**, **Sun Studio** (whose `libdemangle` is undocumented) and **gcj**
  (libiberty's `DMGL_JAVA`) — extinct enough not to be worth the transcription.

A request for one of these is still worth making if it comes with real names out of a
binary, which is what would change the reasoning.
