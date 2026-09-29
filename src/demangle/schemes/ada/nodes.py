"""The Ada symbol tree.

The same parts-in-order shape as the Go, Nim, D and JNI trees: a node is the fragments
it was built from, so rendering concatenates and the tree cannot spell a symbol
differently from the text path.

What the structure is for is the one question the spelling cannot answer. An Ada name is
a dotted path, but `text.split(".")` is wrong the moment a component is an operator --
`oper."+"`, and `system.finalization_root.":="` has a `.` *inside* a component -- or the
entity carries a `.Finalize`. The `parts` here were separated while reading, so they are
the components the parser actually found.
"""

from ...core.ast import Node, rendered

__all__ = ["Attribute", "Component", "Symbol", "build"]


class _Ada(Node):
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
        # Ada symbols carry no signature; accepted to match `Node.spell`.
        return rendered(self.render)

    def build(self, builder):
        return builder.raw(self.render())

    @property
    def text(self):
        return self.render()


class Component(_Ada):
    """One element of the path: a unit, a subprogram, or a quoted operator name."""

    __slots__ = ()
    kind = "component"


class Attribute(_Ada):
    """What the entity is, where GNAT encoded that rather than leaving it implicit.

    `'Size`, `'Elab_Body`, `'Read`, `.Finalize`. Written by the compiler rather than the
    programmer, and carrying its own leading punctuation because the reference does --
    an attribute joins with `'` and a controlled-type operation with `.`.
    """

    __slots__ = ()
    kind = "attribute"


class Symbol(_Ada):
    """A whole GNAT symbol."""

    __slots__ = ()
    kind = "symbol"


#: A part starting with one of these is an attribute of the entity just named, not a name.
_ATTRIBUTE_STARTS = ("'", ".")


def build(symbol):
    """Assemble the tree for a parsed `AdaSymbol`."""
    parts = []
    for at, part in enumerate(symbol.parts):
        if part.startswith(_ATTRIBUTE_STARTS):
            parts.append(Attribute([part]))
            continue
        if at:
            parts.append(".")
        parts.append(Component([part]))
    return Symbol(parts)
