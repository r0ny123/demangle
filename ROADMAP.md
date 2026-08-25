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

### D — implemented, not registered

`src/demangle/schemes/d/` reads **88.4%** of the 16,333 symbols GNU binutils'
`c++filt --format=dlang` can read across the shipped `libgphobos` and `libgdruntime`, and
spells **327** of them differently. It is deliberately *not* registered as a language, so
`demangle()` will not use it: every other scheme here is at 100% on its real-binary
corpus, and one that mis-spells two names in a hundred would put exactly the
plausible-but-wrong output this library treats as worse than silence in front of a caller
with no way to tell. Refusing is safe; guessing is not.

It never raises — checked over all 19,315 symbols — and `tests/conformance/d-real-world.txt`
pins what it reads exactly, so the figure cannot go down while the rest is finished.

What is left, by how much it costs:

- **1,109 refusals from `expected a number`** — the largest single group, and not yet
  diagnosed to one cause.
- **327 mis-spellings.** These matter more than the refusals: a refusal returns the symbol
  unchanged, a mis-spelling does not.
- **169 array/struct literal template values** (`V...A`, `V...S`), which need the value
  decoded rather than just its type.
- **163 `H` template argument markers** and **97 implausible length prefixes**, both
  likely one grammar production each.
- 393 names the *reference* cannot read but this scheme answers. Whether that is
  over-acceptance here or a gap there is unresolved; the LLVM findings above are a
  reminder that it can be either.

### Others

- **Swift** — currently needs the `swift` binary; a pure-Python reader would be a first.
- **Delphi**, **Objective-C**.

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
