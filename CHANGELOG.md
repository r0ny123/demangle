# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **The five spellings that stood between the `gnu` style and `c++filt`.** Over the
  217,730 distinct Itanium symbols in every shared library a stock Ubuntu 24.04 ships,
  the gnu style differed from `c++filt` 2.42 on 80 of the 217,057 it reads. It differs
  on 3, and all three are the `std::once_flag::_Prepare_execution` shape where
  libstdc++'s own header settles it against GNU -- so what is left is a shortfall of the
  reference. In `libLLVM.so.18.1` alone it now reads all 44,093 names `c++filt` reads
  and spells every one of them byte for byte as it does.

  Each was read off `c++filt` with probes rather than guessed, and each is an option
  that is off under `llvm`:

  - `&entity` in a template argument, 60 names, all of them LLVM's `sandboxir`. GNU
    prints `&A::f` where llvm-cxxfilt prints the whole declaration `&A::f(int)`, and
    brackets it -- `&(A::f() const)`, `&(void A::f<int>())`, `&(f())` -- for every shape
    that is not a bare *qualified* function name.
  - Operand bracketing, 11 names and every expression the corpus does not cover. GNU
    brackets by *kind* rather than by precedence: a name, a qualified name, a braced
    initialiser list and a function parameter go bare and everything else is wrapped, so
    it writes `(1)+(2)` and `!(x<int>)` but `std::x+(2)` and `{parm#1}+(2)`. Arguments
    on a *qualifier* do not make a name a template-id -- `!is_array<T>::value` is
    unbracketed -- so the last component decides. Also `sizeof {parm#1}` against
    `sizeof (1)`, `noexcept({parm#1})`, and `(1)?(2) : (3)`.
  - A doubled cv-qualifier, 3 names. `const` applied to a type that already carries it
    adds nothing, so `c++filt` folds the repeat and `llvm-cxxfilt` keeps it; the outer
    one wins, which is why `K V K i` and `K K V i` both print `volatile const`. Verified
    over all 39 sequences of one to three qualifiers, and its boundaries: an array
    passes qualifiers through to its elements, every other declarator stops them.
  - The `{default arg#1}` scope of an entity declared in a default argument, 2 names,
    which llvm-cxxfilt drops -- giving one name to two different lambdas when a function
    has one in its body too.
  - A generic lambda's `auto:1`. `[](auto a, auto b)` is mangled as references to
    parameters the closure never declared, and GNU numbers them by *index*, so
    `Ul T0_ T_ E` is `{lambda(auto:2, auto:1)#1}`. The number is the only thing that
    tells two of them apart. This closes the last open item under ROADMAP heading 0 for
    that style.


- **The five `<special-name>` productions GNU reads and LLVM does not**, and GNU's
  wording for the two both read but word differently. `TF <type>` (`typeinfo fn for`),
  `TJ <type>` (`java Class for`) and `GA <encoding>` (`hidden alias for`) are GNU
  extensions rather than ABI productions -- libiberty's `d_special_name` reads them,
  llvm-cxxfilt refuses them, and this refused them too. `GTt` and `GTn`, the transaction
  clones, were already read. In the `gnu` style `TH` and `TW` are now `TLS init function
  for x` and `TLS wrapper function for x`, which is what `c++filt` prints; the default
  `llvm` style keeps `thread-local initialization routine for x`. The whole table is
  pinned against both references, in both styles, by `tests/test_special_names.py`.

  Honest about the size of it: `TF`, `TJ` and `GA` appear in none of the 345,601 symbols
  in the shared libraries this was measured against -- `TJ` is gcj's, which no longer
  ships. `TH` does appear, and was worded wrongly under `gnu`.

- **Ada, as GNAT encodes it.** The last of the pre-Itanium formats libiberty still
  carries: when the GNU v2, lucid, ARM and HP styles were dropped from the default,
  `--format=gnat` stayed. The reference is `ada_demangle` in `libiberty/cplus-dem.c`,
  with GCC's own `exp_dbug.ads` documenting the encoding normatively. Narrow but
  concentrated -- avionics, rail, defence.

  **34 of 34** against the cases `demangle-expected` marks `--format=gnat`. One of them
  is a name the reference declines, printing `<x_E>`; that is recorded as the name
  unchanged, which is how `demangle()` says the same thing.

  There are no types in it -- an Ada symbol names an entity and stops -- so the grammar
  is small and the detection is the whole problem. `yz__qrs` is a package and a
  subprogram, and it is equally what any C program writes. Parsing the name and claiming
  whatever parses reads 797 names from the other corpora here and **6,764 real symbols**
  from this machine's own libraries as Ada, which is not detection but a coin toss with
  a confident voice.

  So a name is claimed only when it carries something GNAT wrote and a C compiler would
  not -- `_ada_`, an `O`-operator, a `TK` task suffix, a `P`/`N` protected subprogram, a
  stream `S[RWIO]`, a controlled `D[FA]`, an `X` body-nested marker, a `___elabb`-style
  special name, a `_B`/`_E` entry body, an overload number -- *and* the whole name is
  accounted for. That second half exists because several of the reference's suffixes stop
  reading and abandon the rest: `rDF16_`, the Itanium encoding of `_Float16 restrict`,
  really does parse as `r.Finalize` with `16_` to spare. Measured under the whole rule:
  **0** claims over the other schemes' 81,457 names and **0** over 339,117 real symbols.

  The cost is that 4 of the 34 vectors are not auto-detected, being lower-case
  identifiers joined by `__` and nothing else. They read under `language="ada"`. Not
  claiming a name returns it unchanged, which is what an unreadable name does anyway;
  claiming someone else's rewrites it into a plausible lie.

- **Metrowerks CodeWarrior C++.** The other pre-Itanium mangling, and the one libiberty
  never read: `cplus-dem.c` has no CodeWarrior flag, `demangle-expected` has no vectors
  for it, and binutils has never demangled one. GameCube and Wii titles, Palm OS, BeOS
  and classic Mac OS were built with it, which is why the reference is a decompilation
  project's tool -- `encounter/cwdemangle`, public domain under CC0-1.0 -- rather than a
  compiler vendor's.

  **47 of 47** against that tool's own test module, under all three of its option
  settings.

  It looks like the ARM encoding and is not. A template argument list is written out
  *literally* in the symbol, `single_ptr<10CModelData>`, so finding where the name ends
  means counting brackets before looking for the `__`. A pointer-to-member is
  `M<class>F` followed by two hidden parameters whose spelling says whether the member
  function is `const`. A function-local static is `@LOCAL@f@v` on Wii and
  `v$localstatic1$f` on GameCube. And the type spelling is the reference's own:
  `const char*`, not `char const *`.

  The two pre-Itanium schemes overlap, and the split between them is a decision rather
  than a fallout. `AtEnd__13ivRubberGroup` is valid under both, both readings parse, and
  they differ only in spelling -- so GNU v2 is offered first and the commoner mangling
  wins the tie. What is unambiguously CodeWarrior, GNU v2 now refuses rather than
  mis-reads: a literal `<...>` argument list (no GNU v2 compiler writes one), a `__ct`
  or `__dt` marker the `gnu` style does not know and would spell as an ordinary function
  name, and a declarator with an empty pointer slot -- `int (CGuiWidget::)(...)` -- which
  is what GNU v2's reading of a CodeWarrior pointer-to-member produces and is not
  something C++ spells. `@LOCAL@` and `@GUARD@` are left by the Delphi scheme, which
  otherwise claims every leading `@`.

  Detection is held to the same bar: **0** claims over the 80,748 names in the other
  schemes' corpora, and **0** over 339,117 symbols from real shared libraries.

- **Pre-Itanium C++: g++ 2.x, cfront/ARM, Lucid, HP aCC and EDG.** Everything C++ before
  the Itanium ABI, which is what a binary from before 2000 -- and anything HP's or
  Lucid's compilers built after it -- holds. Five manglings, and one demangler, because
  that is how libiberty implements them: `cplus-dem.c` reads all five under five style
  flags, and this is a transcription of it at GCC 8.3.0, the last release that carried
  it before GCC 9 deleted it.

  **1324 of 1324** against that same tree's own `demangle-expected`: every case it marks
  `--format=gnu`, `--format=lucid`, `--format=arm` or `--format=hp` -- 662 names -- scored
  under both settings of `DMGL_PARAMS`, because the file records both spellings and they
  reach the parser differently.

  The style is an option rather than something detection works out, and deliberately:
  `__ct__1cFi` is `c::c(int)` to a cfront compiler and `c::__ct(int)` to g++, both
  readings parse, and nothing in the name says which is right. `gnu` is the default;
  `demangle.style("llvm", gnuv2={"style": "arm"})` picks another, and `params` and `ansi`
  are there beside it as the reference's other two flags.

  Detection is the whole risk. A name in this family is an ordinary C identifier with a
  `__` somewhere in it -- `AtEnd__13ivRubberGroup` is something a C compiler would have
  accepted -- so there is no prefix to key on, and a scheme that guessed would rewrite
  other people's symbols into plausible lies. Three things instead: the scheme is offered
  *last*, after Itanium, so it only sees what nothing else claimed; `detect` runs the
  whole parse rather than a shape test; and a reading has to have decoded something a C++
  name has -- a name whose components are not identifiers, a `static` with no class to be
  static in, a label with nothing keyed to it, a parameter with no type in it -- or it is
  refused. Measured, not argued: over the 80,748 names in every other scheme's corpus it
  claims **none**, and over **339,117** symbols from this machine's own shared libraries
  it claims **one** -- `drm_intel_gem_bo_map__wc`, where `wc` is a valid argument list and
  `c++filt --format=gnu` reads it identically.

  Two places are stricter than the reference, both where the reference reads something
  that is not a type: a class name of length zero, and a type that spells no base type at
  all. Neither appears in its own vectors, and the second is what
  `drm_intel_gem_bo_map__cpu` looked like.

