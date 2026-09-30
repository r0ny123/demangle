"""Reading D mangled names.

The grammar is from the D ABI specification, dlang.org/spec/abi.html, and the spelling is
checked against GNU binutils' D demangler (`c++filt --format=dlang`) over every `_D`
symbol in the shipped `libgphobos` and `libgdruntime` -- about 19,000 names, which is the
same methodology the C++ schemes were built on and for the same reason: a hand-written
corpus covers the constructs someone thought of.

The shape of a name:

    MangledName    ::= "_D" QualifiedName Type
    QualifiedName  ::= SymbolFunctionName+
    SymbolFunctionName ::= SymbolName
                         | SymbolName TypeFunctionNoReturn
                         | SymbolName "M" TypeModifiers? TypeFunctionNoReturn
    SymbolName     ::= LName | TemplateInstanceName | IdentifierBackRef | "0"
    LName          ::= Number Name

So a name is a dotted path, and what follows the path is the declared thing's own type.
Two details make that harder than it reads.

**Back references.** `Q` followed by a base-26 number names an earlier identifier or type
*by distance*, counted backwards from where the `Q` itself begins. The number is written
with upper-case letters for every digit but the last, which is lower case -- so `Qz` is
26 and `QBc` is 55. Resolving one means re-parsing at the referenced offset, which is why
the parser carries its whole input rather than consuming a stream.

**Where the path stops.** Nothing marks the last component. A component is followed by its
own function type when it is a *scope* -- a function containing the symbol -- and by the
symbol's type when it is the last. The rule that works, and that GNU's demangler uses too,
is positional: keep reading components while the next byte can open one.

What is *not* printed is the return type. The reference omits it -- `get() const` for a
method returning `int` -- and this follows, because a demangler nobody's output matches is
of no use to a tool showing symbols beside those of other demanglers.
"""

import re
import string

from ...core.limits import DEFAULT_LIMITS

__all__ = ["DSymbol", "parse_d_symbol"]

#: A set, not a string: `"" in string.digits` is True, and `peek()` returns "" at the end.
DIGITS = frozenset(string.digits)

#: A compiler-generated anonymous scope, which the reference leaves out of the path.
_ANONYMOUS = re.compile(r"__S\d+")

#: A real literal's significand, passed through in the upper case it is written in.
HEX_DIGITS = frozenset(string.hexdigits)

#: Basic types, from the ABI's Type production.
BASIC_TYPES = {
    "v": "void",
    "b": "bool",
    "g": "byte",
    "h": "ubyte",
    "s": "short",
    "t": "ushort",
    "i": "int",
    "k": "uint",
    "l": "long",
    "m": "ulong",
    "f": "float",
    "d": "double",
    "e": "real",
    "o": "ifloat",
    "p": "idouble",
    "j": "ireal",
    "q": "cfloat",
    "r": "cdouble",
    "c": "creal",
    "a": "char",
    "u": "wchar",
    "w": "dchar",
    "n": "typeof(null)",
}

#: Two-character basic types, all introduced by `z`.
WIDE_BASIC_TYPES = {"zi": "cent", "zk": "ucent"}

#: Spelled in the order encountered, which is the order the compiler emits.
FUNCTION_ATTRIBUTES = {
    "Na": "pure",
    "Nb": "nothrow",
    "Nc": "ref",
    "Nd": "@property",
    "Ne": "@trusted",
    "Nf": "@safe",
    "Ni": "@nogc",
    "Nj": "return",
    "Nl": "scope",
    "Nm": "@live",
}

#: `Nk` is two characters and must be tested before `N` is mistaken for anything else.
PARAMETER_STORAGE = {"I": "in", "J": "out", "K": "ref", "L": "lazy", "M": "scope"}

#: The D convention is the default and is spelled with nothing.
CALLING_CONVENTIONS = {
    "F": "",
    "U": "extern(C) ",
    "W": "extern(Windows) ",
    "R": "extern(C++) ",
    "V": "extern(Pascal) ",
    "Y": "extern(Objective-C) ",
}

#: What the reference writes after the closing quote.
STRING_SUFFIX = {"a": "", "w": "w", "d": "d"}

INTEGER_SUFFIX = {"uint": "u", "long": "L", "ulong": "uL", "ubyte": "u", "ushort": "u"}

#: Type modifiers, innermost first as the ABI writes them.
TYPE_MODIFIERS = {"x": "const", "y": "immutable", "O": "shared", "Ng": "inout"}

#: Renamed as written in source. `__xdtor` and `__xpostblit` are not renamed; for
#: `__postblit` see `qualified_name`.
RENAMED_COMPONENTS = {
    "__ctor": "this",
    "__dtor": "~this",
}
#: Compiler-generated last components, spelled as a prefix to the path:
#: `initializer for rt.util.utility._Complex`.
SPECIAL_COMPONENTS = {
    "__init": "initializer for ",
    "__vtbl": "vtable for ",
    "__Class": "ClassInfo for ",
    "__ModuleInfo": "ModuleInfo for ",
    "__Interface": "Interface for ",
}


#: Measured with `c++filt --format=dlang` over every byte: not `\a`, `\b`, `"` or `\\`.
_STRING_ESCAPES = {0x09: "\\t", 0x0A: "\\n", 0x0B: "\\v", 0x0C: "\\f", 0x0D: "\\r"}

#: Prefix and zero-padded width for a character the reference cannot print.
_CHARACTER_ESCAPES = {"char": ("\\x", 2), "wchar": ("\\u", 4), "dchar": ("\\U", 8)}


def _printable(code):
    """Whether the reference writes this byte as itself. Printable ASCII, and no more."""
    return 0x20 <= code <= 0x7E


def _escaped(code, digits):
    """One character of a string literal, as the reference spells it.

    An unprintable byte is written `\\x` and *the two digits the name carried*:
    `dlang_parse_string` copies them out of the input rather than formatting the value,
    so `B2` stays upper-case and `b2` stays lower. Formatting instead lower-cased every
    one of them, which no compiler makes visible -- dmd writes its hex in lower case --
    but which is a different string from the one the name spells.
    """
    if _printable(code):
        return chr(code)
    escape = _STRING_ESCAPES.get(code)
    return escape if escape is not None else f"\\x{digits}"


