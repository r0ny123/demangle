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
rather than copied. `render` below spells those the MSVC way as part of a declaration;
asking one of them for its *own* spelling in isolation goes through the shared builder,
which writes `int [2]` where this scheme writes `int[2]`. Nothing that is spelled as a
whole symbol takes that path.

Array extents nest outermost first, so `int[2][3]` is an array of two arrays of three
and each node's `dimension` means what it says on its own.

What the tree does *not* take apart is a name. Namespaces, template arguments and the
class a member belongs to arrive here already spelled, because the mangling resolves its
back-references against text and the parser has to render each one as it goes to number
the next correctly. So the structure recovered is the declaration's shape -- what it
declares, its indirections, its parameter list, its return type -- over leaves that are
text.
"""

from ...core.ast import Array, Name, Node, Raw, rendered
from ...core.style import get_style
from .options import DEFAULT_OPTIONS

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


class _Spelled(Node):
    """A node this scheme spells itself.

    `build()` hands a foreign builder finished text rather than a shape. The shared
    builder has nowhere to put a calling convention, so re-emitting the shape into it
    would produce a spelling MSVC does not use, and a demangler that answers two
    different things for one symbol is worse than one that answers text. Inspecting the
    tree is unaffected: that is what the structure is for.
    """

    __slots__ = ()

    def spell(self, declarator="", style=None):
        # C++ output styles do not apply; a style carries only this scheme's options.
        options = get_style(style).options_for("msvc") or DEFAULT_OPTIONS
        return rendered(lambda: render(self, declarator, options=options))

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

    What the spelling opens with is three pieces rather than one, because each can be
    left out on its own: `prefix` is what comes before everything (`"[thunk]: "`, an
    `extern "C"`), `access` is `"public: "` and its siblings, `member_type` is `"static "`
    or `"virtual "`. `suffix` is the qualifiers that trail a member function's parameter
    list. All four stay text because none is part of the type: they say how the symbol is
    reached, not what it is. `declarator` is the name, which lands in the hole its type
    leaves.
    """

    __slots__ = ("access", "declarator", "member_type", "prefix", "suffix", "type")
    kind = "declaration"

    def __init__(self, prefix, declarator, type_, suffix="", access="", member_type=""):
        self.prefix = prefix
        self.access = access
        self.member_type = member_type
        self.declarator = declarator
        self.type = type_
        self.suffix = suffix

    @property
    def declares_a_function(self):
        """Whether this declares a function rather than data.

        A variable of function-pointer type is data: `int (__cdecl *p)(int)` has a
        `FunctionType` in it, but not as the thing being declared, which is why the
        reference leaves its convention alone under `--no-calling-convention`.
        """
        return self.type.kind == "function"

    def children(self):
        return (self.declarator, self.type)


