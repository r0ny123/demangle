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

#: Characters after which a declarator needs no separating space: `int*x` reads fine,
#: `intx` does not.
_TIGHT_ENDINGS = ("*", "&", "(", " ", ":")


class Spelling:
    """One type or name, split around its declarator position."""

    __slots__ = ("is_array", "is_function", "left", "members", "ref_kind", "right")

    #: The cv-qualifiers this spelling ends with, innermost first, and the text with
    #: those words taken off. Only a qualified type carries them, and only `qualify`
    #: writes them -- which is why they are class attributes here and slots on the
    #: subclass below: the great majority of spellings are not qualified types, and
    #: adding two more stores to every construction would be paid for by all of them.
    cv = ()
    stem = ""

    def __init__(self, left, right="", is_function=False, is_array=False, ref_kind="", members=None):
        self.left = left
        self.right = right
        self.is_function = is_function
        self.is_array = is_array
        #: "", "&" or "&&". Tracked so reference collapsing can be applied.
        self.ref_kind = ref_kind
        #: For a parameter pack, its members; None for an ordinary type.
        #:
        #: A pack stays a sequence rather than becoming its joined text, because a
        #: declarator applied to a pack applies to every member: `Dp O T_` over three
        #: arguments is three rvalue references, not one wrapped around the joined
        #: spelling. Every constructor below distributes over `members` when present.
        self.members = members

    def spell(self, declarator=""):
        if not declarator:
            return self.left + self.right if self.right else self.left
        left = self.left
        if not left:
            return declarator + self.right
        joiner = "" if left.endswith(_TIGHT_ENDINGS) else " "
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


def _wrap(inner, token, ref_kind=""):
    """Apply a declarator token, parenthesising where precedence demands it.

    Without the parentheses `int (*)(char)` would read as `int *(char)`: a function
    returning a pointer, which is a different type. The rule is that a declarator
    binding tighter than the one already applied needs grouping, and function and array
    types are precisely the cases where one already has been.
    """
    if inner.members is not None:
        # Applying a declarator to a pack applies it to each member.
        return pack_of(_wrap(member, token) for member in inner.members)
    if inner.is_function or inner.is_array:
        left = inner.left
        # A function's left half already ends in the space after its return type; an
        # array's ends in an identifier character and needs one added.
        spacer = "" if not left or left.endswith((" ", "(")) else " "
        # The bound now follows the `)` this closes with, not the element type, so its
        # spacing is decided against that: `int vector[4] (*) [3]`, not `(*)[3]`.
        right = ")" + _respace_bound(")", inner.right) if inner.is_array else ")" + inner.right
        return Spelling(left + spacer + "(" + token, right, ref_kind=ref_kind)
    return Spelling(inner.left + token, inner.right, ref_kind=ref_kind)


