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

- **`tools/mutate.py`, which damages real symbols and asks the reference about the
  wreckage.** `tools/enumerate.py` counts short strings, and that is the wrong length
  for everything that only appears once a name is long: a substitution referring back to
  a component built earlier, a template argument list three deep, a return type that is
  itself a function pointer. No alphabet is small enough to reach those by counting.
  This starts from the 640,892 checked-in symbols instead and truncates, deletes,
  duplicates, transposes, substitutes and splices them, so a mutant lands *near* the
  emitted space rather than in the grammar's cheap corners. It shares `ACCEPTED` with
  the enumerator -- those rules say why a reference's answer is not evidence, and that
  does not depend on how the name was found -- and adds two mechanisms of its own:
  `RESCUE`, which asks the reference about a substitute name where it cannot read the
  one in hand for a known reason, and `SECOND_OPINION`, which asks another reference
  about the same name. The draw is seeded, so a failure reproduces exactly, and the
  divergence count is pinned in both directions rather than driven to zero -- the three
  still open are named in the tool's docstring with why each is. Twenty-eight defects came
  out of the first three sittings with it, in four schemes; all twenty-eight are below.

- **`tools/rustc-demangle-reference/`, so Rust has its own reference on this machine.**
  `llvm-cxxfilt` and `c++filt` each carry their own Rust reader -- LLVM's is a port of an
  older `rustc-demangle`, binutils' is independent of both -- so neither of them is the
  implementation `src/demangle/schemes/rust/` is a port of, and where the three disagree
  neither settles it. This is a twenty-line front end over the crate itself, answering
  one spelling per line the way the C++ demanglers do; `tools/enumerate.py` and
  `tools/mutate.py` use it when it has been built and fall back to `llvm-cxxfilt` when it
  has not. Against `rustc-demangle` 0.1.28 this library now spells 5,752 of the 5,753
  distinct Rust symbols in the corpora identically, and the one that differs is the
  `Display` impl reporting a trailing `@@16` separately rather than printing it --
  `rustfilt` prints `foo@@16` for that name too.

### Fixed

- **An MSVC corpus a compiler wrote.** `msvc-llvm-corpus.txt` is LLVM's own *test* file
  -- vectors somebody chose -- and every MSVC corpus here was derived from it.
  `tools/corpus_sources/msvc/msvc.cpp` is compiled instead, by
  `clang++ --target=x86_64-pc-windows-msvc` at four standards and two optimisation
  levels: 123 real symbols, with vftables, RTTI records, thunks through multiple and
  virtual inheritance, guards, the dynamic initialiser and atexit stubs, the anonymous
  namespace, local scopes and the extended integer types. Two defects came out of its
  first run, both in shapes LLVM's vectors do not carry; both are below.
  `tools/generate_corpus.py` grew `--sources`, `--target`, `--compiler`, `--prefix` and
  `--defects` to do it, and `tests/conformance/msvc-reference-defects.txt` holds the
  three names from that run `llvm-undname` cannot read at all.

