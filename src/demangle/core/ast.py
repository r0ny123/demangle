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

import inspect
import sys

from .builder import Builder
from .errors import LimitExceeded
from .style import Style, get_style

__all__ = [
    "AST_BUILDER",
    "Array",
    "AstBuilder",
    "Builtin",
    "Decorated",
    "Expression",
    "Function",
    "Literal",
    "MemberPointer",
    "Name",
    "Node",
    "Pack",
    "ParameterPack",
    "Pointer",
    "Qualified",
    "Qualify",
    "RValueReference",
    "Raw",
    "Reference",
    "Special",
    "Template",
    "VendorQualify",
    "builder_for",
]


def rendered(produce):
    """Run a rendering call, reporting a stack overflow as the bound it is.

    `max_depth` bounds the *parse*. Rendering the tree afterwards is a second walk with
    frames of its own -- `build` and every scheme's `render` are genuinely recursive over
    the node shapes -- so a tree that was well inside the limit can still be deeper than
    the interpreter's stack. That comes back as the bound it is rather than as
    `RecursionError`, which this package's contract says cannot escape. `demangle()` on
    the same name still answers, because it never builds the tree.

    The mangled name is not available here: a node knows its own shape and not the bytes
    it came from.
    """
    try:
        return produce()
    except RecursionError as error:
        raise LimitExceeded("", "recursion depth", sys.getrecursionlimit()) from error


class Node:
    """Base class for every parsed component.

    `__slots__` throughout: a single C++ symbol can be a few hundred nodes and a binary
    a few hundred thousand symbols, so per-instance dictionaries are not affordable.
    """

    __slots__ = ("size",)
    kind = "node"

    #: The fields a positional `match` sees, in constructor order. `match Pointer(inner)`
    #: is what "returns a walkable tree" means to a Python caller now.
    #:
    #: Written out on every class in this module rather than computed, so that a type
    #: checker reading a caller's `match` statement can see it -- one derived in
    #: `__init_subclass__` is invisible to every static tool. A scheme's own nodes get the
    #: derived one, which is why `__init_subclass__` is still here; and
    #: `tests/test_api_surface.py` checks each declaration against the constructor it has
    #: to agree with, so neither can drift from the other.
    __match_args__ = ()

    #: Upper bound on the rendered length of this subtree, filled in by `AstBuilder` as
    #: it constructs. An over-estimate is fine and deliberate -- it is used to enforce a
    #: resource bound, where erring high is the safe direction.
    #: Carried rather than computed so the output bound can be checked in constant time:
    #: the tree is a DAG with shared subtrees, and walking it to measure would be
    #: exponential in exactly the cases the bound exists to stop.

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "__match_args__" not in cls.__dict__:
            cls.__match_args__ = _match_args(cls)

    def children(self):
        """Direct children, in source order. Leaves return an empty tuple."""
        return ()

    def to_dict(self):
        """This subtree as plain data -- dicts, lists and strings -- for `json.dumps`.

        Every node becomes a dict with a `kind` and the fields that node kind has, so the
        *role* of each child survives: a `function` has `returns`, `parameters` and
        `name`, not three anonymous children. A field holding a node becomes a nested
        dict and one holding a sequence of them becomes a list; anything else -- a
        spelling, a qualifier list, a flag -- is carried as it is.

        `kind` is the vocabulary to switch on, and `demangle.node_kinds()` is all of it.

        A node reached more than once is written out once, with an `id`, and afterwards
        as `{"$ref": id}`. This is not a size optimisation, it is the difference between
        terminating and not: the structure is a *graph*, not a tree. Itanium's
        substitutions make a component reachable from several places, and a Rust node
        names its own children twice over -- `parts` orders them and `base` and
        `arguments` say what they are -- so writing each occurrence out in full doubles
        per level. One real symbol from the Rust toolchain took 4.7 seconds and 363MB
        that way, and there is no bound on how much worse it can get.
        """
        return rendered(lambda: _emit(self, _shared_nodes(self), {}))

    def build(self, builder):
        """Re-emit this subtree into `builder`, returning its handle."""
        raise NotImplementedError

    def walk(self):
        """Yield this node and every descendant, depth first.

        Iterative rather than recursive, and not for speed: a `yield from` per level
        means one interpreter frame per level for the whole traversal, so a tree well
        inside `max_depth` -- which bounds the *parse*, in frames of a different size --
        could still exhaust the stack here. An explicit stack cannot.
        """
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            children = node.children()
            if children:
                stack.extend(reversed(children))

    def find(self, kind):
        """Yield every descendant of the given `kind`, including this node."""
        return (node for node in self.walk() if node.kind == kind)

    def render(self):
        """The text of this subtree, under the default spelling policy.

        `spell()` is the method to reach for -- it takes the `style` the tree was parsed
        under, which this cannot. This exists because most of the node classes in the
        package already had it: a scheme whose spelling rules do not fit C++ declarator
        syntax carries its fragments as text and renders by concatenating them, so
        `render()` is what its own nodes and its own tests use, and `spell()` delegates
        to it. What they did *not* have was a shared definition, so a tree was a mixture:
        every Rust node answered `render()` and the `Decorated` wrapper a version suffix
        puts around one did not, and neither did any Itanium node. Walking a tree and
        asking each node for its text -- which is the obvious thing to do with `walk()`
        -- raised `AttributeError` partway through.

        Defined here so that it does not. For a node that carries pre-rendered text this
        is what it always was; for one spelled from structure it is `spell()` with no
        declarator, which is the same string.
        """
        return self.spell()

    def spell(self, declarator="", style=None):
        """Render as declaration text.

        `style` selects the spelling policy, and should be the style the tree was parsed
        under: some choices -- whether a `std::` abbreviation is expanded, for instance --
        are made during parsing and are already baked into the tree, so spelling a
        gnu-parsed tree with LLVM's builder gives a mixture of the two.
        """
        builder = get_style(style).spelling_builder
        return rendered(lambda: builder.spell(self.build(builder), declarator))

    def __str__(self):
        return self.spell()

    def _fields(self):
        """Every declared slot in the class hierarchy, outermost base first.

        Takes a class as readily as an instance -- `Node._fields(SomeClass)` -- because
        `__match_args__` is computed before any instance of that class exists.

        `self.__slots__` alone gives only the most-derived class's, which is empty for
        `Pointer`, `Reference` and every other node whose state lives on a shared base --
        so comparing on it made all of them equal to each other regardless of content.
        """
        names = []
        for klass in reversed((self if isinstance(self, type) else type(self)).__mro__):
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


