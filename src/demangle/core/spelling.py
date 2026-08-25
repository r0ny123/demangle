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

#: Characters after which a declarator needs no separating space: `int*x` reads fine,
#: `intx` does not.
_TIGHT_ENDINGS = ("*", "&", "(", " ", ":")


class Spelling:
    """One type or name, split around its declarator position."""

    __slots__ = ("is_array", "is_function", "left", "ref_kind", "right")

    def __init__(self, left, right="", is_function=False, is_array=False, ref_kind=""):
        self.left = left
        self.right = right
        self.is_function = is_function
        self.is_array = is_array
        #: "", "&" or "&&". Tracked so reference collapsing can be applied.
        self.ref_kind = ref_kind

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


def _wrap(inner, token):
    """Apply a declarator token, parenthesising where precedence demands it.

    Without the parentheses `int (*)(char)` would read as `int *(char)`: a function
    returning a pointer, which is a different type. The rule is that a declarator
    binding tighter than the one already applied needs grouping, and function and array
    types are precisely the cases where one already has been.
    """
    if inner.is_function or inner.is_array:
        left = inner.left
        # A function's left half already ends in the space after its return type; an
        # array's ends in an identifier character and needs one added.
        spacer = "" if not left or left.endswith((" ", "(")) else " "
        return Spelling(left + spacer + "(" + token, ")" + inner.right)
    return Spelling(inner.left + token, inner.right)


class SpellingBuilder(Builder):
    """Builds C++ declaration text. The backend behind `demangle()`.

    Stateless, so one instance is shared by every parse rather than allocated per name.
    """

    __slots__ = ("legacy_angle_spacing",)

    def __init__(self, legacy_angle_spacing=False):
        #: Write `Foo<Bar<int> >` rather than `Foo<Bar<int>>`. Required before C++11,
        #: when `>>` at the end of a template-id lexed as a right-shift operator. GNU
        #: c++filt still prints it; llvm-cxxfilt does not. Neither is wrong.
        self.legacy_angle_spacing = legacy_angle_spacing

    # -- leaves ----------------------------------------------------------------

    def builtin(self, spelling):
        return Spelling(spelling)

    def name(self, text):
        return Spelling(text)

    def raw(self, text):
        return Spelling(text)

    def literal(self, kind, value):
        return Spelling(value)

    # -- composition -----------------------------------------------------------

    def qualified(self, parts):
        return Spelling("::".join(part.left + part.right for part in parts))

    def template(self, base, arguments):
        rendered = ", ".join(str(argument) for argument in arguments)
        if self.legacy_angle_spacing and rendered.endswith(">"):
            rendered += " "
        return Spelling(f"{base.left}{base.right}<{rendered}>")

    def qualify(self, inner, qualifiers):
        if not qualifiers:
            return inner
        text = " ".join(qualifiers)
        if inner.is_function:
            # cv on a function type qualifies the implicit object parameter, so it
            # trails the parameter list rather than the return type.
            return Spelling(inner.left, inner.right + " " + text, is_function=True)
        # Both `int const` and `int* const` are "const applied to the thing on the
        # left", and C++ spells both postfix. The reference demanglers agree.
        return Spelling(inner.left + " " + text, inner.right, is_array=inner.is_array)

    # -- declarators -----------------------------------------------------------

    def pointer(self, inner):
        return _wrap(inner, "*")

    def reference(self, inner):
        # C++ reference collapsing ([dcl.ref]): applying `&` to any reference yields an
        # lvalue reference. `T& &`, `T&& &` and `T& &&` are all `T&`. This shows up in
        # every `std::forward` instantiation, where `O T_` is applied to a `T` already
        # bound to `char const&` and must not print `char const&&&`.
        if inner.ref_kind == "&":
            return inner
        if inner.ref_kind == "&&":
            return Spelling(inner.left[:-1], inner.right, ref_kind="&")
        result = _wrap(inner, "&")
        result.ref_kind = "&"
        return result

    def rvalue_reference(self, inner):
        # `T& &&` collapses to `T&`; only `T&& &&` stays an rvalue reference.
        if inner.ref_kind:
            return inner
        result = _wrap(inner, "&&")
        result.ref_kind = "&&"
        return result

    def member_pointer(self, owner, inner):
        # `int Foo::*` needs the separating space that `int (Foo::*)()` does not: in the
        # function and array cases `_wrap` supplies an opening parenthesis instead.
        token = f"{owner}::*"
        if inner.is_function or inner.is_array:
            return _wrap(inner, token)
        left = inner.left
        joiner = "" if not left or left.endswith(_TIGHT_ENDINGS) else " "
        return Spelling(left + joiner + token, inner.right)

    def array(self, inner, dimension):
        bound = f" [{dimension}]" if dimension else " []"
        return Spelling(inner.left, bound + inner.right, is_array=True)

    def function(self, returns, parameters, suffix="", name=None):
        rendered = ", ".join(str(parameter) for parameter in parameters)
        tail = "(" + rendered + ")" + suffix
        if returns is None:
            result = Spelling("", tail, is_function=True)
        else:
            result = Spelling(returns.left + " ", tail + returns.right, is_function=True)
        if name is None:
            return result
        return Spelling(result.spell(str(name)))

    def pack(self, inner):
        return Spelling(inner.left + "...", inner.right)

    def vendor_qualify(self, inner, qualifier):
        return Spelling(inner.left + " " + qualifier, inner.right)

    # -- whole symbols ---------------------------------------------------------

    def special(self, label, inner):
        return Spelling(label + str(inner))

    # -- inspection ------------------------------------------------------------

    def spell(self, handle, declarator=""):
        return handle.spell(declarator)


#: Shared instances. Both are immutable after construction, so one of each serves every
#: call rather than being allocated per name. Parsers take a builder argument rather
#: than reaching for these; the API layer selects one per requested style.
SPELLING_BUILDER = SpellingBuilder()
LEGACY_SPELLING_BUILDER = SpellingBuilder(legacy_angle_spacing=True)
