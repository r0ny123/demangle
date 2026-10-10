"""The fast builder: C++ declaration syntax, built directly as text.

C++ does not write a type before the name, it writes it *around* the name. The type
"pointer to function taking char and returning int" is spelled `int (*)(char)`, and
giving it a name puts that name in a hole in the middle: `int (*f)(char)`. Arrays do
the same: `int a[10]`, and a pointer to one is `int (*)[10]`.

So a partially built type is a pair of strings, `left` and `right`, and rendering is
`left + declarator + right`. Every function here exists to keep that hole in the right
place as declarators nest. It is the part of a demangler most often subtly wrong, which
is why it is one small module with its own tests rather than logic spread through three
parsers.

Spellings are immutable. Nothing here mutates a handle it was given, so a builder
product can be stored in a substitution table and reused without defensive copying --
which matters, because that is exactly what the Itanium back-reference scheme does.
"""

from .builder import Builder
from .decorations import describe

#: After these a declarator name needs no separating space of its own.
_TIGHT_ENDINGS = (" ", "(")


class Spelling:
    """One type or name, split around its declarator position."""

    __slots__ = ("is_array", "is_function", "left", "members", "ref_kind", "right")

    #: Set only on `QualifiedSpelling`; class attributes here so the common unqualified
    #: case pays no extra stores.
    cv = ()
    stem = ""

    def __init__(self, left, right="", is_function=False, is_array=False, ref_kind="", members=None):
        self.left = left
        self.right = right
        self.is_function = is_function
        self.is_array = is_array
        #: "", "&" or "&&", for reference collapsing.
        self.ref_kind = ref_kind
        #: For a parameter pack, its members; None otherwise. Kept as a sequence because
        #: a declarator applies to every member: `Dp O T_` over three arguments is three
        #: rvalue references.
        self.members = members

    def spell(self, declarator=""):
        """This type with `declarator` -- a name -- written where a declarator goes.

        Both references space the name off the type unless the type has a *right* part
        to put after it, which is what `NonTypeTemplateParamDecl::printLeft` asks
        `hasRHSComponent` in LLVM's `ItaniumDemangle.h`: `int* $N` and `int A::* $N`
        against `int$N [3]` and `int (*$N) [3]`. A function type's left half already
        ends in a space, so `int $N()` comes out with exactly one either way. Verified
        against both references over every declarator shape; `int*$N`, which is what a
        rule about the last character gives, is what neither writes.
        """
        if not declarator:
            return self.left + self.right if self.right else self.left
        left = self.left
        if not left:
            return declarator + self.right
        joiner = "" if self.right or left.endswith(_TIGHT_ENDINGS) else " "
        return left + joiner + declarator + self.right

    def __str__(self):
        return self.left + self.right if self.right else self.left

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Spelling({self.left!r}, {self.right!r})"


class QualifiedSpelling(Spelling):
    """A type that ends in cv-qualifiers, remembering which and what precedes them.

    Enough to answer "is this already const" without reading the text back, which is
    what `collapse_duplicate_qualifiers` needs and what nothing else does.
    """

    __slots__ = ("cv", "stem")

    def __init__(self, left, right="", is_array=False, cv=(), stem=""):
        super().__init__(left, right, is_array=is_array)
        self.cv = cv
        self.stem = stem


def pack_of(members):
    """A parameter pack, spelled as its members and still addressable as a sequence.

    Nested packs are spliced. A pack whose one member is an expansion of an empty pack
    is empty, not a pack of one empty thing -- otherwise `AnalysisManager<T_, J Dp T0_ E>`
    with `T0_` bound to nothing prints the separator for an argument that is not there.
    """
    flattened = []
    for member in members:
        if member.members is not None:
            flattened.extend(member.members)
        else:
            flattened.append(member)
    flattened = tuple(flattened)
    return Spelling(", ".join([member.left + member.right for member in flattened]), members=flattened)


def _respace_bound(left, right):
    """Space an array's bound off whatever now precedes it, or take the space away.

    The reference decides this from the last character it has printed: a bound follows a
    space unless what came before already ends in a bracket. So `int [3]` and
    `int (*) [3]` and `int vector[4] const [3]`, but `int vector[4][3]` and
    `int [5][4]`.

    "What came before" moves as declarators nest -- a `const` or a `*` goes in between
    the two halves -- so the decision cannot be made once when the array is built. It is
    re-made by every constructor that changes `left`, which is what this is for.
    """
    bound = right.lstrip(" ")
    if not bound.startswith("["):
        return right
    return bound if left.endswith("]") else " " + bound


