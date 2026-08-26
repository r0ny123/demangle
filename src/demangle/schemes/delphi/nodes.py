"""The Delphi/C++Builder symbol tree.

Left-to-right like Go and Free Pascal: a node is its fragments in order, rendering
concatenates, and the tree cannot spell a symbol differently from the text path.

What a caller wants is the unit, the class, and the encoded parameters -- none of which
survives splitting the spelling, because a length-prefixed type can itself hold `@` and
`%` template markup.
"""

from ...core.ast import Node, rendered

__all__ = ["DelphiName", "Parameters", "Symbol", "build"]


class _Delphi(Node):
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
        return rendered(self.render)

    def build(self, builder):
        return builder.raw(self.render())

    @property
    def text(self):
        return self.render()


class DelphiName(_Delphi):
    """A unit, class, or routine name."""

    __slots__ = ()
    kind = "name"


class Parameters(_Delphi):
    """The parameter list, as the mangling recorded it."""

    __slots__ = ()
    kind = "parameters"


class Symbol(_Delphi):
    """A whole Delphi/C++Builder symbol.

    `delphi_kind` is what the mangling said this is -- `function`, `constructor`,
    `data`, and so on -- which the spelling alone does not always say.
    """

    __slots__ = ("delphi_kind",)
    kind = "symbol"

    def __init__(self, parts, delphi_kind):
        super().__init__(parts)
        self.delphi_kind = delphi_kind


def build(symbol):
    """Assemble the tree for a parsed `DelphiSymbol`."""
    text = symbol.text
    open_paren = text.find("(")
    if open_paren < 0:
        return Symbol([DelphiName([text])], symbol.kind)
    head, rest = text[:open_paren], text[open_paren:]
    close = rest.rfind(")")
    if close < 0:
        return Symbol([DelphiName([text])], symbol.kind)
    params = rest[1:close]
    tail = rest[close + 1 :]
    parts = [DelphiName([head]), "("]
    if params:
        inner = []
        for at, piece in enumerate(params.split(", ")):
            if at:
                inner.append(", ")
            inner.append(DelphiName([piece]))
        parts.append(Parameters(inner))
    parts.append(")")
    if tail:
        parts.append(tail)
    return Symbol(parts, symbol.kind)
