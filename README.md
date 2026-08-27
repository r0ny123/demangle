# demangle

Read mangled symbol names — **Itanium C++** (GCC/Clang), **MSVC**, **Rust**, **Swift**,
**Objective-C**, **Go**, **D**, **Nim**, **Free Pascal** and **Delphi** — in pure Python. No dependencies, no native
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
>>> demangle.demangle("@Unit@Class@Method$qqrv")
'__fastcall Unit::Class::Method()'
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

All schemes return full trees. A Rust symbol comes back as a `symbol` holding a
`path` of `name` components, with `impl`, `template`, `type` and `literal` nodes for what
the path carries:

```python
>>> tree = demangle.parse("_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E")
>>> [node.text for node in tree.find("name")]
['core', 'fmt', 'Formatter', 'pad']
```

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
>>> demangle.demangle_type(".PEAX", language="msvc")     # a type descriptor's own symbol
'void *'
>>> demangle.demangle_type("SaySiG", language="swift")
'[Swift.Int]'
```

`language` is required, and that is not an oversight. A whole symbol announces its
scheme — `_Z`, `?`, `$s` — and a type encoding announces nothing at all, so `Si` is
`std::istream` to the Itanium reader and `Swift.Int` to the Swift one with no evidence
to decide between them. Every reference tool puts this behind a flag for the same reason:
`c++filt -t`, libiberty's `DMGL_TYPES`, `UnDecorateSymbolName`'s `UNDNAME_TYPE_ONLY`,
Swift's `demangleTypeAsString`. On the command line it is
`demangle --types -l itanium`, which reads one encoding per argument or per line rather
than filtering symbols out of mixed text — `Pi` is an ordinary word, and `I like Pi`
should stay as it is.

### A file, not a name

`nm` writes an address and a type letter before a name; a crash log writes a frame
number. So the useful operation over a file is "substitute every symbol-shaped word and
copy the rest through", which is what the command does with no arguments — and now what
the library does too:

```python
>>> demangle.demangle_text("0000000000001139 T _ZN3foo3barEv")
'0000000000001139 T foo::bar()'
>>> demangle.demangle_stream(sys.stdin, sys.stdout)     # a line at a time, for pipes
>>> [f.mangled for f in demangle.find_symbols("a _ZN3foo3barEv b")]
['_ZN3foo3barEv']
```

`find_symbols` gives the span as well as the spelling, for a caller that needs to know
*where* in a line a symbol was.

### Detection and batches

```python
>>> demangle.detect("?f@@YAXH@Z")
'msvc'
>>> list(demangle.demangle_all(symbol_table))     # generator, shares the cache
```

### Bytes, when the names came from a symbol table

An ELF or Mach-O string table holds bytes, and they are not reliably UTF-8 — a truncated
table cuts a name mid-character. Every entry point has a bytes form, so reading one does
not mean guessing an encoding first:

```python
>>> demangle.demangleb(b"_ZN3foo3barEv")
b'foo::bar()'
```

`demangleb_strict`, `detectb`, `parseb`, `signatureb`, `demangleb_type` and
`parseb_type` go with it. Undecodable bytes
survive the round trip: `demangleb` hands back exactly what it was given, byte for byte,
rather than raising.

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

The nodes carry `__match_args__`, so structural pattern matching works:

```python
>>> from demangle.core.ast import Builtin, Pointer
>>> match demangle.parse("_Z1fPi").parameters[0]:
...     case Pointer(Builtin(name)): print("pointer to", name)
pointer to int
```

A node reached more than once — an Itanium substitution, a Rust node named both by
position and by role — is written once with an `id` and afterwards as `{"$ref": id}`. The
structure is a graph, and expanding it in full does not always terminate in useful time.

### Styles

The two reference demanglers legitimately disagree on some spellings. Both are
available, and neither is wrong:

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

The five MSVC options are `llvm-undname`'s five flags and mean the same:
`calling_convention`, `access_specifier`, `member_type`, `return_type`, `variable_type`.
On the command line they are `--no-calling-convention` and its siblings. Over LLVM's own
609-name corpus this agrees with the reference on **1250 / 1253** of the differences the
flags make ††.

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
$ demangle --types -l itanium PKFvRiE              # a bare type, as `c++filt -t`
$ demangle --no-calling-convention '?f@@YAXH@Z'    # `void f(int)`
$ demangle --simplified _TtFSiSu                   # Swift, the way Xcode shows it
$ demangle --json _Z1fPi                           # the parse tree as JSON
```

