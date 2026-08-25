"""The structured builder: a tree a caller can walk.

`demangle()` answers "what does this name say". `parse()` answers "what are its parts" --
which namespace, which template arguments, how many parameters and of what types. Tools
that rename functions, group symbols by library, or match call signatures need the
second, and recovering it by pattern-matching the string is not workable: C++
declaration syntax nests, and nesting is what regular expressions cannot follow.

Every node re-emits itself into any builder through `build()`. That is what keeps this
module free of rendering logic: turning a tree back into text drives `SpellingBuilder`
over it, so there is exactly one implementation of declarator placement in the package
and a tree can never disagree with the fast path. It also means a new output format --
JSON, HTML, a token stream -- is a builder, not a second traversal to keep in sync.
"""

from .builder import Builder
from .style import get_style

__all__ = [
    "AST_BUILDER",
    "Array",
    "AstBuilder",
    "Builtin",
    "Expression",
    "Function",
    "Literal",
    "MemberPointer",
    "Name",
    "Node",
    "Pack",
    "Pointer",
    "Qualified",
    "Qualify",
    "RValueReference",
    "Raw",
    "Reference",
    "Special",
    "Template",
    "VendorQualify",
]


class Node:
    """Base class for every parsed component.

    `__slots__` throughout: a single C++ symbol can be a few hundred nodes and a binary
    a few hundred thousand symbols, so per-instance dictionaries are not affordable.
    """

    __slots__ = ("size",)
    kind = "node"

    #: Upper bound on the rendered length of this subtree, filled in by `AstBuilder` as
    #: it constructs. An over-estimate is fine and deliberate -- it is used to enforce a
    #: resource bound, where erring high is the safe direction.
    #: Carried rather than computed so the output bound can be checked in constant time:
    #: the tree is a DAG with shared subtrees, and walking it to measure would be
    #: exponential in exactly the cases the bound exists to stop.

    def children(self):
        """Direct children, in source order. Leaves return an empty tuple."""
        return ()

    def build(self, builder):
        """Re-emit this subtree into `builder`, returning its handle."""
        raise NotImplementedError

    def walk(self):
        """Yield this node and every descendant, depth first."""
        yield self
        for child in self.children():
            yield from child.walk()

    def find(self, kind):
        """Yield every descendant of the given `kind`, including this node."""
        return (node for node in self.walk() if node.kind == kind)

    def spell(self, declarator="", style=None):
        """Render as declaration text.

        `style` selects the spelling policy, and should be the style the tree was parsed
        under: some choices -- whether a `std::` abbreviation is expanded, for instance --
        are made during parsing and are already baked into the tree, so spelling a
        gnu-parsed tree with LLVM's builder gives a mixture of the two.
        """
        builder = get_style(style).spelling_builder
        return builder.spell(self.build(builder), declarator)

    def __str__(self):
        return self.spell()

    def _fields(self):
        """Every declared slot in the class hierarchy, outermost base first.

        `self.__slots__` alone gives only the most-derived class's, which is empty for
        `Pointer`, `Reference` and every other node whose state lives on a shared base --
        so comparing on it made all of them equal to each other regardless of content.
        """
        names = []
        for klass in reversed(type(self).__mro__):
            for slot in getattr(klass, "__slots__", ()):
                if slot != "size" and slot not in names:
                    names.append(slot)
        return names

    def __repr__(self):  # pragma: no cover - debugging aid
        fields = ", ".join(f"{slot}={getattr(self, slot, None)!r}" for slot in self._fields())
        return f"{type(self).__name__}({fields})"

    def __eq__(self, other):
        if type(self) is not type(other):
            return NotImplemented
        return all(getattr(self, s, None) == getattr(other, s, None) for s in self._fields())

    def __hash__(self):
        return hash((type(self).__name__, *(_hashable(getattr(self, s, None)) for s in self._fields())))


def _hashable(value):
    return tuple(value) if isinstance(value, list) else value


# -- leaves -------------------------------------------------------------------


