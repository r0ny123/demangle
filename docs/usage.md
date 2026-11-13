# Using the library

Every entry point, in the order a caller tends to need them, and the command line at
the end. The [API reference](reference/api.md) has the full signatures.

## The string you probably want

```python
demangle.demangle(name)  # the spelling, or `name` itself
demangle.demangle_strict(name)  # raises DemanglingError instead
```

`demangle()` is built for the case where you are labelling every symbol in a binary and
most of them are not mangled at all. It never raises over a name: one it cannot read
comes back exactly as it went in, because a wrong expansion is worse than a mangled
one -- it matches neither the symbol nor the declaration. What it does raise on is a
mistake in the call itself, such as a `language` that does not exist.

```python
>>> import demangle
>>> demangle.demangle("eqdestroy___systemZassertions_23")
'system/assertions.=destroy'
>>> demangle.demangle("MYUNIT$_$TWIDGET_$__$$_AREA$$LONGINT")
'MYUNIT.TWIDGET.AREA: LONGINT'
>>> demangle.demangle("AddAlignment__9ivTSolverUiP12ivInteractorP7ivTGlue")
'ivTSolver::AddAlignment(unsigned int, ivInteractor *, ivTGlue *)'
>>> demangle.demangle("__dt__6CActorFv")
'CActor::~CActor()'
>>> demangle.demangle("example.com/m/v2%2e5.(*T).Method", language="go")
'example.com/m/v2.5.(*T).Method'
```

## The tree

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

`find()` also takes a node class, which is the form a type checker can follow: it
knows a `Name` has `text` and a `Function` has `parameters`.

```python
>>> from demangle.core.ast import Name
>>> [name.text for name in demangle.parse("_ZNK3Foo3barIiEEvPKc").find(Name)]
['Foo', 'bar']
```

All schemes return full trees. A Rust symbol comes back as a `symbol` holding a
`path` of `name` components, with `impl`, `template`, `type` and `literal` nodes for
what the path carries:

```python
>>> tree = demangle.parse("_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E")
>>> [node.text for node in tree.find("name")]
['core', 'fmt', 'Formatter', 'pad']
```

`to_dict()` turns a tree into plain data, `demangle --json` prints it, and
`node_kinds()` is the vocabulary to switch on rather than something to read out of the
source:

```python
>>> demangle.parse("_Z1fPi").to_dict()
{'kind': 'function', 'name': {'kind': 'name', 'text': 'f'}, 'parameters': [{'kind': 'pointer', 'inner': {'kind': 'builtin', 'spelling': 'int'}}], 'returns': None, 'suffix': ''}
>>> demangle.node_kinds("d")
('name', 'path', 'symbol')
```

