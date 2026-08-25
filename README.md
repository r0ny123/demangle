# demangle

Read mangled symbol names — **Itanium C++** (GCC/Clang), **MSVC**, and **Rust** — in
pure Python. No dependencies, no native code, no compiler required.

```python
>>> import demangle
>>> demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi")
'std::vector<int, std::allocator<int>>::push_back(int const&)'
>>> demangle.demangle("?f@@YAXH@Z")
'void __cdecl f(int)'
>>> demangle.demangle("_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E")
'core::fmt::Formatter::pad'
```

## Why this exists

Every existing option in Python binds to a native demangler: `cxxfilt` and `pycxxfilt`
wrap LLVM or libstdc++, `undname` wraps Wine through CFFI. That means a C toolchain at
install time, a platform-specific wheel, and — for MSVC names — nothing maintained at
all. For a tool that has to run anywhere Python runs, that is a real constraint.

It also means every one of them hands back a string. If you want the namespace, the
template arguments, or the parameter types, you get to write a regular expression
against C++ declaration syntax, which nests, and so cannot be parsed that way.

This library does neither. It is pure Python, and it can give you a tree.

## Install

```console
pip install demangle
```

Python 3.11 or newer. That is the whole dependency list.

## Use

### The string you probably want

```python
demangle.demangle(name)  # never raises; returns `name` unchanged if unreadable
demangle.demangle_strict(name)  # raises DemanglingError instead
```

`demangle()` is built for the case where you are labelling every symbol in a binary and
most of them are not mangled at all. A name it cannot read comes back exactly as it went
in, because a wrong expansion is worse than a mangled one — it matches neither the
symbol nor the declaration.

### The structure, when you need it

```python
>>> tree = demangle.parse("_ZNK3Foo3barIiEEvPKc")
>>> tree.spell()
'void Foo::bar<int>(char const*) const'
>>> [node.text for node in tree.find("name")]
['Foo', 'bar']
>>> function = next(tree.find("function"))
>>> len(function.parameters)
1
```

Every node supports `.walk()`, `.find(kind)`, `.children()` and `.spell()`.

Itanium and MSVC return full trees. **Rust does not yet** — it returns a single `raw`
node holding the spelling, so `walk()` and `find()` see nothing below the root for a
Rust symbol. `demangle()` is unaffected. See [ROADMAP.md](ROADMAP.md).

### Detection and batches

```python
>>> demangle.detect("?f@@YAXH@Z")
'msvc'
>>> list(demangle.demangle_all(symbol_table))     # generator, shares the cache
```

### Styles

The two reference demanglers legitimately disagree on some spellings. Both are
available, and neither is wrong:

```python
>>> demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi", style="llvm")   # default
'std::vector<int, std::allocator<int>>::push_back(int const&)'
>>> demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi", style="gnu")
'std::vector<int, std::allocator<int> >::push_back(int const&)'
```

### Command line

```console
$ nm -a libfoo.so | demangle
$ demangle _ZNSt6vectorIiSaIiEE9push_backERKi
$ demangle --tree _Z1fPKc
$ demangle --detect _RNvC6_123foo3bar
```

## Correctness

Correctness is defined against the reference implementations and measured on symbols
real compilers actually emit, not on hand-picked examples.

### Checked-in corpora

Replayed by the test suite. No compiler and no reference demangler needed.

| Corpus | Reference | Exact |
|---|---|---|
| Real shipped libstdc++ | `llvm-cxxfilt` 18.1.3 | **5913 / 5913** |
| Rust, both schemes | `rustfilt` (rustc-demangle 0.1.28) | **5316 / 5316** |
| MSVC — LLVM's own test corpus | `llvm-undname` 18.1.3 | **609 / 609** |
| Rust toolchain (`rustc_driver`, `libstd`) | `rustfilt` | **394 / 394** |
| Purpose-built C++, llvm style | `llvm-cxxfilt` 18.1.3 | **278 / 278** |
| Purpose-built C++, gnu style | GNU `c++filt` 2.42 | **272 / 275** † |
| Regression corpus | `llvm-cxxfilt` 18.1.3 | **30 / 30** |

† The three shortfalls are not ours to fix: in each, the two reference implementations
disagree with *each other* about what belongs in the substitution table — not about how
to spell it. Matching both would mean two incompatible parses of the same bytes, so we
follow LLVM and pin the disagreements by name.