def _character(kind, code):
    """A character *literal*, which the reference spells differently from a string.

    None of the named escapes appear here -- a newline in a `char` literal is `'\\x0a'`,
    not `'\\n'` -- and only a `char` is ever written as itself: a printable `wchar` is
    still `'\\u0041'`.

    The width is a minimum and not a cap: the reference writes `'\\x100'` for a `char`
    mangled as 256, and refuses a value that will not fit in 32 bits. Integer literals
    carry no such limit -- `Vmi18446744073709551616` is answered -- so this is the
    character path's own.
    """
    if code > 0xFFFFFFFF:
        raise DemangleFailure("character literal out of range")
    if kind == "char" and _printable(code):
        return f"'{chr(code)}'"
    prefix, width = _CHARACTER_ESCAPES[kind]
    return f"'{prefix}{code:0{width}x}'"


class _Exhausted(Exception):
    """The work budget ran out. Deliberately *not* a `DemangleFailure`.

    This parser backtracks: `qualified_name` and `scope_type` both try a production and
    catch `DemangleFailure` to mean "that was not it, put the cursor back". A budget that
    reported exhaustion as a `DemangleFailure` was therefore caught by the very handlers
    it was meant to stop -- the parse backtracked, tried again, and made no progress
    towards finishing. Raising something those handlers do not catch is what makes the
    budget a bound rather than a suggestion.
    """


class DemangleFailure(Exception):
    """This name is not one this parser can read."""


class _Reader:
    """Position in the mangled name, with the whole string kept for back references."""

    __slots__ = ("depth", "end", "pos", "text")

    MAX_DEPTH = 200

    #: Fifty times any real constant: a guard against a name built to be long.
    MAX_VALUE_DIGITS = 1024

    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.end = len(text)
        self.depth = 0

    def peek(self, offset=0):
        at = self.pos + offset
        return self.text[at] if at < self.end else ""

    def take(self):
        if self.pos >= self.end:
            raise DemangleFailure("unexpected end of name")
        char = self.text[self.pos]
        self.pos += 1
        return char

    def eat(self, char):
        if self.pos < self.end and self.text[self.pos] == char:
            self.pos += 1
            return True
        return False

    def starts_with(self, prefix):
        return self.text.startswith(prefix, self.pos)

    def number(self, bounded=True):
        """Read a decimal run.

        `bounded` caps the digit count, which is right for a *length prefix* -- one longer
        than the name itself is malformed, not enormous -- and wrong for a *value*: a
        `ulong` template argument such as `Vmi3988292384` is ten digits and perfectly
        ordinary. Capping both refused every symbol carrying a large constant.
        """
        start = self.pos
        while self.pos < self.end and self.text[self.pos] in DIGITS:
            self.pos += 1
        if self.pos == start:
            raise DemangleFailure("expected a number")
        if bounded and self.pos - start > 9:
            raise DemangleFailure("implausible length prefix")
        if self.pos - start > self.MAX_VALUE_DIGITS:
            # Also keeps the interpreter's own int-conversion limit from reaching the caller.
            raise DemangleFailure("implausible numeric literal")
        return int(self.text[start : self.pos])

    def digits(self):
        """A decimal run, as both its value and the characters it was written with.

        `dlang_parse_integer` appends the characters it read rather than the number they
        spell, so a leading zero survives into the spelling: `Vki024` is `024u`, and
        normalising it to `24u` spells a literal the name did not carry. The value is
        still needed for the kinds that are *not* echoed -- a `bool` reads `true` and a
        `char` reads as a quoted character -- so both come back.
        """
        start = self.pos
        return self.number(bounded=False), self.text[start : self.pos]


class DSymbol:
    """One parsed D symbol: its dotted path, and the type of what it declares."""

    __slots__ = ("generated", "path", "raw", "suffix", "text")

    def __init__(self, raw, path, text, suffix="", generated=""):
        self.raw = raw
        self.generated = generated
        self.path = path
        self.text = text
        self.suffix = suffix


def _back_reference_number(reader):
    """Read a base-26 back-reference distance.

    Upper case for every digit but the last, lower case for the last, which is what makes
    the end of the number self-delimiting. The digits are **zero-based**: `a` is 0, `m` is
    12, `Ba` is 26. Verified rather than assumed -- in
    `_D2rt4util7utility__T8_ComplexTdZQm6__initZ` the `Q` sits at offset 33 and must
    resolve to the `8_Complex` at offset 21, a distance of 12, which is `m` counted from
    zero and not from one.
    """
    value = 0
    while True:
        char = reader.take()
        if "A" <= char <= "Z":
            value = value * 26 + (ord(char) - ord("A"))
        elif "a" <= char <= "z":
            return value * 26 + (ord(char) - ord("a"))
        else:
            raise DemangleFailure(f"malformed back reference digit {char!r}")


