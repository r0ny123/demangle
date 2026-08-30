# Roadmap

What is not done yet, and -- for the schemes -- what was done and why, so the reasoning
survives the commit that carried it. Each open entry says what would have to change, so
anyone can pick one up.

**No scheme is outstanding.** Section 1 is now a record rather than a queue: every
mangling this project set out to read, it reads, and the survey below says why nothing
else on the list is worth transcribing. What remains open is the measured shortfall
against the upstream corpora, immediately below, and every item of it is documented
rather than merely counted.

Conformance is measured against the reference projects' *own* corpora as well as the
ones checked in here. Every checked-in corpus is exact against its reference, and so are
whole symbol tables from libLLVM, libclang-cpp, the Rust toolchain, libstdc++, the Swift
runtime, libgphobos, the Free Pascal runtime, the shipped Objective-C runtime and every
symbolic reference in the Swift metadata — well over half a million real symbols. What
follows is what the upstream corpora still find, all of it pinned in both directions by
the test suite so it can only go up and cannot quietly stop being accurate.

**Itanium**, libcxxabi's `DemangleTestCases.inc`: 29,914 of 29,928 exact. Fourteen left,
and thirteen of them are not shortfalls: four are bare types refused on purpose, and nine
record llvm-cxxfilt's own reading of a recorded `<template-param>`, which heading 0 below
settles against four compilers' output. The number went *down* from 29,923 for that
reason, and `tests/test_conformance.py` says so where it pins it.

- Four are `<type>` manglings with no `_Z` prefix at all — `i` for `int`,
  `PKFvRiE` for `void (*)(int&) const`. They are refused *as symbols*, deliberately and
  as `llvm-cxxfilt` refuses them: a demangler offered every symbol in a binary and
  willing to read `i` as `int` will rename half a C library. Asked for deliberately they
  are read — `demangle_type(enc, language=...)`, or `demangle --types -l itanium`, which
  is `c++filt -t` and `__cxa_demangle`'s type mode. So these four stay counted against
  the corpus, because the corpus scores the symbol entry point, and none of them is
  unreadable.
- ~~**Per-level template parameter tracking**~~ — *done*. `TemplateArgumentTable` is a
  stack: level 0 is the innermost `<template-args>`, and each generic lambda and each
  template template parameter declaration opens a level of its own, so `TL<k>_<n>_`
  reaches past one to the list outside it. Held flat, the levels overwrote each other
  and such a reference came out as the numbering it carried — `T`, `T1` — which names
  nothing. A level that is not in scope at all is now refused rather than named, as the
  reference refuses it; inside a `<constraint-expression>` it is still spelled by its own
  mangled text, which is also what the reference does and for the reason it gives.
- ~~**An expansion over an empty pack, inside an expression**~~ — *done*. `Dp` already
  dropped the argument in a type list; `sp` did not, so `getT<$_5>()()(std::forward<>(fp))`
  was printed where the reference prints `getT<$_5>()()()`. Handled where the reference
  handles it: any member of a comma-separated list that prints nothing takes its comma
  with it.
- ~~**A `sr` whose type carries template arguments**~~ — *done*. `srN <unresolved-type>
  <template-args> E <base-unresolved-name>` — arguments after the type in the `N` form,
  and *zero* qualifier levels after them, neither of which the ABI's own grammar admits
  and both of which Clang emits. The arguments also sit outside the substitution entry
  the type records, so an `S_` written after one names the bare parameter.
- **The last one is a self-referential conversion operator**, `_Zcv1BIRT_EIS1_E`, whose
  type is the argument list that contains it. The reference guards against printing a
  cycle by printing *nothing* the second time round, so it answers `operator B<><>`;
  this reads the type again once the arguments are bound and answers
  `operator B<auto&><auto&>`. Neither is the declaration, because there is no
  declaration — no compiler emits this, and it comes from LLVM's fuzz corpus.

**D**, libiberty's `d-demangle-expected`: 366 of 366. What the last of them needed was
not in the D ABI at all -- the five characters the reference names inside a string, the
different rule for a character *literal*, hex float and complex values, associative-array
values written as pairs where the type says so (through a back reference, if that is how
it was written), struct and function-literal values, `extern(Pascal)`, the anonymous and
`__S<n>` path components it leaves out, and the malformed template instances it refuses
outright. Each was derived by running the reference over the input space rather than read
from a specification that does not describe it.

