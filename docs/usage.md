# Using the library

The entry points, roughly in the order you'll need them, and the command line at the
end. The [API reference](reference/api.md) has the full signatures.

## Demangling a name

```python
demangle.demangle(name)  # the spelling, or `name` itself
demangle.demangle_strict(name)  # raises DemanglingError instead
```

`demangle()` never raises over a name. One it can't read comes back exactly as it went
in, so you can run it over every symbol in a binary, most of which aren't mangled
anyway. It only raises when the call itself is wrong, say a `language` that doesn't
exist.

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

`parse()` gives you the tree the spelling is rendered from:

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

`find()` also takes a node class. That's the form a type checker can follow, since it
knows a `Name` has `text` and a `Function` has `parameters`:

```python
>>> from demangle.core.ast import Name
>>> [name.text for name in demangle.parse("_ZNK3Foo3barIiEEvPKc").find(Name)]
['Foo', 'bar']
```

All schemes return full trees. A Rust symbol, for one, is a `symbol` holding a `path`
of `name` components:

```python
>>> tree = demangle.parse("_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E")
>>> [node.text for node in tree.find("name")]
['core', 'fmt', 'Formatter', 'pad']
```

`to_dict()` turns a tree into plain data (`demangle --json` prints it), and
`node_kinds()` lists the kinds a scheme can produce, so you don't have to dig them out
of the source:

```python
>>> demangle.parse("_Z1fPi").to_dict()
{'kind': 'function', 'name': {'kind': 'name', 'text': 'f'}, 'parameters': [{'kind': 'pointer', 'inner': {'kind': 'builtin', 'spelling': 'int'}}], 'returns': None, 'suffix': ''}
>>> demangle.node_kinds("d")
('name', 'path', 'symbol')
```

A node that's reached more than once, like an Itanium substitution, is written once
with an `id` and after that as `{"$ref": id}`. The structure is a graph, and writing it
out in full can blow up.