def _nodes_in(value):
    """Every node a field's value holds, directly or inside a sequence."""
    if isinstance(value, Node):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(node for item in value for node in _nodes_in(item))
    return ()


def _shared_nodes(root):
    """The nodes `to_dict` will reach more than once, by identity.

    Visits each distinct node's fields once, so this is linear in the graph however
    badly the expansion of it would blow up.
    """
    counts = {}
    stack = [root]
    while stack:
        node = stack.pop()
        key = id(node)
        counts[key] = counts.get(key, 0) + 1
        if counts[key] > 1:
            continue
        for field in node._fields():
            stack.extend(_nodes_in(getattr(node, field, None)))
    return {key for key, count in counts.items() if count > 1}


def _emit(node, shared, ids):
    """One node as plain data, writing a repeat as a reference to its first appearance."""
    key = id(node)
    if key in ids:
        return {"$ref": ids[key]}
    out = {"kind": node.kind}
    if key in shared:
        # Registered before the fields are walked, so a node that somehow reaches itself
        # writes a reference rather than recurring for ever.
        ids[key] = len(ids)
        out["id"] = ids[key]
    for field in node._fields():
        out[field] = _plain(getattr(node, field, None), shared, ids)
    return out


def _plain(value, shared, ids):
    """A field value as plain data, converting nodes and sequences of them."""
    if isinstance(value, Node):
        return _emit(value, shared, ids)
    if isinstance(value, (list, tuple)):
        return [_plain(item, shared, ids) for item in value]
    return value