**Swift**, `test/Demangle/Inputs/manglings.txt`: 513 of 513, with no name answered by a
different spelling at any point along the way. The last eight were features Swift added
after this scheme was written -- function-signature specialisation kinds (an escaping
closure, a closure the same as an earlier argument, propagated structs, and `p` becoming
a run rather than one constant), arguments the optimiser dropped, `Tfr` representation
changed, the `$e` Embedded Swift prefix, macro expansion source locations, pack protocol
conformances and an opaque result type's conformance. Each was transcribed from
swiftlang/swift's own `Demangler.cpp` and `NodePrinter.cpp` rather than fitted to the
vectors, which is the only way the no-wrong-spellings property survives.
`simplified-manglings.txt` is a whole output mode this does not have; see section 3.

**Rust**, rustc-demangle's own `#[test]` vectors: 47 of 51. Two of the other four are
not differences from the *tool*: `rustfilt` prints `foo@@16` and echoes `ZN4testE` back
unread, exactly as this does, and the vectors record the library's own `Display` instead.
The remaining two are detection rather than spelling — `_ZN3foo5h05afE` carries a hash
that is not rustc's `17h` and sixteen hex digits, so this reads it as the C++ `foo::h05af`
it could equally be. rustc-demangle can afford the wider rule because it is only ever
handed names a caller has already decided are Rust's; this plugin is offered every symbol
in a binary.

**GNU style**, against `c++filt` 2.42: nothing differs in the 44,093 C++ symbols it reads
in the shipped libLLVM, and 3 differ of the 217,057 it reads across every shared library a
stock Ubuntu 24.04 ships. It was 31 in libLLVM before the `<template-param>` fix under
heading 0 and 80 across the 217,730 after it; the five spelling policies that accounted
for the rest are listed as closed there. All three that remain are the
`std::once_flag::_Prepare_execution` shape, where libstdc++'s own header settles it
against GNU — so this is a shortfall of the reference and not of the style. `c++filt`
refuses 673 of those 217,730 outright and this reads 457 of them. One name in the
purpose-built gnu corpus is pinned, for the requires-clause disagreement rather than for
any of these.

**Nim**: seven shortfalls, that language's own mangling discarding an underscore, listed
by name in `tests/conformance/nim-lossy.txt`.

## 0. Where a reference is wrong

Agreeing with a reference is not the same as being right, and this is the heading for the
places where that has been established rather than assumed. The vectors live in
`tests/conformance/itanium-reference-defects.txt` — the one corpus here whose expected
column comes from the declaration rather than from a demangler — with reduced sources in
`tools/corpus_sources/reference_defects/` and the compiler named on each entry.

- ~~**A recorded `<template-param>` resolved where it was written, not where it is
  read**~~ — *fixed*, for the parameter and for anything built over one. The entry a
  `<template-param>` contributes to the substitution table is the parameter, not the
  argument bound to it at that point, because the mangler canonicalises a template type
  parameter by level and index and so reuses one entry across two different templates.
  Over the 217,730 distinct Itanium symbols in every shared library a stock Ubuntu 24.04
  ships, this changed the answer for 315; llvm-cxxfilt 18.1.3 differs from the corrected
  answer on 322, and GNU c++filt 2.42 refuses 227 of those and agrees with us on 91 of
  the 95 it reads.

- ~~**A generic lambda's own parameter, reached through a substitution**~~ — *closed in
  the gnu style*, which now spells the number GNU spells. For

      template <class T> void run(T &a) { take([](auto &x) { return x; }); }

  both g++ 13.3 and clang++ 18.1.3 write the closure's parameter as `S3_`, the `R T_`
  entry from `run`'s own signature. Read under the closure, `T_` is the closure's
  implicit `auto`, which GNU c++filt spells `auto:1&`; read under `run` it is
  `NoopAnalysis&`, which is what this spells. Closing it means modelling the implicit
  template parameter list a generic lambda has even when the mangling does not declare
  one with `Ty`. What was missing turned out to be only the number: this already read
  `S3_` as the closure's own unbound parameter and spelled it `auto`, where GNU spells
  `auto:1` — by the parameter's *index*, so `Ul T0_ T_ E` is `(auto:2, auto:1)`, which
  is what tells two of a lambda's parameters apart when both are `auto`. Read off
  `c++filt` and put behind `gnu_closure_spelling`, since llvm-cxxfilt spells every one of
  them `auto`. The shipped instance is `_ZSt9transform...runDataflowAnalysis...` in
  libclang-cpp-18 and the reduced case is
  `_Z4takeIZ3runI12NoopAnalysisEvRT_EUlS3_E_EvS2_`; both now match `c++filt` byte for
  byte. The llvm style still differs from llvm-cxxfilt on that name, and deliberately:
  llvm resolves `S3_` under `run` rather than under the closure, which is the defect
  above.