def render(
    node,
    declarator="",
    declarator_is_function=False,
    options=DEFAULT_OPTIONS,
    member_cv="",
    as_pointee=False,
    sigil=False,
):
    """Spell a type around a declarator, the way C nests one inside the other.

    A pointer or array binds to the declarator built so far, and a name therefore ends up
    *inside* its type: `int (*j)[2]`, not `int (*)[2] j`.

    `options` says which parts of a *declaration* to print. It reaches the declaration's
    own function type and stops there: a parameter, a template argument and a variable's
    pointee are all rendered with the defaults, which is what the reference does and why
    `--no-calling-convention` leaves `int (__cdecl *)(int)` alone in a parameter list.

    `sigil` says the declarator opens with an indirection's, which an array has to
    bracket and cannot always see in the text: `*` and `&` it can, `A::*` it cannot,
    since a qualified data name opens the same way. `int (A::*)[4]`, not `int A::*[4]`.
    """
    kind = node.kind
    if kind == "raw":
        if not declarator:
            return node.text
        if declarator.startswith("["):
            return node.text + declarator
        # the reference spaces a declarator off a type ending in an alphanumeric character
        # or a template's ">", and abuts it to anything else: "struct S *" but "struct S_*"
        tail = node.text[-1:]
        sigil = declarator.startswith(("*", "&"))
        # `decltype(auto) *f`: LLVM's main branch writes `decltype(auto)*f`.
        word = tail == ">" or (tail.isascii() and tail.isalnum()) or node.text.endswith(_KEYWORDS_ENDING_IN_A_BRACKET)
        abuts = sigil and not word
        return node.text + ("" if abuts else " ") + declarator
    if kind == "indirection":
        token = node.sigil + " ".join(ordered_qualifiers(node.qualifiers))
        separator = " " if _spaced_off_the_sigil(node, declarator, declarator_is_function, options) else ""
        # Said here, where it is known, rather than guessed from the declarator's text.
        return render(node.inner, token + separator + declarator, options=options, as_pointee=True, sigil=True)
    if kind == "array":
        if sigil or declarator.startswith(("*", "&")):
            declarator = f"({declarator})"
        return render(node.inner, f"{declarator}[{node.dimension}]", options=options)
    if kind == "declaration":
        # a declaration is never nested inside a type, so nothing may be threaded into it
        lead = node.prefix
        if options.access_specifier:
            lead += node.access
        if options.member_type:
            lead += node.member_type
        if not options.variable_type and not node.declares_a_function:
            # `public: static C::sm`, as the reference gives.
            return lead + node.declarator.text + node.suffix
        # Member qualifiers go inside the return type's wrapping, after the parameter
        # list (`FunctionSignatureNode::outputPost`), not after the whole declarator.
        return lead + render(node.type, node.declarator.text, options=options, member_cv=node.suffix)
    # A pointer's pointee has its convention printed by the pointer, which the reference's
    # `--no-calling-convention` does not reach: `int (__cdecl * fn(void))(int)`.
    as_pointee = as_pointee or declarator.startswith(("*", "&"))
    convention = node.convention if options.calling_convention or as_pointee else ""
    # Suppressed is not absent: `$$A6A_N_N@Z` reads `__cdecl(bool)`, not a constructor's
    # `__cdecl (bool)`.
    suppressed = node.returns is not None and not (options.return_type or as_pointee)
    member_cv = node.member_cv or member_cv
    params = ", ".join([render(parameter, options=options) for parameter in node.parameters])
    if node.returns is None:
        # No return type, nothing around the parameters: "public: __thiscall foo::foo(void)"
        return spelled_after(convention, f"{declarator}({params}){member_cv}")
    if not convention and not as_pointee:
        # a pointer keeps the space the convention would have filled: "int ( *)()"
        inner = f"{declarator}({params}){member_cv}"
        return inner if suppressed else render(node.returns, inner, True, options)
    if as_pointee:
        # "int (__attribute__((__swiftcall__))  *j)(int)". A suppressed convention takes
        # its space with it, as `dbghelp` under UNDNAME_NO_MS_KEYWORDS: "int (*)(void)".
        gap = "  " if convention.startswith("__attribute__") else " "
        if not convention and not options.ms_keywords:
            gap = ""
        declarator = f"({convention}{gap}{declarator})"
    else:
        declarator = f"{convention} {declarator}" if declarator else convention
    inner = f"{declarator}({params}){member_cv}"
    return inner if suppressed else render(node.returns, inner, True, options)


#: Type names that end in `)` and are still one word.
_KEYWORDS_ENDING_IN_A_BRACKET = ("decltype(auto)",)


