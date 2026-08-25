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

- **Swift** — currently needs the `swift` binary; a pure-Python reader would be a first.
- **D** — well specified at dlang.org/spec/abi.html, and `gdc`/`ldc` are installable, so
  the corpus can be compiled rather than hand-written.
- **Delphi**, **Objective-C**.

## 2. Performance

- Interning repeated components within one binary's symbol table.
- A batch detection pass over a whole table, rather than name by name.
- A benchmark corpus large enough not to sit entirely in cache.
