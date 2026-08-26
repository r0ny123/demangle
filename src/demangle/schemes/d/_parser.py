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

from ...core.limits import DEFAULT_LIMITS

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


#: How the reference writes a character inside a string literal that cannot stand as
#: itself. Anything else below space, or above ASCII, goes out as `\xNN`.
_STRING_ESCAPES = {
    0x0A: "\\n",
    0x09: "\\t",
    0x0D: "\\r",
    0x5C: "\\\\",
    0x07: "\\a",
    0x08: "\\b",
    0x0C: "\\f",
    0x0B: "\\v",
}


def _escaped(code):
    escape = _STRING_ESCAPES.get(code)
    if escape is not None:
        return escape
    if code < 0x20 or code == 0x7F:
        return f"\\x{code:02x}"
    return chr(code)


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
        return int(self.text[start : self.pos])


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
        # A symbol used as a *template argument* is spelled without the qualifiers an
        # enclosing scope would carry: `FilterResult!(bitsSet(), ...)` where the same
        # function in the path reads `initializer() const`. Tracked rather than passed
        # down because every production between the two is unaware of it.
        self._in_symbol_argument = False
        self._trailing_had_attributes = True
        # How many back references this name may still follow. A `Q` names an earlier
        # position and is read by parsing that position again, so following one can
        # follow more -- and the work is exponential in how deeply they nest rather than
        # linear in the length of the name.
        #
        # Memoising the result (below) helps and is not enough: a back reference into
        # the middle of a qualified name re-reads the *sequence* from there, and the
        # `_in_symbol_argument` state that a template argument flips changes what a
        # position means, so the same position is genuinely read more than once.
        #
        # So there is also a budget, proportional to the length of the name, and a name
        # that exhausts it is refused. That is the right answer as well as the cheap one:
        # `c++filt --format=dlang` refuses this name too, and libiberty ships it as a
        # regression vector -- `std.format.formattedWrite` out of a real D binary, 2,695
        # characters carrying 441 `Q`s. An 800-character prefix of it took over 25
        # seconds here; the whole name never finished.
        #
        # The budget is `max_substitutions`, which is exactly what that bound is for and
        # which this scheme was not consulting -- only Itanium and Delphi were. It does
        # not scale with the length of the name on purpose: the work a follow does grows
        # with the name too, so a length-proportional budget still grows super-linearly.
        self._follows = max_substitutions
        # A budget on *productions entered*, which is the only thing that bounds this
        # parser reliably.
        #
        # It backtracks in two places and reads speculatively in a third, so a bound on
        # any one mechanism can be sidestepped by another: bounding back references left
        # the speculative lookahead free to explode, and memoising the lookahead left the
        # back references free. Counting the work itself is indifferent to which path is
        # taken.
        #
        # Sixty-four times the length of the name: a well-formed symbol needs a number of
        # productions linear in its length, so this is two orders of magnitude of
        # headroom for anything a compiler emits, and still finite for a name whose
        # back references feed on each other.
        self._work = 64 * len(text) + 4096
        # The output bound, consulted *while* building rather than on the finished
        # string. This is what the profile actually said: on a real 2,695-character D
        # symbol out of `std.format.formattedWrite`, 18 of 35 seconds were inside 4,047
        # calls to `str.join` -- four milliseconds each, because the pieces being joined
        # were enormous. Only three hundred thousand function calls in total, so the
        # parser was not looping; it was assembling a string far larger than any caller
        # would accept, and the check on `len(symbol.text)` at the end could not know
        # that until it was too late to matter.
        self._max_output = max_output
        # What a back reference at a given position resolved to, keyed by the position,
        # the production read there and the one piece of parser state that can change
        # the answer.
        #
        # Without this, following a back reference re-parsed the region it points at --
        # and that region contains back references of its own, so the work was
        # exponential in how deeply they nest. It is not a theoretical shape: this is
        # `std.format.formattedWrite` out of a real D binary, 2,695 characters with 441
        # `Q`s in it, which libiberty ships as a regression vector of its own. An
        # 800-character *prefix* of it took over 25 seconds; the whole name did not
        # finish. A back reference names a position, the grammar at a position is
        # deterministic, so reading it twice can only ever produce the same answer.
        self._resolved = {}
        # Whether a whole symbol name starts at a position -- see `_symbol_name_follows`.
        self._starts_symbol = {}

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
        # `__postblit` is renamed by the reference only when the function has no
        # attributes, so the decision needs the type, which is read after the path.
        postblit = len(path) > 1 and path[-1] == "__postblit"
        if len(path) > 1 and path[-1] in SPECIAL_COMPONENTS:
            prefix = SPECIAL_COMPONENTS[path.pop()]
            # These carry no type of their own beyond the `Z` that ends them.
            self.reader.eat("Z")
            if self.reader.pos != self.reader.end:
                raise DemangleFailure("unconsumed input after a generated symbol")
            return prefix + ".".join(path)
        self._trailing_had_attributes = True
        trailing = self.trailing_type()
        if postblit and not self._trailing_had_attributes:
            # `this(this)` reads as a declaration already, so the reference writes no
            # parameter list after it.
            path[-1] = "this(this)"
            trailing = ""
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
            saved, saved_depth = self.reader.pos, self.reader.depth
            try:
                component = self.symbol_name()
            except DemangleFailure:
                # `Q` opens both an identifier back reference and a *type* back
                # reference, and only position tells them apart. One that does not
                # resolve to an identifier is the symbol's own type starting, so the
                # path ends here.
                self.reader.pos, self.reader.depth = saved, saved_depth
                break
            # A scope's own function type *is* spelled -- a symbol inside a function is
            # written `enclosing(params).inner` -- so the parameters come back here rather
            # than being discarded.
            parts.append(self._spelled_component(component) + self.scope_type())
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
        length = reader.number()
        start = reader.pos
        if start + length > reader.end:
            raise DemangleFailure("identifier runs past the end of the name")
        # A template instance is a *length-prefixed* identifier whose content happens to
        # be `__T<name><args>Z`. The test above catches only the bare form, so
        # `_D8demangle11__T4testTaZv` -- where `11` counts `__T4testTaZ` -- was read as
        # an identifier called `__T4testTaZ` and printed as one. The reference spells it
        # `demangle.test!(char)`, and this is the largest single group of disagreements
        # with libiberty's corpus.
        #
        # Bounded by the length prefix, so a malformed body cannot read past its own
        # identifier: the cursor is put back and the raw text used if it does not parse.
        text = reader.text[start : start + length]
        if text.startswith(("__T", "__U")):
            saved, saved_end = reader.pos, reader.end
            reader.end = start + length
            try:
                spelled = self.template_instance()
                if reader.pos == start + length:
                    reader.end = saved_end
                    reader.pos = start + length
                    return spelled
            except (DemangleFailure, _Exhausted):
                pass
            finally:
                reader.end = saved_end
            reader.pos = saved
        reader.pos = start + length
        return text

    @staticmethod
    def _spelled_component(name):
        return RENAMED_COMPONENTS.get(name, name)

    def template_instance(self):
        """`__T <LName> <TemplateArgs>* Z`, spelled `name!(argument, ...)`."""
        reader = self.reader
        reader.pos += 3
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
            # A whole qualified name, not one component: `SQBaQz3run` is
            # `std.parallelism.run`, and reading a single component left the rest to be
            # taken for further arguments.
            #
            # The name may carry its own `_D` prefix -- a symbol argument is mangled as a
            # complete symbol, so `S_DQBg3net4curl7CurlAPI7_handle` appears where a bare
            # path would do just as well.
            # Newer compilers write the argument's *length* first: `S11` then eleven
            # characters holding `6symbol3foo`, or `S20` then twenty holding a complete
            # `_D`-prefixed symbol. Without reading it, the length ran together with the
            # name -- `S116symbol3foo` was read as a name beginning `116symbol` -- and the
            # whole template instance was refused. Between them the two shapes account
            # for 73 of the 79 vectors in libiberty's corpus that still disagreed.
            #
            # The length bounds the argument, so the cursor cannot run past it into the
            # arguments that follow.
            bounded = self._symbol_argument_bound()
            if reader.starts_with("_D"):
                # A complete mangled symbol, path *and* type: the `_handle` in
                # `S_DQBg3net4curl7CurlAPI7_handlePv` is a `void*`, and the `Pv` has to be
                # consumed even though the reference prints only the path.
                reader.pos += 2
                outer = self._in_symbol_argument
                self._in_symbol_argument = True
                try:
                    # Its own type is spelled too where it is a function: the reference
                    # writes `regexImpl(const(char)[], ...)` for one naming a function.
                    spelled = ".".join(self.qualified_name())
                    spelled += self.trailing_type()
                finally:
                    self._in_symbol_argument = outer
                if bounded is not None:
                    reader.pos = bounded
                return self._cap(spelled)
            outer = self._in_symbol_argument
            self._in_symbol_argument = True
            try:
                spelled = ".".join(self.qualified_name())
            finally:
                self._in_symbol_argument = outer
            if bounded is not None:
                reader.pos = bounded
            return self._cap(spelled)
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

    def template_value(self, kind, suffix=True):
        """A value argument. Only the forms a compiler emits are modelled."""
        reader = self.reader
        char = reader.peek()
        if char == "n":
            reader.pos += 1
            return "null"
        if char == "i":
            reader.pos += 1
            return self._integer_literal(kind, reader.number(bounded=False), suffix=suffix)
        if char == "N":
            reader.pos += 1
            return self._integer_literal(kind, reader.number(bounded=False), negative=True, suffix=suffix)
        if char in DIGITS:
            return self._integer_literal(kind, reader.number(bounded=False), suffix=suffix)
        if char in ("a", "u", "w"):
            return self.string_literal()
        if char == "A":
            # An array literal: `A <count> <value>...`, where each value has the element
            # type. `VAmA2i104i1281` is a `ulong[]` holding `[104, 1281]`.
            reader.pos += 1
            count = reader.number()
            element = kind.removesuffix("[]")
            # No literal suffix inside an array: the reference writes `[104, 1281]`, not
            # `[104uL, 1281uL]`, even though each element is a `ulong`.
            return "[" + ", ".join(self.template_value(element, suffix=False) for _ in range(count)) + "]"
        if char in ("e", "c", "S"):
            # Real and complex literals, array literals, struct literals. The reference
            # spells each in a way that needs the value decoded, and inventing a spelling
            # would be worse than declining: the name is refused rather than answered
            # wrongly.
            raise DemangleFailure(f"template value form {char!r} not modelled")
        raise DemangleFailure(f"unknown template value {char!r}")

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
        # No split works, so the digits are not a length at all: this is the older form,
        # `S <LName>`, where the argument is an ordinary length-prefixed name and the
        # first digits belong to *it*. `S6symbol` is that shape, and reading its `6` as
        # an outer length left nothing that parsed.
        return None

    def _is_symbol_argument(self, start, end):
        """Whether `[start, end)` is exactly one qualified name, optionally `_D`-prefixed."""
        reader = self.reader
        saved, saved_end, saved_depth = reader.pos, reader.end, reader.depth
        outer = self._in_symbol_argument
        try:
            reader.pos, reader.end = start, end
            self._in_symbol_argument = True
            if reader.starts_with("_D"):
                reader.pos += 2
            self.qualified_name()
            if reader.pos < end:
                self.trailing_type()
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
        the region it reads contains scopes that run it again -- so the same position was
        read over and over, and a real 2,695-character D symbol out of
        `std.format.formattedWrite` took seconds rather than milliseconds.

        Whether a symbol name starts at a position is a property of the position, so
        asking twice can only get the same answer. The one piece of parser state that
        changes what a position means is in the key.
        """
        key = (at, self._in_symbol_argument)
        answer = self._starts_symbol.get(key)
        if answer is None:
            reader = self.reader
            saved, saved_depth = reader.pos, reader.depth
            try:
                self.symbol_name()
                answer = True
            except DemangleFailure:
                answer = False
            finally:
                reader.pos, reader.depth = saved, saved_depth
            self._starts_symbol[key] = answer
        return answer

    def identifier_back_reference(self):
        reader = self.reader
        at = reader.pos
        reader.pos += 1
        distance = _back_reference_number(reader)
        # Measured from the `Q`, and a zero distance would point at the `Q` itself.
        target = at - distance
        if distance <= 0 or target < 0 or target >= at:
            raise DemangleFailure("back reference outside the name")
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
                resolved = self.symbol_name()
            finally:
                reader.depth -= 1
        finally:
            reader.pos = saved
        self._resolved[key] = (resolved,)
        return resolved

    # -- types -----------------------------------------------------------------

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
                modifiers = self.type_modifiers()
            if reader.peek() not in CALLING_CONVENTIONS:
                raise DemangleFailure("not a scope")
            _, attributes, parameters, _ = self.function_type(returns=False)
            # A full symbol name has to follow, not merely a byte that could open one.
            # `Qq` opens an identifier back reference *and* a type back reference, so
            # testing the byte alone made the return type of `rt_linkOption` look like
            # another path component and took the whole name with it.
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
        trailing = "" if self._in_symbol_argument else " ".join(modifiers)
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
                # `X` is `f(T t...)` and `Y` is `f(T t, ...)`. The difference is exactly
                # the separator, so `X` glues the ellipsis to the last parameter and `Y`
                # stands as one of its own.
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
        return '"' + "".join(_escaped(code) for code in codes) + '"'

    @staticmethod
    def _integer_literal(kind, value, negative=False, suffix=True):
        """A numeric literal, spelled as the type it belongs to is spelled.

        A `bool` reads `false`/`true` and a `char` reads as a quoted character; both are
        mangled as integers, so the type has to be consulted rather than the value.
        """
        if kind == "bool":
            return "true" if value else "false"
        if kind in ("char", "wchar", "dchar"):
            return "'" + _escaped(value).replace('\\"', '"') + "'"
        sign = "-" if negative else ""
        return f"{sign}{value}{INTEGER_SUFFIX.get(kind, '') if suffix else ''}"

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
            # The reference spells this `typeof(*null)` -- what the type is written as in
            # D source -- rather than by its name. `noreturn` is the newer spelling and
            # reads better, but the point of this scheme is to agree with the demangler
            # everyone else's tooling uses.
            return "typeof(*null)"
        if pair == "Nh":
            # `Nh <Type>`, a SIMD vector. The element type is an array, and the reference
            # writes the pair as `__vector(byte[8])`.
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
            reader.pos += 1
            count = reader.number()
            return self._cap(f"{self.type_()}[{count}]")
        if char == "H":
            reader.pos += 1
            key = self.type_()
            return self._cap(f"{self.type_()}[{key}]")
        if char == "P":
            reader.pos += 1
            pointee = self.type_()
            # D spells a pointer to a function as `int(char[]) function`, not with a `*`:
            # the word *is* the pointer. Adding one gives `... function*`, which is not a
            # type anyone writes.
            return self._cap(pointee if pointee.endswith(" function") else f"{pointee}*")
        if char in ("C", "S", "E", "T"):
            reader.pos += 1
            return self._cap(".".join(self.qualified_name()))
        if char == "D":
            reader.pos += 1
            modifiers = self.type_modifiers()
            if reader.peek() == "Q":
                # The function type is a back reference: `MxDQsm` is a delegate whose
                # signature was written earlier in the name.
                inner = self.type_back_reference()
                trailing = " ".join(modifiers)
                spelled = inner.removesuffix(" function")
                return self._cap(f"{spelled} {trailing} delegate" if trailing else f"{spelled} delegate")
            convention, attributes, parameters, returns = self.function_type()
            words = " ".join([*attributes, *modifiers])
            spelled = f"{convention}{returns}({', '.join(parameters)})"
            return self._cap(f"{spelled} {words} delegate" if words else f"{spelled} delegate")
        if char in CALLING_CONVENTIONS:
            convention, attributes, parameters, returns = self.function_type()
            words = " ".join(attributes)
            spelled = f"{convention}{returns}({', '.join(parameters)})"
            return self._cap(f"{spelled} {words} function" if words else f"{spelled} function")
        if char == "B":
            # A tuple: `B <Number> <Type>...`, which the reference names rather than
            # spelling as a bare parenthesised list -- `Tuple!(char, char)`, because that
            # is the type D source would write.
            reader.pos += 1
            count = reader.number()
            return self._cap(f"Tuple!({', '.join(self.type_() for _ in range(count))})")
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
        self._follows -= 1
        if self._follows < 0:
            raise _Exhausted
        key = (target, "type", self._in_symbol_argument)
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
                resolved = self.type_()
            finally:
                reader.depth -= 1
        finally:
            reader.pos = saved
        self._resolved[key] = (resolved,)
        return resolved

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
            _, attributes, parameters, _ = self.function_type()
            self._trailing_had_attributes = bool(attributes)
            spelled = f"({', '.join(parameters)})"
            trailing = " ".join(modifiers)
            return f"{spelled} {trailing}" if trailing else spelled
        # A variable rather than a function. The reference prints only its path, but the
        # type still has to be consumed: leaving it made every such symbol fail the
        # "everything was read" check that stops a partial parse being reported as a
        # whole one.
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
