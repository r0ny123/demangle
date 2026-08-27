# Roadmap

What is not done yet, in the order it matters. Each entry says what would have to
change, so anyone can pick one up.

Conformance is measured against the reference projects' *own* corpora as well as the
ones checked in here. Every checked-in corpus is exact against its reference, and so are
whole symbol tables from libLLVM, libclang-cpp, the Rust toolchain, libstdc++, the Swift
runtime, libgphobos, the Free Pascal runtime, the shipped Objective-C runtime and every
symbolic reference in the Swift metadata — well over half a million real symbols. What
follows is what the upstream corpora still find, all of it pinned in both directions by
the test suite so it can only go up and cannot quietly stop being accurate.

**Itanium**, libcxxabi's `DemangleTestCases.inc`: 29,923 of 29,928 exact. Five left,
and four of them are not shortfalls.

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

**GNU style**, against `c++filt` 2.42 over the 44,049 C++ symbols in the shipped
libLLVM: 31 differ, and on every one of those this matches `llvm-cxxfilt` exactly — they
are names the two references spell differently from *each other*, a back-reference they
resolve to different entries and a `const` applied to a type that already carries one.
A further 97 `c++filt` refuses outright and this reads. Two names in the purpose-built
gnu corpus are pinned for the same reason.

**Nim**: seven shortfalls, that language's own mangling discarding an underscore, listed
by name in `tests/conformance/nim-lossy.txt`.

## 1. More schemes

The plugin interface exists so these need no core changes. Go landed this way, without
touching `core` at all.

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

Two schemes are worth weighing, and a survey of what the reverse-engineering tools
actually ship says so:

- **Pre-Itanium C++: the GNU v2 / cfront / ARM family**, and Metrowerks CodeWarrior's
  variant. `__ls__7ostreamPCc`, `BuildLight__9CGuiLightCFv`. binutils *deleted* these
  styles in 2019 and Ghidra ships a second, older copy of libiberty specifically to keep
  reading them, which is the loudest available signal that analysts still meet them —
  console and embedded decompilation lives on this. There is no normative document (the
  ARM is the origin), so it would be transcribed from
  `Decompollaborate/gnuv2_demangle` and `encounter/cwdemangle`. The largest single gap.
- **Ada/GNAT** — `pkg__proc$2`, still carried by libiberty as `--format=gnat` when the
  others were dropped, and documented normatively in GCC's own `exp_dbug.ads`. Narrow
  but concentrated: avionics, rail, defence.

Everything else surveyed is either not a mangling scheme at all (Zig, Erlang, Julia, V,
Odin and Kotlin/Native emit readable or unencoded names), already covered here
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

  The figure quoted here was 10.4%, and re-measuring it against libstdc++ symbols on a
  later build gave 24% — 57.9µs a name detected against 43.7µs with `language="itanium"`
  forced. The number moves with the corpus and with how many schemes are registered, so
  it is recorded as a range rather than a constant: **detection is 10–25% of an Itanium
  name**, and it is the largest single item left. What would actually recover it is a
  wider screen than one character — eight of the ten schemes are offered every `_`, and
  `go`, `nim` and `pascal` are offered every symbol whatever it starts with.

Nothing is left under this heading. The next thing worth measuring is `parse()`, which
has had the same attention only once.

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
  same types differently: **1,076 of 1,076** against `llvm-cxxfilt --types` and 1,073
  against `c++filt -t`, the three being a doubled `KK` cv-qualifier that GNU folds away
  and LLVM keeps, on input no compiler emits.

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
