"""The MSVC type tree, and the declarator spelling that belongs to it.

`core/spelling.py` places a declarator the way a C declaration wants it, and for the
Itanium schemes that is the whole story. It is not the whole story here. MSVC writes a
calling convention *inside* the parentheses of a function pointer -- `int (__cdecl
*)(void)` -- doubles the space after a convention spelled as an attribute, carries
`__unaligned` along with const and volatile through every indirection, and still leaves
room for a convention that spells as nothing at all: `int ( *)()`. None of that can be
said through the shared builder without teaching `core` about one scheme, so the
placement rules live here, next to the nodes they walk.

The nodes are otherwise ordinary `core.ast.Node`s, so `.walk()`, `.find(kind)` and
`.spell()` work as they do for any other scheme. Where a core node already means exactly
what this scheme means -- a fixed piece of text, an array, an identifier -- it is used
rather than copied.

What the tree does *not* hold is a name. Namespaces, template arguments and parameter
types reach this module already spelled, because the name grammar above resolves
back-references against rendered text and cannot defer it. The structure recovered is
therefore the declaration's shape -- what it declares, its indirections, its parameter
list, its return type -- and the leaves of that shape are text.
"""

import re

from ...core.ast import Array as _CoreArray
from ...core.ast import Name, Node, Raw

__all__ = [
    "Array",
    "Declaration",
    "FunctionType",
    "Indirection",
    "Name",
    "Raw",
    "apply_qualifiers",
    "is_member_function_pointer",
    "merge_qualifiers",
    "prefixed",
    "qualify_declared",
    "render",
    "spelled_after",
]

#: A member-pointer declarator is "Owner::*", possibly qualified. The pattern is anchored
#: and refuses parentheses so that a nested type's own "::*" -- which a rendered parameter
#: may well hold -- is not mistaken for one.
_MEMBER_POINTER_RE = re.compile(r"^[^()]*::\*")


class _Spelled(Node):
    """A node this scheme spells itself.

    `build()` hands a foreign builder finished text rather than a shape. The shared
    builder has nowhere to put a calling convention, so re-emitting the shape into it
    would produce a spelling MSVC does not use, and a demangler that answers two
    different things for one symbol is worse than one that answers text. Inspecting the
    tree is unaffected: that is what the structure is for.
    """

    __slots__ = ()

    def spell(self, declarator=""):
        return render(self, declarator)

    def build(self, builder):
        return builder.raw(render(self))


class Indirection(_Spelled):
    """A pointer, a reference, or a pointer into a class.

    `sigil` is `*`, `&`, `&&`, or `Owner::*` for a pointer to member -- the owner is part
    of the declarator in MSVC's spelling, not a type of its own. `qualifiers` are the
    indirection's own, the ones written to its right in `char *const`; what it points at
    carries its own.
    """

    __slots__ = ("inner", "qualifiers", "sigil")
    kind = "indirection"

    def __init__(self, sigil, qualifiers, inner):
        self.sigil = sigil
        self.qualifiers = tuple(qualifiers)
        self.inner = inner

    def children(self):
        return (self.inner,)

    @property
    def points_into_class(self):
        """Whether this is a pointer to member rather than an ordinary pointer."""
        return self.sigil.endswith("::*")


class Array(_Spelled, _CoreArray):
    """An array, spelled MSVC's way.

    Extents nest outermost first, so `int[2][3]` is an array of two arrays of three and
    each node's `dimension` means what it says on its own. An empty one is unbounded.
    """

    __slots__ = ()


class FunctionType(_Spelled):
    """A function type: its calling convention, parameters, return type and member cv.

    The declarator is deliberately absent. MSVC interleaves it with the convention --
    `int (__cdecl *p)(void)` puts the pointer between them -- so the name a function
    declares belongs to the `Declaration` around this node, and `render` threads it
    through rather than storing it here.

    `returns` is None for the forms the scheme writes no return type for: a constructor,
    a destructor, an operator that leaves the slot empty. `member_cv` is what trails the
    parameter list of a member function, already spelled with its leading space.
    """

    __slots__ = ("convention", "member_cv", "parameters", "returns")
    kind = "function"

    def __init__(self, convention, parameters, returns=None, member_cv=""):
        self.convention = convention
        self.parameters = tuple(parameters)
        self.returns = returns
        self.member_cv = member_cv

    def children(self):
        head = (self.returns,) if self.returns is not None else ()
        return (*head, *self.parameters)


class Declaration(_Spelled):
    """A whole symbol: what it declares, and what MSVC writes around it.

    `prefix` is the access and storage the spelling opens with -- `"public: virtual "` --
    and `suffix` the qualifiers that trail a member function's parameter list. Both stay
    text because neither is part of the type: they say how the symbol is reached, not
    what it is. `declarator` is the name, which lands in the hole its type leaves.
    """

    __slots__ = ("declarator", "prefix", "suffix", "type")
    kind = "declaration"

    def __init__(self, prefix, declarator, type_, suffix=""):
        self.prefix = prefix
        self.declarator = declarator
        self.type = type_
        self.suffix = suffix

    def children(self):
        return (self.declarator, self.type)


