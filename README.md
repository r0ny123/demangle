# demangle

Read mangled symbol names — **Itanium C++** (GCC/Clang), **MSVC**, **Rust**, **Swift**,
**Go**, **D**, **Nim** and **Free Pascal** — in pure Python. No dependencies, no native
code, no compiler required.

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
>>> demangle.demangle("example.com/m/v2%2e5.(*T).Method", language="go")
'example.com/m/v2.5.(*T).Method'
>>> demangle.demangle("eqdestroy___systemZassertions_23")
'system/assertions.=destroy'
>>> demangle.demangle("MYUNIT$_$TWIDGET_$__$$_AREA$$LONGINT")
'MYUNIT.TWIDGET.AREA: LONGINT'
```

## Why this exists

Every existing option in Python binds to a native demangler: `cxxfilt` and `pycxxfilt`
wrap LLVM or libstdc++, `undname` wraps Wine through CFFI. That means a C toolchain at
install time, a platform-specific wheel, and — for MSVC — nothing maintained at all.

They also all hand back a string. Want the namespace, the template arguments, or the
parameter types, and you are writing a regular expression against C++ declaration syntax,
which nests, and so cannot be parsed that way.

This library is pure Python, and it can give you a tree.

## Install

```console
pip install demangle          # once released to PyPI
pip install git+https://github.com/r0ny123/demangle    # until then
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

All three schemes return full trees. A Rust symbol comes back as a `symbol` holding a
`path` of `name` components, with `impl`, `template`, `type` and `literal` nodes for what
the path carries:

```python
>>> tree = demangle.parse("_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E")
>>> [node.text for node in tree.find("name")]
['core', 'fmt', 'Formatter', 'pad']
```

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
| Purpose-built C++, llvm style | `llvm-cxxfilt` 18.1.3 | **280 / 280** |
| Purpose-built C++, gnu style | GNU `c++filt` 2.42 | **295 / 297** † |
| Regression corpus | `llvm-cxxfilt` 18.1.3 | **31 / 31** |
| Swift runtime + the compiler's own test corpus | `swift-demangle` 5.10.1 | **8494 / 8494** |
| Nim 1.6 and 2.2, against the compiler's own record ¶ | `.ndi` debug mapping | **2115 / 2115** |
| Free Pascal 3.2.2 runtime and packages § | re-assembly + `ppudump` | **3899 / 3899** |
| Go, from the shipped toolchain | round trip ‡ | **1498 / 1498** |
| D, from the shipped libgphobos | GNU `c++filt --format=dlang` | **1257 / 1257** |

§ Free Pascal ships no demangler either. The property is re-assembly — the parts this
splits out, rejoined with the compiler's own separators, must reproduce the symbol — and
it holds for all 236,570 readable symbols in the shipped runtime, not only the sample
above. Independently, `ppudump` prints both a unit's mangled names and the names it
declares, and every name read is one the unit declares. Case is not recoverable: Pascal
is case-insensitive and the compiler upper-cases before mangling.

¶ Nim has no reference demangler either, and its mangling is not injective: `mangle`
drops an underscore before a digit, so `len0_16` and `len016` are the same symbol. What
carries correctness is the same round-trip property Go uses — re-mangling what we read
must reproduce the bytes — plus agreement with the name the compiler recorded for a
debugger. Seven names in the corpus cannot come back exactly, and all seven are that one
documented loss; they are listed by name in `tests/conformance/nim-lossy.txt` rather than
rounded off.

‡ Go has no reference demangler — `go tool nm` prints symbol names with their escapes
intact and nothing in the toolchain decodes one. So the check is a property instead:
re-escaping a decoded package path must reproduce the bytes the linker wrote, where the
escaping is a transcription of Go's own `objabi.PathToPrefix`. It is verified over every
symbol in the shipped toolchain binaries, not just the recorded sample.

