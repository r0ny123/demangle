"""The Free Pascal symbol tree.

The same parts-in-order shape as the other schemes: a node is the fragments it was built
from, so rendering concatenates and the tree cannot spell a symbol differently from the
text path.

What a caller wants from one of these is the unit, the class, and the signature -- none of
which survives splitting the spelling, because a parameter type can be a generic whose own
name holds a `$` and a `.`.
"""

from ...core.ast import Node, rendered

__all__ = ["Parameters", "PascalName", "Symbol", "Unit", "build"]


class _Pascal(Node):
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
        # `declarator` is a C++ notion; accepted to match `Node.spell`.
        return rendered(self.render)

    def build(self, builder):
        return builder.raw(self.render())

    @property
    def text(self):
        return self.render()


class Unit(_Pascal):
    """The unit, or the program, the symbol belongs to."""

    __slots__ = ()
    kind = "path"


class PascalName(_Pascal):
    """A class, record or routine name."""

    __slots__ = ()
    kind = "name"


class Parameters(_Pascal):
    """The parameter list, as the mangling recorded it."""

    __slots__ = ()
    kind = "parameters"


class Symbol(_Pascal):
    """A whole Free Pascal symbol.

    `pascal_kind` is what the mangling said this is -- `routine`, or one of the nine
    markers, or `section` or `label` -- which the spelling alone does not always say.
    """

    __slots__ = ("pascal_kind",)
    kind = "symbol"

    def __init__(self, parts, pascal_kind):
        super().__init__(parts)
        self.pascal_kind = pascal_kind


def build(symbol):
    """Assemble the tree for a parsed `PascalSymbol`."""
    from ._parser import spell_routine_name

    if symbol.kind in ("label", "section"):
        return Symbol([symbol.text], symbol.kind)

    if symbol.kind == "WRPR" and symbol.wrapped is not None:
        index, implementation, tail = symbol.wrapped
        parts = ["interface wrapper for ", Unit([symbol.unit])]
        for piece in symbol.scope:
            parts.extend((".", PascalName([piece])))
        parts.extend((f" #{index}: ", PascalName([implementation]), tail))
        if symbol.indirect:
            parts.append(" (indirect reference)")
        return Symbol(parts, symbol.kind)

    # The lead (`vmt for `, the kind word, `program `) is found by searching for the name
    # as the spelling writes it (without a program's `P$`) and with the scope attached,
    # so a unit name that is also a word in the lead cannot match the wrong place.
    spelled_unit = symbol.unit[2:] if symbol.unit.startswith("P$") else symbol.unit
    qualified = ".".join([spelled_unit, *symbol.scope])
    at = symbol.text.find(qualified) if qualified else -1
    lead = symbol.text[:at] if at > 0 else ""
    parts = [lead] if lead else []
    parts.append(Unit([spelled_unit]))
    for piece in symbol.scope:
        parts.extend((".", PascalName([piece])))
    if symbol.name:
        parts.extend((".", PascalName([spell_routine_name(symbol.name)])))
    if symbol.kind == "routine":
        if symbol.elided:
            parts.append("(<parameters elided by the compiler>)")
        elif symbol.parameters:
            inner = []
            for at, parameter in enumerate(symbol.parameters):
                if at:
                    inner.append(", ")
                inner.append(PascalName([parameter]))
            parts.extend(("(", Parameters(inner), ")"))
        if symbol.result is not None:
            parts.extend((": ", PascalName([symbol.result])))
    if symbol.indirect:
        parts.append(" (indirect reference)")
    return Symbol(parts, symbol.kind)
