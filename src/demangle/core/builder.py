"""The contract between a parser and its output.

Every parser in this package is written against `Builder` and never constructs its own
strings or nodes. It recognises a grammar production and reports it -- `builder.pointer(
inner)`, `builder.template(base, args)` -- leaving what that *becomes* to the builder.

The reason is that the two things callers want from a demangler have opposite cost
profiles. Labelling every symbol in a binary wants a string and nothing else, several
hundred thousand times. Inspecting one symbol's template arguments wants a tree. Writing
the parser twice to serve both would guarantee the two copies disagree, and the ways
they would disagree are exactly the subtle ones -- declarator placement, substitution
numbering -- that are hardest to notice.

So: one parser, two builders (`SpellingBuilder`, `AstBuilder`), and a third is a class
rather than a fork.

Values passed between these methods are *handles*. A parser may hold one, pass it back
in, and ask the builder to spell it, but must never inspect it -- a handle is a
`Spelling` under one builder and a `Node` under another. `spell()` exists for the few
places where a grammar genuinely requires the text of something already built (naming
the class in `Foo::Foo`, for instance), and is the only legal way to look inside.
"""

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

Handle = Any
"""An opaque builder product. Its concrete type is the builder's business."""


@runtime_checkable
class Builder(Protocol):
    """What a parser may ask for. Implementations choose what to produce."""

    # -- leaves ----------------------------------------------------------------

    def builtin(self, spelling: str) -> Handle:
        """A primitive type: `int`, `void`, `char`."""

    def name(self, text: str) -> Handle:
        """A single identifier, already decoded from its length-prefixed form."""

    def raw(self, text: str) -> Handle:
        """Pre-rendered text with no further structure.

        The escape hatch for productions whose spelling a scheme fixes outright, such
        as `std::nullptr_t`. Reach for a specific method first: text passed here is
        invisible to structured consumers.
        """

    def literal(self, kind: Handle | None, value: str) -> Handle:
        """A constant appearing in a type, such as a non-type template argument."""

    # -- composition -----------------------------------------------------------

    def qualified(self, parts: Sequence[Handle]) -> Handle:
        """A scoped name: the parts of `a::b::c`, outermost first."""

    def template(self, base: Handle, arguments: Sequence[Handle]) -> Handle:
        """A template specialisation, `base<arguments...>`."""

    def qualify(self, inner: Handle, qualifiers: Sequence[str]) -> Handle:
        """Apply cv-qualifiers, in the canonical order the caller has already imposed."""

    # -- declarators -----------------------------------------------------------

    def pointer(self, inner: Handle) -> Handle:
        """`inner*`."""

    def reference(self, inner: Handle) -> Handle:
        """`inner&`."""

    def rvalue_reference(self, inner: Handle) -> Handle:
        """`inner&&`."""

    def member_pointer(self, owner: Handle, inner: Handle) -> Handle:
        """`inner Owner::*` -- a pointer to member."""

    def array(self, inner: Handle, dimension: str) -> Handle:
        """`inner [dimension]`. An empty dimension means an unbounded array."""

    def function(
        self,
        returns: Handle | None,
        parameters: Sequence[Handle],
        suffix: str = "",
        name: Handle | None = None,
    ) -> Handle:
        """A function type or a whole function declaration.

        `returns` is None where the scheme encodes no return type -- constructors,
        destructors, conversion operators, and plain non-template functions under the
        Itanium ABI. `suffix` carries what trails the parameter list and binds to the
        function rather than the return type: cv-qualifiers, a ref-qualifier,
        `noexcept`. `name` is the declarator, absent for a bare function *type*.
        """

    def pack(self, inner: Handle) -> Handle:
        """An *unexpanded* parameter pack expansion, `inner...`."""

    def parameter_pack(self, members: Sequence[Handle]) -> Handle:
        """A pack of concrete arguments.

        Must stay a sequence rather than collapse to joined text: a declarator applied
        to a pack applies to every member, so `Dp O T_` over three arguments is three
        rvalue references.
        """

    def vendor_qualify(self, inner: Handle, qualifier: str) -> Handle:
        """A vendor extended qualifier, spelled after the type it applies to."""

    # -- whole symbols ---------------------------------------------------------

    def special(self, label: str, inner: Handle) -> Handle:
        """A symbol that is *about* an entity: `vtable for Foo`, `typeinfo for Bar`."""

    def decorated(self, inner: Handle, decoration: str) -> Handle:
        """A symbol carrying a symbol-table decoration: an ELF version, a clone suffix.

        Kept as structure rather than folded into text so a caller can ask which copy of
        a function a symbol names, or strip the version and compare.
        """

    # -- inspection ------------------------------------------------------------

    def spell(self, handle: Handle, declarator: str = "") -> str:
        """Render a handle, placing `declarator` in the type's declarator position."""

    def size(self, handle: Handle) -> int:
        """How many characters `handle` would render to, without rendering it.

        Must be O(1). This is what lets a parser enforce an output bound *while*
        building: the substitution scheme allows each component to be assembled from two
        copies of an earlier one, so output can double every few input bytes. Checking
        the bound by measuring `spell()` would mean materialising the very string the
        bound exists to prevent -- a few hundred bytes of input can reach gigabytes.
        """


__all__ = ["Builder", "Handle"]
