# demangle

[![CI](https://github.com/r0ny123/demangle/actions/workflows/ci.yml/badge.svg)](https://github.com/r0ny123/demangle/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/downloads/)
[![Licence](https://img.shields.io/badge/licence-MIT-green)](https://github.com/r0ny123/demangle/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-r0ny123.github.io-blue)](https://r0ny123.github.io/demangle/)

**Read mangled symbol names in pure Python** — Itanium C++ (GCC/Clang), MSVC, Rust,
Swift, Objective-C, Go, D, Nim, Free Pascal, Delphi, Ada/GNAT, JNI, and the pre-Itanium
C++ families (g++ 2.x, cfront/ARM, Lucid, HP aCC, CodeWarrior). No dependencies, no
native code, no compiler required.

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
>>> demangle.demangle("@Unit@Class@Method$qqrv")
'__fastcall Unit::Class::Method()'
>>> demangle.demangle("AddAlignment__9ivTSolverUiP12ivInteractorP7ivTGlue")
'ivTSolver::AddAlignment(unsigned int, ivInteractor *, ivTGlue *)'
>>> demangle.demangle("__dt__6CActorFv")
'CActor::~CActor()'
```

- **No toolchain.** No C compiler at install time, no platform-specific wheel, no
  native library to find at run time: one pure-Python wheel that runs wherever Python
  does.
- **A tree, not just a string.** `parse()` answers with nodes to walk, so the namespace,
  the template arguments and the parameter types are fields rather than a regular
  expression against C++ declaration syntax, which nests and so cannot be parsed that
  way.
- **Measured, not asserted.** Every scheme is scored against a reference or, where none
  exists, a property the mangling must satisfy; see Correctness, below.
- **Safe on untrusted input.** Recursion depth, output size, substitution count and
  input length are all bounded, and every bound is configurable per call; see Safety,
  below.

## Install

```console
pip install demangle
```

Python 3.13 or newer. That is the whole dependency list.

The documentation, API reference included, is published at
<https://r0ny123.github.io/demangle/>.

## Use

### The string you probably want

```python
demangle.demangle(name)  # the spelling, or `name` itself
demangle.demangle_strict(name)  # raises DemanglingError instead
```

`demangle()` is built for the case where you are labelling every symbol in a binary and
most of them are not mangled at all. It never raises: a name it cannot read comes back
exactly as it went in, because a wrong expansion is worse than a mangled one — it
matches neither the symbol nor the declaration.

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

All schemes return full trees. A Rust symbol comes back as a `symbol` holding a
`path` of `name` components, with `impl`, `template`, `type` and `literal` nodes for
what the path carries:

```python
>>> tree = demangle.parse("_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E")
>>> [node.text for node in tree.find("name")]
['core', 'fmt', 'Formatter', 'pad']
```

[Working with the tree](https://r0ny123.github.io/demangle/analysing-a-binary/) works
one real task through `parse()` end to end — finding every function in libstdc++ that
takes a string by const reference, and measuring what the regular-expression version of
the same question gets wrong.

### The parts, when the spelling is not what you want

`signature()` gives the pieces rather than the string — the base name without its
scope, the scope without the base name, the parameter types on their own:

```python
>>> parts = demangle.signature("_ZNSt6vectorIiSaIiEE9push_backERKi")
>>> parts.namespace, parts.base_name
('std::vector<int, std::allocator<int>>', 'push_back')
>>> parts.parameters
('int const&',)
>>> demangle.signature("$s4main3FooV3baryS2i_SStF").return_type
'Swift.Int'
```

`namespace`, the scheme's separator and `base_name` spell `qualified_name` exactly, so
the three recombine. Splitting the *string* would not give that: `::` and `.` and `,`
occur inside template arguments and inside operator names as well as between components.

What each scheme records differs, and the fields say so rather than guessing. A C++ or
Swift name carries a signature; a Rust or Go path does not, and its `parameters` is
`None` — which is not `()`. `is_function` is `False` where the name does not say.

### A type on its own, not a whole symbol

A `typeinfo` name, an RTTI type descriptor and a Swift metadata typeref all carry a
*type* rather than a symbol. `demangle_type()` reads one, and `parse_type()` gives its
tree:

```python
>>> demangle.demangle_type("PKFvRiE", language="itanium")
'void (*)(int&) const'
>>> demangle.demangle_type("PEAX", language="msvc")
'void *'
>>> demangle.demangle_type("SaySiG", language="swift")
'[Swift.Int]'
```

`language` is required, and that is not an oversight: a whole symbol announces its
scheme — `_Z`, `?`, `$s` — and a type encoding announces nothing at all, so `Si` is
`std::istream` to the Itanium reader and `Swift.Int` to the Swift one. Every reference
tool puts this behind a flag for the same reason. On the command line it is
`demangle --types -l itanium`, one encoding per argument rather than a filter over mixed
text — `Pi` is an ordinary word, and `I like Pi` should stay as it is.

### A file, not a name

`nm` writes an address and a type letter before a name; a crash log writes a frame
number. So the useful operation over a file is "substitute every symbol-shaped word and
copy the rest through", which is what the command does with no arguments, and what the
library does too:

```python
>>> demangle.demangle_text("0000000000001139 T _ZN3foo3barEv")
'0000000000001139 T foo::bar()'
>>> [f.mangled for f in demangle.find_symbols("a _ZN3foo3barEv b")]
['_ZN3foo3barEv']
```

`find_symbols` gives the span as well as the spelling, for a caller that needs to know
*where* in a line a symbol was. `demangle_stream(sys.stdin, sys.stdout)` does the same
a line at a time, for pipes.

### Detection and batches

```python
>>> demangle.detect("?f@@YAXH@Z")
'msvc'
>>> list(demangle.demangle_all(["_Z1fv", "main"]))    # shares the cache
['f()', 'main']
```

### Bytes, when the names came from a symbol table

An ELF or Mach-O string table holds bytes, and they are not reliably UTF-8 — a truncated
table cuts a name mid-character. Every single-name entry point has a bytes form, so
reading one does not mean guessing an encoding first:

```python
>>> demangle.demangleb(b"_ZN3foo3barEv")
b'foo::bar()'
```

`demangleb_strict`, `detectb`, `parseb`, `signatureb`, `demangleb_type` and
`parseb_type` go with it. Undecodable bytes survive the round trip: `demangleb` hands
back exactly what it was given, byte for byte, rather than raising.

### The tree as data

`parse()` returns a walkable tree; `to_dict()` turns it into plain data, `--json` prints
it, and `node_kinds()` is the vocabulary to switch on rather than something to read out
of our source:

```python
>>> demangle.parse("_Z1fPi").to_dict()
{'kind': 'function', 'name': {'kind': 'name', 'text': 'f'}, 'parameters': [{'kind': 'pointer', 'inner': {'kind': 'builtin', 'spelling': 'int'}}], 'returns': None, 'suffix': ''}
>>> demangle.node_kinds("d")
('name', 'path', 'symbol')
```

The nodes carry `__match_args__`, so a `match` statement can test the shape of a
subtree; [the tree's
reference](https://r0ny123.github.io/demangle/reference/core/#the-tree) has an example.

A node reached more than once — an Itanium substitution, a Rust node named both by
position and by role — is written once with an `id` and afterwards as `{"$ref": id}`.
The structure is a graph, and expanding it in full does not always terminate in useful
time.

### Styles

The two Itanium reference demanglers, `llvm-cxxfilt` and GNU `c++filt`, legitimately
disagree on some spellings. Both are available, and neither is wrong:

```python
>>> demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi", style="llvm")   # default
'std::vector<int, std::allocator<int>>::push_back(int const&)'
>>> demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi", style="gnu")
'std::vector<int, std::allocator<int> >::push_back(int const&)'
```

### Printing less of a name

A decorated name expands to a great deal more than the name. `style()` composes what to
leave out, at the call site rather than by registering anything globally:

```python
>>> demangle.demangle("?g@C@@UEAAXXZ")
'public: virtual void __cdecl C::g(void)'
>>> narrow = demangle.style("llvm", msvc={"calling_convention": False, "access_specifier": False})
>>> demangle.demangle("?g@C@@UEAAXXZ", style=narrow)
'virtual void C::g(void)'
```

The MSVC scheme has nine such options, five from `llvm-undname` and four from
`UnDecorateSymbolName`, each with a command-line flag;
[`MsvcOptions`](https://r0ny123.github.io/demangle/reference/schemes/#demangle.schemes.msvc.options.MsvcOptions)
says which is which and how far each reaches.

Swift has the bundle Xcode and LLDB show instead of the full spelling — `Either` for
`Monads.Either`, `(_:)` for `(Swift.Int) -> Swift.UInt`, `specialized f()` for a page of
specialisation arguments. It is `--simplified` on the command line, and exact against
Swift's own 217 vectors:

```python
>>> from demangle.schemes.swift import SIMPLIFIED_OPTIONS
>>> demangle.demangle("_TtFSiSu", style=demangle.style("llvm", swift=SIMPLIFIED_OPTIONS))
'(_:)'
```

### Command line

```console
$ nm -a libfoo.so | demangle
$ demangle _ZNSt6vectorIiSaIiEE9push_backERKi
$ demangle --tree _Z1fPKc
$ demangle --detect _RNvC6_123foo3bar
$ demangle -p _ZNSt6vectorIiSaIiEE9push_backERKi    # the name, without the signature
$ demangle --base-name _ZSt4sortIPiEvT_S1_         # `sort<int*>`
$ demangle --no-return-type _ZSt4sortIPiEvT_S1_    # the declaration, minus `void `
$ demangle --ret-postfix _Z1fIiET_S0_              # `f<int>(int)int`, as `DMGL_RET_POSTFIX`
$ demangle --strip-underscore '_?f@@YAXH@Z'        # ignore one leading underscore
$ demangle --keep-hash _ZN4core3fmt5write17h05af221e174051e9E   # keep Rust's hash
$ demangle --types -l itanium PKFvRiE              # a bare type, as `c++filt -t`
$ demangle --no-calling-convention '?f@@YAXH@Z'    # `void f(int)`
$ demangle --no-tag-kind '?g3@@YAXVV@@@Z'          # `void __cdecl g3(V)`
$ demangle --simplified _TtFSiSu                   # Swift, the way Xcode shows it
$ demangle --json _Z1fPi                           # the parse tree as JSON
```

## Correctness

Correctness here is a measurement rather than a claim. Every scheme is scored against
the reference implementation for its mangling — `llvm-cxxfilt`, GNU `c++filt`,
`llvm-undname`, `rustfilt`, `swift-demangle`, `cwdemangle`, Embarcadero's own unmangler
— and, where no reference exists, against what the compiler itself recorded or a
property the mangling has to satisfy: re-mangling what was read must reproduce the
symbol the compiler wrote.

The checked-in corpora are replayed by the test suite with no compiler and no reference
demangler present. Beyond them, whole symbol tables are run live against the references:
about 2,500,000 real symbols from shipped libraries, and every name that differs is
accounted for one by one.

**[CONFORMANCE.md](https://r0ny123.github.io/demangle/CONFORMANCE/) is the whole
picture** — what each corpus is measured against, the notes behind every number, the
live runs, and the places where following a reference would itself be the defect.

## Safety

A mangled name is untrusted input in any tool that opens files it did not produce, so
the bounds are the design rather than a hardening pass.
[SECURITY.md](https://r0ny123.github.io/demangle/SECURITY/) has the threat
model, how the bounds are enforced and tested, and how to report privately.

## Performance

Pure Python, measured on the conformance corpora (`benchmarks/bench.py`):

| Workload | Throughput |
|---|---|
| Cold — every name distinct | ~22,000 names/sec |
| Warm — names repeat, as in a real symbol table | ~2,700,000 names/sec |
| Non-mangled names rejected | ~1,200,000 names/sec |
| Full AST construction | ~18,000 names/sec |

Taken from `benchmarks/baseline.json`; treat them as ratios rather than absolutes. The
gap between cold and warm is the point: symbol tables repeat themselves relentlessly, and
results are cached.

`bench.py --check` gates CI against the committed baseline. It compares figures
normalised against a calibration workload measured in the same run, so the gate reports
a slower *demangler* rather than a slower *machine*.

## Architecture

The short version: **parsers never build their own output** — each reports the grammar
productions it recognises to a builder, so one parser serves both `demangle()` and
`parse()`.

Schemes are plugins: a separate distribution can add a language through the
`demangle.languages` entry-point group without patching this package.

See [ARCHITECTURE.md](https://r0ny123.github.io/demangle/ARCHITECTURE/) for the full
picture and [Adding a scheme](https://r0ny123.github.io/demangle/adding-a-scheme/) to
add a language of your own.

## Contributing

New schemes, corpus contributions, and conformance bug reports are all welcome — see
[CONTRIBUTING.md](https://r0ny123.github.io/demangle/CONTRIBUTING/), or [report a wrong
spelling](https://github.com/r0ny123/demangle/issues/new?template=conformance-bug.yml).

## Licence

MIT. The Rust and MSVC readers derive from MIT- and BSD-licensed originals;
[NOTICE](https://github.com/r0ny123/demangle/blob/main/NOTICE) has the details.
