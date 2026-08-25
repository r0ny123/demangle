"""The Nim symbol tree.

The same parts-in-order shape as the Rust, Go and D trees: a node is the fragments it was
built from, so rendering concatenates and the tree cannot spell a symbol differently from
the text path.

Splitting the spelling on `.` would not do: a Nim module path holds `/`, and a routine
name can be `.` itself -- `dot___ops_17` is the operator `.`, whose demangling is
`ops..`. The tree hands the two halves over already separated.
"""

from ...core.ast import Node

__all__ = ["NimName", "Path", "Symbol", "build"]


class _Nim(Node):
    __slots__ = ("parts",)

    def __init__(self, parts):
        self.parts = tuple(parts)
        total = 0
        for part in self.parts:
            total += len(part) if isinstance(part, str) else part.size
        self.size = total

    def children(self):
        return tuple(part for part in self.parts if isinstance(part, Node))

    def render(self):
        return "".join(part if isinstance(part, str) else part.render() for part in self.parts)

    def spell(self, declarator="", style=None):
        # Accepted to match `Node.spell` and ignored: Nim has no declarator position.
        return self.render()

    def build(self, builder):
        return builder.raw(self.render())

    @property
    def text(self):
        return self.render()


class NimName(_Nim):
    """A routine or variable name, with its escapes undone."""

    __slots__ = ()
    kind = "name"


class Path(_Nim):
    """The module the symbol belongs to, as a path with `/` separators."""

    __slots__ = ()
    kind = "path"


class Symbol(_Nim):
    """A whole Nim symbol.

    `nim_kind` is what the mangling said this is -- `routine`, `type`, `type-info`,
    `marker` or `temporary` -- which the spelling alone does not always make plain.
    """

    __slots__ = ("nim_kind",)
    kind = "symbol"

    def __init__(self, parts, nim_kind):
        super().__init__(parts)
        self.nim_kind = nim_kind


def build(symbol):
    """Assemble the tree for a parsed `NimSymbol`."""
    if symbol.kind == "routine":
        return Symbol([Path([symbol.module]), ".", NimName([symbol.name])], symbol.kind)
    if symbol.name:
        prefix = symbol.text[: len(symbol.text) - len(symbol.name)]
        return Symbol([prefix, NimName([symbol.name])], symbol.kind)
    return Symbol([symbol.text], symbol.kind)