- **ARM64EC hybrid names.** A function built for the hybrid ABI carries `$$h` after its
  qualified name, and this refused every one of them. So does everything else:
  `llvm-undname` 18.1.3 refuses `?func@@$$hYAXXZ`, and current upstream has no `$$h` in
  `MicrosoftDemangle.cpp` -- there is no reference *spelling* to copy, and inventing one
  is how a demangler starts inventing.

  There is a normative *rule*, though. LLVM's `getArm64ECDemangledFunctionName` says what
  an ARM64EC name is the hybrid form of, and it is what the compiler emits an `EXPORTAS`
  directive against -- so its answer is the name the linker resolves. An MD5 name loses a
  trailing `$$h@`; any other loses the first `$$h` wherever it stands. This reads the name
  that leaves, as a *fallback*, so a name that already parses is never rewritten.

  That makes the conformance check exact without a reference binary: **606 of 606** names
  from LLVM's own corpus, with the marker inserted where LLVM's *mangler* puts it,
  demangling to what the name without it demangles to. The `#name` form is recognised and
  deliberately not read -- it would mean claiming every string opening with a `#` to strip
  one character.

- **MSVC RTTI type descriptor names, `.PEAX` and `.?AVFoo@@`.** A `type_info` points at
  a string and the linker spells it as a `.` followed by a bare type encoding. It is not
  a decorated name -- no `?`, nothing declared -- so every one was refused, and a PE
  symbol dump full of them said nothing. **106 of 106** against `llvm-undname`, counting
  the descriptor *objects* (`??_R0<type>@8`) beside the names.

  Claiming a leading `.` where a symbol table is full of `.text`, `.L1234` and
  `.constprop.0` is the risk, and the answer is measurement rather than argument: what
  follows the dot has to parse as a *whole* type before anything is said about it, and
  none of the 731 dot-prefixed names in the checked-in corpora, nor any of 51 section and
  label names, is claimed. The Objective-C scheme still wins `._OBJC_CLASS_...`, which is
  what priority is for.

- **The marker in an RTTI descriptor goes where a declarator goes.** `??_R0PEAY01H@8` was
  `int (*)[2] \`RTTI Type Descriptor'` and the reference prints
  `int (*\`RTTI Type Descriptor')[2]`: it is spelled as the name being declared, not
  appended after the type. Only visible on a type that wraps its name, which is why the
  one such vector in the corpus did not catch it.

- **The stream filter and the tree, as library API rather than as CLI internals.** Three
  shapes a Python caller expects and did not find, each of which already had a working
  implementation inside the command or behind a private helper.

  `demangle_text()` and `demangle_stream()` substitute every symbol-shaped word in mixed
  text and copy the rest through -- what `nm ... | demangle` does, without shelling out
  to our own command. rustc-demangle ships `demangle_stream` as a crate function rather
  than only inside `rustfilt`; the precedent is clear. `find_symbols()` is the same scan
  without the substitution, giving the *span* of each symbol -- the question
  `microsoftDemangle`'s `n_read` out-parameter and `llvm-undname --warn-trailing` answer
  in C, here for every scheme at once. The command and the library share one tokenizer,
  because two would drift and the drift would be silent.

  `Node.to_dict()` turns a tree into plain data, `demangle --json` prints it, and every
  node carries `__match_args__`, so `match Pointer(Builtin(name))` works -- which is what
  "returns a walkable tree" means to a Python caller now. `node_kinds()` publishes the
  vocabulary to switch on, per scheme or as a whole: `swift-demangle` prints `kind=`
  dumps and libiberty publishes a hundred `DEMANGLE_COMPONENT_*` enumerators for the same
  reason, and a test checks the list against every corpus so it cannot drift.

  A node reached more than once is written out once, with an `id`, and afterwards as
  `{"$ref": id}`. That is not a size optimisation: the structure is a *graph*. Itanium's
  substitutions make one component reachable from several places, and a Rust node names
  its children twice over -- `parts` orders them, `base` and `arguments` say what they
  are -- so writing every occurrence in full doubles per level. One real symbol from the
  Rust toolchain cost 4.7 seconds and 363MB that way before this; it is under a second
  and 32MB now, and bounded by the graph rather than by its expansion.

- **Swift's simplified spelling, exact against the compiler's own 217 vectors.** What
  `swift-demangle --simplified` prints, and what Xcode and LLDB show in a stack trace:
  `Either` for `Monads.Either`, `(_:)` for `(Swift.Int) -> Swift.UInt`,
  `specialized f()` for a page of specialisation arguments, `destroy for T` for
  `destroy value witness for T`.

  Built as options the printer consults rather than as a pass over the text, which is
  where the reference builds it and for the reason it has to be: dropping a module
  qualification means knowing which run of characters *was* the module, and after
  printing nothing does. `SwiftOptions` carries the twelve flags upstream's
  `SimplifiedUIDemangleOptions()` bundle actually changes here -- the rest of its
  fourteen either match this printer already or change nothing over the 217 vectors, and
  a flag no vector exercises is a flag with no reference behind it.

  `demangle --simplified` on the command line, `style("llvm", swift=SIMPLIFIED_OPTIONS)`
  from the library. Scored **217 of 217** and pinned, corpus checked in.

- **A per-call options object: `style()`, and MSVC's five suppression flags.** The two
  named styles say how to *spell* a name; what a caller usually wants to vary is how much
  of it to spell, and every peer tool composes that at the call site --
  `llvm-undname --no-calling-convention`, `UnDecorateSymbolName`'s mask, `c++filt -p`.
  Doing it here meant building a `Style` and registering it globally, which is a process-
  wide change for one question.

      demangle(name, style=demangle.style("llvm", msvc={"calling_convention": False}))

  `Style.with_options` is the composition and `style()` the front door. A composed style
  is an object rather than a registered name, and `demangle()` deliberately does not cache
  a call that passes one, so one caller's narrower spelling is never served to another.

  The MSVC scheme grew the five options `llvm-undname` has, spelled and meaning the same:
  `calling_convention`, `access_specifier`, `member_type`, `return_type`, `variable_type`,
  with `--no-...` flags on the command line. `--no-return-type` now goes through the
  option for MSVC rather than through the render-time cut, and that is a fix: a return
  type there wraps *around* the declarator, so `?fn@@YAP6AHH@ZXZ` had no prefix to strip
  and came back unchanged.

  Scored at **1,250 of 1,253** against `llvm-undname` over the differences the flags make
  on LLVM's own 609-name corpus, pinned in `tests/conformance/msvc-suppressions.txt`. The
  three are names where the reference's own `--no-return-type` leaves an unclosed
  bracket. Three rules were derived from the reference rather than guessed, and each
  reads as a bug until you watch it happen: a function reached as a *pointer's* pointee
  keeps its calling convention, because the pointer prints it rather than the signature;
  a symbol naming a *scope* keeps its full spelling; and `extern "C" ` groups with
  `static` and `virtual` rather than with the access specifier.

- **Bare type encodings: `demangle_type()`, `parse_type()`, and `demangle --types`.**
  A `typeinfo` name, an MSVC RTTI type descriptor and a Swift metadata typeref carry a
  *type* rather than a symbol, and nothing in this package could read one. Itanium, MSVC
  and Swift now each expose a `parse_type` on their plugin, and the two public entry
  points dispatch to it; the schemes with no type grammar of their own say so by name in
  the error rather than failing obscurely.

  `language` is required, and is the whole design of the interface. A symbol announces
  its scheme -- `_Z`, `?`, `$s` -- and a type encoding announces nothing at all: `Si` is
  `std::istream` read as Itanium and `Swift.Int` read as Swift, and no evidence in the
  string decides between them. Detection is not merely unimplemented here, it is
  impossible, which is why every reference puts this behind a flag of its own --
  `c++filt -t`, libiberty's `DMGL_TYPES`, `UnDecorateSymbolName`'s `UNDNAME_TYPE_ONLY`. On the command line `--types` reads one encoding per argument or per
  line rather than filtering symbols out of mixed text, because a type encoding is an
  ordinary word and `I like Pi` must not become `I like int*`.

  `demangleb_type()` and `parseb_type()` go with them, because a type encoding is read
  out of a binary as often as a symbol is -- an Itanium `typeinfo` name sits in
  `.rodata` and an MSVC type descriptor's name in `.rdata`.

  Scored over 1,076 encodings against **both** references, one corpus each because the
  two spell the same types differently: **1,076 of 1,076** against `llvm-cxxfilt
  --types` (18.1.3) and **1,073 of 1,076** against `c++filt -t` (binutils 2.42), pinned
  in both directions. The three misses are a doubled `KK` cv-qualifier, which GNU folds
  away and LLVM keeps; no compiler emits one, since the ABI writes a single
  `<CV-qualifiers>` group per type.