† The two shortfalls are not ours to fix: in each, the two reference implementations
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
| `libstdc++.so.6`, gnu style | 5,990 | **100%** |
| Swift runtime + Foundation | 48,368 | **100%** |
| Nim standard library routine names ¶ | 5,946 | **99.87%** |
| Free Pascal runtime and packages § | 236,570 | **100%** |
| `libgphobos` + `libgdruntime` (D) | 16,333 | **100%** |
| Go toolchain (`go`, `compile`, `link`) | 30,733 | round trip ‡ |

About 450,000 real symbols, all exact.

The purpose-built corpus reached 100% while libstdc++ was demangling *one symbol in
5,913* — the first one carried an ELF version suffix, a shape no hand-written test thinks
to include. Nearly every defect fixed here came from reading real shipped binaries.

The libstdc++ row is against GNU rather than LLVM because llvm-cxxfilt 18 reads 78 of
those 5,990 names as unreadable and echoes them back: the transaction-safe clone prefix
`_ZGTt`, and the `DF` floating-point productions. Checking against GNU over the whole
library rather than over the recorded sample is what found two real gaps here — `DF16b`
is `std::bfloat16_t` and `DF<n>x` is `_FloatNx` terminated by its own `x`, and GNU puts a
space before a template argument list whose name ends with `<`, so `operator<< <int>`.
No name in either checked-in corpus carried any of them.

The purpose-built corpus still earns its place: compiled by **both** `clang++` and `g++`
across C++11 through C++20 at two optimisation levels, it reaches constructs a released
library happens not to contain — concepts, coroutine frames, generic lambdas,
requires-clauses.

Pass counts are pinned as exact numbers, so an improvement cannot quietly mask a
regression; `tests/test_readme.py` checks this table against those pins.

Regenerate with `tools/generate_corpus.py` and `tools/generate_rust_corpus.py`; compare
against a live reference with `tools/differential.py`.

### On trusting the references

Not blindly. `clang-cl` emits `?f@@YAX_L@Z` for `__int128` and `llvm-undname` — LLVM's
own demangler — rejects it. The two C++ references contradict each other on substitution
numbering. Both echo their input on failure, which reads exactly like success.

So the split is deliberate: the **ABI specification** governs grammar and structure, the
**references** govern spelling. Ambiguities were settled by probing both references and
accepted only where they agreed (`tools/probe_substitutions.py` makes one print its own
substitution table). Where they genuinely differ, the difference is a `style` rather than
a silent winner.

## Safety

A mangled name is untrusted input in any tool that opens files it did not produce.

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

Treat these as ratios rather than absolutes. The gap between cold and warm is the point:
symbol tables repeat themselves relentlessly, and results are cached.

`bench.py --check` gates CI against the committed baseline. It compares figures normalised
against a calibration workload measured in the same run, so the gate reports a slower
*demangler* rather than a slower *machine*.

## Architecture

The short version: **parsers never build their own output**. Each one is written against
a `Builder` protocol and reports the grammar productions it recognises; a fast text
builder or a tree builder decides what those become. That is what lets one parser serve
both `demangle()` and `parse()` with no second implementation to drift.

Schemes are plugins. `core` never imports one, they never import each other, and a
separate distribution can add a language through the `demangle.languages`
entry-point group without patching this package. Both rules are enforced by tests.

[docs/analysing-a-binary.md](docs/analysing-a-binary.md) works one real task through
`parse()` end to end — finding every function in libstdc++ that takes a string by const
reference, and measuring what the regular-expression version of the same question gets
wrong.

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
reference prints, and what this library prints.

Fixing a spelling or adding a language should not require understanding the whole
codebase. Where it does, that is a bug.

## Licence

MIT. The Rust demangler derives from Team bi0s' `rust_demangler` (MIT) and the MSVC
demangler from [SMDA](https://github.com/danielplohmann/smda) (BSD 2-Clause); both are
substantially modified, and both upstream licences are reproduced in [NOTICE](NOTICE).
