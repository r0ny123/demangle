# Roadmap

What is not done yet, in the order it matters. Each entry says what would have to
change, so anyone can pick one up.

Conformance gaps are **not** listed here any more, because there are none of ours left:
every checked-in corpus is exact against its reference, and so are whole symbol tables
from libLLVM, libclang-cpp, the Rust toolchain, libstdc++, the Swift runtime, libgphobos
and the Free Pascal runtime — about 450,000 real symbols. The three GNU-style shortfalls
are disagreements *between the two references* about substitution table contents, pinned
by name in `tests/test_conformance.py`. The seven Nim shortfalls are that language's own
mangling discarding an underscore, listed by name in
`tests/conformance/nim-lossy.txt`.

## 1. More schemes

The plugin interface exists so these need no core changes. Go landed this way, without
touching `core` at all.

D, Swift, Nim and Free Pascal have all landed the same way. What each is measured
against differs, and the difference is the interesting part:

- **D** — 100% against GNU binutils' `c++filt --format=dlang`.
- **Swift** — exact against `swift-demangle` 5.10.1 over the whole shipped runtime and
  the compiler's own test corpus, in both the current mangling and Swift 3's.
- **Nim** — no reference demangler exists, so the property is that re-mangling what is
  read reproduces the symbol, plus agreement with the name the compiler recorded in its
  own `.ndi` files.
- **Free Pascal** — no reference demangler either; the property is re-assembly, over all
  236,570 readable symbols in the shipped runtime, plus a check against `ppudump`.

What is left, ordered by how often an analyst actually meets it:

- **Borland/Embarcadero Delphi** — a *different* scheme from Free Pascal's, written
  `@Unit@Class@Method$qqrv`, and what a Delphi-built PE's package exports carry. Not
  implemented, and deliberately: there is no Delphi compiler and no reference demangler
  to check against on any platform this is developed on, and every other scheme here was
  settled by measurement rather than by reading a specification. Anyone with a Delphi
  toolchain, or a corpus of Delphi-built BPLs with known contents, could close this.
- **Objective-C** — macOS, and barely mangled: mostly `+[Class method]` forms.
- **Swift's ObjC-runtime forms** beyond `_Tt`: the `$s` mangling covers everything the
  compiler emits, but a symbolic reference points into the binary's own metadata and
  cannot be resolved from a name alone. Reading one would mean an API that takes the
  binary too.

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
- **Inlining the detection call into `demangle`** — *rejected*. Detection is now 10.4% of
  an Itanium name; roughly 1.5% of that is the `_claims` frame itself. Recovering it
  means duplicating the symbol-table-decoration fallback at the call site, and that rule
  is one that has to stay in one place.

Nothing is left under this heading. The next thing worth measuring is `parse()`, which
has had the same attention only once.
