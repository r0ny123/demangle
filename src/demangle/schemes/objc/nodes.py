"""The Objective-C symbol tree.

The same parts-in-order shape as the other schemes: a node is the fragments it was built
from, so rendering concatenates and the tree cannot spell a symbol differently from the
text path.

What a caller wants from one of these is the class, the category and the selector, which
is exactly what splitting `-[NSString(Extra) stringWithFormat:]` back out of a string
gets wrong -- a category name can hold a bracket in no runtime, but a selector holds
colons and spaces, and a block's parent is a whole method with brackets of its own.
"""

from ...core.ast import Node, rendered

__all__ = ["Category", "ClassName", "Selector", "Symbol", "build"]


class _Objc(Node):
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


class ClassName(_Objc):
    """The class, or the protocol, the symbol belongs to."""

    __slots__ = ()
    kind = "name"


class Category(_Objc):
    """The category, when the symbol names one."""

    __slots__ = ()
    kind = "path"


class Selector(_Objc):
    """The selector, colons and all."""

    __slots__ = ()
    kind = "name"


class Symbol(_Objc):
    """A whole Objective-C symbol.

    `objc_kind` is what the mangling said this is -- `instance method`, `class method`,
    `class`, `category`, `ivar`, `selector`, `block`, `reference`, `encoding` or
    `label` -- which the spelling alone does not always say.

    `runtime` is which family wrote it: `apple`, `gnu`, or empty where the form is
    shared. `ambiguous` is set when the mangling admits more than one reading and this
    is the preferred one rather than the only one.
    """

    __slots__ = ("ambiguous", "objc_kind", "runtime")
    kind = "symbol"

    def __init__(self, parts, objc_kind, runtime="", ambiguous=False):
        super().__init__(parts)
        self.objc_kind = objc_kind
        self.runtime = runtime
        self.ambiguous = ambiguous


def build(symbol):
    """Assemble the tree for a parsed `ObjcSymbol`.

    The pieces are placed by *searching the spelling for them* rather than by rebuilding
    it, so the tree renders to the same string the text path produces however the
    spelling is worded. A piece that is not in the spelling -- a block's class, say,
    which is inside the parent it names -- simply is not a node.
    """
    text = symbol.text
    pieces = []
    for value, factory in (
        (symbol.class_name, ClassName),
        (symbol.category, Category),
        (symbol.selector, Selector),
    ):
        if value:
            pieces.append((value, factory))

    # Searched for after the label, which can hold the name: `Objective-C selector b`.
    at = len(symbol.label)
    parts = [text[:at]] if at else []
    for value, factory in pieces:
        found = text.find(value, at)
        if found < 0:
            continue
        if found > at:
            parts.append(text[at:found])
        parts.append(factory([value]))
        at = found + len(value)
    if at < len(text):
        parts.append(text[at:])
    if not parts:
        parts = [text]
    return Symbol(parts, symbol.kind, runtime=symbol.runtime, ambiguous=symbol.ambiguous)