class _Parser:
    """Recursive descent over one mangled name."""

    def __init__(
        self,
        text,
        max_substitutions=DEFAULT_LIMITS.max_substitutions,
        max_output=DEFAULT_LIMITS.max_output,
    ):
        self.reader = _Reader(text)
        # A symbol as a template argument omits its scopes' qualifiers:
        # `FilterResult!(bitsSet(), ...)` where the path reads `initializer() const`.
        self._in_symbol_argument = False
        # Whether a type's qualified name, or a `_D`-prefixed symbol argument, is being read;
        # see `scope_type`.
        self._in_type_name = False
        self._suffix_modifiers = True
        self._trailing_had_attributes = True
        # Back references followed; each re-parses a position, so work is exponential in
        # nesting. `c++filt` also refuses libiberty's 441-`Q` `formattedWrite` vector.
        self._follows = max_substitutions
        # Productions entered: the one bound backtracking, lookahead and back references
        # cannot sidestep. Linear in a real symbol, so 64x is ample headroom.
        self._work = 64 * len(text) + 4096
        # Consulted while building: assembling an oversized string is where the time goes.
        self._max_output = max_output
        # Back-reference results by position, production and the state that changes them.
        self._resolved = {}
        # A type back reference may only point before this, as `last_backref` in
        # `dlang_type_backref`: it keeps a chain of them finite.
        self._last_backref = len(self.reader.text)
        # LName spans; a back reference strictly inside one is refused (no compiler writes one).
        self._lname_spans = []
        #: How many spans cover each position strictly inside one, so `_is_inside_lname`
        #: need not scan them. A count because the spans overlap.
        self._lname_cover = {}
        self._backref_inside_lname = False
        # Whether a whole symbol name starts at a position -- see `_symbol_name_follows`.
        self._starts_symbol = {}

    def parse(self):
        reader = self.reader
        if not reader.starts_with("_D"):
            raise DemangleFailure("not a D mangled name")
        reader.pos += 2
        path = self.qualified_name()
        if not path:
            raise DemangleFailure("no qualified name")
        prefix = ""
        if path and path[-1] in SPECIAL_COMPONENTS and reader.peek() == "Z":
            # `dlang_parse_mangle`: an artificial symbol ends with a mandatory `Z`;
            # `_D3foo6__vtblFZv` is the ordinary function `foo.__vtbl()`.
            prefix = SPECIAL_COMPONENTS[path.pop()]
            reader.pos += 1
            if reader.pos != reader.end:
                raise DemangleFailure("unconsumed input after a generated symbol")
            # `_D6__initZ`: the reference writes `initializer for`, no trailing space.
            return prefix + ".".join(path) if path else prefix.rstrip()
        anonymous_last = self._last_component_anonymous
        self._trailing_had_attributes = True
        trailing = self.trailing_type()
        if anonymous_last:
            # The type belongs to the anonymous component, which is not spelled:
            # `c++filt` writes `Mutex.unlock` for `_D4core4sync5mutex5Mutex6unlock0FNeZv`.
            trailing = ""
        return prefix + ".".join(path) + trailing

    def _opens_symbol_name(self):
        char = self.reader.peek()
        if char in DIGITS or char == "Q":
            return True
        # A template instance follows the name it qualifies directly, with no length
        # prefix of its own: `7utility__T8_ComplexTdZ`.
        return self.reader.starts_with("__T") or self.reader.starts_with("__U")

    def qualified_name(self):
        parts = []
        # Whether the *last* component read was an anonymous `0`. See `parse`.
        self._last_component_anonymous = False
        while self._opens_symbol_name():
            saved, saved_depth = self.reader.pos, self.reader.depth
            # A literal `0` skips the argument consumption (`dlang_parse_qualified`
            # `continue`s); a back reference resolving to one does not. See `parse`.
            anonymous = self.reader.peek() == "0"
            try:
                component = self.symbol_name()
            except DemangleFailure:
                # A `Q` not pointing at an identifier is a type back reference: the
                # symbol's own type. Nothing else is put back: `dlang_parse_qualified`
                # has no second reading, so `VE3foo3bar42Z` is refused.
                if self.reader.text[saved] != "Q" or self._back_reference_targets_identifier(saved):
                    raise
                self.reader.pos, self.reader.depth = saved, saved_depth
                break
            if component == "__postblit" and self.reader.starts_with("MFZ"):
                # `dlang_lname` matches `__postblitMFZ` as one token and writes `this(this)`;
                # no other shape is renamed.
                self.reader.pos += 3
                self._last_component_anonymous = False
                parts.append("this(this)")
                continue
            if self.reader.text[saved] in DIGITS and _ANONYMOUS.fullmatch(component):
                # `__S<n>`, a compiler scope, is written as nothing, and `dlang_identifier`
                # reads the next identifier at once: `_D8demangle4mainFZ4__S1xi` is refused.
                # Via a back reference, `dlang_symbol_backref` spells it as it stands.
                self._last_component_anonymous = False
                if not self._opens_symbol_name() or self.reader.peek() == "0":
                    # a `0` here is a refused length, not the anonymous component
                    raise DemangleFailure("a compiler scope with nothing after it")
                continue
            if anonymous:
                # Skipped whole: a following function type is the symbol's own, so
                # `_D1a0FZ1bi` is refused. See `parse` for the type.
                self._last_component_anonymous = True
                continue
            # `enclosing(params).inner`
            spelled = self._spelled_component(component) + self.scope_type()
            # A back reference to an anonymous component keeps its slot: `_D1a0Qb1ci` is `a..c`.
            self._last_component_anonymous = False
            parts.append(spelled)
        return parts

    def symbol_name(self):
        work = self._work = self._work - 1
        if work < 0:
            raise _Exhausted
        reader = self.reader
        if reader.peek() == "Q":
            return self.identifier_back_reference()
        if reader.peek() == "0":
            reader.pos += 1
            return ""
        if reader.starts_with("__T") or reader.starts_with("__U"):
            return self.template_instance()
        lname_start = reader.pos
        length = reader.number()
        start = reader.pos
        if start + length > reader.end:
            raise DemangleFailure("identifier runs past the end of the name")
        # A length-prefixed `__T...Z` is a template instance (`_D8demangle11__T4testTaZv`
        # is `demangle.test!(char)`). Like `dlang_parse_template`, the body is read
        # against all that remains and its length checked afterwards, not bounded first.
        text = reader.text[start : start + length]
        # `dlang_identifier` needs `len >= 5`: `_D4main3__TFZv` is `main.__T()`.
        if text.startswith(("__T", "__U")) and length >= 5:
            spelled = self.template_instance()
            if reader.pos != start + length:
                # the reference refuses a malformed template rather than printing it back
                raise DemangleFailure("malformed template instance")
            return spelled
        if any(char.isascii() and not (char.isalnum() or char == "_") for char in text):
            # `_D1aE3foo6En961*` is refused. `tools/mutate.py --seed 17`.
            raise DemangleFailure("identifier is not a D name")
        reader.pos = start + length
        self._remember_lname(lname_start, reader.pos)
        return text

    @staticmethod
    def _spelled_component(name):
        return RENAMED_COMPONENTS.get(name, name)

    def template_instance(self):
        """`__T <LName> <TemplateArgs>* Z`, spelled `name!(argument, ...)`."""
        reader = self.reader
        reader.pos += 3
        if reader.peek() == "0":
            # `dlang_parse_template` refuses `_D5__T0Zv`.
            raise DemangleFailure("a template instance with no name")
        name = self._spelled_component(self.symbol_name())
        arguments = []
        while not reader.eat("Z"):
            if reader.pos >= reader.end:
                raise DemangleFailure("unterminated template instance")
            reader.eat("H")
            arguments.append(self.template_argument())
        return self._cap(f"{name}!({', '.join(arguments)})")

    def template_argument(self):
        reader = self.reader
        marker = reader.take()
        if marker == "T":
            return self.type_()
        if marker == "S":
            # A whole qualified name: `SQBaQz3run` is `std.parallelism.run`. It may
            # carry `_D`, and newer compilers write a length first (`S116symbol3foo`),
            # which then bounds the argument.
            bounded = self._symbol_argument_bound()
            if reader.starts_with("_D"):
                # Path and type (`S_DQBg3net4curl7CurlAPI7_handlePv`); only the path is
                # printed. `dlang_symbol_name_p` must accept what follows the `_D`, else
                # it is a length and the name is refused (`S_DaZv`).
                after = reader.text[reader.pos + 2 : reader.pos + 5]
                # A `Q` counts only if it points at a digit (`dlang_symbol_name_p`):
                # `S_DQiZv` is refused.
                named = after[:1] in DIGITS or after in ("__T", "__U")
                if after[:1] == "Q":
                    named = self._back_reference_targets_identifier(reader.pos + 2)
                if not named:
                    raise DemangleFailure("a symbol argument whose `_D` is followed by no name")
                spelled = self.mangled_symbol()
                if bounded is not None:
                    reader.pos = bounded
                return self._cap(spelled)
            outer, outer_suffix = self._in_symbol_argument, self._suffix_modifiers
            self._in_symbol_argument, self._suffix_modifiers = True, False
            try:
                spelled = ".".join(self.qualified_name())
            finally:
                self._in_symbol_argument, self._suffix_modifiers = outer, outer_suffix
            if bounded is not None:
                reader.pos = bounded
            if not spelled:
                # An anonymous-only argument still takes a slot, so it is refused.
                raise DemangleFailure("a symbol argument with no name")
            return self._cap(spelled)
        if marker == "V":
            # Kept to tell `int[int]` from a static array of one.
            code = self._type_code()
            kind = self.type_()
            return self.template_value(kind, code=code)
        # `TemplateArgX` is `T`, `V`, `S` or `X`; `dlang_template_args` refuses a bare
        # symbol name, and accepting one rescued malformed names into plausible ones.
        if marker == "X":
            # Externally mangled: a length and that many characters, kept as they stand.
            length = reader.number()
            start = reader.pos
            reader.pos = min(start + length, reader.end)
            return reader.text[start : reader.pos]
        raise DemangleFailure(f"unknown template argument marker {marker!r}")

    def mangled_symbol(self):
        """A whole `_D`-prefixed symbol appearing where a name or a value was expected.

        Its own type is spelled too where it is a function: the reference writes
        `regexImpl(const(char)[], ...)` for an argument naming one, and
        `mangle.__lambda71()` for a function literal in a struct value.
        """
        reader = self.reader
        if not reader.starts_with("_D"):
            raise DemangleFailure("expected a mangled symbol")
        reader.pos += 2
        outer = self._in_symbol_argument
        outer_suffix = self._suffix_modifiers
        self._in_symbol_argument = True
        self._suffix_modifiers = True
        try:
            path = ".".join(self.qualified_name())
            # As in `parse`: an anonymous last component's type is not spelled
            # (`foo.bar` for `_D3foo3bar0FNbmZm`).
            anonymous_last = self._last_component_anonymous
            trailing = self.trailing_type()
            return path if anonymous_last else path + trailing
        finally:
            self._suffix_modifiers = outer_suffix
            self._in_symbol_argument = outer

    def template_value(self, kind, suffix=True, code=""):
        """A value argument. Only the forms a compiler emits are modelled."""
        reader = self.reader
        char = reader.peek()
        if char == "n":
            reader.pos += 1
            return "null"
        if char == "i":
            reader.pos += 1
            return self._integer_literal(kind, *reader.digits(), suffix=suffix)
        if char == "N":
            reader.pos += 1
            return self._integer_literal(kind, *reader.digits(), negative=True, suffix=suffix)
        if char in DIGITS:
            return self._integer_literal(kind, *reader.digits(), suffix=suffix)
        if char in STRING_SUFFIX:
            return self.string_literal()
        if char == "e":
            reader.pos += 1
            return self.real_literal()
        if char == "c":
            # A complex literal: `c<real>c<imaginary>`, spelled `<real>+<imaginary>i`.
            reader.pos += 1
            real = self.real_literal()
            if not reader.eat("c"):
                raise DemangleFailure("complex literal without its imaginary part")
            return f"{real}+{self.real_literal()}i"
        if char == "A":
            # `A <count> <value>...`: `VAmA2i104i1281` is `[104, 1281]`; an associative
            # array's values come in pairs, `[1:2, 3:4]`.
            reader.pos += 1
            count = reader.number()
            if code == "H":
                pairs = (
                    f"{self.template_value(None, suffix=False)}:{self.template_value(None, suffix=False)}"
                    for _ in range(count)
                )
                return "[" + ", ".join(pairs) + "]"
            # `dlang_parse_arrayliteral` reads elements untyped: `[0, 1]` for a `bool[]`.
            return "[" + ", ".join(self.template_value(None, suffix=False) for _ in range(count)) + "]"
        if char == "f":
            # A function literal: a whole mangled symbol, as a lambda in a struct field.
            reader.pos += 1
            return self.mangled_symbol()
        if char == "S":
            # A struct literal: `S <count>` field values, each untyped.
            reader.pos += 1
            count = reader.number()
            fields = ", ".join(self.template_value(None, suffix=False) for _ in range(count))
            return f"{kind or ''}({fields})"
        raise DemangleFailure(f"unknown template value {char!r}")

    def real_literal(self):
        """`NAN | INF | NINF | [N] <hexdigit> <hexdigits> P [N] <digits>`.

        Written the way D source writes a hexadecimal float, with the point after the
        first digit: `0A8P6` is `0x0.A8p6` and `A8P2` is `0xA.8p2`.
        """
        reader = self.reader
        for mangled, spelled in (("NAN", "NaN"), ("INF", "Inf"), ("NINF", "-Inf")):
            if reader.starts_with(mangled):
                reader.pos += len(mangled)
                return spelled
        sign = "-" if reader.eat("N") else ""
        digits = self._hex_digits()
        if not digits:
            raise DemangleFailure("real literal without a significand")
        if not reader.eat("P"):
            raise DemangleFailure("real literal without an exponent")
        exponent = "-" if reader.eat("N") else ""
        start = reader.pos
        while reader.pos < reader.end and reader.text[reader.pos] in DIGITS:
            reader.pos += 1
        if reader.pos == start:
            raise DemangleFailure("real literal without an exponent")
        return f"{sign}0x{digits[0]}.{digits[1:]}p{exponent}{reader.text[start : reader.pos]}"

    def _hex_digits(self):
        reader = self.reader
        start = reader.pos
        while reader.pos < reader.end and reader.text[reader.pos] in HEX_DIGITS:
            reader.pos += 1
        return reader.text[start : reader.pos]

    def _symbol_argument_bound(self):
        """Where a length-prefixed symbol template argument ends, or None if unprefixed.

        Newer compilers write the argument's length first, and the digits run straight
        into the name -- which is itself length-prefixed. `S116symbol3foo` is `S`, the
        length `11`, and eleven characters of `6symbol3foo`; read greedily the length
        comes out as `116`, which does not fit.

        So the split is chosen by what parses rather than by how many digits there are.
        Longest first, because a longer length is the more specific reading, and the
        candidate is accepted only if the region it delimits is exactly a qualified name
        -- no leftovers, nothing running on into the arguments that follow.
        """
        reader = self.reader
        start = reader.pos
        digits = start
        while digits < reader.end and reader.text[digits] in DIGITS:
            digits += 1
        if digits == start:
            return None
        for stop in range(digits, start, -1):
            length = int(reader.text[start:stop])
            end = stop + length
            if end > reader.end:
                continue
            if self._is_symbol_argument(stop, end):
                reader.pos = stop
                return end
        # No split works: the older `S <LName>` form, as `S6symbol`.
        return None

    def _is_symbol_argument(self, start, end):
        """Whether `[start, end)` is exactly one qualified name, optionally `_D`-prefixed."""
        reader = self.reader
        saved, saved_end, saved_depth = reader.pos, reader.end, reader.depth
        outer = self._in_symbol_argument
        try:
            reader.pos, reader.end = start, end
            self._in_symbol_argument = True
            # `dlang_template_symbol_param` parses the region only on a digit or `_D`,
            # so `S1i` is the old-form name `i`.
            prefixed = reader.starts_with("_D")
            if prefixed:
                reader.pos += 2
            elif reader.peek() not in DIGITS:
                return False
            self.qualified_name()
            if prefixed:
                # `dlang_parse_mangle` needs a type: `_D6symbol3foo3bar2Zv` is spelled as it stands.
                if reader.pos >= end:
                    return False
                if reader.peek() == "Z":
                    reader.pos += 1
                else:
                    self.trailing_type()
            # Unprefixed: a qualified name and nothing more. `tools/mutate.py --seed 15`.
            return reader.pos == end
        except (DemangleFailure, _Exhausted):
            return False
        finally:
            reader.pos, reader.end, reader.depth = saved, saved_end, saved_depth
            self._in_symbol_argument = outer

    def _cap(self, text):
        """Return `text`, unless it is already larger than the whole answer may be.

        Applied to what the recursive productions hand back, because a component larger
        than `max_output` guarantees a result larger than `max_output` -- and the point
        of saying so here rather than at the end is that here it has not yet been joined
        into something larger still.
        """
        if len(text) > self._max_output:
            raise _Exhausted
        return text

    def _symbol_name_follows(self, at):
        """Whether a whole symbol name starts at `at`. Memoised by position.

        The test is "read one and see", which means a full speculative parse whose result
        is then thrown away. `scope_type` runs it for every component of the path, and
        the region it reads contains scopes that run it again -- so without memoising, the same
        position is read over and over, and a long symbol such as one from
        `std.format.formattedWrite` takes seconds rather than milliseconds.

        Whether a symbol name starts at a position is a property of the position, so
        asking twice can only get the same answer. The one piece of parser state that
        changes what a position means is in the key.
        """
        key = (at, self._in_symbol_argument)
        answer = self._starts_symbol.get(key)
        if answer is None:
            reader = self.reader
            saved, saved_depth = reader.pos, reader.depth
            saved_spans = len(self._lname_spans)
            try:
                self.symbol_name()
                answer = True
            except DemangleFailure:
                answer = False
            finally:
                reader.pos, reader.depth = saved, saved_depth
                self._forget_lnames(saved_spans)
            self._starts_symbol[key] = answer
        return answer

    def _back_reference_target(self, at):
        """Where the `Q` at `at` points, or None if it points outside the name."""
        reader = self.reader
        saved = reader.pos
        reader.pos = at + 1
        try:
            distance = _back_reference_number(reader)
        except DemangleFailure:
            return None
        finally:
            reader.pos = saved
        target = at - distance
        return target if 0 <= target < at else None

    def _remember_lname(self, start, end):
        """Record an identifier's span, and mark the positions strictly inside it."""
        self._lname_spans.append((start, end))
        cover = self._lname_cover
        for position in range(start + 1, end):
            cover[position] = cover.get(position, 0) + 1

    def _forget_lnames(self, keep):
        """Drop every span recorded after the first `keep`, and unmark what they covered.

        The probe in `_symbol_name_follows` reads a whole symbol to find out whether one
        is there and then puts the cursor back; the spans it recorded on the way have to
        go back too, or a later back reference is refused for landing inside an
        identifier that was never really read.
        """
        spans = self._lname_spans
        if len(spans) == keep:
            return
        cover = self._lname_cover
        for start, end in spans[keep:]:
            for position in range(start + 1, end):
                depth = cover[position] - 1
                if depth:
                    cover[position] = depth
                else:
                    del cover[position]
        del spans[keep:]

    def _is_inside_lname(self, target):
        return target in self._lname_cover

    def _back_reference_targets_identifier(self, at):
        """`dlang_symbol_name_p` on a `Q` at `at`: whether it points at a digit."""
        target = self._back_reference_target(at)
        return target is not None and self.reader.text[target] in DIGITS and not self._is_inside_lname(target)

    def identifier_back_reference(self):
        reader = self.reader
        at = reader.pos
        reader.pos += 1
        distance = _back_reference_number(reader)
        # Measured from the `Q`, and a zero distance would point at the `Q` itself.
        target = at - distance
        if distance <= 0 or target < 0 or target >= at:
            raise DemangleFailure("back reference outside the name")
        if self._is_inside_lname(target):
            self._backref_inside_lname = True
            raise DemangleFailure("back reference targets inside an identifier")
        self._follows -= 1
        if self._follows < 0:
            raise _Exhausted
        key = (target, "identifier", self._in_symbol_argument)
        found = self._resolved.get(key)
        if found is not None:
            return found[0]
        saved = reader.pos
        reader.pos = target
        try:
            if reader.depth > _Reader.MAX_DEPTH:
                raise DemangleFailure("back reference recursion")
            reader.depth += 1
            try:
                if reader.peek() not in DIGITS:
                    # `dlang_symbol_backref` targets only a length-prefixed identifier, not
                    # a `__T` instance or another `Q`.
                    raise DemangleFailure("a back reference to something that is not an identifier")
                while reader.peek() == "0" and reader.text[reader.pos + 1 : reader.pos + 2] in DIGITS:
                    # `dlang_number` takes the whole digit run (`06289` is 6289); only a
                    # lone `0` is the empty identifier.
                    reader.pos += 1
                if reader.peek() == "0":
                    resolved = self.symbol_name()
                else:
                    # `dlang_lname`: spelled as it stands with only the structor renames, so
                    # `13__T4testThTuZ` is the identifier `__T4testThTuZ`.
                    length = reader.number()
                    start = reader.pos
                    if start + length > reader.end:
                        raise DemangleFailure("identifier runs past the end of the name")
                    reader.pos = start + length
                    resolved = self._spelled_component(reader.text[start : start + length])
            finally:
                reader.depth -= 1
        finally:
            reader.pos = saved
        self._resolved[key] = (resolved,)
        return resolved

    def scope_type(self):
        work = self._work = self._work - 1
        if work < 0:
            raise _Exhausted
        """Read this component's own function type if it is a scope, and spell it.

        Nothing marks a scope, so the only way to tell is to read a function type and see
        whether another path component follows it. Entirely speculative, therefore: every
        byte is put back when it does not, the leading `M` included. That `M` is why this
        cannot consume as it goes -- for the *last* component it introduces the symbol's
        own member-function type, and eating it leaves nothing for `trailing_type` to
        find.
        """
        reader = self.reader
        saved, saved_depth = reader.pos, reader.depth
        try:
            modifiers = []
            if reader.peek() == "M":
                reader.pos += 1
                # `dlang_parse_qualified` calls `dlang_type_modifiers` here too: `MxxF` is refused.
                modifiers = self.this_modifiers()
            if reader.peek() not in CALLING_CONVENTIONS:
                raise DemangleFailure("not a scope")
            _, attributes, parameters, _ = self.function_type(returns=False)
            if self._in_type_name or self._in_symbol_argument:
                # No trailing type here, so `dlang_parse_qualified` keeps the parameters
                # as the component's scope whenever they parse.
                if reader.pos >= reader.end:
                    raise DemangleFailure("not a scope")
            else:
                # A full symbol name, not a byte that could open one: `Qq` opens both kinds
                # of back reference (`rt_linkOption`).
                if not self._opens_symbol_name():
                    raise DemangleFailure("not a scope")
                after = reader.pos
                if not self._symbol_name_follows(after):
                    raise DemangleFailure("not a scope")
                reader.pos = after
        except DemangleFailure:
            reader.pos, reader.depth = saved, saved_depth
            return ""
        del saved_depth
        spelled = f"({', '.join(parameters)})"
        # `dlang_parse_qualified` writes `this` modifiers only via `dlang_parse_mangle`:
        # `main.S.bar().x` for `S4main1S3barMxFZ1x`.
        trailing = " ".join(modifiers) if self._suffix_modifiers else ""
        self._last_scope_had_attributes = bool(attributes)
        return self._cap(f"{spelled} {trailing}" if trailing else spelled)

    def type_modifiers(self):
        found = []
        reader = self.reader
        while True:
            if reader.starts_with("Ng"):
                reader.pos += 2
                found.append("inout")
            elif reader.peek() in ("x", "y", "O"):
                found.append(TYPE_MODIFIERS[reader.take()])
            else:
                return found

    def this_modifiers(self):
        """`dlang_type_modifiers`: the qualifiers on a `this` parameter or a delegate.

        Not the rule that governs the modifiers on a *type*, where each one wraps the
        next and `xx` is `const(const(int))`. Here `O` (shared) and `Ng` (inout) recurse
        and `x` (const) and `y` (immutable) `return`, so at most one of the last two
        appears and it comes last. `MOx` reads as `shared const`; `MxO`, `Mxx`, `Myy` and
        `Mxy` are refused, with the unread character left over. Reading the run the way a
        type reads it spelled `foo.bar() const const` for a symbol the reference will not
        read at all.
        """
        found = []
        reader = self.reader
        while True:
            if reader.starts_with("Ng"):
                reader.pos += 2
                found.append("inout")
            elif reader.peek() == "O":
                found.append(TYPE_MODIFIERS[reader.take()])
            elif reader.peek() in ("x", "y"):
                found.append(TYPE_MODIFIERS[reader.take()])
                return found
            else:
                return found

    def function_attributes(self):
        found = []
        reader = self.reader
        while reader.peek() == "N" and reader.text[reader.pos : reader.pos + 2] in FUNCTION_ATTRIBUTES:
            found.append(FUNCTION_ATTRIBUTES[reader.text[reader.pos : reader.pos + 2]])
            reader.pos += 2
        return found

    def function_type(self, returns=True):
        """`<CallConvention> <FuncAttrs> <Parameters> <Variadic> <ReturnType>`.

        `returns` is False for the `TypeFunctionNoReturn` a *scope* component carries: the
        grammar gives a scope's function no return type, and reading one anyway swallows
        the next component of the path.
        """
        reader = self.reader
        convention = reader.take()
        if convention not in CALLING_CONVENTIONS:
            raise DemangleFailure(f"unknown calling convention {convention!r}")
        attributes = self.function_attributes()
        parameters = []
        while True:
            char = reader.peek()
            if char == "Z":
                reader.pos += 1
                break
            if char in ("X", "Y"):
                # `X` is `f(T t...)`, `Y` is `f(T t, ...)`.
                reader.pos += 1
                if char == "X" and parameters:
                    parameters[-1] += "..."
                else:
                    parameters.append("...")
                break
            if not char:
                raise DemangleFailure("unterminated parameter list")
            parameters.append(self.parameter())
        spelled = self.type_() if returns else ""
        return CALLING_CONVENTIONS[convention], attributes, parameters, spelled

    def parameter(self):
        """`[M] [Nk] [I[K] | J | K | L] <Type>` -- a fixed sequence, not a set.

        `dlang_function_args` reads each of these once, in this order, then the type; `I`
        alone takes a second, `IK`. Repeats and reorderings such as `FMMfZv`, `FIJfZv`
        and `FNkMfZv` are refused, as the reference refuses them.
        """
        reader = self.reader
        storage = []
        if reader.starts_with("NkM"):
            # DMD 2.104+ writes `Nk` before `M`; libiberty refuses it, `core.demangle`
            # reads it as `return scope`, which is followed here.
            reader.pos += 3
            storage.extend(("return", "scope"))
        else:
            if reader.peek() == "M":
                reader.pos += 1
                storage.append("scope")
            if reader.starts_with("Nk"):
                reader.pos += 2
                storage.append("return")
        char = reader.peek()
        if char in ("I", "J", "K", "L"):
            reader.pos += 1
            storage.append(PARAMETER_STORAGE[char])
            if char == "I" and reader.peek() == "K":
                # `in ref`, the only pair the reference spells.
                reader.pos += 1
                storage.append(PARAMETER_STORAGE["K"])
        rendered = self.type_()
        return " ".join([*storage, rendered]) if storage else rendered

    def string_literal(self):
        """`a|w|d <Number> "_" <hex>`, the characters written two hex digits each.

        `VAyaa1_2b` is a one-character `immutable(char)[]` holding 0x2b, which the
        reference spells `"+"`.

        The marker is the *literal's* width and not the encoding's: all three read one
        byte a character, and `w` and `d` differ only in the suffix they put after the
        closing quote -- `"abc"w`, `"abc"d`. `u` is not one of them, and the reference
        refuses a name that uses it.
        """
        reader = self.reader
        suffix = STRING_SUFFIX.get(reader.take())
        if suffix is None:
            raise DemangleFailure("unknown string literal marker")
        count = reader.number()
        if not reader.eat("_"):
            raise DemangleFailure("string literal without its separator")
        digits = count * 2
        if reader.pos + digits > reader.end:
            raise DemangleFailure("string literal runs past the end of the name")
        raw = reader.text[reader.pos : reader.pos + digits]
        reader.pos += digits
        try:
            pairs = [(int(raw[at : at + 2], 16), raw[at : at + 2]) for at in range(0, digits, 2)]
        except ValueError:
            raise DemangleFailure("string literal is not hex") from None
        return '"' + "".join(_escaped(code, written) for code, written in pairs) + f'"{suffix}'

    @staticmethod
    def _integer_literal(kind, value, digits, negative=False, suffix=True):
        """A numeric literal, spelled as the type it belongs to is spelled.

        A `bool` reads `false`/`true` and a `char` reads as a quoted character; both are
        mangled as integers, so the type has to be consulted rather than the value. Every
        other kind is written with the digits the name carried -- see `_Reader.digits`.

        The `N` sign is written for every kind; the reference writes `-'\x11'`, `-true`.
        """
        sign = "-" if negative else ""
        if kind == "bool":
            return f"{sign}{'true' if value else 'false'}"
        if kind in _CHARACTER_ESCAPES:
            return f"{sign}{_character(kind, value)}"
        return f"{sign}{digits}{INTEGER_SUFFIX.get(kind, '') if suffix else ''}"

    def type_(self):
        work = self._work = self._work - 1
        if work < 0:
            raise _Exhausted
        reader = self.reader
        if reader.depth > _Reader.MAX_DEPTH:
            raise DemangleFailure("type nesting too deep")
        reader.depth += 1
        try:
            return self._type()
        finally:
            reader.depth -= 1

    def _type(self):
        modifiers = self.type_modifiers()
        rendered = self._type_body()
        for modifier in reversed(modifiers):
            rendered = f"{modifier}({rendered})"
        return rendered

    def _type_body(self):
        reader = self.reader
        char = reader.peek()

        if char == "Q":
            return self.type_back_reference()
        pair = reader.text[reader.pos : reader.pos + 2]
        if pair == "Nn":
            reader.pos += 2
            # As the reference spells `noreturn`.
            return "typeof(*null)"
        if pair == "Nh":
            # `Nh <Type>`, a SIMD vector: `__vector(byte[8])`.
            reader.pos += 2
            return self._cap(f"__vector({self.type_()})")
        if pair in WIDE_BASIC_TYPES:
            reader.pos += 2
            return WIDE_BASIC_TYPES[pair]
        if char in BASIC_TYPES:
            reader.pos += 1
            return BASIC_TYPES[char]
        if char == "A":
            reader.pos += 1
            return self._cap(f"{self.type_()}[]")
        if char == "G":
            # The bound is a value, not a length prefix.
            reader.pos += 1
            # Verbatim digits, as `dlang_type`'s `G` case: `G012a` is `char[012]`.
            _, count = reader.digits()
            return self._cap(f"{self.type_()}[{count}]")
        if char == "H":
            reader.pos += 1
            key = self.type_()
            return self._cap(f"{self.type_()}[{key}]")
        if char == "P":
            reader.pos += 1
            # `int(char[]) function`: the word is the pointer. Only a `P` followed by a
            # calling convention absorbs (`dlang_type`), so `PPUZi` keeps a `*`.
            absorbs = reader.peek() in CALLING_CONVENTIONS
            pointee = self.type_()
            return self._cap(pointee if absorbs else f"{pointee}*")
        if char in ("C", "S", "E", "T"):
            # `dlang_parse_qualified` needs at least one component: `_D3fooC` is refused.
            # Measured by cursor movement, since `_D3fooC0` (anonymous) reads.
            reader.pos += 1
            before = reader.pos
            outer, outer_suffix = self._in_type_name, self._suffix_modifiers
            self._in_type_name, self._suffix_modifiers = True, False
            try:
                spelled = ".".join(self.qualified_name())
            finally:
                self._in_type_name, self._suffix_modifiers = outer, outer_suffix
            if reader.pos == before:
                raise DemangleFailure(f"a {char!r} type with no qualified name")
            return self._cap(spelled)
        if char == "D":
            reader.pos += 1
            modifiers = self.this_modifiers()
            if reader.peek() == "Q":
                # `dlang_type_backref` with `is_function`: the target must be a function type.
                target = self._back_reference_target(reader.pos)
                if target is None or reader.text[target] not in CALLING_CONVENTIONS:
                    raise DemangleFailure("a delegate's back reference does not point at a function type")
                inner = self.type_back_reference()
                spelled = inner.removesuffix(" function")
                return self._cap(" ".join(["", spelled, "delegate", *modifiers]).strip())
            convention, attributes, parameters, returns = self.function_type()
            spelled = f"{convention}{returns}({', '.join(parameters)})"
            # `char() pure delegate const`
            return self._cap(" ".join([spelled, *attributes, "delegate", *modifiers]))
        if char in CALLING_CONVENTIONS:
            convention, attributes, parameters, returns = self.function_type()
            words = " ".join(attributes)
            spelled = f"{convention}{returns}({', '.join(parameters)})"
            return self._cap(f"{spelled} {words} function" if words else f"{spelled} function")
        if char == "B":
            # `B <Number> <Type>...`, spelled `Tuple!(char, char)`.
            reader.pos += 1
            count = reader.number()
            return self._cap(f"Tuple!({', '.join(self.type_() for _ in range(count))})")
        raise DemangleFailure(f"unknown type code {char!r}")

    def _type_code(self):
        """The first character of the type about to be read, through any back reference.

        A value's spelling can depend on its type's *shape* -- an associative array
        writes `[0:"c"]` where a static array writes `[0, "c"]` -- and a type written as
        `QFh` says nothing about its shape until it is followed. The reference follows it
        for the same reason; a chain is followed to its end, and anything malformed comes
        back as the `Q` itself so the ordinary parse reports it.
        """
        reader = self.reader
        at = reader.pos
        for _ in range(_Reader.MAX_DEPTH):
            if reader.text[at : at + 1] != "Q":
                return reader.text[at : at + 1]
            saved = reader.pos
            reader.pos = at + 1
            try:
                distance = _back_reference_number(reader)
            except DemangleFailure:
                return "Q"
            finally:
                reader.pos = saved
            target = at - distance
            if distance <= 0 or target < 0 or target >= at or self._is_inside_lname(target):
                return "Q"
            at = target
        return "Q"

    def type_back_reference(self):
        reader = self.reader
        at = reader.pos
        reader.pos += 1
        distance = _back_reference_number(reader)
        # Measured from the `Q`, and a zero distance would point at the `Q` itself.
        target = at - distance
        if distance <= 0 or target < 0 or target >= at:
            raise DemangleFailure("type back reference outside the name")
        if self._is_inside_lname(target):
            self._backref_inside_lname = True
            raise DemangleFailure("type back reference targets inside an identifier")
        if at >= self._last_backref:
            # The chain has turned round; see `_last_backref`.
            raise DemangleFailure("a type back reference read from inside its own target")
        self._follows -= 1
        if self._follows < 0:
            raise _Exhausted
        # Keyed on this `Q`'s position: the bound it installs changes the reading, so two
        # references to one target cannot share. `tools/mutate.py --seed 69`.
        key = (target, "type", self._in_symbol_argument, at)
        found = self._resolved.get(key)
        if found is not None:
            return found[0]
        saved = reader.pos
        saved_bound = self._last_backref
        reader.pos = target
        self._last_backref = at
        try:
            if reader.depth > _Reader.MAX_DEPTH:
                raise DemangleFailure("back reference recursion")
            reader.depth += 1
            try:
                resolved = self.type_()
            finally:
                reader.depth -= 1
        finally:
            reader.pos = saved
            self._last_backref = saved_bound
        self._resolved[key] = (resolved,)
        return resolved

    def trailing_type(self):
        """The declared thing's own type, spelled the way the reference spells it."""
        reader = self.reader
        if reader.pos >= reader.end:
            # `_D QualifiedName Type`: the reference refuses `_D1a`.
            raise DemangleFailure("no type after the qualified name")
        if reader.eat("Z"):
            return ""
        modifiers = []
        has_this = reader.eat("M")
        if has_this:
            modifiers = self.this_modifiers()
        char = reader.peek()
        if has_this and char not in CALLING_CONVENTIONS:
            # `M` is a member function's `this`: a function type must follow.
            raise DemangleFailure("a `this` parameter with no function type after it")
        if char in CALLING_CONVENTIONS:
            _, attributes, parameters, _ = self.function_type(returns=False)
            # A function's `Z` may be followed by the artificial symbol's: `_D4main3fooFZZ`
            # is `main.foo()`. The return type is consumed, not printed.
            if not reader.eat("Z"):
                self.type_()
            self._trailing_had_attributes = bool(attributes)
            spelled = f"({', '.join(parameters)})"
            trailing = " ".join(modifiers)
            return f"{spelled} {trailing}" if trailing else spelled
        # A variable: only the path is printed, but the type must be consumed.
        self.type_()
        return ""


def parse_d_symbol(
    symbol,
    max_substitutions=DEFAULT_LIMITS.max_substitutions,
    max_output=DEFAULT_LIMITS.max_output,
):
    """Parse `symbol`, returning a `DSymbol`, or raise `DemangleFailure`."""
    parser = _Parser(symbol, max_substitutions, max_output)
    text = parser.parse()
    if parser.reader.pos != parser.reader.end:
        raise DemangleFailure("unconsumed input")
    generated = ""
    body = text
    for prefix in SPECIAL_COMPONENTS.values():
        if text.startswith(prefix):
            generated, body = prefix, text[len(prefix) :]
            break
    path, _, rest = body.partition("(")
    return DSymbol(
        raw=symbol,
        path=path,
        text=text,
        suffix=f"({rest}" if rest else "",
        generated=generated,
    )
