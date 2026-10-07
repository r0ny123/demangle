"""The Swift symbol tree a caller walks.

Swift's own demangling tree has 385 node kinds, most of which say something about the
lowered representation rather than about the declaration --
`ImplParameterResultDifferentiability` is not what a tool wants to match on. So this
exposes a small tree in the vocabulary the other schemes here use: `name`, `module`,
`type`, `template`, `function`, `parameters`.

The tree is built **during printing**, not from the demangling tree. That is the point:
the printer's spelling of a node depends on where it sits -- a class is
`Foundation.NSData` as a type and `NSData` as a prefix -- so a subtree rendered on its own
would not say what it says in the whole name. Recording the boundaries of one traversal
means every node's text is exactly the run of characters it produced, and the tree cannot
spell the symbol differently from `demangle()`.
"""

from ...core.ast import Node, rendered
from ._printer import Printer, _Invalid
from .options import DEFAULT_OPTIONS

__all__ = ["SwiftName", "build"]

#: Swift kinds that get a node of their own; everything else is text in its parent.
INTERESTING = {
    "Global": "symbol",
    "Module": "module",
    "Identifier": "name",
    "Class": "type",
    "Structure": "type",
    "Enum": "type",
    "Protocol": "type",
    "TypeAlias": "type",
    "OtherNominalType": "type",
    "BoundGenericClass": "template",
    "BoundGenericStructure": "template",
    "BoundGenericEnum": "template",
    "BoundGenericProtocol": "template",
    "BoundGenericOtherNominalType": "template",
    "BoundGenericTypeAlias": "template",
    "BoundGenericFunction": "template",
    "Function": "function",
    "Allocator": "function",
    "Constructor": "function",
    "Destructor": "function",
    "Deallocator": "function",
    "Subscript": "function",
    "ExplicitClosure": "function",
    "ImplicitClosure": "function",
    "Variable": "variable",
    "ArgumentTuple": "parameters",
    "ReturnType": "result",
    "Extension": "extension",
    "DependentGenericSignature": "generics",
}


class SwiftName(Node):
    """One part of a Swift symbol: the text it produced, and the nodes inside it.

    `parts` interleaves strings and nodes in output order, so `render()` is a
    concatenation and cannot drift from what the printer wrote.
    """

    __slots__ = ("kind", "parts", "swift_kind")

    def __init__(self, kind, swift_kind, parts):
        self.kind = kind
        #: The compiler's own kind name, for the finer distinction.
        self.swift_kind = swift_kind
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
        # Swift has no declarator position; accepted to match `Node.spell`.
        return rendered(self.render)

    def build(self, builder):
        return builder.raw(self.render())

    @property
    def text(self):
        return self.render()


class _TreePrinter(Printer):
    """A `Printer` that also records where each interesting node's output began and ended.

    Overriding `print` rather than re-walking is what keeps the two in step: whatever the
    printer decides -- including the nodes it declines to print at all -- is what the tree
    records.
    """

    def __init__(self, options=DEFAULT_OPTIONS):
        super().__init__(options)
        self._open = [[]]

    def write(self, text):
        super().write(text)
        if text:
            self._open[-1].append(text)

    def print(self, node, depth, as_prefix_context=False):
        wanted = INTERESTING.get(node.kind) if node is not None else None
        if wanted is None:
            return super().print(node, depth, as_prefix_context)
        self._open.append([])
        try:
            result = super().print(node, depth, as_prefix_context)
        finally:
            parts = self._open.pop()
            if parts:
                self._open[-1].append(SwiftName(wanted, node.kind, parts))
        return result

    def tree(self):
        parts = self._open[0]
        if len(parts) == 1 and isinstance(parts[0], Node):
            return parts[0]
        return SwiftName("symbol", "Global", parts)


def build(root, options=DEFAULT_OPTIONS):
    """Spell `root` while recording its structure, returning the tree.

    Returns `None` for a tree the printer refuses, which is the same answer the text path
    gives -- neither invents a partial reading.
    """
    printer = _TreePrinter(options)
    try:
        printer.print(root, 0)
    except (_Invalid, IndexError, AttributeError, KeyError, RecursionError):
        return None
    return printer.tree()