- **Itanium `tr` and `tw`, the throw expressions.** `decltype(throw)` and
  `decltype(throw 1)` were refused outright. Both references read them and spell the
  operand differently -- GNU brackets it, LLVM writes it after a space -- so the
  existing `gnu_expression_spelling` option carries the difference.

- **Swift symbolic references**, and an API that takes the binary too. A mangled name in
  Swift *metadata* is not always self-contained: where it would have to spell a type the
  image already describes, the compiler writes a one-byte marker and a four-byte signed
  offset relative to itself. The bytes are transcribed from
  `Demangler.cpp::demangleSymbolicReference` -- `01` and `02` for a context descriptor
  direct and indirect, `09` for an accessor function, `0A` and `0B` for the two extended
  existential shapes, `03`-`08` and `0C` reserved and refused, `FF` alignment padding and
  skipped.

  Two consequences fall out of that encoding, and both are handled here. A metadata blob
  **cannot be split on NUL**, because the four offset bytes are arbitrary and very often
  hold a zero -- `symbolic.end_of_name` walks a name instead, which is what Swift's own
  reflection reader does. And a name holding one **is not text**, so
  `swift.demangle_symbolic` takes `bytes` where the rest of the package takes `str`.

  Resolving one needs the image, so `resolve.ContextResolver` reads the descriptor the
  offset reaches, walks its `Parent` chain to the module, and hands back a *mangled
  fragment* -- `4demo5PointV` -- which the demangler then reads in place. A fragment
  rather than a finished spelling is what keeps the rest of the mangling working: a
  reference may be the base of a bound generic, and "types register as substitutions even
  when symbolically referenced", so a later `AA` refers back to it. `Image`, `elf_image`
  and `macho_image` map a file into addresses; a caller with a debugger or a memory dump
  supplies its own `read`.

  Verified twice over, and neither check is this library's own opinion. **Resolution**:
  every symbolic reference in the Swift 5.10.1 runtime that lands on a descriptor the
  linker named -- 4,528 of them -- resolves to the name that symbol demangles to, with
  `swift-demangle` reading both sides. **Spelling**: each reference replaced by the
  fragment it resolved to gives a self-contained name, and `swift-demangle` agrees with
  what we spelled on 4,799 of 4,799. A further 856 are set aside because splicing itself
  changes their meaning: a symbolic reference contributes one substitution and its
  spelled-out equivalent contributes one per nominal component, which shifts every later
  back-reference.

  Getting the last 35 right meant reading a descriptor's **import info**, the components
  after the name that a Clang-imported type carries: `N` gives the ABI name and `S` the
  symbol namespace, whose value `t` means the descriptor came from a C `typedef` and is
  written as a type alias. Without it `__C.CFArrayRef` reads as `__C.CFArray`, which is
  the user-facing name and not the one the compiler mangles.

- **Objective-C symbol names**, across all three runtimes. Objective-C is barely
  mangled, and what mangling there is belongs to the compiler rather than the language,
  so the rules are transcribed from clang's `lib/AST/Mangle.cpp`,
  `lib/CodeGen/CGObjCMac.cpp` and `lib/CodeGen/CGObjCGNU.cpp`, and from the symbols GCC's
  own front end left in the shipped `libobjc.a`. Four families are read: the Apple
  runtimes' `-[NSString stringWithFormat:]`, the GNU family's `_i_NSString__length`,
  Apple's `_OBJC_CLASS_$_NSString` data symbols and the fragile ABI's
  `.objc_class_name_NSString`, plus block invocation functions, whose parent method is
  length-prefixed by `mangleObjCMethodNameAsSourceName`, and the GNU runtime's type
  encodings, where `@` is written as a control byte because `@` marks a version in an
  ELF symbol.

  There is no reference demangler, so the expected output is not this library's own:
  every corpus symbol was emitted by clang 18.1.3 for Objective-C this package wrote,
  and the expectation is what the *declaration* said. 2,665 of 2,665 across the macOS,
  i386-fragile and GNUstep 2.0 ABIs, and every readable symbol in the shipped
  `libobjc.a`.

  **The GNU-family method mangling is not injective**, and clang says so where it writes
  it: "it has obvious collisions in the face of underscores within class names, category
  names, and selectors". A `:` and a field separator are both `_`. Every reading that
  re-mangles to the symbol is found; the preferred one is the reading that needs no
  category, because a method outside a category leaves that field empty and its two
  separators fall together into a visible doubled underscore. Measured: 445 of 445 when
  the identifiers are written the way Objective-C is written, 396 of 422 on a corpus
  built to put underscores in all three, and `ambiguous` is set on every name where
  another reading exists. The 26 are listed by name in
  `tests/conformance/objc-lossy.txt`.

  Two GNUstep forms are refused rather than guessed at. `.objc_category_FooBar` joins the
  two names with no separator at all -- `CGObjCGNU.cpp` writes
  `".objc_category_" + ClassName + CategoryName` -- so nothing can say where one ends.

  The `_i_`/`_c_` method form is shaped like an ordinary C identifier, so whether to
  claim it on sight had to be measured rather than argued: over 375,190 symbols from 400
  shared libraries and archives, plus this package's corpora and the shipped libstdc++,
  libLLVM, libclang-cpp and Swift runtime, it claims exactly five names, and all five
  are real Objective-C methods in `libobjc.a`.

- **Swift symbol names**, both manglings. A port of the compiler's own demangler and node
  printer, because nothing smaller is enough: the mangling is postfix and compresses
  against three tables that span a whole name, so a symbol cannot be read a piece at a
  time, and how a piece is *spelled* depends on where it sits in the tree. Exact against
  `swift-demangle` 5.10.1 over all 48,368 `$s` symbols in the shipped runtime and
  Foundation, and over all 376 cases in the compiler's own
  `test/Demangle/Inputs/manglings.txt` -- which covers SIL function types,
  function-signature specialisations, key-path thunks, autodiff, macro expansions and
  the Swift 3 mangling, still what the ObjC runtime holds for a Swift class. It refuses
  exactly the 69 names the reference itself refuses.

- **Nim symbol names.** Nim compiles to C, so a Nim symbol is an ordinary C identifier
  with no prefix to key on, and nothing in the toolchain reads one back. The rules are
  transcribed from the compiler's `mangleutils.mangle` and
  `modulegraphs.uniqueModuleName`; the correctness argument is Go's -- re-mangling what
  is read must reproduce the symbol -- plus agreement with the name the compiler recorded
  in its own `.ndi` debug-mapping files, 2,115 of 2,115. The mangling is not injective in
  two places, and both are documented rather than papered over: `mangle` drops an
  underscore before a digit, and `result` contains `lt`, so a greedy decoder reads it as
  `resu<`. What rules the second out is that Nim names have a grammar.

- **Free Pascal symbol names.** The rules are transcribed from the compiler's
  `make_mangledname`; the property is re-assembly, and it holds for all 236,570 readable
  symbols in the 1,074 object files of the shipped 3.2.2 runtime and packages.
  Independently checked against `ppudump`. Case is not recoverable -- Pascal is
  case-insensitive and the compiler upper-cases before mangling -- and that is stated
  rather than hidden.

- **Borland/Embarcadero Delphi symbol names.** A different scheme from Free Pascal's,
  `@Unit@Class@Method$qqrv`, shared with C++Builder. Transcribed from Embarcadero's
  `unmangle.c` -- the unmangler TDUMP, the linker and the debugger run -- because there
  is no Delphi compiler on the platforms this package is developed on. Spelling is what
  that unmangler prints, including C++ `::`. Microsoft's 32-bit `__fastcall` C decoration
  `@name@N` is refused. Checked against that unmangler's own vectors and against a dump of
  the real `tdump.exe -q -um` over the export tables of real BPLs and C++Builder DLLs:
  11,363 of 11,363 Delphi names exact, with the 10 MSVC `@name@N` decorations sitting next
  to them refused. That dump is checked in as `tests/conformance/delphi-tdump.txt` and
  replayed in CI, with its expected column being the reference's output rather than this
  library's own reading. Several defects showed up only against it: conversion operators
  (`$o`) consumed the following `$` so `qv` looked like junk in the name; the calling
  convention was inserted in front of the whole name rather than after the `__linkproc__`
  marker, which is where the unmangler puts it; and two rewrites of the finished spelling
  -- collapsing the duplicated qualifier the unmangler really does print, and hoisting
  `__fastcall` to the front afterwards -- moved 692 real exports away from the reference
  and are gone. The spelling is now whatever the transcription produces, unedited.
  Those tables exercise 75% of the parser; the constructs they never produce are covered
  separately by `tests/conformance/delphi-constructs.txt`, 53 hand-built names
  corroborated against the independent Ruby port of the same unmangler rather than
  against TDUMP -- weaker evidence, kept in its own file and labelled as such.

- **Go symbol names.** A new scheme, registered like any other -- `core` was not touched.
  Go escapes a `.` that falls after the last `/` of a package path, so
  `example.com/m/v2%2e5.T.M` does not split into package and name where the raw text
  suggests; the rules are transcribed from Go's own `cmd/internal/objabi/path.go`. Both
  directions are implemented, because that is what makes the decoder checkable without a
  reference: re-escaping a decoded path must reproduce the bytes the linker wrote.
  Verified over all 30,733 Go symbols in the shipped toolchain.