- ~~**The remaining gnu-style spelling gaps**~~ — *closed*, 80 of the 217,057 names GNU
  c++filt reads down to 3, and those 3 are the reference defect above. None of the five
  changed what a name *means*; all were spelling policy, each read off `c++filt` with
  probes rather than guessed, each behind its own option and off under llvm style.

  - `&entity` in a template argument, 60 names, all of them LLVM's `sandboxir`. GNU
    prints `&A::f` where llvm-cxxfilt prints the whole declaration `&A::f(int)`, and
    brackets it — `&(A::f() const)`, `&(void A::f<int>())`, `&(f())` — for every shape
    that is not a bare *qualified* function name. `tests/test_address_of.py`.
  - Operand bracketing, 11 names and every expression the corpus does not cover. GNU
    brackets by *kind* rather than by precedence: a name, a qualified name, a braced
    initialiser list and a function parameter go bare and everything else is wrapped, so
    it writes `(1)+(2)` and `!(x<int>)` but `std::x+(2)` and `{parm#1}+(2)`. Arguments on
    a *qualifier* do not make a name a template-id — `!is_array<T>::value` is unbracketed
    — so the last component decides. `tests/test_gnu_expressions.py`.
  - A doubled cv-qualifier, 3 names. `const` applied to a type that already carries it
    adds nothing, so `c++filt` folds the repeat away and `llvm-cxxfilt` keeps it; the
    outer one wins, which is why `K V K i` and `K K V i` both print `volatile const`.
    Verified over all 39 sequences of one to three qualifiers.
    `tests/test_types.py::TestDuplicateQualifiers`.
  - The `{default arg#1}` scope of an entity declared in a default argument, 2 names,
    which llvm-cxxfilt drops — giving one name to two different lambdas when a function
    has one in its body too. `tests/test_local_names.py`.
  - A generic lambda's `auto:1`, 1 name; see the item above.

- ~~**`gnuv2` claims ordinary C symbols**~~ — *mostly closed*, issue #6. Two of the
  three causes were spellings no declaration contains, and `_plausible` now refuses them:
  a parameter list holding `int0_t` (what libiberty prints when `I` is followed by
  something that is not hex) and `void` used as one parameter among several. Detection
  only — `language="gnuv2"` stays bug-compatible with libiberty, which is what the
  1,324-vector corpus measures — and neither rule costs a vector in it. CodeWarrior holds
  the `void` rule too, because it reads the same letters out of the same C names. False
  claims over the 345,601 shipped symbols: 8 to 3.

  The three left are the third cause and it has no fix. `PyInit__lldb` is a well-formed
  encoding of `(long, long, double, bool)` and `drm_intel_gem_bo_map__wc` of
  `(wchar_t, char)`; nothing distinguishes them from a real pre-Itanium symbol, because a
  free function in that mangling is spelled exactly like a C identifier with a `__` and a
  run of type letters. Binutils 2.42 dropped `gnu-v2` from `--format` rather than keep
  guessing. The `schemes/go` option — require positive evidence before auto-claiming a
  bare `name__<builtins>`, and leave the rest to `language="gnuv2"` — was measured rather
  than argued about, and it costs 58 of the 458 corpus names detection currently claims:
  `overload1arg__Fi`, `polar__Fdd`, `complexfunc5__FPFPc_PFl_i` and the rest of
  libiberty's own free functions, which have no class, no template and no marker by
  construction. The narrower form — decline only when every parameter is a builtin —
  drops the same 58, because it is the same set. 58 correct readings to remove 3 wrong
  ones is the wrong side of the trade, and `TestTheThreeItStillClaimsWrongly` in
  `tests/test_gnuv2.py` pins both sides of it so the rule cannot be adopted by accident.

