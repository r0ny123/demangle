"""The CodeWarrior tree.

The same parts-in-order shape as the Go, Nim, D, JNI and pre-Itanium C++ trees: a node is
the fragments it was built from, so rendering concatenates and the tree cannot spell a
symbol differently from the text path.

The reference builds one string out of a `pre`/`post` pair, so the structure here is
sliced out of the finished spelling at the boundaries the parser recorded -- where the
qualified name ended, and exactly what the argument list contributed. Where the slice
does not add up, the whole spelling becomes one `name` part rather than a structure that
might not be true.
"""

from ...core.ast import Node, rendered

__all__ = ["CodeWarriorName", "Parameters", "Symbol", "Type", "build"]


class _CodeWarrior(Node):
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


class CodeWarriorName(_CodeWarrior):
    """The entity's name with its scope: `rstl::basic_string<wchar_t>::mNull`."""

    __slots__ = ()
    kind = "name"


class Type(_CodeWarrior):
    """One parameter type, spelled."""

    __slots__ = ()
    kind = "type"


class Parameters(_CodeWarrior):
    """The argument list, with each type a `Type` inside it."""

    __slots__ = ()
    kind = "parameters"


class Symbol(_CodeWarrior):
    """A whole CodeWarrior symbol."""

    __slots__ = ()
    kind = "symbol"


def build(symbol):
    """Assemble the tree for a parsed `CodeWarriorSymbol`."""
    text = symbol.text
    name = symbol.qualified_name
    arguments = symbol.arguments_text

    if name:
        at = text.find(name)
        if at >= 0:
            head, tail = text[:at], text[at + len(name) :]
            middle = _parameters(name, arguments, symbol.parameters)
            if middle is not None:
                parts = []
                if head:
                    parts.append(head)
                parts.extend(middle)
                if tail:
                    parts.append(tail)
                return Symbol(parts)

    return Symbol([CodeWarriorName([text])])


def _parameters(name, arguments, types):
    """`name` split around its argument list, or None where the split does not add up.

    The brackets stay outside the `parameters` node, which is the shape the
    scheme-independent reader in `demangle._signature` looks for: a literal fragment
    opening with `(` is what tells it the name has ended and the signature has begun.
    """
    if not arguments:
        return [CodeWarriorName([name])]
    at = name.rfind(arguments)
    if at < 0 or not (arguments.startswith("(") and arguments.endswith(")")):
        return None
    lead = name[:at]
    trail = name[at + len(arguments) :]
    inner = arguments[1:-1]

    if not types:
        body = [CodeWarriorName([lead]), "(", Parameters([inner]), ")"]
        return body + ([trail] if trail else [])

    parts = []
    cursor = 0
    for spelled in types:
        found = inner.find(spelled, cursor)
        if found < 0:
            return None
        if found > cursor:
            parts.append(inner[cursor:found])
        parts.append(Type([spelled]))
        cursor = found + len(spelled)
    if cursor < len(inner):
        parts.append(inner[cursor:])
    body = [CodeWarriorName([lead]), "(", Parameters(parts), ")"]
    return body + ([trail] if trail else [])