class Builtin(Node):
    """A primitive type."""

    __slots__ = ("spelling",)
    kind = "builtin"

    def __init__(self, spelling):
        self.spelling = spelling

    def build(self, builder):
        return builder.builtin(self.spelling)


class Name(Node):
    """A single identifier."""

    __slots__ = ("text",)
    kind = "name"

    def __init__(self, text):
        self.text = text

    def build(self, builder):
        return builder.name(self.text)


class Raw(Node):
    """Text whose spelling the scheme fixes outright, with no further structure."""

    __slots__ = ("text",)
    kind = "raw"

    def __init__(self, text):
        self.text = text

    def build(self, builder):
        return builder.raw(self.text)


class Literal(Node):
    """A constant value, such as a non-type template argument."""

    __slots__ = ("type", "value")
    kind = "literal"

    def __init__(self, type_, value):
        self.type = type_
        self.value = value

    def children(self):
        return (self.type,) if self.type is not None else ()

    def build(self, builder):
        return builder.literal(self.type.build(builder) if self.type else None, self.value)


# -- composition --------------------------------------------------------------


class Expression(Node):
    """A constant expression appearing in a type or a template argument.

    `parts` interleaves fixed text with operand subtrees, in output order, exactly as
    the parser reported them -- so rendering concatenates and cannot spell the
    expression differently from the text path. `form` says which shape it is: `binary`,
    `unary`, `call`, `conditional`, `sizeof`, and so on.

    Brackets, where the spelling needs them, are plain parts. Precedence is settled by
    the parser before it reports the production, because only the parser knows what the
    operand was; a consumer reading the tree sees the operands, not the punctuation.
    """

    __slots__ = ("form", "parts")
    kind = "expression"

    def __init__(self, form, parts):
        self.form = form
        self.parts = tuple(parts)

    def children(self):
        return tuple(p for p in self.parts if isinstance(p, Node))

    @property
    def operands(self):
        """Just the operands, without the punctuation between them."""
        return self.children()

    def build(self, builder):
        return builder.expression(self.form, [p if isinstance(p, str) else p.build(builder) for p in self.parts])


class Qualified(Node):
    """A scoped name, `a::b::c`, outermost part first."""

    __slots__ = ("parts",)
    kind = "qualified"

    def __init__(self, parts):
        self.parts = tuple(parts)

    def children(self):
        return self.parts

    def build(self, builder):
        return builder.qualified([part.build(builder) for part in self.parts])


class Template(Node):
    """A template specialisation, `base<arguments...>`."""

    __slots__ = ("arguments", "base")
    kind = "template"

    def __init__(self, base, arguments):
        self.base = base
        self.arguments = tuple(arguments)

    def children(self):
        return (self.base, *self.arguments)

    def build(self, builder):
        return builder.template(self.base.build(builder), [a.build(builder) for a in self.arguments])


class Qualify(Node):
    """cv-qualifiers applied to a type."""

    __slots__ = ("inner", "qualifiers")
    kind = "qualify"

    def __init__(self, inner, qualifiers):
        self.inner = inner
        self.qualifiers = tuple(qualifiers)

    def children(self):
        return (self.inner,)

    def build(self, builder):
        return builder.qualify(self.inner.build(builder), self.qualifiers)


# -- declarators --------------------------------------------------------------


class _Unary(Node):
    __slots__ = ("inner",)

    def __init__(self, inner):
        self.inner = inner

    def children(self):
        return (self.inner,)


class Pointer(_Unary):
    __slots__ = ()
    kind = "pointer"

    def build(self, builder):
        return builder.pointer(self.inner.build(builder))


class Reference(_Unary):
    __slots__ = ()
    kind = "reference"

    def build(self, builder):
        return builder.reference(self.inner.build(builder))


class RValueReference(_Unary):
    __slots__ = ()
    kind = "rvalue_reference"

    def build(self, builder):
        return builder.rvalue_reference(self.inner.build(builder))


