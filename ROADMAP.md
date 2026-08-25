# Roadmap

What is not done yet, in the order it matters. Each entry says what would have to
change, so anyone can pick one up.

Conformance gaps are **not** listed here any more, because there are none of ours left:
every checked-in corpus is exact against its reference, and so are whole symbol tables
from libLLVM, libclang-cpp, the Rust toolchain and libstdc++ — about 112,000 real
symbols. The three GNU-style shortfalls are disagreements *between the two references*
about substitution table contents, pinned by name in `tests/test_conformance.py`.

## 1. Reference diversity

Everything is measured against exactly one build of each reference: `llvm-cxxfilt`
18.1.3, GNU `c++filt` 2.42, `llvm-undname` 18.1.3, `rustc-demangle` 0.1.28. That catches
our defects; it does not catch a reference's.

- A matrix over several LLVM and binutils versions, so a reference changing its own
  output shows up as a diff rather than a mystery.
- For MSVC there is no second opinion at all. Microsoft's `UnDecorateSymbolName` is the
  real ground truth, and `llvm-undname` is a reimplementation with known gaps — it
  rejects `?f@@YAX_L@Z`, which `clang-cl` itself emits for `__int128`.
- More platforms and architectures, where calling conventions and thunks differ.

## 2. More schemes

The plugin interface exists so these need no core changes:

- **Swift** — currently needs the `swift` binary; a pure-Python reader would be a first.
- **D**, **Delphi**, **Go**, **Objective-C**.

## 3. Performance

- Interning repeated components within one binary's symbol table.
- A batch detection pass over a whole table, rather than name by name.
- A benchmark corpus large enough not to sit entirely in cache.

## 4. Documentation

- The API reference is published from docstrings at
  <https://r0ny123.github.io/demangle/>. What it still lacks is a worked example of
  using `parse()` for a real analysis task — the feature most likely to be overlooked.
