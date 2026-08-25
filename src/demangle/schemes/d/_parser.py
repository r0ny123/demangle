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

import string

__all__ = ["DSymbol", "parse_d_symbol"]

#: A *set* of digits, not the string. `peek()` returns "" at the end of the name, and
#: `"" in string.digits` is True -- `in` on a string is substring containment, so an
#: empty character tests as a member of everything. Against a set it is not.
DIGITS = frozenset(string.digits)

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

#: Function attributes. Emitted in this order by the compiler, which is why they are
#: spelled in the order encountered rather than sorted.
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

#: How a parameter is passed. `Nk` is two characters and must be tested before `N` is
#: mistaken for anything else.
PARAMETER_STORAGE = {"I": "in", "J": "out", "K": "ref", "L": "lazy", "M": "scope"}

#: Calling conventions, and how the reference spells each. The D convention is the
#: default and is spelled with nothing at all.
CALLING_CONVENTIONS = {
    "F": "",
    "U": "extern(C) ",
    "W": "extern(Windows) ",
    "R": "extern(C++) ",
    "Y": "extern(Objective-C) ",
}

#: The suffix D writes on an integer literal of each type, as the reference spells it.
INTEGER_SUFFIX = {"uint": "u", "long": "L", "ulong": "uL", "ubyte": "u", "ushort": "u"}

#: Type modifiers, innermost first as the ABI writes them.
TYPE_MODIFIERS = {"x": "const", "y": "immutable", "O": "shared", "Ng": "inout"}

#: Compiler-generated last components, and what the reference calls the symbol. Each names
#: a thing *about* the entity the rest of the path names, so the path is spelled and the
#: component itself becomes the prefix -- `initializer for rt.util.utility._Complex`.
#: Components the reference renames rather than prefixes. A constructor is spelled the
#: way it is written in source.
RENAMED_COMPONENTS = {
    "__ctor": "this",
    "__dtor": "~this",
}
#: `__xdtor` and `__xpostblit` are *not* renamed -- checked against the reference rather
#: than assumed from the pattern, and it leaves both alone.
#:
#: `__postblit` is a quirk worth recording. The reference renames it to `this(this)` when
#: the function carries no attributes and leaves it as `__postblit` when it does, which is
#: not a distinction the language makes: `_D3foo3Bar10__postblitMFZv` demangles to
#: `foo.Bar.this(this)` and the same name with `NaNbNiNf` to `foo.Bar.__postblit()`. Every
#: `__postblit` in the shipped libraries has attributes, so this follows the behaviour the
#: real symbols get and does not rename it.

SPECIAL_COMPONENTS = {
    "__init": "initializer for ",
    "__vtbl": "vtable for ",
    "__Class": "ClassInfo for ",
    "__ModuleInfo": "ModuleInfo for ",
    "__Interface": "Interface for ",
}


class DemangleFailure(Exception):
    """This name is not one this parser can read."""


class _Reader:
    """Position in the mangled name, with the whole string kept for back references."""

    __slots__ = ("depth", "end", "pos", "text")

    MAX_DEPTH = 200

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

    def number(self):
        start = self.pos
        while self.pos < self.end and self.text[self.pos] in DIGITS:
            self.pos += 1
        if self.pos == start:
            raise DemangleFailure("expected a number")
        # A length prefix long enough to overflow anything real is a malformed name, not
        # a very large one.
        if self.pos - start > 9:
            raise DemangleFailure("implausible length prefix")
        return int(self.text[start : self.pos])