class Pack(_Unary):
    """A parameter pack expansion."""

    __slots__ = ()
    kind = "pack"

    def build(self, builder):
        return builder.pack(self.inner.build(builder))


class ParameterPack(Node):
    """A pack of concrete template arguments."""

    __slots__ = ("members",)
    kind = "parameter_pack"

    def __init__(self, members):
        self.members = tuple(members)

    def children(self):
        return self.members

    def build(self, builder):
        return builder.parameter_pack([member.build(builder) for member in self.members])


class MemberPointer(Node):
    """A pointer to member, `Type Owner::*`."""

    __slots__ = ("inner", "owner")
    kind = "member_pointer"

    def __init__(self, owner, inner):
        self.owner = owner
        self.inner = inner

    def children(self):
        return (self.owner, self.inner)

    def build(self, builder):
        return builder.member_pointer(self.owner.build(builder), self.inner.build(builder))


class Array(Node):
    """An array type. An empty `dimension` means an unbounded array."""

    __slots__ = ("dimension", "inner")
    kind = "array"

    def __init__(self, inner, dimension):
        self.inner = inner
        self.dimension = dimension

    def children(self):
        return (self.inner,)

    def build(self, builder):
        return builder.array(self.inner.build(builder), self.dimension)


class VendorQualify(Node):
    """A vendor extended qualifier, spelled after the type it applies to."""

    __slots__ = ("inner", "qualifier")
    kind = "vendor_qualify"

    def __init__(self, inner, qualifier):
        self.inner = inner
        self.qualifier = qualifier

    def children(self):
        return (self.inner,)

    def build(self, builder):
        return builder.vendor_qualify(self.inner.build(builder), self.qualifier)


class Function(Node):
    """A function type, or a whole function declaration when `name` is present."""

    __slots__ = ("name", "parameters", "returns", "suffix")
    kind = "function"

    def __init__(self, returns, parameters, suffix="", name=None):
        self.returns = returns
        self.parameters = tuple(parameters)
        self.suffix = suffix
        self.name = name

    def children(self):
        head = (self.returns,) if self.returns is not None else ()
        tail = (self.name,) if self.name is not None else ()
        return (*head, *self.parameters, *tail)

    def build(self, builder):
        return builder.function(
            self.returns.build(builder) if self.returns is not None else None,
            [parameter.build(builder) for parameter in self.parameters],
            self.suffix,
            self.name.build(builder) if self.name is not None else None,
        )


class Decorated(Node):
    """A symbol plus what the symbol table appended to it.

    `decoration` keeps its separator: `"@@GLIBCXX_3.4"`, `".cold"`. A tool that wants
    the undecorated entity walks to `inner`; one that wants to know which clone or which
    library version this is reads `decoration`.
    """

    __slots__ = ("decoration", "inner")
    kind = "decorated"

    def __init__(self, inner, decoration):
        self.inner = inner
        self.decoration = decoration

    def children(self):
        return (self.inner,)

    def build(self, builder):
        return builder.decorated(self.inner.build(builder), self.decoration)


class Special(Node):
    """A symbol *about* an entity: `vtable for Foo`, `typeinfo for Bar`."""

    __slots__ = ("inner", "label")
    kind = "special"

    def __init__(self, label, inner):
        self.label = label
        self.inner = inner

    def children(self):
        return (self.inner,)

    def build(self, builder):
        return builder.special(self.label, self.inner.build(builder))


# -- the builder ---------------------------------------------------------------


def _sized(node, size):
    node.size = size
    return node


def _sizes(nodes):
    return sum(node.size for node in nodes)