- **A worked `parse()` example**, `docs/analysing-a-binary.md`, taking one real question
  through the tree against the system libstdc++ and measuring what the regular-expression
  version of the same question gets wrong. Its code and its numbers are executed by the
  test suite, so the page cannot drift from the library.

- **Three `DF` productions that were read as one.** `DF <n> _` is `_FloatN`, but
  `DF <n> x` is `_FloatNx` -- the `x` *is* the terminator, and there is no `_` after it --
  and `DF16b` alone is `std::bfloat16_t`. Reading `x` as an optional flag before a
  required `_` refused every `_Float32x`, `_Float64x` and `bfloat16_t` in the shipped
  libstdc++.

- **GNU's space before a template argument list whose name ends with `<`.** `operator<<`
  instantiated at `int` reads `operator<< <int>` in gnu style, for the same reason
  `Foo<Bar<int> >` does: three angle brackets in a row. 32 names in libstdc++.

  Both were found by checking against the reference over the *whole* library rather than
  over the recorded sample, and neither could have been found against llvm-cxxfilt 18,
  which reads none of the `DF` forms at all.

- `demangle.parse(..., style="gnu")` now spells `Dn` as `decltype(nullptr)`, which is
  what GNU c++filt writes; LLVM's `std::nullptr_t` remains the default. Found by
  `tools/differential.py --cross`: no name in the gnu corpus carried a `Dn`, so nothing
  had ever asked.


- **Expressions are structure, not text.** `decltype(a + b)` reached the tree as one
  opaque node, so a caller wanting the operands had to parse C++ back out of a string.
  The parser now reports them through a new `Builder.expression(form, parts)` method:
  `form` names the shape (`binary`, `conditional`, `call`, `sizeof`, `cast`, `new`, and
  the rest) and `parts` interleaves the production's fixed text with its operands'
  handles. `core.ast` gains an `Expression` node with an `operands` view. Brackets are
  parts, so a parenthesised operand stays reachable instead of being glued into text.

  One method rather than one per operator: the parser already owns operator spelling,
  which comes from tables that exist to be checked against the ABI, so what is left for
  a builder to decide is structure.

- **Rust `parse()` returns a tree.** A Rust symbol was a single `raw` node, so `walk()`
  and `find()` saw nothing below the root. It now comes back as a `symbol` holding a
  `path` of `name` components, with `impl`, `namespace`, `template`, `type` and
  `literal` nodes for what a path carries. An impl names its self-type and its trait as
  fields, a closure carries its disambiguator, and the legacy scheme's hash is kept
  although it is still not spelled.

  The v0 printer now emits into a *sink* rather than concatenating a string: `TextSink`
  joins the fragments, `TreeSink` remembers where each production began and ended. There
  is one traversal, so a tree renders to exactly what `demangle()` returns by
  construction rather than by agreement. Verified over all 5,738 Rust names in the
  corpora: text byte-identical to before the change, every tree spelling identical to
  its text, and no name left as a bare leaf.

- **`signature()` and `Signature`: the parts of a name, in one shape, for every scheme.**
  A disassembler labelling a call site wants the base name without its namespace; a
  cross-reference wants the namespace without the base name; a signature matcher wants
  the parameter types. Splitting the spelling gets all three wrong the same way, because
  `::` and `.` and `,` occur inside template arguments and operator names as well as
  between the parts a caller means.

  The split is done on the tree. C++ builds a declaration and the fields are read off
  it; Swift, D, Go, Nim, Free Pascal and Delphi build a node that *is* its own fragments
  in output order, so `parts` says which `.` separates two components and which is
  inside a name. `ops..` is the module `ops` and the operator `..`;
  `Foundation.FileHandle.(_check in _2DF8)()` opens two brackets and only the second is
  a call. Text is the last resort, and there it counts brackets and leaves a phrase --
  `inout Swift.Int`, `operator new` -- whole rather than splitting it at a space.

  `namespace` + the scheme's separator + `base_name` spells `qualified_name` exactly,
  checked over every checked-in corpus under both styles: 74,875 names. What each scheme
  records differs and the fields say so rather than guessing -- a Rust path carries no
  signature, so its `parameters` is `None`, which is not `()`.

- **`demangle -p`, `--base-name` and `--no-return-type`**, over `signature()`. `-p` is
  `c++filt -p`, and over libstdc++ and the gnu corpus the two agree on 6132 of 6213. The
  81 are deliberate: `c++filt` strips the parameter list from the outermost declaration
  only, so a thunk keeps its target's, and it drops a `[clone .cold]` suffix while
  keeping `@@GLIBCXX_3.4`. This strips throughout and keeps both, because a filter over a
  symbol table should not quietly discard part of the symbol.

- **JNI native method names.** `Java_com_example_Foo_bar__Ljava_lang_String_2` is
  `com.example.Foo.bar(java.lang.String)`. A `native` method is called through a C
  function whose name encodes its class, its name and -- where it is overloaded -- its
  parameter types; Android ships them by the thousand and nothing else reads them, so
  the usual way is by eye. Neither binutils nor LLVM demangles these, and neither does
  Ghidra or IDA.

  It is the one scheme here whose encoding is written down *normatively* -- the JNI
  specification's "Resolving Native Method Names" -- rather than having to be
  transcribed from a reference implementation. What the encoding does not carry is the
  boundary between package, class and method: all three are joined with `_`, and `/` is
  also `_`. So the spelling puts the whole path in one run and the tree does not claim a
  structure the name does not have.

  The prefix is not treated as proof. A name must also decode -- a declaring class as
  well as a method, and a valid JVM descriptor where a signature is present -- so
  `Java_helper` is left alone. Over the 75,414 names in the checked-in corpora and
  262,845 distinct symbols from 120 system libraries, it claims none.

- **`signatureb`**, joining `demangleb`, `demangleb_strict`, `parseb` and `detectb`. A
  symbol table holds bytes and they are not reliably UTF-8; every entry point has a bytes
  form so reading one does not mean guessing an encoding first.

- **C++20 and C++23 Itanium productions**: module names (`W`, `WP`, `_ZGIW`),
  requires-clauses and requires-expressions (`Q`, `rq`, `rQ`), fold expressions, vendor
  expressions, explicit object parameters (`NH`), designated initialisers, subobject
  references, pack indexing, constrained placeholders, declarator-placed parameter
  names, `objcproto`, template parameter objects (`TA`), and `char` arrays spelled as
  string literals.

- **Rust pattern types and splat arguments**, and rustc-demangle's own `#[test]` vectors
  as a corpus.

- **Swift 6 and later**: the lowered function type, value generics, coroutine accessors
  and coro thunks, called-once functions, isolated deinit, suppressed conformances, the
  no-value-witness outlined operations, and Swift's own `manglings.txt` as a corpus --
  every vector in it. The last eight needed four more function-signature specialisation
  kinds (an escaping closure, a closure the same as an earlier argument, propagated
  structs, and `p` becoming a *run* so one argument can carry several constants),
  arguments the optimiser dropped, `Tfr` representation changed, the `$e` Embedded Swift
  prefix, macro expansion source locations, pack protocol conformances, and an opaque
  result type's conformance. Each was transcribed from swiftlang/swift's own
  `Demangler.cpp` and `NodePrinter.cpp`; fitting a grammar to eight examples is how a
  demangler with no wrong spellings starts having them.

- **MSVC wide and multi-byte string literals**, and the truncated ones. `??_C@_1...` is
  `L"wide"`; a narrow literal's character width is not in the encoding, so the
  reference's guess from the trailing and embedded nul bytes is transcribed here.

### Performance

- **Three schemes stop scanning a name one character at a time in Python.** The same
  shape in three places, each on a path every symbol of that scheme takes.

  MSVC refuses a decorated name holding a control character, written as
  `any(char < " " or char == "\x7f" for char in name)` -- a generator resumed once per
  character, at three call sites. As one compiled scan it is 7.8x cheaper on a
  64-character name, and **10.2%** off the MSVC corpus.

  Swift's `_record_words` splits every identifier into the words a later one may refer
  back to, calling `_is_word_end` and `_is_word_start` per character: 322,000 calls to
  the first alone over the Swift corpus, the largest single item in its profile, each an
  interpreter frame around one comparison. Written out, the splitter is 1.65x faster and
  the corpus is **5.3%** faster. Checked against the original on 400,000 random
  identifiers: the same split every time.

  Rust's `_is_hex` and `_is_symbol_like` are a `str.strip` and a compiled match now.
  All three were checked for equivalence over 200,000 random strings including
  non-ASCII, surrogates and control characters.

- **The tree builder stops paying for generators.** `sum(node.size for node in nodes)` is
  a frame resumed once per element, and `AstBuilder` asks it of every qualified name,
  every template argument list, every parameter list and every pack -- seven times per
  name over the Itanium corpus. Measured at 234ns for three nodes against 68ns for the
  loop that replaces it. 2.2% off `parse()`. `spelling.py` had already made this argument
  about its joins and the three sites here had been missed, along with four in the
  Itanium parser.