def _match_args(cls):
    """A subclass's constructor parameters, as attribute names, for `__match_args__`.

    Taken from the constructor rather than from `__slots__` because positional matching
    is positional *in the constructor* -- `Array(inner, dimension)` matches in that
    order, and the slots happen to be alphabetical. A parameter that does not become an
    attribute of the same name would make every position after it mean the wrong thing,
    so a class with one gets no positional matching at all rather than a misleading
    tuple; `Literal`'s `type_` is the one such parameter here, and loses its underscore.
    """
    try:
        parameters = list(inspect.signature(cls.__init__).parameters)[1:]
    except (TypeError, ValueError):  # pragma: no cover - a builtin or C-level __init__
        return ()
    fields = set(Node._fields(cls))
    names = []
    for parameter in parameters:
        name = parameter.rstrip("_")
        if name not in fields:
            return ()
        names.append(name)
    return tuple(names)


# -- leaves -------------------------------------------------------------------


class Builtin(Node):
    """A primitive type."""

    __slots__ = ("spelling",)
    kind = "builtin"
    __match_args__ = ("spelling",)

    def __init__(self, spelling):
        self.spelling = spelling

    def build(self, builder):
        return builder.builtin(self.spelling)


class Name(Node):
    """A single identifier."""

    __slots__ = ("text",)
    kind = "name"
    __match_args__ = ("text",)

    def __init__(self, text):
        self.text = text

    def build(self, builder):
        return builder.name(self.text)


class Raw(Node):
    """Text whose spelling the scheme fixes outright, with no further structure."""

    __slots__ = ("text",)
    kind = "raw"
    __match_args__ = ("text",)

    def __init__(self, text):
        self.text = text

    def build(self, builder):
        return builder.raw(self.text)


class Literal(Node):
    """A constant value, such as a non-type template argument."""

    __slots__ = ("type", "value")
    kind = "literal"
    __match_args__ = ("type", "value")

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
    __match_args__ = ("form", "parts")

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
    __match_args__ = ("parts",)

    def __init__(self, parts):
        self.parts = tuple(parts)

    def children(self):
        return self.parts

    def build(self, builder):
        return builder.qualified([part.build(builder) for part in self.parts])


class Template(Node):
    """A template specialisation, `base<arguments...>`."""

    __slots__ = ("angle_space", "arguments", "base")
    kind = "template"
    __match_args__ = ("base", "arguments", "angle_space")

    def __init__(self, base, arguments, angle_space=True):
        self.base = base
        self.arguments = tuple(arguments)
        #: Whether a style that separates consecutive closing angle brackets should do
        #: so here. False where the last argument was a pack with no members: GNU
        #: c++filt writes `A<B<int>>` for that and `A<B<int> >` for everything else, and
        #: the pack is not in the tree to be asked about later.
        self.angle_space = angle_space

    def children(self):
        return (self.base, *self.arguments)

    def build(self, builder):
        return builder.template(self.base.build(builder), [a.build(builder) for a in self.arguments], self.angle_space)


class Qualify(Node):
    """cv-qualifiers applied to a type."""

    __slots__ = ("inner", "qualifiers")
    kind = "qualify"
    __match_args__ = ("inner", "qualifiers")

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
    __match_args__ = ("inner",)

    def __init__(self, inner):
        self.inner = inner

    def children(self):
        return (self.inner,)


class Pointer(_Unary):
    __slots__ = ()
    kind = "pointer"
    __match_args__ = ("inner",)

    def build(self, builder):
        return builder.pointer(self.inner.build(builder))


class Reference(_Unary):
    __slots__ = ()
    kind = "reference"
    __match_args__ = ("inner",)

    def build(self, builder):
        return builder.reference(self.inner.build(builder))


class RValueReference(_Unary):
    __slots__ = ()
    kind = "rvalue_reference"
    __match_args__ = ("inner",)

    def build(self, builder):
        return builder.rvalue_reference(self.inner.build(builder))


class Pack(_Unary):
    """A parameter pack expansion."""

    __slots__ = ()
    kind = "pack"
    __match_args__ = ("inner",)

    def build(self, builder):
        return builder.pack(self.inner.build(builder))


