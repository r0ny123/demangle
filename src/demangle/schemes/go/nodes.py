"""The Go symbol tree.

The same shape as the Rust tree, and for the same reason: a Go symbol spells strictly
left to right, so a node is its parts in order and rendering concatenates them. A tree
therefore cannot spell a symbol differently from the text path.

What the structure is for is the question a tool actually asks of a Go binary -- which
package is this in, is it a method and on what, is it an instantiation of a generic --
none of which can be answered by splitting the raw name on `.`, because a package path
may contain a dot of its own (written `%2e`) and a receiver may contain one too.
"""

from ...core.ast import Node, rendered

__all__ = ["Generic", "GoName", "Package", "Receiver", "Symbol", "build", "render"]


def render(part):
    return part if isinstance(part, str) else part.render()


class _Go(Node):
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
        # Go has no declarator position and styles only differ for C++; accepted to
        # match `Node.spell`.
        return rendered(self.render)

    def build(self, builder):
        return builder.raw(self.render())

    @property
    def text(self):
        return self.render()


class GoName(_Go):
    """An identifier: a function, a method, a type."""

    __slots__ = ()
    kind = "name"


class Package(_Go):
    """An import path, with its escapes already decoded."""

    __slots__ = ()
    kind = "path"


class Receiver(_Go):
    """The type a method is defined on.

    `pointer` says whether it was written `(*T).M` or `T.M`, which is a real distinction:
    they are different methods with different symbols.
    """

    __slots__ = ("pointer",)
    kind = "receiver"

    def __init__(self, parts, pointer):
        super().__init__(parts)
        self.pointer = pointer


class Generic(_Go):
    """A generic instantiation's arguments, `[go.shape.int]` or `[int]`.

    Kept as text rather than parsed further: the arguments are Go type syntax, which is a
    language of its own, and nothing in a symbol table needs it taken apart. `arguments`
    is what stood between the brackets.
    """

    __slots__ = ("arguments",)
    kind = "template"

    def __init__(self, parts, arguments):
        super().__init__(parts)
        self.arguments = arguments


class Symbol(_Go):
    """A whole Go symbol.

    `generated` is the linker's own prefix where there is one -- `go:` for an itab or a
    string, `type:` for a runtime type descriptor -- and empty for an ordinary symbol.
    """

    __slots__ = ("generated",)
    kind = "symbol"

    def __init__(self, parts, generated=""):
        super().__init__(parts)
        self.generated = generated


def build(symbol):
    """Assemble the tree for a parsed `GoSymbol`.

    Built from the decoded pieces in output order, so `Symbol.render()` reproduces
    `GoSymbol.text` exactly rather than approximately.
    """
    parts = []
    if symbol.generated:
        parts.append(symbol.generated)
    if symbol.package:
        parts.append(Package((symbol.package,)))
        parts.append(".")

    name = symbol.name
    if symbol.receiver is not None:
        close = name.index(").")
        parts.append(Receiver(("(*", GoName((symbol.receiver,)), ")"), pointer=True))
        parts.append(".")
        name = name[close + 2 :]

    if symbol.generic is not None:
        open_bracket = name.index("[")
        parts.append(GoName((name[:open_bracket],)))
        parts.append(Generic(("[", symbol.generic, "]"), arguments=symbol.generic))
    else:
        parts.append(GoName((name,)))

    return Symbol(parts, generated=symbol.generated)
