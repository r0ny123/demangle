"""The JNI symbol tree.

The same parts-in-order shape as the Go, Nim and D trees: a node is the fragments it was
built from, so rendering concatenates and the tree cannot spell a symbol differently
from the text path.

What the structure is for is the one question splitting the spelling cannot answer: a
parameter type may itself contain a `.` -- `java.lang.String` is three of them -- so
`text.split(", ")` on the argument list is right and `split(".")` on the path is not.
The `parameters` node hands the types over already separated.
"""

from ...core.ast import Node, rendered

__all__ = ["JniName", "Parameters", "Path", "Symbol", "build"]


class _Jni(Node):
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
        # Java has no declarator position; accepted to match `Node.spell`.
        return rendered(self.render)

    def build(self, builder):
        return builder.raw(self.render())

    @property
    def text(self):
        return self.render()


class Path(_Jni):
    """The package and class the method was declared in, as far as the name says.

    Both are joined with the same character the mangling uses for a package separator,
    so the two cannot be told apart; this is the whole of what precedes the method.
    """

    __slots__ = ()
    kind = "path"


class JniName(_Jni):
    """The method's own name, with its escapes undone."""

    __slots__ = ()
    kind = "name"


class Parameters(_Jni):
    """The parameter types, present only where the method is overloaded."""

    __slots__ = ()
    kind = "parameters"


class Symbol(_Jni):
    """A whole JNI native method symbol."""

    __slots__ = ()
    kind = "symbol"


def build(symbol):
    """Assemble the tree for a parsed `JniSymbol`."""
    parts = []
    if symbol.declaring:
        parts.extend((Path([symbol.declaring]), "."))
    parts.append(JniName([symbol.method]))
    if symbol.parameters is not None:
        inner = []
        for at, parameter in enumerate(symbol.parameters):
            if at:
                inner.append(", ")
            inner.append(JniName([parameter]))
        parts.extend(("(", Parameters(inner), ")"))
    return Symbol(parts)