The nodes also work with `match`; [the tree's reference](reference/core.md#the-tree)
has an example. [Working with the tree](analysing-a-binary.md) takes one real question
through `parse()` from start to finish.

## The parts of a name

`signature()` gives you the pieces instead of the string: the base name without its
scope, the scope on its own, the parameter types.

```python
>>> parts = demangle.signature("_ZNSt6vectorIiSaIiEE9push_backERKi")
>>> parts.namespace, parts.base_name
('std::vector<int, std::allocator<int>>', 'push_back')
>>> parts.parameters
('int const&',)
>>> demangle.signature("$s4main3FooV3baryS2i_SStF").return_type
'Swift.Int'
```

`namespace`, the scheme's separator and `base_name` put back together give
`qualified_name` exactly. You can't get that by splitting the string, because `::`,
`.` and `,` also turn up inside template arguments and operator names.

Schemes don't all record the same things, and the fields say so. A C++ or Swift name
carries a signature. A Rust or Go path doesn't, so its `parameters` is `None`, which
isn't the same as `()`, and `is_function` is `False` where the name doesn't say.

## Errors and limits

`demangle_strict()`, `parse()`, `signature()` and the type readers raise a
`DemanglingError` when they can't read a name. The subclass tells you why:

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

| Error | Means |
| --- | --- |
| `NotMangledError` | No scheme claims the name. |
| `ParseError` | The name breaks its scheme's grammar; `position` is where. |
| `TruncatedError` | A `ParseError` for a name that stops partway. |
| `LimitExceeded` | The name may be fine, but it's bigger than this call was allowed to read. |

That last one exists because a mangled name is untrusted input: a few dozen bytes can
describe a type nested deep enough to exhaust the stack.
[`Limits`][demangle.core.limits.Limits] caps the depth, the output, the substitutions
and the input on every call:

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

The defaults sit well above anything a compiler emits. `RELAXED_LIMITS` is for input
you trust. The [security policy](SECURITY.md) has the reasoning.

## Bare types

A `typeinfo` name, an RTTI type descriptor and a Swift metadata typeref carry a type
rather than a symbol. `demangle_type()` reads one and `parse_type()` gives its tree:

```python
>>> demangle.demangle_type("PKFvRiE", language="itanium")
'void (*)(int&) const'
>>> demangle.demangle_type("PEAX", language="msvc")
'void *'
>>> demangle.demangle_type("SaySiG", language="swift")
'[Swift.Int]'
```

`language` is required here. A whole symbol announces its scheme with `_Z`, `?` or
`$s`, but a type encoding announces nothing: `Si` is `std::istream` to the Itanium
reader and `Swift.Int` to the Swift one.

## Text and streams

`nm` puts an address and a type letter before each name, and a crash log puts a frame
number. `demangle_text()` rewrites every symbol-shaped word and leaves the rest alone,
which is also what the command does with no arguments:

```python
>>> demangle.demangle_text("0000000000001139 T _ZN3foo3barEv")
'0000000000001139 T foo::bar()'
>>> [f.mangled for f in demangle.find_symbols("a _ZN3foo3barEv b")]
['_ZN3foo3barEv']
```

`find_symbols()` also gives each symbol's span, for when you need to know where in the
line it was. `demangle_stream(sys.stdin, sys.stdout)` does the same a line at a time.

## Choosing the scheme

```python
>>> demangle.detect("?f@@YAXH@Z")
'msvc'
>>> list(demangle.demangle_all(["_Z1fv", "main"]))    # shares the cache
['f()', 'main']
```

`detect()` says what a name looks like, which is cheap. `detect(name, strict=True)`
says which scheme actually reads it, which costs a parse.

`language="msvc"` forces one scheme. A sequence, `language=("itanium", "swift")`, is an
allow-list: detection runs as usual, but only among those.
[Labelling a symbol table](labelling-a-symbol-table.md#say-which-schemes-the-table-can-hold)
shows both on a real table.

## Bytes

A string table holds bytes, and they aren't always valid UTF-8: a truncated table can
cut a name mid-character. Every single-name entry point has a bytes form, so you don't
have to guess an encoding first:

```python
>>> demangle.demangleb(b"_ZN3foo3barEv")
b'foo::bar()'
```

The others are `demangleb_strict`, `detectb`, `parseb`, `signatureb`, `demangleb_type`
and `parseb_type`. Bytes that don't decode come back exactly as given.

## Styles

`llvm-cxxfilt` and GNU `c++filt` spell some Itanium names differently, and neither is
wrong. Pick whichever you need to match:

```python
>>> demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi", style="llvm")   # default
'std::vector<int, std::allocator<int>>::push_back(int const&)'
>>> demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi", style="gnu")
'std::vector<int, std::allocator<int> >::push_back(int const&)'
```

`style()` builds a style that leaves things out, for one call, without registering
anything globally:

```python
>>> demangle.demangle("?g@C@@UEAAXXZ")
'public: virtual void __cdecl C::g(void)'
>>> narrow = demangle.style("llvm", msvc={"calling_convention": False, "access_specifier": False})
>>> demangle.demangle("?g@C@@UEAAXXZ", style=narrow)
'virtual void C::g(void)'
```

MSVC has nine of these options, each with a command-line flag;
[`MsvcOptions`][demangle.schemes.msvc.options.MsvcOptions] lists them.

Swift has the short form Xcode and LLDB show: `Either` for `Monads.Either`, `(_:)` for
`(Swift.Int) -> Swift.UInt`. It's `--simplified` on the command line.

```python
>>> from demangle.schemes.swift import SIMPLIFIED_OPTIONS
>>> demangle.demangle("_TtFSiSu", style=demangle.style("llvm", swift=SIMPLIFIED_OPTIONS))
'(_:)'
```

## Start-up

A scheme is imported the first time a name needs it, so a script that reads one MSVC
name never loads the Swift reader. That first import takes a few milliseconds (about 7
for MSVC), which a long-running service may prefer to pay before its first request:

```python
>>> demangle.preload("itanium", "msvc")    # or preload() for every scheme
```

## Plugins

Another package can add a scheme through the `demangle.languages` entry-point group.
Installed plugins aren't loaded until you call `demangle.load_plugins()` (the `demangle`
command does), so installing something can't quietly replace a built-in scheme.
[Adding a scheme](adding-a-scheme.md) shows how to write one.

## The command line

`demangle` is a filter like `c++filt`, over any mix of schemes. `python -m demangle` is
the same command, for when the script isn't on `PATH`.

```console
$ nm -a libfoo.so | demangle
$ demangle _ZNSt6vectorIiSaIiEE9push_backERKi
$ demangle -b _ZN3foo3barEv                         # `mangled ==> demangled`
$ demangle -m _ZN3foo3barEv                         # only the names it could read
$ demangle -s gnu _ZNSt6vectorIiSaIiEE9push_backERKi # output style: llvm or gnu
$ demangle --strip-underscore '_?f@@YAXH@Z'         # ignore one leading underscore
$ printf '%s\0' "$name" | demangle -0               # names ended by NUL, in and out
```

Choosing the scheme:

```console
$ demangle --detect _RNvC6_123foo3bar
$ demangle --detect --strict _ZN3Foo               # the scheme that reads it, or an error
$ nm -a Foo.dylib | demangle -l itanium,swift      # detect among these schemes only
$ demangle --types -l itanium PKFvRiE              # a bare type, as `c++filt -t`
$ demangle --list-languages                        # schemes and their aliases
```

Printing less than the full declaration:

```console
$ demangle -p _ZNSt6vectorIiSaIiEE9push_backERKi   # the name, without the signature
$ demangle --base-name _ZSt4sortIPiEvT_S1_         # `sort<int*>`
$ demangle --no-return-type _ZSt4sortIPiEvT_S1_    # the declaration, minus `void `
$ demangle --ret-postfix _Z1fIiET_S0_              # `f<int>(int)int`, as `DMGL_RET_POSTFIX`
$ demangle --keep-hash _ZN4core3fmt5write17h05af221e174051e9E   # keep Rust's hash
$ demangle --no-calling-convention '?f@@YAXH@Z'    # `void f(int)`
$ demangle --no-tag-kind '?g3@@YAXVV@@@Z'          # `void __cdecl g3(V)`
$ demangle --simplified _TtFSiSu                   # Swift, the way Xcode shows it
```

The tree and JSON:

```console
$ demangle --tree _Z1fPKc
$ demangle --json _Z1fPi                           # the parse tree as JSON
$ nm -a libfoo.so | demangle --json-lines          # a JSON object per symbol
$ demangle --json-lines --signature _Z1fPi         # ... with the parts of the name
```

`--types` takes one encoding per argument instead of filtering text, because `Pi` is an
ordinary word and `I like Pi` should stay as it is.

`--json-lines` writes `{"mangled": ..., "demangled": ..., "language": ...}` per name.
`language` is the scheme that read it, or `null` if it came back unchanged.
`--signature` adds the fields of `signature()`, all `null` for a name nothing reads, so
every record has the same keys. Over a stream you get one object per symbol the filter
rewrites.

A name whose bytes aren't UTF-8 gets one more key, `mangled_bytes`, with the bytes in
base64. `mangled` carries each such byte as a lone surrogate (`"_Z3foo\udcff"`), which
most JSON readers turn into U+FFFD. `demangled_bytes` does the same for the spelling
when the bytes carry through to it.

`demangle -h` lists the common options and `demangle --help` all of them. The exit
status is 0 when every name was read or printed unchanged, 1 when one was refused
(under `--strict`, or with no tree to print for `--tree` or `--json`), 2 for a usage
error and 130 after Ctrl-C.
