# demangle

[![CI](https://github.com/r0ny123/demangle/actions/workflows/ci.yml/badge.svg)](https://github.com/r0ny123/demangle/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![Licence](https://img.shields.io/badge/licence-MIT-green)](https://github.com/r0ny123/demangle/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-r0ny123.github.io-blue)](https://r0ny123.github.io/demangle/)
[![Open Playground](https://img.shields.io/badge/Open%20Playground-r0ny123.github.io-2ea44f?logo=googlechrome&logoColor=white)](https://r0ny123.github.io/demangle/playground/)

**Read mangled symbol names in pure Python.** Itanium C++ (GCC, Clang), MSVC, Rust,
Swift, Objective-C, Go, D, Nim, Free Pascal, Delphi and C++Builder, Ada/GNAT, JNI, and
the pre-Itanium C++ families (g++ 2.x, cfront/ARM, Lucid, HP aCC, CodeWarrior), with no
dependencies.

```python
>>> import demangle
>>> demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi")
'std::vector<int, std::allocator<int>>::push_back(int const&)'
>>> demangle.demangle("?f@@YAXH@Z")
'void __cdecl f(int)'
>>> demangle.demangle("_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E")
'core::fmt::Formatter::pad'
>>> demangle.demangle("$s10Foundation4DataV5countSivg")
'Foundation.Data.count.getter : Swift.Int'
>>> demangle.demangle("@Unit@Class@Method$qqrv")
'__fastcall Unit::Class::Method()'
>>> demangle.demangle("CreateFileW")    # not mangled, so returned as it is
'CreateFileW'
```

- **Pure Python, everywhere.** CPython 3.11+, PyPy, and the browser through Pyodide.
  On CPython for Linux, macOS and Windows, pip picks a wheel whose Itanium parser is
  compiled by mypyc from the same source, about twice as fast; everything else gets the
  pure wheel, as does `pip install --no-binary demangle demangle`. Nothing to compile at
  install time.
- **A tree, not just a string.** `parse()` returns nodes to walk and `signature()` the
  parts of a name, so nothing has to take a C++ declaration apart with a regular
  expression.
- **Measured against the references.** Every scheme is scored against its reference
  demangler (`llvm-cxxfilt`, GNU `c++filt`, `llvm-undname`, `rustc-demangle`,
  `swift-demangle` and others) or, where there is none, against what the compiler
  recorded, over checked-in corpora and about 2,500,000 real symbols.
- **Safe on hostile input.** Recursion depth, output size, substitution count and input
  length are bounded while a name is read, and `demangle()` never raises over a name.

## Install

```console
pip install demangle
```

## Quick start

`demangle()` returns the spelling, or the name unchanged when nothing reads it, so it
is safe to run over every symbol in a binary. When you need more than the string:

```python
>>> demangle.parse("_ZNK3Foo3barIiEEvPKc").spell()           # a tree; raises if unreadable
'void Foo::bar<int>(char const*) const'
>>> demangle.signature("_ZNSt6vectorIiSaIiEE9push_backERKi").base_name
'push_back'
>>> demangle.demangle_text("0000000000001139 T _ZN3foo3barEv")   # every symbol in a line
'0000000000001139 T foo::bar()'
>>> demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi", style="gnu")
'std::vector<int, std::allocator<int> >::push_back(int const&)'
```

The command is a filter like `c++filt`, over any mix of schemes:

```console
$ nm -D libfoo.so | demangle
$ demangle '?f@@YAXH@Z' _RNvC6_123foo3bar
$ nm -D libfoo.so | demangle --json-lines       # one JSON object per symbol
```

Scripts for [IDA Pro, Binary Ninja and
Ghidra](https://r0ny123.github.io/demangle/integrations/) relabel a database with it,
and the [playground](https://r0ny123.github.io/demangle/playground/) runs it in the
browser.

## Documentation

The full documentation is at <https://r0ny123.github.io/demangle/>.

| Page | What it covers |
| --- | --- |
| [Using the library](https://r0ny123.github.io/demangle/usage/) | Every entry point, styles and options, and the command line |
| [Working with the tree](https://r0ny123.github.io/demangle/analysing-a-binary/) | One real task through `parse()`, against the regular-expression version |
| [Labelling a symbol table](https://r0ny123.github.io/demangle/labelling-a-symbol-table/) | One label per address, the same whichever compiler built the binary |
| [API reference](https://r0ny123.github.io/demangle/reference/api/) | Signatures and docstrings |
| [Conformance](https://r0ny123.github.io/demangle/CONFORMANCE/) | What each scheme is measured against, and every number |
| [Comparison](https://r0ny123.github.io/demangle/comparison/) | Coverage and speed beside LLVM, `cpp_demangle` and `itanium_demangler` |
| [Security policy](https://r0ny123.github.io/demangle/SECURITY/) | The threat model, the bounds, and how to report privately |
| [Architecture](https://r0ny123.github.io/demangle/ARCHITECTURE/) | How the parsers, builders and schemes fit together |

## Performance

Measured on the conformance corpora by `benchmarks/bench.py`, whose `--check` gates CI
against `benchmarks/baseline.json`:

| Workload | Throughput |
| --- | --- |
| Cold, every name distinct | ~22,000 names/s |
| Warm, names repeat as in a real symbol table | ~2,700,000 names/s |
| Names that are not mangled | ~1,200,000 names/s |
| Full tree construction | ~18,000 names/s |

On names it has not seen, a native demangler is an order of magnitude or more faster:
LLVM's reads libstdc++'s Itanium names about 50 times as fast. A symbol table repeats
its names, though, and a name read once costs a dictionary lookup afterwards.

## Contributing

New schemes, corpus entries and conformance reports are welcome; see
[CONTRIBUTING.md](https://r0ny123.github.io/demangle/CONTRIBUTING/), or [report a wrong
spelling](https://github.com/r0ny123/demangle/issues/new?template=conformance-bug.yml).

## Licence

MIT. The Rust and MSVC readers derive from MIT- and BSD-licensed originals;
[NOTICE](https://github.com/r0ny123/demangle/blob/main/NOTICE) has the details.
