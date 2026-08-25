# Roadmap

What is not done yet, in the order it matters. Each entry says what would have to change,
so anyone can pick one up.

## Known conformance gaps

Three names in the 196-symbol Itanium corpus and two in the 609-name MSVC corpus do not
match the reference. They are listed here rather than quietly excluded.

| Scheme | Name | Issue |
|---|---|---|
| Itanium | `_ZSt12construct_atIcJRKcEE...` | Pack expansion over a substituted parameter appends a spurious `...`, and names appearing inside a `decltype` expression are entered into the substitution table when ABI 5.1.10 says expressions are not substitutable. |
| Itanium (gnu) | `_ZN5outer5inner7deducedIiEEDTplfp_fp_ET_` | GNU spells function parameters in expressions as `{parm#1}` and puts a space after `decltype`. Needs a GNU expression-spelling option. |
| MSVC | 2 names | Inherited from the original implementation; not yet diagnosed. |

## 1. Convert the MSVC and Rust parsers to the builder protocol

**The highest-priority item.** Both were written before this architecture existed and
still assemble text directly, so `parse()` returns a single `Raw` node for them instead
of a tree. `demangle()` is unaffected.

The public API does not change when this lands — only the shape of what `parse()`
returns gets richer. The Itanium parser is the worked example to follow.

## 2. Expression substitution semantics

ABI 5.1.10: "we do not substitute for expressions, though names appearing in them might
be substituted." The parser currently records types met inside expressions, which is what
causes the `construct_at` failure above. Fixing it needs a careful reading of what
"names appearing in them" includes, and probe-driven confirmation.

## 3. Broader corpora

Present coverage is two compilers on one platform. Worth adding:

- More toolchains and versions (older GCC, MSVC proper, Intel).
- Other architectures and platforms, where calling conventions and thunks differ.
- Symbols scraped from real distribution binaries rather than only from purpose-built
  sources.
- A Rust conformance corpus generated with `rustc-demangle`, which the project does not
  yet have — Rust is currently covered by unit tests only.

## 4. More schemes

The plugin interface exists so these do not need core changes:

- **Swift** — currently needs the `swift` binary; a pure-Python reader would be a first.
- **D**, **Delphi**, **Go**, **Objective-C**.

## 5. Performance

- A C-free fast path for detection over a whole symbol table at once.
- Interning repeated components within a single binary's symbol table.
- Benchmarks on a corpus large enough to be representative (the present one is small
  enough to sit in cache).

## 6. Documentation

- API reference published from docstrings.
- A worked example of using `parse()` for a real analysis task, which is the feature most
  likely to be overlooked.
