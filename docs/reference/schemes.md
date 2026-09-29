# Schemes

Each mangling scheme is a plugin. `core` never imports one and they never import each
other, so any of them can be developed, replaced or shipped separately -- see
[Adding a scheme](../adding-a-scheme.md).

Each section renders what the scheme's package exports, then the modules behind it: its
options, its node kinds and, where it is worth reading, its parser. Each object appears
once, with the module that says most about it -- an options class with its options.

## Itanium C++ ABI

::: demangle.schemes.itanium
    options:
      members:
        - PLUGIN
        - detect
        - parse
        - parse_type
        - ItaniumParser

### Options

::: demangle.schemes.itanium.options
    options:
      heading_level: 3

### Substitutions

::: demangle.schemes.itanium.substitutions
    options:
      heading_level: 3

### Parser

::: demangle.schemes.itanium.parser
    options:
      heading_level: 3
      members: false

## Microsoft Visual C++

::: demangle.schemes.msvc
    options:
      members:
        - PLUGIN
        - detect
        - parse
        - parse_type

### Options

::: demangle.schemes.msvc.options
    options:
      heading_level: 3

### Nodes

::: demangle.schemes.msvc.nodes
    options:
      heading_level: 3
      members:
        - Indirection
        - FunctionType
        - Declaration
        - render
        - spelled_after
        - prefixed
        - merge_qualifiers
        - ordered_qualifiers
        - apply_qualifiers
        - is_member_function_pointer
        - qualify_declared

## Rust

::: demangle.schemes.rust

### Options

::: demangle.schemes.rust.options
    options:
      heading_level: 3

### Nodes

::: demangle.schemes.rust.nodes
    options:
      heading_level: 3

## Swift

::: demangle.schemes.swift
    options:
      members:
        - PLUGIN
        - detect
        - parse
        - parse_type
        - demangle_symbol
        - demangle_type
        - demangle_old_symbol
        - print_root
        - demangle_symbolic
        - typerefs
        - SymbolicReference
        - scan
        - end_of_name
        - Image
        - elf_image
        - macho_image
        - ContextResolver

### Options

::: demangle.schemes.swift.options
    options:
      heading_level: 3

### Demangler

::: demangle.schemes.swift._demangler
    options:
      show_root_heading: false
      heading_level: 4
      members:
        - Demangler

### The Swift 3 mangling

::: demangle.schemes.swift._old_demangler
    options:
      show_root_heading: false
      heading_level: 4
      members:
        - OldDemangler

### Printer

::: demangle.schemes.swift._printer
    options:
      show_root_heading: false
      heading_level: 4
      members:
        - Printer

### Symbolic references

::: demangle.schemes.swift.symbolic
    options:
      heading_level: 3
      members:
        - read
        - names

### Resolving one

::: demangle.schemes.swift.resolve
    options:
      heading_level: 3
      members:
        - MalformedImage

### Nodes

::: demangle.schemes.swift.nodes
    options:
      heading_level: 3

## D

::: demangle.schemes.d

### Nodes

::: demangle.schemes.d.nodes
    options:
      heading_level: 3

## Go

::: demangle.schemes.go

### Nodes

::: demangle.schemes.go.nodes
    options:
      heading_level: 3

## Nim

::: demangle.schemes.nim

### Parser

::: demangle.schemes.nim._parser
    options:
      show_root_heading: false
      heading_level: 4
      members:
        - mangle
        - unmangle
        - mangle_module
        - unmangle_module

### Nodes

::: demangle.schemes.nim.nodes
    options:
      heading_level: 3

## Free Pascal

::: demangle.schemes.pascal

### Parser

::: demangle.schemes.pascal._parser
    options:
      show_root_heading: false
      heading_level: 4
      members:
        - spell_routine_name

### Nodes

::: demangle.schemes.pascal.nodes
    options:
      heading_level: 3

## Pre-Itanium C++

::: demangle.schemes.gnuv2
    options:
      members:
        - PLUGIN
        - STYLES
        - detect
        - parse
        - GnuV2Symbol
        - demangle_gnuv2

### Options

::: demangle.schemes.gnuv2.options
    options:
      heading_level: 3

### Parser

::: demangle.schemes.gnuv2._parser
    options:
      show_root_heading: false
      heading_level: 4
      members: false

### Nodes

::: demangle.schemes.gnuv2.nodes
    options:
      heading_level: 3

## CodeWarrior

::: demangle.schemes.codewarrior
    options:
      members:
        - PLUGIN
        - detect
        - parse
        - CodeWarriorSymbol
        - demangle_codewarrior

### Options

::: demangle.schemes.codewarrior.options
    options:
      heading_level: 3

### Parser

::: demangle.schemes.codewarrior._parser
    options:
      show_root_heading: false
      heading_level: 4
      members: false

### Nodes

::: demangle.schemes.codewarrior.nodes
    options:
      heading_level: 3

## Delphi / C++Builder

::: demangle.schemes.delphi

### Parser

::: demangle.schemes.delphi._parser
    options:
      show_root_heading: false
      heading_level: 4
      members: false

### Nodes

::: demangle.schemes.delphi.nodes
    options:
      heading_level: 3

## Ada / GNAT

As with Go, a name that spells itself can still be a reading rather than a refusal:
`demangle_ada("x")` reads `x` as a unit of that name, because a bare lower-case identifier
is a valid Ada unit name. `detect` still declines it, so autodetection never claims it.

::: demangle.schemes.ada

### Nodes

::: demangle.schemes.ada.nodes
    options:
      heading_level: 3

## JNI

::: demangle.schemes.jni

### Nodes

::: demangle.schemes.jni.nodes
    options:
      heading_level: 3

## Objective-C

::: demangle.schemes.objc

### Parser

::: demangle.schemes.objc._parser
    options:
      show_root_heading: false
      heading_level: 4
      members:
        - decode_type_encoding

### Nodes

::: demangle.schemes.objc.nodes
    options:
      heading_level: 3