- **MSVC: a dynamic initializer's name may be qualified.** `demangleInitFiniStub` reads a
  whole declarator and hands its name to the stub, so `??__Eg@inner@outer@@YAXXZ` is
  `` `dynamic initializer for 'outer::inner::g'' ``. This read the leading identifier and
  refused anything after it -- which is every namespace-scope object with a non-trivial
  constructor, and every function-local static, whose scope is written the same way. The
  qualified name goes *inside* the quotes, where the reference puts it.

- **Itanium: `sizeof...` writes an ellipsis after an operand that is not a pack.**
  `SizeofParamPackExpr` prints its operand through a pack expansion, and an expansion
  that finds no pack in what it printed writes a `...` after it -- the same rule that
  makes `sp fp_` read `fp...`. So `sZ` over a parameter bound to a pack spells the
  members, and over anything else spells the operand and an ellipsis:
  `sizeof...(int...)` for a `T_` bound to `int`, and `sizeof...(T...)` inside a
  requires-clause, where the parameter is spelled by its own mangled name and nothing is
  bound at all. This wrote neither. Clang emits the second for any constrained variadic
  template.

- **The Itanium corpus at C++20 and C++23.** `tools/corpus_sources/modern23.cpp` adds
  constrained templates and requires-clauses, coroutines, the defaulted spaceship,
  abbreviated function templates, `auto` and class-type non-type template parameters,
  deducing `this`, the multi-argument subscript and the static call operator; `c++23`
  joins the standards every source is compiled at. `itanium-real-world.txt` goes from
  279 names to 318 and the GNU-style one from 300 to 311, with coroutine frames,
  `Tk`-constrained parameters, `Q` requires-clauses, `constinit` and `thread_local`
  among what is new. The `structured` benchmark walks that corpus, so its baseline is
  re-recorded over 636 names rather than 558; the mutation pin moves with it too, because
  the same file is a mutation seed and a bigger seed set is a different draw.

- **Itanium: a constructor repeated a class name cut short.** `Foo<int>::Foo` drops the
  template arguments and the ABI tags the class name carries, and the cut was made by
  searching the *spelling* for the first `<` or `[`. Every class named by an operator has
  one of those inside its name, so `_ZNssC1Ev` came back as `operator<=>::operator()` and
  `_ZN1XixC1Ev` as `X::operator[]::operator()` -- a constructor of a class the encoding
  does not mention. The name is now taken from the component that was read, before any
  template arguments attached to it, so nothing has to be recognised in the text.
  `llvm-cxxfilt` reads all of these in full; GNU `c++filt` refuses most and answers
  `X::operator[]::X()` for the one it reads. Found by mutation, and the same change fixes
  the reverse ordering in an inheriting constructor -- `CI <variant> <base>` read its base
  type before the class name, so a base that was itself a nested name left its own last
  component standing where the class should be, which is what `c++filt` does today.

- **Itanium: the `F` friend marker was read and then dropped.**
  `<unqualified-name> ::= F <name>` says the function was declared inside the class it is
  a friend of. It was spelled for a source name and an operator, and discarded for a
  constructor, a destructor and an unnamed type -- so `_ZN1AFC1Ev` came back as `A::A()`,
  a different declaration from the one the encoding spells. The two references put the
  marker in different places, so both are followed: `llvm-cxxfilt` writes the word before
  the name, `A::friend f()`, and GNU `c++filt` a bracketed suffix after the name and its
  ABI tags but before its template arguments, `A::f[abi:xyz][friend]<int>()`. That second
  spelling is the new `gnu_friend_spelling` option, on in the GNU style.

- **Itanium: a fold expression printed llvm's spacing under the GNU style.** GNU `c++filt`
  writes no spaces around the operator or the ellipsis -- `(...+(1, 2))` -- and brackets
  the initialiser of a binary fold by kind rather than by precedence, the same rule it
  applies to every other operand, so a literal initialiser gets brackets and a function
  parameter does not: `((9)+...+(1, 2))` and `((1)+...+{parm#1})`. This wrote neither
  half, printing `(9 + ... + (1, 2))` in both styles.

- **Itanium: a cv-qualified function type reached through a substitution.** Both
  references spell one written out as `void () const`; reached through a `<substitution>`
  each contradicts itself -- `llvm-cxxfilt` answers `void  const()`, moving the qualifier
  into the declarator and doubling a space, and `c++filt` answers `void ( const)()`. This
  spells the substituted type the way both spell the written-out one, which is now
  pinned in `tests/test_types.py` and carried as an `ACCEPTED` rule in
  `tools/enumerate.py` with the reason.

- **`tools/invariants.py`: the mutants, put to the library instead of to a reference.**
  Some properties have no reference to ask about -- that a style decides a spelling and
  never whether a name parses, that `parse(name).spell()` is what `demangle(name)`
  returns in every style, that `signature`, `demangleb` and `parse().to_dict()` raise
  nothing but a `DemanglingError` on a name `demangle` read. It reuses `tools/mutate.py`'s
  operators and changes the oracle -- and, because none of this needs a reference, it
  seeds from *every* corpus rather than from the five schemes a reference can be asked
  about, so Swift, Nim, Free Pascal, Delphi, Go, Objective-C and JNI are fuzzed here and
  nowhere else. Each corpus's own characters are the alphabet its mutations draw from.
  The first invariant was broken for twelve corpus names when it was written, and
  `--seed 1 --count 20000 --corpus itanium-libcxxabi` reports it on the parser as it
  stood and nothing on the parser as it is, which is what says the instrument works
  rather than that it is quiet. CI runs it beside the mutation check.

- **Itanium: the output style decided whether a name parses.** GNU c++filt substitutes
  the argument bound to a `<template-param>` inside a requires-clause where llvm-cxxfilt
  spells the parameter by its own mangled name, and that is the
  `symbolic_constraint_parameters` option. Under the GNU style a parameter that resolved
  to nothing -- which is most of what clang emits constraints for, since a clause names
  parameters of enclosing templates that are not all in scope -- refused the whole name.
  Twelve names in the corpora read under `--style llvm` and came back mangled under
  `--style gnu`, `std::pair`'s constrained constructor among them, which is what GCC 13
  emits for the real `std::pair`. What cannot be substituted now falls back to the
  spelling the other style uses, and `tests/test_conformance.py` checks the invariant
  over every corpus: a style is a spelling policy and cannot change what the grammar
  accepts.

- **Itanium: a template-id inside a requires-clause installed a `T_` scope.** A clause
  names no entity, so what it mentions is a type mentioned in passing -- but its
  arguments became the innermost template scope, so in
  `Q ... R 11SmallerThan I Li1234E E T S0_ ...` the nested requirement's `1234` was what
  the *next* requirement's `T_` resolved to, and a type requirement naming `T` printed
  `typename 1234`. Only visible under a style that substitutes rather than spells,
  which is why it surfaced with the fix above.

- **Rust: `_R` alone claimed a name that was not Rust's.** `<symbol-name> ::= _R <path>`,
  and every `<path>` production opens with one of seven letters; the screen tested only
  the prefix, so `detect` answered `rust` for CodeWarrior's
  `__RTTI__40TObjOwnerDerivedFromIObj<12CStringTable>` -- a name in this package's own
  corpus. `demangle` fell through to the scheme that owns it and spelled it correctly, so
  only the label was wrong; `detect` is a public answer of its own, and a caller labelling
  a symbol table gets that answer and no second chance.

- **The text filter rewrote Java annotations, Python decorators and Swift attributes.**
  `@Name` is a real Delphi symbol -- Embarcadero's `tdump -um` reads it as `Name`, and 33
  of them are in the corpus -- and it is also `@Override`, `@property`, `@escaping` and
  `@param`. `find_symbols` and `demangle_text` are run over whole files, so
  `@Override public void f()` came back `Override public void f()`, and a Swift signature
  this library had just printed came back with its `@escaping` and `@autoclosure` shaved
  off, and `typeinfo for X const*@@CXXABI_FLOAT128` came back with `__linkproc__
  CXXABI_FLOAT128` where the ELF version had been -- a version suffix this library prints
  on every versioned symbol. What those readings have in common is that the identifier
  survives them whole: all they add is the marker's name, or nothing at all. The caller
  can see the identifier already and cannot see whether it was an annotation, so the
  filter now declines them. A reading that says more is untouched, which is why the 475
  `._OBJC_CLASS_*` names in the corpus are still found. `demangle()` still reads them:
  there the caller has said the word is a name. Pinned by a test over every corpus --
  seven spellings are still rewritten, all of them pre-Itanium names whose demangling
  contains a component that really is symbol-shaped, and a filter over prose cannot be a
  fixed point in general.

- **Delphi claimed fragments of MSVC symbols.** The parser is a port of Borland's
  `unmangle.c` and copies characters through rather than checking an alphabet, so a `?`
  passed straight into an identifier -- and `?` is *the* MSVC marker, with none in any of
  the 11,363 recorded exports. Where the text filter tokenised
  `??R<lambda_1>@?0??define_lambda@@YAHXZ@QBE@XZ` at the angle brackets its token cannot
  hold, what was left came back as `?0??define_lambda::__linkproc__ YAHXZ::QBE::XZ`: a
  Delphi declaration built out of half an MSVC symbol. `detect` refuses a name carrying
  one now; `language="delphi"` still reads it, because there the caller has said what the
  name is. Six of the ten remaining partial readings over every corpus are what is left,
  all of them MSVC and CodeWarrior names the token cannot hold whole.

- **The text filter cut Delphi names in half.** `find_symbols` and `demangle_text`
  tokenise a line before offering the words to the library, and the token held no `%` or
  `#` -- which Delphi writes for a template argument list and a virtual-method-table
  flag. 638 readable names of the corpora came apart, and the pieces that happened to
  read were handed back as symbols: `@%TAutoDriver$24Shdocvw_tlb@IWebBrowser2%@$bnot$xqv`
  reported `@$bnot$xqv` as `operator !() const`, a real declaration belonging to a class
  the fragment no longer names. Both are in the token now. `<` and `>` are deliberately
  still out, though MSVC writes `<unnamed-type-a>` and `<lambda_0>`: objdump spells a
  call target `call 1050 <_ZN3foo3barEv>`, and a token that takes the brackets in is one
  no scheme reads, so the 99 names they would recover cost the symbol in the listing this
  module exists to filter. Measured both ways round, over the corpora and over synthetic
  `nm`, objdump and crash-log lines.

- **Objective-C: detection cost two thirds of what it was.** The screen asked whether
  any layer of assembler decoration leaves `_i_` or `_c_` at the front, and asked it by
  building the candidate list and running a generator over it -- on every symbol in a
  binary, since this scheme declares no first character. Every strip that production
  makes is one or two characters off the front, so the offsets are written out instead:
  1.13us a name over the shipped libstdc++ becomes 0.43, and detection across all the
  schemes offered a lower-case name drops from 3.51us to 3.15. The predicate is
  unchanged, checked against the list form over 301,313 strings -- every corpus name,
  every string up to five characters over a telling alphabet, and 200,000 random ones.

- **Rust: 234,000 fewer interpreter calls over the Rust corpus.** The three `skip_*`
  productions still kept their depth guard in a wrapper around an inner method, which is
  two frames per production on a pass whose whole job is to validate; `namespace` is one
  character and ran 59,000 times through a frame of its own; three quarters of the 82,000
  `opt_integer_62` calls found no tag and returned zero; and half the 78,000 `Ident`
  objects were built by the skip pass to be thrown away. Guard and body now share a
  frame, `namespace` is inlined at its two sites, the two hot disambiguator sites test
  for the tag before calling, and `ident(False)` validates without allocating. Measured
  by call count -- 2,732,768 to 2,464,124, deterministic -- because this machine's
  wall-clock spread is larger than the change: six interleaved before/after runs put both
  at 75-77us per name with 63-82us of noise around it. The reading is unchanged: the full
  suite passes and 20,000 mutants still agree with `rustc-demangle` exactly.

- **A reference defect the exclusion list was not covering.**
  `_Z16templateTemplateIN5outer5inner6HolderEiET_IT0_Li3EES4_` sat in
  `itanium-real-world.txt` with the corrected spelling and was absent from
  `itanium-reference-defects.txt`, so the next regeneration re-recorded llvm-cxxfilt
  18.1's answer for it -- which is what the first regeneration in a while did. It is the
  same `<template-param>` substitution defect as the other nine, and llvm-cxxfilt 20.1
  and GNU c++filt 2.42 both agree with the declaration. Now excluded, so the protection
  the file exists to give actually covers it.

- **MSVC: four more productions a compiler emits.** `??__M` and `??__L` are
  `operator<=>` and `operator co_await`, both written with the double-underscore prefix
  and neither in the table -- the first is C++20's three-way comparison, which clang
  emits for any class that declares one. A signature ends with `Z` *or* with `_E`, which
  marks it `noexcept`: `demangleThrowSpecification` takes either, and expecting the `Z`
  alone refused every `noexcept` function type, `int (*)(int) noexcept` included. A
  deduced return type may be written as a back reference to an earlier one -- `?A?4@`,
  which is what a lambda nested inside another lambda produces -- and reading the
  identifier directly both missed that form and left the back-reference table one entry
  short for every name after it. And `$M <type> <integer>` is an `auto` non-type template
  argument, where the reference spells only the value: `A<42>`, `A<99>` for a `char`,
  `A<1>` for a `bool`.

  All four came from `tools/corpus_sources/msvc/modern.cpp`, added beside `msvc.cpp` in
  the same run. `llvm-undname` 18.1 refuses the `$M` form, so its expectation is 20.1's
  output and it is pinned by name rather than recorded in the corpus; the two versions
  agree on all 764 other MSVC names here, and CI now cross-checks 16, 18 and 20.

- **MSVC: a member function's qualifiers go inside what its return type wraps.**
  `FunctionSignatureNode::outputPost` writes the parameter list and then the quals, so
  they land inside whatever the return type wraps around the declarator. Appended to the
  declaration instead they came out past the wrapping, and
  `?b7@S@@QEBAAEAY01$$CBDXZ` -- a const member returning a reference to an array, which
  `clang++ --target=x86_64-pc-windows-msvc` emits for two lines of ordinary C++ -- read
  `char const (& __cdecl S::b7(void))[2] const`: a const array rather than a const member
  function. The same for a pointer to an array, a function pointer and a function
  reference. They stay on the declaration node as well, because they are how the symbol
  is reached rather than part of its type, and `signature()` and the tree both read them
  off it.

  Found by asking the compiler rather than the fuzzer: the mutation sweep flagged the
  shape, and the symbol that settled it came off an object file.

- **Itanium: only the first pointer to a protocol-qualified `objc_object` is the word
  `id`.** `U <n>objcproto<protocol> <type>` is `objc_object<A>`, and a pointer to it is
  `id<A>` -- `id` *is* the pointer, so it is not written again. Only the first one. This
  collapsed the unpointed form to `id<A>` as well and then handed the same handle back
  out of every `P`, so `PPU11objcproto1A11objc_object` and `PPPU...` came back `id<A>`
  too, where the reference writes `id<A>*` and `id<A>**`.

- **Itanium: an inheriting constructor carries a variant.** `CI1` through `CI5`, the
  same five an ordinary constructor carries; `parseCtorDtorName` requires the digit and
  `llvm-cxxfilt` refuses `CI0`, `CI6` and `CIT` alike. Taking whatever character stood
  there read `_ZN1BCIT1AEi` as `B::B(int)`.

- **MSVC: qualifiers are written in the reference's order, not the order they were
  read.** `outputQualifiers` tests a bitmask, const first, so a type qualified twice --
  a pointee qualifier and then the variable's own, which `?s4@PR13182@@3PCDD` is -- came
  out `char volatile const *` where the reference writes `char const volatile *`. The
  same for a pointer's own: `__unaligned` comes after `__restrict`, on a pointer and on
  a pointee alike. And the test for a qualifier the type already carries is on the
  *trailing* words rather than on the text: asking whether the word appears anywhere
  found one inside a template argument and dropped a `volatile` that belongs to the
  symbol.

- **MSVC: `extern "C"` goes after `static` and `virtual`, not before.**
  `?fn@@$$J0EAAHH@Z` is `private: virtual extern "C" int __cdecl fn(int)`, and this had
  `extern "C" virtual`. Every `$$J` in the corpora is on a free function or an ordinary
  member, where the two orders are the same string.

- **MSVC: five markers read more loosely than the reference reads them.** `$$J0` is one
  literal and the digit is part of it, so `$$J3` and `$$J4` are not names; the pointer
  ext-qualifier run is an optional `E`, then an optional `I`, then an optional `F`, in
  that order and each at most once, so `IE` is not one either -- and `F` was missing from
  that run altogether, so `?h3@@3QEIAHFA`, `int __unaligned *const __restrict h3`, was
  refused; only a pointer points into a class, so `A8foo@@AEHH@Z` -- a reference to
  member function -- is not a type, and the declarator it produced was not a spelling;
  `demangleVcallThunkNode` consumes `$B` and nothing else, so a `??_9` name carrying a
  vtordisp slot was two thunk kinds at once; and `$$Y` names an alias template only where
  a template argument stands, where this took it anywhere and read
  `?f@@YAX$$YURetVal@@@Z` as a parameter called `URetVal`.

- **MSVC: a dynamic initializer's name ends with the variable it runs for.**
  `demangleInitFiniStub` reads the variable, then the `@` terminators the form requires,
  then the function encoding -- there is nowhere for another component to go. Reading one
  made `??__E?i@C@@0HA@e@@QEAAHXZ` into an initializer inside a namespace `e`, with an
  access specifier and a return type the form does not have either.

- **Rust: a bound lifetime runs to `'z` before it starts counting.**
  `print_lifetime_from_index` takes `depth = bound_lifetime_depth - lt` and writes
  `'a' + depth` while `depth < 26`. This carried a `depth` one larger and undid it at the
  letter, which is the same answer for the first twenty-five and not for the rest: the
  twenty-sixth came out `'_26` where the reference writes `'z`, and every one after it
  was numbered one too high.

- **Rust: the `.llvm.<hash>` marker includes its leading dot.** Searching for `llvm.`
  without it deleted text that belongs to the symbol: `_RNvCs1_1a1fllvm.123` has no
  suffix at all -- the reference refuses it, because `llvm.123` is left over and a
  leftover has to open with a `.` -- and this threw eight characters away to read it as
  `a::f`.

- **D: a back reference points at a length-prefixed identifier.**
  `dlang_symbol_backref` reads a `dlang_number` and then that many characters, so what a
  `Q` points at is an identifier and nothing else -- not a `__T` template instance.
  Reading whatever stood there resolved a mutated index onto a whole instance and spelled
  it as a path component, naming it twice:
  `_D3std5range__T6ChunksTAhZQo5emptyMFNaNbNdNiNfZb` came back
  `std.range.Chunks!(ubyte[]).Chunks!(ubyte[]).empty()`. This was 56 of the 119 shapes
  the fuzzer had this scheme reading and the reference refusing.

- **D: an array bound is written with the digits the name carried.** `dlang_type`'s `G`
  case remembers where the digit run began and appends it verbatim, so `G012a` is
  `char[012]` and re-formatting it wrote a different bound. The same rule as an integer
  literal, in the one place it had not been applied.

- **D: `__postblit` is renamed only on a bare member signature.** The reference writes
  `this(this)` where the type is exactly a `this` parameter and an empty D-convention
  signature, and leaves the name alone otherwise: `MFNaZv`, `MxFZv`, `MOFZv`, `MUZv`,
  `MFiZv`, `UZv` and `FZv` are all `__postblit`. "No attributes" was the first reading of
  the rule and renamed six shapes it does not.

- **D: a generated symbol with no path is still one.** `_D6__initZ` has nothing left to
  name once the marker is taken off, and the reference still writes the prefix.
  Requiring a component before it read the marker as an ordinary name and answered
  `__init`.

- **Itanium, `gnu` style: a comma expression has no space after the comma.** GNU writes
  no space around any infix operator -- `(1)+(2)`, `(1)<(2)` -- and the comma was the
  one this wrote with one, so `decltype ((1), (2))` where `c++filt` writes
  `decltype ((1),(2))`. The `llvm` style keeps `decltype(1, 2)`, which is what
  `llvm-cxxfilt` writes. A comma-*separated list* is a different thing and still has its
  space in both styles.

- **Rust: the `.llvm.<hash>` marker includes its leading dot.** Searching for `llvm.`
  without it deleted text that belongs to the symbol: `_RNvCs1_1a1fllvm.123` has no
  suffix at all -- the reference refuses it, because `llvm.123` is left over and a
  leftover has to open with a `.` -- and this threw eight characters away to read it as
  `a::f`. `find` rather than `rfind` now too, which is `rustc_demangle::demangle`'s own
  choice.

- **Itanium: five of the seven `<prefix>` productions are bases and take no prefix on
  the left.** `<substitution>`, `<template-param>` and `<decltype>` can *open* a prefix
  and cannot follow one -- only `<prefix> <unqualified-name>` and
  `<template-prefix> <template-args>` recurse. Reading them anywhere spelled a scope
  inside a scope that cannot contain it: `_ZN1aSt1bEv` as `a::std::b()`, `_ZN1aSa1bEv`
  as `a::std::allocator::b()`, `_ZN1a1bS_1cEv` as `a::b::a::c()` and `_ZN1aDtfp_E1bEv`
  as `a::decltype(fp)::b()`. Every one is a declaration a person would believe, none is
  a name any compiler writes, and both references refuse all of them. At the front,
  where they occur, all three are untouched: `_ZNSt1a1bEv`, `_ZNSaIwE1bEv`,
  `_ZNSt3maxIiEEvv` and `_ZNDtfp_E1bEv` still read. This was 34 of the first mutation
  sitting's divergences in one shape -- a deleted or duplicated character anywhere in a
  long `_ZNSb...` symbol leaves an abbreviation stranded mid-prefix.

- **MSVC: the ARM64EC marker is removed once, not until none is left.**
  `getArm64ECMangledFunctionName` inserts one `$$h` into a name that has none, and
  `getArm64ECDemangledFunctionName` removes the first and no more -- so a name carrying
  two is not one either function can produce, and what is left after removing one still
  does not read. This recursed through the whole rule instead, stripping markers one at
  a time, so `?f@@$$h$$hYAXXZ` came back `void __cdecl f(void)`.

- **D: an artificial symbol is the one that ends in `Z`.** `dlang_parse_mangle` reads
  `__init`, `__vtbl`, `__Class`, `__interface` and `__ModuleInfo` as generated symbols
  that end with a `Z` and carry no type. The `Z` is what makes one, and eating it only
  if it happened to be there read a truncated `_D10TypeInfo_c6__vtbl` as the whole of
  `_D10TypeInfo_c6__vtblZ`. Refusing to fall through was the other half of it:
  `_D3foo6__vtblFZv` is an ordinary function that happens to be called `__vtbl`, which
  the reference spells `foo.__vtbl()` and this read as nothing at all.

- **D: a `this` parameter's qualifiers end at the first `const` or `immutable`.**
  `dlang_type_modifiers` is not the rule a *type* follows -- there each modifier wraps
  the next, and `xx` is `const(const(int))`. On a `this` parameter or a delegate, `O`
  (shared) and `Ng` (inout) recurse while `x` and `y` return, so at most one of the last
  two appears and it comes last. Reading the run the way a type reads it spelled
  `foo.bar() const const` for `_D3foo3barMxxFZv`, and accepted `MxO`, `Mxy` and `Myy`,
  all four of which the reference refuses.

- **D: an anonymous last component does not lend its type to the one before it.**
  `dlang_parse_qualified` skips a literal `0` with a `continue`, which steps over the
  "consume the encoded arguments" that every other component goes through -- so the
  function type after it belongs to a component the reference leaves out of the
  spelling, and the reference does not spell it either.
  `_D4core4sync5mutex5Mutex6unlock0FNeZv` came back `core.sync.mutex.Mutex.unlock()`,
  which says `unlock` is that function; its `()` is somewhere else. Only the last
  component, and only a literal `0`: an anonymous one in the middle leaves the type
  belonging to the component after it, and a back reference that *resolves* to an
  anonymous component is a component that spells nothing rather than one that was
  skipped.

- **D: a template argument is one of the four the grammar names.** `TemplateArgX` is
  `T Type`, `V Type Value`, `S Number_opt QualifiedName` or `X`, and nothing else. A
  bare symbol name -- a length, a `Q` back reference or a `_D` symbol with no `S` in
  front of it -- was read here as well, on the grounds that a compiler emits one where
  the argument's kind is unambiguous from the grammar. It does not,
  `dlang_template_args` refuses one outright, and no name in either corpus -- 1,257 real
  symbols and libiberty's own 366 vectors -- needs it. What it did instead was turn
  malformed names into plausible ones: a mutant that has lost the `S` from `TSQBi...`
  read the back references after it as further arguments, so a qualified name came apart
  into `PackedArrayViewImpl!(float, std, uni, BitPacked!(uint, 11uL), BitPacked, 16uL)`
  -- five arguments where the name has two. An `S` argument whose qualified name spells
  nothing is refused for the same reason: it took a slot and was spelled as one.

- **Itanium: a `<nested-name>` may not end on a data-member or closure prefix either.**
  `<data-member-prefix> ::= <member source-name> [<template-args>] M` and
  `<closure-prefix> ::= [<prefix>] <unqualified-name> M` are both `<prefix>`
  productions, so an `<unqualified-name>` still has to follow before the `E`: something
  is named inside the member or the closure. The `M` carries no spelling and was
  consumed silently, so `_Z1fN1aME` -- "a member of `a`, and here is which one" -- came
  back as `f(a)`, and `_Z1fNSaME` as `f(std::allocator)`. `c++filt` refuses every shape
  of it; `llvm-cxxfilt` reads the ones whose prefix is a source name and refuses the one
  whose prefix is a substitution, which is the same grammar half-applied. The
  substitution half of this rule shipped in the entry below; this is the other half of
  the same production.

- **D: a pointer to a function pointer lost every level above the first.** D spells a
  pointer to a function as `int(char[]) function` -- the word *is* the pointer -- and
  this decided which `P` was that word by looking at the pointee's *spelling*. So the
  outer `P` of `PPUZi` saw a pointee already ending in `function` and absorbed itself
  too: `PPUZi` and `PPPUZi` both came back `extern(C) int() function`, the spelling of
  `PUZi`. `dlang_type` decides from the character after the `P` -- a calling convention,
  and nothing else -- and so does this now. A real druntime symbol shows it:
  `_d_run_main`'s third parameter is `extern(C) int(char[][]) function*`.

- **D: an integer literal is written with the digits the name carried.**
  `dlang_parse_integer` appends the characters it read rather than the number they
  spell, so a leading zero is part of the literal: `Vki024` is `024u` and `Vmi007` is
  `007uL`. This parsed and re-formatted, spelling `24u` and `7uL`. The same rule governs
  the two hex digits of an unprintable character inside a string, which the reference
  copies out of the name -- `\xB2` stayed upper-case there and was lower-cased here.

- **D: a negative value kept its sign only for the kinds that spell their digits.** The
  `N` that marks one is written whatever the kind is, and the two kinds that spell a
  *value* rather than its digits dropped it with the digits: `VaN17` came back
  `'\x11'` and `VbN1` came back `true`, each the positive literal rather than the
  reference's `-'\x11'` and `-true`. Losing a sign spells a different value; the
  reference's spelling is one D source cannot write either, but that is a separate
  thing.

- **D: a template argument with no name is refused.** A bare `0` is the anonymous
  *scope* inside a path, where the reference writes nothing for it. An argument list has
  no such thing, and the empty string took a slot and was spelled as one: `Vln0` came
  back `!(null, )` and `00` came back `!(, )`, each with a visible empty argument. The
  reference refuses both.

- **Rust: a `<base-62-number>` is sixty-four bits wide.** RFC 2603 writes
  `{<0-9a-zA-Z>} "_"` and states no bound, but `rustc-demangle` -- which this scheme is
  a port of -- accumulates into a `u64` through `checked_mul` and `checked_add`, so a
  field whose digits overrun 64 bits is a parse error there and in LLVM's port of it.
  binutils, whose Rust reader is neither, reads such a field by wrapping and answers
  `a[aa303280a73f210e]::f` for a crate disambiguator that cannot exist. This read it
  too. The boundary is now the reference's exactly, checked against it: rustc writes a
  disambiguator that is a truncated 64-bit hash, so nothing a compiler emits is near the
  edge -- this was found by mutating symbols that a compiler did emit.

- **`_Complex` and `_Imaginary` are not cv-qualifiers, and a repeat of one does not
  collapse.** They go through `qualify`, which under the `gnu` style folds a duplicate --
  correctly, for `const` and friends, because `[basic.type.qualifier]` says so. Nothing
  folds a duplicate `_Imaginary`: `c++filt` 2.42 writes `signed char _Imaginary
  _Imaginary` for `_Z1fGGa` and this wrote one of them, losing a word of the name. The
  two styles disagreed with each other about it, too, since only the gnu one collapses
  at all. The flag rides on the tree node as well as the builder call, so a tree still
  spells what the text path spelled -- which puts a `cv` key on a `qualify` node's
  `to_dict()` and a third entry in its `__match_args__`, both of which a consumer
  rebuilding a tree needs.

- **Three more Itanium shapes neither reference reads.** A `<nested-name>` ending in a
  substitution -- `N ... <prefix> <unqualified-name> E`, and a `<substitution>` is not an
  `<unqualified-name>` -- so `_ZNSaEv` came back as `std::allocator()`, `_ZN1aSaEv` as
  `a::std::allocator()` with a `std::` nested inside an `a::`, and `_ZN1aS_Ev` as
  `a::a()`. A run of internal-linkage markers, where the grammar allows one: `_Z1fLL1A`
  and `_Z1fLLL1A` both read as `f(A)`, the spelling the well-formed `_Z1fL1A` has. And a
  `<template-param>` that binds to nothing -- an *empty* argument list, where
  `_Z1fIET_a` read as `auto f<>(signed char)`, and an index past the end of a real one,
  where `_Z1fIiEvT0_` read as `void f<int>(auto)`. The `auto` fallback belongs to the two
  readings that find nothing bound on purpose, a generic lambda's invented parameters and
  a conversion operator's type read ahead of its arguments, and those are marked by where
  the reading *is* rather than by what is in scope, because a lambda's level and an empty
  argument list are indistinguishable from the tables. That rule also changes
  `_Zcv1BIRT_EIS1_E`, a self-referential conversion operator with no declaration to
  spell: it is refused now, as `c++filt` refuses it, rather than answered
  `operator B<auto&><auto&>`.

- **D: a parameter's storage classes are a sequence, not a set.**
  `[M] [Nk] [I[K] | J | K | L] <Type>`: `dlang_function_args` reads each once, in that
  order, and then reads the type. Written as a loop here, it took any order and any
  number of them -- `FMMfZv` came back as `(scope scope float)` and `FIJfZv` as
  `(in out float)`, neither of which is a parameter anything can declare, and `FNkMfZv`
  reordered `return scope` out of the order the encoding puts it in. `I` is the only one
  that takes a second, `in ref`; `IKK` is not a parameter either.

- **D: a `this` parameter with no function type after it.** `M` marks a member
  function's `this`, so a function type has to follow. `dlang_parse_mangle` sets
  `is_function` on seeing it and calls `dlang_function_type`, which fails without a
  calling convention. This read a plain type instead and dropped the `M`, the modifiers
  and the type with it, so `_D4test3fooMf` came back as `test.foo` -- a variable, out of
  a symbol that says it is a member function. Together with the entry below, this takes
  the enumeration differential against `c++filt --format=dlang` from 74,000 divergences
  over 17 million generated `_D` names to 20, and the entry above takes those 20 to
  none.

- **D: a class, struct, enum or typedef type with no name after it was accepted.**
  `C <QualifiedName>` and its three siblings, where the name is not optional --
  `dlang_parse_qualified` reads at least one symbol name and fails otherwise. This
  joined an empty list and returned `""`, so `_D3fooC`, a variable whose type is a class
  with no name, came back as `foo`, and `_D3fooFCZv` as `foo()` with the parameter
  simply gone. `c++filt --format=dlang` (binutils 2.42) hands back every one. The check
  is on how far the cursor moved rather than on what came out: a zero-length component
  is anonymous and spells nothing, and `_D3fooC0` is a name the reference does read.
  Found by enumerating every `_D` name up to eight characters over a grammar-shaped
  alphabet -- 52,052 of the 260,260 this read were names the reference refuses, and they
  were all this one shape.

- **MSVC: a local static guard's number was read signed.** It counts the static the
  guard belongs to within its function, so it has no negative. `llvm-undname` 18.1 reads
  it unsigned and refuses `??_Bx@@5?0`; this read it signed and answered
  ``x::`local static guard'{-1}``, a scope index that cannot exist. Both the plain and
  the thread form. Found by enumerating 40 million MSVC names and asking the reference
  about each one this reads -- of the 306,000 it reads, this and the four below were all
  that differed.

- **A type in expression position was read as one, and neither reference does that.**
  `_expression` ended with a catch-all: whatever was left that could open a `<type>`, it
  read as one. The comment said array bounds and non-type template arguments arrive
  there; instrumented over the conformance corpora and every Itanium symbol this machine
  ships -- 137,561 names -- it fires exactly zero times, because both of those
  productions read their operand themselves. What it did do was give malformed input a
  spelling: `_Z1fDTaE` came back as `f(decltype(signed char))`, `_Z1fDBa_` as
  `f(_BitInt(signed char))` and `_Z1fAa_a` as `f(signed char [signed char])`, none of
  which are things. `llvm-cxxfilt`'s `parseExpr` has no type alternative at all, and
  `c++filt` reads none of them either -- including `_Z1fDTDTfp_EEv`, a `decltype` nested
  inside a `decltype`, which looks like it ought to work and does not. That one had a
  test vector here, in the depth-limit suite, which now uses `-(-(-fp))` instead: an
  expression nested inside an expression, which is what that test wanted and what both
  references read. The expressions that legitimately mention a type are unaffected:
  `DT T_ E` is still `decltype(int)` where `T_` is bound to `int`, `DT ng ng fp_ E` is
  still `decltype(-(-fp))`, and `st`, `at` and `ti` name their operand's type through
  their own codes.

- **Rust: a non-digit where an identifier's length belongs read as a length of zero.**
  `<identifier>` opens with a decimal length and the reference requires one --
  rustc-demangle reads it as `self.digit_10()?`, so anything else ends the parse. This
  read a non-digit as zero and left the character in place, and the `_` that separates a
  length from its text then swallowed it, so nothing was left over for the residual
  check to refuse. What came back was a name with an empty component spelled as though
  it were there and blank: `_RNvC_1f` is a function in a crate with no name and read as
  `::f`, and `_RNvC1CC_` has an instantiating crate that is not a path at all and read
  as `C`. `llvm-cxxfilt` 18.1 hands back every one of these. A length written `0` is a
  different thing and stays legal -- `_RNvC0_1f` is `::f` to the reference too -- so the
  rule is "not a digit", not "falsy". Found by enumerating every `_R` name up to eight
  characters over a grammar-shaped alphabet and asking the reference about each one this
  reads.

- **A literal with no value invented a zero, and the two `nullptr` literals were one
  spelling under `gnu`.** `<expr-primary> ::= L <type> <value> E`, and the value is not
  optional: `_Z1fILaEE` came back as `f<(signed char)0>`, a zero nowhere in the name and
  the same spelling the well-formed `_Z1fILa0EE` has. `Dn` is the exception, and it is
  decided by the two characters written rather than by what they spell --
  `_Z1fILSt9nullptr_tEE` names the same type the long way round and both references
  refuse it. The two `Dn` forms are also two spellings to `c++filt`: `LDnE` is
  `decltype(nullptr)` where `LDn0E` is `(decltype(nullptr))0`, and both are `nullptr` to
  `llvm-cxxfilt`. This wrote `(decltype(nullptr))0` for both under `gnu`.

- **A `void` that arrived by substitution was mistaken for the `v` that means "no
  parameters".** `f(void)` is how the mangling writes `f()`, and both references read
  that by position: the first signature type, if it is a literal `void`, *is* the empty
  list. This asked instead whether every parameter spelled `void` after the fact, which
  is a different question. `_Z1fIvEvT_` is `template <class T> void f(T)` instantiated
  with `void` -- both references spell it `void f<void>(void)` and this spelled
  `void f<void>()`, dropping a parameter that is in the name and answering to no
  reference at all. So did `_Z1fIvEvPFvT_E` inside a function type, and
  `_Z1fIJEEvDpT_v` where an empty pack precedes the `v`. And a list of several voids is
  not an empty one: `_Z1fvv` is `f(void, void)` to `c++filt` and refused outright by
  `llvm-cxxfilt`, where this said `f()`. Every one of these now matches at least one
  reference, and matches `llvm-cxxfilt` wherever it reads the name at all -- the two it
  does not are the two it refuses.

- **A conversion operator's return type was read out of the input, where it is not.**
  It encodes none however template it is -- what it returns is in its name -- and this
  read a type anyway and discarded it, spending the first type of the signature. That is
  invisible while the type in question is the `v` of an empty parameter list, since
  discarding it and spelling `()` from what remained came to the same thing, and wrong
  the moment the operator takes a parameter: `_ZN1ScviEiv` is `S::operator int(int,
  void)` to `llvm-cxxfilt` and came back here as `S::operator int()`. It shared a flag
  with the return type GNU *omits* on the function a local name is scoped by, which is a
  different thing -- that one is in the input and has to be read before it can be
  dropped -- so the two are separate flags now. Found because the rule below refused
  seven libcxxabi vectors that had been passing on the two errors cancelling.

- **A template parameter with no arguments in scope was read as `auto`.** `T_` indexes
  the enclosing `<template-args>`, and a plain function has none, so `_Z1f1AT_` came
  back as `f(A, auto)` -- a declaration a reader would believe, of a type the encoding
  does not contain. Both references hand it back. The `auto` fallback is right for the
  two readings that legitimately find nothing bound, and both of those have something in
  scope: a generic lambda occupies a level even when it declared no parameters, and a
  conversion operator's type is read ahead of the arguments that bind it. Over the
  226,402 names of the corpora plus every Itanium symbol this machine ships, exactly one
  reaches that fallback, and it is a generic lambda.

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

- **A function signature with no parameter types was read as a declaration.**
  `<bare-function-type> ::= <signature type>+` is one or more, and a template
  specialisation spends its first type on the return type -- a plain function encodes
  none, because overloads cannot differ by it. So `_Z1fIiEi` has a return type and then
  nothing, which is not a declaration of anything; both `c++filt` 2.42 and
  `llvm-cxxfilt` 18.1 hand it straight back, and the reference reads the production as a
  do-while for exactly this reason. It came back here as `int f<int>()`, which is what
  the well-formed `_Z1fIiEiv` says -- two manglings spelled as one name, and one of them
  was not a mangling. The rule counts *types read* rather than parameters kept, because
  a parameter list can legitimately end up empty after being read: `v` on its own is how
  the grammar spells `()`, and an empty pack expansion drops out. Byte-identical on all
  563,331 real symbols. Found by enumerating every Itanium name up to five characters
  over a grammar-shaped alphabet and asking both references about each one this reads.

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