def _wrap(inner, token, ref_kind="", tight_after_star=False, tight_before_group=False):
    """Apply a declarator token, parenthesising where precedence demands it.

    Without the parentheses `int (*)(char)` would read as `int *(char)`: a function
    returning a pointer, which is a different type. The rule is that a declarator
    binding tighter than the one already applied needs grouping, and function and array
    types are precisely the cases where one already has been.

    `tight_after_star` is GNU c++filt's spacing for a function's group that follows a
    `*`: `d_print_function_type` writes the space before its `(` unless the last
    character printed is `(` or `*`, so a pointer to a function returning a pointer to
    a function is `void (*(*)())()` to it and `void (* (*)())()` to llvm-cxxfilt, and a
    `&` keeps its space either way: `int (& (*)()) [3]`. An array's group is
    `d_print_array_type`'s, which writes ` (` whatever came before, so a reference to an
    array of pointers stays `tree_node* (&) [3]`.

    `tight_before_group` is llvm-cxxfilt's spacing for a pointer to a member whose type
    is an array: `PointerToMemberType::printLeft` writes its `(` straight after the
    member type where `PointerType::printLeft` writes a space first, so
    `int(A::*) [3]` beside `int (*) [3]`, and `void (*(A::*) [3])()` for an array of
    function pointers. GNU c++filt spaces both.
    """
    if inner.members is not None:
        return pack_of(_wrap(member, token, tight_after_star=tight_after_star) for member in inner.members)
    if inner.is_function or inner.is_array:
        left = inner.left
        tight = (" ", "(", "*") if tight_after_star and inner.is_function else (" ", "(")
        spacer = "" if tight_before_group or not left or left.endswith(tight) else " "
        # The bound follows the `)`, so it is spaced against that: `int vector[4] (*) [3]`.
        right = ")" + _respace_bound(")", inner.right) if inner.is_array else ")" + inner.right
        return Spelling(left + spacer + "(" + token, right, ref_kind=ref_kind)
    return Spelling(inner.left + token, inner.right, ref_kind=ref_kind)


class _BuiltinCache(dict):
    def __missing__(self, key):
        return Spelling(key)


_BUILTIN_SPELLINGS = _BuiltinCache(
    {
        name: Spelling(name)
        for name in (
            "void",
            "bool",
            "char",
            "signed char",
            "unsigned char",
            "short",
            "unsigned short",
            "int",
            "unsigned int",
            "long",
            "unsigned long",
            "long long",
            "unsigned long long",
            "__int128",
            "unsigned __int128",
            "float",
            "double",
            "long double",
            "__float128",
            "half",
            "wchar_t",
            "char8_t",
            "char16_t",
            "char32_t",
            "auto",
            "decltype(auto)",
            "decltype(nullptr)",
            "decimal32",
            "decimal64",
            "decimal128",
            "std::nullptr_t",
            "std::bfloat16_t",
            "_BitInt",
            "unsigned _BitInt",
            "...",
            "_Float16",
            "_Float32",
            "_Float64",
            "_Float128",
            "_Float16x",
            "_Float32x",
            "_Float64x",
            "_Float128x",
        )
    }
)