class DSymbol:
    """One parsed D symbol: its dotted path, and the type of what it declares."""

    __slots__ = ("path", "raw", "suffix", "text")

    def __init__(self, raw, path, text, suffix=""):
        self.raw = raw
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

    def __init__(self, text):
        self.reader = _Reader(text)

    # -- entry -----------------------------------------------------------------

    def parse(self):
        reader = self.reader
        if not reader.starts_with("_D"):
            raise DemangleFailure("not a D mangled name")
        reader.pos += 2
        path = self.qualified_name()
        if not path:
            raise DemangleFailure("no qualified name")
        prefix = ""
        if len(path) > 1 and path[-1] in SPECIAL_COMPONENTS:
            pass
        else:
            path = [self._spelled_component(part) for part in path]
        if len(path) > 1 and path[-1] in SPECIAL_COMPONENTS:
            prefix = SPECIAL_COMPONENTS[path.pop()]
            # These carry no type of their own beyond the `Z` that ends them.
            self.reader.eat("Z")
            if self.reader.pos != self.reader.end:
                raise DemangleFailure("unconsumed input after a generated symbol")
            return prefix + ".".join(path)
        trailing = self.trailing_type()
        return prefix + ".".join(path) + trailing

    # -- names -----------------------------------------------------------------

    def _opens_symbol_name(self):
        char = self.reader.peek()
        if char in DIGITS or char == "Q":
            return True
        # A template instance follows the name it qualifies directly, with no length
        # prefix of its own: `7utility__T8_ComplexTdZ`.
        return self.reader.starts_with("__T") or self.reader.starts_with("__U")

    def qualified_name(self):
        parts = []
        while self._opens_symbol_name():
            component = self.symbol_name()
            # A scope's own function type *is* spelled -- a symbol inside a function is
            # written `enclosing(params).inner` -- so the parameters come back here rather
            # than being discarded.
            parts.append(component + self.scope_type())
        return parts

    def symbol_name(self):
        reader = self.reader
        if reader.peek() == "Q":
            return self.identifier_back_reference()
        if reader.peek() == "0":
            reader.pos += 1
            return ""
        if reader.starts_with("__T") or reader.starts_with("__U"):
            return self.template_instance()
        length = reader.number()
        start = reader.pos
        if start + length > reader.end:
            raise DemangleFailure("identifier runs past the end of the name")
        reader.pos = start + length
        return reader.text[start : start + length]

    @staticmethod
    def _spelled_component(name):
        return RENAMED_COMPONENTS.get(name, name)

    def template_instance(self):
        """`__T <LName> <TemplateArgs>* Z`, spelled `name!(argument, ...)`."""
        reader = self.reader
        reader.pos += 3
        name = self._spelled_component(self.symbol_name())
        reader.eat("H")
        arguments = []
        while not reader.eat("Z"):
            if reader.pos >= reader.end:
                raise DemangleFailure("unterminated template instance")
            arguments.append(self.template_argument())
        return f"{name}!({', '.join(arguments)})"

    def template_argument(self):
        reader = self.reader
        marker = reader.take()
        if marker == "T":
            return self.type_()
        if marker == "S":
            # A whole qualified name, not one component: `SQBaQz3run` is
            # `std.parallelism.run`, and reading a single component left the rest to be
            # taken for further arguments.
            return ".".join(self.qualified_name())
        if marker == "V":
            kind = self.type_()
            return self.template_value(kind)
        if marker in DIGITS or marker == "Q" or marker == "_":
            # A bare symbol name, with no `S` in front of it. The compiler emits these
            # where the argument is a symbol whose kind is unambiguous from the grammar.
            reader.pos -= 1
            return self.symbol_name()
        if marker == "X":
            # An externally mangled argument: a length and that many characters, kept as
            # they stand because they are another mangling entirely.
            length = reader.number()
            start = reader.pos
            reader.pos = min(start + length, reader.end)
            return reader.text[start : reader.pos]
        raise DemangleFailure(f"unknown template argument marker {marker!r}")

    def template_value(self, kind):
        """A value argument. Only the forms a compiler emits are modelled."""
        reader = self.reader
        char = reader.peek()
        if char == "n":
            reader.pos += 1
            return "null"
        if char == "i":
            reader.pos += 1
            return self._integer_literal(kind, reader.number())
        if char == "N":
            reader.pos += 1
            return self._integer_literal(kind, reader.number(), negative=True)
        if char in DIGITS:
            value = reader.number()
            if kind == "bool":
                return "true" if value else "false"
            if kind == "char":
                return f"'\\x{value:02x}'"
            return str(value)
        if char in ("a", "u", "w"):
            return self.string_literal()
        if char in ("e", "c", "A", "S"):
            # Real and complex literals, array literals, struct literals. The reference
            # spells each in a way that needs the value decoded, and inventing a spelling
            # would be worse than declining: the name is refused rather than answered
            # wrongly.
            raise DemangleFailure(f"template value form {char!r} not modelled")
        raise DemangleFailure(f"unknown template value {char!r}")

    def identifier_back_reference(self):
        reader = self.reader
        at = reader.pos
        reader.pos += 1
        distance = _back_reference_number(reader)
        # Measured from the `Q`, and a zero distance would point at the `Q` itself.
        target = at - distance
        if distance <= 0 or target < 0 or target >= at:
            raise DemangleFailure("back reference outside the name")
        saved = reader.pos
        reader.pos = target
        try:
            if reader.depth > _Reader.MAX_DEPTH:
                raise DemangleFailure("back reference recursion")
            reader.depth += 1
            try:
                return self.symbol_name()
            finally:
                reader.depth -= 1
        finally:
            reader.pos = saved

    # -- types -----------------------------------------------------------------

    def scope_type(self):
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
                modifiers = self.type_modifiers()
            if reader.peek() not in CALLING_CONVENTIONS:
                raise DemangleFailure("not a scope")
            _, _, parameters, _ = self.function_type(returns=False)
            if not self._opens_symbol_name():
                raise DemangleFailure("not a scope")
        except DemangleFailure:
            reader.pos, reader.depth = saved, saved_depth
            return ""
        spelled = f"({', '.join(parameters)})"
        trailing = " ".join(modifiers)
        return f"{spelled} {trailing}" if trailing else spelled

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
                reader.pos += 1
                parameters.append("...")
                break
            if not char:
                raise DemangleFailure("unterminated parameter list")
            parameters.append(self.parameter())
        spelled = self.type_() if returns else ""
        return CALLING_CONVENTIONS[convention], attributes, parameters, spelled

    def parameter(self):
        reader = self.reader
        storage = []
        while True:
            if reader.starts_with("Nk"):
                reader.pos += 2
                storage.append("return")
            elif reader.peek() in PARAMETER_STORAGE and reader.peek() != "M":
                storage.append(PARAMETER_STORAGE[reader.take()])
            elif reader.peek() == "M" and reader.peek(1) not in ("", "Z"):
                reader.pos += 1
                storage.append("scope")
            else:
                break
        rendered = self.type_()
        return " ".join([*storage, rendered]) if storage else rendered

    def string_literal(self):
        """`a|u|w <Number> "_" <hex>`, the characters written two hex digits each.

        `VAyaa1_2b` is a one-character `immutable(char)[]` holding 0x2b, which the
        reference spells `"+"`.
        """
        reader = self.reader
        width = {"a": 1, "u": 2, "w": 4}[reader.take()]
        count = reader.number()
        if not reader.eat("_"):
            raise DemangleFailure("string literal without its separator")
        digits = count * width * 2
        if reader.pos + digits > reader.end:
            raise DemangleFailure("string literal runs past the end of the name")
        raw = reader.text[reader.pos : reader.pos + digits]
        reader.pos += digits
        try:
            codes = [int(raw[at : at + width * 2], 16) for at in range(0, digits, width * 2)]
        except ValueError:
            raise DemangleFailure("string literal is not hex") from None
        return '"' + "".join(chr(code) for code in codes) + '"'

    @staticmethod
    def _integer_literal(kind, value, negative=False):
        sign = "-" if negative else ""
        return f"{sign}{value}{INTEGER_SUFFIX.get(kind, '')}"

    def type_(self):
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
        if pair in WIDE_BASIC_TYPES:
            reader.pos += 2
            return WIDE_BASIC_TYPES[pair]
        if char in BASIC_TYPES:
            reader.pos += 1
            return BASIC_TYPES[char]
        if char == "A":
            reader.pos += 1
            return f"{self.type_()}[]"
        if char == "G":
            reader.pos += 1
            count = reader.number()
            return f"{self.type_()}[{count}]"
        if char == "H":
            reader.pos += 1
            key = self.type_()
            return f"{self.type_()}[{key}]"
        if char == "P":
            reader.pos += 1
            pointee = self.type_()
            # D spells a pointer to a function as `int(char[]) function`, not with a `*`:
            # the word *is* the pointer. Adding one gives `... function*`, which is not a
            # type anyone writes.
            return pointee if pointee.endswith(" function") else f"{pointee}*"
        if char in ("C", "S", "E", "T"):
            reader.pos += 1
            return ".".join(self.qualified_name())
        if char == "D":
            reader.pos += 1
            modifiers = self.type_modifiers()
            convention, attributes, parameters, returns = self.function_type()
            words = " ".join([*attributes, *modifiers])
            spelled = f"{convention}{returns}({', '.join(parameters)})"
            return f"{spelled} {words} delegate" if words else f"{spelled} delegate"
        if char in CALLING_CONVENTIONS:
            convention, attributes, parameters, returns = self.function_type()
            words = " ".join(attributes)
            spelled = f"{convention}{returns}({', '.join(parameters)})"
            return f"{spelled} {words} function" if words else f"{spelled} function"
        if char == "B":
            # A tuple: `B <Number> <Type>...`
            reader.pos += 1
            count = reader.number()
            return f"({', '.join(self.type_() for _ in range(count))})"
        raise DemangleFailure(f"unknown type code {char!r}")

    def type_back_reference(self):
        reader = self.reader
        at = reader.pos
        reader.pos += 1
        distance = _back_reference_number(reader)
        # Measured from the `Q`, and a zero distance would point at the `Q` itself.
        target = at - distance
        if distance <= 0 or target < 0 or target >= at:
            raise DemangleFailure("type back reference outside the name")
        saved = reader.pos
        reader.pos = target
        try:
            if reader.depth > _Reader.MAX_DEPTH:
                raise DemangleFailure("back reference recursion")
            reader.depth += 1
            try:
                return self.type_()
            finally:
                reader.depth -= 1
        finally:
            reader.pos = saved

    # -- what follows the path -------------------------------------------------

    def trailing_type(self):
        """The declared thing's own type, spelled the way the reference spells it."""
        reader = self.reader
        if reader.pos >= reader.end:
            # `_D QualifiedName Type` -- the type is not optional. A bare path is not a
            # mangled name, and the reference refuses `_D1a` rather than answering `a`.
            raise DemangleFailure("no type after the qualified name")
        if reader.eat("Z"):
            return ""
        modifiers = []
        if reader.eat("M"):
            modifiers = self.type_modifiers()
        char = reader.peek()
        if char in CALLING_CONVENTIONS:
            _, _, parameters, _ = self.function_type()
            spelled = f"({', '.join(parameters)})"
            trailing = " ".join(modifiers)
            return f"{spelled} {trailing}" if trailing else spelled
        # A variable rather than a function. The reference prints only its path, but the
        # type still has to be consumed: leaving it made every such symbol fail the
        # "everything was read" check that stops a partial parse being reported as a
        # whole one.
        self.type_()
        return ""


def parse_d_symbol(symbol):
    """Parse `symbol`, returning a `DSymbol`, or raise `DemangleFailure`."""
    parser = _Parser(symbol)
    text = parser.parse()
    if parser.reader.pos != parser.reader.end:
        raise DemangleFailure("unconsumed input")
    return DSymbol(raw=symbol, path=text.split("(")[0], text=text)
