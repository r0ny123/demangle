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

**Itanium**, libcxxabi's `DemangleTestCases.inc`: 29,913 of 29,928 exact. Fifteen left,
in four groups, and none of them is a name read wrongly: four are bare types refused on
purpose, one is a self-referential conversion operator refused because it has no
declaration, nine record llvm-cxxfilt's own reading of a recorded `<template-param>`
(which heading 0 below settles against four compilers' output), and one is numbered by
GCC 12's closure-prefix rule rather than the ABI's. The number went *down* from 29,923
for those reasons, and `tests/test_conformance.py` says so where it pins it.

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
- **One is a self-referential conversion operator**, `_Zcv1BIRT_EIS1_E`, whose type is
  the argument list that contains it. The reference guards against printing a cycle by
  printing *nothing* the second time round, so it answers `operator B<><>`; this used to
  read the type again once the arguments were bound and answer `operator B<auto&><auto&>`.
  Neither is the declaration, because there is no declaration — no compiler emits this,
  and it comes from LLVM's fuzz corpus. It is refused now, which is what `c++filt` 2.42
  does with it, and it is refused by the general rule rather than by a guard: a
  `<template-param>` reads as `auto` only where nothing is bound on purpose, and the
  `T_` here is in neither such reading.
- **One is numbered by GCC 12**, `_ZNK1xMUlTyT_E_clIiEEDaS_`, a lambda in the
  initializer of a variable `x`. GCC 12 and earlier left the closure prefix out of the
  substitution table, so the vector's `S_` is the lambda's own invented parameter; by the
  ABI's numbering, which GCC 13 and Clang both emit, it is `x`. `tests/test_conformance.py`
  pins both readings and says which compiler writes which.

**D**, libiberty's `d-demangle-expected`: 366 of 366. What the last of them needed was
not in the D ABI at all -- the five characters the reference names inside a string, the
different rule for a character *literal*, hex float and complex values, associative-array
values written as pairs where the type says so (through a back reference, if that is how
it was written), struct and function-literal values, `extern(Pascal)`, the anonymous and
`__S<n>` path components it leaves out, and the malformed template instances it refuses
outright. Each was derived by running the reference over the input space rather than read
from a specification that does not describe it.

**Swift**, `test/Demangle/Inputs/manglings.txt`: 514 of 514, with no name answered by a
different spelling at any point along the way. The last eight were features Swift added
after this scheme was written -- function-signature specialisation kinds (an escaping
closure, a closure the same as an earlier argument, propagated structs, and `p` becoming
a run rather than one constant), arguments the optimiser dropped, `Tfr` representation
changed, the `$e` Embedded Swift prefix, macro expansion source locations, pack protocol
conformances and an opaque result type's conformance. Each was transcribed from
swiftlang/swift's own `Demangler.cpp` and `NodePrinter.cpp` rather than fitted to the
vectors, which is the only way the no-wrong-spellings property survives.
`simplified-manglings.txt` is a whole output mode this does not have; see section 3.
The reference is `tools/swift-demangle-reference/`, which builds swiftlang/swift's own
`lib/Demangling` at a pinned revision -- nothing a distribution ships reads a Swift name
at all -- and the one place following it would be the defect is
`tests/conformance/swift-reference-defects.txt`.

The vectors are not the whole grammar, and having a reference is what makes the rest
reachable. Diffing the node kinds this scheme builds against the compiler's own
`DemangleNodes.def` named nine manglings with no production here at all -- the identity
thunk, the three outlined enum-payload operations, inline-array sugar, a value generic
parameter, the two key path method thunk helpers, and the body and preamble macro roles
-- none of which any corpus carries. Each was then put to the reference rather than
guessed at. `ALL_KINDS` now mirrors `DemangleNodes.def` exactly and a test walks every
tree the corpus produces and fails on a kind missing from it, so the same drift cannot
happen again in silence.

**Rust**, rustc-demangle's own `#[test]` vectors: 47 of 51. Two of the other four are
not differences from the *tool*: `rustfilt` prints `foo@@16` and echoes `ZN4testE` back
unread, exactly as this does, and the vectors record the library's own `Display` instead.
The remaining two are detection rather than spelling — `_ZN3foo5h05afE` carries a hash
that is not rustc's `17h` and sixteen hex digits, so this reads it as the C++ `foo::h05af`
it could equally be. rustc-demangle can afford the wider rule because it is only ever
handed names a caller has already decided are Rust's; this plugin is offered every symbol
in a binary.

Against the crate rather than its vectors: `tools/rustc-demangle-reference/` is a
twenty-line front end over `rustc-demangle` 0.1.28 itself, and over the 5,753 distinct
Rust symbols in the corpora this spells 5,752 identically. The one is the `@@16` above.
Neither of the demanglers a Linux box already has is that implementation -- LLVM's Rust
reader is a port of an older version and binutils' is independent of both -- so it is
built here rather than assumed, and `tools/enumerate.py` and `tools/mutate.py` ask it
where it exists.

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

**Nim**: eight shortfalls where the language's own mangling discards an underscore before
a digit (`len0_16`, `len17_32`, `len33_64` in `pure/hashes.nim`, and five MySQL wrapper
routines with C-linkage imports), listed and explained in
`tests/conformance/nim-lossy.txt` and pinned in `tests/test_nim.py`.

## 0. Where a reference is wrong

Agreeing with a reference is not the same as being right, and this is the heading for the
places where that has been established rather than assumed. The vectors live in the
`*-reference-defects.txt` corpora — the ones whose expected column comes from the
declaration rather than from a demangler — with reduced sources in
`tools/corpus_sources/reference_defects/` and `tools/corpus_sources/msvc/`, and the
compiler named on each entry.

- ~~**A qualifier in front of an MSVC deduced return type, dropped by
  `llvm-undname`**~~ — *settled*, from the source rather than argued about. A deduced
  return type is written `?` and a name — `?A?<auto>@@` — and takes a qualifier like any
  other type, so `const auto structured_const()` is `?B?<auto>@@`.
  `CustomTypeNode::outputPre` in LLVM's `MSNodes.cpp` is `Identifier->output(OB, Flags);`
  and nothing else, where every other type node's `outputPre` writes its qualifiers
  first — so `?A`, `?B`, `?C` and `?D` in front of a custom type all come back spelled
  the same, and the `const` is gone. Not a refusal but a wrong reading, which is the
  worse kind: `<auto> __cdecl hard::structured_const(void)` is the declaration of a
  different function. Compiler-emitted, so `tests/conformance/msvc-reference-defects.txt`
  pins it against the source in `tools/corpus_sources/msvc/modern.cpp`, and `ACCEPTED` in
  `tools/enumerate.py` carries the reason the reference's answer is not evidence when the
  fuzzers reach the same shape.

- ~~**The extension qualifiers on the pointee of an MSVC pointer to member, dropped by
  `llvm-undname`**~~ — *settled*, the same way. `__restrict` on a pointer and
  `__unaligned` on what it points at are ordinary declarator syntax for this target, and
  the mangling writes both in the pointee's own letters: `PEQExt@1@PEIFAH` is
  `int __unaligned *__restrict ns::Ext::*`. The reference prints both words for that same
  `PEIFAH` standing on its own and neither when it is a member pointer's pointee, so two
  declarations come back from it as one spelling — `int *ns::Ext::*`, which is a third
  type that is neither. Found by `tools/mutate.py --seed 42` wearing an ARM64EC marker
  and then written in C++ to see whether a compiler reaches it, which it does without
  being asked for anything unusual: `extended_member`, `takes_extended_member` and the
  agreeing `extended_plain` in `tools/corpus_sources/msvc/msvc.cpp` are pinned against
  their declarations in `tests/conformance/msvc-reference-defects.txt`, and `ACCEPTED`
  in `tools/enumerate.py` explains the divergence for the fuzzers.

- ~~**A `<template-template-param>` is a substitution candidate, and `llvm-cxxfilt`
  does not record it**~~ — *settled*, from two compilers' output. `T_ I ... E` is two
  grammar components: the parameter, which 5.1.10 names as a candidate in its own right,
  and the specialisation built over it, which is a `<type>`. So it contributes two
  entries, and this library has recorded both since the change that reads
  `...T_IT0_Li3EES5_` — a name g++ 13.3 and clang++ 18.1.3 both emit, whose `S5_` is
  reachable only if `T_` took an index of its own.

  `llvm-cxxfilt` 18 records the second alone, so every index at or after the parameter's
  is one out. For `template <template <class> class C, class T> void f(C<T>, C<T>, C<T>)`
  the shift runs off the end and it refuses `_Z1fI1AiEvT_IT0_ES3_S3_` outright; for a
  `g(C<T>, C<int>)` beside it the shift lands one short and it answers
  `void g<A, char>(A<char>, char<int>)`, and `char<int>` is not a type. `c++filt` 2.42
  agrees with the declarations, and so does this. Pinned against the source in
  `tools/corpus_sources/reference_defects/template_template_param.cpp`. Reached by
  `tools/mutate.py --seed 43` through a damaged `std::pair` constructor, where `c++filt`
  refuses the mutant for its own reasons and so is not there to be the second opinion —
  which is the only case `ACCEPTED` has to carry, since where that reference reads such a
  name the rule for "both agree with this library" has already taken it.

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

  ```cpp
  template <class T> void run(T &a) { take([](auto &x) { return x; }); }
  ```

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

  A sixth was found after that count, by `tools/mutate.py --seed 56`, and it is not
  reached by any shipped symbol: the whole of `<template-param-decl>`, which is what a
  generic lambda writes for each parameter it declares. GNU puts the name *after* the
  type rather than where a declarator goes — `int (*) [3] $N0`, which is not a
  declaration anyone can write — puts a `Tp`'s ellipsis on the type rather than on the
  name, spells a `Tt` `class` and writes that `Tt`'s own declarations without their
  names, and numbers every *printed* declaration in one sequence across kinds where
  llvm-cxxfilt counts each kind separately. Read off `c++filt` 2.42 over every ordering
  of the three kinds, every declarator shape, and two levels of `Tt` nesting — 242 names
  agreeing byte for byte. `tests/test_gnu_expressions.py`.

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

- **`c++filt --format=gnat` aborts on an eight-character name** — *open*, and the one
  entry here that is a crash rather than a wrong reading. Binutils 2.42:

  ```
  $ printf 'aSO__bDF\n' | c++filt --format=gnat
  *** buffer overflow detected ***: terminated
  Aborted
  ```

  All three parts are needed — an `SO` attribute marker, a `__` separator and a `DF`
  suffix — and dropping any one of them returns normally, so it is the two expansions in
  one name that overrun. Only the GNAT format reaches it; `c++filt` left to detect the
  scheme does not. This library reads the shape as `a'Output.b.Finalize`, which is why a
  mutant of a GNAT runtime symbol went to the reference through the gate and took the
  whole Ada run of `tools/mutate.py --seed 37` down with it. `ask_tolerantly` now splits
  a batch to the name that did it and leaves that one out of the comparison, and
  `tests/test_ada.py` pins the reading. Worth reporting upstream: `c++filt` is what `nm`,
  `objdump` and `addr2line` use, and the symbols in a binary are not always friendly.

- ~~**A `<template-param>` bound to a pack, resolved to one member and not the same
  one**~~ — *settled*. `tools/mutate.py --seed 39` reads

  ```
  _ZSt12construct_atIcJRbcEEDTgsnwcvPvLi0E_T_pispcl7declvalIT0_EEEEPS3_DpOS4_
  ```

  a mutant of a libstdc++ symbol whose `S3_` is the entry `T0_` contributed, and `T0_` is
  bound to the pack `J Rb c E`. Probing the table with `tools/probe_substitutions.py`
  gives three answers for that one entry: the pack here, `bool&` to `llvm-cxxfilt`, and
  `char` to `c++filt`. The references disagree with each other, which is the same defect
  section 0 already records — the entry is the parameter, not an argument bound to it —
  reached through a pack rather than through a second template scope.

  It was left unexplained because the evidence that would narrow a rule — that the two
  references disagree *with each other* — cannot be read off their answers, which differ
  by output style whether they disagree or not. What settles it is asking a name small
  enough that the answers cannot hide anything. `_Z1fIiJbcdEEvT_DpT0_` has two entries
  for its pack: entry one is what `T0_` contributed and entry two is the `Dp` expansion's
  own. All three demanglers agree entry two is the whole pack. On entry one,
  `llvm-cxxfilt` says `bool` — the *first* member — and `c++filt` says `double` — the
  *last* — while this says the pack.

  So the two references each record one member and not the same one, which is what says
  neither has a rule here; and the rule this library does have is the one heading 0
  already establishes against four compilers' output for the unpacked case, that the
  entry is the parameter rather than the argument bound to it. A pack parameter is not a
  different kind of parameter.

  `TestABackReferenceToAPackBoundParameter` in `tests/test_types.py` pins all four
  entries, and `ACCEPTED` in `tools/enumerate.py` accepts the shape — a back-reference
  that landed on a pack-bound parameter's entry, asked of the parser, which is the only
  thing that knows which entry an `S<n>_` landed on — rather than either answer. It is
  not one stray mutant: seeds 39, 42, 43 and 48 all reach it, every one a damaged
  `std::construct_at` whose `S3_` or `S4_` is the entry `T0_` contributed.

- ~~**A pre-Itanium template whose second argument is a value**~~ — *fixed*.
  `__opi__t2TA2Z5__pt__8_PFcPv_i` from `tools/mutate.py --seed 54` was
  `TA<__pt_, 8>::operator int(int (*)(char, void *))` to the reference and
  `_PFcPv_i::operator int(void)` here: the value argument was not read, and what was left
  of the name was resynchronised as a class name. The smaller
  `__opi__t2TA2Z5__pt__1_i` showed it plainly — `_::operator int(int)`, naming a class
  called `_`, which is the kind of answer this package treats as worse than none.

  The production turned out to be two lines of the reference rather than a grammar to
  add. `demangle_fund_type` in `cplus-dem.c` ends its switch on `'\0'` and `'_'` with a
  bare `break`: an empty fundamental type, successful, integral, and consuming nothing,
  so `demangle_template_value_parm` reads the `_8_` after it with
  `consume_count_with_underscores`. This library already had that case and refused it
  deliberately — it is one of the three shapes that made the scheme claim ordinary C
  symbols, and `drm_intel_gem_bo_map__cpu` is a C function rather than a call taking a
  `__restrict *`. The refusal is now lifted in exactly one place, the type in front of a
  template *value* argument, which no C name can reach because the whole shape sits
  inside a `t <count> <name> <count>` production. All 5,112 template names in
  `tests/conformance/gnuv2-*.txt` are unchanged, the detection numbers are unchanged,
  and `TestATemplateValueArgumentWithNoTypeInFrontOfIt` in `tests/test_gnuv2.py` pins
  both sides of it.

- ~~**`$$C` over a `__restrict` pointer puts the two qualifier words in either order**~~
  — *settled, and it was cosmetic*. `?r1@Q@ns@@QEBAAEAY03$$CBPIAD@Z` from
  `tools/mutate.py --seed 42` is `char *const __restrict` here and
  `char *__restrict const` to `llvm-undname`: the same declaration, since the order of
  `const` and `__restrict` after a `*` is free, and the two agree on every shape a
  compiler writes — `?x@@3QIADA` is `char *const __restrict x` to both. They part only
  where an outer `$$CB` is applied over a pointer that already carries `I`, which is a
  shape section 0 elsewhere records no compiler as writing. Nothing to fix, so what
  closes it is saying so where the fuzzers ask: `ACCEPTED` in `tools/enumerate.py` now
  sorts the words in each run directly after a `*` before comparing, which keeps the
  multiset — a word one side drops or adds still differs and is still reported — and
  `TestDollarCOverARestrictPointer` in `tests/test_msvc.py` pins the reading along with
  the compiler-written neighbour the two agree on.

- ~~**A constrained `decltype(auto)` is a type, and so is a substitution candidate**~~ —
  *settled*, and `tools/mutate.py --seed 40` is clean. `DK <type-constraint>` is a
  `<type>`, which ABI 5.1.10 makes a candidate, so this records the composite;
  `llvm-cxxfilt` 18.1.3 records nothing for it. Probing `_Z1fDKN1A1BE` with
  `tools/probe_substitutions.py` shows the whole disagreement: `S_` is `A` to both, and
  `S0_` is `A::B decltype(auto)` here and out of range there. One entry's difference is
  enough to move every later back-reference, which is how a mutant of

  ```
  _ZNK5clang6driver5tools7openbsd4Link12ConstructJobE...DKNS0_9InputInfoE...S9_...
  ```

  comes back with `llvm::SmallVector<llvm, 4u>` from the reference and
  `llvm::SmallVector<clang::driver::InputInfo decltype(auto), 4u>` here.

  What settles it is that the omission is those two codes and not a rule that reference
  holds about compound types. Probed the same way, it records the composite for `Dv2_i`
  and for `Dpi`. And it is the omission its `DB` had as well: libcxxabi's own corpus
  carries `_Z6myfuncRDB8_S0_` as `myfunc(_BitInt(8)&, _BitInt(8)&)`, which needs
  `DB8_` in the table and which the shipped `llvm-cxxfilt` 18 refuses — so upstream
  added that entry after the production, which is the reading this always was: support
  landed without the table entry it implies. Neither g++ 13.3 nor clang++ 18.1.3 emits
  `Dk` or `DK` at all — both write `Tk` in the `<template-param-decl>` instead — so the
  grammar is the whole of the evidence and there is no compiler output to weigh against
  it. Pinned by `TestAConstrainedPlaceholderIsASubstitutionCandidate` in
  `tests/test_types.py`, and `ACCEPTED` in `tools/enumerate.py` now carries the reason,
  read off the parser's own `_constrained_placeholder_recorded` rather than off the two
  characters, which a `<source-name>` may also hold.

## 1. More schemes

*Nothing here is outstanding.* Kept as a record of what each scheme is measured against,
because that differs per scheme and is the part worth knowing before trusting a number.

The plugin interface exists so a scheme needs no core changes. Go landed that way,
without touching `core` at all, and so did the thirteen that followed -- fourteen schemes
in total, the last three of them (pre-Itanium C++, CodeWarrior, Ada/GNAT) in this branch.

D, Swift, Nim, Free Pascal, Objective-C and Delphi have all landed the same way. What
each is measured against differs, and the difference is the interesting part:

- ~~**D**~~ — 100% against GNU binutils' `c++filt --format=dlang`.
- ~~**Swift**~~ — exact against `swift-demangle` built from swiftlang/swift's own sources,
  over the whole shipped runtime and the compiler's own test corpus, in both the current
  mangling and Swift 3's. Nothing a distribution ships reads a Swift name, so the
  reference is built here: `tools/swift-demangle-reference/`.
- ~~**Nim**~~ — no reference demangler exists, so the property is that re-mangling what is
  read reproduces the symbol, plus agreement with the name the compiler recorded in its
  own `.ndi` files.
- ~~**Free Pascal**~~ — no reference demangler either; the property is re-assembly, over all
  236,570 readable symbols in the shipped runtime, plus a check against `ppudump`.
- ~~**Delphi / C++Builder**~~ — no Delphi compiler here, so the grammar is Embarcadero's
  `unmangle.c` and the spelling is what TDUMP prints. A different scheme from Free
  Pascal's.

- ~~**JNI**~~ — *landed*. `Java_com_example_Foo_bar__Ljava_lang_String_2` is the C function
  a `native` method is called through, and Android ships them by the thousand; the usual
  way to read one is by eye, because neither binutils nor LLVM reads them and neither
  does Ghidra or IDA. It is also the one scheme here whose encoding is *written down
  normatively* -- the JNI specification's "Resolving Native Method Names" -- rather than
  having to be transcribed from a reference implementation. What it cannot recover is
  the package/class boundary, which the encoding genuinely does not carry, so the
  spelling puts the whole path in one run rather than inventing a structure.

- ~~**Pre-Itanium C++: the GNU v2 / cfront / ARM family**~~ — *landed*. `__ls__7ostreamPCc`,
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
  of `DMGL_PARAMS` — **1324 of 1324**. That tree is now also *built* here, as
  `tools/cplus-dem-reference/`, so the fuzzers have an oracle for this scheme too: it
  reproduces the corpus exactly, and over 420,000 mutants this library never reads a name
  it refuses.

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

- ~~**Metrowerks CodeWarrior**~~ — *landed*, beside the four above. The other pre-Itanium
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

  A search for real-world CodeWarrior-built PowerPC ELF or PEF binaries with embedded
  symbol tables found none to be had: commercial GameCube/Wii titles stripped their
  symbol tables completely (leaving bare `.dol` executables, whose symbols decompilation
  projects recover into external map files), while Classic Mac OS PEF binaries stored
  debugging symbols in external `.xSYM` sidecars rather than in-binary symbol tables.
  The vectors transcribed from `encounter/cwdemangle` remain the authoritative test set.

- ~~**Ada/GNAT**~~ — *landed*, **34 of 34** against the cases `demangle-expected` marks
  `--format=gnat`, and **1,438 of 1,438** real-world GNAT runtime symbols from
  `libgnat`/`libgnarl` extracted via `tools/generate_ada_corpus.py` and scored against
  `c++filt --format=gnat`. The last of the pre-Itanium formats libiberty still carries:
  when the GNU v2, lucid, ARM and HP styles were dropped from the default, `--format=gnat`
  stayed. The reference is `ada_demangle` in `cplus-dem.c`, with GCC's own `exp_dbug.ads`
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
  Block invocations (`___[length]-[Class method]_block_invoke`) follow Clang's
  `mangleFunctionBlock`; well-formed block invocations are demangled, and mismatched
  length prefixes are strictly refused.
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

- **Rust's second reading of the same path** — *done*, **17%** off the Rust corpora and
  **9.3%** off the whole cold benchmark's Python-level call count, with byte-identical
  output. `_run` opened with a `skip_path` over the symbol path so that the residual
  after it could be checked before anything was written, and then reset the cursor and
  read the same path again to print it. That is the whole path twice: 4,972 of the
  49,597 `skip_path` calls the checked-in Rust corpora cost, and every recursion under
  them. Printing *is* the pass that finds where the path ends, so the skip is gone; the
  `<instantiating-crate>` after it is still skipped, because that one is not printed.

  The one thing the old ordering bought was which refusal a name gets when it is
  malformed *and* too long for the caller's `max_output` -- the residual check came
  first, so the answer was "not this scheme" rather than "you set a bound". That is
  restored on the one path where the two differ rather than given up, and it costs
  nothing on the path that answers.

  The legacy scheme's escape loop went with it: it asked `startswith` three times and
  then scanned the whole remainder for a `$` on every iteration -- 131,691 calls over
  these corpora, forty-three per name -- and copied the rest of a component with
  `rest[1:].find(...)` to look one character ahead. Dispatching on `rest[0]` and finding
  from an offset does the same work in one comparison.

  Verified rather than assumed: every checked-in corpus still exact, 46,088 damaged Rust
  names read identically under both styles and through the tree, and 40,000 components
  built out of escape fragments -- refusals included, since a refusal is an answer here.

Nothing is left under this heading. `parse()` was the next thing worth measuring, and it
has now been measured rather than guessed at: against `demangle_strict()` over the same
names, cache cleared each round, median of nine.

| scheme | corpus | `parse()` against the text path |
| --- | --- | --- |
| MSVC | LLVM's own 609 | **0.87x** |
| Itanium | libcxxabi, 29,928 | **1.04x** |
| Swift | real-world, 8,000 | **1.23x** |
| Rust | every corpus name it reads, 5,752 | **1.35x**, was 1.61x |

Which says the headroom was not where the phrasing implied. Building the tree is *free*
for the two C-family schemes -- MSVC comes out ahead because the text path renders a
declaration the tree merely records -- and the multiplier lives in the schemes whose
nodes are parts in output order.

Rust was the outlier, and most of what made it one was not the tree at all. `Printer.node`
brackets every production, and under a tree sink it returned a `@contextlib.contextmanager`
generator: a generator object, a `_GeneratorContextManager` around it and two `next` calls,
five frames to reach an `open` and a `close`, 89,796 times over these corpora. The text
path had already been given a hand-written no-op scope for exactly this reason -- the
comment on `_NoScope` says it was 8% there -- and the tree path was left behind. It has
one too now, `_Scope`, which is the same two methods without the generator: **12.0% off**,
measured by alternating the two versions three times, medians of seven, 637.9ms against
561.4ms with no overlap between the sets.

What is left is the node objects themselves, and that is where this stops. `_Rust.__init__`
sums its parts to set `size`, and `size` is what `_check_length` enforces `max_output`
against -- a bound on untrusted input. It could be handed the figure by the sink, which
knows it in O(1), but a wrong `size` is a resource bound that silently does not hold. A
few percent is not worth that, and nothing measured here is on the benchmark's hot path
anyway, which times `demangle()`. A caller who wants the tree is asking for the tree.

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
- The claim above stopped being true for D, and the measurement did not catch it because
  it predates the code. Refusing a back reference that lands inside an identifier means
  asking, per reference, whether its target lies strictly inside any identifier read so
  far, and that was a scan of every span recorded. The spans are neither sorted nor
  disjoint — backtracking re-reads a region and a template instance's components nest —
  so the scan cannot stop early and grows with the name: over the D corpus, 0.03 span
  comparisons per character at 50 characters and 240 per character at 500. The longest
  real D symbol, 2,695 characters, spent 125,776 of them. The spans now carry a map of
  the positions they cover, so the question costs one lookup and the marking is linear in
  the identifier text: 1,601 marks on that same name, and 11.3ms down to 5.7ms. Re-measure
  this section's numbers when a scheme gains a check that consults everything read so far.

These were found by fuzzing rather than by reading, which is the point of the campaign
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

  Since extended by the four `UnDecorateSymbolName` mask bits `llvm-undname` has no flag
  for — `ms_keywords`, `leading_underscores`, `this_type` and `tag_kind` — scored at
  **1,064 of 1,064** against Microsoft's own `dbghelp.dll`, which meant doing the work on
  a Windows machine. They reach further than the five above, and that is the reference's
  doing rather than a convenience: `llvm-undname`'s flags stop at the edge of the symbol
  and Microsoft's reach every occurrence of what they name. Asking both references about
  the four flags they *share* turned up three disagreements about exactly that, which
  `UNDNAME_REACH_DIVERGENCES` records rather than resolves.

  The rest of that list has since landed too. `--strip-underscore` is `c++filt`'s and
  `llvm-cxxfilt`'s, which agree on every case including the sharp one: a name that does
  *not* read once stripped comes back as it arrived rather than a character short, so
  `_Z1fv` under the flag prints itself. It matters for fewer names here than it does
  there, because the Itanium, Swift and Rust readers already tolerate the extra
  underscore a Mach-O symbol carries; an MSVC name, which opens with `?`, does not.

  `--ret-postfix` is libiberty's `DMGL_RET_POSTFIX` — the return type after the
  parameter list, with no space, which is the spelling `java_demangle_v3` asks for. No
  shipped tool exposes it, so the reference was built: `cp-demangle.c` at the gcc-13 tag
  against a twenty-line `main`, which is what `--ret-drop` was checked against as well.
  Doing that found a defect in `--no-return-type`, which had been cutting the return type
  off the *front* of the spelling: a return type that wraps the declarator —
  `int (*g<int>(int))(int)` — has no prefix to cut, so the flag silently did nothing.
  MSVC had met the same shape and answered it with a scheme option; the Itanium tree now
  answers it directly. 342 of the 345 corpus names the reference reads match exactly
  under both flags, and the three that do not differ in a `std::` abbreviation under
  both alike.

  Rust hash retention is one option and not two, because that is how rustc-demangle has
  it: the same `alternate` bit that hides the legacy `17h<16 hex>` component also hides
  the v0 crate disambiguator and the type suffix on an integer const, so `{}` writes
  `features[9f05e0465351d495]::const_signed::<-17i32>` where `{:#}` writes
  `features::const_signed::<-17>`. `demangle --keep-hash` is the first of those, scored
  at **5,751 of 5,753** against the reference asked the same way; the two that differ do
  so in both modes and for reasons that have nothing to do with the hash.

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

## 4. Issue #28: Next Steps and Modern Compiler Sweeps

The seed ladder across existing corpora reached saturation (seeds 0 to 32 clean at 200k,
every corpus exact). Issue #28 mapped out the subsequent stretch of work:

- ~~**Probing schemes with no reference**~~ — *completed*:
  - ~~**Ada**~~ — *landed*: `tools/generate_ada_corpus.py` samples 1,438 real-world symbols from `libgnat`
    and `libgnarl`, 100% exact against GNU binutils' `c++filt --format=gnat`, seeding the
    fuzzers with real GNAT runtime names.
  - ~~**Nim**~~ — *settled*: The eight lossy names in the standard library where the compiler discards an
    underscore before a digit were pinned in `tests/conformance/nim-lossy.txt` and verified in
    `tests/test_nim.py`.
  - ~~**Objective-C**~~ — *settled*: Block invocation symbols (`___[length]-[Class method]_block_invoke`)
    were evaluated against Clang's `mangleFunctionBlock`: well-formed invocations are
    supported, and length mismatches are refused to prevent false claims.
  - ~~**CodeWarrior**~~ — *settled*: A search for CodeWarrior-built PowerPC ELF/PEF binaries with symbol tables
    confirmed none are available (shipping GameCube/Wii discs stripped symbols into `.dol`
    executables, and Classic Mac OS PEF binaries stored debug symbols in external `.xSYM`
    sidecars).
  - ~~**Free Pascal**~~ — *verified*: Probed against `ppudump -Va` across all runtime units (4,384 of 4,384
    symbols matching declared names).

- ~~**Defect investigations from the hunt**~~ — *completed*:
  - ~~**D back-reference landing inside an identifier's characters**~~ — *fixed*: The span of
    every length-prefixed identifier is recorded and back-reference targets landing strictly
    inside a span are refused.
  - ~~**`--refusals` at 200k**~~ — *completed*: Mutator refusals run across Itanium, MSVC, Swift, D, and
    GNUv2 at 200,000 mutants to triage cases where references accept corrupted inputs.
    Divergences were confirmed to be reference leniencies (ignoring invalid identifier
    characters, dropping trailing garbage, or accepting malformed template names).

- ~~**Reseeding the fuzzers**~~ — *completed*:
  - ~~**Swift / Go generic shapes and new node kinds**~~ — *done*: Swept against Swift 6.2+
    (the full `test/Demangle/Inputs/manglings.txt` at swiftlang/swift main, `async_Main`
    funclets and modern node kinds) and Go 1.25/1.26 (linker generated symbols, tagged struct
    escapes, and generic shape instantiations).
  - ~~**Itanium**~~ — *settled*: GCC 14+ / Clang 19+ constructs at `-std=c++26` with C++20
    modules (`W` module names, `DF` floats, friend declarations, structured bindings)
    audited and covered in conformance corpora and differential testing.
  - ~~**MSVC**~~ — *settled*: Modern MSVC toolset constructs (`$$Q`, lambda numbering,
    `__int128`) audited and pinned against `llvm-undname`.
  - ~~**Rust v0**~~ — *settled*: 5,753 Rust symbols exact against the `rustc-demangle` 0.1.28
    reference crate, and all Unicode scalar value ranges swept.
  - ~~**D**~~ — *settled*: Modern LDC/GDC frontend forms (`NkM`) and backref bounds audited and
    exact against `c++filt --format=dlang`.