class SpellingBuilder(Builder):
    """Builds C++ declaration text. The backend behind `demangle()`.

    Stateless, so one instance is shared by every parse rather than allocated per name.
    """

    __slots__ = (
        "collapse_duplicate_qualifiers",
        "gnu_clone_suffix",
        "legacy_angle_spacing",
        "tight_after_star",
        "tight_member_array",
    )

    def __init__(
        self,
        legacy_angle_spacing=False,
        gnu_clone_suffix=False,
        collapse_duplicate_qualifiers=False,
        tight_after_star=False,
        tight_member_array=True,
    ):
        #: Write `Foo<Bar<int> >` (pre-C++11 `>>` lexing) and `operator<< <int>` rather
        #: than `operator<<<int>`. GNU c++filt does; llvm-cxxfilt does not.
        self.legacy_angle_spacing = legacy_angle_spacing
        #: Write a clone suffix as `[clone .cold]` (GNU c++filt) rather than `(.cold)`.
        self.gnu_clone_suffix = gnu_clone_suffix
        #: Write `void (*(*)())()` rather than `void (* (*)())()`, as GNU c++filt does.
        self.tight_after_star = tight_after_star
        #: Write `int(A::*) [3]` rather than `int (A::*) [3]`, as llvm-cxxfilt does.
        self.tight_member_array = tight_member_array
        #: Print `int const` for `K K i`, rather than llvm-cxxfilt's `int const const`
        #: ([basic.type.qualifier]: at most one of each). The outer one wins, so `K V K i`
        #: and `K K V i` both read `volatile const`, as c++filt prints them (checked over
        #: all 39 sequences of one to three). An array passes the qualifiers through to
        #: its element; every other declarator stops them.
        self.collapse_duplicate_qualifiers = collapse_duplicate_qualifiers

    # The class itself rather than a method calling it: a class attribute does not bind,
    # so `builder.name(text)` is `Spelling(text)`, and these are the builder's commonest
    # calls. Builtins are finite and immutable, so pre-created instances are returned directly.
    builtin = _BUILTIN_SPELLINGS.__getitem__
    name = Spelling
    raw = Spelling

    def literal(self, kind, value):
        return Spelling(value)

    def expression(self, form, parts):
        return Spelling("".join([part if isinstance(part, str) else self.spell(part) for part in parts]))

    def qualified(self, parts):
        n = len(parts) if isinstance(parts, (list, tuple)) else len(parts := tuple(parts))
        if n == 2:
            p0, p1 = parts
            s0 = p0.left if not p0.right else p0.left + p0.right
            s1 = p1.left if not p1.right else p1.left + p1.right
            return Spelling(f"{s0}::{s1}")
        if n == 1:
            p0 = parts[0]
            return Spelling(p0.left if not p0.right else p0.left + p0.right)
        return Spelling("::".join([part.left if not part.right else part.left + part.right for part in parts]))

    def template(self, base, arguments, angle_space=True):
        n = len(arguments) if isinstance(arguments, (list, tuple)) else len(arguments := tuple(arguments))
        if n == 1:
            arg = arguments[0]
            rendered = arg.left if not arg.right else arg.left + arg.right
        elif n == 2:
            a0, a1 = arguments
            s0 = a0.left if not a0.right else a0.left + a0.right
            s1 = a1.left if not a1.right else a1.left + a1.right
            rendered = f"{s0}, {s1}"
        else:
            rendered = ", ".join(
                [argument.left if not argument.right else argument.left + argument.right for argument in arguments]
            )
        if self.legacy_angle_spacing and angle_space and rendered.endswith(">"):
            rendered += " "
        name = base.left if not base.right else f"{base.left}{base.right}"
        opening = " <" if self.legacy_angle_spacing and name.endswith("<") else "<"
        return Spelling(f"{name}{opening}{rendered}>")

    def qualify(self, inner, qualifiers, cv=True):
        """Apply `qualifiers` to `inner`, on the right, the way C++ writes them.

        `cv` says whether these are cv-qualifiers, which is what decides whether a
        repeat collapses. `_Complex` and `_Imaginary` go through here too and are not
        cv-qualifiers: `[basic.type.qualifier]` folds a duplicate `const`, and nothing
        folds a duplicate `_Imaginary`. `c++filt` 2.42 writes `signed char _Imaginary
        _Imaginary` for `_Z1fGGa`; collapsing it to one would lose a word of the name,
        and only the gnu style collapses at all.
        """
        if not qualifiers:
            return inner
        if inner.members is not None:
            return pack_of(self.qualify(member, qualifiers, cv=cv) for member in inner.members)
        if inner.is_function:
            # cv on a function type qualifies the implicit object parameter, so it
            # trails the parameter list rather than the return type.
            return Spelling(inner.left, inner.right + " " + " ".join(qualifiers), is_function=True)
        if not (self.collapse_duplicate_qualifiers and cv):
            # Both `int const` and `int* const` are postfix in C++, as the references agree.
            left = inner.left + " " + " ".join(qualifiers)
            right = _respace_bound(left, inner.right) if inner.is_array else inner.right
            return Spelling(left, right, is_array=inner.is_array)
        # Repeated qualifiers move to the end: the outermost is the one c++filt prints last.
        stem = inner.stem if inner.cv else inner.left
        combined = tuple(q for q in inner.cv if q not in qualifiers) + tuple(qualifiers)
        left = stem + " " + " ".join(combined)
        right = _respace_bound(left, inner.right) if inner.is_array else inner.right
        return QualifiedSpelling(left, right, is_array=inner.is_array, cv=combined, stem=stem)

    def pointer(self, inner):
        return _wrap(inner, "*", tight_after_star=self.tight_after_star)

    def parameter_pack(self, members):
        return pack_of(members)

    def reference(self, inner):
        # Reference collapsing ([dcl.ref]): `&` applied to any reference is `T&`; every
        # `std::forward` instantiation reaches this.
        if inner.members is not None:
            return pack_of(self.reference(member) for member in inner.members)
        if inner.ref_kind == "&":
            return inner
        if inner.ref_kind == "&&":
            return Spelling(inner.left[:-1], inner.right, ref_kind="&")
        return _wrap(inner, "&", ref_kind="&", tight_after_star=self.tight_after_star)

    def rvalue_reference(self, inner):
        # `T& &&` collapses to `T&`; only `T&& &&` stays an rvalue reference.
        if inner.members is not None:
            return pack_of(self.rvalue_reference(member) for member in inner.members)
        if inner.ref_kind:
            return inner
        return _wrap(inner, "&&", ref_kind="&&", tight_after_star=self.tight_after_star)

    def member_pointer(self, owner, inner):
        if owner.members is not None:
            # The owner packs too: a pointer to a member of each of them.
            return pack_of(self.member_pointer(one, inner) for one in owner.members)
        if inner.members is not None:
            return pack_of(self.member_pointer(owner, member) for member in inner.members)
        token = f"{owner}::*"
        if inner.is_function or inner.is_array:
            return _wrap(
                inner,
                token,
                tight_after_star=self.tight_after_star,
                tight_before_group=inner.is_array and self.tight_member_array,
            )
        # Always spaced, unlike a bare `*`: `int*A::*` would read as one token.
        left = inner.left
        joiner = "" if not left or left.endswith((" ", "(")) else " "
        return Spelling(left + joiner + token, inner.right)

    def array(self, inner, dimension):
        if inner.members is not None:
            # An empty pack gives no arrays at all.
            return pack_of(self.array(member, dimension) for member in inner.members)
        if inner.cv:
            # cv on an array is cv on its elements, so `K A3_ K i` has one `const`.
            bound = f"[{dimension}]" if dimension else "[]"
            right = inner.right.lstrip(" ") if inner.is_array else inner.right
            return QualifiedSpelling(
                inner.left,
                _respace_bound(inner.left, bound + right),
                is_array=True,
                cv=inner.cv,
                stem=inner.stem,
            )
        bound = f"[{dimension}]" if dimension else "[]"
        right = inner.right
        # Only the first bracket of a multi-dimensional array is spaced off the type:
        # `Libcall const (&) [5][4]`. Dimensions are built inside out.
        if inner.is_array:
            right = right.lstrip(" ")
        return Spelling(inner.left, _respace_bound(inner.left, bound + right), is_array=True)

    def function(self, returns, parameters, suffix="", name=None):
        rendered = ", ".join([parameter.left + parameter.right for parameter in parameters])
        params = "(" + rendered + ")"
        if returns is None:
            result = Spelling("", params + suffix, is_function=True)
        else:
            # No space after a return type that wraps around the name (a pointer to a
            # function or array: `int (*f())()`), told apart by having a right half.
            left = returns.left
            wraps = bool(returns.right)
            joiner = "" if wraps and left.endswith(("*", "&", "(", " ")) else " "
            # A function-type return's `()` is in the right half, so this function's
            # suffix goes after it: `f name()() requires C`; an array return or a grouped
            # declarator keeps it before: `int () const []`, `int (*f() const)()`.
            function_return = wraps and returns.right.lstrip().startswith("(")
            if function_return:
                result = Spelling(left + joiner, params + returns.right + suffix, is_function=True)
            else:
                result = Spelling(left + joiner, params + suffix + returns.right, is_function=True)
        if name is None:
            return result
        return Spelling(result.spell(str(name)))

    def pack(self, inner):
        """An unexpanded pack expansion: the whole type, then `...`.

        The reference's `ParameterPackExpansion` prints its child -- both halves -- and
        appends the ellipsis, so a declarator type keeps its shape and the dots follow
        it: `void ()...`, `void (*)()...`, `int [3]...`. Putting them in the left half
        alone would put them where a declarator's name goes, `void (*...)()` and
        `int... [3]`, which is not what any reference prints. A type with no right half
        is unchanged.
        """
        return Spelling(inner.left + inner.right + "...")

    def vendor_qualify(self, inner, qualifier):
        """A vendor qualifier goes after the *whole* type, declarator and all.

        The reference prints the type and then the extension, so a function type comes
        out `void () block_pointer` -- not `void block_pointer()`, which is what putting
        the word in the left half alone gives for anything that has a right half.

        Distributes over a pack like every other declarator: an empty pack gives nothing
        rather than a bare ` enable_if`, a qualifier on nothing.
        """
        if inner.members is not None:
            return pack_of(self.vendor_qualify(member, qualifier) for member in inner.members)
        return Spelling(inner.spell() + " " + qualifier)

    def special(self, label, inner):
        return Spelling(label + str(inner))

    def decorated(self, inner, decoration):
        return Spelling(inner.spell() + describe(decoration, self.gnu_clone_suffix))

    def spell(self, handle, declarator=""):
        return handle.spell(declarator)

    def size(self, handle):
        return len(handle.left) + len(handle.right)

    def members(self, handle):
        """The members of a parameter pack, or None if this is not one."""
        return handle.members


#: Shared instances (immutable). Parsers take a builder argument; the API layer picks one.
SPELLING_BUILDER = SpellingBuilder()
LEGACY_SPELLING_BUILDER = SpellingBuilder(
    legacy_angle_spacing=True,
    gnu_clone_suffix=True,
    collapse_duplicate_qualifiers=True,
    tight_after_star=True,
    tight_member_array=False,
)