## 1. More schemes

*Nothing here is outstanding.* Kept as a record of what each scheme is measured against,
because that differs per scheme and is the part worth knowing before trusting a number.

The plugin interface exists so a scheme needs no core changes. Go landed that way,
without touching `core` at all, and so did the thirteen that followed -- fourteen schemes
in total, the last three of them (pre-Itanium C++, CodeWarrior, Ada/GNAT) in this branch.

D, Swift, Nim, Free Pascal, Objective-C and Delphi have all landed the same way. What
each is measured against differs, and the difference is the interesting part:

- **D** — 100% against GNU binutils' `c++filt --format=dlang`.
- **Swift** — exact against `swift-demangle` 5.10.1 over the whole shipped runtime and
  the compiler's own test corpus, in both the current mangling and Swift 3's.
- **Nim** — no reference demangler exists, so the property is that re-mangling what is
  read reproduces the symbol, plus agreement with the name the compiler recorded in its
  own `.ndi` files.
- **Free Pascal** — no reference demangler either; the property is re-assembly, over all
  236,570 readable symbols in the shipped runtime, plus a check against `ppudump`.
- **Delphi / C++Builder** — no Delphi compiler here, so the grammar is Embarcadero's
  `unmangle.c` and the spelling is what TDUMP prints. A different scheme from Free
  Pascal's.

- **JNI** — *landed*. `Java_com_example_Foo_bar__Ljava_lang_String_2` is the C function
  a `native` method is called through, and Android ships them by the thousand; the usual
  way to read one is by eye, because neither binutils nor LLVM reads them and neither
  does Ghidra or IDA. It is also the one scheme here whose encoding is *written down
  normatively* -- the JNI specification's "Resolving Native Method Names" -- rather than
  having to be transcribed from a reference implementation. What it cannot recover is
  the package/class boundary, which the encoding genuinely does not carry, so the
  spelling puts the whole path in one run rather than inventing a structure.

- **Pre-Itanium C++: the GNU v2 / cfront / ARM family** — *landed*. `__ls__7ostreamPCc`,
  `BuildLight__9CGuiLightCFv`. binutils *deleted* these styles in 2019 and Ghidra ships a
  second, older copy of libiberty specifically to keep reading them, which was the
  loudest available signal that analysts still meet them — console and embedded
  decompilation lives on this. It was the largest single gap and the only item left that
  was a whole scheme.

  Five manglings, one demangler: g++ before 3.0, Lucid's `lcc`, the ARM/cfront encoding,
  HP aCC and EDG are the same code in `libiberty/cplus-dem.c` under five style flags, and
  the port is a transcription of it at `releases/gcc-8.3.0`, the last release that
  carried it. Scored against that tree's own `demangle-expected`: the 662 cases marked
  `--format=gnu`, `--format=lucid`, `--format=arm` or `--format=hp`, under both settings
  of `DMGL_PARAMS` — **1324 of 1324**.

  The two things that made it a pass of its own were the ones named here before it
  started. The demangler is *stateful* in a way none of the other schemes are —
  constructor and destructor counters the signature code decrements, a type vector for
  back references plus separate B and K squangling vectors, and an
  `iterate_demangle_function` that saves and restores the whole state to retry a
  different `__` split when the first guess fails. And detection was the shipping risk
  rather than the reading: a GNU v2 name is an ordinary C identifier with `__` in it, so
  the whole name is parsed before anything is claimed, the scheme is offered *last* of
  all, and the false-positive rate was measured before it landed — **0** claims over the
  80,748 names in every other scheme's corpus, and **1** over 339,117 real symbols, on a
  name `c++filt --format=gnu` reads the same way.

  Which of the five wrote a name is not recoverable from the name, so the style is an
  option rather than a guess: `demangle.style("llvm", gnuv2={"style": "arm"})`, with
  `gnu` the default.

