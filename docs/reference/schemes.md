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

## Swift

The largest grammar here by a wide margin. The mangling is postfix and compresses against
three tables that span a whole name, so the demangler is a stack machine rather than
recursive descent; and how a node is *spelled* depends on where it sits, so the printer
is a separate pass.

::: demangle.schemes.swift

### Demangler

::: demangle.schemes.swift._demangler
    options:
      members:
        - Demangler
        - demangle_symbol

### The Swift 3 mangling

A different grammar, still what the ObjC runtime holds for a Swift class.

::: demangle.schemes.swift._old_demangler
    options:
      members:
        - OldDemangler
        - demangle_old_symbol

### Printer

::: demangle.schemes.swift._printer
    options:
      members:
        - Printer
        - print_root

## D

::: demangle.schemes.d

## Go

::: demangle.schemes.go

## Nim

::: demangle.schemes.nim

### Parser

The mangling is not injective, and both places it loses information are documented here
alongside what the decoder does about them.

::: demangle.schemes.nim._parser
    options:
      members:
        - mangle
        - unmangle
        - mangle_module
        - unmangle_module
        - parse_nim_symbol
        - detect

## Free Pascal

::: demangle.schemes.pascal

### Parser

::: demangle.schemes.pascal._parser
    options:
      members:
        - PascalSymbol
        - parse_pascal_symbol
        - spell_routine_name
        - detect
