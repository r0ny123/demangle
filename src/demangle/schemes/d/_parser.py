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

#: A *set* of digits, not the string. `peek()` returns "" at the end of the name, and
#: `"" in string.digits` is True -- `in` on a string is substring containment, so an
#: empty character tests as a member of everything. Against a set it is not.
DIGITS = frozenset(string.digits)

#: A compiler-generated anonymous scope, which the reference leaves out of the path.
_ANONYMOUS = re.compile(r"__S\d+")

#: A real literal's significand, which the mangling writes in upper case and the
#: reference passes through as it stands.
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
    "V": "extern(Pascal) ",
    "Y": "extern(Objective-C) ",
}

#: The three string-literal markers, and what the reference writes after the closing
#: quote for each. A `char` string gets nothing; the wide ones name their width.
STRING_SUFFIX = {"a": "", "w": "w", "d": "d"}

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
#: `__postblit` is a quirk worth recording. The reference renames it to `this(this)` only
#: where the thirteen characters `__postblitMFZ` stand together -- `dlang_lname` matches
#: them as one thing, wherever in the name they are, and writes no parameter list --
#: so `_D3foo3Bar10__postblitMFZv` is `foo.Bar.this(this)`, and every one of `MFNaZv`,
#: `MxFZv`, `MOFZv`, `MUZv`, `MFiZv`, `UZv` and `FZv` is left as `__postblit`, which is
#: not a distinction the language makes. "No attributes" was the first reading of it and
#: renamed six shapes the reference does not; "the last component" was the second, and
#: left an interior one as `__postblit()`. See `qualified_name`. Every `__postblit` in
#: the shipped libraries has attributes, so the real symbols are not renamed either way.

SPECIAL_COMPONENTS = {
    "__init": "initializer for ",
    "__vtbl": "vtable for ",
    "__Class": "ClassInfo for ",
    "__ModuleInfo": "ModuleInfo for ",
    "__Interface": "Interface for ",
}


#: The five characters the reference names inside a string literal. Derived by running
#: `c++filt --format=dlang` over every byte value rather than from the C escapes it looks
#: like: `\a` and `\b` are *not* among them, and neither `"` nor `\\` is escaped at all.
_STRING_ESCAPES = {0x09: "\\t", 0x0A: "\\n", 0x0B: "\\v", 0x0C: "\\f", 0x0D: "\\r"}

