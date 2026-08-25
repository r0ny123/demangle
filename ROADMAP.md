# Roadmap

What is not done yet, in the order it matters. Each entry says what would have to
change, so anyone can pick one up.

Conformance gaps are **not** listed here any more, because there are none of ours left:
every checked-in corpus is exact against its reference, and so are whole symbol tables
from libLLVM, libclang-cpp, the Rust toolchain and libstdc++ — about 112,000 real
symbols. The three GNU-style shortfalls are disagreements *between the two references*
about substitution table contents, pinned by name in `tests/test_conformance.py`.

## 1. More schemes

The plugin interface exists so these need no core changes. Go landed this way, without
touching `core` at all.

D landed the same way, and is registered: 100% against GNU binutils'
`c++filt --format=dlang` over every symbol it can read in the shipped `libgphobos` and
`libgdruntime`.

Ordered by how often an analyst actually meets them, which is not the order this list was
originally in — Go and Rust are the languages modern malware is written in, and D is not:

- **Nim** — real and rising in loaders and droppers (NimzaLoader, Nimbda). Was missing
  from this list entirely, which was an oversight: it is a more common sight in a sample
  than D or Delphi.
- **Swift** — macOS and iOS. `swift-demangle` ships with the toolchain and is a usable
  oracle.
- **Delphi** — banking trojans and a long tail of older families, plus packers.
- **Objective-C** — macOS, and barely mangled: mostly `+[Class method]` forms.

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

What is left:

- Rust is the slowest scheme by a distance — 76us a name against 44us for Itanium and
  3.6us for MSVC, after a 23% improvement from removing per-production `contextlib` use
  and hoisting `len()` out of the reader's inner loops. The remaining cost is spread
  across `eat`, `ident` and `integer_62` rather than concentrated anywhere.
- A profile-guided pass over the Itanium parser, which has had none.