class AstBuilder(Builder):
    """Builds `Node` trees. The backend behind `parse()`.

    One instance serves every call. The only state is the leaf table below, which is a
    cache and does not make an answer depend on what was parsed before it.

    Each method records the rendered size of what it built, which is what makes `size()`
    constant time -- see the note on `Node.size`.

    Leaves are interned; nothing else is. Measured over the 5,913 trees of the shipped
    libstdc++: leaves are 55% of all nodes and repeat 49 times over on average -- 15,654
    `builtin` instances hold 32 distinct spellings, 8,807 `raw` instances hold 16.
    Interning them costs nothing because a leaf is keyed by its *text*, so a lookup is
    one string hash and no comparison of subtrees; it is 40% less memory over that corpus
    and, because allocating fewer objects is less work than allocating more, about 6%
    faster as well.

    Interning composite nodes too was measured and rejected. It collapses the tree much
    further -- to 2.4MB where leaf interning gives 4.2MB -- but a composite hashes by
    walking its children, so building that table costs seven times the whole parse. Seven
    times slower to save memory a caller may not be short of is the wrong trade.
    `Node.__eq__` still compares composites by value, so a caller wanting to deduplicate
    a particular result can.
    """

    __slots__ = ("_leaves",)

    #: Bound on distinct leaves held. A binary's symbol table draws from a small pool of
    #: identifiers, so this is not reached in ordinary use; it exists so a tool walking a
    #: corpus of unrelated binaries cannot accumulate without end. Cleared wholesale for
    #: the reason `BoundedCache` gives: tracking recency costs work on every hit, which is
    #: the operation being optimised.
    MAX_LEAVES = 4096

    def __init__(self):
        self._leaves = {}

    def _leaf(self, cls, text):
        key = (cls, text)
        found = self._leaves.get(key)
        if found is not None:
            return found
        if len(self._leaves) >= self.MAX_LEAVES:
            self._leaves.clear()
        node = self._leaves[key] = _sized(cls(text), len(text))
        return node

    def builtin(self, spelling):
        return self._leaf(Builtin, spelling)

    def name(self, text):
        return self._leaf(Name, text)

    def raw(self, text):
        return self._leaf(Raw, text)

    def expression(self, form, parts):
        size = sum(len(p) if isinstance(p, str) else p.size for p in parts)
        return _sized(Expression(form, parts), size)

    def literal(self, kind, value):
        return _sized(Literal(kind, value), len(value) + (kind.size if kind else 0))

    def qualified(self, parts):
        return _sized(Qualified(parts), _sizes(parts) + 2 * max(len(parts) - 1, 0))

    def template(self, base, arguments):
        return _sized(Template(base, arguments), base.size + _sizes(arguments) + 2 * len(arguments) + 2)

    def qualify(self, inner, qualifiers):
        if not qualifiers:
            return inner
        width = sum(len(q) + 1 for q in qualifiers)
        return _sized(Qualify(inner, qualifiers), inner.size + width)

    def pointer(self, inner):
        return _sized(Pointer(inner), inner.size + 3)

    def reference(self, inner):
        return _sized(Reference(inner), inner.size + 3)

    def rvalue_reference(self, inner):
        return _sized(RValueReference(inner), inner.size + 4)

    def member_pointer(self, owner, inner):
        return _sized(MemberPointer(owner, inner), owner.size + inner.size + 5)

    def array(self, inner, dimension):
        return _sized(Array(inner, dimension), inner.size + len(dimension) + 4)

    def function(self, returns, parameters, suffix="", name=None):
        width = (returns.size + 1 if returns is not None else 0) + (name.size if name is not None else 0)
        width += _sizes(parameters) + 2 * len(parameters) + len(suffix) + 2
        return _sized(Function(returns, parameters, suffix, name), width)

    def pack(self, inner):
        return _sized(Pack(inner), inner.size + 3)

    def parameter_pack(self, members):
        return _sized(ParameterPack(members), _sizes(members) + 2 * len(members))

    def vendor_qualify(self, inner, qualifier):
        return _sized(VendorQualify(inner, qualifier), inner.size + len(qualifier) + 1)

    def special(self, label, inner):
        return _sized(Special(label, inner), inner.size + len(label))

    def decorated(self, inner, decoration):
        return _sized(Decorated(inner, decoration), inner.size + len(decoration) + 10)

    def spell(self, handle, declarator=""):
        return handle.spell(declarator)

    def size(self, handle):
        return handle.size


AST_BUILDER = AstBuilder()
