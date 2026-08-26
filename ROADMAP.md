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

**Itanium**, libcxxabi's `DemangleTestCases.inc`: 29,910 of 29,928 exact. Twelve
refusals and six wrong spellings, in four groups.

- Four are `<type>` manglings with no `_Z` prefix at all — `i` for `int`,
  `PKFvRiE` for `void (*)(int&) const`. `__cxa_demangle` reads them; `llvm-cxxfilt`
  refuses them and so does this, deliberately: a demangler offered every symbol in a
  binary and willing to read `i` as `int` will rename half a C library.
- Four are `_block_invoke` names whose enclosing function is itself Itanium-mangled.
  The Objective-C scheme claims them and spells them its own way, `block #1 in ...`,
  without reading the enclosing name; the reference spells them
  `invocation function for block in ...`. Closing it means deciding which scheme owns
  the shape.
- Six need per-level template parameter tracking: `TL<level>_<index>_` inside a
  generic lambda's own parameter list, where the parameter belongs to a template two
  levels out. This tracks one level.
- The rest are single shapes: a `cp` call inside a `decltype` whose arguments come from
  an enclosing pack, and a variadic-generic conformance path.

**D**, libiberty's `d-demangle-expected`: 293 of 366. The remainder is the older
mangling libiberty still reads and current DMD and GDC no longer write.

**Swift**, `test/Demangle/Inputs/manglings.txt`: 505 of 513, and *no* name in it
answered with a different spelling — every failure is a refusal. The eight are new
function-signature specialisation kinds, variadic-generic conformances, macro expansion
source locations, and one opaque-return-type shape. `simplified-manglings.txt` is a
whole output mode this does not have; see section 3.

**Rust**, rustc-demangle's own `#[test]` vectors: 47 of 51. Three of the other four are
its `{:#}` "no hash" mode rather than the `{}` this prints, and the fourth is a legacy
name with no leading underscore, no hash and no `$...$` escape, which this declines
because `ZN` is a perfectly ordinary start to a C identifier.

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

Nothing is left under this heading that an analyst actually meets. The last one was
Delphi:

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

One is left, and it is Swift's:

- **Swift's simplified manglings** — `test/Demangle/Inputs/simplified-manglings.txt` is
  the same names printed with module qualifications, generic signatures and the argument
  labels dropped: `Foundation.FileHandle.readToEnd() throws -> Foundation.Data?` becomes
  `FileHandle.readToEnd()`. It is what `swift-demangle --simplified` prints, and what an
  IDE shows in a stack trace. This has no such mode. It belongs in the Swift printer as
  an option object the way the Itanium scheme carries `GNU_OPTIONS`, not as a pass over
  the text: dropping a module qualification needs to know which run of characters *was*
  the module, and after printing nothing does.
