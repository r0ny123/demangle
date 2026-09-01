# Public API

A handful of functions cover what callers actually do. Everything else on this page is either an
error type you may want to catch or an extension point you may want to use.

```python
import demangle

demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi")
# 'std::vector<int, std::allocator<int>>::push_back(int const&)'
```

::: demangle.api
    options:
      members:
        - demangle
        - demangle_strict
        - parse
        - detect
        - demangle_all
        - languages
        - styles
        - cache_clear
        - cache_stats

## A type on its own

A `typeinfo` name, an MSVC RTTI type descriptor and a Swift metadata typeref carry a
*type* rather than a symbol. `language` is required for these two and cannot be made
optional: a symbol announces its scheme -- `_Z`, `?`, `$s` -- and a type encoding
announces nothing at all, so `Si` is `std::istream` read as Itanium and `Swift.Int` read
as Swift.

```python
demangle.demangle_type("PKFvRiE", language="itanium")
# 'void (*)(int&) const'
```

::: demangle.api
    options:
      members:
        - demangle_type
        - parse_type

## Printing less of a name

The two named styles say how to spell a name; what a caller usually wants to vary is how
*much* of it to spell. `style()` composes that for one call, without registering
anything: the result is an object, and `demangle()` does not cache a call that passes
one, so a narrower spelling asked for here is never served to a caller asking for the
default.

```python
demangle.demangle("?f@@YAXH@Z", style=demangle.style("llvm", msvc={"calling_convention": False}))
# 'void f(int)'
```

MSVC's nine options come from two references, and which one names a field decides how far
that field reaches. The five `llvm-undname` flags are declaration-level: they apply to the
declaration and to the types written inside it, and stop at the edge of the symbol. The
four `UnDecorateSymbolName` mask bits are lexical, and reach every occurrence of what they
name — including the ones inside a template argument and inside the enclosing symbol a
local name is scoped by. `MsvcOptions` below says which is which.

::: demangle.api
    options:
      members:
        - style

::: demangle.schemes.msvc.options
    options:
      members:
        - MsvcOptions

::: demangle.schemes.swift.options
    options:
      members:
        - SwiftOptions

::: demangle.schemes.gnuv2.options
    options:
      members:
        - GnuV2Options

::: demangle.schemes.codewarrior.options
    options:
      members:
        - CodeWarriorOptions

## A file, not a name

`nm` writes an address and a type letter before a name. The useful operation over a file
is to substitute every symbol-shaped word and copy the rest through, which is what the
command does with no arguments and what these do from Python.

::: demangle.filter
    options:
      members:
        - demangle_text
        - demangle_stream
        - find_symbols
        - Found

## The parts of a name

`demangle()` answers what a name says; `signature()` answers what its pieces are. The
split is done on the tree rather than on the string, because `::` and `.` and `,` occur
inside template arguments and operator names as well as between components.

```python
demangle.signature("_ZNSt6vectorIiSaIiEE9push_backERKi").base_name
# 'push_back'
```

::: demangle._signature
    options:
      members:
        - signature
        - signatureb
        - Signature

## Bytes

A symbol table holds bytes, and they are not reliably UTF-8. Every entry point has a
bytes form, so reading one does not mean guessing an encoding first; undecodable bytes
survive the round trip rather than raising.

::: demangle.api
    options:
      members:
        - demangleb
        - demangleb_strict
        - demangleb_type
        - parseb
        - parseb_type
        - detectb

## The tree

`parse()` returns nodes to walk; `to_dict()` turns one into plain data, and
`node_kinds()` is the vocabulary to switch on. The nodes carry `__match_args__`, so
structural pattern matching works over them.

::: demangle.api
    options:
      members:
        - node_kinds

::: demangle.core.ast
    options:
      members:
        - Node

## Registering another scheme

A distribution can add a scheme of its own without being part of this one, either by
calling it or by advertising a `demangle.languages` entry point.
[Adding a scheme](../adding-a-scheme.md) is the walk-through. The package exports this
under the name `demangle.register_language`; it is `demangle.core.registry.register`,
spelled unambiguously for a caller who has imported the package and nothing else.

::: demangle.core.registry
    options:
      members:
        - register

## Errors

`demangle()` never raises. `demangle_strict()` and `parse()` raise these, and nothing
else -- a plugin that fails some other way is wrapped, with the original chained.

::: demangle.core.errors
    options:
      members:
        - DemanglingError
        - NotMangledError
        - ParseError
        - TruncatedError
        - LimitExceeded

## Limits

A mangled name is untrusted input. Every bound is configurable per call.

::: demangle.core.limits

## Styles

Where the reference demanglers legitimately disagree, the choice is a style rather than
a silent decision in the parser.

::: demangle.core.style
    options:
      members:
        - Style
        - get_style
        - register_style
        - available_styles
