"""The pre-Itanium C++ tree.

The same parts-in-order shape as the Go, Nim, D and JNI trees: a node is the fragments
it was built from, so rendering concatenates and the tree cannot spell a symbol
differently from the text path.

The reference builds text by prepending and appending into one buffer, and there is no
point pretending otherwise -- so the structure here is sliced out of the finished
spelling at boundaries the parser recorded while it ran: where the function's name
ended, and exactly what the argument list contributed. Everything else stays as literal
text, in the position the demangler wrote it.

Where the slice does not add up -- the recorded name is not found where it should be --
the whole spelling is emitted as one `name` part rather than a structure that might not
be true. `tests/test_gnuv2.py` pins how many of libiberty's own vectors take each path.
"""

from ...core.ast import Node, rendered

__all__ = ["GnuV2Name", "Parameters", "Symbol", "Type", "build"]


class _GnuV2(Node):
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
        # Accepted to match `Node.spell` and ignored: the name is already a declaration.
        return rendered(self.render)

    def build(self, builder):
        return builder.raw(self.render())

    @property
    def text(self):
        return self.render()


class GnuV2Name(_GnuV2):
    """The entity's name with its scope: `ivTSolver::AddAlignment`.

    Template arguments belong to the component they qualify, so they are inside this and
    not split out: `foo::bar<int>` is one name.
    """

    __slots__ = ()
    kind = "name"


class Type(_GnuV2):
    """One parameter type, spelled."""

    __slots__ = ()
    kind = "type"


class Parameters(_GnuV2):
    """The argument list, parentheses included, with each type a `Type` inside it."""

    __slots__ = ()
    kind = "parameters"


class Symbol(_GnuV2):
    """A whole pre-Itanium C++ symbol."""

    __slots__ = ()
    kind = "symbol"


def build(symbol):
    """Assemble the tree for a parsed `GnuV2Symbol`.

    Falls back to one flat `name` part whenever the recorded boundaries do not line up
    with the spelling, so `render()` is the spelling in every case.
    """
    text = symbol.text
    name = symbol.qualified_name
    arguments = symbol.arguments_text

    if name:
        anchor = name + arguments
        at = text.find(anchor)
        if at >= 0:
            middle = _parameters(arguments, symbol.parameters)
            if middle is not None:
                head, tail = text[:at], text[at + len(anchor) :]
                parts = []
                if head:
                    parts.append(head)
                parts.append(GnuV2Name([name]))
                parts.extend(middle)
                if tail:
                    parts.append(tail)
                return Symbol(parts)

    return Symbol([GnuV2Name([text])])


def _parameters(arguments, types):
    """The argument list as `["(", Parameters(...), ")"]`, `[]` for none, None if it broke.

    The brackets stay outside the node, which is the shape the scheme-independent reader
    in `demangle._signature` looks for: a literal fragment opening with `(` is what tells
    it the name has ended and the signature has begun.

    Each recorded type is located in the list's own text rather than joined back
    together, because the separators are the reference's -- `, ` between arguments but a
    bare `,` before `...` -- and re-deriving them would be a second implementation of
    something already spelled once.
    """
    if not arguments:
        return []
    if not (arguments.startswith("(") and arguments.endswith(")")):
        return None
    inner = arguments[1:-1]
    if not types:
        # `(void)` for a function that takes none: nothing was captured to place.
        return ["(", Parameters([inner]), ")"]
    parts = []
    at = 0
    for spelled in types:
        found = inner.find(spelled, at)
        if found < 0:
            return None
        if found > at:
            parts.append(inner[at:found])
        parts.append(Type([spelled]))
        at = found + len(spelled)
    if at < len(inner):
        parts.append(inner[at:])
    return ["(", Parameters(parts), ")"]