- **The Itanium reader is 7.4% faster on the project's own corpus and 10.2% on 217,730
  real symbols, with byte-identical output.** Six changes, each measured by alternating
  two worktrees and taking the median of nine timed rounds per process -- the machine's
  run-to-run spread is wider than several of them individually, and two trees measured
  in turn cancel it. Identical trees measured that way come out at 0.993 and 1.005.

  `Reader.peek()` takes no argument. It is called around fifty times per name, more than
  anything else in the package, and CPython charges for a default it then has to bind;
  the offset form is now `ahead(n)`. Worth 3.3% alone. `Reader.length_prefixed()` reads a
  `<source-name>`'s length and the characters it counts in one frame instead of three.
  `_type`'s dispatch chain is ordered by how often each arm is taken, measured over those
  217,730 symbols: a nested name is a third of every type read and a substitution a
  fifth, and both had to fall through fifteen character comparisons. `prefix_component`
  gets the same treatment through one membership test, since three components in four
  are a length-prefixed name. `cv_qualifiers` asks one lookahead before consuming
  anything and returns a precomputed tuple. The bounds every production checks are read
  from `limits` once at construction.

- **The detection path, which most symbols in a real binary never get past, is 10.9%
  cheaper.** `split_decorations` is called once per name rather than once per candidate
  scheme: its answer depends on nothing but the name, and for a name carrying no ELF
  version suffix -- almost all of them -- there is nothing for any plugin to ask again.
  Over a table of ordinary C identifiers that is 3.4 interpreter frames per name down to
  one. `get_style` writes out `_table()`'s own fast path, `_keep` is inlined, and
  `_resolve` is not called when no language was named.

- **Rust's v0 reader dispatches a path and a type by what the corpus actually holds.**
  Both the printer and the skipper reached a `<type>` that is a path by falling off the
  end of their tag chains, and `I` and `N` are 58% of the types in the Rust corpus; one
  membership test settles them. The path chains are reordered by the same measurement.
  Between 1% and 5% depending on the run, which is not cleanly separable from the
  machine's own spread -- kept because it is a strict reduction in work and provably
  output-identical.

- **The benchmark checks the corpus it timed against the corpus the baseline timed, for
  every phase.** It asked that of `structured` alone, and the cold corpus -- the one the
  headline figure comes from -- quietly lost four names without anything noticing.


- **Nim's detection is 3x cheaper on a name that is not a symbol.** This scheme declares
  no first character -- a Nim symbol is an ordinary C identifier -- so it is offered
  *every* symbol a caller has, and it was half the cost of detection across all six
  schemes that see a lower-case name.

  Its screen is two necessary conditions, `"__" in name` and a `_u?[0-9]+$` regular
  expression, and both must hold. The regex ran first, so every name with no `__` at all
  paid for a `search` whose `$` anchor does not stop it trying each position on the way.
  Swapping them changes no verdict -- checked on every name of both corpora below -- and
  the membership test is one C-level scan:

      synthetic non-symbols   0.484us -> 0.161us   (3.0x)
      real libstdc++ symbols  0.501us -> 0.342us   (1.5x)

  It wins on real symbols too, despite 39% of them carrying a `__`, because the other 61%
  now stop at the membership test. Detection over the six schemes goes from 1.029us to
  0.783us a name, and the whole negative path -- names that are not mangled, the majority
  in any real symbol table -- is about 7% faster.

  A wider change was measured and rejected: a declarative substring screen on
  `LanguagePlugin`, letting the registry skip a plugin without calling into it. Pure call
  overhead is 35% of detection cost, so the ceiling is real, but the three schemes that
  could use one are worth ~0.1-0.15us between them, which is under 4% of a call and below
  the benchmark's own 8% noise floor. A permanent public field is too much to pay for
  that, and gnuv2 needs a richer predicate than a substring anyway: 17 of its vectors
  carry no `__`, being the `_3RNG$singleMantissa` and `_$_10BitmapComp` marker forms.


- **Detection screens on the first character.** A `LanguagePlugin` may declare the
  characters its names can begin with, and the registry then never offers it a name that
  starts otherwise. With eight schemes registered, labelling names that are not mangled
  at all costs less than it did with five (65 against 73, machine-relative), where asking
  every scheme would have cost 100. A scheme that declares nothing is always offered, so
  this changes nothing for one that does not opt in, and `tests/test_core.py` checks each
  declaration against every corpus rather than trusting it.

- **Itanium demangling is 18.5% faster**, and MSVC 9.4% and Rust 4.4% with it, from a
  profile-guided pass the parser had never had. Nothing in it changes a grammar; the
  spelling of all 290,489 symbols in the shipped libstdc++, libLLVM and libclang-cpp is
  byte-identical before and after, in both styles.

  The parser's own share was interpreter frames around a few bytecodes: the recursion
  guard, entered and left 76,662 times over the corpus to add one to an integer and take
  it away again, is written out at its seven sites; `template_arg` asks one lookahead
  where it asked five to reach the common case; the loops that end at `E` ask `peek`
  once rather than `eat` and then `eof`; `Reader.expect` no longer reaches its test
  through `eat`. In `core.spelling` the joins take list comprehensions rather than
  generator expressions -- a generator is a frame resumed once per element, 79,000
  resumes for 23,000 `qualified` calls.

  Two of the larger wins were not in the parser at all. `SpellingBuilder.decorated` and
  `ast.Node.spell` each carried an `import` *inside* the function, executed per call.
  And detection turned out to be 18.5% of what an Itanium name cost -- not the 1.45%
  recorded when the measurement was made over a corpus dominated by cheap MSVC names and
  before three more schemes landed. Half of that was `registry.candidates` taking a lock
  twice, through two more frames, to reach one dictionary lookup; it reads its cache
  directly now, which is sound because the values are finished tuples and `register`
  discards the whole dictionary rather than editing it. Most of the rest was Nim's
  `detect`, which -- having no prefix to key on, so being offered every symbol in a
  binary -- ran five anchored regular expressions over each. It now screens on a
  *necessary* condition of the first of them, verified to change no answer over 280,935
  corpus and generated names.

- **Rust demangling is a further 33% faster** (80us to 54us a name over the 5,710-name
  real-world corpus, on top of the 23% below). Nothing clever, and nothing structural:
  the scheme is a port and it kept the reference's shape, where a helper costs nothing.
  Here a helper is an interpreter frame, and these run once per *character* -- `eat`,
  `peek`, `next_func` and the two digit readers between them 1.5 million times over that
  corpus. They are written out at the call sites that are hot, the three `skip_*`
  productions no longer reach their bodies through a wrapper that exists only to
  increment a depth counter, and `<basic-type>` is a module-level dict rather than an
  `lru_cache`d function that rebuilt its table on every miss. Two allocations went with
  them: the punycode decoder's 128-element buffer, which was built once per identifier
  and discarded unread for every ASCII one, and the quadratic `rest = rest[1:]` that
  stripped a legacy length prefix a character at a time.

  Verified by replaying 60,000 mutated, truncated and spliced Rust symbols through the
  library before and after: byte-identical on every one.

- **Rust demangling is 23% faster** (99us to 76us a name over the real-world corpus). The
  structure work had put a `contextlib` context manager around every grammar production,
  which on the text path reaches two no-ops through a generator and a wrapper object; and
  the reader recomputed `len()` of its input in `peek`, `eat` and `next_func`, over a
  million times across 2,000 symbols. Digit decoding is now a table lookup rather than a
  chain of `in`, `islower` and two `ord` calls.

- **`parse()` uses 40% less memory and is about 6% faster.** Leaf nodes are interned,
  keyed by their text: they are 55% of all nodes in a real tree and repeat 49 times over,
  and 15,654 `builtin` instances across the shipped libstdc++ hold 32 distinct spellings.

- The benchmark corpus now spans every conformance corpus and all four schemes, 14,041
  names rather than 887. The old sample was dominated by cheap MSVC names and flattered
  the cold figure by roughly 3x.

- **A parameter is asked its size, not its spelling.** `void`-only parameter lists were
  detected by rendering each parameter and comparing the text, on every function.

### Fixed

- **A truncated encoding borrowed the literal that bounds it.** `___Z<encoding>_block_invoke`
  is the one place a parser moves the end of input: a regex says where the encoding
  stops and `reader.length` is shortened to there, so the literal after it must be
  unreadable. `peek`, `take`, `ahead` and `eof` honoured that bound; `expect`, `eat`,
  `startswith`, `peek2` and `remaining` indexed the string and did not. So a
  substitution written at the very end took the `_` of `_block_invoke` as its
  terminator, and `___ZN1a1bES_block_invoke` -- which both `c++filt` and `llvm-cxxfilt`
  leave alone -- came back as `invocation function for block in a::b(a)`. Across the
  Itanium corpora, 4,931 truncated encodings read as though they were whole, in 3,317
  distinct spellings, every one of them looking like a real declaration.

  The overshoot was silent rather than loud because `eof` is `pos >= length`: a cursor
  that had gone *past* the end satisfied the check for having consumed all of it. Every
  method answers against `length` now, and the invariant is asserted directly -- a
  `Reader` that reports any write leaving `pos` beyond `length`, run over the corpus and
  over the same names truncated inside a window, which found 633 violations before and
  none after. Byte-identical on all 563,331 real symbols: nothing well-formed went
  anywhere near this.

  Three lookaheads in the Itanium parser reached past the class into `reader.text` for
  the same answer -- whether an abbreviation is followed by a constructor, whether `gs`
  introduces an allocation, whether a vendor qualifier precedes a function type. This
  literal happens to contain none of the characters they test for, so none of them could
  be fooled by it; they go through a bounded `ahead2` now anyway, because an invariant
  with three documented exceptions is not one.

