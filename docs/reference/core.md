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

What `parse()` returns.

::: demangle.core.ast
    options:
      members:
        - Node
        - AstBuilder

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