- **Metrowerks CodeWarrior** — *landed*, beside the four above. The other pre-Itanium
  mangling, and a scheme of its own rather than a sixth style, because libiberty never
  read it: `cplus-dem.c` has no CodeWarrior flag and `demangle-expected` has no vectors
  for it. The reference is `encounter/cwdemangle`, the tool decompilation projects for
  GameCube and Wii titles run, and its own test module is the corpus — **47 of 47**,
  under all three of its option settings. Detection to the same bar as its neighbour:
  **0** claims over the other schemes' 80,748 names and **0** over 339,117 real symbols.

  Where the two overlap, GNU v2 is offered first: a name valid under both should go to
  the commoner mangling, and what is unambiguously CodeWarrior — a literal `<...>`
  argument list, `@LOCAL@`, `$localstatic`, a `__dt` — GNU v2 now refuses rather than
  mis-reads.

- **Ada/GNAT** — *landed*, **34 of 34** against the cases `demangle-expected` marks
  `--format=gnat`. The last of the pre-Itanium formats libiberty still carries: when the
  GNU v2, lucid, ARM and HP styles were dropped from the default, `--format=gnat` stayed.
  The reference is `ada_demangle` in `cplus-dem.c`, with GCC's own `exp_dbug.ads`
  documenting the encoding normatively. Narrow but concentrated: avionics, rail, defence.

  There are no types in it. An Ada symbol names an entity and stops, which makes the
  grammar small and the *detection* the whole problem: `yz__qrs` is a package and a
  subprogram, and equally it is what any C program writes. Parsing and claiming whatever
  parses reads 797 names from the other corpora here and **6,764 real symbols** from this
  machine's libraries as Ada. So a name is claimed only when it carries an encoding GNAT
  writes and a C compiler does not, *and* the whole name is accounted for — **0** claims
  over the other schemes' 81,457 names, **0** over 339,117 real symbols. The 4 vectors
  with no such encoding are read under `language="ada"` and not by guess.

With that, nothing on this list is open. Everything else surveyed is either not a
mangling scheme at all (Zig, Erlang, Julia, V, Odin and Kotlin/Native emit readable or
unencoded names), already covered here
(Borland C++Builder *is* this project's Delphi scheme; Objective-C++ is Itanium with
Objective-C types), or extinct enough not to be worth the transcription (Watcom,
Sun Studio's undocumented `libdemangle`, gcj's `DMGL_JAVA`).

The last one before JNI was Delphi:

- ~~**Borland/Embarcadero Delphi**~~ — *landed*. A different scheme from Free Pascal's,
  written `@Unit@Class@Method$qqrv`, transcribed from Embarcadero's `unmangle.c` (the
  unmangler TDUMP runs) because there is no Delphi compiler on the platforms this is
  developed on. Spelling is what that unmangler prints. Microsoft's `@name@N` 32-bit
  `__fastcall` C decoration is refused. Checked against the unmangler's own test vectors
  and a recorded dump of the real `tdump.exe -q -um` over the export tables of real BPLs
  and C++Builder DLLs, 11,363 of 11,363, with the whole symbol consumed. That dump is
  checked in and replayed, so the number is a test result rather than a claim.

- ~~**Objective-C**~~ — *landed*. It turned out to be four families across three
  runtimes rather than one form, and the interesting part is that the rules belong to the
  compiler rather than the language: they are transcribed from clang's `Mangle.cpp`,
  `CGObjCMac.cpp` and `CGObjCGNU.cpp`, and checked against what clang emitted for
  declarations this package wrote. The GNU-family method mangling is not injective and
  clang's own source says so; the readings are enumerated rather than guessed at.
- ~~**Swift's symbolic references**~~ — *landed*, with the API that takes the binary too.
  `swift.demangle_symbolic` reads a name as bytes and takes a resolver;
  `resolve.ContextResolver` is one, over an `Image` that `elf_image` or `macho_image`
  builds from a file. The reference cannot be resolved from a name alone and so is
  refused without a resolver, exactly as the reference demangler refuses it. Checked
  against the linker over the whole Swift 5.10.1 runtime, and against `swift-demangle`
  after splicing each resolved fragment back in.

## 2. Performance

Two of the three items originally listed here were measured and settled; what remains is
below. The measurements are recorded because a rejected idea is only useful if the reason
survives.

- **Batch detection over a whole table** — *not worth doing*. Detection is 1.45% of total
  demangling time over the 14,318-name corpus (10.7ms of 737ms). Batching could recover
  some fraction of that fraction, in exchange for an API that has to be kept in step with
  the per-name one.