class ParameterPack(Node):
    """A pack of concrete template arguments."""

    __slots__ = ("members",)
    kind = "parameter_pack"
    __match_args__ = ("members",)

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
    __match_args__ = ("owner", "inner")

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
    __match_args__ = ("inner", "dimension")

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
    __match_args__ = ("inner", "qualifier")

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
    __match_args__ = ("returns", "parameters", "suffix", "name")

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
    __match_args__ = ("inner", "decoration")

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
    __match_args__ = ("label", "inner")

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


#: An empty pack of concrete arguments, which is what a declarator applied to one
#: becomes. Built once: it carries no state and every occurrence means the same thing.
_EMPTY_PACK = _sized(ParameterPack(()), 0)


def _distributes_to_nothing(inner):
    """Whether applying a declarator to `inner` yields nothing at all.

    A declarator applied to a pack applies to every member -- `Dp O T_` over three
    arguments is three rvalue references -- so over *no* members it is no references,
    and the result renders to nothing. `SpellingBuilder` has always done this, because
    its `_wrap` distributes through `pack_of` and `pack_of(())` is empty.

    This builder did not, and the difference was visible. It reported a size for a
    parameter that rendered to nothing, and `size` is documented as an over-estimate --
    so the parser, asking "did this parameter drop out entirely" the cheap way, was told
    no and left a separator behind: `f(std::launch, std::function<void ()>&&, )`.
    Mirroring the distribution here makes `size == 0` an exact answer to that question
    for the one shape where it was not.
    """
    return type(inner) is ParameterPack and not inner.members


def _sizes(nodes):
    """The widths of `nodes`, added up.

    Written as a loop rather than `sum(node.size for node in nodes)`. A generator is a
    frame resumed once per element, and this is asked of every qualified name, every
    template argument list, every parameter list and every pack -- seven times per name
    over the Itanium corpus. Measured at 234ns for three nodes against 68ns for the
    loop. `spelling.py` says the same thing about its joins.
    """
    total = 0
    for node in nodes:
        total += node.size
    return total


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

    __slots__ = ("_leaves", "_style")

    #: Bound on distinct leaves held. A binary's symbol table draws from a small pool of
    #: identifiers, so this is not reached in ordinary use; it exists so a tool walking a
    #: corpus of unrelated binaries cannot accumulate without end. Cleared wholesale for
    #: the reason `BoundedCache` gives: tracking recency costs work on every hit, which is
    #: the operation being optimised.
    MAX_LEAVES = 4096

    def __init__(self, style=None):
        self._leaves = {}
        # The style's *name* where it has a registered one, so re-registering under that
        # name is picked up rather than remembered; the style object itself where it was
        # composed for one call and no name would find it. See `builder_for`.
        self._style = style

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
        size = 0
        for part in parts:
            size += len(part) if isinstance(part, str) else part.size
        return _sized(Expression(form, parts), size)

    def literal(self, kind, value):
        return _sized(Literal(kind, value), len(value) + (kind.size if kind else 0))

    def qualified(self, parts):
        return _sized(Qualified(parts), _sizes(parts) + 2 * max(len(parts) - 1, 0))

    def template(self, base, arguments, angle_space=True):
        return _sized(Template(base, arguments, angle_space), base.size + _sizes(arguments) + 2 * len(arguments) + 2)

    def qualify(self, inner, qualifiers):
        if not qualifiers:
            return inner
        if _distributes_to_nothing(inner):
            return _EMPTY_PACK
        width = 0
        for qualifier in qualifiers:
            width += len(qualifier) + 1
        return _sized(Qualify(inner, qualifiers), inner.size + width)

    def pointer(self, inner):
        if _distributes_to_nothing(inner):
            return _EMPTY_PACK
        return _sized(Pointer(inner), inner.size + 3)

    def reference(self, inner):
        if _distributes_to_nothing(inner):
            return _EMPTY_PACK
        return _sized(Reference(inner), inner.size + 3)

    def rvalue_reference(self, inner):
        if _distributes_to_nothing(inner):
            return _EMPTY_PACK
        return _sized(RValueReference(inner), inner.size + 4)

    def member_pointer(self, owner, inner):
        # The owner distributes too: a pointer to a member of no class at all is no
        # pointer, and reporting a size for one left a separator behind.
        if _distributes_to_nothing(owner) or _distributes_to_nothing(inner):
            return _EMPTY_PACK
        return _sized(MemberPointer(owner, inner), owner.size + inner.size + 5)

    def array(self, inner, dimension):
        if _distributes_to_nothing(inner):
            return _EMPTY_PACK
        return _sized(Array(inner, dimension), inner.size + len(dimension) + 4)

    def function(self, returns, parameters, suffix="", name=None):
        width = (returns.size + 1 if returns is not None else 0) + (name.size if name is not None else 0)
        width += _sizes(parameters) + 2 * len(parameters) + len(suffix) + 2
        return _sized(Function(returns, parameters, suffix, name), width)

    def pack(self, inner):
        return _sized(Pack(inner), inner.size + 3)

    def parameter_pack(self, members):
        # Nested packs are spliced, which `SpellingBuilder.pack_of` has always done and
        # this builder did not. Two things went wrong without it. A pack whose one
        # member is an empty pack has a non-zero `size` and renders to nothing, so
        # `_drops_out` kept it and the parameter list printed the separator for an
        # argument that is not there -- `f(int, , nn::Up)` where the reference and the
        # spelling path both print `f(int, nn::Up)`. And a pack's *arity* is what an
        # expansion over it ranges across, so an unspliced pack of one empty pack made
        # `Dp` produce one member here and none there: the two builders disagreeing
        # about how many parameters a signature has.
        flattened = []
        for member in members:
            if type(member) is ParameterPack:
                flattened.extend(member.members)
            else:
                flattened.append(member)
        return _sized(ParameterPack(flattened), _sizes(flattened) + 2 * len(flattened))

    def vendor_qualify(self, inner, qualifier):
        if _distributes_to_nothing(inner):
            return _EMPTY_PACK
        return _sized(VendorQualify(inner, qualifier), inner.size + len(qualifier) + 1)

    def special(self, label, inner):
        return _sized(Special(label, inner), inner.size + len(label))

    def decorated(self, inner, decoration):
        return _sized(Decorated(inner, decoration), inner.size + len(decoration) + 10)

    def spell(self, handle, declarator=""):
        return handle.spell(declarator, style=self._style)

    def size(self, handle):
        return handle.size

    def members(self, handle):
        """The members of a parameter pack, or None if this is not one."""
        return handle.members if type(handle) is ParameterPack else None


