# Core

The scheme-agnostic half. A new mangling scheme is written against these and nothing
else; [Architecture](../ARCHITECTURE.md) has the layering rules.

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

The node classes a tree is made of, and the builder a scheme hands them to. Every class
declares `__match_args__`, so a caller can match on the shape of a subtree rather than
compare `node.kind` against a string:

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

A distribution can add a scheme of its own by calling `register` or by advertising a
`demangle.languages` entry point. Advertised plugins load only when a program calls
`demangle.load_plugins()` (the `demangle` command does); `register` always takes
effect. [Adding a scheme](../adding-a-scheme.md) is the walk-through.

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

::: demangle.core.style
    options:
      members:
        - Style
        - register_style
        - get_style
        - available_styles

## Caching

::: demangle.core.cache