#: What the reference writes before a character it cannot print, and how many digits it
#: pads the value to, by the width of the character type.
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

    #: Digits allowed in a value. A `ulong` is twenty of them and the reference answers
    #: one digit longer still, so this is fifty times any real constant -- it is a guard
    #: against a name built to be long, not a limit on what D can write.
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
            # A value has no length to check it against, so it needs a bound of its own.
            # Not for the arithmetic -- the interpreter refuses to convert a digit string
            # this long at all, and that refusal would reach a caller as its own message
            # rather than as this parser saying the name is malformed.
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
        # A symbol used as a *template argument* is spelled without the qualifiers an
        # enclosing scope would carry: `FilterResult!(bitsSet(), ...)` where the same
        # function in the path reads `initializer() const`. Tracked rather than passed
        # down because every production between the two is unaware of it.
        self._in_symbol_argument = False
        # Whether a type's qualified name, or a `_D`-prefixed symbol argument, is being read;
        # see `scope_type`.
        self._in_type_name = False
        self._suffix_modifiers = True
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
        # The position of the type back reference being resolved, or the end of the name
        # when none is: a type back reference may only stand *before* it. `dlang_type_backref`
        # keeps the same bound in `last_backref`, and it is what makes a chain of them
        # finite -- each one resolved is read from an earlier position than the last, so
        # the name is walked backwards and never round. Without it, a type whose spelling
        # reaches the very `Q` that named it -- the enum at 27 in
        # `_D3std4conv__T7enumRepTyAaTEQBa6socket12SocketOptionVQBaiX0ZQBuyQBo`, a scope's
        # function type whose parameter is `QBa`, the reference back to 27 -- was read
        # again from inside itself, two hundred levels deep, and each level's speculative
        # scope type fell back to a plain name only where the depth ran out: two
        # kilobytes of `SocketOption(SocketOption(SocketOption(...` for a mutant the
        # reference spells in one level.
        self._last_backref = len(self.reader.text)
        # The spans of every LName (start of Number to end of Name) in the symbol.
        # A back reference targeting strictly inside an LName is refused: no compiler
        # writes one, and libiberty resolving them turns characters inside an
        # identifier into types or names.
        self._lname_spans = []
        #: How many of those spans cover each position strictly inside one. The spans
        #: alone answer `_is_inside_lname` by being scanned, and they are neither sorted
        #: nor disjoint -- backtracking re-reads a region and a template instance's
        #: components nest -- so the scan cannot be cut short and grows with the name.
        #: Over the D corpus that is 192,857 steps, 125,776 of them on the single
        #: longest symbol, against 75,222 positions to mark and 1,601 on that same name.
        #: A count rather than a flag because the spans overlap: a position leaves the
        #: map when the last span covering it is dropped, not the first.
        self._lname_cover = {}
        self._backref_inside_lname = False
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
        if path and path[-1] in SPECIAL_COMPONENTS and reader.peek() == "Z":
            # `dlang_parse_mangle`: an artificial symbol ends with `Z` and has no type.
            # The `Z` is what *makes* it one, and it is not optional. Eating it only if
            # it was there read `_D10TypeInfo_c6__vtbl` -- a truncated symbol -- as the
            # whole of `_D10TypeInfo_c6__vtblZ`, and refusing to fall through read
            # `_D3foo6__vtblFZv` as nothing at all, where the reference spells it
            # `foo.__vtbl()`: an ordinary function that happens to be called `__vtbl`.
            prefix = SPECIAL_COMPONENTS[path.pop()]
            reader.pos += 1
            if reader.pos != reader.end:
                raise DemangleFailure("unconsumed input after a generated symbol")
            # `_D6__initZ` has nothing left to name once the marker is taken off, and the
            # reference still writes the prefix -- `initializer for`, with no trailing
            # space. Requiring a component before it read the marker as an ordinary name
            # and answered `__init`.
            return prefix + ".".join(path) if path else prefix.rstrip()
        anonymous_last = self._last_component_anonymous
        self._trailing_had_attributes = True
        trailing = self.trailing_type()
        if anonymous_last:
            # The type belongs to the anonymous component, and the reference does not
            # spell a component it left out. `_D4core4sync5mutex5Mutex6unlock0FNeZv` is
            # an anonymous symbol inside `unlock`, and writing its parameter list after
            # the path said `unlock` was that function: `Mutex.unlock()` for a name whose
            # `()` is somewhere else. `c++filt --format=dlang` writes `Mutex.unlock`.
            # Consumed either way -- the check that the whole name was read depends on
            # it -- and only the spelling is dropped.
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
        # Whether the *last* component read was an anonymous `0`. See `parse`.
        self._last_component_anonymous = False
        while self._opens_symbol_name():
            saved, saved_depth = self.reader.pos, self.reader.depth
            # A literal `0` is skipped by `dlang_parse_qualified` with a `continue`,
            # which steps over the "consume the encoded arguments" that every other
            # component goes through. A back reference that *resolves* to an anonymous
            # component is not skipped -- it is a component that spells nothing, and its
            # type is still spelled. See `parse`.
            anonymous = self.reader.peek() == "0"
            try:
                component = self.symbol_name()
            except DemangleFailure:
                # `Q` opens both an identifier back reference and a *type* back
                # reference, and only position tells them apart. One that does not
                # point at a length-prefixed identifier is the symbol's own type
                # starting, so the path ends here.
                #
                # Nothing else is put back. `dlang_symbol_name_p` says a digit or a
                # `__T` *is* the next component, and `dlang_parse_qualified` has no
                # second reading for one that does not parse -- the name fails. Backing
                # out instead handed the digits to whatever came next: the `42` of
                # `VE3foo3bar42Z` was read as an old-style bare integer value and the
                # name spelled `test!(42)`, where the reference refuses it and a
                # compiler writes `i42`.
                if self.reader.text[saved] != "Q" or self._back_reference_targets_identifier(saved):
                    raise
                self.reader.pos, self.reader.depth = saved, saved_depth
                break
            if component == "__postblit" and self.reader.starts_with("MFZ"):
                # `dlang_lname` matches the thirteen characters `__postblitMFZ` as one
                # thing and writes `this(this)` -- no parameter list, and no rename
                # for any other shape, `MFNaZ` and `FZ` included. Renaming the last
                # component only left an interior one as `__postblit()`.
                self.reader.pos += 3
                self._last_component_anonymous = False
                parts.append("this(this)")
                continue
            if self.reader.text[saved] in DIGITS and _ANONYMOUS.fullmatch(component):
                # A `__S<n>` is a compiler scope -- a fake parent that makes a name
                # unique -- and the reference writes nothing for it. `dlang_identifier`
                # steps over it and reads the next identifier there and then: nothing
                # else may follow, not a scope type and not the end of the name.
                # `_D8demangle4mainFZ4__S1xi` is refused, where reading the `xi` as the
                # symbol's type spelled `demangle.main()`. Only the length-prefixed form
                # is skipped; `__S` alone and `__S1a` are ordinary names, and so is a
                # `__S1` reached through a back reference, which `dlang_symbol_backref`
                # spells as it stands.
                self._last_component_anonymous = False
                if not self._opens_symbol_name() or self.reader.peek() == "0":
                    # `dlang_identifier` there and then: a `0` is a length of nothing
                    # to it, refused, not the anonymous component the path loop skips.
                    raise DemangleFailure("a compiler scope with nothing after it")
                continue
            if anonymous:
                # A literal `0` is skipped whole. `dlang_parse_qualified` `continue`s past
                # it, which steps over the "consume the encoded arguments" every other
                # component goes through: a function type after it is the symbol's own,
                # never a scope's, so `_D1a0FZ1bi` is refused where reading `FZ` as the
                # anonymous component's scope spelled `a.().b`. See `parse` for what
                # becomes of the type.
                self._last_component_anonymous = True
                continue
            # A scope's own function type *is* spelled -- a symbol inside a function is
            # written `enclosing(params).inner` -- so the parameters come back here rather
            # than being discarded.
            spelled = self._spelled_component(component) + self.scope_type()
            # A back reference that resolves to an anonymous component is not skipped:
            # `dlang_symbol_backref` reads a zero-length name and appends nothing, and
            # the `.` before the next component is written all the same. So the slot is
            # kept, and `_D1a0Qb1ci` is `a..c`; dropping it spelled `a.c`.
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
        # A template instance is a *length-prefixed* identifier whose content happens to
        # be `__T<name><args>Z`. The test above catches only the bare form, so
        # `_D8demangle11__T4testTaZv` -- where `11` counts `__T4testTaZ` -- was read as
        # an identifier called `__T4testTaZ` and printed as one. The reference spells it
        # `demangle.test!(char)`, and this is the largest single group of disagreements
        # with libiberty's corpus.
        #
        # Not bounded by the length prefix while it is read: `dlang_parse_template`
        # reads the body against the whole of what remains and compares what it
        # consumed with the length afterwards, refusing the name on a mismatch. Bounding
        # it first read a mutant of `demangle.fn!(sym, val("null"))` where the reference
        # refuses it: inside the body, `sym` is followed by a `V` that opens a function
        # type whose parameter list happens to run to a `Z` far past the body, and the
        # reference reads that greedily, as it reads any scope inside a type, and then
        # finds the `v` after it is no template argument. Under the bound the greedy
        # reading failed at the body's end, was put back, and the name read.
        text = reader.text[start : start + length]
        # Five is the shortest a template instance can be -- `__T`, a one-digit length,
        # a one-character name, and the closing `Z` make six, and `dlang_identifier`
        # settles for `len >= 5`. Under that, `__T` is a name like any other: the
        # reference reads `_D4main3__TFZv` as `main.__T()`, and trying the template
        # grammar on it first refused the name.
        if text.startswith(("__T", "__U")) and length >= 5:
            spelled = self.template_instance()
            if reader.pos != start + length:
                # A body that opens `__T` and does not parse to exactly its length is a
                # malformed template, not an identifier that happens to look like one:
                # the reference refuses the whole name rather than printing the
                # mangling back inside a path.
                raise DemangleFailure("malformed template instance")
            return spelled
        if any(char.isascii() and not (char.isalnum() or char == "_") for char in text):
            # `dlang_lname` copies the bytes, but a `*` in the counted name is not a
            # D identifier: `_D1aE3foo6En961*` is refused, and so is the seed-17
            # mutant `_D3std6stream9BOMEndianyG5E3std6system6En961*`.
            # `tools/mutate.py --seed 17`.
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
            # `dlang_parse_template` refuses a template whose name is the anonymous `0`;
            # reading one spelled `!()` for `_D5__T0Zv`.
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
                # `dlang_template_symbol_param` takes the `_D` form only where a symbol
                # name follows the prefix (`dlang_symbol_name_p`); otherwise the `_D`
                # is read as a length, which it is not, and the name is refused.
                # `S_DaZv` came back as an argument spelling nothing.
                after = reader.text[reader.pos + 2 : reader.pos + 5]
                # A back reference counts as a name only where it points at one:
                # `dlang_symbol_name_p` follows the `Q` and asks for a digit there.
                # Taking any `Q` read `S_DQiZv` -- a reference into the middle of a
                # type -- as a symbol argument spelling nothing, `abc!()`, where the
                # reference refuses the name. Found by mutating real symbols.
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
                # `qualified_name` leaves out an anonymous component, which is right
                # inside a path -- the reference writes nothing for it -- and leaves an
                # `S` argument spelling nothing at all. It still took a slot, so
                # `TrieBuilder!(..., , ...)` came back with a visible empty argument in
                # the middle of the list.
                raise DemangleFailure("a symbol argument with no name")
            return self._cap(spelled)
        if marker == "V":
            # The type's own first character is kept: an associative array writes its
            # values as key/value pairs, and nothing in the *spelling* of `int[int]`
            # distinguishes it from a static array of one.
            code = self._type_code()
            kind = self.type_()
            return self.template_value(kind, code=code)
        # A bare symbol name -- a length, a `Q` back reference or a `_D` symbol with no
        # `S` in front of it -- was read here, on the grounds that the compiler emits one
        # where the argument's kind is unambiguous. It does not. `TemplateArgX` is
        # `T Type`, `V Type Value`, `S Number_opt QualifiedName` or `X` and nothing else,
        # `dlang_template_args` refuses everything else outright, and no name in either
        # corpus -- 1,257 real symbols and libiberty's own 366 vectors -- needs it.
        #
        # What it did instead was rescue malformed names into plausible ones. A mutated
        # `TSQBi...` that has lost its `S` reads the `Q` back references after it as
        # further arguments, so a qualified name came apart into
        # `PackedArrayViewImpl!(float, std, uni, BitPacked!(uint, 11uL), BitPacked, 16uL)`
        # -- five arguments where the name has two -- and a bare `0` took a slot and was
        # spelled as one, giving `!(null, )` and `!(, )` with a visible empty argument.
        if marker == "X":
            # An externally mangled argument: a length and that many characters, kept as
            # they stand because they are another mangling entirely.
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
            # As in `parse`: a type after an anonymous last component belongs to that
            # component, which the reference does not spell, so neither is the type.
            # `dlang_parse_qualified` steps past the `0` and `dlang_parse_mangle` reads
            # the type as the symbol's own, printing nothing for it: `foo.bar` for
            # `_D3foo3bar0FNbmZm`, as an argument just as at the top.
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
            # A complex literal: two reals, written `<real>+<imaginary>i`, each introduced
            # by its own `c`.
            reader.pos += 1
            real = self.real_literal()
            if not reader.eat("c"):
                raise DemangleFailure("complex literal without its imaginary part")
            return f"{real}+{self.real_literal()}i"
        if char == "A":
            # An array literal: `A <count> <value>...`, where each value has the element
            # type. `VAmA2i104i1281` is a `ulong[]` holding `[104, 1281]`. Where the type
            # is an associative array the values come in pairs -- `[1:2, 3:4]` -- and
            # neither half is spelled as the element type, because there is no one
            # element type to spell them as.
            reader.pos += 1
            count = reader.number()
            if code == "H":
                pairs = (
                    f"{self.template_value(None, suffix=False)}:{self.template_value(None, suffix=False)}"
                    for _ in range(count)
                )
                return "[" + ", ".join(pairs) + "]"
            # An element carries no type of its own: `dlang_parse_arrayliteral` reads
            # each value with no type at all, so the reference writes `[104, 1281]` for
            # a `ulong[]` -- no `uL` -- and `[0, 1]` for a `bool[]` and `[65, 66]` for a
            # `char[]`. Spelling the elements as the element type wrote `[false, true]`
            # and `['A', 'B']`.
            return "[" + ", ".join(self.template_value(None, suffix=False) for _ in range(count)) + "]"
        if char == "f":
            # A function literal: a whole mangled symbol standing where a value was
            # expected, which is how a lambda reaches a struct's field.
            reader.pos += 1
            return self.mangled_symbol()
        if char == "S":
            # A struct literal: the type's own name, then `S <count>` and that many field
            # values. A field carries no type of its own, so none of them is spelled as a
            # character or a bool however it was mangled.
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
            # `dlang_template_symbol_param` parses the region only where it opens on a
            # digit or on `_D`; anything else is left standing and the length does not
            # match. Without the test, `S1i` -- a one-character name `i` in the older
            # form -- had the `1` taken for a length and the `i` for a symbol's type,
            # and the argument the reference spells `i` was refused.
            prefixed = reader.starts_with("_D")
            if prefixed:
                reader.pos += 2
            elif reader.peek() not in DIGITS:
                return False
            self.qualified_name()
            if prefixed:
                # `dlang_parse_mangle`: `_D QualifiedName Type` or `_D QualifiedName Z`,
                # and the type is not optional. A region that is a qualified name and
                # nothing more, `_D6symbol3foo3bar2Zv`, is not a symbol to it, and the
                # argument is spelled as it stands; reading it as one spelled
                # `symbol.foo.bar.Zv`.
                if reader.pos >= end:
                    return False
                if reader.peek() == "Z":
                    reader.pos += 1
                else:
                    self.trailing_type()
            # Unprefixed: the region is a qualified name and nothing more. A leftover
            # type letter -- `S11` then `9symbol3foo`, nine characters of name and an
            # `o` -- made the length look exact because `trailing_type` ate the `o` as
            # `ifloat`, and the argument was spelled `symbol3fo`. libiberty refuses.
            # `tools/mutate.py --seed 15`.
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
                    # `dlang_symbol_backref` reads a `dlang_number` and then that many
                    # characters, so what a `Q` points at is a length-prefixed identifier
                    # and nothing else -- not a `__T` template instance, and not another
                    # `Q`. Reading whatever stood there resolved a mutated index onto a
                    # whole template and spelled it as a path component:
                    # `_D3std5range__T6ChunksTAhZQo5emptyMFNaNbNdNiNfZb` came back
                    # `std.range.Chunks!(ubyte[]).Chunks!(ubyte[]).empty()`, with the
                    # instance named twice. 56 of the 119 shapes the mutation fuzzer had
                    # this scheme reading and the reference refusing were this one.
                    raise DemangleFailure("a back reference to something that is not an identifier")
                while reader.peek() == "0" and reader.text[reader.pos + 1 : reader.pos + 2] in DIGITS:
                    # `dlang_symbol_backref` reads the length with `dlang_number`, which
                    # takes the whole digit run: `06289` is a length of 6289 and `01a` is
                    # `a`. Only a lone `0` is the empty identifier, which `symbol_name`
                    # reads as the anonymous component it is. Stopping at the first `0`
                    # read a target inside a mutated name's digits as anonymous and went
                    # on, spelling `..length` where the reference refuses the name.
                    reader.pos += 1
                if reader.peek() == "0":
                    resolved = self.symbol_name()
                else:
                    # `dlang_symbol_backref` is `dlang_number` and then `dlang_lname` --
                    # a length and that many characters, spelled as they stand, with
                    # only the constructor and destructor renames. A target whose body
                    # happens to be a template instance, `13__T4testThTuZ`, is the
                    # identifier `__T4testThTuZ` to it, where `symbol_name` read the
                    # template and spelled `test!(ubyte, wchar)` twice over. No
                    # compiler points a back reference at one; the reference's reading
                    # is the one followed.
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
                # The `this` rule, not the type rule: `dlang_parse_qualified` calls
                # `dlang_type_modifiers` here too, so `MxxF` is refused on the second `x`
                # as it is on a symbol's own `this`. Reading the run as a type spelled
                # `foo() const const.bar()` for a name the reference refuses.
                modifiers = self.this_modifiers()
            if reader.peek() not in CALLING_CONVENTIONS:
                raise DemangleFailure("not a scope")
            _, attributes, parameters, _ = self.function_type(returns=False)
            if self._in_type_name or self._in_symbol_argument:
                # Inside a type's name or a symbol argument there is no trailing type
                # for the parameters to be, so `dlang_parse_qualified` keeps them as the
                # component's scope whenever they parse and the name goes on: the
                # `QCe` after `QHxFNcQEsZ` in a mutant of `std.utf.byUTF` is the next
                # parameter of the enclosing function, and the reference spells the
                # struct `...byUTF(ByCodeUnitImpl)`. Asking for a component to follow
                # put the function type back and read it as that parameter instead.
                if reader.pos >= reader.end:
                    raise DemangleFailure("not a scope")
            else:
                # A full symbol name has to follow, not merely a byte that could open
                # one. `Qq` opens an identifier back reference *and* a type back
                # reference, so testing the byte alone made the return type of
                # `rt_linkOption` look like another path component and took the whole
                # name with it.
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
        # `suffix_modifiers`: `dlang_parse_qualified` writes a scope's `this` modifiers
        # for a symbol and for a `_D`-prefixed symbol argument, which goes through
        # `dlang_parse_mangle`, and not for a plain symbol argument or a type's name --
        # `main.S.bar().x` for `S4main1S3barMxFZ1x`, with the `const` left out.
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
        """`[M] [Nk] [I[K] | J | K | L] <Type>` -- a fixed sequence, not a set.

        `dlang_function_args` reads each of these once and in this order, and then reads
        the type. Written as a loop here, it accepted any order and any number of them:
        `FMMfZv` came back as `(scope scope float)` and `FIJfZv` as `(in out float)`,
        neither of which is a parameter anything can declare, and `FNkMfZv` reordered
        `return scope` out of the order the encoding puts it in. The reference hands all
        three back.

        `I` is the one that takes a second: `in ref`, written `IK`. Nothing else does.
        """
        reader = self.reader
        storage = []
        if reader.starts_with("NkM"):
            # `return scope`, written in that order. DMD 2.104 began writing `Nk` ahead
            # of the `M` for a `return scope` parameter, and libiberty -- which reads
            # `M` then `Nk` and nothing else -- refuses every function the LDC 1.40
            # runtime declares with one, 766 of its 16,197 symbols. D's own
            # `core.demangle` reads both orders and spells this one `return scope`,
            # which is what is followed here; the `M`-first order still reads as
            # libiberty reads it.
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

        The `N` that marks a negative value is written whatever the kind is, and it used
        to be dropped for the two kinds that do not spell their digits: `VaN17` came back
        `'\x11'` and `VbN1` came back `true`, each the *positive* literal. The reference
        writes `-'\x11'` and `-true`, which is a value D source cannot spell either --
        but losing the sign spells a different value rather than an unspellable one.
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
            # The bound is a *value*, not a length prefix into the name: `char[1234567890]`
            # is an ordinary declaration and its ten digits reach no further than the two
            # of `char[10]`.
            reader.pos += 1
            # The digits the name carried, not the number they spell. `dlang_type`'s `G`
            # case remembers where the run began and appends it verbatim, so `G012a` is
            # `char[012]`; re-formatting it wrote `char[12]`, a different bound. Same rule
            # as an integer literal -- see `_Reader.digits`.
            _, count = reader.digits()
            return self._cap(f"{self.type_()}[{count}]")
        if char == "H":
            reader.pos += 1
            key = self.type_()
            return self._cap(f"{self.type_()}[{key}]")
        if char == "P":
            reader.pos += 1
            # D spells a pointer to a function as `int(char[]) function`, not with a `*`:
            # the word *is* the pointer. Only the `P` that *is* that word absorbs, though,
            # and `dlang_type` decides that from the character after the `P` -- a calling
            # convention, and nothing else. Deciding it from the pointee's *spelling*
            # instead swallowed every level above the first: `PPUZi` and `PPPUZi` both
            # came back `extern(C) int() function`, so a pointer to a function pointer
            # was spelled as the function pointer itself.
            absorbs = reader.peek() in CALLING_CONVENTIONS
            pointee = self.type_()
            return self._cap(pointee if absorbs else f"{pointee}*")
        if char in ("C", "S", "E", "T"):
            # `C <QualifiedName>` and its three siblings, where the name is not optional:
            # `dlang_parse_qualified` reads at least one symbol name and fails otherwise.
            # This joined an empty list and returned `""`, so `_D3fooC` -- a variable
            # whose type is a class with no name -- came back as `foo`, and `_D3fooFCZv`
            # as `foo()` with the parameter gone. The reference hands both back.
            #
            # Measured on how far the cursor moved rather than on what came out: a
            # zero-length component is anonymous and spells nothing, and `_D3fooC0` is a
            # name the reference does read.
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
                # The function type is a back reference: `MxDQsm` is a delegate whose
                # signature was written earlier in the name. `dlang_type_backref` is
                # called with `is_function` set and reads a *function type* at the
                # target, so a `Q` pointing at anything else fails the name. Resolving
                # it as a type spelled `real delegate*` for `PDQg` where the reference
                # refuses.
                target = self._back_reference_target(reader.pos)
                if target is None or reader.text[target] not in CALLING_CONVENTIONS:
                    raise DemangleFailure("a delegate's back reference does not point at a function type")
                inner = self.type_back_reference()
                spelled = inner.removesuffix(" function")
                return self._cap(" ".join(["", spelled, "delegate", *modifiers]).strip())
            convention, attributes, parameters, returns = self.function_type()
            spelled = f"{convention}{returns}({', '.join(parameters)})"
            # A delegate's *attributes* belong to the function it wraps and are written
            # before the word; its own modifiers qualify the delegate and are written
            # after it -- `char() pure delegate const`.
            return self._cap(" ".join([spelled, *attributes, "delegate", *modifiers]))
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
            # Reached from inside the resolution of a back reference that stands at or
            # before this one: the chain has turned round. See `_last_backref`.
            raise DemangleFailure("a type back reference read from inside its own target")
        self._follows -= 1
        if self._follows < 0:
            raise _Exhausted
        # What a target reads as depends on the bound in force while it is read, so the
        # bound is part of the key.
        key = (target, "type", self._in_symbol_argument, self._last_backref)
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
        has_this = reader.eat("M")
        if has_this:
            modifiers = self.this_modifiers()
        char = reader.peek()
        if has_this and char not in CALLING_CONVENTIONS:
            # `M` is the `this` parameter of a member function, so a function type has
            # to follow it. `dlang_parse_mangle` sets `is_function` on seeing it and
            # calls `dlang_function_type`, which fails without a calling convention.
            # Reading a plain type instead dropped the `M`, the modifiers and the type,
            # so `_D4test3fooMf` came back as `test.foo` -- a variable, from a symbol
            # that says it is a member function.
            raise DemangleFailure("a `this` parameter with no function type after it")
        if char in CALLING_CONVENTIONS:
            _, attributes, parameters, _ = self.function_type(returns=False)
            # `_D QualifiedName Z` -- and the last component of the path may carry a
            # parameter list of its own, so a function's `Z` can be followed by the
            # artificial symbol's `Z` rather than a return type. `dlang_parse_qualified`
            # reads the parameters, `dlang_parse_mangle` takes the `Z`, and c++filt
            # spells `_D4main3fooFZZ` as `main.foo()`. Reading a return type there
            # refused it. The return type itself is never printed, only consumed.
            if not reader.eat("Z"):
                self.type_()
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
