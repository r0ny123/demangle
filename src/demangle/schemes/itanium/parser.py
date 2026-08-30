"""A recursive-descent parser for the Itanium C++ ABI mangling scheme.

This is the scheme GCC and Clang emit, and by extension almost every C++ toolchain
outside the Microsoft ecosystem. It is also used, in its older form, by Rust's legacy
mangling.

The parser is written against `core.builder.Builder`: it recognises grammar productions
and reports them, never constructing output itself. See ARCHITECTURE.md for why.

Structure of this file mirrors the specification's own ordering -- names, then types,
then template arguments, then expressions -- so a production can be found by its
section number. Grammar comments quote the ABI verbatim.

Reference: Itanium C++ ABI section 5.1, https://itanium-cxx-abi.github.io/cxx-abi/abi.html
A transcription of the productions is vendored at docs/specs/itanium-grammar.txt.
"""

import re

from ...core.errors import LimitExceeded, NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.reader import DIGITS, Reader
from .options import DEFAULT_OPTIONS
from .substitutions import (
    DeferredProduction,
    ParameterReference,
    SubstitutionTable,
    TemplateArgumentTable,
)
from .tables import (
    BUILTIN_TYPES,
    CONSTRUCTOR_KINDS,
    CV_COMBINATIONS,
    DESTRUCTOR_KINDS,
    EXTENDED_BUILTIN_TYPES,
    INFIX_OPERATORS,
    LOOSEST_PRECEDENCE,
    OPERATORS,
    POSTFIX_OPERATORS,
    POSTFIX_PRECEDENCE,
    PRECEDENCE,
    PREFIX_OPERATORS,
    PRIMARY_PRECEDENCE,
    QUALIFIER_LETTERS,
    RIGHT_ASSOCIATIVE,
    SIMPLE_PRECEDENCE,
    SPECIAL_ENCODING_NAMES,
    SPECIAL_ENCODING_NAMES_GNU,
    SPECIAL_TYPE_NAMES,
    STD_ABBREVIATIONS,
    STD_ABBREVIATIONS_EXPANDED,
    STD_ABBREVIATIONS_EXPANDED_GNU,
    TIGHT_INFIX,
    UNARY_PRECEDENCE,
)

__all__ = ["ItaniumParser", "detect", "parse", "parse_type"]

#: Characters that can open a <type>. Used only to decide whether an ambiguous
#: expression position holds a type, so it errs towards inclusion.
_TYPE_STARTERS = frozenset("vwbcahstijlmxynofdegzPRODCGUFAMTSN0123456789")

#: <template-param-decl> introducers. None can be confused with a <template-param>,
#: which is always `T_` or `T` followed by digits.
_PARAMETER_DECLARATIONS = frozenset({"Ty", "Tk", "Tn", "Tt", "Tp"})


#: N1169 fixed-point types. The letter after `DA`/`DR` names the underlying integer,
#: and a plain `int` contributes no word of its own: `DAi` is `_Accum`, `DAs` is
#: `short _Accum`, `DAm` is `unsigned long _Accum`.
_FIXED_POINT_INTEGERS = {
    "s": "short ",
    "t": "unsigned short ",
    "i": "",
    "j": "unsigned ",
    "l": "long ",
    "m": "unsigned long ",
}


#: Clang's allocation-token prefix, `__alloc_token_[<digits>_]` before an ordinary
#: mangled name. It marks the allocation call a hardened allocator should account to a
#: particular type, and it carries no part of the name, so it is stripped and reported as
#: a suffix -- which is what the reference prints.
_ALLOC_TOKEN = "__alloc_token_"

#: What follows a block invocation function's encoding. The leading group is greedy, so
#: the split is at the *last* `_block_invoke`: an enclosing function may be called that
#: itself. A number may follow with or without its own underscore, but an underscore
#: with no number after it is not one of these names at all.
_BLOCK_INVOKE = re.compile(r"(.*)_block_invoke(?:_\d+|\d*)(?:\..*)?\Z", re.DOTALL)

#: What a comma expression binds at. An operand that binds no tighter than this needs
#: brackets to sit in a comma-separated list.
_COMMA_BINDING = PRECEDENCE["cm"]

#: `Ts`, `Tu` and `Te` name a dependent type with the keyword the writer used.
_ELABORATED_KEYWORDS = {"s": "struct", "u": "union", "e": "enum"}

#: What a `<template-param>` index looks like where an elaborated specifier would have a
#: name: `Ts0_` is the pack marker, `TsN...E` is `struct ...`.
_INDEX_START = frozenset("0123456789_")

#: Clang's vendor qualifier for "conforms to this Objective-C protocol", and the type
#: that a pointer to it is spelled `id<...>` rather than `objc_object<...>*`.
#: How the two references spell C99's complex and imaginary qualifiers, indexed by
#: `gnu_complex_spelling`.
_COMPLEX_WORDS = (
    {"C": "complex", "G": "imaginary"},
    {"C": "_Complex", "G": "_Imaginary"},
)

#: How many characters the prefixes of one name may build, as a multiple of the output
#: bound. Every `<prefix>` is a substitution candidate (5.1.10), so a nested name of N
#: components records N entries -- and each entry is the whole prefix, so their sizes sum
#: to O(N^2). No single one exceeds `max_output`, which is why that bound never fired:
#: `_ZN` and 8,190 components of `1a` is 16KB of input that read in a second and
#: allocated 98MB, which is more work than one symbol should be able to buy.
#:
#: Sixteen times the output bound is a megabyte by default. The worst of the 217,730
#: distinct Itanium symbols in the shared libraries of a stock Ubuntu 24.04 records
#: 10,209 characters -- the median is 92 -- so this is a hundred times what the largest
#: real name needs, and it caps that 16KB name at about a megabyte and ten milliseconds.
_PREFIX_BUDGET = 16

#: The characters that open a `<prefix>` component that is *not* an
#: `<unqualified-name>`: a substitution, a template parameter, a decltype, a template
#: argument list, a closure-prefix terminator, a requires-clause.
_PREFIX_MARKERS = frozenset("STDIMQ")

#: What opens a `<class-enum-type>`: a length-prefixed name, a nested name, a local name
#: or an internal-linkage marker. One membership test in place of four comparisons, on
#: the arm `_type` takes for a third of every type it reads.
_CLASS_ENUM_START = frozenset(DIGITS | {"N", "Z", "L"})

#: The operators that measure or interrogate a type, as `(expression form, opening
#: text)`. One branch rather than seven: they differ only in the word and in whether the
#: operand is a type or an expression.
_MEASURING_OPERATORS = {
    "st": ("sizeof", "sizeof"),
    "sz": ("sizeof", "sizeof"),
    "at": ("alignof", "alignof"),
    "az": ("alignof", "alignof"),
    "ti": ("typeid", "typeid"),
    "te": ("typeid", "typeid"),
    "nx": ("noexcept", "noexcept"),
}

#: The three of those whose operand is a `<type>` rather than an `<expression>`. Both
#: references bracket a type here and neither brackets it twice.
_MEASURING_A_TYPE = frozenset({"st", "at", "ti"})

#: A callee GNU c++filt leaves unbracketed: a plain identifier, a `::`-qualified path of
#: them, or a function parameter. Anything else -- a template-id, an operator name, a
#: name rooted at global scope -- it wraps, because the text would otherwise run into the
#: bracket that follows it.
_PLAIN_CALLEE = re.compile(r"(?:[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*|\{parm#\d+\})\Z")

_OBJC_PROTOCOL = "objcproto"
_OBJC_OBJECT = "objc_object"


#: The C escapes, by the value they stand for. Values below seven have none, and are
#: written as a single octal digit -- `\0`, `\1`, `\6` -- which is what the reference does.
_STRING_ESCAPES = {7: "\\a", 8: "\\b", 9: "\\t", 10: "\\n", 11: "\\v", 12: "\\f", 13: "\\r"}

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _string_literal(values):
    r"""Spell a run of character values as a quoted string, the way the reference does.

    A `char` array in a braced initialiser is a string: `tl A6_c Lc72E Lc101E ...` is
    `"Hello"`, not `char [6]{(char)72, (char)101, ...}`. Both say the same thing and one
    of them is readable.

    Bytes above 127 are decoded as UTF-8 where they form it, so an emoji in a template
    argument comes back as itself rather than as four escapes. Where they do not, each
    byte is escaped on its own.

    The one subtlety is `"\xF""ello"`. A hex escape has no length limit in C, so `\xF`
    followed by `e` would read as `\xFe`; the reference closes the string and opens
    another rather than emit something that means a different thing.
    """
    raw = bytes(value & 0xFF for value in values)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    out = []
    previous_was_hex_escape = False
    for character in text:
        code = ord(character)
        if character == '"':
            piece = '\\"'
        elif character == "\\":
            piece = "\\\\"
        elif code < 7:
            piece = f"\\{code}"
        elif code in _STRING_ESCAPES:
            piece = _STRING_ESCAPES[code]
        elif code < 0x20 or code == 0x7F:
            piece = f"\\x{code:X}"
        else:
            piece = character
        if previous_was_hex_escape and piece[:1] in _HEX_DIGITS:
            out.append('""')
        out.append(piece)
        previous_was_hex_escape = piece.startswith("\\x")
    return '"' + "".join(out) + '"'


def detect(name):
    """Cheap test for "is this plausibly an Itanium mangled name".

    Runs on every symbol a caller passes, including the overwhelming majority that are
    not mangled at all, so it does no work beyond a prefix comparison.
    """
    return (
        name.startswith("_Z")
        or name.startswith("__Z")
        or name.startswith("___Z")
        or name.startswith("____Z")
        or name.startswith("_GLOBAL__")
        or name.startswith(_ALLOC_TOKEN)
    )


#: `void` is four characters, and `builder.size` is O(1) on both builders. Filtering on
#: the width before spelling anything is what keeps `_is_all_void` from rendering a
#: parameter list that was never going to be `(void)`.
_VOID_WIDTH = 4


def _drops_out(builder, handle):
    """Whether a parsed parameter spells to nothing and so is not a parameter at all.

    `Dp T_` over a pack bound to nothing expands to no parameters, and must not leave a
    separator behind. Asked with `size` rather than by spelling the parameter: `size` is
    O(1) by contract, where `spell` on the tree builder is a full recursive render of
    the subtree -- run here once per parameter of every function in the symbol table,
    for a question that is only ever "is it empty".
    """
    return builder.size(handle) == 0


def _is_all_void(builder, parameters):
    """Whether every parameter is `void`, which is how the scheme spells "none".

    C++ writes `f()` where the mangling writes `f(void)`. The width test comes first so
    that an ordinary signature is settled without spelling anything; only a list whose
    parameters are all four characters wide can be the one this is looking for.
    """
    for parameter in parameters:
        if builder.size(parameter) != _VOID_WIDTH:
            return False
    return all(builder.spell(parameter) == "void" for parameter in parameters)


