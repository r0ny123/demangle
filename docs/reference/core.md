# Core

The scheme-agnostic half. Nothing here imports a scheme, and a new mangling scheme is
written against these and nothing else.

## The builder protocol

The contract between a parser and its output, and the reason one parser can serve both
`demangle()` and `parse()` without a second implementation to keep in sync.

::: demangle.core.builder

## Spelling

C++ does not write a type before the name, it writes it *around* the name. This is the
module that keeps the hole in the right place.

::: demangle.core.spelling
    options:
      members:
        - Spelling
        - SpellingBuilder
        - pack_of

## The tree

`Node` itself is documented once, on the [public API page](api.md#the-tree), because
that is where a caller meets it -- two renderings of one class give `mkdocs-autorefs`
two primary URLs for it and every cross-reference then picks one at random. What is here
is the rest of `core.ast`: the node classes a tree is made of, the builder a scheme hands
them to, and the wrapper that carries what the linker appended to a name.

The classes are the vocabulary of a `match` statement. Every one of them declares
`__match_args__`, so a caller can match on the shape of a subtree rather than compare
`node.kind` against a string:

```python
import demangle
from demangle.core.ast import Builtin, Pointer

match demangle.parse("_Z1fPi").parameters[0]:
    case Pointer(Builtin(spelling)):
        print("pointer to", spelling)
```

`demangle.node_kinds()` answers the same question the other way round, for a caller
switching on the `kind` string instead: it lists the kinds a given scheme can produce.

::: demangle.core.ast
    options:
      members:
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

What the linker and the compiler append to a name, which belongs to no mangling scheme.

::: demangle.core.decorations

## Caching

::: demangle.core.cache