`-p` is `c++filt -p`: over the shipped libstdc++ and the GNU-style corpus the two agree
on **6132 / 6213** names. The 81 are deliberate. `c++filt` strips the parameter list only
from the outermost declaration, so a thunk keeps its target's — `non-virtual thunk to
X::~X()` — and it drops a `[clone .cold]` suffix while keeping an `@@GLIBCXX_3.4` one.
This strips throughout and keeps both suffixes, because a filter over a symbol table
should not quietly discard part of the symbol. One more is a name `c++filt` refuses and
this reads.

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
| Purpose-built C++, gnu style | GNU `c++filt` 2.42 | **298 / 300** † |
| Regression corpus | `llvm-cxxfilt` 18.1.3 | **31 / 31** |
| Bare `<type>` encodings, llvm style | `llvm-cxxfilt --types` 18.1.3 | **1076 / 1076** |
| Bare `<type>` encodings, gnu style | GNU `c++filt -t` 2.42 | **1073 / 1076** ‡‡ |
| Swift runtime + the compiler's own test corpus | `swift-demangle` 5.10.1 | **8494 / 8494** |
| Nim 1.6 and 2.2, against the compiler's own record ¶ | `.ndi` debug mapping | **2115 / 2115** |
| Free Pascal 3.2.2 runtime and packages § | re-assembly + `ppudump` | **3899 / 3899** |
| Go, from the shipped toolchain | round trip ‡ | **1498 / 1498** |
| D, from the shipped libgphobos | GNU `c++filt --format=dlang` | **1257 / 1257** |
| Objective-C, three ABIs, against the declaration ⁂ | clang 18.1.3 + `libobjc.a` | **2665 / 2665** |
| Swift, simplified spelling — the compiler's own vectors | `swift-demangle --simplified` | **217 / 217** |
| Delphi/C++Builder, against Embarcadero's unmangler ◊ | recorded `tdump -um` | **11363 / 11363** |

†† The three are names where `llvm-undname --no-return-type` leaves an unclosed
bracket — `int (__cdecl * (__cdecl B::*volatile memptrtofun7)(void)` is not a declaration
of anything. This keeps the balanced spelling, which is what the reference itself prints
with no flag.

‡‡ The same 1,076 encodings under both references, which spell them differently. The
three are a doubled `KK` cv-qualifier: `c++filt` folds the repeat away and `llvm-cxxfilt`
keeps it, and this follows LLVM. The ABI writes one `<CV-qualifiers>` group per type, so
no compiler emits `KK` and the two references disagree only about input neither is given.

§ Free Pascal ships no demangler either. The property is re-assembly — the parts this
splits out, rejoined with the compiler's own separators, must reproduce the symbol — and
it holds for all 236,570 readable symbols in the shipped runtime, not only the sample
above. Independently, `ppudump` prints both a unit's mangled names and the names it
declares, and every name read is one the unit declares. Case is not recoverable: Pascal
is case-insensitive and the compiler upper-cases before mangling.

✻ A mangled name in Swift *metadata* can hold a one-byte marker and a four-byte offset
in place of a type the image already describes, so reading one needs the image:
`swift.demangle_symbolic` takes bytes and a resolver, and `resolve.ContextResolver` is
one. Each reference resolves to a descriptor; the check is that the symbol the *linker*
put at that address demangles to the same name, with `swift-demangle` reading both sides.
Splicing each resolved fragment back in gives a self-contained name the reference can
read, and it agrees with our spelling on 4,799 of 4,799.

⁂ Objective-C has no reference demangler, and barely a mangling: what there is comes from
the compiler rather than the language, so the rules are transcribed from clang's
`Mangle.cpp`, `CGObjCMac.cpp` and `CGObjCGNU.cpp`. The expected column is not this
library's own output but what the *declaration* said — every symbol was emitted by clang
for Objective-C this package wrote, or read out of the shipped GCC runtime. The
GNU-family method mangling is not injective, and clang says so where it writes it:
`_i_A_B_c` is `-[A(B) c]` and `-[A_B c]` alike. Every reading that re-mangles is found and
the preferred one is flagged `ambiguous`; the 26 names where the preference differs from
the declaration are listed in `tests/conformance/objc-lossy.txt` rather than rounded off.

◊ Delphi and C++Builder share one mangling, `@Unit@Class@Method$qqrv`. There is no Delphi
compiler on the platforms this is developed on, so the grammar is Embarcadero's own
`unmangle.c` — the code TDUMP, the linker and the debugger run. The reference is a
recorded one: `tests/conformance/delphi-tdump.txt` is a dump of the real `tdump.exe
-q -um` over the export tables of real BPLs and C++Builder DLLs, and its expected column
is what that unmangler printed. All 11,363 readable entries are replayed in CI, and the
10 MSVC `@name@N` decorations sitting in the same tables are refused — Microsoft's 32-bit
`__fastcall` C decoration is not this scheme. That includes what the unmangler prints
where a C++ programmer would not: `SetFlat(const const bool)` for `qqrxo`, which is
`copy_args` emitting the qualifier and the type spelling it again. Free Pascal's
`$`-delimited names are a different mangling and are read by the `pascal` scheme.

Those export tables do not contain every construct the grammar has: they exercise 75% of
the parser, and the missing quarter is C++Builder territory — pointer to member,
`char16_t`, rvalue references, `__saveregs`, the special tables, the Delphi 4 template
forms. `tests/conformance/delphi-constructs.txt` covers those with 53 hand-built names,
corroborated against an independent implementation of the same unmangler rather than
against TDUMP. That is **weaker evidence**, it is kept in its own file so it cannot be
read as part of the row above, and its header says so. A C++Builder `.map` or TDUMP dump
carrying these shapes would replace it with real evidence.

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
to spell it — and matching both would mean two incompatible parses of the same bytes.
Both are pinned by name.

‖ The 31 names the `gnu` style spells differently from `c++filt` are all names the two
references spell differently from *each other*, and on every one of them this matches
`llvm-cxxfilt` exactly: a back-reference the two resolve to different entries, and a
`const` applied to a type that already carries one, which the mangling really does say
and which GNU folds. A further 97 names `c++filt` refuses outright and this reads.

A third disagreement used to be here and is now reproduced instead. GNU omits the space
it otherwise puts between two closing angle brackets when the last template argument is
an empty pack, which is a bookkeeping slip rather than a rule: libiberty decides on a
field it updates on every append and does not restore when it rewinds the separator in
front of an argument that printed nothing, so the character it tests is that separator's
space. The same output shows both spellings in one name — `f<A<B<C>>, JE>` comes out
`void f<A<B<C> >>(A<B<C> >)`. The `gnu` style exists to reproduce `c++filt`, slip and
all, so it now does; the default `llvm` style spaces neither, as `llvm-cxxfilt` does.

### Whole symbol tables

Run live against the reference, not replayed.

| Binary | Symbols | Agree |
|---|---|---|
| `libLLVM.so.18.1` | 44,186 | **100%** |
| `libclang-cpp.so` + Polly + LTO | 41,140 | **100%** |
| `librustc_driver`, `libstd`, `libtest` | 20,697 | **100%** |
| `libstdc++.so.6`, gnu style | 5,990 | **100%** |
| `libLLVM.so.18.1`, gnu style ‖ | 44,049 | **99.93%** |
| `libLLVM`, `libclang-cpp`, `libstdc++`, llvm style | 264,610 | **100%** |
| Swift runtime + Foundation | 48,368 | **100%** |
| Nim standard library routine names ¶ | 5,946 | **99.87%** |
| Free Pascal runtime and packages § | 236,570 | **100%** |
| `libgphobos` + `libgdruntime` (D) | 16,333 | **100%** |
| Go toolchain (`go`, `compile`, `link`) | 30,733 | round trip ‡ |
| Objective-C, 3 ABIs + shipped `libobjc.a` ⁂ | 3,163 | **100%** |
| Swift metadata symbolic references ✻ | 4,528 | **100%** |
| Delphi/C++Builder BPL and DLL export tables ◊ | 11,363 | **100%** |

About 460,000 real symbols, all exact.

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
| Cold — every name distinct | ~24,000 names/sec |
| Warm — names repeat, as in a real symbol table | ~2,200,000 names/sec |
| Non-mangled names rejected | ~510,000 names/sec |
| Full AST construction | ~16,500 names/sec |

Treat these as ratios rather than absolutes. The gap between cold and warm is the point:
symbol tables repeat themselves relentlessly, and results are cached.

The cold figure is lower than earlier releases reported and the demangler is faster than
it was. The benchmark corpus used to be 887 names, most of them cheap MSVC ones; it is
now all 14,041 names in every conformance corpus, across every scheme. What changed is
what is being measured.

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