- **The three pre-Itanium false claims are kept on purpose, and now there is a number
  saying why.** `gnuv2` claims and rewrites three of the 345,601 symbols a stock Ubuntu
  24.04 ships -- `PyInit__lldb`, `PyInit__sre`, `drm_intel_gem_bo_map__wc` -- and the
  obvious next rule is to require positive evidence before auto-claiming a bare
  `name__<builtins>`, the way `schemes/go` declines `fmt.Println`. Measured instead of
  adopted: it would drop 58 of the 458 names in libiberty's own corpus that detection
  claims, among them `overload1arg__Fi` and `polar__Fdd`. A free overloaded function is
  the canonical thing this mangling encodes and has no class, no template and no marker
  by construction, so the rule cannot tell the residue from the corpus -- both have
  empty evidence and a parameter list of nothing but fundamental types. 58 correct
  readings to remove 3 wrong ones is the wrong side of the trade.
  `TestTheThreeItStillClaimsWrongly` pins both sides so the rule cannot be adopted by
  accident.

- **`render()` was a method most node classes had and no class declared.** The schemes
  whose spelling does not fit C++ declarator syntax carry their fragments as text and
  render by concatenating them, so `render()` is what their own nodes use and what
  `spell()` delegates to -- but `core.ast`'s nodes never had it, and neither did four of
  MSVC's. A Rust tree answered `render()` on every node until the symbol carried an ELF
  version suffix, which wraps the tree in a `core.Decorated` that did not, so walking a
  tree and asking each node for its text raised `AttributeError` partway through.
  Defined on `Node` now, as `spell()` with no declarator, which is the same string every
  one of the 57 classes that already had it was returning: checked over all 1,851,583
  nodes of the corpora in both styles. `spell(style=...)` is still the one to reach for,
  because it takes the style the tree was parsed under and this cannot.

  Found by fuzzing the public API surface rather than `demangle()` -- `signature`,
  `demangle_type`, the bytes forms, and the node protocol -- which had not been fuzzed
  before. Nothing else came out of it: `signature`'s own invariants held over 45,000
  mutated names, and so did every entry point's contract.

- **Sixteen kilobytes of nested name bought a second of CPU and 98MB.** Every
  `<prefix>` is a substitution candidate (ABI 5.1.10), so a nested name of N components
  records N entries -- and each entry is the whole prefix, so their sizes sum to O(N^2).
  No single one exceeds `max_output`, which is why that bound never fired: `_ZN` and
  8,190 components of `1a` read in a second and allocated 98MB before returning a
  24KB name.

  The prefixes of one name are now charged against a budget of sixteen times the output
  bound -- a megabyte by default, and it moves with `max_output` for a caller who raises
  it. The worst of the 217,730 distinct Itanium symbols in the shared libraries of a
  stock Ubuntu 24.04 records 10,209 characters against that, and the median is 92, so
  the budget is a hundred times what the largest real name needs. That 16KB name is now
  refused in twenty milliseconds and about a megabyte, and the ceiling does not move as
  the input grows. Every one of the 217,730 demangles to exactly what it did.

- **A Swift name ending in the middle of a specialisation could take the process out.**
  `Demangler.next_char` returned `""` past the end of input without moving, so
  `push_back` -- meant to be its exact inverse -- moved the cursor onto the *last
  character of the name* and the caller read it again. In a loop that is a loop that
  never advances: `demangle_func_spec_param` reads a run of propagated constants and
  puts back the letter that ends the run, so `_T03foo4_123ABTf3psbp` grew one
  specialisation parameter per iteration until the process ran out of memory. Nine
  seconds and 1.5 GB before the OS stepped in; 0.06 seconds and a refusal now.
  `demangle()` is documented never to raise for a string, and what it did instead was
  take the process with it.

  Fixed in `next_char`, so `push_back` is the inverse everywhere it appears rather than
  at the sites someone happened to check: eleven others follow a `next_char` that can
  reach the end. Found by mutation fuzz over every checked-in corpus, run under an
  address-space cap so a runaway allocation reports the name that caused it.

- **The tree could spell a name differently from `demangle()`, in three ways.** The two
  builders read one parser precisely so that they cannot, and each of these was a place
  where one of them knew something the other did not.

  A pack whose one member is an empty pack was empty to `SpellingBuilder` and a pack of
  one to `AstBuilder`, so `_Z1fIJEJT_EiEviT0_N2nn2UpE` printed `f(int, , nn::Up)` through
  the tree and `f(int, nn::Up)` through the text. A pack's *arity* is what an expansion
  ranges across, so the two also disagreed about how many parameters a signature has.

  `array`, `member_pointer` and `vendor_qualify` never learned to distribute over a pack
  -- `_wrap` does it for pointers, references and cv-qualifiers, and those three do not
  go through `_wrap`. Over an empty pack that printed a declarator on nothing: ` [3]`,
  ` enable_if`, ` ::*` for a parameter that is not there.

  Free Pascal's `build` finds everything the spelling puts in front of the qualified
  name by looking for that name in the finished text. A program's unit carries a `P$`
  that the spelling drops, so the search found nothing and the whole lead went with it:
  `U_$P$XLIB_$$_PX_OPEN_F` rendered `XLIB.PX_OPEN_F` where the text path says `program
  variable XLIB.PX_OPEN_F`.

  All three were found by fuzzing, and the test that would have caught any of them is
  now in `tests/test_architecture.py`: the invariant every scheme shares, checked over
  every corpus at once in both styles rather than each scheme over its own. 81,496
  names, zero disagreements.


- **Go: a package path that did not decode to text came back as a string that cannot be
  written out.** `unescape_path` is a faithful port of `objabi.PrefixToPath`, which works
  on bytes; `%89` is a valid escape and decodes to a byte that is not UTF-8, so the result
  reached the caller as a lone surrogate. `demangle()` is documented never to raise, and a
  caller reads that as a promise it can print or serialise the answer -- but
  `"example.com/x/pkg\udc89.Foo".encode("utf-8")` raises `UnicodeEncodeError`, one step
  later and somewhere else. `PathToPrefix` only produces such a name from a path that was
  not text to begin with, which the module system does not permit, so the symbol is now
  refused and comes back unchanged. `escape_path` and `unescape_path` keep their byte
  fidelity, because the round-trip property rests on it.

  The invariant is asserted for every scheme rather than only for Go, in
  `tests/test_robustness.py::TestTheResultIsWritable`: whatever `demangle()` returns must
  encode. Found by offering 60,000 random ASCII strings to the whole registry and looking
  at what came back changed.

- **Pre-Itanium C++ claimed ordinary C symbols.** `g_cclosure_marshal_VOID__INT` came
  back as `g_cclosure_marshal_VOID(int0_t)` and `PyInit__csv` as
  `PyInit(char, short, void)`. Both are naming conventions an analyst meets constantly --
  GLib's generated marshallers are in every GTK binary, and `PyInit_<module>` covers every
  CPython extension whose name begins with an underscore -- and both spellings are things
  no C++ declaration contains.

  Neither is a defect in the grammar, and the grammar is unchanged. `int0_t` is what
  libiberty prints when `I` is followed by something that is not hex: `demangle_fund_type`
  copies at most two characters, runs `sscanf("%x")` over them and prints `int%u_t`
  whatever happened. `_hex_prefix` reproduces that deliberately, and
  `demangle_strict(name, language="gnuv2")` still does, bug for bug, because that
  faithfulness is what the 1,324-vector libiberty corpus measures.

  What changed is *detection*. `_plausible` already refuses `int (CGuiWidget::)(...)` on
  the ground that a spelling which cannot be a declaration is not a reading; two more of
  the same kind join it -- a parameter list containing `int0_t`, and `void` used as one
  parameter among several. Neither costs anything on libiberty's corpus, where no vector
  has either shape. The CodeWarrior scheme reads the same run of type letters out of the
  same C names and picked up `f__Fcsv` as soon as `gnuv2` stopped, so it holds the `void`
  rule too.

  Over the 345,601 symbols in every shared library a stock Ubuntu 24.04 ships, the
  schemes' false claims go from 8 to 3. The three left -- `PyInit__lldb` reading as
  `(long, long, double, bool)`, `PyInit__sre`, and `drm_intel_gem_bo_map__wc` as
  `(wchar_t, char)` -- are well-formed v2 encodings of well-formed parameter lists, so no
  plausibility rule separates them from a real pre-Itanium symbol. That is why binutils
  2.42 no longer offers `gnu-v2` in `--format` at all; it is recorded under heading 0 of
  ROADMAP.md and in issue #6 rather than guessed at.

