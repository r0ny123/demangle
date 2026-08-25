"""The Rust symbol tree.

Rust's spelling has no declarator syntax at all. A C++ type wraps the name it declares
-- `int (*)(char)` -- which is why `core/spelling.py` carries a left/right pair and why
MSVC needs a renderer of its own. Rust writes `&mut [u8; 4]` strictly left to right, and
a path is just its components joined by `::`. So the structure worth recovering here is
not a declarator shape: it is *which part of the name is which* -- the crate, the module
path, the generic arguments, the trait an impl is for, the closure disambiguator.

Every node holds `parts`: a tuple of plain strings and child nodes, in output order.
Rendering concatenates them. That is deliberate and is the whole correctness argument
for this module -- the printer in `_v0.py` emits one linear stream of fragments whether
it is building text or a tree, so a tree's rendering cannot drift from the text path.
It is not that the two are tested to agree; there is only one stream, and the tree is
that stream with the boundaries remembered.

Kinds are shared with the other schemes where they mean the same thing, so a tool can
ask any tree for its `name` or `template` nodes without knowing which language produced
it. `impl` and `namespace` are Rust's own: nothing in the C-family schemes means either.
"""

from ...core.ast import Node

__all__ = [
    "Generics",
    "Impl",
    "Namespace",
    "Path",
    "RustName",
    "Symbol",
    "Type",
    "Value",
    "render",
]


def render(part):
    """Text for a part, which is either a plain fragment or a subtree."""
    return part if isinstance(part, str) else part.render()


class _Rust(Node):
    """A node whose spelling is the fragments it was built from, in order.

    `build()` hands a foreign builder finished text. Re-emitting the shape through the
    shared builder would spell Rust's `&mut T` as C++'s `T&`, and a demangler that
    answers two different things for one symbol is worse than one that answers text.
    Inspecting the tree is unaffected -- that is what the structure is for.
    """

    __slots__ = ("parts",)

    def __init__(self, parts):
        self.parts = tuple(parts)
        total = 0
        for part in self.parts:
            total += len(part) if isinstance(part, str) else part.size
        self.size = total

    def children(self):
        return tuple(p for p in self.parts if isinstance(p, Node))

    def render(self):
        return "".join(render(p) for p in self.parts)

    def spell(self, declarator="", style=None):
        # `declarator` and `style` are accepted to match `Node.spell` and ignored. Rust
        # has no declarator position to place one in, and the output styles exist only
        # where llvm-cxxfilt and GNU c++filt disagree about C++.
        return self.render()

    def build(self, builder):
        return builder.raw(self.render())

    @property
    def text(self):
        """The rendered text, so a caller can read a node without spelling a whole tree."""
        return self.render()


class RustName(_Rust):
    """A single identifier: a crate name, a module, a function, a field.

    Named `RustName` rather than `Name` because `core.ast.Name` already exists and means
    the same thing to a consumer -- hence the shared `kind` -- but spells itself through
    the shared builder, which is not this scheme's speller.
    """

    __slots__ = ()
    kind = "name"


class Path(_Rust):
    """A path: `std::collections::HashMap`, or a crate root on its own."""

    __slots__ = ()
    kind = "path"


class Namespace(_Rust):
    """A compiler-generated path component: `{closure#0}`, `{shim:vtable#0}`.

    `tag` is the namespace letter from the mangling -- `C` for a closure, `S` for a
    shim, or a vendor's own -- and `disambiguator` separates several in one scope.
    """

    __slots__ = ("disambiguator", "tag")
    kind = "namespace"

    def __init__(self, parts, tag, disambiguator):
        super().__init__(parts)
        self.tag = tag
        self.disambiguator = disambiguator


class Impl(_Rust):
    """An inherent or trait impl: `<Foo>`, `<Foo as Bar>`.

    `trait` is the trait's subtree for a trait impl and None for an inherent one, so a
    caller can ask what a method implements without matching on rendered text.
    """

    __slots__ = ("self_type", "trait")
    kind = "impl"

    def __init__(self, parts, self_type, trait):
        super().__init__(parts)
        self.self_type = self_type
        self.trait = trait


class Generics(_Rust):
    """A generic argument application, `Vec<u8>` or `Foo::<T>`.

    `base` is what is being applied to and `arguments` are the arguments themselves,
    both also present in `parts` -- the fields name them, `parts` orders them.
    """

    __slots__ = ("arguments", "base")
    kind = "template"

    def __init__(self, parts, base, arguments):
        super().__init__(parts)
        self.base = base
        self.arguments = tuple(arguments)


class Type(_Rust):
    """A type. `form` says which shape: `reference`, `pointer`, `array`, `slice`,
    `tuple`, `dyn`, `fn`, `basic`, `path`.

    One class rather than nine because nothing walks these structurally -- a caller
    asking "is this a reference" wants a field to test, and `kind` stays `type` so the
    common query finds every one of them.
    """

    __slots__ = ("form",)
    kind = "type"

    def __init__(self, parts, form):
        super().__init__(parts)
        self.form = form


class Value(_Rust):
    """A const generic argument: an integer, a `bool`, a `char`, a structural const."""

    __slots__ = ("form",)
    kind = "literal"

    def __init__(self, parts, form):
        super().__init__(parts)
        self.form = form


class Symbol(_Rust):
    """A whole Rust symbol.

    `hash` is the value of the legacy scheme's trailing `17h<16 hex>` component -- the
    sixteen digits, without the `h` marker or the length prefix. The mangling carries it
    and the spelling drops it; it is recorded because it is the only thing telling two
    monomorphisations of one generic function apart. `suffix` is whatever followed
    the mangled name -- an LLVM `.llvm.<hash>` from the internaliser, a `.0` from a
    promoted constant.
    """

    __slots__ = ("hash", "suffix", "vendor")
    kind = "symbol"

    def __init__(self, parts, hash=None, suffix="", vendor=""):
        super().__init__(parts)
        self.hash = hash
        self.suffix = suffix
        self.vendor = vendor
