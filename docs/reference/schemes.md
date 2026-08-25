# Schemes

Each mangling scheme is a plugin. `core` never imports one and they never import each
other, so any of them can be developed, replaced or shipped separately -- see
[Adding a scheme](../adding-a-scheme.md).

## Itanium C++ ABI

GCC, Clang, and essentially every C++ toolchain outside the Microsoft ecosystem.

::: demangle.schemes.itanium

### Options

::: demangle.schemes.itanium.options

### Substitutions

Back-reference numbering is implicit in the encoding, so one wrong entry silently
corrupts every later reference in a name. It lives in its own module for that reason.

::: demangle.schemes.itanium.substitutions

### Parser

::: demangle.schemes.itanium.parser
    options:
      members:
        - ItaniumParser
        - detect
        - parse

## Microsoft Visual C++

::: demangle.schemes.msvc

### Nodes

MSVC's declarator spelling is its own -- calling conventions sit inside the parentheses,
and the spacing rules differ -- so it supplies its own node kinds and renderer rather
than reusing the shared C-family one.

::: demangle.schemes.msvc.nodes
    options:
      members:
        - Indirection
        - FunctionType
        - Declaration
        - render

## Rust

::: demangle.schemes.rust
