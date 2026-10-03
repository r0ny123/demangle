"""The scheme-agnostic half of the library.

Nothing here imports a mangling scheme at import time -- `style` reaches for the
schemes' option defaults lazily, the first time a style is asked for -- and a new
scheme is written against these modules and nothing else:

- `builder` -- the contract between a parser and its output, and the reason one parser
  can serve both `demangle()` and `parse()` with no second implementation to drift.
- `spelling` -- C-family declarator placement, shared by every scheme that spells its
  types the way C does.
- `ast` -- the tree `parse()` returns.
- `reader` -- a bounds-checked cursor; the input primitive for a new parser.
- `errors`, `limits` -- the failure and resource contracts.
- `plugin`, `registry` -- how a scheme announces itself.
- `decorations` -- what a symbol table appends to a name, which belongs to no scheme.
- `style` -- the choices on which the reference implementations legitimately differ.

An explicit `__init__` rather than a namespace package: an implicit one silently merges
with any same-named directory another installed distribution happens to ship, which for
a package called `core` is not a remote possibility.
"""