def render(node, declarator="", declarator_is_function=False):
    """Spell a type around a declarator, the way C nests one inside the other.

    A pointer or array binds to the declarator built so far, and a name therefore ends up
    *inside* its type: `int (*j)[2]`, not `int (*)[2] j`.
    """
    kind = node.kind
    if kind == "raw":
        if not declarator:
            return node.text
        if declarator.startswith("["):
            return node.text + declarator
        # the reference spaces a declarator off a type ending in an alphanumeric character
        # or a template's ">", and abuts it to anything else: "struct S *" but "struct S_*"
        # and "enum <unnamed-type-*"
        tail = node.text[-1:]
        sigil = declarator.startswith(("*", "&"))
        abuts = sigil and not (tail == ">" or (tail.isascii() and tail.isalnum()))
        return node.text + ("" if abuts else " ") + declarator
    if kind == "indirection":
        token = node.sigil + " ".join(node.qualifiers)
        # a nested *function* declarator is separated from the sigil - "int * (__cdecl *)()"
        # - while a parenthesised pointer declarator abuts it: "int (*(*a)[20])()"
        nested_function = declarator_is_function and not declarator.startswith(("*", "&", "(*", "(&"))
        separator = (
            " " if declarator and not declarator.startswith("[") and (node.qualifiers or nested_function) else ""
        )
        return render(node.inner, token + separator + declarator)
    if kind == "array":
        if declarator.startswith(("*", "&")):
            declarator = f"({declarator})"
        return render(node.inner, f"{declarator}[{node.dimension}]")
    if kind == "declaration":
        # a declaration is never nested inside a type, so nothing may be threaded into it
        return node.prefix + render(node.type, node.declarator.text) + node.suffix
    convention, returns, member_cv = node.convention, node.returns, node.member_cv
    params = ", ".join([render(parameter) for parameter in node.parameters])
    if returns is None:
        # the forms that write no return type write nothing around the parameter list
        # either, so the convention simply precedes the name: "public: __thiscall foo::foo(void)"
        return spelled_after(convention, f"{declarator}({params}){member_cv}")
    if not convention and not declarator.startswith(("*", "&")) and not _MEMBER_POINTER_RE.match(declarator):
        # a convention spelled with nothing leaves a named declarator alone, while a pointer
        # keeps its parentheses and the space the convention would have filled: "int ( *)()"
        return render(returns, f"{declarator}({params}){member_cv}", True)
    # a member-pointer declarator is "Owner::*", possibly qualified; the test is anchored so
    # that a nested type's own "::*" - which a rendered parameter may hold - does not count
    if declarator.startswith(("*", "&")) or _MEMBER_POINTER_RE.match(declarator):
        # an attribute-spelled convention carries a space of its own here, so a pointer to a
        # __swiftcall function reads "int (__attribute__((__swiftcall__))  *j)(int)"
        gap = "  " if convention.startswith("__attribute__") else " "
        declarator = f"({convention}{gap}{declarator})"
    else:
        declarator = f"{convention} {declarator}" if declarator else convention
    return render(returns, f"{declarator}({params}){member_cv}", True)


def spelled_after(convention, text):
    """Join a calling convention to what follows it, skipping the ones spelled with nothing."""
    return f"{convention} {text}" if convention else text


def prefixed(text, node):
    """Put `text` in front of a whole symbol, without disturbing the shape underneath it."""
    if not text:
        return node
    if node.kind == "declaration":
        return Declaration(text + node.prefix, node.declarator, node.type, node.suffix)
    return Raw(text + render(node))


def merge_qualifiers(left, right):
    # "__unaligned" travels with const and volatile: a pointer that points at an unaligned
    # pointer keeps it - "int __unaligned *__unaligned *"
    merged = [qual for qual in ("const", "volatile", "__unaligned") if qual in left or qual in right]
    return tuple(merged)


def apply_qualifiers(node, quals):
    """Qualify a named type, as a pointee qualifier or a $$C wrapper does.

    Only ever reached with a named type: an indirection merges its qualifiers as it is
    built, and a back-reference declines rather than accept one.
    """
    return Raw(f"{node.text} {' '.join(quals)}") if quals else node


def is_member_function_pointer(node):
    return node.kind == "indirection" and node.points_into_class and node.inner.kind == "function"


def qualify_declared(node, quals):
    """Place a data symbol's trailing qualifier on what the symbol declares.

    It belongs one level inside an outermost pointer rather than on the pointer -
    "?s@@3PADB" is "char const *s" and "?s@@3PAPADB" is "char *const *s" - and an array
    passes it on to its element, the way C spells one: "?arr@@3QAY01HB" is
    "int const (*const arr)[2]".
    """
    if not quals:
        return node
    if node.kind == "indirection":
        return Indirection(node.sigil, node.qualifiers, _qualify_element(node.inner, quals))
    return _qualify_element(node, quals)


def _qualify_element(node, quals):
    if node.kind == "array":
        return Array(_qualify_element(node.inner, quals), node.dimension)
    return _qualify(node, quals)


def _qualify(node, quals):
    """Add qualifiers to a parsed type, wherever that type keeps them.

    A named type spells them in its text; a pointer or reference carries its own, so they
    join those instead of being appended to a rendering that already placed the sigil.
    """
    if node.kind == "indirection":
        return Indirection(node.sigil, merge_qualifiers(node.qualifiers, quals), node.inner)
    # a named type spells its qualifiers in its own text, so one it already carries must not
    # be spelled twice: "?s@@3QBDD" is "char const volatile *const", not "char const const .."
    spelled = node.text.split()
    return apply_qualifiers(node, tuple(qual for qual in quals if qual not in spelled))