- **Interning repeated components** — *done for leaves, rejected for composites*. Leaves
  are 55% of all nodes and repeat 49 times over; keying them by text costs one string
  hash and gives 40% less memory and about 6% less time. Interning composites collapses
  the tree further still, 4.2MB to 2.4MB, but a composite hashes by walking its children
  and building that table costs seven times the whole parse.
- **A benchmark corpus that is not a microbenchmark** — *done*, and the original
  diagnosis was wrong. The old 887-name corpus was not too small for cache: holding
  composition constant, per-name cost is flat from 500 names to 5,913. It was
  unrepresentative in *composition*, being mostly cheap MSVC names, which flattered the
  headline figure by 3x. The benchmark now spans every corpus and all four schemes.
- **Rust's remaining cost** — *done*, and what read as an obstacle was the answer.
  "Spread across `eat`, `ident` and `integer_62` rather than concentrated anywhere"
  means the cost *is* the interpreter frames: those are per-character helpers, and they
  run 1.5 million times over the 5,710-name corpus. Writing them out at the hot call
  sites, and dropping the wrapper that reached each `skip_*` body through a second frame,
  took 80us a name to 54us without touching the grammar. Verified byte-identical over
  60,000 mutated names.

- **A profile-guided pass over the Itanium parser** — *done*, and the profile said what
  the Rust one had: a name costs 400 interpreter frames and about half the time is the
  frames rather than the work inside them. Writing out the recursion guard, asking one
  lookahead instead of five in `template_arg`, and ending the `E`-terminated loops on a
  single `peek` took 18.5% off. Two findings were outside the parser: `decorated` and
  `Node.spell` each ran an `import` statement per call, and detection was 18.5% of an
  Itanium name rather than the 1.45% recorded above — that figure was measured over a
  corpus dominated by cheap MSVC names, and before three more schemes were registered.
- **Interning builtin spellings** — *rejected*. `builtin` is called 14,765 times over the
  Itanium corpus and holds about thirty distinct texts, so a cache would remove almost
  every allocation. It measured under 1%, and it would make `SpellingBuilder` — shared by
  every parse and documented as stateless — carry state, with a bound needed against
  `_Float<n>`, whose spelling the input chooses.
- **Inlining the detection call into `demangle`** — *rejected*. Roughly 1.5% of the cost
  is the `_claims` frame itself. Recovering it means duplicating the
  symbol-table-decoration fallback at the call site, and that rule is one that has to
  stay in one place.

  What *was* recoverable there, and is now taken, is the question `_claims` asked over
  again: `split_decorations` depends on nothing but the name, and every candidate scheme
  was asking it separately. Asked once per name instead — with the answer for a name
  carrying no ELF version suffix being that no plugin need ask again — that is 3.4
  interpreter frames per name down to one over a table of ordinary C identifiers, and
  10.9% off the detection path with `get_style`'s own fast path written out beside it.

  The figure quoted here was 10.4%, and re-measuring it against libstdc++ symbols on a
  later build gave 24% — 57.9µs a name detected against 43.7µs with `language="itanium"`
  forced. The number moves with the corpus and with how many schemes are registered, so
  it is recorded as a range rather than a constant: **detection is 10–25% of an Itanium
  name**, and it is the largest single item left. What would actually recover it is a
  wider screen than one character — eight of the ten schemes are offered every `_`, and
  `go`, `nim` and `pascal` are offered every symbol whatever it starts with.

- **A second profile-guided pass over the Itanium parser** — *done*, **7.4%** off the
  project's own Itanium corpus and **10.2%** off 217,730 real symbols, with byte-identical
  output. The profile said the same thing a third time, and the answer was to stop paying
  for frames and comparisons that the corpus says are not needed.

  `Reader.peek()` takes no argument — it is called around fifty times a name, more than
  anything else in the package, and CPython charges for a default it then has to bind;
  worth 3.3% alone, with the offset form moved to `ahead(n)`. `Reader.length_prefixed()`
  reads a `<source-name>` in one frame instead of three. `_type`'s dispatch chain and
  `prefix_component`'s are ordered by how often each arm is taken over those 217,730
  symbols rather than by the grammar: a nested name is a third of every type read and a
  substitution a fifth, and both had to fall through fifteen comparisons to reach the
  bottom. `cv_qualifiers` asks one lookahead before consuming anything. The bounds every
  production checks are read from `limits` once.

  Rust's v0 reader got the same treatment where the same shape was visible — `I` and `N`
  are 58% of its types and both were found by falling off the end of a chain — for
  between 1% and 5%, which is not cleanly separable from this machine's own spread.

  Measured throughout by alternating two worktrees and taking the median of nine timed
  rounds per process; identical trees measured that way come out at 0.993 and 1.005,
  where a single run of the committed benchmark has a 24% spread on its cold phase.

