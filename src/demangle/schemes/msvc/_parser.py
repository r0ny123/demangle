"""Demangling for MSVC decorated symbol names."""

import re
import string
from functools import lru_cache

from ...core.limits import DEFAULT_LIMITS
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
from .options import DEFAULT_OPTIONS

try:
    from demangle._speedups import fast_msvc_identifier as _fast_msvc_identifier
except ImportError:

    def _fast_msvc_identifier(text, pos, length):
        return None


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
    # Deduced return types (`?A_P`, `?A_T`, clang's dependent `auto`): llvm-undname 18
    # refuses them; these are LLVM main's spellings.
    "P": "auto",
    "Q": "char8_t",
    "S": "char16_t",
    "T": "decltype(auto)",
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
# a thunk adjusts "this"; the reference prefixes the spelling and marks the adjustment
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
_DYNAMIC_INITIALISERS = {"E": "dynamic initializer for", "F": "dynamic atexit destructor for"}
_GUARDS = {"B": "`local static guard'", "__J": "`local static thread guard'"}
#: The operators written with the `??__` prefix rather than a single code.
_DOUBLE_UNDERSCORE_OPERATORS = {"L": "operator co_await", "M": "operator<=>"}
#: Brackets a bare function type's calling convention inside a rendered template name
#: until the name is used; see `_Demangler._conventionsResolved`.
_CONVENTION_MARK = "\x00"

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
    # `?A`-`?Z` are 0xC1-0xDA and `?a`-`?z` 0xE1-0xFA (`demangleCharLiteral`).
    **{chr(65 + at): chr(0xC1 + at) for at in range(26)},
    **{chr(97 + at): chr(0xE1 + at) for at in range(26)},
}
_LITERAL_SPELLINGS = {
    0x00: "\\0",
    0x07: "\\a",
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0B: "\\v",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x27: "\\'",
    0x5C: "\\\\",
}

#: Bytes of a literal written into the name, however long the string.
_LITERAL_MAX_BYTES = 64

#: Most bytes a literal decodes to: the documented 32, times four as the reference allows.
_LITERAL_MAX_DECODED = 32 * 4


#: See the note in `schemes/msvc/__init__.py`.
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


def _escaped_literal_character(value):
    """One character of a string literal, spelled the way the reference spells it."""
    escape = _LITERAL_SPELLINGS.get(value)
    if escape is not None:
        return escape
    if 0x1F < value < 0x7F:
        return chr(value)
    digits = f"{value:X}"
    return "\\x" + digits.rjust(len(digits) + len(digits) % 2, "0")


def _guess_character_width(raw, length):
    """How many bytes to the character a narrow literal's bytes are.

    The encoding does not say -- `char`, `char16_t` and `char32_t` all mangle as `_0` --
    so this is the reference's guess, transcribed. An odd length settles it at one. A
    string short enough to have been written whole is settled by the width of its
    terminator. Otherwise the embedded nul bytes decide: over two thirds of them nul is
    four bytes to the character, over a third is two.
    """
    if length % 2:
        return 1
    if length < 32:
        trailing = 0
        for value in reversed(raw):
            if value:
                break
            trailing += 1
        if trailing >= 4 and length % 4 == 0:
            return 4
        return 2 if trailing >= 2 else 1
    nuls = sum(1 for value in raw if not value)
    if nuls >= 2 * len(raw) // 3 and length % 4 == 0:
        return 4
    return 2 if nuls >= len(raw) // 3 else 1


_RTTI_NAMES = {
    "2": "`RTTI Base Class Array'",
    "3": "`RTTI Class Hierarchy Descriptor'",
    "4": "`RTTI Complete Object Locator'",
}
# Special names spelled with the "6" storage form; vcall, typeof and local static guard
# take a storage class this parser does not model, so they decline.
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
#: The same, split into the two pieces `MsvcOptions` can drop separately.
_DATA_ACCESS_PARTS = {
    "0": ("private: ", "static "),
    "1": ("protected: ", "static "),
    "2": ("public: ", "static "),
    "3": ("", ""),
    "4": ("", ""),
}
_POINTER_KINDS = {"P": (), "Q": ("const",), "R": ("volatile",), "S": ("const", "volatile")}
# what stands where a pointee's cv would, when the pointer points into a class instead
_MEMBER_DATA_QUALS = {"Q": (), "R": ("const",), "S": ("volatile",), "T": ("const", "volatile")}
_CV_QUALS = {"A": (), "B": ("const",), "C": ("volatile",), "D": ("const", "volatile")}
_CV = {"A": "", "B": " const", "C": " volatile", "D": " const volatile"}
# shared nodes: a node holds no parse state
_BASIC_TYPE_NODES = {code: Raw(name) for code, name in _BASIC_TYPES.items()}
_EXTENDED_TYPE_NODES = {code: Raw(name) for code, name in _EXTENDED_TYPES.items()}
_NULLPTR = Raw("std::nullptr_t")
_ELLIPSIS = Raw("...")
_VOID_PARAMETERS = (Raw("void"),)


def _spaced(word):
    """A trailing qualifier with the space that precedes it, or nothing if it is not printed.

    A suppressed keyword has to take its space with it: `f(void) const __restrict` losing
    the `__restrict` is `f(void) const`, not `f(void) const `.
    """
    return f" {word}" if word else ""


class _Conversion:
    """A conversion operator, whose name is the type it converts to.

    That type is written in the return slot, which is read long after the name, so the
    spelling is finished once the signature has been. It may be a template -- a
    conversion function template, `??$?BU...@@`, `operator<A, B> T` -- in which case the
    arguments go between the word and the type, which is where the reference's
    `ConversionOperatorIdentifierNode` writes them.
    """

    def __init__(self, arguments=""):
        self.arguments = arguments


class _Structor:
    """A constructor or destructor: its spelling comes from the class it belongs to.

    It may itself be a template, in which case the arguments follow the class name it
    borrows: "??$?0N@?$Foo@H@@QEAA@N@Z" is Foo<int>::Foo<int><double>.
    """

    def __init__(self, is_destructor, arguments=""):
        self.is_destructor = is_destructor
        self.arguments = arguments


class _LimitHit(Exception):
    """A resource bound stopped the parse, rather than the grammar refusing the name.

    Kept apart from `_Bail` so the caller can report it as what it is. Reported as "not
    a decorated name this demangler can read" it would mislead: the name may
    be perfectly well formed and simply larger than the caller allowed, and a tool
    deciding whether to widen its `Limits` cannot tell the two apart from that message.

    Carries the bound that *actually* stopped the parse. The output bound is the caller's
    narrowed by one of this scheme's own -- `min(limits.max_output, 32 * len(mangled) +
    256)` -- so reading the caller's figure back out of `limits` can name a number that
    was never in force.
    """

    def __init__(self, what, limit):
        super().__init__(what)
        self.what = what
        self.limit = limit