def _spaced_off_the_sigil(node, declarator, declarator_is_function, options):
    """Whether a pointer's sigil is separated from the declarator it binds to.

    Two shapes take the space and everything else abuts. The pointer's own qualifiers take
    one, or `char *const p` would read `char *constp`. A nested *function* declarator takes
    one, because the declaration's own calling convention is written there:
    `int * (__cdecl * __cdecl fn(void))(int)`.

    Where that leaves a run that is dropping every Microsoft keyword depends on what the
    pointer points at, and the reference is not being inconsistent about it. A pointer *to
    a function* writes both conventions in one bracket, so with neither of them left there
    is nothing to separate and the sigil closes up: `int * (*fn(void))(int)`. A pointer to
    anything else was only ever holding its sigil off the name -- `char const * __cdecl
    f4(...)` is a pointer return type, not a function pointer -- so the space stays:
    `char const * f4(...)`.
    """
    if not declarator or declarator.startswith("["):
        return False
    if node.qualifiers:
        return True
    # Anything but a function declarator abuts: `int (*(*a)[20])()`.
    if not declarator_is_function or declarator.startswith(("*", "&")):
        return False
    return options.ms_keywords or node.inner.kind != "function"


def spelled_after(convention, text):
    """Join a calling convention to what follows it, skipping the ones spelled with
    nothing."""
    return f"{convention} {text}" if convention else text


def prefixed(text, node):
    """Put `text` in front of a whole symbol, without disturbing the shape underneath it.

    The one caller is `extern "C" `, and it lands in the declaration's `member_type`
    rather than ahead of everything: the reference groups it with `static` and `virtual`,
    so `--no-member-type` drops all three together.

    After them and not before, which is where the reference writes it: `?fn@@$$J0EAAHH@Z`
    is `private: virtual extern "C" int __cdecl fn(int)`, not `extern "C" virtual`.
    Only a name carrying both shows the difference, and every `$$J` in the corpora is on
    a free function or an ordinary member, where `member_type` is empty and the two
    orders are the same string.
    """
    if not text:
        return node
    if node.kind == "declaration":
        return Declaration(node.prefix, node.declarator, node.type, node.suffix, node.access, node.member_type + text)
    return Raw(text + render(node))


def merge_qualifiers(left, right):
    # "int __unaligned *__unaligned *"
    return ordered_qualifiers(tuple(left) + tuple(right))


#: The reference's fixed order (`outputQualifiers` tests a bitmask, const first).
_QUALIFIER_ORDER = ("const", "volatile", "__restrict", "__unaligned")


#: Keyed without leading underscores, so `leading_underscores=False`'s `restrict` ranks.
_QUALIFIER_RANK = {qual.lstrip("_"): rank for rank, qual in enumerate(_QUALIFIER_ORDER)}


def ordered_qualifiers(quals):
    """`quals` in `_QUALIFIER_ORDER`, deduplicated, in whatever spelling they arrived in.

    Ranked by the word without its underscores, so a run that is dropping them orders
    the same qualifiers the same way. The spelling kept is the one that was passed in:
    this decides an order, not a wording.
    """
    if not quals:
        return ()
    ranked = {}
    for qual in quals:
        rank = _QUALIFIER_RANK.get(qual.lstrip("_"))
        if rank is not None:
            ranked.setdefault(rank, qual)
    return tuple(ranked[rank] for rank in sorted(ranked))


def apply_qualifiers(node, quals):
    """Qualify a named type, as a pointee qualifier or a $$C wrapper does.

    Only ever reached with a named type: an indirection merges its qualifiers as it is
    built, and a back-reference declines rather than accept one.

    In `_QUALIFIER_ORDER` and not in the order they arrived. Appending would make a
    type qualified twice -- a pointee qualifier and then the variable's own, which
    `?s4@PR13182@@3PCDD` is -- come out `char volatile const *` where the reference
    writes `char const volatile *`. So any already at the end of the text are taken back
    off and the whole set is written in one order.
    """
    if not quals:
        return node
    words = node.text.split(" ")
    trailing = []
    while words and words[-1].lstrip("_") in _QUALIFIER_RANK:
        trailing.insert(0, words.pop())
    ordered = ordered_qualifiers((*trailing, *quals))
    return Raw(" ".join([*words, *ordered]))


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
    # Only *trailing* words count: "?s@@3QBDD" is "char const volatile *const", but a
    # qualifier inside a template argument (`?h@FTypeWithQuals@@3U?$S@$$A8@@HCAHXZ@1@C`)
    # must not suppress the symbol's own.
    return apply_qualifiers(node, quals)