class ItaniumParser:
    """Parses one mangled name into one builder. Single use.

    State -- cursor, substitution table, template scope -- is per name, so an instance
    is cheap and never shared. The builder, by contrast, is stateless and shared.
    """

    __slots__ = (
        "_abbrev",
        "_abbrev_expanded",
        "_ctor_dtor",
        "_deferred",
        "_depth",
        "_drop_return",
        "_entity_local",
        "_entity_shape",
        "_explicit_object",
        "_in_constraint",
        "_mangled",
        "_max_depth",
        "_max_output",
        "_module_names",
        "_modules",
        "_naming",
        "_objc_id_ids",
        "_objc_ids",
        "_pack_arity",
        "_pack_ids",
        "_pack_index",
        "_packs",
        "_parameter_counts",
        "_parameter_uses",
        "_pending_conversion",
        "_precedence",
        "_prefixes",
        "_productions",
        "_reject_unbound_parameters",
        "_rework",
        "_saw_empty_pack",
        "_saw_pack",
        "_scope_has_pack",
        "_simple_name",
        "_size",
        "_trailing_empty_pack",
        "_try_template_args",
        "builder",
        "limits",
        "options",
        "reader",
        "subs",
        "targs",
    )

    def __init__(self, mangled, builder, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
        if len(mangled) > limits.max_input:
            raise LimitExceeded(mangled, "input length", limits.max_input)
        self._mangled = mangled
        self.options = options
        expanded = STD_ABBREVIATIONS_EXPANDED_GNU if options.expand_std_abbreviations else STD_ABBREVIATIONS_EXPANDED
        self._entity_shape = None
        self._simple_name = False
        self._entity_local = False
        self._abbrev_expanded = expanded
        self._abbrev = expanded if options.expand_std_abbreviations else STD_ABBREVIATIONS
        self.reader = Reader(mangled)
        self.builder = builder
        # The one builder method the parser calls on every type it reads, bound once.
        self._size = builder.size
        self.limits = limits
        # The two bounds every production checks, read once. `limits` is a frozen
        # dataclass, so reaching through it is two attribute loads on a path taken
        # around four hundred thousand times over a symbol table of forty thousand.
        self._max_depth = limits.max_depth
        self._max_output = limits.max_output
        self._prefixes = _PREFIX_BUDGET * limits.max_output
        self.subs = SubstitutionTable(mangled, limits.max_substitutions)
        self.targs = TemplateArgumentTable()
        # How many times a template parameter has been resolved. A production that
        # advanced this depended on the scope it ran under, so the entry it contributed
        # is kept as its input span rather than as the handle it built -- see
        # `DeferredProduction`.
        self._parameter_uses = 0
        # Re-read spans, keyed by (entry index, scope generation). Without it a chain of
        # entries each built over the one before -- `T_`, `P S0_`, `P S1_`, ... -- costs
        # one walk of the whole chain per back-reference, which is quadratic in the name.
        # With it each entry is read at most once per scope, and the scope only changes
        # where the grammar installs one: over the 217,730 distinct Itanium symbols in
        # the shared libraries of a stock Ubuntu 24.04 the highest any name reached was
        # 15 scopes, 784 names re-read anything at all, and the largest re-read 303
        # characters of its own 362. `tests/test_substitution_parameters.py` pins that a
        # chain of 400 stays flat, which is what fails if this memo is ever mis-keyed.
        self._deferred = {}
        self._depth = 0
        # Precedence of the expression just parsed, read by a containing operator to
        # decide whether it needs brackets. Primary by default: most expressions are
        # names or literals and never need any.
        self._precedence = PRIMARY_PRECEDENCE
        # Identities of template arguments that were argument packs. A pack is already
        # spelled as its comma-separated members, so expanding it must not also append
        # an ellipsis.
        # Pack handles seen in this name. The list holds strong references so no address
        # can be reused, and the set of ids makes membership O(1) -- an `id()`-keyed set
        # *alone* is the trap: CPython reuses an address once an object is collected, so
        # it can report a brand new handle as a pack it has never seen. Silent and
        # data-dependent, which is the worst kind. Keeping both is what makes it safe.
        self._packs = []
        self._pack_ids = set()

        # Whether the name just parsed was a constructor or destructor. They are the
        # one case where a template specialisation still encodes no return type.
        self._ctor_dtor = False
        # True while reading the name of the entity being declared, false once its
        # signature begins. Only a name's own template arguments become the `T_` scope;
        # a `basic_string<T_, T0_, T1_>` mentioned in a parameter list must resolve
        # against the enclosing function's arguments, not replace them.
        self._naming = True
        # Module names by handle identity, and the strong references that keep those
        # identities from being reused. A module name is a substitution candidate, so a
        # `S<n>_` in a prefix may turn out to be one -- and it decorates the name that
        # follows it rather than being a component of its own.
        self._modules = []
        self._module_names = {}
        # Handles for a protocol-qualified `objc_object`, which a pointer collapses into
        # `id<...>`. Tracked by identity with a strong reference beside it, the same way
        # packs and module names are.
        self._objc_ids = []
        self._objc_id_ids = set()
        # Types read so far, and how many this parse may read a *second* time.
        #
        # Two productions re-read a span they have already read -- a conversion
        # operator's type, and a pack expansion's pattern -- and expansions nest, so the
        # work is the product of the arities: at eight members a pattern seven
        # expansions deep is read eight million times for sixty bytes of input. The
        # charge is the work each re-reading actually does rather than the width of the
        # span, because the width of an outer expansion says nothing about the
        # expansions inside it. Charged as it is spent rather than measured afterwards,
        # which is the whole point of a bound, and generous against `max_output` so that
        # a name whose expansion is within that bound can still finish.
        self._productions = 0
        self._rework = 2 * limits.max_output + 4096
        # Set by `parse_type`, and only there. See the `auto` fallback in
        # `template_param`: in a whole symbol an unbound `T_` may still be bound later,
        # so it is spelled rather than refused. A bare `<type>` has no enclosing
        # template at all and never will, so there `auto` would be an invented answer.
        self._reject_unbound_parameters = False
        # Set for exactly one encoding: the function enclosing a local name, whose
        # return type GNU c++filt omits. The type is still parsed -- it is there in the
        # input either way -- and then discarded.
        self._drop_return = False
        # Whether the name just read declares its object parameter explicitly, `N H ...`.
        # C++23 lets a member function name the object it is called on, and the
        # reference marks that parameter `this`.
        self._explicit_object = False
        # Where an unread conversion operator's type starts, and how many substitution
        # entries there were before it: `(position, mark)`, or None. See
        # `_reread_conversion`.
        self._pending_conversion = None
        # Whether a `<template-param>` at the head of a type may take template arguments
        # of its own. False while reading a conversion operator's type, where an `I`
        # that follows opens the *operator's* argument list: in `cvT_I4MerpE` the
        # argument belongs to the operator, and reading it as `T_<Merp>` leaves the
        # operator's own list unread and the name unreadable.
        self._try_template_args = True
        # Whether the template arguments currently in scope include a parameter pack.
        # An expansion is written `Dp <type>`, and what it expands to is the pack's
        # members -- so when a pack is in scope the ellipsis has already been spent and
        # printing one would double it. With no pack in scope the expansion is
        # unexpanded and the ellipsis is the whole point.
        self._scope_has_pack = False
        # Per-kind counters for the synthetic `$T` / `$N` / `$TT` names a generic
        # lambda's declared template parameters are spelled with.
        self._parameter_counts = {}
        # True while reading a requires-clause, where parameters are spelled by name.
        self._in_constraint = False
        # Set when a <template-param> read since the flag was last cleared resolved to a
        # pack with no members. A `Dp` pattern that mentions one expands to *nothing*,
        # and there is no other way to tell: the pattern spells fine on its own -- as
        # `std::decay<>::type` -- and only the emptiness of the pack it ranges over says
        # there are no copies of it.
        self._saw_empty_pack = False
        # Whether the type just read mentioned a parameter bound to a pack. An
        # expansion whose pattern did has already been expanded -- its members are
        # spelled out -- so an ellipsis would be spelling it a second time.
        self._saw_pack = False
        # How many members the pack a pattern mentions has, and which of them is being
        # spelled. `Dp` reads its pattern once per member with `_pack_index` set, which
        # is what turns `Dp unary<T_>` into `unary<int>, unary<float>` rather than the
        # one type `unary<int, float>`.
        self._pack_arity = None
        self._pack_index = None
        # Whether the argument list just read ended in a pack with no members. GNU
        # c++filt writes `A<B<int>>` for that and `A<B<int> >` for everything else, and
        # the pack is dropped rather than kept, so the answer has to travel beside the
        # list rather than be recoverable from it.
        self._trailing_empty_pack = False

    # -- recursion control -----------------------------------------------------

    # The depth guard is written out at each of its seven sites rather than called.
    # A production enters and leaves 76,662 times over the Itanium corpus, and the pair
    # is two whole interpreter frames to add one to an integer and take it away again.
    # The shape at every site is the same:
    #
    #     depth = self._depth = self._depth + 1
    #     if depth > self._max_depth:
    #         raise LimitExceeded(self._mangled, "recursion depth", self._max_depth)
    #     try:
    #         ...
    #     finally:
    #         self._depth = depth - 1
    #
    # The restore is absolute -- `self._depth = depth - 1`, not `self._depth -= 1` -- and
    # that is the part worth stating. When the guard itself raises, the increment has
    # already happened and no `finally` of its own undoes it, so a relative decrement
    # left every enclosing frame unwinding from a value one too high. Nothing observes
    # it today: a parser is built fresh per name and the one `except` in this file
    # cannot see a `LimitExceeded`. It is still the wrong number to leave behind.

    # -- entry point -----------------------------------------------------------

    def parse(self):
        """<mangled-name> ::= _Z <encoding> [. <vendor-specific suffix>]"""
        reader = self.reader
        if reader.startswith("___Z") or reader.startswith("____Z"):
            return self.block_invocation()
        # `__alloc_token_[<digits>_]` wraps an ordinary mangled name. It says which
        # allocation a hardened allocator should account to which type and names no part
        # of the entity, so it comes off the front and goes back on as a suffix -- which
        # is how the reference prints it. `__alloc_token_malloc` is not one: what follows
        # has to be a mangled name in its own right, and `malloc` is not.
        alloc_token = ""
        if reader.startswith(_ALLOC_TOKEN):
            after = reader.pos + len(_ALLOC_TOKEN)
            digits = after
            while digits < reader.length and reader.text[digits] in DIGITS:
                digits += 1
            if digits > after and digits < reader.length and reader.text[digits] == "_":
                after = digits + 1
            if reader.text.startswith(("_Z", "__Z"), after):
                reader.pos = after
                alloc_token = " (.alloc_token)"
        # A Mach-O symbol table carries the extra leading underscore the linker adds, so
        # `__Z...` and `_Z...` name the same thing. Strip it only when a second
        # underscore follows, or `_Z1fv` would lose the one the grammar needs.
        if reader.startswith("__Z"):
            reader.take()
        if not reader.eat("_Z"):
            raise NotMangledError(self._mangled, "not an Itanium mangled name")

        result = self.encoding()

        if not reader.eof:
            suffix = reader.remaining
            # A clone suffix -- `.cold`, `.part.0`, `.llvm.<hash>`, a coroutine's
            # `.actor` -- can only be identified here, as what the grammar could not
            # consume. It cannot be split off in advance: a `.` also occurs *inside*
            # identifiers, notably in the frame types Clang synthesises for coroutines.
            if suffix.startswith("."):
                result = self.builder.decorated(result, suffix)
            else:
                raise ParseError(self._mangled, reader.pos, f"unconsumed input {suffix!r}")

        if alloc_token:
            result = self.builder.decorated(result, alloc_token)
        if self.builder.size(result) > self._max_output:
            raise LimitExceeded(self._mangled, "output length", self._max_output)
        return result

    def block_invocation(self):
        """`___Z <encoding> _block_invoke [[_] <digits>] [. <suffix>]`, a Clang extension.

        The function a block's body compiles to. Its name is the enclosing function's,
        with an underscore in front for the block and another for the leading one a
        Mach-O symbol table adds -- which is why `___Z` is three underscores and
        `____Z` is four.

        The reference finds the encoding's end by parsing until a type will not read and
        then requiring the literal; this bounds the encoding at the literal instead,
        which is the same split and does not need the parameter loop to swallow an
        error. Bounded at the *last* occurrence, because a function may be called
        `_block_invoke` itself and the encoding is as long as it can be.

        Neither the trailing number nor a `.` suffix is printed: the number distinguishes
        several blocks in one function, and what a caller is told is which function the
        block was written in.
        """
        reader = self.reader
        # The prefix ends in `Z`, so the two spellings cannot be confused for one
        # another however they are tried: `____Z` does not start with `___Z`.
        if not (reader.eat("___Z") or reader.eat("____Z")):
            raise NotMangledError(self._mangled, "not an Itanium mangled name")
        found = _BLOCK_INVOKE.match(reader.remaining)
        if found is None:
            raise NotMangledError(self._mangled, "not a block invocation function")
        whole = reader.length
        reader.length = reader.pos + len(found.group(1))
        try:
            result = self.builder.special("invocation function for block in ", self.encoding())
            if not reader.eof:
                raise ParseError(self._mangled, reader.pos, f"unconsumed input {reader.remaining!r}")
        finally:
            reader.length = whole
        reader.pos = whole
        return result

    def encoding(self):
        """<encoding> ::= <function name> <bare-function-type> | <data name> | <special-name>

        Every path out of here records what the entity *was* in `_entity_shape`, for
        `_entity_operand` to read back. An encoding nested inside this one -- a
        `L _Z... E` in the template arguments -- writes it first and this overwrites it,
        which is the order that leaves the outermost answer standing.
        """
        special = self.special_name()
        if special is not None:
            self._entity_shape = ("special", None)
            return special

        name, quals, ref_qualifier, is_template = self.name()

        reader = self.reader
        if reader.eof or reader.peek() in ("E", "."):
            # A data symbol: a name and nothing after it, and so no return type for a
            # conversion operator's name to have suppressed.
            self._drop_return = False
            self._explicit_object = False
            self._entity_shape = ("data", name)
            return name
        return self.bare_function_type(name, quals, ref_qualifier, is_template)

    def bare_function_type(self, name, quals=(), ref_qualifier="", is_template=False):
        """<bare-function-type> ::= <signature type>+

        The first type is the return type *only* for a template specialisation: a plain
        function does not encode its return type, because overloads cannot differ by it.
        For a template they can, so it is part of the signature (5.1.5.3).
        """
        builder = self.builder
        reader = self.reader
        attributes = ""
        if reader.startswith("Ua9enable_ifI"):
            # `Ua <source-name> <template-arg>* E` is a vendor attribute; `enable_if` is
            # the only one either reference spells, and it goes after the signature.
            reader.pos += len("Ua9enable_ifI")
            conditions = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated enable_if attribute")
                condition, _ = self.template_arg()
                if condition is not None:
                    conditions.append(builder.spell(condition))
            attributes = f" [enable_if:{', '.join(conditions)}]"
        explicit_object = self._explicit_object
        self._explicit_object = False
        was_naming = self._naming
        self._naming = False
        try:
            returns = self.type_() if is_template else None
            if self._drop_return:
                returns = None
                self._drop_return = False

            parameters = []
            while not reader.eof and reader.peek() not in ("E", ".", "Q"):
                parameter = self.type_()
                if explicit_object and not parameters:
                    explicit_object = False
                    parameter = builder.raw("this " + builder.spell(parameter))
                if not _drops_out(builder, parameter):
                    parameters.append(parameter)
        finally:
            self._naming = was_naming

        if parameters and _is_all_void(builder, parameters):
            parameters = []

        suffix = ""
        if quals:
            suffix += " " + " ".join(quals)
        if ref_qualifier:
            suffix += " " + ref_qualifier
        suffix += attributes
        if reader.eat("Q"):
            # The requires-clause closes the declaration, after the qualifiers.
            suffix += " requires " + builder.spell(self.constraint_expression())
        # A function whose declaration is nothing but a name and a parameter list is the
        # one shape GNU prints as `&Name`; everything a `suffix` or a return type adds is
        # something it would have to drop. See `gnu_entity_operand_spelling`.
        self._entity_shape = ("function", name if returns is None and not suffix else None)
        return builder.function(returns, parameters, suffix, name)

    # -- 5.1.4 special names ---------------------------------------------------

    def special_name(self):
        """Vtables, typeinfo, thunks, guard variables. None if this is not one.

        Guarded: several of these productions contain an `<encoding>`, which can be
        another special name, so `_Z` followed by `GV` repeated is unbounded recursion.
        """
        depth = self._depth = self._depth + 1
        if depth > self._max_depth:
            raise LimitExceeded(self._mangled, "recursion depth", self._max_depth)
        try:
            return self._special_name()
        finally:
            self._depth = depth - 1

    def _special_name(self):
        reader = self.reader
        code = reader.peek2()

        if code in SPECIAL_TYPE_NAMES:
            reader.pos += 2
            return self.builder.special(SPECIAL_TYPE_NAMES[code], self.type_())

        if code == "GI":
            # <special-name> ::= GI <module-name>
            reader.pos += 2
            return self.builder.special("initializer for module ", self.builder.raw(self.module_name()))

        if code == "TA":
            # <special-name> ::= TA <template-arg>
            #
            # The object a class-type template argument is bound to. Its operand is a
            # <template-arg> rather than a <type>, so `_ZTAX...E` carries an expression.
            reader.pos += 2
            argument, _ = self.template_arg()
            if argument is None:
                raise ParseError(self._mangled, reader.pos, "template parameter object without an argument")
            return self.builder.special("template parameter object for ", argument)

        if code == "GR":
            # GR <object name> _  /  GR <object name> <seq-id> _
            reader.pos += 2
            inner = self.name()[0]
            # A seq-id numbers the temporary when a scope has more than one. Where
            # there is none there is no `_` either, and demanding one refused
            # `_ZGRZN1N1gEvE1a`.
            if not reader.eof:
                reader.seq_id()
            return self.builder.special(self._encoding_special_label("GR"), inner)

        if code in SPECIAL_ENCODING_NAMES:
            reader.pos += 2
            return self.builder.special(self._encoding_special_label(code), self.encoding())

        if code == "GT":
            reader.pos += 2
            marker = reader.take()
            label = "transaction clone for " if marker == "t" else "non-transaction clone for "
            return self.builder.special(label, self.encoding())

        if code == "TC":
            # <special-name> ::= TC <type> <offset number> _ <base type>
            #
            # A construction vtable: the one used while a base subobject is being built,
            # so it names *two* types and both references spell it "<base>-in-<derived>".
            # The offset is where the base sits in the derived object and neither
            # reference prints it. Both types are ordinary <type> productions and enter
            # the substitution table as such -- `_ZTCSt9strstream16_Si` uses `Si` for
            # `std::istream` because `St` was recorded reading the first one.
            reader.pos += 2
            derived = self.type_()
            reader.number()
            reader.expect("_")
            base = self.type_()
            builder = self.builder
            return builder.special(
                "construction vtable for ",
                builder.expression("construction-vtable", [base, "-in-", derived]),
            )

        if code == "Tc":
            # Tc <call-offset> <call-offset> <base encoding>
            reader.pos += 2
            self.call_offset()
            self.call_offset()
            return self.builder.special("covariant return thunk to ", self.encoding())

        if reader.peek() == "T" and reader.ahead(1) in ("h", "v"):
            # T <call-offset> <base encoding>
            reader.take()
            virtual = reader.peek() == "v"
            self.call_offset()
            label = "virtual thunk to " if virtual else "non-virtual thunk to "
            return self.builder.special(label, self.encoding())

        return None

    def _encoding_special_label(self, code):
        """How this style words the special name `code`."""
        if self.options.gnu_special_name_spelling:
            gnu = SPECIAL_ENCODING_NAMES_GNU.get(code)
            if gnu is not None:
                return gnu
        return SPECIAL_ENCODING_NAMES[code]

    def call_offset(self):
        """<call-offset> ::= h <nv-offset> _ | v <v-offset> _"""
        reader = self.reader
        kind = reader.take()
        if kind not in ("h", "v"):
            raise ParseError(self._mangled, reader.pos, "expected a call offset")
        reader.number()
        if kind == "v":
            reader.expect("_")
            reader.number()
        reader.expect("_")

    # -- 5.1.2 names -----------------------------------------------------------

    def name(self, as_type=False):
        """<name> ::= <nested-name> | <unscoped-name>
                    | <unscoped-template-name> <template-args> | <local-name>

        Returns (handle, cv-qualifiers, ref-qualifier, is-template-specialisation).
        The qualifiers belong to the *function* the name introduces rather than to the
        name, but the nested-name production is where they are encoded, so they travel
        back out with it.
        """
        reader = self.reader
        char = reader.peek()

        if char == "N":
            return self.nested_name(as_type)
        if char == "Z":
            return self.local_name(as_type), (), "", False

        if char == "S":
            # Either an abbreviation or a back-reference, each of which may be an
            # <unscoped-template-name> that template arguments then attach to.
            if reader.ahead(1) == "t":
                reader.pos += 2
                inner = self.unqualified_name()
                base = self.builder.qualified([self.builder.name("std"), inner])
            else:
                base = self.substitution()
                named = self._module_of(base)
                if named is not None:
                    base = self.unqualified_name(module=named)
            if reader.peek() == "I":
                # An <unscoped-template-name> is a substitution candidate in its own
                # right (5.1.10), recorded before the arguments that specialise it.
                # Verified against both references: in `_ZSt4sortIPiEvT_S_`, `S_` is
                # `std::sort`.
                self.subs.remember(base, "unscoped-template-name")
                return self.apply_template_args(base), (), "", True
            return base, (), "", False

        base = self.unqualified_name()
        if reader.peek() == "I":
            self.subs.remember(base, "unscoped-template-name")
            return self.apply_template_args(base), (), "", True
        return base, (), "", False

    def _spend_prefix(self, combined):
        """Charge a recorded prefix against the budget, or refuse the name.

        See `_PREFIX_BUDGET`. Reported as the output bound because that is what it
        bounds -- the characters this name may cause to be built -- and it is the one a
        caller raises to allow more, the same way `_spend_rework` reports it.
        """
        self._prefixes -= self._size(combined)
        if self._prefixes < 0:
            raise LimitExceeded(self._mangled, "output length", self._max_output)
        return combined

    def _spend_rework(self, characters):
        """Charge a re-reading against the budget, or refuse the name."""
        self._rework -= characters
        if self._rework < 0:
            raise LimitExceeded(self._mangled, "output length", self._max_output)

    def _expand_pattern(self, start, mark, arity):
        """Read a pack expansion's pattern once per member of the pack it ranges over.

        The reader and the substitution table are put back exactly as the first reading
        left them, so a back-reference later in the name still counts what that reading
        entered and nothing the re-readings did.
        """
        reader = self.reader
        resume = reader.pos
        recorded = self.subs.capture(mark)
        outer_index = self._pack_index
        members = []
        try:
            for index in range(arity):
                spent = self._productions
                reader.pos = start
                self.subs.restore_from(mark, [])
                self._pack_index = index
                members.append(self.type_())
                self._spend_rework(self._productions - spent)
        finally:
            self._pack_index = outer_index
            reader.pos = resume
            self.subs.restore_from(mark, recorded)
        return self.builder.parameter_pack(members)

    def _reread_conversion(self, pending):
        """Spell a conversion operator's type again, now that its arguments are bound.

        `operator T_<char>` is written with the type before the arguments, so the first
        reading of it has nothing to resolve `T_` against and spells `auto`. Rather than
        carry an unresolved node through the builders -- the spelling builder has no
        node to carry -- the span is simply read a second time, with the table wound
        back so the second reading records what the first did and no more, and then
        wound forward again to where the arguments left it.
        """
        start, mark = pending
        reader = self.reader
        resume = reader.pos
        recorded = self.subs.capture(mark)
        scope = self.targs.snapshot()
        # Not naming anything the second time round: the arguments are already in
        # scope, and a list nested inside the type -- `Muncher<T_*...>` has one -- would
        # otherwise install itself over them, which is what made the first reading
        # spell `auto` in the first place.
        was_naming = self._naming
        was_trying = self._try_template_args
        had_pack = self._scope_has_pack
        self._naming = False
        self._try_template_args = False
        # The arguments now in scope are the operator's own, so whether an expansion in
        # the type has a pack to expand is decided by them.
        self._scope_has_pack = any(id(argument) in self._pack_ids for argument in self.targs.outer())
        self.subs.restore_from(mark, [])
        reader.pos = start
        spent = self._productions
        try:
            return "operator " + self.builder.spell(self.type_())
        finally:
            self._spend_rework(self._productions - spent)
            self._scope_has_pack = had_pack
            self._try_template_args = was_trying
            self._naming = was_naming
            self.targs.restore(scope)
            reader.pos = resume
            self.subs.restore_from(mark, recorded)

    def _conversion_pending(self):
        """The conversion operator's type waiting to be read again, cleared. Or None."""
        pending = self._pending_conversion
        self._pending_conversion = None
        return pending

    def apply_template_args(self, base):
        """Attach <template-args> to a name.

        Deliberately records nothing. A function template specialisation is not a
        substitution candidate: 5.1.10 excludes function names, and both reference
        demanglers reject `_Z1fIiEvS0_`, proving the table for `_Z1fIiEv...` holds only
        `f`. Where the specialisation is a *type* rather than a function, `_type()`
        records it, because there the candidate is <type>.
        """
        pending = self._conversion_pending()
        arguments = self.template_arguments(install_scope=True)
        angle_space = not self._trailing_empty_pack
        if pending is not None:
            base = self.builder.name(self._reread_conversion(pending))
        return self.builder.template(base, arguments, angle_space)

    def nested_name(self, as_type=False):
        """A qualified name.

        ```
        <nested-name> ::= N [<CV-qualifiers>] [<ref-qualifier>] <prefix> <unqualified-name> E
                        | N [<CV-qualifiers>] [<ref-qualifier>] <template-prefix> <template-args> E
        ```
        """
        reader = self.reader
        reader.expect("N")
        # `N H` marks a C++23 explicit object member function. It stands where the
        # qualifiers would be, and there are none: what would have qualified the
        # implicit object parameter is written on the explicit one instead.
        quals = ()
        ref_qualifier = ""
        if reader.peek() == "H":
            # Licensed by `peek`: the character it just returned is the one consumed.
            reader.pos += 1
            self._explicit_object = True
        else:
            quals = self.cv_qualifiers()
            char = reader.peek()
            if char == "R":
                reader.pos += 1
                ref_qualifier = "&"
            elif char == "O":
                reader.pos += 1
                ref_qualifier = "&&"

        parts = []
        is_template = False
        module = ""
        outer_ctor_dtor = self._ctor_dtor
        self._ctor_dtor = False
        try:
            max_depth = self._max_depth
            peek = reader.peek
            while True:
                char = peek()
                if char == "E":
                    # Licensed by `peek`, as above.
                    reader.pos += 1
                    break
                if not char:
                    raise ParseError(self._mangled, reader.pos, "unterminated nested name")
                depth = self._depth = self._depth + 1
                if depth > max_depth:
                    raise LimitExceeded(self._mangled, "recursion depth", max_depth)
                try:
                    is_template, module = self.prefix_component(parts, as_type, module)
                finally:
                    self._depth = depth - 1

            if not parts:
                raise ParseError(self._mangled, reader.pos, "empty nested name")
            # A template constructor -- `basic_string<allocator<char>>(char const*, ...)`
            # -- is a template, but constructors have no return type to encode, so the
            # leading type of the signature is a parameter like any other.
            if self._ctor_dtor:
                is_template = False
        finally:
            self._ctor_dtor = outer_ctor_dtor
        name = parts[0] if len(parts) == 1 else self.builder.qualified(parts)
        return name, quals, ref_qualifier, is_template

    def prefix_component(self, parts, as_type=False, module=""):
        """One component of a <prefix>, appended to `parts`.

        Returns `(is-template-specialisation, module)`. Only the first answer for the
        final component reaches the caller, and this is where it is known. The second is
        a C++20 module name this component turned out to be rather than contain, which
        decorates the component *after* it.

        Every <prefix> is a substitution candidate (5.1.10) *except* the last, which is
        an <unqualified-name> -- and function and operator names are explicitly excluded.
        We cannot tell we are on the last component until the closing `E` is in sight,
        so the check is "is the next character `E`".
        """
        reader = self.reader
        builder = self.builder
        char = reader.peek()

        # Three components in four are a length-prefixed name and none of the markers
        # below: one membership test sends those straight to the tail rather than
        # through six comparisons that will all fail.
        if char in _PREFIX_MARKERS:
            if char == "S":
                component = self.substitution(expanded=self._abbreviation_scopes_a_structor())
                named = self._module_of(component)
                if named is not None:
                    # A module name, not a scope: it belongs to the component that follows.
                    return False, named
                parts.append(component)
                return False, module

            if char == "I":
                # <template-prefix> <template-args>: the arguments attach to the component
                # just read, and the pair becomes one substitutable component.
                if not parts:
                    raise ParseError(self._mangled, reader.pos, "template arguments with no name")
                pending = self._conversion_pending()
                arguments = self.template_arguments(install_scope=True)
                angle_space = not self._trailing_empty_pack
                if pending is not None:
                    parts[-1] = builder.name(self._reread_conversion(pending))
                parts[-1] = builder.template(parts[-1], arguments, angle_space)
                combined = parts[0] if len(parts) == 1 else builder.qualified(parts)
                # Only an *interior* <template-prefix> <template-args> is a separate
                # candidate. When the closing `E` follows, this specialisation is the whole
                # nested-name, and the enclosing <type> production records it -- recording
                # here too would enter it twice and shift every later index by one.
                if reader.peek() != "E":
                    self.subs.remember(self._spend_prefix(combined), "prefix")
                return True, module

            if char == "T":
                component, reference = self.template_param_binding()
                parts.append(component)
                self.subs.remember(reference if reference is not None else component, "template-template-param")
                return False, module

            if char == "D" and reader.ahead(1) in ("t", "T"):
                parts.append(self.decltype_())
                return False, module

            if char == "M":
                # <closure-prefix> terminator; carries no spelling of its own.
                reader.take()
                return False, module

            if char == "Q":
                # A C++20 requires-clause, `Q <constraint-expression>`. It constrains the
                # template but is not part of its name, and neither reference demangler
                # prints it -- so it is parsed for its side effects on the substitution
                # table and otherwise discarded. Newer than the grammar snapshot in
                # docs/specs/.
                reader.take()
                outer_constraint = self._in_constraint
                self._in_constraint = True
                try:
                    self.expression()
                finally:
                    self._in_constraint = outer_constraint
                return False, module

        component = self.unqualified_name(scope=parts, module=module)
        parts.append(component)
        if reader.peek() != "E":
            combined = parts[0] if len(parts) == 1 else builder.qualified(parts)
            self.subs.remember(self._spend_prefix(combined), "prefix")
        return False, ""

    def local_name(self, as_type=False):
        """<local-name> ::= Z <function encoding> E <entity name> [<discriminator>]
        | Z <function encoding> E s [<discriminator>]
        | Z <function encoding> Ed [<parameter number>] _ <entity name>
        """
        reader = self.reader
        builder = self.builder
        reader.expect("Z")
        # `Z <encoding> E` holds a complete function declaration, with template
        # parameters of its own. Without a fresh naming context its `T_` and `T0_`
        # resolve against whatever enclosing template mentioned this local entity --
        # which is exactly what happens when a lambda defined inside one function
        # template is passed as an argument to another.
        outer_naming = self._naming
        outer_scope = self.targs.snapshot()
        self._naming = True
        outer_drop_return = self._drop_return
        outer_explicit_object = self._explicit_object
        self._drop_return = not self.options.local_name_return_type
        self._explicit_object = False
        try:
            outer = self.encoding()
        finally:
            self._drop_return = outer_drop_return
            self._explicit_object = outer_explicit_object

        if reader.eat("Ed"):
            # `d [<number>] _` says the entity lives in a default argument rather than
            # in the function body. The number is a compact one -- absent is zero -- and
            # names which default argument, so GNU's `{default arg#1}` is it plus one.
            argument = 0
            if reader.peek() != "_":
                argument = int(reader.number(allow_negative=False)) + 1
            reader.expect("_")
            try:
                entity_is_type = as_type or reader.peek() == "U"
                inner, quals, ref_qualifier, is_template = self.name()
                parts = [outer, inner]
                if self.options.gnu_default_argument_scope:
                    parts.insert(1, builder.raw(f"{{default arg#{argument + 1}}}"))
                combined = builder.qualified(parts)
                if not entity_is_type and not reader.eof and reader.peek() not in ("E", "_"):
                    combined = self.bare_function_type(combined, quals, ref_qualifier, is_template)
            finally:
                self._naming = outer_naming
                self.targs.restore(outer_scope)
            return combined

        reader.expect("E")

        if reader.eat("s"):
            self.discriminator()
            self._naming = outer_naming
            self.targs.restore(outer_scope)
            return builder.qualified([outer, builder.raw("string literal")])

        try:
            # A closure or unnamed type is a *type*, so nothing follows it. Any other
            # entity may be a function, in which case its signature does. Without this
            # test, `Z1gvEUlvE_S_` reads the following parameter as the lambda's
            # signature and the substitution table is a parameter short from then on.
            entity_is_type = as_type or reader.peek() == "U"
            inner, quals, ref_qualifier, is_template = self.name()
            # The signature is applied to the *combined* name, not to the entity alone:
            # a return type belongs at the front of the whole declaration, so a generic
            # lambda's `operator()` reads `auto f()::'lambda'<...>::operator()(...)` and
            # not `f()::auto 'lambda'...`.
            combined = builder.qualified([outer, inner])
            if (
                not entity_is_type
                and not reader.eof
                and reader.peek() not in ("E", "_")
                and not self._at_bare_discriminator()
            ):
                combined = self.bare_function_type(combined, quals, ref_qualifier, is_template)
            self.discriminator()
        finally:
            self._naming = outer_naming
            self.targs.restore(outer_scope)
        return combined

    def _at_bare_discriminator(self):
        """Whether what is left is a discriminator written without its `_`.

        The grammar says `_ <number>`, and a compiler writes it that way -- but not
        always. Clang emits a bare digit run for a local static whose name already ends
        in a digit, and the reference accepts it *only* when it runs to the end of the
        name, which is what keeps it from being confused with a length-prefixed anything.

        Without this, `_ZZN12_GLOBAL__N_115ARMDAGToDAGISel6SelectEPN4llvm6SDNodeEE7Opcodes8`
        read its trailing `8` as the start of a signature and the whole name was refused.
        65 of the names in libcxxabi's own corpus are this shape -- the largest single
        group of refusals in it.
        """
        reader = self.reader
        at = reader.pos
        text, end = reader.text, reader.length
        if at >= end or text[at] not in DIGITS:
            return False
        while at < end and text[at] in DIGITS:
            at += 1
        return at == end

    def discriminator(self):
        """Tells apart same-named entities in one function.

        ```
        <discriminator> ::= _ <non-negative number> | __ <number> _
        ```

        Tells apart same-named entities in one function. It carries no spelling, but it
        has to be consumed or it looks like trailing junk.
        """
        reader = self.reader
        if reader.peek() != "_":
            if self._at_bare_discriminator():
                reader.pos = reader.length
            return
        if reader.ahead(1) == "_":
            saved = reader.pos
            reader.pos += 2
            try:
                reader.number(allow_negative=False)
                reader.expect("_")
            except ParseError:
                reader.pos = saved
            return
        if reader.ahead(1) in DIGITS:
            reader.take()
            reader.number(allow_negative=False)

    # -- 5.1.2 unqualified names -----------------------------------------------

    def unqualified_name(self, scope=None, module=""):
        """<unqualified-name> ::= [<module-name>] <name body> [<abi-tags>]

        ```
        <name body> ::= <operator-name> | <ctor-dtor-name> | <source-name>
                      | <unnamed-type-name> | DC <source-name>+ E
        ```

        `module` is a C++20 module name the caller already read -- a <prefix> can reach
        one through a substitution -- and is spelled after the name: `Foo@MOD`.
        """
        reader = self.reader
        builder = self.builder
        char = reader.peek()
        if char == "W":
            module = self.module_name(module)
            char = reader.peek()
        # `F` marks a friend declared inside the class it is a friend of. The scope is
        # already in `parts`, which `qualified` joins with `::`, so the marker is the
        # word that follows the last `::`.
        friend = ""
        if char == "F" and scope:
            # Licensed by `peek`: the character it just returned is the one consumed
            # here, with nothing in between.
            reader.pos += 1
            friend = "friend "
            char = reader.peek()

        if char in DIGITS:
            name = self.plain_source_name()
            # `_in_module` is a call for a question that is almost always no, on the
            # production every component of every qualified name goes through.
            if module:
                name = f"{name}@{module}"
            tags = self.abi_tags() if reader.peek() == "B" else ""
            return builder.name(friend + name + tags) if friend else builder.name(name + tags)

        if char == "L":
            # An internal-linkage name. The marker carries no spelling, but it recurses,
            # so a run of them has to be bounded like any other recursive production.
            reader.take()
            depth = self._depth = self._depth + 1
            if depth > self._max_depth:
                raise LimitExceeded(self._mangled, "recursion depth", self._max_depth)
            try:
                inner = self.unqualified_name(scope, module)
            finally:
                self._depth = depth - 1
            return builder.name(friend + builder.spell(inner)) if friend else inner

        if char == "C":
            return self.constructor_name(scope, module)

        if char == "D":
            following = reader.ahead(1)
            if following in DESTRUCTOR_KINDS:
                reader.pos += 2
                self._ctor_dtor = True
                return builder.name(self._in_module("~" + self.enclosing_class_name(scope), module))
            if following == "C":
                # A structured binding declaration: DC <source-name>+ E
                reader.pos += 2
                names = []
                while not reader.eat("E"):
                    if reader.eof:
                        raise ParseError(self._mangled, reader.pos, "unterminated structured binding")
                    names.append(self.source_name())
                return builder.name(friend + self._in_module("[" + ", ".join(names) + "]", module))

        if char == "U":
            return self.unnamed_type_name()

        operator = builder.name(friend + self._in_module(self.operator_name(), module) + self.abi_tags())
        if reader.peek() != "I":
            # Nothing is going to bind a conversion operator's template parameters, so
            # there is nothing to read again. Cleared here rather than left to expire,
            # because the next template argument list in the name is somebody else's.
            self._pending_conversion = None
        return operator

    def constructor_name(self, scope, module=""):
        """A constructor name.

        ```
        <ctor-dtor-name> ::= C1 | C2 | C3 | CI1 <base class type> | CI2 <base class type>
        ```
        """
        reader = self.reader
        reader.expect("C")
        self._ctor_dtor = True
        if reader.eat("I"):
            # An inheriting constructor names the base it inherits from.
            reader.take()
            self.type_()
            return self.builder.name(self._in_module(self.enclosing_class_name(scope), module))
        marker = reader.take()
        if marker not in CONSTRUCTOR_KINDS:
            raise ParseError(self._mangled, reader.pos, f"unknown constructor variant {marker!r}")
        return self.builder.name(self._in_module(self.enclosing_class_name(scope), module) + self.abi_tags())

    def enclosing_class_name(self, scope):
        """The bare class name that a constructor or destructor repeats.

        `Foo::Foo` is encoded as "the class, then a constructor marker" -- the marker
        has no spelling of its own, so the name is read back off the scope we are
        standing in. A specialisation's constructor drops the arguments: the constructor
        of `Foo<int>` is spelled `Foo<int>::Foo`, not `Foo<int>::Foo<int>`.
        """
        if not scope:
            raise ParseError(self._mangled, self.reader.pos, "constructor outside any class scope")
        spelled = self.builder.spell(scope[-1])
        # Drop everything the class name carries but the constructor does not: template
        # arguments (`Foo<int>::Foo`, never `Foo<int>::Foo<int>`) and ABI tags
        # (`failure[abi:cxx11]::failure`). Both attach directly to the class name, so
        # cutting at whichever comes first removes them and nothing else.
        cut = min((index for index in (spelled.find("<"), spelled.find("[")) if index > 0), default=-1)
        if cut > 0:
            spelled = spelled[:cut]
        # Then take the last component. A scope reached through an abbreviation arrives
        # as one part rather than as separate prefixes -- `Sa` is the single component
        # `std::allocator` -- and the class name is only its tail, so without this the
        # constructor of `std::allocator<char>` reads `std::allocator<char>::std::allocator`.
        separator = spelled.rfind("::")
        return spelled[separator + 2 :] if separator >= 0 else spelled

    def source_name(self):
        """<source-name> ::= <positive length number> <identifier>"""
        text = self.plain_source_name()
        return text + self.abi_tags() if self.reader.peek() == "B" else text

    def plain_source_name(self):
        """A <source-name> without the ABI tags that may follow it.

        Split out because a module name goes *between* the two: `3FooB3ABI` inside
        module `MOD` is `Foo@MOD[abi:ABI]`, not `Foo[abi:ABI]@MOD`.
        """
        reader = self.reader
        length, text = reader.length_prefixed()
        if length <= 0:
            raise ParseError(self._mangled, reader.pos, "source name of non-positive length")
        if text.startswith("_GLOBAL__N"):
            # The compiler's spelling for an anonymous namespace.
            return "(anonymous namespace)"
        return text

    def _identifier(self):
        """The <source-name> of an ABI tag: a length and an identifier, and no more.

        `source_name` reads any tags that follow the identifier, which is right
        everywhere but here: inside a tag, a following `B` opens the *next* tag of the
        run rather than one nested in this one. Reading it as nested spelled
        `f[abi:foo[abi:bar]]()` for what is two tags on one name.
        """
        reader = self.reader
        length = int(reader.digits())
        if length <= 0:
            raise ParseError(self._mangled, reader.pos, "abi tag of non-positive length")
        return reader.take_exactly(length)

    def module_name(self, module=""):
        """A C++20 module name, as the text that goes after the `@`.

        ```
        <module-name>    ::= <module-subname>+ | <substitution>
        <module-subname> ::= W <source-name> | W P <source-name>
        ```

        Submodules join with `.` and a partition with `:`, so `W3FooWP3BarW3Baz` is
        `Foo:Bar.Baz`. Every prefix of the run is a substitution candidate in its own
        right, which is how `S1_` comes to stand for `FOO.BAR` in a later parameter.

        Newer than the grammar snapshot in docs/specs/.
        """
        reader = self.reader
        while reader.peek() == "W":
            reader.pos += 1
            partition = reader.eat("P")
            subname = self.plain_source_name()
            if module or partition:
                module += ":" if partition else "."
            module += subname
            handle = self.builder.raw(module)
            # Strong reference and identity, the same arrangement packs use: an id-keyed
            # map alone would report a reused address as a module it has never seen.
            self._modules.append(handle)
            self._module_names[id(handle)] = module
            self.subs.remember(handle, "module-name")
        return module

    def _module_of(self, handle):
        """The module name a substitution stands for, or None if it stands for a type."""
        return self._module_names.get(id(handle))

    @staticmethod
    def _in_module(text, module):
        return f"{text}@{module}" if module else text

    def abi_tags(self):
        """ABI tags, spelled `[abi:tag]`.

        ```
        <abi-tags> ::= <abi-tag>*     <abi-tag> ::= B <source-name>
        ```

        Callers on a hot path ask `reader.peek() == "B"` before calling: the answer is no
        for all but a handful of names in a symbol table, and asking it here costs a whole
        interpreter frame to return the empty string -- five times per name over the
        Itanium corpus, which was 9% of every `peek` the parser makes. The test is
        repeated below so that a caller who does not ask is still right.
        """
        reader = self.reader
        if reader.peek() != "B":
            return ""
        tags = []
        while reader.peek() == "B":
            reader.pos += 1
            tags.append(f"[abi:{self._identifier()}]")
        return "".join(tags)

    def unnamed_type_name(self, lambda_expression=False):
        """A closure or unnamed type.

        `lambda_expression` spells the closure as the *expression* that made it rather
        than as its type -- `[](){...}`, which is what a lambda written as a template
        argument reads as.

        ```
        <unnamed-type-name> ::= Ut [<number>] _ | <closure-type-name>
        <closure-type-name> ::= Ul <lambda-sig> E [<number>] _
        ```
        """
        reader = self.reader
        reader.expect("U")

        if reader.eat("t"):
            index = reader.digits() if reader.peek() in DIGITS else ""
            reader.expect("_")
            if self.options.gnu_closure_spelling:
                return self.builder.raw(f"{{unnamed type#{int(index) + 2 if index else 1}}}")
            return self.builder.raw(f"'unnamed{index}'")

        if reader.eat("l"):
            # <lambda-sig> ::= <template-param-decl>* [Q <constraint>] <parameter type>+
            #
            # A generic lambda declares its template parameters first, and they become
            # the `T_` scope its own signature is written against -- *replacing* the
            # enclosing one rather than extending it. ABI 5.1.8: a use of `auto` in the
            # parameter list is mangled as the corresponding artificial parameter, so a
            # `T_` the lambda did not declare is an `auto` and not the enclosing
            # template's argument. Extending the scope resolved those against whatever
            # the enclosing template happened to have, printing `int&&` where the
            # parameter was written `auto&&`.
            declarations = []
            saved_counts = self._parameter_counts
            saved_scope = self.targs.snapshot()
            self._parameter_counts = {}
            if self._naming:
                # A lambda that *is* the entity being named starts from nothing: its own
                # parameters are level 0 and no enclosing list is reachable. One written
                # inside a type or an expression keeps the levels around it, and its own
                # list goes on top -- which is how `TL0__` in a lambda inside a template
                # function's parameter reaches the lambda while `T_` reaches the
                # function's arguments.
                self.targs.clear()
            declared = []
            self.targs.push(declared)
            constraint = ""
            try:
                while reader.peek2() in _PARAMETER_DECLARATIONS:
                    _, declaration = self.template_param_decl(params=declared)
                    declarations.append(declaration)
                if reader.eat("Q"):
                    constraint = f" requires {self.builder.spell(self.constraint_expression())} "
                parameters = []
                while not reader.eat("E"):
                    if reader.eof:
                        raise ParseError(self._mangled, reader.pos, "unterminated lambda signature")
                    if reader.eat("Q"):
                        # A trailing requires-clause, after the parameters.
                        trailing = self.builder.spell(self.constraint_expression())
                        reader.expect("E")
                        return self._closure(
                            declarations, constraint, parameters, f" requires {trailing}", lambda_expression
                        )
                    parameters.append(self.builder.spell(self.type_()))
            finally:
                self._parameter_counts = saved_counts
                self.targs.restore(saved_scope)
            return self._closure(declarations, constraint, parameters, "", lambda_expression)

        if reader.eat("b"):
            # <unnamed-type-name> ::= Ub [<number>] _ -- an Objective-C block literal.
            if reader.peek() in DIGITS:
                reader.digits()
            reader.expect("_")
            return self.builder.raw("'block-literal'")

        raise ParseError(self._mangled, reader.pos, "unknown unnamed-type-name")

    def _closure(self, declarations, constraint, parameters, trailing, lambda_expression=False):
        """Spell a closure type, once its signature has been read."""
        reader = self.reader
        template_header = f"<{', '.join(declarations)}>" if declarations else ""
        if parameters == ["void"]:
            parameters = []
        index = reader.digits() if reader.peek() in DIGITS else ""
        reader.expect("_")
        signature = f"{template_header}{constraint}({', '.join(parameters)}){trailing}"
        if lambda_expression:
            # The lambda that made the closure, not the closure: `[]` and its declarator
            # and a body the mangling does not carry. The discriminator says which
            # closure in the enclosing scope this is, and no spelling of the expression
            # writes it.
            return self.builder.raw(f"[]{signature}{{...}}")
        if self.options.gnu_closure_spelling:
            number = int(index) + 2 if index else 1
            return self.builder.raw(f"{{lambda{signature}#{number}}}")
        return self.builder.raw(f"'lambda{index}'{signature}")

    def operator_name(self):
        """<operator-name>, including conversions and literal operators."""
        reader = self.reader
        code = reader.peek2()

        if code == "cv":
            # A conversion operator names the type it converts to, and that type may
            # reference the operator's own template parameters -- which are written
            # *after* it. When it does, the position and the table mark are kept so the
            # type can be read again once the arguments are in scope; see
            # `_reread_conversion`.
            #
            # A conversion operator also encodes no return type, however template it is:
            # what it returns is in its name.
            reader.pos += 2
            start = reader.pos
            mark = self.subs.mark()
            was_trying = self._try_template_args
            self._try_template_args = False
            try:
                spelled = "operator " + self.builder.spell(self.type_())
            finally:
                self._try_template_args = was_trying
            self._pending_conversion = (start, mark)
            # Only the entity's own name suppresses a return type. A conversion operator
            # mentioned inside an expression -- `&A::operator int` as a template
            # argument -- is not this declaration's name, and letting it set the flag
            # would drop the return type of whatever function the name belongs to.
            if self._naming:
                self._drop_return = True
            return spelled

        if code == "li":
            reader.pos += 2
            return 'operator"" ' + self.source_name()

        if len(code) == 2 and code[0] == "v" and code[1] in DIGITS:
            # A vendor extended operator: v <digit> <source-name>
            reader.pos += 2
            return "operator " + self.source_name()

        if code in OPERATORS:
            reader.pos += 2
            spelling, needs_space = OPERATORS[code]
            return "operator" + (" " if needs_space else "") + spelling

        raise ParseError(self._mangled, reader.pos, f"unknown operator code {code!r}")

    # -- 5.1.10 substitutions --------------------------------------------------

    def _abbreviation_scopes_a_structor(self):
        """Whether an abbreviation at the cursor is the scope of a constructor or destructor.

        It decides how the abbreviation is spelled. `Ss::c_str` prints as
        `std::string::c_str()`, but `Ss`'s constructor prints as
        `std::basic_string<char, ...>::basic_string()` -- because the constructor is
        named for the class, and the class is the template, not the typedef. Both
        reference demanglers agree, and it is why `_ZNSdC1EOSd` spells its scope in full
        while spelling the very same abbreviation short as a parameter type.
        """
        reader = self.reader
        if reader.peek2() not in STD_ABBREVIATIONS:
            return False
        following = reader.ahead2(2)
        if len(following) != 2:
            return False
        return (following[0] == "C" and following[1] in CONSTRUCTOR_KINDS) or (
            following[0] == "D" and following[1] in DESTRUCTOR_KINDS
        )

    def substitution(self, expanded=False):
        """A back-reference or a predefined abbreviation.

        ```
        <substitution> ::= S <seq-id> _ | S_ | St | Sa | Sb | Ss | Si | So | Sd
        ```

        `expanded` forces the full spelling of an abbreviation regardless of style,
        which is what a constructor's scope needs: a constructor is named for its class,
        and the class is the template rather than the typedef.
        """
        reader = self.reader
        reader.expect("S")
        code = "S" + reader.peek()
        table = self._abbrev_expanded if expanded else self._abbrev
        if code in table:
            reader.take()
            tags = self.abi_tags()
            if tags:
                # 5.1.2: where a name that would use a built-in substitution carries ABI
                # tags, the tags are appended and *the result* is a substitutable
                # component. The abbreviation on its own is not one -- the encoder never
                # had to enter it either -- so only the tagged form is remembered.
                return self.subs.remember(self.builder.raw(table[code] + tags), "type")
            return self.builder.raw(table[code])
        index = reader.seq_id()
        entry = self.subs.lookup(index)
        kind = type(entry)
        if kind is ParameterReference:
            # The entry is the parameter, not what it was bound to where it was
            # recorded; those differ whenever the back-reference is read under a
            # different template scope. `bind_template_param` does the pack handling
            # `_pack_aware` would, so it is not applied twice. See `ParameterReference`.
            return self.bind_template_param(entry.index, entry.level)
        if kind is DeferredProduction:
            # The same, for a component built *over* a parameter. See
            # `DeferredProduction`.
            return self._pack_aware(self._reread(index, entry))
        return self._pack_aware(entry)

    def _pack_aware(self, handle):
        """Report a pack, and stand in for one of its members while one is being read.

        A pack reaches a pattern as a `<template-param>` most of the time, but it can
        also arrive as a back-reference -- `Dp N S3_ 4type E` names its members through
        `S3_` -- and an expansion has to range over it either way.
        """
        members = self.builder.members(handle)
        if members is None:
            return handle
        self._saw_pack = True
        if not members:
            self._saw_empty_pack = True
        if self._pack_arity is None:
            self._pack_arity = len(members)
        if self._pack_index is not None and self._pack_index < len(members):
            return members[self._pack_index]
        return handle

    def template_param(self):
        """A reference to a template parameter, resolved against the scope in force."""
        return self.template_param_binding()[0]

    def template_param_binding(self):
        """A <template-param>, as both what it resolves to and the reference itself.

        ```
        <template-param> ::= T_ | T <parameter-2 non-negative number> _
                           | TL <level> _ [<parameter-2 non-negative number>] _
        ```

        The `TL` form names a parameter of an enclosing template by level as well as by
        index, which Clang emits inside the constraints of a nested template. It is
        newer than the grammar snapshot in docs/specs/.

        Returns `(handle, reference)`. The reference is what a caller recording this
        parameter as a substitution candidate must store -- see `ParameterReference` for
        why the resolved handle is the wrong thing to keep. It is `None` inside a
        constraint, where the parameter is spelled by its own mangled text and so cannot
        mean anything different when the entry is read again.
        """
        reader = self.reader
        begin = reader.pos
        reader.expect("T")

        # `TL <level-1> _` names the level; a bare `T` is level 0, the innermost
        # enclosing <template-args>. The levels above that are the lists a generic
        # lambda and a template template parameter declare, in nesting order.
        level = 0
        if reader.eat("L"):
            level = int(reader.digits()) + 1
            reader.expect("_")
        else:
            # Tp/Ts mark a pack expansion of the parameter; the pack was recorded as one
            # argument, so the marker only needs consuming.
            reader.eat("p") or reader.eat("s")
        index = 0 if reader.peek() == "_" else reader.integer(allow_negative=False) + 1
        reader.expect("_")

        if self._in_constraint and self.options.symbolic_constraint_parameters:
            # Inside a requires-clause the references spell a parameter by its own
            # mangled name -- `T_` is `T`, `TL0__` is `TL0_` -- rather than substituting
            # the argument bound to it, because not every enclosing template's
            # parameters are tracked well enough to substitute reliably.
            return self.builder.raw(reader.text[begin : reader.pos - 1]), None
        return self.bind_template_param(index, level), ParameterReference(index, level)

    def bind_template_param(self, index, level=0):
        """What `TL<level>_<index>_` names under the arguments currently in scope."""
        reader = self.reader
        self._parameter_uses += 1
        bound = self.targs.lookup(index, level)
        if bound is not None:
            if id(bound) in self._pack_ids:
                self._saw_pack = True
                if not self.builder.spell(bound):
                    self._saw_empty_pack = True
                members = self.builder.members(bound)
                if members is not None:
                    if self._pack_arity is None:
                        self._pack_arity = len(members)
                    if self._pack_index is not None and self._pack_index < len(members):
                        return members[self._pack_index]
            return bound

        if self._reject_unbound_parameters:
            # A bare `<type>` carries no template arguments and cannot acquire any, so
            # nothing will ever bind this parameter. Both `c++filt -t` and
            # `__cxa_demangle` refuse such an encoding rather than name a type that is
            # not in it.
            raise ParseError(self._mangled, reader.pos, "template parameter with nothing to bind it")

        if level and level >= self.targs.depth():
            # A level that is not in scope at all -- `TL8_1_` where nothing is eight
            # templates deep. There is no parameter for this to be, so the reference
            # refuses the name rather than naming one, and so does this. Inside a
            # requires-clause it is spelled by its own mangled text instead, which is
            # the branch above.
            raise ParseError(self._mangled, reader.pos, f"no template parameter level {level} in scope")

        # Level 0, or a level in scope whose parameter is not. A generic lambda's `auto`
        # parameter is mangled as a reference to a parameter it never declared (ABI
        # 5.1.8), and a conversion operator's type is written *before* the arguments
        # that bind it -- `cv PT_ I c E` reads `T_` with nothing in scope at all, and is
        # then read again once there is.
        #
        # llvm-cxxfilt spells both `auto`. GNU c++filt numbers them: `[](auto a, auto b)`
        # is `{lambda(auto:1, auto:2)#1}`, by the parameter's index and not by its
        # position, so `Ul T0_ T_ E` is `(auto:2, auto:1)`. The number is what tells two
        # of a lambda's parameters apart when both are `auto`; the conversion operator
        # never shows it, because that reading is thrown away and made again.
        if self.options.gnu_closure_spelling:
            return self.builder.raw(f"auto:{index + 1}")
        return self.builder.raw("auto")

    def _symbolic_parameter(self, index):
        """A template parameter spelled by name rather than by the argument bound to it."""
        return self.builder.raw("T" + ("" if index == 0 else str(index - 1)))

    def decltype_(self):
        """<decltype> ::= Dt <expression> E | DT <expression> E"""
        reader = self.reader
        reader.expect("D")
        marker = reader.take()
        if marker not in ("t", "T"):
            raise ParseError(self._mangled, reader.pos, "expected a decltype")
        expression = self.expression()
        reader.expect("E")
        keyword = "decltype " if self.options.gnu_expression_spelling else "decltype"
        return self.builder.expression("decltype", [keyword, "(", expression, ")"])

    # -- 5.1.5 types -----------------------------------------------------------

    def cv_qualifiers(self):
        """cv-qualifiers, returned in C++'s canonical order.

        ```
        <CV-qualifiers> ::= [r] [V] [K]
        ```

        At most one of each, in the order the grammar gives. Consuming a whole run
        instead would fold `KK` into a single qualified type where the ABI has two
        nested ones -- one substitution entry rather than two, shifting every later
        back-reference in the name.
        """
        reader = self.reader
        # The common answer, and why it is asked for before anything is consumed: most
        # types carry no qualifiers, and finding that out through `eat` three times
        # costs three frames where one lookahead settles it.
        if reader.peek() not in QUALIFIER_LETTERS:
            return ()
        mask = 0
        if reader.eat("r"):
            mask = 1
        if reader.eat("V"):
            mask |= 2
        if reader.eat("K"):
            mask |= 4
        return CV_COMBINATIONS[mask]

    def type_(self):
        self._productions += 1
        depth = self._depth = self._depth + 1
        if depth > self._max_depth:
            raise LimitExceeded(self._mangled, "recursion depth", self._max_depth)
        subs = self.subs
        start = self.reader.pos
        uses, entries = self._parameter_uses, len(subs)
        try:
            result = self._type()
            # Checked here, on every type, rather than once on the finished name. The
            # substitution scheme lets each component be built from two copies of an
            # earlier one -- `M S_ S_` doubles -- so a few hundred bytes of input can
            # describe gigabytes of output. Measuring the finished string would mean
            # building the very thing the bound exists to prevent; `size()` is O(1).
            if self._size(result) > self._max_output:
                raise LimitExceeded(self._mangled, "output length", self._max_output)
            # A template parameter was resolved while this production ran, so what it
            # spells depends on the scope -- and the entry it just contributed must be
            # re-read under the scope a later back-reference is written in, not frozen to
            # this one. `_type` records the whole production last, after anything nested
            # in it, so the entry to replace is the final one. See `DeferredProduction`.
            # Ordered by how often each is false. Nearly every type in a symbol table
            # resolves no template parameter at all, and that test is one attribute load
            # and a comparison, where the others reach into the table.
            if (
                self._parameter_uses != uses
                and subs.recording
                and len(subs) > entries
                and type(subs.last) is not ParameterReference
            ):
                subs.defer_last(start, self.reader.pos)
            return result
        finally:
            self._depth = depth - 1

    def _reread(self, index, span):
        """Read a `DeferredProduction` again, under the scope now in force.

        Recording is off: those bytes contributed their entries the first time round, and
        adding them again would renumber the table under the very back-reference being
        resolved. `_naming` is off for the same reason it is off inside any type -- a
        nested argument list must not replace the enclosing entity's `T_` scope.
        """
        # Not memoised inside a pack expansion. `Dp` reads its pattern once per member
        # with `_pack_index` set, and the scope does not change between those readings --
        # so a memo keyed on the scope would hand every member the first one's answer.
        # The bound the memo exists for still holds there: a pack expansion is already
        # linear in the members it has, and the members come out of the input.
        key = None if self._pack_index is not None else (index, self.targs.generation)
        if key is not None:
            found = self._deferred.get(key)
            if found is not None:
                return found
        reader, subs = self.reader, self.subs
        saved_pos, saved_naming, saved_recording = reader.pos, self._naming, subs.recording
        reader.pos = span.start
        self._naming = False
        subs.recording = False
        try:
            result = self.type_()
        finally:
            reader.pos = saved_pos
            self._naming = saved_naming
            subs.recording = saved_recording
        if key is not None:
            self._deferred[key] = result
        return result

    def _type(self):
        reader = self.reader
        builder = self.builder
        subs = self.subs
        char = reader.peek()

        # <builtin-type>: never a substitution candidate (5.1.10). One lookup, and the
        # cursor is stepped rather than asked to re-check a character `peek` just
        # returned -- this production reads every type in every name.
        builtin = BUILTIN_TYPES.get(char)
        if builtin is not None:
            reader.pos += 1
            return builder.builtin(builtin)

        # A third of every type read is a nested name, and one in six is a
        # substitution: the chain below is ordered by how often each arm is taken
        # over the Itanium symbols of a stock Ubuntu 24.04, because a `<type>` that
        # had to fall through fifteen character comparisons to reach the arm that
        # takes a third of them paid for all fifteen.
        if char in _CLASS_ENUM_START:
            return subs.remember(self.class_enum_type(), "type")

        if char == "S":
            if reader.ahead(1) == "t":
                # `St <unqualified-name>` is an <unscoped-name>, not a bare
                # abbreviation: the name that follows belongs to it.
                return subs.remember(self.class_enum_type(), "type")
            component = self.substitution()
            named = self._module_of(component)
            if named is not None:
                # `S1_ 1A` is `A@FOO.BAR`: the entry is the module, and the name that
                # follows is the type. The pair is a <type> and so a candidate of its
                # own -- the module entry alone is not one a later `S<n>_` can mean.
                component = subs.remember(self.unqualified_name(module=named), "type")
            if reader.peek() == "I":
                arguments = self.template_arguments()
                return subs.remember(builder.template(component, arguments, not self._trailing_empty_pack), "type")
            return component

        if char == "R":
            reader.pos += 1
            return subs.remember(builder.reference(self.type_()), "type")
        if char == "P":
            reader.pos += 1
            inner = self.type_()
            if id(inner) in self._objc_id_ids:
                # `objc_object` conforming to a protocol, pointed to, is `id<A>` -- the
                # pointer is part of what `id` means, so it is not written again.
                return subs.remember(inner, "type")
            return subs.remember(builder.pointer(inner), "type")
        if char in QUALIFIER_LETTERS:
            if self._at_function_type():
                # 5.1.5.3: <function-type> ::= [<CV-qualifiers>] [<exception-spec>] [Dx]
                # F [Y] <bare-function-type> [<ref-qualifier>] E. The qualifiers are part
                # of *this* production, so `KFbvE` is the single component
                # `bool () const` -- not a `bool ()` that a qualifier is then applied to.
                # Recording both would enter one component too many and shift every
                # later back-reference.
                return self.function_type_production()
            qualifiers = self.cv_qualifiers()
            inner = self.type_()
            return subs.remember(builder.qualify(inner, qualifiers), "type")

        if char == "T":
            following = reader.ahead(1)
            if following in _ELABORATED_KEYWORDS and reader.ahead(2) not in _INDEX_START:
                # <class-enum-type> ::= Ts <name> | Tu <name> | Te <name>
                #
                # A dependent type the writer had to spell out: `struct T::c`. `Ts` is told
                # from the `Ts <index> _` pack marker by what follows -- an index is digits
                # or `_`, and a name is neither.
                keyword = _ELABORATED_KEYWORDS[following]
                reader.pos += 2
                return subs.remember(builder.raw(f"{keyword} {builder.spell(self.class_enum_type())}"), "type")

            component, reference = self.template_param_binding()
            recorded = reference if reference is not None else component
            if reader.peek() == "I" and self._try_template_args:
                # <template-template-param> <template-args>. The parameter is recorded in
                # its own right *and* the application is, so this contributes two entries
                # even though the parameter resolves to something already in the table.
                # A <template-param> is a distinct grammar component from the entity it
                # names, and 5.1.10 makes each component a candidate.
                #
                # Settled against the manglers rather than a demangler. For
                # `template<template<class, int> class H, class T> H<T,3> f(H<T,3>)`,
                # g++ 13.3 and clang++ 18.1.3 both emit `...T_IT0_Li3EES5_` -- the
                # parameter is `S5_`, which is only reachable if `T_` took an index of
                # its own. Recording one entry made that name unreadable and shifted
                # every later back-reference in any name that applies a template
                # template parameter.
                subs.remember(recorded, "template-template-param")
                arguments = self.template_arguments()
                return subs.remember(builder.template(component, arguments, not self._trailing_empty_pack), "type")
            # A <template-param> reached through <type> is a <type>, and <type> is a
            # candidate. Confirmed by `_ZSt4sortIPiEvT_S1_`, where `S1_` resolves to
            # `int*` -- the entry the `T_` parameter itself contributed.
            subs.remember(recorded, "type")
            return component

        if char == "O":
            reader.pos += 1
            return subs.remember(builder.rvalue_reference(self.type_()), "type")
        if char == "D":
            extended = self.extended_type()
            if extended is not None:
                return extended

        if char == "F":
            return self.function_type_production()
        if char == "A":
            return subs.remember(self.array_type(), "type")
        if char == "M":
            return subs.remember(self.member_pointer_type(), "type")

        if char == "U":
            # <type> ::= U <source-name> [<template-args>] <type>  -- vendor qualifier
            return subs.remember(self.qualified_type(), "type")

        if char == "u":
            # <builtin-type> ::= u <source-name> [<template-args>]
            #
            # A vendor extended type, and the one builtin that *is* a substitution
            # candidate: 5.1.10 excludes builtins "other than vendor extended types", so
            # `_Z1fu3fooS_` is `f(foo, foo)` with `S_` naming the first one. This is how
            # ARM's SVE types (`__SVInt8_t`), `__bf16`, `__uuidof` and Clang's builtin
            # type transformations (`__add_pointer(int)`) all reach a mangled name.
            reader.pos += 1
            spelled = self.source_name()
            if reader.peek() == "I":
                rendered = ", ".join([builder.spell(argument) for argument in self.template_arguments()])
                # A transformation is spelled as a call, a vendor *type* as a template.
                # Clang writes `__add_pointer(int)` and `__uuidof(T)`, both of which are
                # named with a leading double underscore; anything else keeps `<...>`.
                spelled = f"{spelled}({rendered})" if spelled.startswith("__") else spelled + self._angled(rendered)
            return subs.remember(builder.raw(spelled), "type")

        if char == "C" or char == "G":
            # C99's `_Complex` and `_Imaginary`. Both references qualify the type from
            # the right, which is what makes `PCd` a pointer to a complex double rather
            # than a complex pointer; they differ only in the word, and the style says
            # which. Spelling it `std::complex<double>` named a different type -- a C++
            # class template -- and lost the declarator besides.
            reader.pos += 1
            qualifier = _COMPLEX_WORDS[self.options.gnu_complex_spelling][char]
            return subs.remember(builder.qualify(self.type_(), (qualifier,)), "type")

        raise ParseError(self._mangled, reader.pos, f"unknown type code {char!r}")

    def extended_type(self):
        """The `D`-introduced types: extended builtins, decltype, packs, vectors."""
        reader = self.reader
        builder = self.builder
        pair = reader.peek2()

        if pair in EXTENDED_BUILTIN_TYPES:
            reader.pos += 2
            if pair in ("DB", "DU"):
                # _BitInt(N): DB <number> _ | DB <expression> _
                #
                # Recorded as a substitution candidate, unlike every other builtin.
                # 5.1.10 excludes "<builtin-type> other than vendor extended types", and
                # a `_BitInt` carries a width -- so there is something to refer back to,
                # and the reference does refer back: `_Z6myfuncRDB8_S0_` is
                # `myfunc(_BitInt(8)&, _BitInt(8)&)`, which needs `_BitInt(8)` to be
                # entry zero. Without it every later back-reference in such a name was
                # off by one, and the name was refused rather than mis-spelled -- which
                # is the one mercy in it.
                width = reader.digits() if reader.peek() in DIGITS else self.expression_text()
                reader.expect("_")
                return self.subs.remember(builder.raw(f"{EXTENDED_BUILTIN_TYPES[pair]}({width})"), "type")
            if pair == "Dn" and self.options.gnu_nullptr_spelling:
                return builder.builtin("decltype(nullptr)")
            return builder.builtin(EXTENDED_BUILTIN_TYPES[pair])

        if pair == "DF":
            # Three productions share the `DF` prefix and are told apart by what
            # terminates them: `DF <n> _` is `_FloatN`, `DF <n> x` is `_FloatNx` -- the
            # `x` *is* the terminator, there is no `_` after it -- and `DF16b` alone is
            # `std::bfloat16_t`. Reading `x` as an optional flag before a required `_`
            # refused every `_FloatNx` in the shipped libstdc++.
            reader.pos += 2
            width = reader.digits()
            if reader.eat("b"):
                if width != "16":
                    raise ParseError(self._mangled, reader.pos, "bfloat is only 16 bits wide")
                return builder.builtin("std::bfloat16_t")
            if reader.eat("x"):
                return builder.builtin(f"_Float{width}x")
            reader.expect("_")
            return builder.builtin(f"_Float{width}")

        if pair in ("Dt", "DT"):
            return self.subs.remember(self.decltype_(), "type")

        if pair == "Dp":
            reader.pos += 2
            start = reader.pos
            mark = self.subs.mark()
            outer_empty = self._saw_empty_pack
            outer_pack = self._saw_pack
            outer_arity = self._pack_arity
            self._saw_empty_pack = False
            self._saw_pack = False
            self._pack_arity = None
            try:
                inner = self.type_()
                over_empty = self._saw_empty_pack
                over_pack = self._saw_pack
                arity = self._pack_arity
            finally:
                self._saw_empty_pack = outer_empty
                self._saw_pack = outer_pack or self._saw_pack
                self._pack_arity = outer_arity
            if over_empty:
                # The pattern ranges over a pack with no members, so it expands to no
                # types at all -- not to one type with an empty argument list. Both
                # references drop the argument entirely; keeping it puts a spurious
                # `std::decay<>::type...` in every `std::async` in libstdc++.
                #
                # It is still a <type> and still enters the substitution table: what is
                # empty is what it expands to, not the production.
                return self.subs.remember(builder.parameter_pack([]), "type")
            if over_pack and arity and len(builder.members(inner) or ()) != arity:
                # The pattern is more than a declarator round the parameter -- a
                # template applied to it, say -- so distributing the members through it
                # is not something the builders can do to a finished handle. It is read
                # again, once per member: `Dp unary<T_>` over `{int, float}` is
                # `unary<int>, unary<float>`, not the single `unary<int, float>`.
                return self.subs.remember(self._expand_pattern(start, mark, arity), "type")
            if id(inner) in self._pack_ids or over_pack or self._scope_has_pack:
                # The expansion is a <type> in its own right and is recorded as one,
                # separately from the type it expands: `Dp R T1_` contributes both the
                # `R T1_` entry and the expansion's. Both reference demanglers do this,
                # and a name referring past them comes out short otherwise.
                #
                # `inner` is a pack, and every declarator between here and the `T_` has
                # already distributed over its members -- so `Dp O T_` over three
                # arguments arrives as three rvalue references, fully spelled. Expansion
                # is what those members *are*; an ellipsis would be spelling it twice.
                return self.subs.remember(inner, "type")
            # No pack in scope: this is an unexpanded expansion, and the ellipsis is the
            # whole content of it.
            return self.subs.remember(builder.pack(inner), "type")

        if pair in ("Dk", "DK"):
            # <type> ::= Dk <type-constraint>   # `C auto`
            #          | DK <type-constraint>   # `C decltype(auto)`
            placeholder = "auto" if pair == "Dk" else "decltype(auto)"
            reader.pos += 2
            constraint = builder.spell(self.name()[0])
            return self.subs.remember(builder.raw(f"{constraint} {placeholder}"), "type")

        if pair == "Dy":
            # <type> ::= Dy <pack> <index> -- C++26 pack indexing.
            reader.pos += 2
            pattern = self.type_()
            index = self.expression()
            return self.subs.remember(builder.raw(f"({builder.spell(pattern)})[{builder.spell(index)}]"), "type")

        if pair == "Dv":
            return self.subs.remember(self.vector_type(), "type")

        # <function-type> ::= [<CV-qualifiers>] [<exception-spec>] [Dx] F ... E, so an
        # exception specification introduces a function type rather than wrapping one.
        # Spelling it around the result instead loses the declarator: a pointer to a
        # `void () noexcept` would come out `void () noexcept*` rather than
        # `void (*)() noexcept`.
        if pair in ("Do", "DO", "Dw", "Dx"):
            return self.function_type_production()

        if pair in ("DA", "DR") or (pair == "DS" and reader.ahead(2) == "D" and reader.ahead(3) in "AR"):
            return builder.builtin(self.fixed_point_type())

        return None

    def fixed_point_type(self):
        """<builtin-type> ::= [DS] DA <int-code> | [DS] DR <int-code>

        Embedded C's saturating and non-saturating fixed-point types (N1169), added to
        the ABI in 2023. `DS` is the `_Sat` qualifier and comes first.
        """
        reader = self.reader
        saturating = reader.eat("DS")
        kind = reader.take_exactly(2)
        if kind not in ("DA", "DR"):
            reader.fail("expected a fixed-point type")
        code = reader.take()
        integer = _FIXED_POINT_INTEGERS.get(code)
        if integer is None:
            reader.fail(f"unknown fixed-point underlying type {code!r}")
        prefix = "_Sat " if saturating else ""
        return f"{prefix}{integer}{'_Accum' if kind == 'DA' else '_Fract'}"

    def vector_type(self):
        """<type> ::= Dv <number> _ <type> | Dv _ <expression> _ <type>"""
        reader = self.reader
        reader.expect("Dv")
        size = self.expression_text() if reader.eat("_") else reader.digits()
        reader.expect("_")
        inner = self.type_()
        spelled = self.builder.spell(inner)
        if self.options.gnu_vector_spelling:
            return self.builder.raw(f"{spelled} __vector({size})")
        return self.builder.raw(f"{spelled} vector[{size}]")

    def class_enum_type(self):
        """<class-enum-type> ::= <name> | Ts <name> | Tu <name> | Te <name>

        `as_type` matters for a <local-name>: whether a signature follows the entity is
        not decidable from the grammar alone, only from where the name sits.
        `Z <encoding> E <entity>` reached as an <encoding> may be a local *function*, and
        the types after it are its parameters; reached as a <type> it names a local class,
        and what follows belongs to whatever mentioned it.
        """
        name, _, _, _ = self.name(as_type=True)
        return name

    def _at_function_type(self):
        """Whether the cursor is on a <function-type>, cv-qualifiers and all.

        The optional prefixes -- up to three cv-qualifiers, then one exception
        specification, then `Dx` -- belong to the production rather than wrapping it, so
        recognising it means looking past them.
        """
        reader = self.reader
        at = reader.pos
        for letter in ("r", "V", "K"):
            if reader.ahead(at - reader.pos) == letter:
                at += 1
        offset = at - reader.pos
        if reader.ahead2(offset) in ("Do", "DO", "Dw", "Dx"):
            return True
        return reader.ahead(offset) == "F"

    def qualified_type(self):
        """<qualified-type>, as the reference reads it: one production, one candidate.

        A vendor qualifier and the cv-qualifiers underneath it are read together, so
        `U3AS1Ki` enters *one* substitution and not two. That matters for every later
        back-reference in the name: with two, the `S0_` in `_Z1fPU3AS1KiS0_` names
        `int const AS1` where it should name `int const AS1*`, and the second parameter
        loses its pointer.
        """
        reader = self.reader
        builder = self.builder
        if reader.peek() == "U":
            reader.pos += 1
            qualifier = self.source_name()
            if reader.peek() == "I":
                arguments = self.template_arguments()
                qualifier += self._angled(", ".join([builder.spell(argument) for argument in arguments]))
            inner = self.qualified_type()
            if qualifier.startswith(_OBJC_PROTOCOL):
                # `U <n>objcproto<protocol> <type>` is an Objective-C type conforming to
                # a protocol, and the references write it in angle brackets:
                # `NSArray<A>`, not `NSArray objcproto1A`. A qualified `objc_object` is
                # written `id<A>` once a pointer is applied to it -- and only then, so
                # the rewrite is recorded here and done in the `P` branch.
                # The protocol is itself a length-prefixed name inside the qualifier's:
                # `11objcproto1A` carries `1A`, which is `A`.
                protocol = qualifier[len(_OBJC_PROTOCOL) :]
                digits = 0
                while digits < len(protocol) and protocol[digits] in DIGITS:
                    digits += 1
                if digits and int(protocol[:digits]) == len(protocol) - digits:
                    protocol = protocol[digits:]
                spelled = builder.spell(inner)
                if spelled == _OBJC_OBJECT:
                    handle = builder.raw(f"id<{protocol}>")
                    self._objc_ids.append(handle)
                    self._objc_id_ids.add(id(handle))
                else:
                    handle = builder.raw(f"{spelled}<{protocol}>")
                return handle
            return builder.vendor_qualify(inner, qualifier)
        if self._at_function_type():
            # A cv-qualified function type is its own production either way round.
            return self.type_()
        qualifiers = self.cv_qualifiers()
        inner = self.type_()
        return builder.qualify(inner, qualifiers) if qualifiers else inner

    def function_type_production(self):
        """The whole of `[<CV-qualifiers>] [<exception-spec>] [Dx] F ... E`.

        One grammar component, and so one substitution entry, however many of the
        optional prefixes are present. Reading the exception specification as a type in
        its own right and then qualifying the result would enter two, shifting every
        later back-reference in the name.

        The pieces are spelled in the order C++ writes them -- cv-qualifiers, then the
        ref-qualifier, then the exception specification -- which is not the order the
        scheme encodes them in: `M1XKFivOE` is `int (X::*)() const &&`, with the `K` read
        first and printed second.
        """
        reader = self.reader
        builder = self.builder
        qualifiers = self.cv_qualifiers()
        specification = ""
        pair = reader.peek2()
        if pair == "Do":
            reader.pos += 2
            specification = " noexcept"
        elif pair == "DO":
            # DO <expression> E -- a computed noexcept, `noexcept(sizeof(T) < 8)`.
            reader.pos += 2
            condition = self.expression_text()
            reader.expect("E")
            specification = f" noexcept({condition})"
        elif pair == "Dw":
            # Dw <type>* E -- the pre-C++17 dynamic specification, `throw(int, char)`.
            reader.pos += 2
            thrown = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated throw specification")
                spelled = builder.spell(self.type_())
                # An expansion of an empty pack contributes no type, and listing it
                # anyway left `throw(int, )`.
                if spelled:
                    thrown.append(spelled)
            specification = f" throw({', '.join(thrown)})"
        if reader.eat("Dx"):
            specification += " transaction_safe"
        return self.subs.remember(self.function_type(specification, qualifiers), "type")

    def function_type(self, exception_spec="", cv_qualifiers=()):
        """A function type.

        ```
        <function-type> ::= [<CV-qualifiers>] [<exception-spec>] [Dx] F [Y]
                            <bare-function-type> [<ref-qualifier>] E
        ```

        `exception_spec` is already-spelled text from the caller, which read it before
        the `F` because that is where the grammar puts it.
        """
        reader = self.reader
        builder = self.builder
        reader.eat("Dx")  # a transaction-safe function
        reader.expect("F")
        reader.eat("Y")  # extern "C"

        returns = self.type_()
        parameters = []
        suffix = ""
        while not reader.eat("E"):
            if reader.eof:
                raise ParseError(self._mangled, reader.pos, "unterminated function type")
            if reader.peek() == "R" and reader.ahead(1) == "E":
                reader.take()
                suffix = " &"
                continue
            if reader.peek() == "O" and reader.ahead(1) == "E":
                reader.take()
                suffix = " &&"
                continue
            parameter = self.type_()
            if not _drops_out(builder, parameter):
                parameters.append(parameter)

        if parameters and _is_all_void(builder, parameters):
            parameters = []
        written = "".join([f" {qualifier}" for qualifier in cv_qualifiers])
        return builder.function(returns, parameters, written + suffix + exception_spec)

    def array_type(self):
        """An array type.

        ```
        <array-type> ::= A [<number>] _ <type> | A <expression> _ <type>
        ```
        """
        reader = self.reader
        reader.expect("A")
        if reader.peek() == "_":
            dimension = ""
        elif reader.peek() in DIGITS:
            dimension = reader.digits()
        else:
            # `Builder.array` takes a dimension as characters: an array bound is part of
            # the type's spelling, not an operand position a consumer would walk into.
            dimension = self.expression_text()
        reader.expect("_")
        return self.builder.array(self.type_(), dimension)

    def member_pointer_type(self):
        """<pointer-to-member-type> ::= M <class type> <member type>"""
        reader = self.reader
        reader.expect("M")
        owner = self.type_()
        member = self.type_()
        return self.builder.member_pointer(owner, member)

    # -- 5.1.5.10 template arguments -------------------------------------------

    def template_param_decl(self, ellipsis="", params=None):
        """Guarded wrapper: `Tp` and `Tt` both recurse into this production."""
        depth = self._depth = self._depth + 1
        if depth > self._max_depth:
            raise LimitExceeded(self._mangled, "recursion depth", self._max_depth)
        try:
            return self._template_param_decl(ellipsis, params)
        finally:
            self._depth = depth - 1

    def _template_param_decl(self, ellipsis="", params=None):
        """A declared template parameter.

        ```
        <template-param-decl> ::= Ty | Tk <concept-name> | Tn <type>
                                | Tt <template-param-decl>* E | Tp <template-param-decl>
        ```

        Declares a template parameter rather than supplying an argument. Newer than the
        grammar snapshot in docs/specs/, and emitted by Clang for constrained templates
        and for generic lambdas.

        Returns `(binding, declaration)`. In an ordinary argument list both are
        discarded -- the references print nothing there. In a generic lambda's signature
        both are used: the lambda is spelled `'lambda'<typename $T>($T)`, so the
        declaration is printed and `$T` is what `T_` resolves to inside it. The names
        are numbered per kind, first unsuffixed: `$T`, `$T0`, `$T1`.

        `params` is the level these declarations bind into, or None where they bind into
        nothing -- an ordinary argument list, where a declaration is read for the input
        it consumes and nothing refers back to it. A `Tt` always opens a level of its
        own whatever `params` is, because its inner declarations refer to each other.
        """
        reader = self.reader
        pair = reader.peek2()
        reader.pos += 2

        if pair == "Ty":
            binding = self._declare("T", params)
            return binding, f"typename {ellipsis}{binding}"
        if pair == "Tk":
            # A constrained parameter: the concept it must satisfy, then the parameter.
            # The concept's own arguments are constraint operands, and the reference
            # spells a parameter inside one by its own mangled name.
            outer = self._in_constraint
            self._in_constraint = True
            try:
                concept = self.builder.spell(self.name()[0])
            finally:
                self._in_constraint = outer
            binding = self._declare("T", params)
            return binding, f"{concept} {ellipsis}{binding}"
        if pair == "Tn":
            # The name goes where a declarator goes, so a parameter of array-of-pointer
            # type is `$T0 (*$N) [3]` and not `$T0 (*) [3] $N`.
            kind = self.type_()
            binding = self._declare("N", params)
            return binding, self.builder.spell(kind, f"{ellipsis}{binding}")
        if pair == "Tp":
            # A pack. The ellipsis goes immediately before the name, wherever the name
            # ends up: `$T0 (*...$N0) [3]`. It binds into the same level its inner
            # declaration would have.
            return self.template_param_decl("...", params)
        if pair == "Tt":
            # The name is invented *before* the inner list is read, so it belongs to the
            # level outside it -- `template<typename $T0, ...> typename $TT` has `$TT`
            # beside its siblings and `$T0` a level down. Reading the inner list first
            # numbered them the other way round.
            binding = self._declare("TT", params)
            inner = []
            declared = []
            self.targs.push(declared)
            try:
                while not reader.eat("E"):
                    if reader.eof:
                        raise ParseError(self._mangled, reader.pos, "unterminated template parameter list")
                    if reader.eat("Q"):
                        # A constraint on the template template parameter. The reference
                        # keeps it out of the spelling; it is read for the substitution
                        # entries its operands contribute.
                        self.constraint_expression()
                        reader.expect("E")
                        break
                    inner.append(self.template_param_decl(params=declared)[1])
            finally:
                self.targs.pop()
            return binding, f"template<{', '.join(inner)}> typename {ellipsis}{binding}"
        raise ParseError(self._mangled, reader.pos, f"unknown template parameter declaration {pair!r}")

    def _declare(self, kind, params):
        """Invent a name for a declared parameter, and bind it into `params`."""
        binding = self._parameter_name(kind)
        if params is not None:
            params.append(self.builder.raw(binding))
        return binding

    def _parameter_name(self, kind):
        """The synthetic name for a declared parameter.

        llvm-cxxfilt leaves the first unsuffixed -- `$T`, `$T0`, `$T1` -- while GNU
        c++filt numbers from zero throughout: `$T0`, `$T1`.
        """
        index = self._parameter_counts.get(kind, 0)
        self._parameter_counts[kind] = index + 1
        if self.options.gnu_closure_spelling:
            return f"${kind}{index}"
        return f"${kind}" + ("" if index == 0 else str(index - 1))

    def template_arguments(self, install_scope=False):
        """A template argument list.

        ```
        <template-args> ::= I <template-arg>+ E
        ```

        `T_` names a parameter of the innermost enclosing template *declaration*, so
        which argument list is in scope matters and the two callers differ:

        `install_scope=True` -- these arguments belong to the entity being named, so
        they become the `T_` scope, replacing any outer one. In
        `Class<char>::method<char const*>`, `T_` inside the signature is `char const*`,
        not `char`. They are installed one at a time because a later argument may
        reference an earlier one.

        `install_scope=False` -- these arguments belong to a *type* mentioned in
        passing, such as the `std::allocator<char>` inside a parameter list. They are
        not template parameters of anything being declared here, so they leave the
        scope untouched; letting them append would corrupt every later `T_`.
        """
        reader = self.reader
        reader.expect("I")
        install_scope = install_scope and self._naming
        # `_scope_has_pack` describes *this* argument list, so it is saved and restored
        # like `_naming`. Left to leak outward, a pack nested inside an argument --
        # `f<std::tuple<int>>` -- suppresses the ellipsis on the enclosing `Dp`.
        outer_has_pack = self._scope_has_pack
        self._scope_has_pack = False
        if install_scope:
            self.targs.install()
        # Whatever these arguments contain, it is a type mentioned in passing, not the
        # entity being declared -- so a nested argument list inside one of them must not
        # install a scope of its own. Without this, the arguments of the
        # `__normal_iterator<wchar_t*, ...>` passed to a `basic_string` constructor
        # replace the constructor's own, and every `T_` in the signature resolves to
        # `wchar_t*`.
        was_naming = self._naming
        self._naming = False
        arguments = []
        trailing_empty_pack = False
        try:
            while True:
                char = reader.peek()
                if char == "E":
                    reader.take()
                    break
                if not char:
                    raise ParseError(self._mangled, reader.pos, "unterminated template argument list")
                depth = self._depth = self._depth + 1
                if depth > self._max_depth:
                    raise LimitExceeded(self._mangled, "recursion depth", self._max_depth)
                try:
                    argument, is_empty_pack = self.template_arg()
                finally:
                    self._depth = depth - 1
                if argument is None:
                    # A <template-param-decl>: it declares a parameter rather than
                    # supplying an argument, and neither prints nor occupies a slot.
                    continue
                trailing_empty_pack = is_empty_pack
                if not is_empty_pack:
                    arguments.append(argument)
                if install_scope:
                    self.targs.add(argument)
        finally:
            self._naming = was_naming
            if not install_scope:
                self._scope_has_pack = outer_has_pack
        self._trailing_empty_pack = trailing_empty_pack
        return arguments

    def template_arg(self):
        """One template argument.

        ```
        <template-arg> ::= <type> | X <expression> E | <expr-primary> | J <template-arg>* E
        ```

        Returns `(handle, is_empty_pack)`. The handle is None for a
        <template-param-decl>, which declares a parameter rather than supplying one.

        Emptiness is returned rather than recorded on the parser because argument lists
        nest: an empty pack inside `AnalysisManager<Module, JE>` would otherwise still be
        flagged when the enclosing `PassManager<Function, AnalysisManager<...>>` finished
        its own argument, and the enclosing argument would be dropped.
        """
        reader = self.reader
        builder = self.builder

        # One lookahead for all five alternatives. Asking `peek2`, then `peek`, then
        # `eat`, then `peek`, then `eat` is five interpreter frames to reach the common
        # case -- a `<type>` -- and this runs once per template argument in the library.
        ahead = reader.peek2()
        char = ahead[:1]

        if ahead in _PARAMETER_DECLARATIONS:
            self.template_param_decl()
            return None, False

        if char == "Q":
            # A requires-clause closing out an argument list. Parsed for its effect on
            # the substitution table; neither reference prints it.
            reader.take()
            outer_constraint = self._in_constraint
            self._in_constraint = True
            try:
                self.expression()
            finally:
                self._in_constraint = outer_constraint
            return None, False

        if char == "X":
            reader.take()
            # `>` and `>>` are bracketed inside an argument list, or the closing angle
            # bracket of the list cannot be told from the operator; and a comma is
            # bracketed here for the same reason it is in any other comma-separated
            # list. Which of the three this is, is decided by the two characters that
            # open the expression, so no lookahead over the parsed shape is needed.
            angled = reader.peek2() in ("gt", "rs", "cm")
            expression = self.expression()
            reader.expect("E")
            if angled:
                expression = builder.expression("paren", ["(", expression, ")"])
            return expression, False

        if char == "L":
            return builder.raw(self.expr_primary()), False

        if char == "J":
            reader.take()
            members = []
            while True:
                # `peek` answers `""` past the end, so one call decides between the
                # closing `E`, another member, and a truncated name.
                next_char = reader.peek()
                if next_char == "E":
                    reader.take()
                    break
                if not next_char:
                    raise ParseError(self._mangled, reader.pos, "unterminated argument pack")
                member, _ = self.template_arg()
                if member is not None:
                    members.append(member)
            self._scope_has_pack = True
            handle = builder.parameter_pack(members)
            self._packs.append(handle)
            self._pack_ids.add(id(handle))
            # An empty pack still occupies an argument position for `T_` numbering, but
            # contributes nothing to spell: `AnalysisManager<Module, JE>` is
            # `AnalysisManager<llvm::Module>`, not `AnalysisManager<llvm::Module, >`.
            # Asked of the builder rather than counted here, because a pack whose only
            # member expands another, empty pack is itself empty and only the builder
            # knows that -- it is what flattened them.
            return handle, not builder.spell(handle)

        argument = self.type_()
        # An expansion over an empty pack spells nothing and occupies no argument slot,
        # the same as an empty `J E` pack does.
        return argument, id(argument) in self._pack_ids and not builder.spell(argument)

    # -- 5.1.6.1 literals ------------------------------------------------------

    def expr_primary(self):
        """A literal or a reference to a declared entity.

        ```
        <expr-primary> ::= L <type> <value number> E | L <mangled-name> E | L _Z <encoding> E
        ```
        """
        reader = self.reader
        builder = self.builder
        reader.expect("L")

        if reader.startswith("_Z") or reader.startswith("Z"):
            # A reference to a declared entity rather than a value (5.1.6.2): a complete
            # mangled name embedded in this one. It is parsed with *this* parser's state
            # rather than a fresh one, because the compiler writes substitutions inside
            # it that index the enclosing name's table -- Clang emits exactly that for
            # the address of a function template passed as a non-type argument, and a
            # fresh table makes every one of those unresolvable.
            reader.eat("_")
            reader.expect("Z")
            # `Z <encoding> E <entity>` -- a local name. Read here rather than off the
            # parsed shape because an encoding nested inside this one gets to `name()`
            # first, and this is the only point where the question is about *this* one.
            local = reader.peek() == "Z"
            was_naming = self._naming
            outer_scope = self.targs.snapshot()
            self._naming = True
            try:
                handle = self.encoding()
            finally:
                # The substitution table is shared deliberately, but the `T_` scope is
                # not: the embedded entity has template parameters of its own, and
                # letting them stand would leave every later `T_` in the enclosing name
                # resolving against the wrong argument list.
                self._naming = was_naming
                self.targs.restore(outer_scope)
            reader.expect("E")
            self._entity_local = local
            return builder.spell(handle)

        if reader.peek2() == "Ul":
            # `L <closure-type-name> E` is a *lambda*, written where a value was
            # expected: a closure object as a template argument. The reference spells
            # the expression that made it rather than the type it has, and accepts only
            # `Ul` here -- an unnamed type that is not a closure is not a value.
            handle = self.unnamed_type_name(lambda_expression=True)
            reader.expect("E")
            return builder.spell(handle)

        # `L <array-type> E` is a string literal. The contents are not mangled at all,
        # so there is nothing to print but the type, and the reference prints it in
        # angle brackets inside the quotes to say as much.
        was_array = reader.peek() == "A"
        kind = self.type_()
        spelling = builder.spell(kind)
        if was_array and reader.eat("E"):
            return f'"<{spelling}>"'

        if reader.eat("E"):
            # A literal with no value: how the scheme writes `nullptr`.
            return "nullptr" if spelling == "std::nullptr_t" else f"({spelling})0"

        start = reader.pos
        while not reader.eat("E"):
            if reader.eof:
                raise ParseError(self._mangled, reader.pos, "unterminated literal")
            reader.take()
        value = reader.text[start : reader.pos - 1]
        return self.spell_literal(spelling, value)

    def _entity_operand(self, operator):
        """GNU's spelling of an embedded `<mangled-name>` under a unary operator.

        See `gnu_entity_operand_spelling` for the rule and the vectors it was read off.
        Two things decide it, and both come back from `expr_primary`: what kind of
        entity the encoding named, and whether it was a local name.

        A qualified data name is the only operand c++filt prints bare -- it is a name,
        and a name needs no brackets to be one operand. `&` of a plain qualified
        function is the case this exists for: the name alone, with the parameter list
        the mangling carries dropped, because `&A::f` is what the source wrote.
        """
        self._entity_shape = None
        self._entity_local = False
        text = self.expr_primary()
        kind, name = self._entity_shape or ("literal", None)
        local = self._entity_local

        if kind == "literal" or (kind == "data" and not local):
            return text
        if operator == "&" and kind == "function" and name is not None and not local:
            spelled = self.builder.spell(name)
            # Qualified, which is what tells a member or namespace-scope function from
            # `&(f())`: c++filt prints the name only when it has a scope to print.
            if "::" in spelled:
                return spelled
        return "(" + text + ")"

    @staticmethod
    def spell_literal(kind, value):
        """Render a literal the way the reference demanglers do.

        The scheme encodes the value as digits with a leading `n` for negative, and
        leaves the suffix implied by the type. Reproducing the suffix matters: `1` and
        `1u` are different template arguments and must not print alike.
        """
        if value.startswith("n"):
            value = "-" + value[1:]

        if kind == "std::nullptr_t":
            # `LDn0E`. The value is written and means nothing; there is one of these.
            return "nullptr"
        if kind == "bool":
            return {"0": "false", "1": "true"}.get(value, f"(bool){value}")
        if kind == "int":
            return value
        suffixes = {
            "unsigned int": "u",
            "long": "l",
            "unsigned long": "ul",
            "long long": "ll",
            "unsigned long long": "ull",
        }
        if kind in suffixes:
            return value + suffixes[kind]
        if kind == "char":
            return f"(char){value}"
        return f"({kind}){value}"

    # -- 5.1.6 expressions -----------------------------------------------------

    # -- 5.1.6 unresolved names ------------------------------------------------

    def unresolved_name(self):
        """A name the compiler could not resolve.

        ```
        <unresolved-name> ::= [gs] <base-unresolved-name>
                            | sr <unresolved-type> [<template-args>] <base-unresolved-name>
                            | srN <unresolved-type> [<template-args>]
                                  <unresolved-qualifier-level>* E <base-unresolved-name>
                            | [gs] sr <unresolved-qualifier-level>+ E <base-unresolved-name>
        ```

        A name written in a template that the compiler could not resolve, because it
        depends on a parameter: `std::is_signed_v<T>` inside an `enable_if`. These reach
        a mangled name through SFINAE return types, which is why they are everywhere in
        heavily templated C++ and absent from simple test cases.

        The two `sr` forms without `N` are told apart by what follows: an
        <unresolved-qualifier-level> is a <simple-id> and so begins with a digit, while
        an <unresolved-type> begins with `T`, `D` or `S`.
        """
        reader = self.reader
        prefix = "::" if reader.eat("gs") else ""

        if not reader.eat("sr"):
            text = prefix + self.base_unresolved_name()
            if prefix:
                self._simple_name = False
            return text

        levels = []
        if reader.eat("N"):
            levels.append(self._unresolved_head())
            # `*` and not `+`: the ABI writes one or more levels here, but Clang emits
            # `srN <type> <template-args> E` with none of them -- the arguments are the
            # whole qualification -- and the reference accepts it.
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated qualifier levels")
                levels.append(self.simple_id())
        elif reader.peek() in DIGITS:
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated qualifier levels")
                levels.append(self.simple_id())
        else:
            levels.append(self._unresolved_head())

        levels.append(self.base_unresolved_name())
        if prefix:
            # `::x` is rooted at global scope, which c++filt brackets as an operand.
            self._simple_name = False
        return prefix + "::".join(levels)

    def _unresolved_head(self):
        """`<unresolved-type> [<template-args>]`, which both `sr` forms open with.

        The arguments sit *outside* the production and outside the substitution entry:
        the reference records the bare parameter or decltype and then wraps the
        arguments round it, so an `S_` written after one names the head alone. Reading
        them inside `unresolved_type` recorded the templated form instead, and left the
        `srN` form unable to read them at all -- `srN S6_ I S3_ E E 5value E` was
        refused for wanting a qualifier level where the arguments were.
        """
        text = self.unresolved_type()
        if self.reader.peek() == "I":
            text += self.spelled_template_arguments()
        return text

    def unresolved_type(self):
        """A dependent type at the head of an unresolved name.

        ```
        <unresolved-type> ::= <template-param> | <decltype> | <substitution>
        ```
        """
        reader = self.reader
        builder = self.builder
        if reader.peek() == "T":
            component, reference = self.template_param_binding()
            self.subs.remember(reference if reference is not None else component, "unresolved-type")
            return builder.spell(component)
        if reader.peek() == "D":
            return builder.spell(self.subs.remember(self.decltype_(), "unresolved-type"))
        return builder.spell(self.substitution())

    def simple_id(self):
        """```
        <simple-id> ::= <source-name> [<template-args>]
        ```
        """
        text = self.source_name()
        if self.reader.peek() == "I":
            text += self.spelled_template_arguments()
            # Set after the arguments are read, not before: they are expressions and may
            # contain names of their own, whose reading would otherwise stand.
            self._simple_name = False
            return text
        self._simple_name = True
        return text

    def spelled_template_arguments(self):
        """A template argument list rendered as text, for use inside a name."""
        arguments = self.template_arguments()
        return self._angled(", ".join([self.builder.spell(argument) for argument in arguments]))

    def _angled(self, rendered):
        """Close a hand-built argument list the way the style closes one.

        The builder does this for a list attached to a name; a list spelled into text --
        inside an unresolved name, or a vendor qualifier -- has to ask for the same rule
        rather than write the brackets itself, or `A<B<int> >` comes out `A<B<int>>` in
        a style that separates them.
        """
        if self.options.gnu_angle_spacing and not self._trailing_empty_pack and rendered.endswith(">"):
            rendered += " "
        return f"<{rendered}>"

    def base_unresolved_name(self):
        """<base-unresolved-name> ::= <simple-id> | on <operator-name> [<template-args>]
        | dn <destructor-name>
        """
        reader = self.reader
        if reader.peek() in DIGITS:
            return self.simple_id()
        if reader.eat("dn"):
            # `_simple_name` says whether the *whole* name prints as a plain identifier
            # path, which is the one thing GNU c++filt spells without brackets when it is
            # an operand. A destructor and an operator name are not that, and neither is
            # a name carrying template arguments -- but arguments on a *qualifier* do not
            # count, which is why this is decided by the last component and not by the
            # spelling of the whole.
            text = "~" + self.destructor_name()
            self._simple_name = False
            return text
        # The `on` marker is optional: `srT_pl` names `T::operator+` with nothing to say
        # so, and the reference reads the operator code either way.
        reader.eat("on")
        text = self.operator_name()
        if reader.peek() == "I":
            text += self.spelled_template_arguments()
        self._simple_name = False
        return text

    def destructor_name(self):
        """<destructor-name> ::= <unresolved-type> | <simple-id>"""
        if self.reader.peek() in DIGITS:
            return self.simple_id()
        return self.unresolved_type()

    def expression_name(self):
        """A name appearing in an expression -- the callee of a call, most often.

        Records nothing for the name itself. Section 5.1.10 excludes "function and
        operator names other than extern \"C\" functions" from the candidate set, and
        adds that "we do not substitute for expressions, though names appearing in them
        might be substituted": a name in an expression may *refer* to an existing entry,
        but it does not create one.

        Template arguments applied to it still record their own types, which is how
        `_ZSt12construct_atIcJRKcEE...cl7declvalIT0_EE...` reaches `S4_` -- that entry is
        the `T0_` inside `declval`'s argument list, not `declval` itself.
        """
        reader = self.reader
        builder = self.builder
        if reader.peek() in DIGITS:
            text = self.source_name()
            if reader.peek() == "I":
                arguments = self.template_arguments()
                rendered = ", ".join([builder.spell(argument) for argument in arguments])
                return text + self._angled(rendered)
            return text
        # `on <operator-name>` and `dn <destructor-name>`: a callee named by the operator
        # it *is*, which is how an unresolved `a + b` inside a `decltype` is written.
        #
        # Without this the two letters fell through to `type_()`, which read them as the
        # builtin codes they happen also to be -- `o` is `unsigned __int128` and `n` is
        # `__int128` -- so `_Z1fI1AEDTclonplfp_fp_EET_` came back as
        # `decltype(unsigned __int128(__int128, fp + fp)) f<A>(A)`: a signature naming
        # two types that appear nowhere in the symbol, and no error to say so. Both
        # references read it as `operator+(fp, fp)`.
        if reader.peek2() in ("on", "dn"):
            return self.base_unresolved_name()
        return builder.spell(self.type_())

    def initialiser(self):
        """A parenthesised initialiser list.

        ```
        <initializer> ::= pi <expression>* E
        ```
        """
        reader = self.reader
        if not reader.eat("pi"):
            expression = self.expression()
            reader.eat("E")
            return self.builder.expression("initialiser", ["(", expression, ")"])
        arguments = []
        while not reader.eat("E"):
            if reader.eof:
                raise ParseError(self._mangled, reader.pos, "unterminated initialiser")
            arguments.append(self._element())
        return self.builder.expression("initialiser", ["(", *self._commas(arguments), ")"])

    def expression(self):
        """A constant expression appearing in a type or template argument.

        Returns a builder handle, so an expression is structure to a builder that wants
        structure and text to one that wants text. Only the shapes a compiler actually
        emits into a name are modelled: the full expression grammar is enormous and most
        of it cannot reach a mangled name, because it is not part of any signature.

        Sets `_precedence`, which a containing operator reads immediately afterwards to
        decide whether this operand needs brackets.
        """
        depth = self._depth = self._depth + 1
        if depth > self._max_depth:
            raise LimitExceeded(self._mangled, "recursion depth", self._max_depth)
        try:
            self._precedence = PRIMARY_PRECEDENCE
            return self._expression()
        finally:
            self._depth = depth - 1

    def constraint_expression(self):
        """`Q <expression>`, the C++20 requires-clause.

        Every enclosing template's parameters are in scope inside it, and this parser
        does not track them all -- so a `<template-param>` inside a clause is spelled by
        its own mangled name rather than by whatever it is bound to, which is what the
        reference does for the same reason.
        """
        outer = self._in_constraint
        self._in_constraint = True
        try:
            return self.expression()
        finally:
            self._in_constraint = outer

    def expression_text(self):
        """An expression where the grammar around it needs characters, not a shape.

        An array bound and a `throw(...)` specification are spelled *into* a type by
        productions that take text, so those ask for text. Everything reachable from
        `_expression` keeps the handle.
        """
        return self.builder.spell(self.expression())

    def _spell_parameter(self, index):
        """A reference to a function parameter, `fp_` / `fp0_` / `fpT_`."""
        if self.options.gnu_expression_spelling:
            return f"{{parm#{int(index) + 2 if index else 1}}}"
        return f"fp{index}"

    def _string_members(self):
        """The spelled string for a run of `Lc<value>E` members, or None if it is not one.

        Reads to the closing `E`. Returns None -- having consumed whatever it read, which
        the caller undoes -- as soon as a member is anything but a character literal.
        """
        reader = self.reader
        values = []
        while not reader.eat("E"):
            if reader.eof or not reader.startswith("Lc"):
                return None
            reader.pos += 2
            negative = reader.eat("n")
            if reader.peek() not in DIGITS:
                return None
            value = int(reader.digits())
            if not reader.eat("E"):
                return None
            values.append(-value if negative else value)
        return _string_literal(values)

    def requires_expression(self):
        """A C++20 requires-expression.

        ```
        <expression>  ::= rq <requirement>+ E
                        | rQ <bare-function-type> _ <requirement>+ E
        <requirement> ::= X <expression> [N] [R <type-constraint>]
                        | T <type>
                        | Q <constraint-expression>
        ```

        `rQ` is the form with a parameter list, `requires (T) { ... }`. Each requirement
        is written with a leading space and a closing semicolon, which is how the
        reference lays the braces out.
        """
        reader = self.reader
        builder = self.builder
        parts = ["requires"]
        if reader.startswith("rQ"):
            reader.pos += 2
            parameters = []
            while not reader.eat("_"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated requires parameters")
                parameters.append(self.type_())
            parts += [" (", *self._commas(parameters), ")"]
        else:
            reader.pos += 2
        parts.append(" {")
        while not reader.eat("E"):
            if reader.eof:
                raise ParseError(self._mangled, reader.pos, "unterminated requires expression")
            marker = reader.take()
            if marker == "X":
                # A compound requirement is braced when it says anything more than that
                # the expression is valid.
                expression = self.expression()
                no_throw = reader.eat("N")
                constraint = self.name()[0] if reader.eat("R") else None
                braced = no_throw or constraint is not None
                parts += [" {", expression, "}"] if braced else [" ", expression]
                if no_throw:
                    parts.append(" noexcept")
                if constraint is not None:
                    parts += [" -> ", constraint]
            elif marker == "T":
                parts += [" typename ", self.type_()]
            elif marker == "Q":
                parts += [" requires ", self.constraint_expression()]
            else:
                raise ParseError(self._mangled, reader.pos, f"unknown requirement {marker!r}")
            parts.append(";")
        parts.append(" }")
        self._precedence = PRIMARY_PRECEDENCE
        return builder.expression("requires", parts)

    def _fold_pack(self):
        """The pack half of a fold expression, bracketed and expanded.

        A pack that is bound prints as its members -- `(1, 2, 3)` -- and one that is not
        prints with the ellipsis that says it is still a pack: `(y...)`.
        """
        builder = self.builder
        expanded = self.expression()
        if builder.members(expanded) is not None:
            return builder.expression("paren", ["(", expanded, ")"])
        return builder.expression("paren", ["(", expanded, "...)"])

    def _commas(self, items, separator=", "):
        """`items` interleaved with `separator`, dropping any that spell nothing.

        An expansion over a pack with no members spells nothing, and the comma that
        would have preceded it must not print either: `f(a, xs...)` over an empty `xs`
        is `f(a)`, not `f(a, )`. The reference applies that to every comma-separated
        list as it prints, rather than at each production that builds one, and the
        productions here are the same set -- calls, braced lists, placement arguments,
        a `requires` parameter list.
        """
        builder = self.builder
        parts = []
        for item in items:
            # `size` is O(1) and `spell` is not, and this runs over every argument list
            # in every expression: rendering each one to ask whether it is empty made
            # the structured benchmark 1.4x slower on its own. Size zero settles it for
            # a pack with no members, which is what an expansion over an empty pack
            # gives; only a *pack* can be non-zero in size and still spell nothing --
            # one holding another empty pack -- and asking whether a handle is a pack is
            # itself O(1).
            if builder.size(item) == 0:
                continue
            if builder.members(item) is not None and not builder.spell(item):
                continue
            if parts:
                parts.append(separator)
            parts.append(item)
        return parts

    def _element(self):
        """One member of a comma-separated list, bracketed if it is a comma expression.

        `f((a, b))` passes one argument and `f(a, b)` passes two; without the brackets
        the two spell alike.
        """
        return self._operand(_COMMA_BINDING + 1)

    def _operand(self, binding, subexpression=False):
        """One operand, bracketed only when it binds more loosely than its operator.

        The brackets are reported as parts of a `paren` expression rather than glued on,
        so a consumer reading the tree sees the operand it wrapped instead of having to
        strip punctuation back off a string.

        `subexpression` marks the positions GNU c++filt runs through `d_print_subexpr`
        -- the operands of a unary, binary or ternary operator, and a call's callee --
        where it brackets by *kind* rather than by precedence: everything but a name, a
        braced initialiser list and a function parameter. See `SIMPLE_PRECEDENCE`. The
        other callers of this are list elements and the object of a member access, which
        it prints without asking.
        """
        operand = self.expression()
        if subexpression and self.options.gnu_expression_spelling:
            needed = self._precedence < SIMPLE_PRECEDENCE
        else:
            needed = self._precedence < binding
        if needed:
            return self.builder.expression("paren", ["(", operand, ")"])
        return operand

    def _callee(self):
        """The thing being called, bracketed on the same rule as any other operand.

        `(std::declval<int>)()` and `(::foo)()` and `(operator+)(...)`, but `foo(int)`,
        `std::foo(int)`, `{parm#1}(int)` and `{1}(2)` -- all four confirmed against
        c++filt. Bracketing every callee that was not a plain identifier path was eight
        of the differences from it over libLLVM.
        """
        target = self.expression()
        if self.options.gnu_expression_spelling and self._precedence < SIMPLE_PRECEDENCE:
            return self.builder.expression("paren", ["(", target, ")"])
        return target

    def _named_operand(self, text, simple):
        """A leaf whose spelling is a name, marked as one if GNU would agree it is.

        A qualified path of identifiers and nothing else -- not a template-id, not an
        operator name, not a destructor, not a name rooted at global scope. c++filt
        brackets all of those as operands and prints `std::x+(2)` for this. Arguments on
        an inner qualifier are not template arguments *on the name*: it reads
        `!is_array<T>::value` without brackets, and the last component is what decides.
        """
        self._precedence = SIMPLE_PRECEDENCE if simple else PRIMARY_PRECEDENCE
        return self.builder.raw(text)

    def _expression(self):
        reader = self.reader
        builder = self.builder

        # These are leaves, and a leaf is already a node that says what it is: a literal,
        # a template parameter, a name, a type. Wrapping each in an `Expression` would add
        # a layer carrying nothing its child does not, on the most common productions in
        # the grammar -- and measurably so, since a template parameter appears in nearly
        # every generic name.
        if reader.peek() == "L":
            # A literal is not a name and GNU brackets it as an operand; a `L _Z... E`
            # naming a data symbol is one, and it does not. A local or special name is
            # spelled with the entity it belongs to and is not a name either.
            self._entity_shape = None
            self._entity_local = False
            text = self.expr_primary()
            kind = (self._entity_shape or ("literal", None))[0]
            return self._named_operand(text, kind == "data" and not self._entity_local)
        if reader.peek() == "T":
            # Whatever the parameter is bound to, GNU brackets it as an operand: its
            # `d_print_subexpr` treats a template parameter as a kind of its own.
            self._precedence = PRIMARY_PRECEDENCE
            return self.template_param()

        pair = reader.peek2()

        if pair == "fp":
            # A function parameter reference (5.1.5.9).
            if reader.startswith("fpT"):
                # The implicit object parameter, and the one form with no index: the
                # reference spells it `this`. Read as `fp` with a `T` to skip, the `1`
                # of a following `1b` became its index and the `b` became `bool`.
                reader.pos += 3
                self._precedence = SIMPLE_PRECEDENCE
                return builder.raw("this")
            reader.pos += 2
            index = reader.digits() if reader.peek() in DIGITS else ""
            reader.eat("_")
            self._precedence = SIMPLE_PRECEDENCE
            return builder.raw(self._spell_parameter(index))
        if pair == "fL" and reader.ahead(2) in DIGITS:
            # `fL <number> p ...` is a parameter of an enclosing function; `fL` followed
            # by an operator code is a left fold with an initialiser, read below.
            reader.pos += 2
            reader.digits()
            reader.eat("p")
            index = reader.digits() if reader.peek() in DIGITS else ""
            reader.eat("_")
            self._precedence = SIMPLE_PRECEDENCE
            return builder.raw(self._spell_parameter(index))

        if pair == "sr":
            text = self.unresolved_name()
            return self._named_operand(text, self._simple_name)
        if pair in ("on", "dn"):
            # `<expression> ::= <unresolved-name>`, and an `<unresolved-name>` may be
            # `on <operator-name>` -- a callee named by the operator it is, which is how
            # an unresolved `a + b` inside a `decltype` is written.
            #
            # Without this the two letters fell through to the type productions below,
            # which read them as the builtin codes they happen also to be: `o` is
            # `unsigned __int128` and `n` is `__int128`. `_Z1fI1AEDTclonplfp_fp_EET_`
            # came back as `decltype(unsigned __int128(__int128, fp + fp)) f<A>(A)` -- a
            # signature naming two types that appear nowhere in the symbol, with nothing
            # to say it had gone wrong. Both references read it as `operator+(fp, fp)`.
            text = self.unresolved_name()
            return self._named_operand(text, self._simple_name)
        if pair == "sZ":
            reader.pos += 2
            outer_index = self._pack_index
            self._pack_index = None
            try:
                inner = self.template_param()
            finally:
                self._pack_index = outer_index
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("sizeof_pack", ["sizeof...(", inner, ")"])
        if pair == "sP":
            reader.pos += 2
            members = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated sizeof... pack")
                argument, _ = self.template_arg()
                if argument is not None:
                    members.append(argument)
            # `sZ` writes `sizeof...(`; `sP`, over a captured pack, writes it with a
            # space. Both references agree, and it is the only thing separating them.
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("sizeof_pack", ["sizeof... (", *self._commas(members), ")"])
        if pair in ("st", "sz", "at", "az", "ti", "te", "nx"):
            # `sizeof (int)`, `alignof (x)`, `typeid (T)`, `noexcept (x)`. Each closes
            # with a bracket, so neither reference ever brackets one again -- but GNU
            # does when it is an operand, because none of them is a name.
            reader.pos += 2
            form, keyword = _MEASURING_OPERATORS[pair]
            gnu = self.options.gnu_expression_spelling
            if pair in _MEASURING_A_TYPE:
                parts = [keyword + " (", self.type_(), ")"]
            elif gnu and pair == "nx":
                # The one c++filt writes with no space and always with brackets:
                # `noexcept({parm#1})`, where it writes `sizeof {parm#1}`.
                parts = [keyword + "(", self.expression(), ")"]
            elif gnu and pair in ("sz", "az"):
                # A keyword and then an operand like any other, so the brackets are the
                # operand's: `sizeof (1)` and `sizeof ({parm#1}())`, but `sizeof
                # {parm#1}` and `sizeof std::x`.
                parts = [keyword + " ", self._operand(PRIMARY_PRECEDENCE, subexpression=True)]
            else:
                parts = [keyword + " (", self.expression(), ")"]
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression(form, parts)

        if pair == "tr":
            # `tr` is a rethrow -- `throw;` with no operand. A leaf, so it needs no
            # brackets anywhere.
            reader.pos += 2
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("throw", ["throw"])
        if pair == "tw":
            # `tw <expression>`, a throw with an operand. GNU brackets the operand and
            # LLVM writes it after a space; both spell the keyword the same way.
            reader.pos += 2
            operand = self.expression()
            self._precedence = LOOSEST_PRECEDENCE
            if self.options.gnu_expression_spelling:
                return builder.expression("throw", ["throw (", operand, ")"])
            return builder.expression("throw", ["throw ", operand])

        if pair == "cl":
            reader.pos += 2
            target = self._callee()
            arguments = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated call expression")
                arguments.append(self._element())
            # A call closes with a bracket, so llvm-cxxfilt never brackets one again --
            # `*std::begin(x)`, not `*(std::begin(x))`. GNU does when it is an operand,
            # because a call is not a name.
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("call", [target, "(", *self._commas(arguments), ")"])

        if pair == "cv":
            reader.pos += 2
            kind = self.type_()
            if reader.eat("_"):
                arguments = []
                while not reader.eat("E"):
                    if reader.eof:
                        raise ParseError(self._mangled, reader.pos, "unterminated conversion")
                    arguments.append(self._element())
                cast = builder.expression("cast", ["(", kind, ")(", *self._commas(arguments), ")"])
            else:
                cast = builder.expression("cast", ["(", kind, ")(", self._element(), ")"])
            # A cast binds *looser* than a postfix operator, so it needs brackets when it
            # is the object of one: `((A*)(0))->member`, not `(A*)(0)->member`, which
            # reads as a cast of `0->member`. It looks parenthesised already -- the
            # spelling puts brackets round the type and round the operand -- and leaving
            # the precedence at primary on that account produced the second form, which
            # is a different expression. The reference brackets it, and this is thirteen
            # of the disagreements with libcxxabi's corpus.
            self._precedence = UNARY_PRECEDENCE
            return cast

        if pair == "tl":
            reader.pos += 2
            kind = self.type_()
            spelled = builder.spell(kind)
            if spelled.startswith("char [") or spelled == "char []":
                # A `char` array in a braced initialiser is a string. Tried first and
                # backed out of, because whether it *is* one is not decidable until the
                # members have been read: they must all be character literals.
                saved = reader.pos
                literal = self._string_members()
                if literal is not None:
                    self._precedence = PRIMARY_PRECEDENCE
                    return builder.expression("string_literal", [builder.raw(literal)])
                reader.pos = saved
            members = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated braced initialiser")
                members.append(self._element())
            self._precedence = SIMPLE_PRECEDENCE
            return builder.expression("braced", [kind, "{", *self._commas(members), "}"])

        if pair == "il":
            reader.pos += 2
            members = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated initialiser list")
                members.append(self._element())
            self._precedence = SIMPLE_PRECEDENCE
            return builder.expression("initialiser_list", ["{", *self._commas(members), "}"])

        if pair == "qu":
            reader.pos += 2
            binding = PRECEDENCE["qu"]
            condition = self._operand(binding + 1, subexpression=True)
            when_true = self._operand(binding, subexpression=True)
            when_false = self._operand(binding, subexpression=True)
            self._precedence = binding
            # c++filt writes the `?` hard against its operands and the `:` spaced off
            # them -- `{parm#1}?std::x : std::y` -- which is the same asymmetry its
            # binary operators have.
            mark = "?" if self.options.gnu_expression_spelling else " ? "
            return builder.expression("conditional", [condition, mark, when_true, " : ", when_false])

        if pair in ("dt", "pt"):
            # <expression> ::= dt <expression> <unresolved-name>  (and `pt` for `->`)
            reader.pos += 2
            owner = self._operand(POSTFIX_PRECEDENCE)
            joiner = "." if pair == "dt" else "->"
            # Ordinarily an <unresolved-name>, and the fast path for it is inside
            # `expression`; but the grammar Clang emits allows any expression on the
            # right, and `ptT_Li4E` -- `4u->4` -- is one of those.
            name = self.expression()
            self._precedence = POSTFIX_PRECEDENCE
            return builder.expression("member", [owner, joiner, name])

        if pair == "ix":
            reader.pos += 2
            # Subscript is the one postfix form the reference brackets against another
            # postfix -- `(fp[fp])[fp]`, but `fp.a.b` and `fp++++` unbracketed.
            owner = self._operand(PRIMARY_PRECEDENCE)
            index = self.expression()
            self._precedence = POSTFIX_PRECEDENCE
            return builder.expression("subscript", [owner, "[", index, "]"])

        if pair == "gs":
            # A leading `::` forcing global scope. It introduces either a global-scope
            # allocation -- `::new`, `::delete` -- or a global-scope name, and only the
            # next production says which.
            if reader.ahead2(2) in ("nw", "na", "dl", "da"):
                reader.pos += 2
                self._precedence = UNARY_PRECEDENCE
                return builder.expression("global_scope", ["::", self.expression()])
            return builder.raw(self.unresolved_name())

        if pair == "sp":
            # A pack expansion inside an expression, under the same rule as `Dp`.
            reader.pos += 2
            outer_pack = self._saw_pack
            outer_empty = self._saw_empty_pack
            self._saw_pack = False
            self._saw_empty_pack = False
            try:
                expanded = self.expression()
                over_pack = self._saw_pack
                over_empty = self._saw_empty_pack
            finally:
                self._saw_pack = outer_pack or self._saw_pack
                self._saw_empty_pack = outer_empty
            if over_empty:
                # The pattern ranges over a pack with no members, so it expands to no
                # arguments at all -- not to one argument with an empty list inside it.
                # `f(xs...)` over an empty `xs` is `f()`, and this is what makes the
                # comma before it disappear too; `Dp` does the same in a type list.
                self._precedence = PRIMARY_PRECEDENCE
                return builder.parameter_pack([])
            if over_pack or self._scope_has_pack:
                # The expansion *is* its members, so it binds however they do.
                return expanded
            # An unexpanded one is `x...`, which GNU brackets as an operand.
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("pack_expansion", [expanded, "..."])

        if pair == "nw" or pair == "na":
            reader.pos += 2
            arguments = []
            while not reader.eat("_"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated new expression")
                arguments.append(self.expression())
            kind = self.type_()
            keyword = "new" if pair == "nw" else "new[]"
            gap = " " if self.options.gnu_expression_spelling else ""
            placement = [gap, "(", *self._commas(arguments), ")"] if arguments else []
            self._precedence = UNARY_PRECEDENCE
            parts = [keyword, *placement, " ", kind]
            if not reader.eat("E"):
                parts.append(self.initialiser())
            return builder.expression("new", parts)

        if pair in ("dl", "da"):
            reader.pos += 2
            keyword = "delete" if pair == "dl" else "delete[]"
            operand = self.expression()
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("delete", [keyword, " ", operand])

        if pair in ("dc", "sc", "cc", "rc"):
            reader.pos += 2
            casts = {"dc": "dynamic_cast", "sc": "static_cast", "cc": "const_cast", "rc": "reinterpret_cast"}
            kind, inner = self.type_(), self.expression()
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("named_cast", [casts[pair], "<", kind, ">(", inner, ")"])

        if pair in POSTFIX_OPERATORS:
            # 5.1.6: `pp`/`mm` are postfix; `pp_`/`mm_` are the prefix forms.
            reader.pos += 2
            if reader.eat("_"):
                operand = self._operand(PRIMARY_PRECEDENCE, subexpression=True)
                self._precedence = UNARY_PRECEDENCE
                return builder.expression("unary", [POSTFIX_OPERATORS[pair], operand])
            operand = self._operand(POSTFIX_PRECEDENCE, subexpression=True)
            self._precedence = POSTFIX_PRECEDENCE
            return builder.expression("postfix", [operand, POSTFIX_OPERATORS[pair]])

        if pair in PREFIX_OPERATORS:
            reader.pos += 2
            operator = PREFIX_OPERATORS[pair]
            if self.options.gnu_entity_operand_spelling and (reader.startswith("L_Z") or reader.startswith("LZ")):
                operand = builder.raw(self._entity_operand(operator))
            else:
                # A prefix operator brackets anything that is not already primary,
                # including another prefix operator: the references print `!(!true)`,
                # not `!!true`.
                operand = self._operand(PRIMARY_PRECEDENCE, subexpression=True)
            self._precedence = UNARY_PRECEDENCE
            return builder.expression("unary", [operator, operand])

        if pair[:1] == "f" and pair[1:] in ("l", "r", "L", "R"):
            # <expression> ::= fL <binary-operator> <expression> <expression>  # left, init
            #                | fR <binary-operator> <expression> <expression>  # right, init
            #                | fl <binary-operator> <expression>               # left
            #                | fr <binary-operator> <expression>               # right
            marker = pair[1]
            reader.pos += 2
            code = reader.peek2()
            if code not in INFIX_OPERATORS:
                raise ParseError(self._mangled, reader.pos, f"unknown fold operator {code!r}")
            reader.pos += 2
            left_fold = marker in ("l", "L")
            initialiser = None
            if marker == "L":
                # The initialiser comes first for a left fold and second for a right one.
                initialiser = self._operand(UNARY_PRECEDENCE)
                pack = self._fold_pack()
            elif marker == "R":
                pack = self._fold_pack()
                initialiser = self._operand(UNARY_PRECEDENCE)
            else:
                pack = self._fold_pack()
            operator = f" {INFIX_OPERATORS[code]} "
            # `[init op ]... [op pack]` for a left fold and `[pack op ]... [op init]`
            # for a right one, with the halves an absent initialiser leaves out.
            before = initialiser if left_fold else pack
            after = pack if left_fold else initialiser
            parts = ["("]
            if before is not None:
                parts.append(before)
                parts.append(operator)
            parts.append("...")
            if after is not None:
                parts.append(operator)
                parts.append(after)
            parts.append(")")
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("fold", parts)

        if pair in INFIX_OPERATORS:
            reader.pos += 2
            binding = PRECEDENCE.get(pair, 1)
            # The side that does *not* absorb an equal-precedence neighbour needs the
            # brackets: for a left-grouping operator that is the right operand.
            right_associative = pair in RIGHT_ASSOCIATIVE
            left = self._operand(binding + 1 if right_associative else binding, subexpression=True)
            right = self._operand(binding if right_associative else binding + 1, subexpression=True)
            spelling = INFIX_OPERATORS[pair]
            if pair == "cm":
                # Bracketed only where it sits in a comma-separated list -- a call's
                # arguments, a braced initialiser, a template argument -- which is what
                # `_element` is for. `decltype(4, 3)` has no brackets and gets none.
                self._precedence = binding
                return builder.expression("comma", [left, ", ", right])
            self._precedence = binding
            gap = "" if pair in TIGHT_INFIX or self.options.gnu_expression_spelling else " "
            return builder.expression("binary", [left, gap, spelling, gap, right])

        if pair in ("rq", "rQ"):
            # `requires_expression` sets it to PRIMARY itself.
            return self.requires_expression()

        if pair in ("di", "dx", "dX"):
            # A designated initialiser inside a braced initialiser list.
            #
            #   di <field source-name> <braced-expression>       # .name = expr
            #   dx <index expression> <braced-expression>        # [expr] = expr
            #   dX <begin expression> <end expression> <braced-expression>  # [a ... b] = expr
            #
            # They chain: `.a.b[3][1 ... 4] = 9` is four of them round one value, and
            # only the innermost writes the `=`.
            reader.pos += 2
            if pair == "di":
                designator = ["." + self.source_name()]
            elif pair == "dx":
                designator = ["[", self.expression(), "]"]
            else:
                designator = ["[", self.expression(), " ... ", self.expression(), "]"]
            nested = reader.peek2() in ("di", "dx", "dX")
            value = self.expression()
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("designator", [*designator, *([] if nested else [" = "]), value])

        if pair == "so":
            # so <referent type> <expression> [<offset>] <union-selector>* [p] E
            #
            # A subobject of a named object, used for a template argument that points
            # into one. The selectors and the one-past-the-end marker narrow which
            # subobject; neither reference prints them.
            reader.pos += 2
            kind = self.type_()
            inner = self.expression()
            offset = reader.number(allow_negative=True) if reader.peek() not in ("E", "_", "p") else "0"
            while reader.eat("_"):
                if reader.peek() in DIGITS:
                    reader.number(allow_negative=False)
            reader.eat("p")
            reader.expect("E")
            if offset.startswith("n"):
                offset = "-" + offset[1:]
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("subobject", [inner, ".<", kind, f" at offset {offset or '0'}>"])

        if pair == "mc":
            # mc <type> <expression> [<offset>] E -- a pointer-to-member conversion.
            reader.pos += 2
            kind = self.type_()
            inner = self.expression()
            if reader.peek() not in ("E", ""):
                reader.number(allow_negative=True)
            reader.expect("E")
            self._precedence = UNARY_PRECEDENCE
            return builder.expression("cast", ["(", kind, ")(", inner, ")"])

        if pair == "sy":
            # sy <pack> <index> -- C++26 pack indexing, `(pack...)[index]`.
            reader.pos += 2
            pattern = self.template_param() if reader.peek() == "T" else self.expression()
            index = self.expression()
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("pack_index", ["(", pattern, "...)[", index, "]"])

        if pair == "cp":
            # cp <base-unresolved-name> <expression>* E
            #
            # A call written with the callee parenthesised, which suppresses the
            # argument-dependent lookup a bare name would have had.
            reader.pos += 2
            target = builder.raw(self.base_unresolved_name())
            arguments = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated call expression")
                arguments.append(self._element())
            self._precedence = POSTFIX_PRECEDENCE
            return builder.expression("call", ["(", target, ")(", *self._commas(arguments), ")"])

        if reader.peek() == "u":
            # <expression> ::= u <source-name> <template-arg>* E
            #
            # A vendor extended expression, spelled as a call: `u11__alignof__T_E` is
            # `__alignof__(int)`. Clang emits it for the alignof and uuidof builtins.
            reader.pos += 1
            name = self.source_name()
            arguments = []
            if name == "__uuidof" and reader.peek() in ("t", "z"):
                # The legacy `__uuidof` mangling puts a `t` or `z` where the grammar
                # expects a <template-arg>, and takes exactly one operand with no
                # closing `E`. Neither `__uuidof(short)` nor `__uuidof(...)` can be
                # written, so the marker is not ambiguous with the <type> it looks like.
                marker = reader.take()
                arguments.append(self.type_() if marker == "t" else self.expression())
            else:
                while not reader.eat("E"):
                    if reader.eof:
                        raise ParseError(self._mangled, reader.pos, "unterminated vendor expression")
                    argument, _ = self.template_arg()
                    if argument is not None:
                        arguments.append(argument)
            self._precedence = POSTFIX_PRECEDENCE
            return builder.expression("call", [builder.raw(name), "(", *self._commas(arguments), ")"])

        # A bare name here is an <unresolved-name>: the grammar says so, and it matters
        # because a name in an expression creates no substitution entry while a <type>
        # does. A constraint like `Q 5Sized I T_ E` must contribute the `T` its argument
        # list mentions and nothing for `Sized` itself.
        if reader.peek() in DIGITS:
            text = self.unresolved_name()
            return self._named_operand(text, self._simple_name)

        # What remains that could open a type, is one: array bounds and non-type
        # template arguments both arrive here.
        if reader.peek() in _TYPE_STARTERS:
            self._precedence = PRIMARY_PRECEDENCE
            return self.type_()

        raise ParseError(self._mangled, reader.pos, "unrecognised expression")


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
    """Parse an Itanium mangled name into `builder`, returning its handle."""
    return ItaniumParser(mangled, builder, limits, options).parse()


def parse_type(mangled, builder, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
    """Parse a bare `<type>` -- `Pi`, `PKFvRiE` -- rather than a whole symbol.

    What `__cxa_demangle` reads and `llvm-cxxfilt` refuses, and what a `typeinfo` name
    or an RTTI descriptor carries. It is a separate entry point rather than a fallback
    inside `parse` for the reason `llvm-cxxfilt` refuses it there: a demangler offered
    every symbol in a binary and willing to read `i` as `int` will rename half a C
    library. Asked for deliberately, it is exactly what the caller wants.
    """
    if not mangled:
        raise NotMangledError(mangled, "empty type")
    parser = ItaniumParser(mangled, builder, limits, options)
    parser._reject_unbound_parameters = True
    handle = parser.type_()
    if not parser.reader.eof:
        raise ParseError(mangled, parser.reader.pos, f"unconsumed input {parser.reader.remaining!r}")
    if builder.size(handle) > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)
    return handle