Nothing is left under this heading. The next thing worth measuring is `parse()`, which
has had the same attention only once.

## 2a. What hostile input can buy

A mangled name is untrusted input, so "how much work can one symbol cause" is a question
with a number rather than a posture. Measured by growing a repeated unit until it reaches
the input bound, for every scheme, recording wall time and peak allocation:

- The worst shape in the package is a Swift name of 64KB, the largest `max_input` allows:
  **586ms and 16MB**, and linear in the input from 5KB up. Every other scheme's worst is
  under that. Nothing is superlinear.
- It was not always. `_ZN` and 8,190 components of `1a` — 16KB — read in **a second and
  98MB**, because every `<prefix>` is a substitution candidate and each entry holds the
  whole prefix, so N components record O(N²) characters and no single one crosses
  `max_output`. Bounded now at sixteen times the output bound, which is a hundred times
  what the largest real name of the 217,730 shipped Itanium symbols records.
- A Swift name ending in the middle of a specialisation looped without advancing and ran
  the process out of memory. `Demangler.next_char` returned nothing past the end of input
  *without moving*, so `push_back` un-consumed a character that had really been read.
  Fixed in `next_char`, so the two are inverses everywhere rather than at the sites
  someone happened to check.

Both were found by fuzzing rather than by reading, which is the point of the campaign
recorded here: roughly 550,000 corpus mutations across every scheme, 380,000
grammar-generated Itanium names, 120,000 grammar-generated Swift names and 45,000 MSVC
mutations, checking on each one that nothing but a `DemanglingError` escapes, that the
tree renders exactly what the text path spelled in both styles, that the result is
encodable, and that no substitution-table sentinel reaches a builder. The mutation runs
are under an address-space cap, so a runaway allocation reports the name that caused it
rather than being killed.

Five defects came out of it, all fixed and all with a named regression test: the two
above, and three places where the tree spelled a name differently from `demangle()` — a
pack holding an empty pack, a declarator over an empty pack, and a Free Pascal program's
lead. That last class now has a test of its own over every corpus at once, in
`tests/test_architecture.py`, rather than each scheme over its own.

## 3. Output modes

A caller does not always want the whole spelling. `signature()` answers with the parts —
namespace, base name, parameter types, return type, and what the name does *not* say —
and the CLI's `-p`, `--base-name` and `--no-return-type` print one of them. Those are
render-time selections over what the parse already found; a mode is a different question,
which is whether to spell something *differently*.

- ~~**Bare type encodings**~~ — *landed*. `demangle_type()` and `parse_type()` read a
  `<type>` on its own — `Pi`, an MSVC `PEAX`, a Swift `SaySiG` — which is what a
  `typeinfo` name, an RTTI type descriptor and a Swift metadata typeref carry. `language`
  is required and cannot be made optional: a whole symbol announces its scheme with `_Z`,
  `?` or `$s`, and a type encoding announces nothing at all, so `Si` is `std::istream` to
  the Itanium reader and `Swift.Int` to the Swift one and there is no evidence that
  decides between them. The reference tools put it behind a flag for the same reason —
  `c++filt -t` and `llvm-cxxfilt --types`, libiberty's `DMGL_TYPES`,
  `UnDecorateSymbolName`'s `UNDNAME_TYPE_ONLY`, Swift's `demangleTypeAsString`. Scored
  over 1,076 encodings against *both* references, one corpus each because they spell the
  same types differently: **1,076 of 1,076** against `llvm-cxxfilt --types` and **1,076
  of 1,076** against `c++filt -t`. The last three to close were a doubled `KK`
  cv-qualifier that GNU folds away and LLVM keeps.

