"""The D symbol tree.

The same parts-in-order shape as the Rust and Go trees: a node is the fragments it was
built from, so rendering concatenates and a tree cannot spell a symbol differently from
the text path.

What a caller wants from a D symbol is which module it belongs to, whether it is a
method, and what a template was instantiated with -- none of which survives splitting the
spelling on `.`, because a template argument list contains dots of its own.
"""

from ...core.ast import Node, rendered

__all__ = ["DName", "Path", "Symbol", "build", "render"]


def render(part):
    return part if isinstance(part, str) else part.render()


class _D(Node):
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
        return "".join(render(part) for part in self.parts)

    def spell(self, declarator="", style=None):
        # Accepted to match `Node.spell` and ignored: D has no declarator position.
        return rendered(self.render)

    def build(self, builder):
        return builder.raw(self.render())

    @property
    def text(self):
        return self.render()


class DName(_D):
    """One path component."""

    __slots__ = ()
    kind = "name"


class Path(_D):
    """The dotted path: module, then the entity within it."""

    __slots__ = ()
    kind = "path"


class Symbol(_D):
    """A whole D symbol.

    `generated` is the prefix the reference gives a compiler-generated symbol -- an
    initializer, a vtable, a ClassInfo -- and empty for an ordinary one.
    """

    __slots__ = ("generated",)
    kind = "symbol"

    def __init__(self, parts, generated=""):
        super().__init__(parts)
        self.generated = generated


def build(symbol):
    """Assemble the tree for a parsed `DSymbol`."""
    parts = []
    if symbol.generated:
        parts.append(symbol.generated)
    components = symbol.path.split(".") if symbol.path else []
    inner = []
    for index, component in enumerate(components):
        if index:
            inner.append(".")
        inner.append(DName((component,)))
    parts.append(Path(inner))
    if symbol.suffix:
        parts.append(symbol.suffix)
    return Symbol(parts, generated=symbol.generated)