### Whole symbol tables

Run live against the reference, not replayed.

| Binary | Symbols | Agree |
|---|---|---|
| `libLLVM.so.18.1` | 44,186 | **100%** |
| `libclang-cpp.so` + Polly + LTO | 41,140 | **100%** |
| `librustc_driver`, `libstd`, `libtest` | 20,697 | **100%** |
| `libstdc++.so.6` | 5,913 | **100%** |

About 112,000 real symbols, all exact.

This matters more than it might look. The purpose-built corpus reached 100% while
libstdc++ was demangling *one symbol in 5,913* — the very first one carried an ELF version suffix, a
shape no hand-written test case thinks to include. Nearly every defect fixed in this
project came from reading real shipped binaries.

The purpose-built corpus is still worth having: it is compiled by **both** `clang++` and
`g++` at four language standards (C++11 through C++20) and two optimisation levels, so
it reaches constructs a released library happens not to contain — concepts, coroutine
frames, generic lambdas, requires-clauses.

Pass counts are pinned as exact numbers, so an improvement cannot quietly mask a
regression, and `tests/test_readme.py` checks this table against those pins.

Regenerate with `tools/generate_corpus.py` and `tools/generate_rust_corpus.py`; compare
against a live reference with `tools/differential.py`.

### On trusting the references

Not blindly. `clang-cl` emits `?f@@YAX_L@Z` for `__int128`, and `llvm-undname` — LLVM's
own demangler — rejects it. The two C++ references contradict each other on substitution
numbering. Both echo their input on failure, which is indistinguishable from success
unless you look.

So the split is deliberate: the **ABI specification** governs grammar and structure, and
the **references** govern spelling. Where the specification is ambiguous the behaviour
was settled by probing both references and only accepted when they agreed
(`tools/probe_substitutions.py` makes a reference print its own substitution table).
Where they genuinely differ, the difference is a `style`, not a silent winner.

## Safety

A mangled name is untrusted input in any tool that opens files it did not produce, so:

- `demangle()` never raises, for any input, including binary junk.
- Recursion depth, output size, substitution count and input length are all bounded,
  and the bounds are configurable per call.
- Results are deterministic.
- The property-based suite runs the parsers against arbitrary text, mangling-alphabet
  text, arbitrary bytes, every truncation of a known-good name, and inputs built to
  blow up a naive parser.

## Performance

Pure Python, measured on the conformance corpora (`benchmarks/bench.py`):

| Workload | Throughput |
|---|---|
| Cold — every name distinct | ~48,000 names/sec |
| Warm — names repeat, as in a real symbol table | ~1,900,000 names/sec |
| Non-mangled names rejected | ~430,000 names/sec |
| Full AST construction | ~15,000 names/sec |

Measured on the machine that produced `benchmarks/baseline.json`; treat them as ratios
rather than absolutes.

The gap between cold and warm is the point: symbol tables repeat themselves relentlessly,
and results are cached. Benchmarks are committed with a baseline and `--check` fails on
a regression.

## Architecture

The short version: **parsers never build their own output**. Each one is written against
a `Builder` protocol and reports the grammar productions it recognises; a fast text
builder or a tree builder decides what those become. That is what lets one parser serve
both `demangle()` and `parse()` with no second implementation to drift.

Schemes are plugins. `core` never imports one, they never import each other, and a
separate distribution can add Swift or D support through the `demangle.languages`
entry-point group without patching this package. Both rules are enforced by tests.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full picture, and
[docs/adding-a-scheme.md](docs/adding-a-scheme.md) to add a language. The API reference
is published at <https://r0ny123.github.io/demangle/>.

## Security

The library parses untrusted input by design, so that is treated as a threat model
rather than an edge case. See [SECURITY.md](SECURITY.md) for what is in scope and how to
report privately.

## Contributing

New schemes, corpus contributions, and conformance bug reports are all welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md). A good bug report is a mangled name, what the
reference demangler prints, and what this library prints.

You should not need to understand the whole codebase to fix a spelling or add a
language. That is a design goal, and if it is not true somewhere, that is a bug.

## Licence

MIT. The Rust demangler derives from Team bi0s' `rust_demangler` (MIT) and the MSVC
demangler was originally written for [SMDA](https://github.com/danielplohmann/smda);
see [NOTICE](NOTICE).