- **A `<template-param>` recorded as a substitution candidate was frozen to the wrong
  argument**, and so was anything built over one. ABI 5.1.10 makes a `<template-param>` a
  candidate in its own right, and the entry it contributes is *the parameter* -- `T_`,
  `T0_` -- not whatever argument was bound to it at the point the entry was made. The two
  differ whenever the back-reference is read under a different template scope, which is
  routine: a name mentioning a local entity writes the enclosing function's signature
  against *that* function's parameters, so a `T_` inside it enters the table, and a later
  `S<n>_` naming that entry belongs to the outer template. The mangler does this because
  it canonicalises a template type parameter by level and index, so `T_` of one template
  and `T_` of another are one node to it and the entry is reused.

  Freezing it produced a type the source disproves, spelled plausibly enough to pass for
  the real one: `std::__insertion_sort<llvm::cfg::Update<llvm::BasicBlock*>*, C>` came out
  taking `llvm::BasicBlock*`, and a generic lambda's `operator()<int>` came out taking
  `auto`. A recorded parameter is now a `ParameterReference`, resolved where the
  back-reference is read; a component built over one is a `DeferredProduction`, kept as
  its input span and read again under the scope in force, because a handle is
  already-built output and there is nothing in it to re-resolve. Re-reading is memoised on
  the entry and the scope -- but not inside a pack expansion, which reads its pattern once
  per member without the scope changing, and where a scope-keyed memo handed every member
  the first one's answer.

  Settled against the manglers, because the two references disagree and one of them is
  wrong. Over the 217,730 distinct Itanium symbols in every shared library a stock Ubuntu
  24.04 ships this changes the answer for **315** names. `llvm-cxxfilt` 18.1.3 differs
  from the corrected answer on 322 of them. GNU `c++filt` 2.42 refuses 227 and reads 95,
  agreeing with the corrected answer on 91; of the four left, three are the
  `std::once_flag::_Prepare_execution` constructor shape, where libstdc++'s own header
  settles it against *both* references, and one is a generic lambda whose own parameter
  reaches it through a substitution, where GNU is right and this is not -- one shipped
  symbol, described under heading 0 of ROADMAP.md.

  Five reduced sources are checked in under `tools/corpus_sources/reference_defects/`,
  compiled with g++ 13.3.0 and clang++ 18.1.3, and the names they emit are pinned
  alongside four shipped symbols in `tests/conformance/itanium-reference-defects.txt` --
  the one corpus here whose expected column comes from the declaration rather than from a
  demangler. `tools/generate_corpus.py` reads that file and excludes those names, so a
  regeneration cannot quietly record a reference's wrong answer again, and
  `tests/test_architecture.py` checks that the excused-name lists in the tool and in the
  suite still say the same thing.

  Consequences for the recorded numbers, all in the direction the evidence points. Four
  names left corpora recorded from llvm-cxxfilt for the new one -- three from the
  regression corpus, taking it to **28 / 28**, and one from the purpose-built llvm-style
  corpus, taking it to **279 / 279**. The gnu-style corpus rises to **299 / 300**, because
  one of the two pinned GNU divergences turned out to be ours rather than a disagreement
  between references. libcxxabi's own corpus goes *down*, 29,923 to **29,914 / 29,928**:
  it is llvm-cxxfilt's test file, so the nine vectors that record its model of this are a
  register of the defect rather than of ours, and they are named one by one in
  `tests/test_conformance.py`.

- **MSVC reported a bound the parse never had.** The parser narrows both of its bounds
  with ceilings of its own -- `min(limits.max_depth, MAX_DEPTH)` with `MAX_DEPTH` 64, and
  `min(limits.max_output, 8 * len(mangled) + 256)` -- and that is deliberate: a level
  there costs several interpreter frames, so capping it low is what stops CPython's own
  recursion limit from ever being the thing that ends a parse. A caller may tighten,
  never widen.

  The *report* read the caller's figure back out of `limits`, so a parse that stopped at
  64 announced `exceeded recursion depth limit of 200000` -- a number never in force,
  naming a limit already far above the ceiling that would change nothing if raised.
  Exactly the confusion the internal `_LimitHit` exists to prevent: a tool deciding
  whether to widen its `Limits` was told to widen something that is not the constraint.
  The bound that fired is now the bound reported.

- **`signature()` did not canonicalise the language.** The scheme was whatever the caller
  wrote, so a documented alias missed the separator table and skipped the scheme-specific
  extraction: `signature("+[A_B andThen:do:]", language="objective-c")` reported the whole
  spelling as `base_name` with `is_function` False, where `language="objc"` reported
  `andThen:do:` in `A_B`. Resolved through the registry now, as `parse()` already did.

- **JNI had no separator.** Its spelling joins with `.`, `jni` was absent from the table,
  and the `::` fallback made `com.example.Foo.bar` one base name with an empty namespace
  -- nothing said, for a scheme that is a path and nothing else.

- **A `Style` object was reduced to its name for the tree builder.** A style composed with
  `style(...)` keeps the base's name, so it was silently served the registered style's
  builder; and one whose name is not registered made `parse()` raise `unknown style` from
  inside the parser, for an object `demangle()` accepted. The builder is held by name only
  when the name really is that style's; otherwise it holds the object. The common path --
  `parse(name)`, whose style is the string `"llvm"` -- is unchanged.

- **An Objective-C method name was two tokens.** `+[Alpha copy_it:]` is a class and a
  selector with a space between them, and the stream filter's token pattern is a run of
  word characters, so it came apart into `+[Alpha` and `copy_it:]`. The bracketed form has
  an alternative of its own now.

  This fixes the tokenising, not the finding: a method reaches `find_symbols` from a
  symbol table in its *mangled* form -- `_i_Alpha319_copy_it_`, which holds no space, no
  bracket and no `+`/`-` -- and that has always been found. The bracketed form is what the
  mangled one demangles *to*, so it spells itself and is declined either way. What was
  wrong was one name being read as two.

- **Template parameters are tracked per level, as the reference tracks them.**
  `TL<k>_<n>_` names a parameter of an *enclosing* template, and the table behind it is
  a stack: level 0 is the innermost `<template-args>`, and each generic lambda and each
  template template parameter declaration opens a level of its own. Held flat, the
  levels overwrote each other and such a reference came out as the numbering it carried
  -- `T`, `T1` -- which names nothing at all. A lambda that *is* the entity being named
  starts from an empty stack; one written inside a type or an expression stacks on what
  is already in scope, which is what lets `T_` and `TL0__` mean different things in one
  signature.

  A level that is not in scope at all is now refused rather than named, as
  `llvm-cxxfilt` refuses it. Inside a `<constraint-expression>` it is still spelled by
  its own mangled text -- `C<T> && C<TL0_>` -- which is what the reference does there
  and for the reason it gives: not every enclosing template is tracked well enough
  inside a constraint to substitute reliably.

- **An expansion over an empty pack now disappears inside an expression too.** `Dp`
  already dropped the argument in a type list; `sp` kept it, so
  `getT<$_5>()()(std::forward<>(fp))` was printed where the reference prints
  `getT<$_5>()()()`. Fixed where the reference fixes it: a member of a comma-separated
  list that prints nothing takes its comma with it, which is one rule covering calls,
  braced lists, placement arguments and a `requires` parameter list alike. Tested by
  size rather than by rendering each member -- rendering made the structured benchmark
  1.4x slower on its own.

- **`sr` accepts template arguments after its type, and no qualifier levels after
  those.** `srN <unresolved-type> <template-args> E <base-unresolved-name>`, which
  Clang emits and the ABI's own grammar does not admit: it writes the arguments inside
  `<unresolved-type>` and requires at least one qualifier level. The arguments also sit
  *outside* the substitution entry the type records, so an `S_` written after one names
  the bare parameter -- recording the templated form instead left later references
  short.

  With these three, libcxxabi's own corpus goes from **29,918 to 29,923 of 29,928**.
  Four of the five left are bare `<type>` manglings refused as symbols on purpose and
  read by `demangle_type()`; the fifth is a self-referential conversion operator that
  neither demangler can spell as a declaration.

- **A vector type was spelled LLVM's way under the GNU style.** `Dv4_i` came out
  `int vector[4]` for both styles; `c++filt` writes `int __vector(4)`, which is how GCC's
  own diagnostics spell it. SIMD code mangles `Dv` constantly, so this was not an obscure
  corner. Carried by a new `gnu_vector_spelling` option, as the other divergences are.

- **An array bound was always spaced off its element type.** `A3_Dv4_i` came out
  `int vector[4] [3]` where `llvm-cxxfilt` prints `int vector[4][3]`: the reference omits
  the space whenever what it last printed already ended in a bracket, and only the
  multi-dimensional case of that rule was implemented. What comes before the bound moves
  as declarators nest -- a `const` or a `*` goes in between -- so the decision is re-made
  by every constructor that changes the left half, which is what gets
  `int vector[4] const [3]` and `int vector[4] (*) [3]` right as well.

- **`demangle_type` no longer invents `auto` for a template parameter.** In a whole
  symbol an unresolved `T_` is spelled `auto`, which is what both references do and what
  generic lambdas depend on -- a return type is encoded ahead of the arguments that bind
  it. A bare type has no enclosing template and can never acquire one, so there `auto`
  would name a type that is not in the encoding; `c++filt -t` refuses these and this now
  does too.

- **Delphi: a `%` (or `$`) where a type was expected hung the parser.** `copy_type`
  treated those terminator letters as a no-op and did not advance, so `copy_args` called
  it forever on names such as `@foo$q%`. They are now refused as an unknown type.
  `Limits.max_depth` and `Limits.max_substitutions` are counted through `copy_type` /
  `copy_name` and the argument back-reference table, raising `LimitExceeded` like the
  other schemes. A function-pointer parameter (`double (*)(float, int)`) is one tree
  child; splitting the spelling on every `", "` had made it two.