- ~~**ARM64EC hybrid names**~~ — *landed*, **606 of 606**. A function built for the
  hybrid ABI carries `$$h` after its qualified name, and nothing reads it: `llvm-undname`
  18.1.3 refuses `?func@@$$hYAXXZ` and current upstream has no `$$h` in
  `MicrosoftDemangle.cpp` either. So there is no reference *spelling* — but there is a
  normative *rule*, `getArm64ECDemangledFunctionName` in LLVM's `Mangler.cpp`, which is
  what the compiler emits an `EXPORTAS` directive against and so answers with the name
  the linker resolves. This reads that name, which makes the check exact without a
  reference binary: the marker inserted where LLVM's mangler puts it must not change what
  the name says.

  The `#name` form — the same marker for a symbol that is not a C++ name — is recognised
  and deliberately not read: it would mean claiming every string opening with a `#` to
  strip one character, and this is offered every symbol in a binary where LLVM applies
  its rule only to objects already known to be ARM64EC.

- ~~**MSVC RTTI type descriptor names**~~ — *landed*. A `type_info` points at a string,
  and the linker spells it as a `.` and a bare type encoding: `.PEAX`, `.?AVFoo@@`. Not a
  decorated name — no `?`, nothing declared — so every one of them was refused and a PE
  symbol dump full of them said nothing. **106 of 106** against `llvm-undname`, counting
  the descriptor *objects* (`??_R0<type>@8`) beside the names.

  Claiming a leading `.` in a table full of `.text`, `.L1234` and `.constprop.0` is the
  risk, and it is answered by measurement: what follows the dot must parse as a *whole*
  type, and none of the 731 dot-prefixed names in the checked-in corpora nor any of 51
  section and label names is claimed. Doing it also found the marker was in the wrong
  place in the descriptor object this already read: it goes where a *declarator* goes, so
  a pointer to an array of two is `int (*`RTTI Type Descriptor')[2]`.

- ~~**The stream filter and the tree as library API**~~ — *landed*. `demangle_text()`,
  `demangle_stream()` and `find_symbols()` are the word-scanning filter the command has
  always run, now callable; `Node.to_dict()`, `demangle --json`, `__match_args__` and
  `node_kinds()` make the tree data rather than something to read our source for. The
  serialisation shares repeated nodes, because the structure is a graph and expanding it
  in full does not always terminate in useful time.

- ~~**A per-call options object**~~ — *landed*. `style()` composes a style from a named
  one and per-language changes, at the call site:
  `demangle(name, style=demangle.style("llvm", msvc={"calling_convention": False}))`. A
  composed style is an object rather than a registered name, and `demangle()` does not
  cache a call that passes one, so one caller's narrower spelling cannot be served to
  another. The MSVC scheme grew the five options `llvm-undname` has —
  `calling_convention`, `access_specifier`, `member_type`, `return_type`,
  `variable_type` — scored at **1,250 of 1,253** against the reference over the
  differences those flags make on LLVM's own 609-name corpus. The three are names where
  the reference's own `--no-return-type` leaves an unclosed bracket.

  What the flags apply to is worth writing down, because each looks like a bug until you
  watch the reference do it: a function reached as a *pointer's* pointee keeps its
  calling convention, because the pointer prints it rather than the signature; a symbol
  naming a *scope* keeps its full spelling; and `extern "C" ` goes with `static` and
  `virtual` rather than with the access specifier.

  Still to come from the same list: the Itanium and Rust knobs — `--strip-underscore`,
  `DMGL_RET_POSTFIX`, Rust hash retention — and Swift's simplified bundle below.

- ~~**Swift's simplified manglings**~~ — *landed*, **217 of 217** against
  `test/Demangle/Inputs/simplified-manglings.txt`. What `swift-demangle --simplified`
  prints and what an IDE shows in a stack trace: `Either` for `Monads.Either`, `(_:)` for
  `(Swift.Int) -> Swift.UInt`, `specialized f()` for a page of specialisation arguments.
  Built where the reference builds it — as options the printer consults, not as a pass
  over the text, because dropping a module qualification needs to know which run of
  characters *was* the module and after printing nothing does.

  `SwiftOptions` carries the twelve flags the bundle actually changes here; the rest of
  upstream's fourteen either match this printer already or change nothing over the 217,
  and a flag no vector exercises is a flag with no reference behind it. Reached as
  `demangle --simplified`, or `style("llvm", swift=SIMPLIFIED_OPTIONS)`.
