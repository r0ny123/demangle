# Core

The scheme-agnostic half. Nothing here imports a scheme at import time, and a new mangling
scheme is written against these and nothing else.

## The builder protocol

::: demangle.core.builder

## Spelling

::: demangle.core.spelling
    options:
      members:
        - Spelling
        - SpellingBuilder
        - pack_of

## The tree

The node classes a tree is made of -- `Node` and `Decorated` are also exported from
`demangle` -- and the builder a scheme hands them to. Every class declares
`__match_args__`, so a caller can match on the shape of a subtree rather than compare
`node.kind` against a string:

```python
import demangle
from demangle.core.ast import Builtin, Pointer

match demangle.parse("_Z1fPi").parameters[0]:
    case Pointer(Builtin(spelling)):
        print("pointer to", spelling)
```

A scheme's own node kinds are under its section on the [schemes page](schemes.md), and
`demangle.node_kinds()` lists the `kind` strings a scheme can produce.

::: demangle.core.ast
    options:
      members:
        - Node
        - Builtin
        - Name
        - Raw
        - Literal
        - Expression
        - Qualified
        - Template
        - Qualify
        - Pointer
        - Reference
        - RValueReference
        - Pack
        - ParameterPack
        - MemberPointer
        - Array
        - VendorQualify
        - Function
        - Decorated
        - Special
        - AstBuilder
        - builder_for

## Reading

::: demangle.core.reader

## Plugins

A distribution can add a scheme of its own by calling `register`, which the package
exports as `demangle.register_language`, or by advertising a `demangle.languages` entry
point. [Adding a scheme](../adding-a-scheme.md) is the walk-through.

::: demangle.core.plugin

::: demangle.core.registry
    options:
      members:
        - register
        - get
        - available
        - names
        - aliases

## Symbol-table decorations

::: demangle.core.decorations

## Styles

`Style` and `register_style` are also exported from `demangle`, and `demangle.style()`
composes a style for one call.

::: demangle.core.style
    options:
      members:
        - Style
        - register_style
        - get_style
        - available_styles

## Caching

::: demangle.core.cache
