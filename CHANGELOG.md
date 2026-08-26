# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
  differences to **31**, every one of which matches `llvm-cxxfilt` instead. A further 97
  names `c++filt` refuses outright and this reads.

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