AST_BUILDER = AstBuilder()

#: One tree builder per style, held by style name. Not one for all of them: a parser
#: sometimes has to flatten a subtree to text while building the tree -- a conversion
#: operator names a type, and the name is text -- and flattening under the default style
#: while building under another gives a spelling that is neither. `A::operator
#: std::vector<int, std::allocator<int> >` came back with LLVM's `>>` nested inside GNU's
#: `> >` for exactly that reason.
_BUILDERS = {None: AST_BUILDER}


def builder_for(style):
    """The tree builder for `style`.

    A *registered* style is held by name, so one registered again under a name it already
    had is picked up rather than remembered, and the common path -- `parse(name)`, whose
    style is the string `"llvm"` -- is a dictionary lookup.

    A style *object* that is not the registered one under its name gets its own builder,
    holding the object. Keyed by name it would be wrong in both directions: a composed
    style like `demangle.style("llvm", msvc={...})` keeps the name `"llvm"` and would
    silently be served the registered style's builder, and a style whose name is not
    registered at all made `parse()` raise `unknown style` from inside the parser --
    while `demangle()` accepted the very same object. Building one per call matches the
    bargain `demangle()` already makes for a style object: it is not cached either.

    `setdefault` rather than a lock: two threads arriving together each build one and
    the loser's is discarded, which costs an allocation and cannot produce two builders
    in use for one style.
    """
    if isinstance(style, Style):
        try:
            registered = get_style(style.name)
        except ValueError:
            registered = None
        if registered is not style:
            return AstBuilder(style)
        style = style.name
    found = _BUILDERS.get(style)
    if found is None:
        found = _BUILDERS.setdefault(style, AstBuilder(style))
    return found
