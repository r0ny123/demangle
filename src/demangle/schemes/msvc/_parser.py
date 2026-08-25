"""Demangling for MSVC decorated symbol names."""

import string
from functools import lru_cache

from .nodes import (
    Array,
    Declaration,
    FunctionType,
    Indirection,
    Name,
    Raw,
    apply_qualifiers,
    is_member_function_pointer,
    merge_qualifiers,
    prefixed,
    qualify_declared,
    render,
)

_BASIC_TYPES = {
    "X": "void",
    "D": "char",
    "C": "signed char",
    "E": "unsigned char",
    "F": "short",
    "G": "unsigned short",
    "H": "int",
    "I": "unsigned int",
    "J": "long",
    "K": "unsigned long",
    "M": "float",
    "N": "double",
    "O": "long double",
}
_EXTENDED_TYPES = {
    "J": "__int64",
    "K": "unsigned __int64",
    "L": "__int128",
    "M": "unsigned __int128",
    "N": "bool",
    "Q": "char8_t",
    "S": "char16_t",
    "U": "char32_t",
    "W": "wchar_t",
}
_TAGGED_TYPES = {"T": "union", "U": "struct", "V": "class", "W": "enum"}
_CALLING_CONVENTIONS = {
    "A": "__cdecl",
    "B": "__cdecl",
    "C": "__pascal",
    "D": "__pascal",
    "E": "__thiscall",
    "F": "__thiscall",
    "G": "__stdcall",
    "H": "__stdcall",
    "I": "__fastcall",
    "J": "__fastcall",
    "M": "__clrcall",
    "N": "__clrcall",
    "O": "__eabi",
    "P": "__eabi",
    "Q": "__vectorcall",
    "S": "__attribute__((__swiftcall__))",
    "W": "__attribute__((__swiftasynccall__))",
    # the remaining letters are conventions the reference spells with nothing at all
    "K": "",
    "L": "",
    "R": "",
    "T": "",
    "U": "",
    "V": "",
    "X": "",
    "Y": "",
    "Z": "",
}
# a thunk stands in for a member function and adjusts "this" on the way through; the
# reference prefixes the whole spelling and marks the name with the adjustment
_ADJUSTOR_ACCESS = {
    "G": ("private", False, False),
    "H": ("private", False, False),
    "O": ("protected", False, True),
    "P": ("protected", False, True),
    "W": ("public", False, True),
    "X": ("public", False, True),
}
# a thunk that adjusts through a virtual base writes two displacements after its access
_VTORDISP_ACCESS = {"0": "private", "2": "protected", "4": "public"}
_FUNCTION_ACCESS = {
    "A": ("private", False, False),
    "B": ("private", False, False),
    "C": ("private", True, False),
    "D": ("private", True, False),
    "E": ("private", False, True),
    "F": ("private", False, True),
    "I": ("protected", False, False),
    "J": ("protected", False, False),
    "K": ("protected", True, False),
    "L": ("protected", True, False),
    "M": ("protected", False, True),
    "N": ("protected", False, True),
    "Q": ("public", False, False),
    "R": ("public", False, False),
    "S": ("public", True, False),
    "T": ("public", True, False),
    "U": ("public", False, True),
    "V": ("public", False, True),
}
_OPERATORS = {
    "2": "operator new",
    "3": "operator delete",
    "4": "operator=",
    "5": "operator>>",
    "6": "operator<<",
    "7": "operator!",
    "8": "operator==",
    "9": "operator!=",
    "A": "operator[]",
    "C": "operator->",
    "D": "operator*",
    "E": "operator++",
    "F": "operator--",
    "G": "operator-",
    "H": "operator+",
    "I": "operator&",
    "J": "operator->*",
    "K": "operator/",
    "L": "operator%",
    "M": "operator<",
    "N": "operator<=",
    "O": "operator>",
    "P": "operator>=",
    "Q": "operator,",
    "R": "operator()",
    "S": "operator~",
    "T": "operator^",
    "U": "operator|",
    "V": "operator&&",
    "W": "operator||",
    "X": "operator*=",
    "Y": "operator+=",
    "Z": "operator-=",
}
# the special names spelled with the "6" storage form. The vcall, typeof and local static
# guard codes are data too, but take a storage class this parser does not model, so they
# decline instead of being read as one of these
# what runs around a namespace-scope object with a non-trivial lifetime
_DYNAMIC_INITIALISERS = {"E": "dynamic initializer for", "F": "dynamic atexit destructor for"}
# what guards a function-local static, and the storage class both forms are written with
_GUARDS = {"B": "`local static guard'", "__J": "`local static thread guard'"}
# what a string literal's escapes stand for, and how the reference spells a byte back
_LITERAL_ESCAPES = {
    "0": ",",
    "1": "/",
    "2": "\\",
    "3": ":",
    "4": ".",
    "5": " ",
    "6": "\n",
    "7": "\t",
    "8": "'",
    "9": "-",
}
_LITERAL_SPELLINGS = {"\\": "\\\\", "\n": "\\n", "\t": "\\t", "'": "\\'", "\0": "\\0", '"': '\\"'}
_RTTI_NAMES = {
    "2": "`RTTI Base Class Array'",
    "3": "`RTTI Class Hierarchy Descriptor'",
    "4": "`RTTI Complete Object Locator'",
}
_DATA_SPECIAL_OPERATORS = frozenset("78S")
_UNMODELLED_DATA_SPECIAL_OPERATORS = frozenset("A")
_EXTENDED_OPERATORS = {
    "0": "operator/=",
    "1": "operator%=",
    "2": "operator>>=",
    "3": "operator<<=",
    "4": "operator&=",
    "5": "operator|=",
    "6": "operator^=",
    "7": "`vftable'",
    "8": "`vbtable'",
    "9": "`vcall'",
    "A": "`typeof'",
    "B": "`local static guard'",
    "D": "`vbase dtor'",
    "E": "`vector deleting dtor'",
    "F": "`default ctor closure'",
    "G": "`scalar deleting dtor'",
    "H": "`vector ctor iterator'",
    "I": "`vector dtor iterator'",
    "J": "`vector vbase ctor iterator'",
    "K": "`virtual displacement map'",
    "L": "`eh vector ctor iterator'",
    "M": "`eh vector dtor iterator'",
    "N": "`eh vector vbase ctor iterator'",
    "O": "`copy ctor closure'",
    "S": "`local vftable'",
    "T": "`local vftable ctor closure'",
    "U": "operator new[]",
    "V": "operator delete[]",
    "X": "`placement delete closure'",
    "Y": "`placement delete[] closure'",
}
_DATA_ACCESS = {
    "0": "private: static ",
    "1": "protected: static ",
    "2": "public: static ",
    "3": "",
    "4": "",
}
_POINTER_KINDS = {"P": (), "Q": ("const",), "R": ("volatile",), "S": ("const", "volatile")}
# what stands where a pointee's cv would, when the pointer points into a class instead
_MEMBER_DATA_QUALS = {"Q": (), "R": ("const",), "S": ("volatile",), "T": ("const", "volatile")}
_CV_QUALS = {"A": (), "B": ("const",), "C": ("volatile",), "D": ("const", "volatile")}
_CV = {"A": "", "B": " const", "C": " volatile", "D": " const volatile"}


