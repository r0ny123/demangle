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

__all__ = [
    "AST_BUILDER",
    "Array",
    "AstBuilder",
    "Builtin",
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

    __slots__ = ()
    kind = "node"

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

    def spell(self, declarator=""):
        """Render as C++ declaration text."""
        from .spelling import SPELLING_BUILDER

        return SPELLING_BUILDER.spell(self.build(SPELLING_BUILDER), declarator)

    def __str__(self):
        return self.spell()

    def __repr__(self):  # pragma: no cover - debugging aid
        fields = ", ".join(f"{slot}={getattr(self, slot)!r}" for slot in self.__slots__)
        return f"{type(self).__name__}({fields})"

    def __eq__(self, other):
        if type(self) is not type(other):
            return NotImplemented
        return all(getattr(self, s) == getattr(other, s) for s in self.__slots__)

    def __hash__(self):
        return hash((type(self).__name__, *(_hashable(getattr(self, s)) for s in self.__slots__)))


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


class AstBuilder(Builder):
    """Builds `Node` trees. The backend behind `parse()`.

    Stateless, like its sibling, so one instance serves every call.
    """

    __slots__ = ()

    def builtin(self, spelling):
        return Builtin(spelling)

    def name(self, text):
        return Name(text)

    def raw(self, text):
        return Raw(text)

    def literal(self, kind, value):
        return Literal(kind, value)

    def qualified(self, parts):
        return Qualified(parts)

    def template(self, base, arguments):
        return Template(base, arguments)

    def qualify(self, inner, qualifiers):
        return Qualify(inner, qualifiers) if qualifiers else inner

    def pointer(self, inner):
        return Pointer(inner)

    def reference(self, inner):
        return Reference(inner)

    def rvalue_reference(self, inner):
        return RValueReference(inner)

    def member_pointer(self, owner, inner):
        return MemberPointer(owner, inner)

    def array(self, inner, dimension):
        return Array(inner, dimension)

    def function(self, returns, parameters, suffix="", name=None):
        return Function(returns, parameters, suffix, name)

    def pack(self, inner):
        return Pack(inner)

    def parameter_pack(self, members):
        return ParameterPack(members)

    def vendor_qualify(self, inner, qualifier):
        return VendorQualify(inner, qualifier)

    def special(self, label, inner):
        return Special(label, inner)

    def decorated(self, inner, decoration):
        return Decorated(inner, decoration)

    def spell(self, handle, declarator=""):
        return handle.spell(declarator)


AST_BUILDER = AstBuilder()
