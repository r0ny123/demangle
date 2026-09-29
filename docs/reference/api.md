# Public API

What `import demangle` gives a caller. A few of its exports belong to modules documented
with the rest of the core; [the list at the end](#documented-elsewhere) says where.

```python
import demangle

demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi")
# 'std::vector<int, std::allocator<int>>::push_back(int const&)'
demangle.demangle_type("PKFvRiE", language="itanium")
# 'void (*)(int&) const'
demangle.demangle("?f@@YAXH@Z", style=demangle.style("llvm", msvc={"calling_convention": False}))
# 'void f(int)'
demangle.signature("_ZNSt6vectorIiSaIiEE9push_backERKi").base_name
# 'push_back'
```

## Functions

::: demangle.api
    options:
      members:
        - demangle
        - demangle_strict
        - parse
        - detect
        - demangle_all
        - demangle_type
        - parse_type
        - style
        - node_kinds
        - languages
        - styles
        - demangleb
        - demangleb_strict
        - demangleb_type
        - parseb
        - parseb_type
        - detectb
        - cache_clear
        - cache_stats

## A file, not a name

::: demangle.filter

## The parts of a name

::: demangle._signature
    options:
      show_root_heading: false
      heading_level: 3

## Errors

What the strict entry points raise when a name cannot be read. A plugin that fails some
other way is wrapped in a `ParseError`, with the original chained; an argument that is
wrong rather than unreadable is a `TypeError` or a `ValueError`, as each function says.

::: demangle.core.errors
    options:
      members:
        - DemanglingError
        - NotMangledError
        - ParseError
        - TruncatedError
        - LimitExceeded

## Limits

::: demangle.core.limits

## Documented elsewhere

- [`Node`][demangle.core.ast.Node] and [`Decorated`][demangle.core.ast.Decorated]:
  [the tree](core.md#the-tree).
- [`Style`][demangle.core.style.Style] and
  [`register_style`][demangle.core.style.register_style]: [styles](core.md#styles).
- [`LanguagePlugin`][demangle.core.plugin.LanguagePlugin] and `register_language`, which is
  [`demangle.core.registry.register`][demangle.core.registry.register]:
  [plugins](core.md#plugins).
- The options objects `style()` takes, one per scheme that has any:
  [`ItaniumOptions`][demangle.schemes.itanium.options.ItaniumOptions],
  [`MsvcOptions`][demangle.schemes.msvc.options.MsvcOptions],
  [`RustOptions`][demangle.schemes.rust.options.RustOptions],
  [`SwiftOptions`][demangle.schemes.swift.options.SwiftOptions],
  [`GnuV2Options`][demangle.schemes.gnuv2.options.GnuV2Options] and
  [`CodeWarriorOptions`][demangle.schemes.codewarrior.options.CodeWarriorOptions].