class _Conversion:
    """A conversion operator, whose name is the type it converts to.

    That type is written in the return slot, which is read long after the name, so the
    spelling is finished once the signature has been.
    """


class _Structor:
    """A constructor or destructor: its spelling comes from the class it belongs to.

    It may itself be a template, in which case the arguments follow the class name it
    borrows: "??$?0N@?$Foo@H@@QEAA@N@Z" is Foo<int>::Foo<int><double>.
    """

    def __init__(self, is_destructor, arguments=""):
        self.is_destructor = is_destructor
        self.arguments = arguments


class _Bail(Exception):
    """The name is not one this demangler fully understands."""


class _Demangler:
    """A cursor over one decorated name.

    MAX_DEPTH bounds the mutually recursive name and type parser. A level costs several
    interpreter frames here, so the bound is set low enough that CPython's own recursion
    limit is never the thing that stops a parse - otherwise the answer would depend on how
    deep the caller already is. max_render bounds the rendered result, which back-reference
    reuse can otherwise grow multiplicatively.
    """

    MAX_DEPTH = 64

    def __init__(self, mangled):
        self.text = mangled
        self.pos = 0
        self.name_backrefs = []
        self.arg_backrefs = []
        self.simple = True
        self.template_depth = 0
        self.at_symbol_name = True
        self.requires_signature = False
        self.nested = False
        self.pointee_depth = 0
        self.array_element_depth = 0
        # set only while the next type read stands directly as a template argument
        self.at_argument = False
        self.member_cv = ""
        self.depth = 0
        self.max_render = 8 * len(mangled) + 256

    def eof(self):
        return self.pos >= len(self.text)

    def peek(self):
        if self.eof():
            raise _Bail
        return self.text[self.pos]

    def take(self):
        char = self.peek()
        self.pos += 1
        return char

    def eat(self, char):
        if not self.eof() and self.text[self.pos] == char:
            self.pos += 1
            return True
        return False

    def expect(self, char):
        if not self.eat(char):
            raise _Bail

    def identifier(self):
        end = self.text.find("@", self.pos)
        if end < 0:
            raise _Bail
        name = self.text[self.pos : end]
        if not name:
            raise _Bail
        self.pos = end + 1
        return name

    def rememberName(self, name):
        """Record a name for later back-references, the way the mangler recorded it.

        A name is added only when it is not already held and while the table is under ten
        entries. Both rules move the indices every later back-reference resolves against, so
        appending unconditionally does not merely miss a compression - it reads "?3" as the
        fourth name where the mangler counted three, and answers a name the grammar refuses.
        """
        if name not in self.name_backrefs and len(self.name_backrefs) < 10:
            self.name_backrefs.append(name)

    def templateInstantiation(self, operator=None):
        """A "?$" template name, read in its own back-reference scope.

        The name is usually an identifier, and is recorded inside the fresh scope; when it
        is an operator instead the caller has already read it, and there is nothing to
        record - "??$?HH@S@@QEAAAEAU0@H@Z" is S::operator+<int>.

        The scope opens before the template's own name does, so that name takes index 0
        inside it and the arguments are numbered from 1 - which is what a back-reference
        written inside the argument list resolves against. The rendered result belongs to
        the enclosing scope instead, and the caller records it there.
        """
        saved_names, saved_args = self.name_backrefs, self.arg_backrefs
        self.name_backrefs, self.arg_backrefs = [], []
        self.template_depth += 1
        try:
            if operator is None:
                base = self.identifier()
                self.rememberName(base)
                if self.eof() or self.peek() == "@":
                    raise _Bail
            else:
                base = operator
            args = []
            while not self.eat("@"):
                if self.eof():
                    raise _Bail
                if self.text.startswith("$S", self.pos):
                    # a third spelling of the empty pack
                    self.pos += 2
                    continue
                if self.text.startswith("$$V", self.pos) and not self.text.startswith("$$$V", self.pos):
                    # the other spelling of an empty pack
                    self.pos += 3
                    continue
                if self.text.startswith("$$$V", self.pos):
                    # an empty pack: "f<>" has an argument list and no arguments in it
                    self.pos += 4
                    continue
                if self.text.startswith("$$Z", self.pos):
                    # a pack separator, which stands between arguments and is not one
                    self.pos += 3
                    continue
                self.at_argument = True
                args.append(self.rendered(self.type()))
            return f"{base}<{', '.join(args)}>"
        finally:
            self.template_depth -= 1
            self.name_backrefs, self.arg_backrefs = saved_names, saved_args

    def nameFragment(self, is_leading):
        """One fragment, paired with which spelling its special-name code takes, if any.

        The caller needs that apart from the spelling: a tag type is always named by an
        identifier, and a special name takes either a signature or a storage class by
        which code it is, never both.
        """
        # every later qualified name belongs to a type, and the exception below is only for
        # the symbol's own name, so the very first leading fragment is the one that counts
        is_symbol_name = is_leading and self.at_symbol_name
        if is_leading:
            self.at_symbol_name = False
        char = self.peek()
        if char in string.digits:
            index = int(self.take())
            if index >= len(self.name_backrefs):
                raise _Bail
            return self.name_backrefs[index], None
        if char == "?":
            self.take()
            if self.eat("$"):
                if self.peek() in string.digits:
                    raise _Bail
                operator = None
                if self.peek() == "?":
                    # a template whose name is an operator: "??$?HH@S@@" is operator+<int>
                    self.take()
                    operator, _ = self.operatorName()
                    if isinstance(operator, _Structor):
                        # a constructor may be a template too, and its arguments follow the
                        # class name it borrows rather than replacing it
                        arguments = self.templateInstantiation(operator="")
                        return _Structor(operator.is_destructor, arguments), "func"
                    if not isinstance(operator, str):
                        raise _Bail
                rendered = self.templateInstantiation(operator=operator)
                if not is_symbol_name:
                    # the symbol's own template name is the one exception the mangler makes:
                    # it is not recorded, so "??$f@H@N@@YAXV0@@Z" resolves 0 to N, not to
                    # f<int>. A template met anywhere else is recorded like any other name.
                    self.rememberName(rendered)
                return rendered, "func" if operator else None
            if is_leading:
                # "??A" here is operator[], not the namespace below: the leading fragment is
                # the symbol's own name, and a namespace can only qualify it
                return self.operatorName()
            if self.peek() == "A":
                return self.anonymousNamespace(), None
            # a scope number is written the way a template argument's is, so it may be
            # nibbles too; "A" is not among them because "?A" is the namespace above
            if self.peek() in string.digits or self.peek() in "@BCDEFGHIJKLMNOP":
                return self.localScope(), None
            raise _Bail
        name = self.identifier()
        self.rememberName(name)
        return name, None

    def localScope(self):
        """A scope inside a function: the function's own name, and which scope of it.

        "?1??f@@YAXXZ@" is the second scope of "void __cdecl f(void)". The enclosing name is a complete decorated name in its
        own right and is read as one, in its own back-reference scopes - which is why it is
        parsed by a separate cursor over the same text rather than inline.
        """
        # the scope carries the number a template argument does - a digit standing for itself
        # plus one, nibbles ended by "@" standing for themselves - except that a bare "@" is
        # the scope spelled zero
        spelled = "0" if self.eat("@") else self.templateInteger()
        self.expect("?")
        return f"`{self.nestedSymbol()}'::`{spelled}'"

    def namesADataSymbol(self):
        """Whether what follows is a data symbol rather than a function, without reading it.

        Only the storage class tells the two apart, and it comes after the whole name, so
        this walks a throwaway cursor to it and reports what it found.
        """
        probe = _Demangler("?" + self.text[self.pos :])
        probe.nested = True
        try:
            probe.expect("?")
            probe.qualifiedName()
        except (_Bail, RecursionError):
            return False
        return not probe.eof() and probe.peek() in _DATA_ACCESS

    def nestedSymbol(self, leading_question=True):
        """A complete decorated name written inside another one.

        It continues this name's back-reference table rather than opening its own, so
        "?N@?1??SN@?$NS@H@0@QEAAHXZ@4HA" resolves its 0 to the outer N and
        "??$f@VBar@@$1?x@0@3HA@@YAXXZ" resolves its 0 to the template's own f. It is still a
        symbol, so its own leading template is not recorded either, and it ends where it
        ends rather than at the end of the text.
        """
        if leading_question:
            inner = _Demangler(self.text)
            inner.pos = self.pos
        else:
            # the "?" this name would open with was spent on the code that introduced it,
            # so it is read over a copy that has one
            inner = _Demangler("?" + self.text[self.pos :])
        inner.nested = True
        inner.name_backrefs = self.name_backrefs
        inner.arg_backrefs = self.arg_backrefs
        inner.at_symbol_name = True
        inner.depth = self.depth
        rendered = render(inner.parse())
        self.pos = inner.pos if leading_question else self.pos + inner.pos - 1
        return rendered

    def anonymousNamespace(self):
        """The unnamed namespace of one translation unit: "?A" and an optional discriminator.

        The discriminator tells two of them apart inside one binary, and the reference does
        not spell it, so two anonymous namespaces render alike - which is what C++ source
        looks like too.
        """
        self.expect("A")
        start = self.pos
        if self.eat("0"):
            if not self.eat("x"):
                raise _Bail
            digits = 0
            while not self.eof() and self.peek() in string.hexdigits:
                self.take()
                digits += 1
            if not digits:
                raise _Bail
        # the discriminator, not the spelling, is what a later back-reference resolves to:
        # "?f@?A0x1@@YAXV1@@Z" names its parameter "class 0x1"
        self.rememberName(self.text[start : self.pos])
        self.expect("@")
        return "`anonymous namespace'"

    def operatorName(self):
        """The operator or special name, paired with which spelling it takes."""
        if self.eat("_"):
            if self.eat("_"):
                code = self.peek()
                if code == "J":
                    self.take()
                    return _GUARDS["__J"], "guard"
                if code in _DYNAMIC_INITIALISERS:
                    self.take()
                    self.requires_signature = True
                    if self.peek() == "?":
                        # it may run for a whole symbol of its own, scopes and storage and
                        # all: "??__E?i@C@@0HA@@YAXXZ" runs for "private: static int C::i"
                        target = self.nestedSymbol()
                        # the symbol it runs for is followed by the terminator its own name
                        # would have carried, and the enclosing name still needs one
                        self.expect("@")
                        return f"`{_DYNAMIC_INITIALISERS[code]} `{target}''", "func"
                    if self.namesADataSymbol():
                        # it may run for a data symbol written without its leading "?", the
                        # rest reading as it does for one that has it
                        whole = self.nestedSymbol(leading_question=False)
                        # this one leaves the single terminator the enclosing name needs
                        return f"`{_DYNAMIC_INITIALISERS[code]} `{whole}''", "func"
                    # what it runs for is recorded, unlike a literal operator's suffix:
                    # "??__EFoo@@YAXU0@@Z" resolves its 0 to Foo
                    if self.peek().isdigit():
                        # a digit there stands for an earlier name, and there is none
                        raise _Bail
                    target = self.identifier()
                    if not self.eat("@"):
                        # a plain name may still be qualified, and the whole of that name
                        # belongs inside the quotes rather than around them
                        raise _Bail
                    self.pos -= 1
                    self.rememberName(target)
                    return f"`{_DYNAMIC_INITIALISERS[code]} '{target}''", "func"
                if self.take() != "K":
                    raise _Bail
                # a user-defined literal: the identifier after the code is its suffix, and
                # the reference spells the pair as operator ""suffix
                return f'operator ""{self.identifier()}', "func"
            code = self.take()
            if code == "C":
                return self.stringLiteral(), "descriptor"
            if code == "R" and self.peek() in "1234":
                # the rest of the RTTI family names a class rather than a type, and each
                # is written with its own storage: "8" for these three, the vftable form
                # for the locator. The descriptor carries where the base sits.
                which = self.take()
                if which == "1":
                    written = [self.templateInteger() for _ in range(4)]
                    # where the base sits may be negative, but how far the table reaches and
                    # what it is flagged with may not
                    if any(value.startswith("-") for value in written[2:]):
                        raise _Bail
                    at = ", ".join(str(int(value)) for value in written)
                    return f"`RTTI Base Class Descriptor at ({at})'", "rtti"
                return _RTTI_NAMES[which], "data" if which == "4" else "rtti"
            if code == "R" and self.peek() == "0":
                # a type descriptor names the type it describes rather than a function
                self.take()
                described = self.rendered(self.returnType())
                self.expect("@")
                self.expect("8")
                if not self.nested and not self.eof():
                    raise _Bail
                # the marker abuts a type that already ends in a sigil, as a declarator does
                separator = "" if described.endswith(("*", "&")) else " "
                return f"{described}{separator}`RTTI Type Descriptor'", "descriptor"
            name = _EXTENDED_OPERATORS.get(code)
            if name is None:
                raise _Bail
            if code in _UNMODELLED_DATA_SPECIAL_OPERATORS:
                raise _Bail
            if code == "9":
                # it dispatches through a slot, so it is spelled with that and never with
                # storage: "??_9Base@@3QAHA" is not a name
                self.requires_signature = True
                return name, "vcall"
            if code == "B":
                return name, "guard"
            return name, "data" if code in _DATA_SPECIAL_OPERATORS else "func"
        if self.eat("@"):
            # a name the compiler replaced with a hash of itself; whatever follows the hash
            # is not part of it
            digest = []
            while not self.eat("@"):
                digest.append(self.take())
            # a further decorated name after the hash belongs to it; anything else does not
            suffix = self.text[self.pos :] if self.text.startswith("??", self.pos) else ""
            self.pos = len(self.text)
            return f"??@{''.join(digest)}@{suffix}", "descriptor"
        code = self.take()
        if code in ("0", "1"):
            return _Structor(code == "1"), "func"
        if code == "B":
            # what it converts to is read from the return slot, so it is spelled with a
            # signature and never with storage
            self.requires_signature = True
            return _Conversion(), "func"
        name = _OPERATORS.get(code)
        if name is None:
            raise _Bail
        return name, "func"

    def qualifiedName(self):
        """Count a name level against the depth bound; type() is what enforces it."""
        self.depth += 1
        try:
            return self.qualifiedNameBody()
        finally:
            self.depth -= 1

    def qualifiedNameBody(self):
        first, special_form = self.nameFragment(True)
        if special_form == "descriptor":
            # a type descriptor has read the whole name, the type it describes included
            return first, False, special_form
        scopes = []
        while True:
            if self.eat("@"):
                break
            if self.eof():
                raise _Bail
            scopes.append(self.nameFragment(False)[0])
        scopes.reverse()
        if isinstance(first, _Conversion):
            return "::".join(scopes + ["\0conversion\0"]), False, special_form
        if isinstance(first, _Structor):
            if not scopes:
                raise _Bail
            klass = scopes[-1]
            spelled = klass + first.arguments
            first = "~" + spelled if first.is_destructor else spelled
            return "::".join(scopes + [first]), True, special_form
        return "::".join(scopes + [first]), False, special_form

    def type(self, quals=()):
        self.depth += 1
        if self.depth > self.MAX_DEPTH:
            raise _Bail
        at_argument, self.at_argument = self.at_argument, False
        try:
            return self.typeBody(quals, at_argument)
        finally:
            self.depth -= 1

    def typeBody(self, quals, at_argument=False):
        """One type, qualified by `quals`.

        A digit is a back-reference standing for a whole argument type, so it is only a type
        where a whole argument is one. A qualifier in front of it, or a pointer or reference
        around it - "?h@@YAXPAHPA0@Z" - is a name the mangler cannot have produced, and
        reading one invented a spelling for it.
        """
        char = self.take()
        if char in _BASIC_TYPES:
            return apply_qualifiers(Raw(_BASIC_TYPES[char]), quals)
        if char == "_":
            name = _EXTENDED_TYPES.get(self.take())
            if name is None:
                raise _Bail
            self.simple = False
            return apply_qualifiers(Raw(name), quals)
        if char in _TAGGED_TYPES:
            kind = _TAGGED_TYPES[char]
            if kind == "enum":
                self.expect("4")
            name, _, special_form = self.qualifiedName()
            if special_form is not None:
                raise _Bail
            self.simple = False
            return apply_qualifiers(Raw(f"{kind} {name}"), quals)
        if char == "Y":
            return self.arrayType(quals)
        if char in _POINTER_KINDS:
            return self.indirection(merge_qualifiers(_POINTER_KINDS[char], quals), "*")
        if char == "A":
            if quals:
                raise _Bail
            # only "A" introduces a reference. "B" would be a volatile-qualified one, which
            # C++ has no way to write and no Microsoft-compatible mangler emits, so reading
            # it produced answers for names that cannot exist: "?f2@@YAXBDPAD@Z".
            return self.indirection((), "&")
        if char == "$":
            return self.dollarType(quals, at_argument)
        if char in string.digits:
            # a digit is an argument back-reference, which stands for a whole argument and is
            # read as one in parameters(). Reaching it here means it was written where only a
            # type belongs - a pointee, a template argument, a return type - and the mangler
            # writes none of those; reading them invented spellings for impossible names.
            raise _Bail
        if char == "?" and self.peek() == "<":
            # a placeholder the compiler writes where a type would go, named in brackets:
            # "?A?<decltype-auto>@@" is the deduced return of a function declared with it
            placeholder = self.identifier()
            self.expect("@")
            return apply_qualifiers(Raw(placeholder), quals)
        raise _Bail

    def rendered(self, node, declarator=""):
        text = render(node, declarator)
        if len(text) > self.max_render:
            raise _Bail
        return text

    def dimension(self):
        """A count or an extent, written the way a template argument's number is."""
        spelled = self.templateInteger()
        if spelled.startswith("-"):
            raise _Bail
        return int(spelled)

    def arrayType(self, quals):
        count = self.dimension()
        if count == 0:
            raise _Bail
        # an extent of nothing is spelled with nothing: "$$BY0A@H" is "int[]"
        extents = [self.dimension() for _ in range(count)]
        self.array_element_depth += 1
        try:
            element = self.type(quals)
        finally:
            self.array_element_depth -= 1
        self.simple = False
        # the extents are written as one run and spelled as one run, but each is a dimension
        # in its own right - "int[2][3]" is two arrays of three - so they nest rather than
        # sitting in a single node as text
        for extent in reversed(extents):
            element = Array(element, str(extent) if extent else "")
        return element

    def templateInteger(self):
        """The integer a "$0" template argument carries.

        A single digit stands for itself plus one, so "$00" is 1. Anything larger is spelled
        as nibbles from "A" to "P" terminated by "@", most significant first, and a leading
        "?" negates it: "$0M@" is 12 and "$0?0" is -1.

        The accumulator is 64 bits wide and wraps, which is visible in the corpus: the
        eighteen nibbles of "$0HPPPPPPPPPPPPPPPPPP@" are 18446744073709551615, and a
        magnitude that wraps to zero under a minus sign is spelled "-0".
        """
        negative = self.eat("?")
        char = self.peek()
        if char in string.digits:
            value = int(self.take()) + 1
        else:
            value = 0
            digits = 0
            while not self.eof() and "A" <= self.peek() <= "P":
                value = (value * 16 + (ord(self.take()) - ord("A"))) & 0xFFFFFFFFFFFFFFFF
                digits += 1
            if not digits:
                raise _Bail
            self.expect("@")
        return f"-{value}" if negative else str(value)

    def dollarType(self, quals, at_argument=False):
        if self.peek() in ("1", "E") and self.template_depth:
            # the address of a symbol, or the symbol itself: what follows is a complete
            # decorated name, read in this template's back-reference scope - which is why
            # "??$f@VBar@@$1?x@0@3HA@@YAXXZ" resolves its 0 to f rather than to Bar
            prefix = "&" if self.take() == "1" else ""
            self.simple = False
            return Raw(prefix + self.nestedSymbol())
        if self.peek() == "0":
            if not at_argument:
                # an integer is an argument, not a type: it stands where an argument stands
                # and nowhere a type may nest, so it is not an array's element either
                raise _Bail
            self.take()
            self.simple = False
            return Raw(self.templateInteger())
        if not self.eat("$"):
            raise _Bail
        if self.eat("B"):
            # the type as written rather than as a parameter would decay it, which is a thing
            # to say only where a parameter stands: "?f@@YAX$$BY01H@Z" is not a name. An
            # integer is an argument rather than a type, and a function type is never written
            # this way either
            if self.peek() in "$6" or not at_argument:
                raise _Bail
            return self.type(quals)
        if self.eat("Y"):
            # an alias template is named rather than described
            self.simple = False
            return Raw(self.qualifiedName()[0])
        kind = self.take()
        if kind == "Q":
            return self.indirection(quals, "&&")
        if kind == "C":
            if not (self.template_depth or self.array_element_depth):
                # "$$C" qualifies an array element or a template argument. As a parameter of
                # its own, or as the pointee of a pointer or reference, it is not a type:
                # "?f@@YAX$$CBH@Z" and "?f@@YAXPA$$CBH@Z" are not names
                raise _Bail
            extra = _CV_QUALS.get(self.take())
            if extra is None:
                raise _Bail
            return self.type(merge_qualifiers(extra, quals))
        if kind == "T":
            self.simple = False
            return apply_qualifiers(Raw("std::nullptr_t"), quals)
        if kind == "A":
            return self.functionTypeArgument()
        raise _Bail

    def returnType(self):
        """A return type, which unlike any other position may carry a qualifier of its own.

        Every function returning a class by value is spelled this way, so the prefix is
        ordinary rather than exotic: `?A` is the unqualified case, not an absent one.
        """
        quals = ()
        if self.eat("?"):
            quals = _CV_QUALS.get(self.take())
            if quals is None:
                raise _Bail
        return self.type(quals)

    def indirection(self, own_quals, token):
        """A pointer or reference: `token` plus its own quals, over a qualified pointee."""
        has_ptr64 = self.eat("E")
        if self.eat("I"):
            own_quals = own_quals + ("__restrict",)
        # "__unaligned" qualifies what the pointer points at, and is spelled after the
        # pointee's own const and volatile: "int const __unaligned *"
        unaligned = ("__unaligned",) if self.eat("F") else ()
        modified = has_ptr64 or unaligned or "__restrict" in own_quals
        if self.eat("8"):
            if modified:
                # nothing is pointed at in front of a function type, so no modifier stands
                # there: "P8B@@" and "R8B@@" are names, "PE8B@@" and "RF8B@@" are not
                raise _Bail
            return self.memberFunctionPointer(own_quals, token)
        if token == "*" and self.peek() in _MEMBER_DATA_QUALS:
            # only a pointer points into a class; C++ has no reference to member, so "AT..."
            # is not a name however much it looks like one
            return self.memberDataPointer(own_quals, token, unaligned)
        if self.eat("6"):
            if modified:
                raise _Bail
            convention = _CALLING_CONVENTIONS.get(self.take())
            if convention is None:
                raise _Bail
            returns = self.returnType()
            # a parameter of this function type is a whole-argument position again, so a
            # back-reference is legal there even when the function type is itself a pointee
            saved_pointee_depth = self.pointee_depth
            self.pointee_depth = 0
            try:
                params = self.parameters()
            finally:
                self.pointee_depth = saved_pointee_depth
            self.expect("Z")
            self.simple = False
            return Indirection(token, own_quals, FunctionType(convention, params, returns))
        pointee_quals = _CV_QUALS.get(self.take())
        if pointee_quals is None:
            raise _Bail
        pointee_quals = pointee_quals + unaligned
        self.pointee_depth += 1
        try:
            pointee = self.type(pointee_quals)
        finally:
            self.pointee_depth -= 1
        self.simple = False
        return Indirection(token, own_quals, pointee)

    def memberDataPointer(self, own_quals, token, unaligned=()):
        """A pointer to data member: the class qualifies the declarator, as it does a method.

        The code standing where a pointee's cv would be says both that this points into a
        class and what the member itself is qualified by, so "PRfoo@@D" is
        "char const foo::*".
        """
        member_quals = _MEMBER_DATA_QUALS[self.take()] + unaligned
        owner = self.qualifiedName()[0]
        if self.peek() in ("Q", "R", "S") and self.text[self.pos + 1 : self.pos + 2] not in ("6", "8"):
            # a qualified pointer as the member type is the one shape to avoid: the reference
            # does not spell the qualifiers it would carry - "PQfoo@@SAPEAX" is
            # "void **foo::*" - and nothing on the producer side says which is right. A
            # function type after the same letter is not that shape and reads normally.
            raise _Bail
        member = self.type(member_quals)
        self.simple = False
        return Indirection(f"{owner}::{token}", own_quals, member)

    def memberFunctionPointer(self, own_quals, token):
        """A pointer to member function: "P8" and the class it points into.

        The class qualifies the declarator rather than the type - "void (__thiscall S::*)()"
        - and the member's own cv follows the parameter list, where a member function keeps
        it.
        """
        owner = self.qualifiedName()[0]
        member_cv = self.memberQualifiers()
        convention = _CALLING_CONVENTIONS.get(self.take())
        if convention is None:
            raise _Bail
        returns = self.returnType()
        saved_pointee_depth = self.pointee_depth
        self.pointee_depth = 0
        try:
            params = self.parameters()
        finally:
            self.pointee_depth = saved_pointee_depth
        self.expect("Z")
        self.simple = False
        return Indirection(f"{owner}::{token}", own_quals, FunctionType(convention, params, returns, member_cv))

    def functionTypeArgument(self):
        """A function type written as a template argument: "$$A6", or "$$A8" with a qualifier.

        The "8" form is the one a member function's type takes, but it names no class - the
        reference refuses "$$A8S@@AEHXZ" - so it reads as an ordinary function type carrying
        the qualifier that only a member function can have: "int __cdecl(void) const".
        """
        self.simple = False
        member_cv = ""
        if self.eat("8"):
            self.expect("@")
            self.expect("@")
            member_cv = self.memberQualifiers()
        else:
            self.expect("6")
        convention = _CALLING_CONVENTIONS.get(self.take())
        if convention is None:
            raise _Bail
        returns = self.returnType()
        params = self.parameters()
        self.expect("Z")
        return FunctionType(convention, params, returns, member_cv)

    def memberQualifiers(self):
        """What a member function may carry after its parameters: cv, __restrict, a ref.

        They are written modifier-first and spelled the other way round, so "GB" is
        " const &" and "IA" is " __restrict".
        """
        restrict = ""
        reference = ""
        unaligned = ""
        # they are written in this order and each at most once, so "HH" and "IE" are not
        # names however much they parse like one
        rank = {"E": 0, "I": 1, "F": 2, "G": 3, "H": 3}
        written = -1
        while self.peek() in "EIGHF":
            char = self.take()
            if rank[char] <= written:
                raise _Bail
            written = rank[char]
            if char == "I":
                restrict = " __restrict"
            elif char == "F":
                unaligned = " __unaligned"
            elif char in ("G", "H"):
                reference = " &" if char == "G" else " &&"
        qualifier = _CV.get(self.take())
        if qualifier is None:
            raise _Bail
        return f"{qualifier}{restrict}{unaligned}{reference}"

    def parameters(self):
        """A parameter list, recording each composite parameter for later back-references.

        A trailing Z before the terminator marks a variadic list. Each parameter is kept as
        the type it is rather than as its spelling, so a caller can ask what a function
        takes; the spelling is still produced here, because rendering is what refuses a
        name whose parameters expand past the output bound, and a back-reference repeated
        across a parameter list expands multiplicatively.
        """
        if self.eat("X"):
            return (Raw("void"),)
        params = []
        while True:
            if self.eof():
                raise _Bail
            if self.eat("@"):
                break
            if self.peek() == "Z":
                if not params:
                    raise _Bail
                self.take()
                params.append(Raw("..."))
                break
            if self.peek() in string.digits:
                index = int(self.take())
                if index >= len(self.arg_backrefs):
                    raise _Bail
                node = self.arg_backrefs[index]
                self.rendered(node)
                params.append(node)
                continue
            self.simple = True
            node = self.type()
            if not self.simple and len(self.arg_backrefs) < 10:
                self.arg_backrefs.append(node)
            self.rendered(node)
            params.append(node)
        return tuple(params)

    def parse(self):
        self.expect("?")
        # "$$J" marks a name that was mangled although it is extern "C"; the digit after it
        # counts how many characters of the original mangling it kept, which is not spelled
        extern_c = ""
        name, has_no_return_type, special_form = self.qualifiedName()
        if special_form == "descriptor":
            # it is the whole name: what it describes has already been read
            return Raw(name)
        if special_form == "rtti":
            # these three are written with one storage class and nothing else
            self.expect("8")
            if not self.nested and not self.eof():
                raise _Bail
            return Raw(name)
        if special_form == "guard":
            # a guard is written with one storage class and a number, which counts the
            # static it guards within its function and is left out when it is the first
            self.expect("5")
            counted = self.templateInteger()
            if not self.nested and not self.eof():
                raise _Bail
            return Raw(name if counted == "0" else f"{name}{{{counted}}}")
        if self.eof():
            raise _Bail
        char = self.peek()
        if char == "$" and self.text.startswith("$$J", self.pos):
            self.pos += 3
            if self.eof() or self.peek() not in string.digits:
                raise _Bail
            self.take()
            extern_c = 'extern "C" '
            char = self.peek()
        if char == "9":
            # a name with no signature at all: the linkage is what is being spelled
            self.take()
            if not self.nested and not self.eof():
                raise _Bail
            return Raw(f'extern "C" {name}')
        if (special_form == "data") != (char in "67"):
            raise _Bail
        if self.requires_signature and char not in "Y$" and char not in _FUNCTION_ACCESS:
            # this one runs code, so it is spelled with a signature and never with storage
            raise _Bail
        if char in "67":
            self.take()
            qualifier = _CV.get(self.take())
            if qualifier is None:
                raise _Bail
            # a vftable may say which base it is the table for, as a qualified name of its
            # own: "??_7A@B@@6BC@D@@@" is B::A's table for D::C
            bases = []
            while not self.eat("@"):
                bases.append(self.qualifiedName()[0])
            base = "'s `".join(bases)
            if not self.nested and not self.eof():
                raise _Bail
            spelled = f"{qualifier.strip()} {name}".strip()
            return Raw(f"{spelled}{{for `{base}'}}" if base else spelled)
        if char in _DATA_ACCESS:
            self.take()
            self.simple = True
            declared = self.type()
            # a pointer into a class spells its own storage the long way, below; the short
            # forms are for everything else
            points_into_class = declared.kind == "indirection" and declared.points_into_class
            # __ptr64 and __restrict stand in front of the qualifier, and only where
            # something is pointed at: "?s@@3PEAHEA" is a name and "?s@@3HEA" is not
            trailing = self.take()
            restrict = ()
            seen = set()
            while trailing in ("E", "I"):
                if declared.kind != "indirection" or trailing in seen:
                    raise _Bail
                seen.add(trailing)
                if trailing == "I":
                    restrict = ("__restrict",)
                trailing = self.take()
            if trailing in _MEMBER_DATA_QUALS:
                # a pointer to data member repeats the member's qualifier here and names its
                # class again by back-reference: "?m@@3PQfoo@@HR1@" is "int const foo::*m"
                member_quals = _MEMBER_DATA_QUALS[trailing]
                self.nameFragment(False)
                self.expect("@")
            elif trailing in _CV_QUALS and not points_into_class:
                member_quals = _CV_QUALS[trailing]
            else:
                raise _Bail
            if not self.nested and not self.eof():
                raise _Bail
            if restrict and "__restrict" not in declared.qualifiers:
                # it qualifies the pointer, not what is pointed at, and is written once
                # however many times it is spelled: "?h3@@3QIAHIA" is "int *const __restrict"
                declared = Indirection(declared.sigil, declared.qualifiers + restrict, declared.inner)
            if member_quals and is_member_function_pointer(declared):
                # a member function keeps its qualifier after the parameters, not on what
                # the pointer points at, so this one joins the function rather than the type
                function = declared.inner
                trailing_cv = "".join(f" {qual}" for qual in member_quals)
                declared = Indirection(
                    declared.sigil,
                    declared.qualifiers,
                    FunctionType(
                        function.convention,
                        function.parameters,
                        function.returns,
                        function.member_cv + trailing_cv,
                    ),
                )
            else:
                declared = qualify_declared(declared, member_quals)
            # rendering the declaration here is what refuses one grown past the output
            # bound; the tree it is built from is what the caller is handed
            self.rendered(declared, name)
            return Declaration(_DATA_ACCESS[char], Name(name), declared)
        return prefixed(extern_c, self.function(name, has_no_return_type, special_form == "vcall"))

    def stringLiteral(self):
        """The literal a "??_C" name stands for, spelled the way the reference spells it.

        The length counts the terminator, the eight characters after it are a hash of the
        bytes, and the bytes themselves are written plainly or as an escape - a digit for
        one of ten punctuation characters, or "$" and two nibbles for any byte at all.
        """
        self.expect("@")
        self.expect("_")
        if self.take() != "0":
            # "_0" is a narrow string; the wider encodings spell their bytes differently
            raise _Bail
        length = int(self.templateInteger())
        while not self.eat("@"):
            # the hash is not spelled, but it has to be walked past
            self.take()
        decoded = []
        while not self.eat("@"):
            char = self.take()
            if char != "?":
                decoded.append(char)
                continue
            marker = self.take()
            if marker == "$":
                high, low = self.take(), self.take()
                if not ("A" <= high <= "P" and "A" <= low <= "P"):
                    raise _Bail
                decoded.append(chr((ord(high) - 65) * 16 + ord(low) - 65))
            elif marker in _LITERAL_ESCAPES:
                decoded.append(_LITERAL_ESCAPES[marker])
            else:
                raise _Bail
        if len(decoded) != length or (decoded and decoded[-1] != "\0"):
            raise _Bail
        if not self.nested and not self.eof():
            raise _Bail
        spelled = "".join(_LITERAL_SPELLINGS.get(char, char) for char in decoded[:-1])
        return f'"{spelled}"'

    def signedDisplacement(self):
        """One of a vtordisp thunk's two displacements, which are signed and 32 bits wide."""
        value = int(self.templateInteger()) & 0xFFFFFFFF
        return value - (1 << 32) if value >= (1 << 31) else value

    def thunkFunction(self, name, is_vcall):
        """A thunk whose access slot begins with "$": a vcall, or an adjustment through a
        virtual base.

        A vcall names no access and carries no parameters - the whole of it is the slot it
        dispatches through - while the vtordisp forms are ordinary virtual member functions
        with two displacements written in front of the signature.
        """
        code = self.take()
        if code == "B":
            if not is_vcall:
                raise _Bail
            slot = self.templateInteger()
            # the slot is the whole of it: neither the qualifier nor the convention may be
            # anything else, so "$B7DA" and "$B7FAA" are not names
            self.expect("A")
            self.expect("A")
            if not self.nested and not self.eof():
                raise _Bail
            return Raw(f"[thunk]: __cdecl {name}{{{slot}, {{flat}}}}")
        if code == "R":
            access = _VTORDISP_ACCESS.get(self.take())
            if access is None:
                raise _Bail
            displacements = [self.signedDisplacement() for _ in range(4)]
            return self.thunkBody(name, access, "vtordispex", displacements)
        access = _VTORDISP_ACCESS.get(code)
        if access is None:
            raise _Bail
        first = self.signedDisplacement()
        second = self.signedDisplacement()
        return self.thunkBody(name, access, "vtordisp", [first, second])

    def thunkBody(self, name, access, kind, displacements):
        """The signature a vtordisp or vtordispex thunk carries, once its numbers are read."""
        self.member_cv = self.memberQualifiers()
        convention = _CALLING_CONVENTIONS.get(self.take())
        if convention is None:
            raise _Bail
        returns = None if self.peek() == "@" and self.take() else self.returnType()
        params = self.parameters()
        self.expect("Z")
        if not self.nested and not self.eof():
            raise _Bail
        written = ", ".join(str(value) for value in displacements)
        spelled = f"{name}`{kind}{{{written}}}'"
        signature = FunctionType(convention, params, returns)
        if returns is not None:
            # the bound is enforced where a spelling is completed; the form that writes no
            # return type has nothing wrapped around its parameters to grow one
            self.rendered(signature, spelled)
        return Declaration(f"[thunk]: {access}: virtual ", Name(spelled), signature, self.member_cv)

    def function(self, name, has_no_return_type, is_vcall=False):
        access_char = self.take()
        thunk = ""
        if access_char == "$":
            return self.thunkFunction(name, is_vcall)
        if is_vcall:
            # the slot dispatched through is the whole of a vcall, so it is spelled one way
            raise _Bail
        if access_char == "Y":
            access, is_static, is_virtual = None, False, False
        elif access_char in _ADJUSTOR_ACCESS:
            access, is_static, is_virtual = _ADJUSTOR_ACCESS[access_char]
            thunk = f"`adjustor{{{self.templateInteger()}}}'"
            self.member_cv = self.memberQualifiers()
        else:
            entry = _FUNCTION_ACCESS.get(access_char)
            if entry is None:
                raise _Bail
            access, is_static, is_virtual = entry
            if not is_static:
                self.member_cv = self.memberQualifiers()
            else:
                self.member_cv = ""
        convention = _CALLING_CONVENTIONS.get(self.take())
        if convention is None:
            raise _Bail
        if has_no_return_type or self.peek() == "@":
            # an operator may leave the return slot empty, the way a constructor does; a
            # conversion operator may not, since its return is the type it converts to
            self.expect("@")
            returns = None
        else:
            returns = self.returnType()
        params = self.parameters()
        self.expect("Z")
        if not self.nested and not self.eof():
            raise _Bail
        pieces = []
        if thunk:
            pieces.append("[thunk]: ")
            name = f"{name}{thunk}"
        if access:
            pieces.append(f"{access}: ")
            if is_static:
                pieces.append("static ")
            if is_virtual:
                pieces.append("virtual ")
        if "\0conversion\0" in name:
            if returns is None:
                raise _Bail
            name = name.replace("\0conversion\0", f"operator {self.rendered(returns)}")
        signature = FunctionType(convention, params, returns)
        if returns is not None:
            # as in thunkBody: completing the spelling is what refuses one past the bound
            self.rendered(signature, name)
        trailing = self.member_cv if access and not is_static else ""
        return Declaration("".join(pieces), Name(name), signature, trailing)


def parse_msvc_symbol(name):
    """Return the tree behind a decorated name, or None when it is not fully understood.

    None rather than an exception: which names this demangler declines is a property of
    the grammar it covers, not an error condition, and every caller here has to answer
    for a name it cannot read anyway.

    A name carrying a control character is refused outright: a decorated name is read from a
    NUL-terminated string of source-legal characters and cannot hold one, and an expansion
    holding it would travel into the report as a symbol name. The identifier is copied into
    the answer verbatim, so testing the input is what keeps the answer clean.
    """
    if not name or not name.startswith("?"):
        return None
    if any(char < " " or char == "\x7f" for char in name):
        return None
    try:
        return _Demangler(name).parse()
    except (_Bail, RecursionError):
        return None


@lru_cache(maxsize=4096)
def demangle_msvc_symbol(name):
    """Return a readable C++ name, or the original when it is not fully understood.

    Cached, because this is the call a symbol table makes hundreds of thousands of times
    and one binary names the same type over and over. The tree is not cached with it: a
    caller that wants structure asks for it directly and is not repeating itself the way
    a caller labelling symbols is.
    """
    tree = parse_msvc_symbol(name)
    return name if tree is None else render(tree)