- **Two Itanium gaps, found by checking against the references over whole shipped
  libraries rather than over the recorded sample.** The library now agrees with
  `llvm-cxxfilt` on all 264,610 readable C++ symbols in libLLVM, libclang-cpp and
  libstdc++, where it previously missed two.

  `TC` -- the construction vtable, `TC <type> <offset> _ <base type>`, spelled
  `construction vtable for <base>-in-<derived>` -- was simply not implemented, so every
  one in the shipped libstdc++ and libclang-cpp was refused.

  And a `Dp` expansion whose pattern ranges over an *empty* pack was printing one
  argument where it should print none: `std::async` came out with a spurious
  `std::decay<>::type...` in its return type. An expansion has one copy per member of
  the pack it ranges over, so an empty pack means no copies -- and there is no other way
  to tell, because the pattern spells perfectly well on its own and only the emptiness
  of the pack says there are none of it.

- **`parse(name).spell()` reported a stack overflow as `RecursionError`.** `max_depth`
  bounds the parse; rendering the tree afterwards is a second walk with frames of its
  own, so a tree well inside the limit could still be deeper than the interpreter's
  stack. The documented contract is that these entry points raise `DemanglingError` and
  nothing else, so it now comes back as `LimitExceeded`. `walk()` no longer recurses at
  all, so `find()` works on a tree too deep to render. Found by an adversarial
  differential over a million inputs; it was there before this release's performance
  work and is not caused by it.

- **A Rust symbol carrying a literal non-ASCII character is refused, as the reference
  refuses it.** Both manglings are checked for it, and the check was written as a
  per-character `ord(c) & 0x80` test -- which is not the rule. The reference reads the
  symbol as *bytes*, so U+0100 fails it as two bytes that both have bit 7 set, while as
  one character its value has bit 7 clear and it passed here: `_RC1\u0100` came out as
  `\u0100` where `rustc-demangle` echoes it back unread. Neither mangling ever carries a
  non-ASCII character literally -- v0 spells one in punycode, the legacy scheme writes
  `$u0100$` -- so a name that does is not a Rust symbol. Measured over 8,000 symbols with
  a non-ASCII character spliced in: agreement with the reference goes from 5,954 to
  7,932, and nothing that agreed before disagrees now.

- **Substitution numbering for a template template parameter application.** A
  `<template-param>` used as the base of a template application was not recorded as a
  substitution candidate in its own right, so every later back-reference in such a name
  was one short. `templateTemplate<outer::inner::Holder, int>(int)` came out as
  `(outer::inner::Holder<int, 3>)`, and the name g++ and clang++ actually emit for the
  same declaration was refused outright.

  The behaviour had been settled by probing `llvm-cxxfilt` 18, which is wrong here --
  LLVM changed its own answer between 18 and 20. It is settled now against the manglers:
  g++ 13.3 and clang++ 18.1.3 both emit `S5_` for the parameter, which is only reachable
  if the parameter took an index of its own. One of the three pinned GNU divergences is
  resolved by the fix, and the corpus expectation that had recorded LLVM 18's answer is
  corrected.

- **Every scheme now enforces `Limits` the same way, and enforces it while building.**
  Bounds that were checked after a string had been assembled cannot stop the assembly:
  the Rust printer, the Swift image loaders and punycode decoder, the D printer and the
  Delphi parser all bound their output as they write it now. A nested `Dp` pack expansion
  in an Itanium name cost the product of the arities -- 61 bytes took 3.3 seconds, and
  grew exponentially -- and is charged for the type productions it actually performs.

- **Three ways of answering with something the symbol does not say**, all removed: a
  Rust name that demangled to the empty string, a Rust parse that consumed only a prefix
  of the input and reported the rest as read, and the Itanium `on` operator inventing a
  type it had not been given.

- **A thread-unsafe Rust parser and a cubic Objective-C search.** The Rust demangler kept
  parser state on a module-level object; the Objective-C GNU-family reader enumerated its
  readings in O(n^3).

- **The result cache is keyed on what changes the answer**, so a style registered after a
  name was demangled cannot be served the older spelling.

- **Running out of stack is reported as the bound it is** rather than as `RecursionError`,
  which the documented contract says cannot escape.

- **Itanium spelling**: exception specifications, cv-qualifier order, abi tags, bracket
  placement, a lambda's own parameters, `auto`, conversion types and pack patterns read
  where they belong, and only a declaration's own name suppressing its return type.

- **GNU style now reproduces `c++filt`'s angle spacing** and its C99 complex spelling,
  and brackets a callee only where `c++filt` does. Over the 44,049 C++ symbols in the
  shipped libLLVM the two now differ on 31, and on every one of those this matches
  `llvm-cxxfilt` exactly.

- **D**: every construct libiberty's own corpus exercises, and its output bounded while
  it is built. The spellings that closed the last 73 have no counterpart in the D ABI and
  were derived by running `c++filt --format=dlang` over the input space: the five
  characters it names inside a string (`\a` and `\b` are not among them, and neither `"`
  nor a backslash is escaped at all), the different rule for a character literal, hex
  float and complex values, associative-array values written as pairs where the type says
  so -- through a back reference, if that is how the type was written -- struct and
  function-literal values, `extern(Pascal)`, the anonymous and `__S<n>` path components it
  leaves out, and the malformed template instances it refuses rather than printing back.

### Conformance

Measured against the reference projects' *own* corpora, all pinned in both directions so
a number can only go up and cannot quietly stop being accurate.

- **Itanium**, libcxxabi's `DemangleTestCases.inc`: 29,728 to **29,918 of 29,928**.
- **Swift**, `test/Demangle/Inputs/manglings.txt`: 457 to **513 of 513**, with no name
  answered by a *different* spelling at any point along the way.
- **Rust**, rustc-demangle's own vectors: 38 to **47 of 51**.
- **D**, libiberty's `d-demangle-expected`: 293 to **366 of 366**.
- Four productions taken from libcxxabi's own parser rather than from the ABI document,
  which describes none of them the way the reference reads them: a block written in a
  C++ function (`___Z3foov_block_invoke`, which the Objective-C scheme had been claiming
  and spelling without reading the enclosing name); a vendor qualifier and the
  cv-qualifiers under it as *one* substitutable component, so `S0_` in
  `_Z1fPU3AS1KiS0_` names the pointer and the second parameter keeps it; a vendor
  qualifier written after the whole declarator, `void () block_pointer`; a built-in
  abbreviation carrying ABI tags becoming substitutable where the bare abbreviation is
  not; and a lambda written as a template argument, `X<[](){...}>`.

- **GNU style** against `c++filt` 2.42 over libLLVM's 44,049 C++ symbols: 1,419
  differences to **31**, and from there -- see Added, above -- to **none**: every name
  `c++filt` reads in that library now comes back byte for byte as it spells it. A
  further 97 names it refuses outright and this reads.

### Changed

- `demangle()` output is unchanged for every symbol. This was checked against a snapshot
  taken before the work started, not asserted.

## [0.1.0] -- initial release

First extraction of the demanglers developed inside
[SMDA](https://github.com/danielplohmann/smda) into a standalone library, rebuilt around
a scheme-agnostic core.

### Added

- **Itanium C++ ABI** demangler, written from the specification. Parses to a structured
  tree.
- **MSVC** decorated-name demangler, also structured, with its own node kinds and
  renderer because its declarator spelling genuinely differs from the C-family one.
- **Rust** legacy (`_ZN`) and v0 (`_R`) demangling, including punycode identifiers and
  v0 structural const arguments.
- **Symbol-table decorations** — ELF version suffixes and compiler clone suffixes —
  handled as structure rather than as each grammar's problem.

### Conformance

Every checked-in corpus is exact against its reference, and so are whole symbol tables
read from shipped binaries — about 112,000 real symbols:

| Source | Reference | Exact |
|---|---|---|
| `libLLVM.so.18.1` | `llvm-cxxfilt` 18.1.3 | 44,186 / 44,186 |
| `libclang-cpp.so` + Polly + LTO | `llvm-cxxfilt` 18.1.3 | 41,140 / 41,140 |
| Rust toolchain | `rustc-demangle` 0.1.28 | 20,697 / 20,697 |
| `libstdc++.so.6` | `llvm-cxxfilt` 18.1.3 | 5,913 / 5,913 |
| Rust, both schemes | `rustc-demangle` 0.1.28 | 5,316 / 5,316 |
| MSVC (LLVM's own corpus) | `llvm-undname` 18.1.3 | 609 / 609 |
| Purpose-built C++ | `llvm-cxxfilt` 18.1.3 | 278 / 278 |

The three GNU-style shortfalls are disagreements between the two references about
substitution table contents, pinned by name.
- A **builder protocol** so one parser serves both a fast text path and a structured AST
  path without a second implementation to keep in sync.
- **Plugin registry** with `demangle.languages` entry-point discovery, so a separate
  distribution can add a scheme without patching this one.
- **Output styles** (`llvm`, `gnu`) for the places where the reference implementations
  legitimately disagree.
- `demangle` command-line tool.
- Conformance, property-based, fuzz, robustness and architecture-boundary test suites;
  reproducible benchmarks with a regression gate that also fails when a benchmark gets
  faster by doing less work.
- API reference published from docstrings at <https://r0ny123.github.io/demangle/>.

[Unreleased]: https://github.com/r0ny123/demangle/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/r0ny123/demangle/releases/tag/v0.1.0