A node reached more than once -- an Itanium substitution, a Rust node named both by
position and by role -- is written once with an `id` and afterwards as `{"$ref": id}`.
The structure is a graph, and expanding it in full does not always terminate in useful
time. The nodes carry `__match_args__`, so a `match` statement can test the shape of a
subtree; [the tree's reference](reference/core.md#the-tree) has an example.

[Working with the tree](analysing-a-binary.md) works one real task through `parse()`
end to end, and measures what the regular-expression version of the same question gets
wrong.

## The parts of a name

`signature()` gives the pieces rather than the string -- the base name without its
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
`None` -- which is not `()`. `is_function` is `False` where the name does not say.

## When a name cannot be read

The strict entry points -- `demangle_strict()`, `parse()`, `signature()` and the type
readers -- raise a `DemanglingError`, and which one says what went wrong:

```python
>>> demangle.demangle_strict("main")
Traceback (most recent call last):
    ...
demangle.core.errors.NotMangledError: not a mangled name: 'main'
>>> try:
...     demangle.demangle_strict("_ZN3Foo")
... except demangle.ParseError as error:
...     print(error.position, error)
7 unterminated nested name at offset 7: '_ZN3Foo'
```

`NotMangledError` is a name no scheme claims, `ParseError` one that breaks its scheme's
grammar, with the offset where it did, and `TruncatedError` a `ParseError` for a name
that stops partway. `LimitExceeded` is none of those: the name may be well formed, and
is larger than this call was allowed to read.

A mangled name is untrusted input, and a few dozen bytes can describe a type nested
deeply enough to exhaust the stack. [`Limits`][demangle.core.limits.Limits] bounds the
depth, the output, the substitutions and the input, on every call:

```python
>>> deep = "_Z1f" + "P" * 300 + "i"
>>> demangle.demangle(deep) == deep
True
>>> try:
...     demangle.demangle_strict(deep)
... except demangle.LimitExceeded as error:
...     print(error.limit_name, error.limit_value)
recursion depth 256
>>> demangle.demangle(deep, limits=demangle.RELAXED_LIMITS)[:12]
'f(int*******'
```

The [security policy](SECURITY.md) has the threat model behind the defaults.

## A type on its own

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

`language` is required: a whole symbol announces its scheme -- `_Z`, `?`, `$s` -- and a
type encoding announces nothing at all, so `Si` is `std::istream` to the Itanium reader
and `Swift.Int` to the Swift one. Every reference tool puts this behind a flag for the
same reason.

## Text, not a name

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

## Choosing the scheme

```python
>>> demangle.detect("?f@@YAXH@Z")
'msvc'
>>> list(demangle.demangle_all(["_Z1fv", "main"]))    # shares the cache
['f()', 'main']
```

`detect()` reports what a name looks like, which is cheap; `detect(name, strict=True)`
reports which scheme actually reads it, which costs a parse. `language` forces one
scheme by name, and a sequence of names is an allow-list: detection as usual, among
those schemes alone. [Labelling a symbol table](labelling-a-symbol-table.md#say-which-schemes-the-table-can-hold)
has both at work on a real table.

## Bytes

An ELF or Mach-O string table holds bytes, and they are not reliably UTF-8 -- a
truncated table cuts a name mid-character. Every single-name entry point has a bytes
form, so reading one does not mean guessing an encoding first:

```python
>>> demangle.demangleb(b"_ZN3foo3barEv")
b'foo::bar()'
```

`demangleb_strict`, `detectb`, `parseb`, `signatureb`, `demangleb_type` and
`parseb_type` go with it. Undecodable bytes survive the round trip: `demangleb` hands
back exactly what it was given, byte for byte, rather than raising.

## Styles

The two Itanium reference demanglers, `llvm-cxxfilt` and GNU `c++filt`, legitimately
disagree on some spellings. Both are available, and neither is wrong:

```python
>>> demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi", style="llvm")   # default
'std::vector<int, std::allocator<int>>::push_back(int const&)'
>>> demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi", style="gnu")
'std::vector<int, std::allocator<int> >::push_back(int const&)'
```

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
[`MsvcOptions`][demangle.schemes.msvc.options.MsvcOptions] says which is which and how
far each reaches.

Swift has the bundle Xcode and LLDB show instead of the full spelling -- `Either` for
`Monads.Either`, `(_:)` for `(Swift.Int) -> Swift.UInt`, `specialized f()` for a page of
specialisation arguments. It is `--simplified` on the command line, and exact against
Swift's own 217 vectors:

```python
>>> from demangle.schemes.swift import SIMPLIFIED_OPTIONS
>>> demangle.demangle("_TtFSiSu", style=demangle.style("llvm", swift=SIMPLIFIED_OPTIONS))
'(_:)'
```

## Start-up

Each scheme is imported the first time a name needs it, so a script reading one MSVC
name never loads the Swift reader. A long-running service would rather pay that once,
before its first request:

```python
>>> demangle.preload("itanium", "msvc")    # or preload() for every scheme
```

## Plugins

A separate distribution can add a scheme through the `demangle.languages` entry-point
group. Installed plugins are not loaded on their own: call `demangle.load_plugins()` to
accept them (the `demangle` command does), so an install cannot replace a built-in
scheme unnoticed. [Adding a scheme](adding-a-scheme.md) is the walk-through.

## The command line

```console
$ nm -a libfoo.so | demangle
$ demangle _ZNSt6vectorIiSaIiEE9push_backERKi
$ demangle -b _ZN3foo3barEv                         # `mangled ==> demangled`
$ demangle -m _ZN3foo3barEv                         # only print successfully demangled symbols
$ demangle -s gnu _ZNSt6vectorIiSaIiEE9push_backERKi # choose output style (llvm, gnu)
$ demangle --tree _Z1fPKc
$ demangle --detect _RNvC6_123foo3bar
$ demangle --detect --strict _ZN3Foo               # the scheme that reads it, or an error
$ nm -a Foo.dylib | demangle -l itanium,swift      # detect among these schemes alone
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
$ nm -a libfoo.so | demangle --json-lines          # a JSON object per symbol
$ demangle --json-lines --signature _Z1fPi         # ... with the parts of the name
$ printf '%s\0' "$name" | demangle -0              # whole names ended by NUL, in and out
$ demangle --list-languages                        # list supported schemes and aliases
```

`--types` takes one encoding per argument rather than filtering mixed text: `Pi` is an
ordinary word, and `I like Pi` should stay as it is.

`--json-lines` writes one object per name, `{"mangled": ..., "demangled": ...,
"language": ...}`, where `language` is the scheme that read the name, or `null` where
it came back unchanged; `--signature` adds the fields of `signature()`, `null` for a
name nothing reads, so every record has the same keys. A name whose bytes are not UTF-8
has one more, `mangled_bytes`, the bytes in base64: `mangled` carries each such byte as
a lone surrogate, `"_Z3foo\udcff"`, which most JSON readers turn into U+FFFD; where the
spelling carries them on, `demangled_bytes` does the same for it. Over a stream it
writes one per symbol the filter rewrites. `--json` is the other question, the parse
tree.

`demangle -h` lists the options most runs use and `demangle --help` all of them, with
the exit statuses: 0 when every name was read or printed unchanged, 1 when one was
refused -- under `--strict`, or with no tree to print for `--tree` or `--json` -- 2 for
a usage error, and 130 after Ctrl-C. `python -m demangle` is the same command, where
the script is not on `PATH`.