class SpellingBuilder(Builder):
    """Builds C++ declaration text. The backend behind `demangle()`.

    Stateless, so one instance is shared by every parse rather than allocated per name.
    """

    __slots__ = ("collapse_duplicate_qualifiers", "gnu_clone_suffix", "legacy_angle_spacing")

    def __init__(self, legacy_angle_spacing=False, gnu_clone_suffix=False, collapse_duplicate_qualifiers=False):
        #: Write `Foo<Bar<int> >` rather than `Foo<Bar<int>>`. Required before C++11,
        #: when `>>` at the end of a template-id lexed as a right-shift operator. GNU
        #: c++filt still prints it; llvm-cxxfilt does not. Neither is wrong.
        #:
        #: The same flag also puts a space *before* the argument list when the name ends
        #: with `<`, so that `operator<<` instantiated at `int` reads
        #: `operator<< <int>` rather than `operator<<<int>`. Same reason -- three angle
        #: brackets in a row -- and the same two references differ on it the same way.
        self.legacy_angle_spacing = legacy_angle_spacing
        #: Write a clone suffix as `[clone .cold]` rather than `(.cold)`. GNU c++filt
        #: does the former, llvm-cxxfilt the latter.
        self.gnu_clone_suffix = gnu_clone_suffix
        #: Print `int const` where the mangling says `K K i`, rather than llvm-cxxfilt's
        #: `int const const`.
        #:
        #: A cv-qualifier applied to a type that already carries it adds nothing --
        #: [basic.type.qualifier] gives a type at most one of each -- so `const const`
        #: is a spelling no declaration has. It reaches a mangled name two ways: written
        #: outright, `K K i`, and through a template argument that is already qualified,
        #: `K T_` with `T_` bound to `K i`, which is where all three of the shipped
        #: libraries' instances come from.
        #:
        #: The outer one wins, which decides the order the survivors print in: c++filt
        #: reads `K V K i` as `volatile const` and `K K V i` as `volatile const`.
        #: Verified against it over all 39 sequences of one to three qualifiers.
        #:
        #: An array passes the qualifiers through -- cv on an array qualifies its
        #: element type, so `K A3_ K i` is `int const [3]` -- and every other declarator
        #: stops them: `K P K i` is `int const* const`, two different `const`s.
        self.collapse_duplicate_qualifiers = collapse_duplicate_qualifiers

    # -- leaves ----------------------------------------------------------------

    def builtin(self, spelling):
        return Spelling(spelling)

    def name(self, text):
        return Spelling(text)

    def raw(self, text):
        return Spelling(text)

    def literal(self, kind, value):
        return Spelling(value)

    def expression(self, form, parts):
        return Spelling("".join(part if isinstance(part, str) else self.spell(part) for part in parts))

    # -- composition -----------------------------------------------------------

    # List comprehensions rather than generator expressions in the joins below. A
    # generator is a frame that is resumed once per element -- 79,000 resumes over the
    # Itanium corpus for 23,000 `qualified` calls -- where a comprehension is one frame
    # for the whole list, and `join` has to build a sequence either way.

    def qualified(self, parts):
        return Spelling("::".join([part.left + part.right for part in parts]))

    def template(self, base, arguments, angle_space=True):
        rendered = ", ".join([argument.left + argument.right for argument in arguments])
        if self.legacy_angle_spacing and angle_space and rendered.endswith(">"):
            rendered += " "
        name = f"{base.left}{base.right}"
        opening = " <" if self.legacy_angle_spacing and name.endswith("<") else "<"
        return Spelling(f"{name}{opening}{rendered}>")

    def qualify(self, inner, qualifiers):
        if not qualifiers:
            return inner
        if inner.members is not None:
            return pack_of(self.qualify(member, qualifiers) for member in inner.members)
        if inner.is_function:
            # cv on a function type qualifies the implicit object parameter, so it
            # trails the parameter list rather than the return type.
            return Spelling(inner.left, inner.right + " " + " ".join(qualifiers), is_function=True)
        if not self.collapse_duplicate_qualifiers:
            # Both `int const` and `int* const` are "const applied to the thing on the
            # left", and C++ spells both postfix. The reference demanglers agree.
            left = inner.left + " " + " ".join(qualifiers)
            right = _respace_bound(left, inner.right) if inner.is_array else inner.right
            return Spelling(left, right, is_array=inner.is_array)
        # See `collapse_duplicate_qualifiers`. The ones already there that this does not
        # repeat keep their places; the ones it does repeat move to the end, because the
        # qualifier written outermost is the one c++filt prints last.
        stem = inner.stem if inner.cv else inner.left
        combined = tuple(q for q in inner.cv if q not in qualifiers) + tuple(qualifiers)
        left = stem + " " + " ".join(combined)
        right = _respace_bound(left, inner.right) if inner.is_array else inner.right
        return QualifiedSpelling(left, right, is_array=inner.is_array, cv=combined, stem=stem)

    # -- declarators -----------------------------------------------------------

    def pointer(self, inner):
        return _wrap(inner, "*")

    def parameter_pack(self, members):
        return pack_of(members)

    def reference(self, inner):
        # C++ reference collapsing ([dcl.ref]): applying `&` to any reference yields an
        # lvalue reference. `T& &`, `T&& &` and `T& &&` are all `T&`. This shows up in
        # every `std::forward` instantiation, where `O T_` is applied to a `T` already
        # bound to `char const&` and must not print `char const&&&`.
        if inner.members is not None:
            return pack_of(self.reference(member) for member in inner.members)
        if inner.ref_kind == "&":
            return inner
        if inner.ref_kind == "&&":
            return Spelling(inner.left[:-1], inner.right, ref_kind="&")
        return _wrap(inner, "&", ref_kind="&")

    def rvalue_reference(self, inner):
        # `T& &&` collapses to `T&`; only `T&& &&` stays an rvalue reference.
        if inner.members is not None:
            return pack_of(self.rvalue_reference(member) for member in inner.members)
        if inner.ref_kind:
            return inner
        return _wrap(inner, "&&", ref_kind="&&")

    def member_pointer(self, owner, inner):
        # `int Foo::*` needs the separating space that `int (Foo::*)()` does not: in the
        # function and array cases `_wrap` supplies an opening parenthesis instead.
        if owner.members is not None:
            # The owner is the one operand in this file that is not the type a
            # declarator is being applied to, and it packs the same way: a pointer to a
            # member of each of them.
            return pack_of(self.member_pointer(one, inner) for one in owner.members)
        if inner.members is not None:
            # Applying a declarator to a pack applies it to each member. `_wrap` has
            # always done this; the branch below is the one that reaches a pack, and
            # skipping it left ` ::*` printed for a pack with no members at all.
            return pack_of(self.member_pointer(owner, member) for member in inner.members)
        token = f"{owner}::*"
        if inner.is_function or inner.is_array:
            return _wrap(inner, token)
        # Always spaced, unlike a bare `*`. `int* A::*` needs the gap even though `int*`
        # does not, because `int*A::*` would read as one token; the references agree.
        left = inner.left
        joiner = "" if not left or left.endswith((" ", "(")) else " "
        return Spelling(left + joiner + token, inner.right)

    def array(self, inner, dimension):
        if inner.members is not None:
            # An array of a pack is one array per member, and of an empty pack is no
            # arrays: ` [3]` for a parameter that is not there was what this printed.
            return pack_of(self.array(member, dimension) for member in inner.members)
        if inner.cv:
            # An array carries its element type's qualifiers out with it: cv on an array
            # is cv on the elements, so `K A3_ K i` has one `const` and not two.
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
        # `Libcall const (&) [5][4]`, not `[5] [4]`. Dimensions are built inside out, so
        # the space the inner one took is the one this takes over.
        if inner.is_array:
            right = right.lstrip(" ")
        return Spelling(inner.left, _respace_bound(inner.left, bound + right), is_array=True)

    def function(self, returns, parameters, suffix="", name=None):
        rendered = ", ".join([parameter.left + parameter.right for parameter in parameters])
        tail = "(" + rendered + ")" + suffix
        if returns is None:
            result = Spelling("", tail, is_function=True)
        else:
            # A space after the return type, unless the return type is one that wraps
            # *around* the name -- a pointer to a function or to an array. Those spell
            # `int (*f())()`, with the name hard against the `*`, where an ordinary
            # pointer return spells `int* f()` with the space. The two are told apart by
            # whether the type has a right half to close: a plain `int*` has none.
            #
            # Written as an unconditional `+ " "`, this produced `int (* f<int>())()` and
            # was the largest group of wrong spellings against libcxxabi's corpus.
            left = returns.left
            joiner = "" if returns.right and left.endswith(("*", "&")) else " "
            result = Spelling(left + joiner, tail + returns.right, is_function=True)
        if name is None:
            return result
        return Spelling(result.spell(str(name)))

    def pack(self, inner):
        return Spelling(inner.left + "...", inner.right)

    def vendor_qualify(self, inner, qualifier):
        """A vendor qualifier goes after the *whole* type, declarator and all.

        The reference prints the type and then the extension, so a function type comes
        out `void () block_pointer` -- not `void block_pointer()`, which is what putting
        the word in the left half alone gives for anything that has a right half.

        Distributes over a pack like every other declarator, which it did not: an empty
        pack came out as a bare ` enable_if`, a qualifier on nothing.
        """
        if inner.members is not None:
            return pack_of(self.vendor_qualify(member, qualifier) for member in inner.members)
        return Spelling(inner.spell() + " " + qualifier)

    # -- whole symbols ---------------------------------------------------------

    def special(self, label, inner):
        return Spelling(label + str(inner))

    def decorated(self, inner, decoration):
        return Spelling(inner.spell() + describe(decoration, self.gnu_clone_suffix))

    # -- inspection ------------------------------------------------------------

    def spell(self, handle, declarator=""):
        return handle.spell(declarator)

    def size(self, handle):
        # Both halves are already built, so their lengths are free.
        return len(handle.left) + len(handle.right)

    def members(self, handle):
        """The members of a parameter pack, or None if this is not one."""
        return handle.members


#: Shared instances. Both are immutable after construction, so one of each serves every
#: call rather than being allocated per name. Parsers take a builder argument rather
#: than reaching for these; the API layer selects one per requested style.
SPELLING_BUILDER = SpellingBuilder()
LEGACY_SPELLING_BUILDER = SpellingBuilder(
    legacy_angle_spacing=True, gnu_clone_suffix=True, collapse_duplicate_qualifiers=True
)