class _Bail(Exception):
    """The name is not one this demangler fully understands."""


class _Demangler:
    """A cursor over one decorated name.

    `limits.max_depth` bounds the mutually recursive name and type parser, as it does
    Itanium's. A level costs several interpreter frames, so the interpreter's recursion
    limit may stop a deep name first; that is reported as the same bound (see
    `parse_msvc_symbol_strict`). max_render bounds the rendered result, which
    back-reference reuse can otherwise grow multiplicatively.
    """

    def __init__(self, mangled, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
        self.text = mangled
        #: Cached: `eof` is the hottest method in this scheme.
        self.length = len(mangled)
        #: Held by the parser because back-references resolve against rendered text.
        self.options = options
        self.pos = 0
        self.name_backrefs = []
        self.arg_backrefs = []
        self.simple = True
        self.template_depth = 0
        self.at_symbol_name = True
        #: The symbol's own unqualified name when it is a template, for `addressArgument`.
        self.symbol_template_name = None
        self.last_nested_symbol_template = None
        self.requires_signature = False
        self.nested = False
        self.pointee_depth = 0
        #: Return types of pointed-to functions the cursor is inside. The reference's
        #: `OF_NoCallingConvention` reaches template arguments there, so
        #: `std::function<void (void)>` loses its `__cdecl` in a callback's return type.
        self.in_pointee_return = 0
        self.array_element_depth = 0
        # set only while the next type read stands directly as a template argument
        self.at_argument = False
        self.member_cv = ""
        self.depth = 0
        self.max_depth = limits.max_depth
        # Bounded relative to the name too: a two-character back-reference can double the
        # spelling per level. Over 1,025,085 real names the widest is 12x; 32x is clear.
        self.max_render = min(limits.max_output, 32 * len(mangled) + 256)

    def eof(self):
        return self.pos >= self.length

    # These test the bound inline rather than calling `eof`: they are the hot path.
    def peek(self):
        pos = self.pos
        if pos >= self.length:
            raise _Bail
        return self.text[pos]

    def take(self):
        pos = self.pos
        if pos >= self.length:
            raise _Bail
        self.pos = pos + 1
        return self.text[pos]

    def eat(self, char):
        pos = self.pos
        if pos < self.length and self.text[pos] == char:
            self.pos = pos + 1
            return True
        return False

    def expect(self, char):
        if not self.eat(char):
            raise _Bail

    def identifier(self):
        fast = _fast_msvc_identifier(self.text, self.pos, self.length)
        if fast is not None:
            self.pos, name = fast
            return name
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

        The bound is tested first. Both conditions have to hold and neither has a side
        effect, so the order is free to be chosen on cost -- and a full table is the
        common case on any name long enough to matter, where testing it first skips a
        scan of the list rather than finishing one that cannot change anything.
        """
        if len(self.name_backrefs) < 10 and name not in self.name_backrefs:
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
            text, length = self.text, self.length
            while True:
                pos = self.pos
                if pos >= length:
                    raise _Bail
                char = text[pos]
                if char == "@":
                    self.pos = pos + 1
                    break
                if char == "$":
                    # "$S", "$$V" and "$$$V" spell an empty pack and "$$Z" separates
                    # arguments: none contributes an argument.
                    if text.startswith("$S", pos):
                        self.pos = pos + 2
                        continue
                    if text.startswith("$$$V", pos):
                        self.pos = pos + 4
                        continue
                    if text.startswith(("$$V", "$$Z"), pos):
                        self.pos = pos + 3
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
        # only the symbol's own name is exempt, and it is the very first leading fragment
        is_symbol_name = is_leading and self.at_symbol_name
        if is_leading:
            self.at_symbol_name = False
        char = self.peek()
        if char in string.digits:
            self.pos += 1
            index = int(char)
            if index >= len(self.name_backrefs):
                raise _Bail
            return self.name_backrefs[index], None
        if char == "?":
            self.pos += 1
            if self.eat("$"):
                if self.peek() in string.digits:
                    raise _Bail
                operator = None
                if self.peek() == "?":
                    # a template whose name is an operator: "??$?HH@S@@" is operator+<int>
                    self.take()
                    operator, _ = self.operatorName()
                    if isinstance(operator, _Structor):
                        # a templated constructor's arguments follow the class name it
                        # borrows
                        arguments = self._conventionsResolved(self.templateInstantiation(operator=""))
                        return _Structor(operator.is_destructor, arguments), "func"
                    if isinstance(operator, _Conversion):
                        # So may a conversion operator: `??$?BU...@@` is `operator<A, B> T`.
                        arguments = self._conventionsResolved(self.templateInstantiation(operator=""))
                        return _Conversion(arguments), "func"
                    if not isinstance(operator, str):
                        raise _Bail
                base = self.in_pointee_return
                rendered = self.templateInstantiation(operator=operator)
                if is_symbol_name:
                    # Kept for `$1??$x@H@@3HA`, whose `x<int>` the reference memorises
                    # after the symbol is read -- see `dollarType`.
                    self.symbol_template_name = self._conventionsResolved(rendered, base=base)
                if not is_symbol_name:
                    # The symbol's own template name is not recorded: "??$f@H@N@@YAXV0@@Z"
                    # resolves 0 to N. `memorizeIdentifier` records the `OF_Default` string.
                    self.rememberName(self._conventionsResolved(rendered, base=base))
                if self.template_depth:
                    # Inside another template's arguments the outermost name resolves marks.
                    return rendered, "func" if operator else None
                return self._conventionsResolved(rendered), "func" if operator else None
            if is_symbol_name:
                # "??A" here is operator[]: only a symbol's own name may be an operator
                return self.operatorName()
            if not is_leading:
                if self.peek() == "A":
                    return self.anonymousNamespace(), None
                # a scope number may be nibbles; "?A" is the namespace above
                if self.atLocalScope():
                    return self.localScope(), None
            # No code claims it: `demangleSimpleName` reads it as a plain name, "?" and all.
            return self.questionName(), None
        name = self.identifier()
        self.rememberName(name)
        return name, None

    def questionName(self):
        """A name whose leading "?" is part of it, the "?" already read.

        Empty after the "?" is a name too -- the reference stops at the first "@" that is
        not the very first character, and the "?" is that character -- so "?@" is the name
        "?" rather than a refusal.
        """
        end = self.text.find("@", self.pos)
        if end < 0:
            raise _Bail
        name = "?" + self.text[self.pos : end]
        self.pos = end + 1
        self.rememberName(name)
        return name

    def atLocalScope(self):
        """Whether the "?" just read opens a scope number, by the reference's own test.

        Deciding by lookahead rather than by trying, because the alternative -- reading a
        scope and falling back when it fails -- would have already spent a nested symbol's
        worth of back-reference entries on the shared table by the time it found out.

        A number, then a "?" for the symbol the scope belongs to: one digit standing for
        itself, or "@" for zero, or nibbles from B-P then A-P ended by "@". "A" cannot open
        one because "?A" is the anonymous namespace, and a leading zero would be spelled as
        a digit anyway.
        """
        end = self.text.find("?", self.pos)
        if end < 0:
            return False
        spelled = self.text[self.pos : end]
        if not spelled:
            return False
        if len(spelled) == 1:
            return spelled == "@" or spelled in string.digits
        if spelled[-1] != "@":
            return False
        spelled = spelled[:-1]
        return "B" <= spelled[0] <= "P" and all("A" <= digit <= "P" for digit in spelled[1:])

    def localScope(self):
        """A scope inside a function: the function's own name, and which scope of it.

        "?1??f@@YAXXZ@" is the second scope of "void __cdecl f(void)". The enclosing name
        is a complete decorated name in its own right and is read as one, in its own
        back-reference scopes - which is why it is parsed by a separate cursor over the
        same text rather than inline.
        """
        # a template-argument number, except that a bare "@" is zero
        spelled = "0" if self.eat("@") else self.templateInteger()
        self.expect("?")
        # The reference's flags apply to the named symbol, not to its scopes.
        return f"`{self.nestedSymbol(options=self.options.for_a_scope())}'::`{spelled}'"

    def namesADataSymbol(self):
        """Whether what follows is a data symbol rather than a function, without reading it.

        Only the storage class tells the two apart, and it comes after the whole name, so
        this walks a throwaway cursor to it and reports what it found.
        """
        probe = _Demangler("?" + self.text[self.pos :], options=self.options)
        probe.max_depth = self.max_depth
        probe.max_render = self.max_render
        probe.depth = self.depth
        probe.nested = True
        try:
            probe.expect("?")
            probe.qualifiedName()
        except _Bail:
            return False
        return not probe.eof() and probe.peek() in _DATA_ACCESS

    def nestedSymbol(self, leading_question=True, options=None):
        """A complete decorated name written inside another one.

        It continues this name's back-reference table rather than opening its own, so
        "?N@?1??SN@?$NS@H@0@QEAAHXZ@4HA" resolves its 0 to the outer N and
        "??$f@VBar@@$1?x@0@3HA@@YAXXZ" resolves its 0 to the template's own f. It is still a
        symbol, so its own leading template is not recorded either, and it ends where it
        ends rather than at the end of the text.
        """
        options = self.options if options is None else options
        if leading_question:
            inner = _Demangler(self.text, options=options)
            inner.pos = self.pos
        else:
            # the introducing code spent this name's "?", so read over a copy that has one
            inner = _Demangler("?" + self.text[self.pos :], options=options)
        inner.max_depth = self.max_depth
        inner.max_render = self.max_render
        inner.nested = True
        inner.name_backrefs = self.name_backrefs
        inner.arg_backrefs = self.arg_backrefs
        inner.at_symbol_name = True
        inner.depth = self.depth
        rendered = render(inner.parse(), options=options)
        self.pos = inner.pos if leading_question else self.pos + inner.pos - 1
        self.last_nested_symbol_template = inner.symbol_template_name
        return rendered

    def addressArgument(self):
        """`$1` and a decorated name: the address of a symbol as a template argument.

        The symbol is read in this template's back-reference scope, and the reference
        then memorises its unqualified name --
        `memorizeIdentifier(S->Name-> getUnqualifiedIdentifier())` -- which for a plain
        name changes nothing, since reading it recorded it, and for a template name
        records what a symbol's own template name is otherwise the one exception to:
        `?Zoo@@3U?$Foo@$1??$x@H@@3HA$1?1@3HA@@A` is
        `struct Foo<&int x<int>, &int x<int>> Zoo`, its `?1` the `x<int>` the first
        argument read.
        """
        self.simple = False
        symbol = self.nestedSymbol()
        if self.last_nested_symbol_template is not None:
            self.rememberName(self.last_nested_symbol_template)
        return Raw("&" + symbol)

    def memberPointerArgument(self):
        """A pointer to member under an inheritance model that needs more than an address.

        `H` multiple, `I` virtual and `J` unspecified inheritance carry a function's name
        and one, two or three offsets after it; `F` and `G` are the data-member forms,
        offsets alone. The reference brackets the lot -- `{public: void __cdecl
        S::g(void), 4}`, `{4, 0}` -- and clang writes the first for every
        `filtered_decl_iterator<ObjCMethodDecl, &isClassMethod>` and
        `LazyOffsetPtr<Decl, unsigned int, &ExternalASTSource::GetExternalDecl>` in its
        own Windows build, where 186 symbols carry it. The symbol's
        unqualified name is memorised as `addressArgument` memorises it.
        """
        kind = self.take()
        parts = []
        if kind in ("H", "I", "J") and self.peek() == "?":
            parts.append(self.nestedSymbol())
            if self.last_nested_symbol_template is not None:
                self.rememberName(self.last_nested_symbol_template)
        for _ in range({"H": 1, "I": 2, "J": 3, "F": 2, "G": 3}[kind]):
            parts.append(self.templateInteger())
        self.simple = False
        return Raw("{" + ", ".join(parts) + "}")

    def md5Name(self):
        """`??@<hash>@`: a decorated name too long for the linker, replaced by its MD5.

        Nothing of the original is in the symbol, so the spelling is the name itself, up
        to the `@` that closes the hash -- and `??_R4@` after it, the one thing the
        reference keeps: a complete object locator's tag, which for a hashed name is
        written after the hash rather than before it. Read here rather than only at the
        top of a name because a hashed function is still a scope:
        `?catch$0@?0???@<hash>@@4HA` is a catch block's variable inside one, ``
        `??@<hash>@'::`1'::catch$0 ``, which 409 of Boost 1.84's symbols are.
        """
        end = self.text.find("@", self.pos + 2)
        if end < 0:
            raise _Bail
        name = self.text[self.pos - 1 : end + 1]
        self.pos = end + 1
        if self.text.startswith("??_R4@", self.pos):
            name += "??_R4@"
            self.pos += 6
        return Raw(name)

    def anonymousNamespace(self):
        """The unnamed namespace of one translation unit: "?A" and an optional discriminator.

        The discriminator tells two of them apart inside one binary, and the reference does
        not spell it, so two anonymous namespaces render alike - which is what C++ source
        looks like too.

        Whatever stands between the "?A" and the "@" is that discriminator, and it is taken
        as it is written rather than checked against the "0x" and hex digits a compiler
        emits. The reference does the same, and the spelling is not load-bearing: it is
        never printed, only recorded, so a discriminator this did not expect is a name it
        would refuse for no gain.
        """
        self.expect("A")
        end = self.text.find("@", self.pos)
        if end < 0:
            raise _Bail
        # a later back-reference resolves to the discriminator: "?f@?A0x1@@YAXV1@@Z" ->
        # "class 0x1"
        self.rememberName(self.text[self.pos : end])
        self.pos = end + 1
        return "`anonymous namespace'"

    def endsTheInitialisedName(self):
        """The stub's name ends with the variable it runs for, and carries no scope.

        `demangleInitFiniStub` reads the variable, then the `@` terminators the form
        requires -- two where the leading `?` was written, one where it was not -- and
        then the function encoding. There is nowhere for another component to go, and
        reading one would make `??__E?i@C@@0HA@e@@QEAAHXZ` a dynamic initializer inside
        a namespace `e`: `public: int __cdecl e::`dynamic initializer for ...''(void)`,
        with an access specifier and a return type that the form does not have either.
        """
        if self.peek() != "@":
            raise _Bail

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
                        # a whole symbol: "??__E?i@C@@0HA@@YAXXZ" runs for "private:
                        # static int C::i"
                        target = self.nestedSymbol()
                        self.expect("@")
                        self.endsTheInitialisedName()
                        return f"`{_DYNAMIC_INITIALISERS[code]} `{target}''", "func"
                    if self.namesADataSymbol():
                        # a data symbol written without its leading "?"
                        whole = self.nestedSymbol(leading_question=False)
                        self.endsTheInitialisedName()
                        return f"`{_DYNAMIC_INITIALISERS[code]} `{whole}''", "func"
                    # recorded, unlike a literal operator's suffix: "??__EFoo@@YAXU0@@Z"
                    # -> Foo
                    if self.peek().isdigit():
                        raise _Bail
                    target = self.identifier()
                    self.rememberName(target)
                    # Qualified, all inside the quotes (`demangleInitFiniStub`):
                    # `??__Eg@inner@outer@@YAXXZ` is
                    # `` `dynamic initializer for 'outer::inner::g'' ``.
                    scopes = [target]
                    while self.peek() != "@":
                        if self.eof():
                            raise _Bail
                        scopes.append(self.nameFragment(False)[0])
                    scopes.reverse()
                    spelled = "::".join(scopes)
                    return f"`{_DYNAMIC_INITIALISERS[code]} '{spelled}''", "func"
                if code in _DOUBLE_UNDERSCORE_OPERATORS:
                    self.take()
                    return _DOUBLE_UNDERSCORE_OPERATORS[code], "func"
                if self.take() != "K":
                    raise _Bail
                # user-defined literal: operator ""suffix
                return f'operator ""{self.identifier()}', "func"
            code = self.take()
            if code == "C":
                return self.stringLiteral(), "descriptor"
            if code == "R" and self.peek() in "1234":
                # RTTI names a class; "8" storage for these three, vftable form for the
                # locator
                which = self.take()
                if which == "1":
                    written = [self.templateInteger() for _ in range(4)]
                    # `mdisp, pdisp, vdisp, attributes`: only pdisp may be negative (-1 is no
                    # virtual base), as `llvm-undname` enforces.
                    if written[0].startswith("-") or any(value.startswith("-") for value in written[2:]):
                        raise _Bail
                    at = ", ".join(str(int(value)) for value in written)
                    return f"`RTTI Base Class Descriptor at ({at})'", "rtti"
                return _RTTI_NAMES[which], "data" if which == "4" else "rtti"
            if code == "R" and self.peek() == "0":
                # a type descriptor names the type it describes rather than a function
                self.take()
                described = self.returnType()
                self.expect("@")
                self.expect("8")
                if not self.nested and not self.eof():
                    raise _Bail
                if not self.options.variable_type:
                    # The type is all a descriptor says; vtable and vbtable names keep
                    # theirs.
                    return "`RTTI Type Descriptor'", "descriptor"
                # Where a declarator goes: `int (*`RTTI Type Descriptor')[2]`.
                return self.rendered(described, "`RTTI Type Descriptor'"), "descriptor"
            name = _EXTENDED_OPERATORS.get(code)
            if name is None:
                raise _Bail
            if code in _UNMODELLED_DATA_SPECIAL_OPERATORS:
                raise _Bail
            if code == "9":
                # spelled with its slot, never storage: "??_9Base@@3QAHA" is not a name
                self.requires_signature = True
                return name, "vcall"
            if code == "B":
                return name, "guard"
            return name, "data" if code in _DATA_SPECIAL_OPERATORS else "func"
        if self.eat("@"):
            # an MD5-hashed name
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
            # converts to the return slot's type: a signature, never storage
            self.requires_signature = True
            return _Conversion(), "func"
        name = _OPERATORS.get(code)
        if name is None:
            raise _Bail
        return name, "func"

    def qualifiedName(self):
        self.depth += 1
        if self.depth > self.max_depth:
            raise _LimitHit("recursion depth", self.max_depth)
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
        text, length = self.text, self.length
        while True:
            pos = self.pos
            if pos >= length:
                raise _Bail
            if text[pos] == "@":
                self.pos = pos + 1
                break
            scopes.append(self.nameFragment(False)[0])
        scopes.reverse()
        if isinstance(first, _Conversion):
            self.conversion_arguments = first.arguments
            return "::".join([*scopes, "\0conversion\0"]), False, special_form
        if isinstance(first, _Structor):
            if not scopes:
                raise _Bail
            klass = scopes[-1]
            spelled = klass + first.arguments
            first = "~" + spelled if first.is_destructor else spelled
            return "::".join([*scopes, first]), True, special_form
        return "::".join([*scopes, first]), False, special_form

    def type(self, quals=()):
        self.depth += 1
        if self.depth > self.max_depth:
            raise _LimitHit("recursion depth", self.max_depth)
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
        reading one would invent a spelling for it.
        """
        char = self.take()
        if char in _BASIC_TYPES:
            return apply_qualifiers(_BASIC_TYPE_NODES[char], quals)
        if char == "_":
            node = _EXTENDED_TYPE_NODES.get(self.take())
            if node is None:
                raise _Bail
            self.simple = False
            return apply_qualifiers(node, quals)
        if char in _TAGGED_TYPES:
            kind = _TAGGED_TYPES[char]
            if kind == "enum":
                self.expect("4")
            name, _, special_form = self.qualifiedName()
            if special_form is not None:
                raise _Bail
            self.simple = False
            # `tag_kind` off: `S const *`, not `struct S const *`
            spelled = f"{kind} {name}" if self.options.tag_kind else name
            return apply_qualifiers(Raw(spelled), quals)
        if char == "Y":
            return self.arrayType(quals)
        if char in _POINTER_KINDS:
            return self.indirection(merge_qualifiers(_POINTER_KINDS[char], quals), "*")
        if char == "A":
            if quals:
                raise _Bail
            # only "A" introduces a reference; "B" (volatile reference) is not C++:
            # "?f2@@YAXBDPAD@Z"
            return self.indirection((), "&")
        if char == "$":
            return self.dollarType(quals, at_argument)
        if char in string.digits:
            # an argument back-reference stands only where a whole argument does; see
            # parameters()
            raise _Bail
        if char == "?" and (self.peek() == "<" or self.peek() in string.digits):
            # A named placeholder type, `?A?<decltype-auto>@@`. Through `nameFragment`
            # so it is remembered: a nested lambda's deduced return is the
            # back-reference `?A?4@`.
            placeholder = self.nameFragment(False)[0]
            self.expect("@")
            return apply_qualifiers(Raw(placeholder), quals)
        raise _Bail

    def rendered(self, node, declarator="", member_cv=""):
        text = render(node, declarator, options=self.options, member_cv=member_cv)
        if len(text) > self.max_render:
            raise _LimitHit("output length", self.max_render)
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
        if self.depth + count > self.max_depth:
            raise _LimitHit("recursion depth", self.max_depth)
        # an extent of nothing is spelled with nothing: "$$BY0A@H" is "int[]"
        extents = [self.dimension() for _ in range(count)]
        self.array_element_depth += 1
        try:
            element = self.type(quals)
        finally:
            self.array_element_depth -= 1
        self.simple = False
        # "int[2][3]" is two arrays of three, so extents nest
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
            # a complete decorated name in this template's back-reference scope:
            # "??$f@VBar@@$1?x@0@3HA@@YAXXZ" resolves 0 to f
            if self.take() == "1":
                return self.addressArgument()
            self.simple = False
            return Raw(self.nestedSymbol())
        if self.peek() in ("H", "I", "J", "F", "G") and self.template_depth:
            return self.memberPointerArgument()
        if self.peek() == "0":
            if not at_argument:
                # an integer is an argument, never a type
                raise _Bail
            self.take()
            self.simple = False
            return Raw(self.templateInteger())
        if self.peek() == "M":
            # `$M <type> <nttp>`: an `auto` non-type argument. The reference spells only
            # the value (`A<42>`), then any argument form without its `$`. LLVM main
            # reads these; llvm-undname 18 refuses them, so none is in the corpus. An
            # argument, not a type.
            if not at_argument:
                raise _Bail
            self.take()
            self.type()
            if self.eat("0"):
                self.simple = False
                return Raw(self.templateInteger())
            if self.eat("1"):
                return self.addressArgument()
            if self.peek() in ("H", "I", "J", "F", "G"):
                return self.memberPointerArgument()
            return self.type(quals)
        if not self.eat("$"):
            raise _Bail
        if self.eat("B"):
            # the undecayed type, only where a parameter stands: "?f@@YAX$$BY01H@Z" is
            # not a name
            if self.peek() in "$6" or not at_argument:
                raise _Bail
            return self.type(quals)
        if self.peek() == "Y":
            # An alias template: only `demangleTemplateParameterList` consumes `$$Y`, so
            # `?f@@YAX$$YURetVal@@@Z` and `??$f@PA$$YURetVal@@@@YAXXZ` are not names.
            if not at_argument:
                raise _Bail
            self.pos += 1
            self.simple = False
            return Raw(self.qualifiedName()[0])
        kind = self.take()
        if kind == "Q":
            return self.indirection(quals, "&&")
        if kind == "C":
            if not (self.template_depth or self.array_element_depth):
                # "$$C" qualifies an array element or a template argument only:
                # "?f@@YAX$$CBH@Z" and "?f@@YAXPA$$CBH@Z" are not names
                raise _Bail
            extra = _CV_QUALS.get(self.take())
            if extra is None:
                raise _Bail
            if self.text.startswith("$$C", self.pos):
                # A second `$$C` in a row is not a type (`llvm-undname` refuses it).
                raise _Bail
            return self.type(merge_qualifiers(extra, quals))
        if kind == "T":
            self.simple = False
            return apply_qualifiers(_NULLPTR, quals)
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
        # Read whatever is printed, so a run omitting `__ptr64` still refuses "PE8B@@".
        has_restrict = self.eat("I")
        has_unaligned = self.eat("F")
        modified = has_ptr64 or has_restrict or has_unaligned
        restrict = self.options.keyword("__restrict") if has_restrict else ""
        if restrict:
            own_quals = (*own_quals, restrict)
        # "int const __unaligned *"
        spelled_unaligned = self.options.keyword("__unaligned") if has_unaligned else ""
        unaligned = (spelled_unaligned,) if spelled_unaligned else ()
        if self.peek() == "8":
            if token != "*":
                # C++ has no reference to member: `A8foo@@AEHH@Z` is not a type.
                raise _Bail
            self.pos += 1
            if modified:
                # no modifier before a function type: "P8B@@" is a name, "PE8B@@" is not
                raise _Bail
            return self.memberFunctionPointer(own_quals, token)
        if token == "*" and self.peek() in _MEMBER_DATA_QUALS:
            # C++ has no reference to member
            return self.memberDataPointer(own_quals, token, unaligned)
        if self.eat("6"):
            if modified:
                raise _Bail
            convention = self.callingConvention()
            self.in_pointee_return += 1
            try:
                returns = self.returnType()
            finally:
                self.in_pointee_return -= 1
            # parameters are whole-argument positions again, even inside a pointee
            saved_pointee_depth = self.pointee_depth
            self.pointee_depth = 0
            try:
                params = self.parameters()
            finally:
                self.pointee_depth = saved_pointee_depth
            noexcept_ = self.throwSpecification()
            self.simple = False
            return Indirection(token, own_quals, FunctionType(convention, params, returns, noexcept_))
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
            # The reference drops a qualified member pointer's qualifiers ("PQfoo@@SAPEAX" is
            # "void **foo::*"), and nothing says which is right.
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
        convention = self.callingConvention()
        self.in_pointee_return += 1
        try:
            returns = self.returnType()
        finally:
            self.in_pointee_return -= 1
        saved_pointee_depth = self.pointee_depth
        self.pointee_depth = 0
        try:
            params = self.parameters()
        finally:
            self.pointee_depth = saved_pointee_depth
        member_cv += self.throwSpecification()
        self.simple = False
        return Indirection(f"{owner}::{token}", own_quals, FunctionType(convention, params, returns, member_cv))

    def _conventionsResolved(self, text, base=None):
        """Spell, or drop, every marked calling convention in a rendered template name.

        The reference prints a function pointer's pointee under `OF_NoCallingConvention`,
        a flag that reaches everything inside the *return type*: a function type standing
        as a template argument there is `void (void)`, and the same argument in the
        pointer's parameter list is `void __cdecl(void)`. Its `memorizeIdentifier`
        renders a template name with the default flags and records the string, so a
        name reached again through a back-reference is spelled as if it stood nowhere
        in particular -- which still drops the conventions under pointers *inside* the
        name, because those pointers print their own return types the same way.

        This renders names to text as it reads them, so each mark carries how many
        pointed-to return types its function type stood inside, and a name is resolved
        twice. For display, at the outermost template name, only a mark at depth zero
        is spelled. For the record, at every template name, a mark is spelled when its
        depth is `base` -- the depth the name itself was read at -- and dropped when
        it is deeper, since that pointer is part of the name. 123 callback pointers in
        the LLVM 18.1.8 Windows build carry the first shape and thirteen names reach
        one of them again through a back-reference, from inside a return type and out.
        """
        if _CONVENTION_MARK not in text:
            return text
        parts = text.split(_CONVENTION_MARK)
        keep_at = 0 if base is None else base
        out = []
        # Marks come in pairs: even parts are text, odd are a depth, `:` and a convention.
        for at, part in enumerate(parts):
            if at % 2 == 0:
                out.append(part)
                continue
            depth, _, convention = part.partition(":")
            if int(depth) == keep_at:
                out.append(convention)
        return "".join(out)

    def functionTypeArgument(self):
        """A function type written as a template argument: "$$A6", or "$$A8" with a
        qualifier.

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
        convention = self.callingConvention()
        if self.template_depth:
            # Marked with the pointee-return depth, since the template name is spelled
            # twice; see `_conventionsResolved`. As a parameter itself it is spelled plainly.
            convention = f"{_CONVENTION_MARK}{self.in_pointee_return}:{convention}{_CONVENTION_MARK}"
        returns = self.returnType()
        params = self.parameters()
        member_cv += self.throwSpecification()
        return FunctionType(convention, params, returns, member_cv)

    def throwSpecification(self):
        """What ends a signature: `Z`, or `_E` for a `noexcept` one.

        `demangleThrowSpecification` takes one or the other and refuses anything else.
        Expecting the `Z` alone would refuse every `noexcept` function type -- which
        clang emits for `int (*)(int) noexcept`, an ordinary parameter -- and there is
        no other place the marker can go, since the parameter list has already been
        read.
        """
        if self.eat("_"):
            if not self.eat("E"):
                raise _Bail
            return " noexcept"
        self.expect("Z")
        return ""

    def callingConvention(self):
        """The convention code at the cursor, spelled the way `options` asks for.

        The code is validated whatever is going to be printed: a letter that names no
        convention is not a name, and stays not a name when the convention is being left
        out. Some letters name a convention the reference spells with nothing at all, and
        those arrive here as the empty string rather than as an absence.
        """
        convention = _CALLING_CONVENTIONS.get(self.take())
        if convention is None:
            raise _Bail
        return self.options.keyword(convention)

    def memberQualifiers(self):
        """What a member function may carry after its parameters: cv, __restrict, a ref.

        They are written modifier-first and spelled the other way round, so "GB" is
        " const &" and "IA" is " __restrict".

        All of it describes the implicit `this`, so `this_type` drops the whole group
        rather than a word of it -- and the reading is done first either way, because a
        group written in the wrong order is not a name whether or not it is printed.
        """
        restrict = ""
        reference = ""
        unaligned = ""
        # in this order and each at most once: "HH" and "IE" are not names
        rank = {"E": 0, "I": 1, "F": 2, "G": 3, "H": 3}
        written = -1
        while self.peek() in "EIGHF":
            char = self.take()
            if rank[char] <= written:
                raise _Bail
            written = rank[char]
            if char == "I":
                restrict = _spaced(self.options.keyword("__restrict"))
            elif char == "F":
                unaligned = _spaced(self.options.keyword("__unaligned"))
            elif char in ("G", "H"):
                reference = " &" if char == "G" else " &&"
        qualifier = _CV.get(self.take())
        if qualifier is None:
            raise _Bail
        if not self.options.this_type:
            return ""
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
            return _VOID_PARAMETERS
        params = []
        while True:
            if self.eof():
                raise _Bail
            if self.eat("@"):
                break
            if self.peek() == "Z":
                # `void f(...)`: clang writes `?f@@YAXZZ`.
                self.take()
                params.append(_ELLIPSIS)
                break
            if self.peek() in string.digits:
                index = int(self.take())
                if index >= len(self.arg_backrefs):
                    raise _Bail
                node = self.arg_backrefs[index]
                # spelled for the bound and then kept whole: see the docstring
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
        if self.text.startswith("?@", self.pos):
            return self.md5Name()
        # "$$J0" marks an extern "C" name that was mangled; `demangleFunctionEncoding`
        # consumes all four characters, so `$$J3` is not a name.
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
            # one storage class and the static's index in its function, omitted for the first
            self.expect("5")
            counted = self.templateInteger()
            if counted.startswith("-"):
                # The index is unsigned: the reference refuses `??_B@5?0`.
                raise _Bail
            if not self.nested and not self.eof():
                raise _Bail
            return Raw(name if counted == "0" else f"{name}{{{counted}}}")
        if self.eof():
            raise _Bail
        char = self.peek()
        if char == "$" and self.text.startswith("$$J0", self.pos):
            self.pos += 4
            extern_c = 'extern "C" '
            char = self.peek()
            # llvm-undname refuses a data-storage letter after `$$J0`
            # (`?overloaded_fn@@$$J04HA`).
            if char in _DATA_ACCESS:
                raise _Bail
        if char == "9":
            # a name with no signature at all: the linkage is what is being spelled
            self.take()
            if not self.nested and not self.eof():
                raise _Bail
            return Raw(f'extern "C" {name}')
        if (special_form == "data") != (char in "67"):
            raise _Bail
        if self.requires_signature and char not in "Y$" and char not in _FUNCTION_ACCESS:
            # this one runs code: a signature, never storage
            raise _Bail
        if char in "67":
            self.take()
            qualifier = _CV.get(self.take())
            if qualifier is None:
                raise _Bail
            # the base it is the table for: "??_7A@B@@6BC@D@@@" is B::A's table for D::C
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
            # a pointer into a class spells its storage the long way, below
            points_into_class = declared.kind == "indirection" and declared.points_into_class
            # `demanglePointerExtQualifiers`: optional `E`, `I`, `F`, in that order, each
            # at most once, and only where something is pointed at: "?s@@3HEA" is not a name.
            ptr64 = self.eat("E")
            has_restrict = self.eat("I")
            has_unaligned = self.eat("F")
            if (ptr64 or has_restrict or has_unaligned) and declared.kind != "indirection":
                raise _Bail
            # Spelled as this run spells keywords; the bail above turns on what was read.
            spelled_restrict = self.options.keyword("__restrict") if has_restrict else ""
            restrict = (spelled_restrict,) if spelled_restrict else ()
            spelled_unaligned = self.options.keyword("__unaligned") if has_unaligned else ""
            unaligned = (spelled_unaligned,) if spelled_unaligned else ()
            trailing = self.take()
            if trailing in _MEMBER_DATA_QUALS:
                # repeats the member's qualifier and names the class by back-reference:
                # "?m@@3PQfoo@@HR1@" is "int const foo::*m"
                member_quals = _MEMBER_DATA_QUALS[trailing]
                self.nameFragment(False)
                self.expect("@")
            elif trailing in _CV_QUALS and not points_into_class:
                member_quals = _CV_QUALS[trailing]
            else:
                raise _Bail
            if not self.nested and not self.eof():
                raise _Bail
            if unaligned:
                # `?h3@@3QEIAHFA` is `int __unaligned *const __restrict h3`.
                declared = qualify_declared(declared, unaligned)
            if restrict and restrict[0] not in declared.qualifiers:
                # qualifies the pointer, written once: "?h3@@3QIAHIA" is "int *const
                # __restrict"
                declared = Indirection(declared.sigil, declared.qualifiers + restrict, declared.inner)
            if member_quals and is_member_function_pointer(declared):
                # a member function's qualifier follows its parameters, in the group
                # `this_type` drops
                function = declared.inner
                trailing_cv = "" if not self.options.this_type else "".join(f" {qual}" for qual in member_quals)
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
            # rendered only to enforce the output bound
            self.rendered(declared, name)
            access, storage = _DATA_ACCESS_PARTS[char]
            return Declaration("", Name(name), declared, "", access, storage)
        return prefixed(extern_c, self.function(name, has_no_return_type, special_form == "vcall"))

    def stringLiteral(self):
        """The literal a "??_C" name stands for, spelled the way the reference spells it.

        The length counts the terminator, the eight characters after it are a hash of the
        bytes, and the bytes themselves are written plainly or as an escape - a digit for
        one of ten punctuation characters, or "$" and two nibbles for any byte at all.

        Only 32 bytes are ever written, however long the string is, so a longer one is
        truncated and the reference says so with a trailing "...". A "_1" name is wide,
        two bytes to the character and most significant first; a "_0" name is narrow, and
        whether its bytes are one, two or four to the character is a guess -- the
        encoding does not say, and the reference guesses from the trailing and embedded
        nul bytes.
        """
        self.expect("@")
        self.expect("_")
        wide = self.take()
        if wide not in ("0", "1"):
            raise _Bail
        wide = wide == "1"
        length = int(self.templateInteger())
        if length < (2 if wide else 1):
            raise _Bail
        while not self.eat("@"):
            # the hash is not spelled
            self.take()
        raw = []
        while not self.eat("@"):
            # The reference refuses a narrow literal that overflows its buffer; so does this.
            if not wide and len(raw) >= _LITERAL_MAX_DECODED:
                raise _Bail
            raw.append(self._literalByte())
        if not self.nested and not self.eof():
            raise _Bail

        if wide:
            if length % 2 or len(raw) % 2:
                raise _Bail
            if len(raw) > length:
                # More characters than the declared length allows: refused, as LLVM main
                # does (18.1 answered `L"hell\0"`).
                raise _Bail
            truncated = length > _LITERAL_MAX_BYTES
            values = [(raw[at] << 8) | raw[at + 1] for at in range(0, len(raw), 2)]
            prefix = "L"
            # The terminator by the *declared* length (the encoder writes at most 32 bytes),
            # as the reference counts it down.
            terminator = length // 2 - 1
        else:
            truncated = length > len(raw)
            width = _guess_character_width(raw, length)
            if len(raw) % width:
                raise _Bail
            values = [int.from_bytes(bytes(raw[at : at + width]), "little") for at in range(0, len(raw), width)]
            prefix = {1: "", 2: "u", 4: "U"}[width]
            # a truncated narrow literal always drops the last character decoded
            terminator = len(values) - 1

        # a truncated string keeps every character, as the reference spells it
        if not truncated:
            # an index past the end drops nothing, as when the reference's countdown never
            # reaches the terminator
            values = values[:terminator] + values[terminator + 1 :]
        spelled = "".join(_escaped_literal_character(value) for value in values)
        return f'{prefix}"{spelled}"' + ("..." if truncated else "")

    def _literalByte(self):
        """One byte of a string literal: plain, a digit escape, or "$" and two nibbles."""
        char = self.take()
        if char != "?":
            return ord(char) & 0xFF
        marker = self.take()
        if marker == "$":
            high, low = self.take(), self.take()
            if not ("A" <= high <= "P" and "A" <= low <= "P"):
                raise _Bail
            return (ord(high) - 65) * 16 + ord(low) - 65
        if marker in _LITERAL_ESCAPES:
            return ord(_LITERAL_ESCAPES[marker])
        raise _Bail

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
        if is_vcall and code != "B":
            # `demangleVcallThunkNode` consumes `$B` only: a `??_9` with a vtordisp slot
            # is not a name.
            raise _Bail
        if code == "B":
            if not is_vcall:
                raise _Bail
            slot = self.templateInteger()
            # A literal `A` (`$B7DA` is not a name), then a calling convention: `$B7AE` is
            # a `__thiscall` vcall thunk, as every 32-bit build writes.
            self.expect("A")
            convention = self.callingConvention()
            if not self.nested and not self.eof():
                raise _Bail
            # `[thunk]: Base::`vcall'{8, {flat}}`
            if not self.options.calling_convention:
                convention = ""
            convention = f"{convention} " if convention else ""
            return Raw(f"[thunk]: {convention}{name}{{{slot}, {{flat}}}}")
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
        """The signature a vtordisp or vtordispex thunk carries, once its numbers are
        read."""
        self.member_cv = self.memberQualifiers()
        convention = self.callingConvention()
        returns = None if self.peek() == "@" and self.take() else self.returnType()
        params = self.parameters()
        self.member_cv += self.throwSpecification()
        if not self.nested and not self.eof():
            raise _Bail
        written = ", ".join(str(value) for value in displacements)
        if "\0conversion\0" in name:
            # A conversion operator's name is its return type:
            # `??BEDerived@@$4PPPPPPPM@A@EAAPEAXI@Z` is
            # `EDerived::operator void *`vtordisp{-4, 0}'`.
            if returns is None:
                raise _Bail
            name = name.replace("\0conversion\0", f"operator{self.conversion_arguments} {self.rendered(returns)}")
        spelled = f"{name}`{kind}{{{written}}}'"
        signature = FunctionType(convention, params, returns)
        if returns is not None:
            # enforces the output bound
            self.rendered(signature, spelled, self.member_cv)
        return Declaration("[thunk]: ", Name(spelled), signature, self.member_cv, f"{access}: ", "virtual ")

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
            # The reference prints the displacement as a 32-bit unsigned value, so a
            # negative one -- which no compiler writes -- is its two's complement:
            # `W?B@` is `adjustor{4294967295}` and `W?A@`, negative zero, `adjustor{0}`;
            # LLVM's main branch spells both the same way.
            thunk = f"`adjustor{{{int(self.templateInteger()) & 0xFFFFFFFF}}}'"
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
        convention = self.callingConvention()
        if has_no_return_type or self.peek() == "@":
            # an operator may leave the return slot empty, the way a constructor does; a
            # conversion operator may not, since its return is the type it converts to
            self.expect("@")
            returns = None
        else:
            returns = self.returnType()
        params = self.parameters()
        specification = self.throwSpecification()
        self.member_cv += specification
        if not self.nested and not self.eof():
            raise _Bail
        lead = ""
        access_text = ""
        storage = ""
        if thunk:
            lead = "[thunk]: "
            name = f"{name}{thunk}"
        if access:
            access_text = f"{access}: "
            if is_static:
                storage = "static "
            if is_virtual:
                storage = "virtual "
        if "\0conversion\0" in name:
            if returns is None:
                raise _Bail
            name = name.replace("\0conversion\0", f"operator{self.conversion_arguments} {self.rendered(returns)}")
        signature = FunctionType(convention, params, returns)
        # A free or static function carries no member qualifiers, but a `noexcept` is
        # not one of those: `?f@@YAXX_E` is `void __cdecl f(void) noexcept` to the
        # reference, so the specification is kept with the qualifiers. No compiler
        # writes `_E` on a function's own symbol -- clang writes it only inside a
        # function *type*, which is where the corpora carry it -- so this is the
        # reference's reading of a name none writes, spelled as it spells it.
        trailing = self.member_cv if access and not is_static else specification
        if returns is not None:
            # as in thunkBody: completing the spelling is what refuses one past the bound
            self.rendered(signature, name, trailing)
        return Declaration(lead, Name(name), signature, trailing, access_text, storage)


def parse_msvc_symbol(name, limits=DEFAULT_LIMITS):
    """Return the tree behind a decorated name, or None when it is not fully understood.

    None rather than an exception: which names this demangler declines is a property of
    the grammar it covers, not an error condition, and every caller here has to answer
    for a name it cannot read anyway.

    A name carrying a control character is refused outright: a decorated name is read from a
    NUL-terminated string of source-legal characters and cannot hold one, and an expansion
    holding it would travel into the report as a symbol name. The identifier is copied into
    the answer verbatim, so testing the input is what keeps the answer clean.
    """
    try:
        return parse_msvc_symbol_strict(name, limits)
    except _LimitHit:
        # This entry point answers None for every name it cannot hand back a tree for,
        # whatever the reason. `parse_msvc_symbol_strict` is where a caller that wants to
        # know a *bound* was the reason goes.
        return None


def parse_msvc_symbol_strict(name, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
    """As `parse_msvc_symbol`, but raises `_LimitHit` when a bound stopped the parse.

    The plugin uses this one, so that `demangle_strict()` can report `LimitExceeded`
    rather than claiming the name is unreadable -- it may be perfectly well formed and
    merely larger than this caller allowed.
    """
    if not name or not name.startswith("?"):
        return None
    if _CONTROL_CHARACTER.search(name) is not None:
        return None
    try:
        return _Demangler(name, limits, options).parse()
    except _Bail:
        return None
    except RecursionError as error:
        # The interpreter's stack ran out before `max_depth` did: the same fact about the
        # name, so the same report, as `api._depth_exceeded` gives the other schemes.
        raise _LimitHit("recursion depth", limits.max_depth) from error


def parse_msvc_type(name, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
    """The tree behind a bare *type* encoding -- `PEAX`, `?AVFoo@@`, `.PEAX` -- or None.

    What an RTTI descriptor and a vtable entry carry, and what `UnDecorateSymbolName`'s
    `UNDNAME_TYPE_ONLY` asks for. A leading `.` is accepted and dropped: the linker writes
    one on a type descriptor's symbol, and the encoding after it is the type itself.
    """
    if not name:
        return None
    if _CONTROL_CHARACTER.search(name) is not None:
        return None
    if name.startswith("."):
        name = name[1:]
    if not name:
        return None
    try:
        demangler = _Demangler(name, limits, options)
        # There is no symbol here to name itself, so the one allowance made for a symbol's
        # own name is not made: the class this type names may not be an operator, and a
        # template standing as it is recorded for back-references rather than skipped.
        # Without this `P6AXV?$A@H@@V0@@Z` refuses on its own and reads inside
        # `??_R0P6AXV?$A@H@@V0@@Z@8`, which is the same type either way.
        demangler.at_symbol_name = False
        # `returnType` rather than `type`: this position is the one that may carry a
        # qualifier group of its own, and `?A` -- the unqualified case, not an absent one
        # -- is how every class type is written here. `??_R0?AVFoo@@@8` reads its type the
        # same way.
        tree = demangler.returnType()
    except _Bail:
        return None
    except RecursionError as error:
        raise _LimitHit("recursion depth", limits.max_depth) from error
    if demangler.pos != len(demangler.text):
        return None
    return tree


@lru_cache(maxsize=4096)
def demangle_msvc_symbol(name, limits=DEFAULT_LIMITS):
    """Return a readable C++ name, or the original when it is not fully understood.

    Cached, because this is the call a symbol table makes hundreds of thousands of times
    and one binary names the same type over and over. The tree is not cached with it: a
    caller that wants structure asks for it directly and is not repeating itself the way
    a caller labelling symbols is.
    """
    tree = parse_msvc_symbol(name, limits)
    if tree is None:
        return name
    try:
        return render(tree)
    except RecursionError:
        return name
