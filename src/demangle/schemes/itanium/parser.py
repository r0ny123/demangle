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
import struct

from ...core.errors import LimitExceeded, NotMangledError, ParseError, TruncatedError
from ...core.limits import DEFAULT_LIMITS
from ...core.reader import DIGITS, MAX_NUMBER_DIGITS, Reader
from .options import DEFAULT_OPTIONS
from .substitutions import (
    DeferredProduction,
    ParameterReference,
    SubstitutionMisuse,
    SubstitutionOverrun,
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

#: <template-param-decl> introducers.
_PARAMETER_DECLARATIONS = frozenset({"Ty", "Tk", "Tn", "Tt", "Tp"})


#: N1169 fixed-point types. The letter after `DA`/`DR` names the underlying integer;
#: plain `int` adds no word: `DAi` is `_Accum`, `DAs` is `short _Accum`.
_FIXED_POINT_INTEGERS = {
    "s": "short ",
    "t": "unsigned short ",
    "i": "",
    "j": "unsigned ",
    "l": "long ",
    "m": "unsigned long ",
}


#: Clang's allocation-token prefix, `__alloc_token_[<digits>_]`. It is no part of the
#: name, so it is stripped and reported as a suffix, as the reference prints it.
_ALLOC_TOKEN = "__alloc_token_"

#: Greedy, so the split is at the *last* `_block_invoke`: the enclosing function may be
#: named that too. An underscore with no number after it does not match.
_BLOCK_INVOKE = re.compile(r"(.*)_block_invoke(?:_\d+|\d*)(?:\..*)?\Z", re.DOTALL)

#: An operand binding no tighter than this needs brackets in a comma-separated list.
_COMMA_BINDING = PRECEDENCE["cm"]

#: Precedence known from an expression's first two letters, for operators whose operands
#: are read unbracketed; anything absent decides for itself. See `_operand`.
_NOMINAL_PRECEDENCE = {
    **{code: PRECEDENCE.get(code, 1) for code in INFIX_OPERATORS},
    **dict.fromkeys(PREFIX_OPERATORS, UNARY_PRECEDENCE),
    "qu": PRECEDENCE["qu"],
    "nw": UNARY_PRECEDENCE,
    "na": UNARY_PRECEDENCE,
    "dl": UNARY_PRECEDENCE,
    "da": UNARY_PRECEDENCE,
    "tw": UNARY_PRECEDENCE,
}

#: `Ts`, `Tu` and `Te` name a dependent type with the keyword the writer used.
_ELABORATED_KEYWORDS = {"s": "struct", "u": "union", "e": "enum"}

#: Hex digits per floating-point type. long double is the target's: x87 is written at the
#: type's width, which g++ pads -- 20 digits from clang, 24 from g++ i386, 32 from x86-64.
_FLOAT_WIDTHS = {"float": (8,), "double": (16,), "long double": (16, 20, 24, 32)}
#: An operator function's name: `operator+`, `operator int`, `operator""_km`, `operator()`.
_OPERATOR_FUNCTION = re.compile(r"operator(?![A-Za-z0-9_])")

#: The ABI says lowercase hexadecimal, and LLVM's demangler refuses anything else (18.1
#: misread `Lf3F800000E` as a wrong value); so do we.
_HEX = re.compile(r"[0-9a-f]+")


def _c_hex_float(kind, value):
    """The number as glibc's `%a` prints it, which is what `llvm-cxxfilt` shows.

    Every rule here is measured against `llvm-cxxfilt` 18 on x86-64 rather than taken
    from the C standard, because `%a` leaves the leading digit and the exponent's
    normalisation to the implementation and glibc's choices are what the reference
    prints. Normal numbers are `0x1.<fraction>p<exponent>` with trailing zeros dropped
    and the point with them, so `0x1p+0`; subnormals keep the `0x0.` and the smallest
    exponent; zero is `0x0p+0`, signed; infinity and NaN are the words, signed. A float
    is promoted to double first -- a subnormal float is a normal double, so `00000001`
    is `0x1p-149f` -- and `f` follows; `L` follows a long double.

    The x87 extended format keeps its integer bit in the mantissa, and glibc prints
    that mantissa's top four bits as the leading digit rather than normalising: `1.0L`
    is `0x8p-3L`, `3.14L` is `0xc.8f5c28f5c28f5c3p-2L`, a denormal is
    `0x0.000000000000001p-16385L`, an exponent field of zero prints as one would, and
    an integer bit clear under any other exponent -- an encoding no operation produces
    -- is a NaN. Checked against the reference over 4,580 random and boundary values.
    The IEEE quad follows the double's shape with 28 fraction digits and the exponent
    bias of 16383; no reference on this machine reads one, so that rule is glibc's
    `ldbl-128` printer read rather than measured.

    Thirty-two digits are a quad *or* the x87 format padded to the sixteen bytes a
    `long double` occupies on x86-64, which is what g++ writes there: `1.5L` is
    `0000000000003fffc000000000000000`, the six bytes of padding first because the
    encoding is most significant byte first. The two are told apart by those twelve
    zero digits. A quad with them zero is a denormal below 2^-16414, a value no template
    argument has ever held, while every long double g++ mangles on x86-64 has them; so
    the x87 reading wins, and the one quad it costs is documented in the tests. Reading
    the padded form as a quad printed `0x0.000000003fffcp-16382L` for `1.5L` -- a wrong
    number, where `llvm-cxxfilt` on x86-64 refuses the name for not being the twenty
    digits it expects and `c++filt` brackets the digits without reading them. g++ on
    i386 pads to twelve bytes the same way, twenty-four digits with four zeros in front.
    """
    if kind == "long double" and len(value) == 32 and value.startswith("000000000000"):
        value = value[12:]
    elif kind == "long double" and len(value) == 24:
        # Checked to open with its padding by `spell_float_literal`.
        value = value[4:]
    bits = int(value, 16)
    if kind == "float":
        # Exact: every float is a double.
        bits = int.from_bytes(struct.pack(">d", struct.unpack(">f", bits.to_bytes(4, "big"))[0]), "big")
        return _c_hex_binary(bits, 11, 52) + "f"
    if kind == "double" or len(value) == 16:
        return _c_hex_binary(bits, 11, 52) + ("" if kind == "double" else "L")
    if len(value) == 32:
        return _c_hex_binary(bits, 15, 112) + "L"
    # The x87 extended format: a sign, fifteen exponent bits, then a 64-bit mantissa
    # whose top bit is the integer bit.
    sign = "-" if bits >> 79 else ""
    exponent = (bits >> 64) & 0x7FFF
    mantissa = bits & ((1 << 64) - 1)
    if exponent == 0x7FFF:
        # Infinity is the integer bit alone; anything else under this exponent is a
        # NaN, the pseudo-infinity with the integer bit clear included.
        return sign + ("inf" if mantissa == 1 << 63 else "nan") + "L"
    if exponent == 0 and mantissa == 0:
        return f"{sign}0x0p+0L"
    if exponent and not mantissa >> 63:
        # An "unnormal" (set exponent, integer bit clear), which the hardware treats as invalid.
        # glibc prints it as a NaN.
        return f"{sign}nanL"
    fraction = f"{mantissa & ((1 << 60) - 1):015x}".rstrip("0")
    return f"{sign}0x{mantissa >> 60:x}{'.' + fraction if fraction else ''}p{max(exponent, 1) - 16386:+d}L"


def _c_hex_binary(bits, exponent_bits, fraction_bits):
    """`%a` for an IEEE binary format with a hidden integer bit: a sign bit, then
    `exponent_bits` of biased exponent, then `fraction_bits` of fraction."""
    sign = "-" if bits >> (exponent_bits + fraction_bits) else ""
    exponent = (bits >> fraction_bits) & ((1 << exponent_bits) - 1)
    mantissa = bits & ((1 << fraction_bits) - 1)
    if exponent == (1 << exponent_bits) - 1:
        return sign + ("inf" if mantissa == 0 else "nan")
    if exponent == 0 and mantissa == 0:
        return f"{sign}0x0p+0"
    fraction = f"{mantissa:0{fraction_bits // 4}x}".rstrip("0")
    leading = "1" if exponent else "0"
    bias = (1 << (exponent_bits - 1)) - 1
    return f"{sign}0x{leading}{'.' + fraction if fraction else ''}p{max(exponent, 1) - bias:+d}"


#: How the two references spell C99's complex and imaginary qualifiers, by `gnu_complex_spelling`.
_COMPLEX_WORDS = (
    {"C": "complex", "G": "imaginary"},
    {"C": "_Complex", "G": "_Imaginary"},
)

#: Cap on the characters one name's prefix entries may build, as a multiple of `max_output`.
#: Each <prefix> is a 5.1.10 candidate holding the whole prefix so far, so they sum to
#: O(N^2) (`_ZN` then 8,190 `1a` allocated 98MB). 16x is ~100x the largest real symbol's need.
_PREFIX_BUDGET = 16

#: What opens a `<prefix>` component other than an `<unqualified-name>`: substitution,
#: template parameter, decltype, template args, closure-prefix terminator, requires-clause.
_PREFIX_MARKERS = frozenset("STDIMQ")

#: What opens a `<class-enum-type>`: one set test on a hot arm of `_type`.
_CLASS_ENUM_START = frozenset(DIGITS | {"N", "Z", "L"})

#: The operators that measure or interrogate a type, as `(expression form, opening
#: text)`.
_MEASURING_OPERATORS = {
    "st": ("sizeof", "sizeof"),
    "sz": ("sizeof", "sizeof"),
    "at": ("alignof", "alignof"),
    "az": ("alignof", "alignof"),
    "ti": ("typeid", "typeid"),
    "te": ("typeid", "typeid"),
    "nx": ("noexcept", "noexcept"),
}

#: The three whose operand is a `<type>`; both references bracket it once.
_MEASURING_A_TYPE = frozenset({"st", "at", "ti"})

#: A callee GNU c++filt leaves unbracketed: an identifier, a `::` path of them, or a
#: function parameter. It wraps anything else.
_PLAIN_CALLEE = re.compile(r"(?:[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*|\{parm#\d+\})\Z")

#: Trailing ABI tags, which a constructor's repeated class name drops:
#: `failure[abi:cxx11]::failure`.
_ABI_TAGS_AT_END = re.compile(r"(?:\[abi:[^]]*\])+\Z")

_OBJC_PROTOCOL = "objcproto"
_OBJC_OBJECT = "objc_object"


#: Values below 7 have no escape and are written as one octal digit, as the reference does.
_STRING_ESCAPES = {7: "\\a", 8: "\\b", 9: "\\t", 10: "\\n", 11: "\\v", 12: "\\f", 13: "\\r"}

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _string_literal(values):
    r"""Spell a run of character values as a quoted string, the way the reference does.

    A `char` array in a braced initialiser is a string: `tl A6_c Lc72E Lc101E ...` is
    `"Hello"`, not `char [6]{(char)72, (char)101, ...}`. Both say the same thing and one
    of them is readable.

    Bytes above 127 are decoded as UTF-8 where they form it, so an emoji in a template
    argument comes back as itself rather than as four escapes -- and so does `"hé"`,
    which g++ mangles as the bytes `Lc195ELc169E` and clang as `Lcn61ELcn87E`, the same
    two bytes under `char`'s two signednesses. Where they do not form UTF-8, each such
    byte is escaped on its own, `\xC8`: the byte is what the name says, and the Latin-1
    character it once decoded to here is not.

    The one subtlety is `"\xF""ello"`. A hex escape has no length limit in C, so `\xF`
    followed by `e` would read as `\xFe`; the reference closes the string and opens
    another rather than emit something that means a different thing. An octal escape
    `\0`..`\6` is the same shape: `\27` is one character, and llvm-cxxfilt also
    splits before a hex letter that would not continue the octal, so `"\2e"` comes
    back `"\2""e"`. `tools/mutate.py --seed 8` found the split missing, which made
    `_Z1fIXtl5HellotlA6_cLc2ELc101E...` print `"\2e\xElo"` where the reference
    prints `"\2""e\xElo"`.
    """
    raw = bytes(value & 0xFF for value in values)
    # A byte that is not part of a UTF-8 sequence comes through as a lone surrogate,
    # U+DC80 to U+DCFF, and is escaped below; everything else decoded.
    text = raw.decode("utf-8", "surrogateescape")

    out = []
    previous_was_numeric_escape = False
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
        elif 0xDC80 <= code <= 0xDCFF:
            piece = f"\\x{code - 0xDC00:X}"
        else:
            piece = character
        if previous_was_numeric_escape and piece[:1] in _HEX_DIGITS:
            out.append('""')
        out.append(piece)
        previous_was_numeric_escape = piece.startswith("\\x") or (
            len(piece) == 2 and piece[0] == "\\" and piece[1] in "01234567"
        )
    return '"' + "".join(out) + '"'


def detect(name):
    """Cheap test for "is this plausibly an Itanium mangled name".

    Runs on every symbol a caller passes, including the overwhelming majority that are
    not mangled at all, so it does no work beyond a prefix comparison.

    `_GLOBAL__` is deliberately not one of the prefixes: `parse` has never read those
    names -- GNU's "global constructors keyed to ..." extension -- and claiming them
    here only meant handing them back unchanged one step later.
    """
    return (
        name.startswith("_Z")
        or name.startswith("__Z")
        or name.startswith("___Z")
        or name.startswith("____Z")
        or name.startswith(_ALLOC_TOKEN)
    )


def _drops_out(builder, handle):
    """Whether a parsed parameter spells to nothing and so is not a parameter at all.

    `Dp T_` over a pack bound to nothing expands to no parameters, and must not leave a
    separator behind. Asked with `size` rather than by spelling the parameter: `size` is
    O(1) by contract, where `spell` on the tree builder is a full recursive render of
    the subtree -- run here once per parameter of every function in the symbol table,
    for a question that is only ever "is it empty".
    """
    return builder.size(handle) == 0


class ItaniumParser:
    """Parses one mangled name into one builder. Single use.

    State -- cursor, substitution table, template scope -- is per name, so an instance
    is cheap and never shared. The builder, by contrast, is stateless and shared.
    """

    __slots__ = (
        "_abbrev",
        "_abbrev_expanded",
        "_ambiguous_unresolved_name",
        "_argument_constraint",
        "_auto_substitutes",
        "_bare_angle",
        "_bare_entity_prefix_used",
        "_bare_pack_used",
        "_closure_level",
        "_closure_prefix_entries",
        "_closure_prefix_seen",
        "_closure_prefix_substitutes",
        "_component_has_no_base_name",
        "_constrained_placeholder_recorded",
        "_conversion_unbound",
        "_ctor_dtor",
        "_declaration_after_a_pack",
        "_deferred",
        "_depth",
        "_drop_return",
        "_entity_local",
        "_entity_shape",
        "_expansion_handles",
        "_explicit_object",
        "_in_constraint",
        "_in_special_name",
        "_inherited_base_seen",
        "_inherited_base_substitutes",
        "_last_entry_index",
        "_last_source_name",
        "_legacy_pack_nested",
        "_legacy_pack_used",
        "_mangled",
        "_max_depth",
        "_max_output",
        "_module_names",
        "_modules",
        "_naming",
        "_no_return_type",
        "_objc_ids",
        "_objc_protocols",
        "_old_unresolved_names",
        "_pack_arity",
        "_pack_ids",
        "_pack_index",
        "_pack_named_through_a_back_reference",
        "_packs",
        "_parameter_counts",
        "_parameter_uses",
        "_pending_conversion",
        "_precedence",
        "_prefix_bare",
        "_prefix_bare_has_no_base_name",
        "_prefix_ended_on",
        "_prefix_has_args",
        "_prefixes",
        "_productions",
        "_reading_closure_signature",
        "_reading_conversion_type",
        "_reading_pattern",
        "_reject_unbound_parameters",
        "_rework",
        "_saw_empty_pack",
        "_saw_pack",
        "_scope_has_pack",
        "_simple_name",
        "_size",
        "_specialised_handles",
        "_template_name_argument",
        "_template_name_entries",
        "_template_template_param_floor",
        "_template_template_shifted",
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
        #: Whether `Da` and `Dc` enter the substitution table; see
        #: `ItaniumOptions.undeduced_auto_substitution`. Decided by the name's form when
        #: the option leaves it open: the Mach-O underscore means Apple's clang.
        auto_rule = options.undeduced_auto_substitution
        self._auto_substitutes = mangled.startswith("__Z") if auto_rule is None else auto_rule
        #: Whether the prefix before a closure's `M` enters the table; see
        #: `ItaniumOptions.closure_prefix_substitution`. Open means: Mach-O underscore is Apple's
        #: clang, which leaves it out; else the ABI's rule, and `parse` retries the other.
        closure_rule = options.closure_prefix_substitution
        self._closure_prefix_substitutes = not mangled.startswith("__Z") if closure_rule is None else closure_rule
        #: Whether a closure prefix was read at all, which is what makes the retry worth
        #: making: the other rule changes nothing else.
        self._closure_prefix_seen = False
        #: Table indices of the entries a `<type>` may not name: closure prefixes, and template
        #: names whose arguments must follow.
        self._closure_prefix_entries = set()
        self._template_name_entries = set()
        #: The lowest table index a `<template-template-param>` took, and whether an `S<n>_`
        #: resolved at or after it. `T_ I ... E` records the parameter and its specialisation
        #: (5.1.10; g++ and clang agree, `T_IT0_Li3EES5_`); llvm-cxxfilt 18 records only the
        #: second. Read by tools/enumerate.py.
        self._template_template_param_floor = 1 << 30
        self._template_template_shifted = False
        #: The index the last `S<n>_` named, or None after an abbreviation.
        self._last_entry_index = None
        #: Whether an `S<n>_` named a pack-bound `<template-param>`'s entry and got the pack
        #: (CONFORMANCE.md note 17); llvm-cxxfilt records the first member there, c++filt the last.
        #: Read by tools/enumerate.py. See `_note_pack_binding`.
        self._pack_named_through_a_back_reference = False
        #: How many `Dp` patterns are being read for their arity: a modifier over an
        #: empty pack is the pattern's business there, not a refusal's.
        self._reading_pattern = 0
        #: Whether a pack with members stood as a type outside any expansion (no compiler writes
        #: it); llvm-cxxfilt reads the first member, c++filt refuses. Read by tools/enumerate.py.
        self._bare_pack_used = False
        #: Whether the next `<type>` stands directly as a template argument, where a template's
        #: bare name is allowed (`ScalarMemoTable<int, HashTable>`). Set by `template_arg`,
        #: cleared by the first `<type>` that looks.
        self._template_name_argument = False
        expanded = STD_ABBREVIATIONS_EXPANDED_GNU if options.expand_std_abbreviations else STD_ABBREVIATIONS_EXPANDED
        self._entity_shape = None
        self._simple_name = False
        self._entity_local = False
        self._abbrev_expanded = expanded
        self._abbrev = expanded if options.expand_std_abbreviations else STD_ABBREVIATIONS
        self.reader = Reader(mangled)
        self.builder = builder
        # Called on every type read; bound once.
        self._size = builder.size
        self.limits = limits
        # Hot path: read once rather than through the frozen `limits` dataclass.
        self._max_depth = limits.max_depth
        self._max_output = limits.max_output
        self._prefixes = _PREFIX_BUDGET * limits.max_output
        self.subs = SubstitutionTable(mangled, limits.max_substitutions)
        self.targs = TemplateArgumentTable()
        # Template-parameter resolutions so far. A production that advanced this depended on
        # its scope, so its entry is kept as its input span -- see `DeferredProduction`.
        self._parameter_uses = 0
        # Re-read spans, keyed by (entry index, scope generation). Without it a chain `T_`,
        # `P S0_`, `P S1_`, ... is quadratic; tests/test_substitution_parameters.py pins that.
        self._deferred = {}
        self._depth = 0
        # Precedence of the expression just parsed, read by a containing operator to decide
        # on brackets.
        self._precedence = PRIMARY_PRECEDENCE
        # Pack handles seen in this name. The list holds strong references so an `id()` in
        # the set can never be reused by a new handle.
        self._packs = []
        self._pack_ids = set()

        # Whether the name just parsed was a constructor or destructor. They are the
        # one case where a template specialisation still encodes no return type.
        self._ctor_dtor = False
        #: Whether an inheriting constructor's `<base class type>` was read, and whether it was
        #: entered in the table; see `ItaniumOptions.inherited_constructor_substitution`.
        self._inherited_base_seen = False
        self._inherited_base_substitutes = options.inherited_constructor_substitution is True
        # True while a special name's operand is read: a local entity there takes no function
        # type, and a template parameter is refused (both references refuse `_ZTVN1AIcT_EE`).
        # See `local_name` and `bind_template_param`.
        self._in_special_name = False
        # Whether the <unqualified-name> just read has no base name for a constructor
        # or destructor to repeat, and whether the component `_prefix_bare` holds is
        # one; see `enclosing_class_name`.
        self._component_has_no_base_name = False
        #: The last <source-name> read, template arguments aside: libiberty's
        #: `di->last_name`, which is what c++filt names a constructor or destructor
        #: after when the scope itself has no name to repeat. See `enclosing_class_name`.
        self._last_source_name = ""
        self._prefix_bare_has_no_base_name = False
        # Whether the last component of the nested name being read carries template
        # arguments, and the handles of every specialisation entered in the
        # substitution table -- see the `I` branch of `prefix_component`.
        self._prefix_has_args = False
        # Keyed by identity and holding the handle too, so that a handle the parser has
        # let go of cannot lend its address to a later one.
        self._specialised_handles = {}
        # The handles the `Dp` production recorded as expansions, as distinct from the
        # packs they range over; see `_pack_aware`.
        self._expansion_handles = {}
        # True while reading the declared entity's name. Only its own template arguments become
        # the `T_` scope, not those of a type in the parameter list.
        self._naming = True
        # Module names by handle identity, plus strong references. A module name is a
        # substitution candidate that decorates the following name rather than being a component.
        self._modules = []
        self._module_names = {}
        # Protocol-qualified `objc_object` handles, which one pointer collapses into `id<...>`;
        # tracked by identity like packs and module names.
        self._objc_ids = []
        self._objc_protocols = {}
        # Types read so far, and how many this parse may re-read. Conversion-operator types and
        # pack patterns are re-read, and nested expansions multiply, so the actual re-reading
        # work is charged as it is spent, generously against `max_output`.
        self._productions = 0
        self._rework = 2 * limits.max_output + 4096
        # Set only by `parse_type`: a bare `<type>` has no enclosing template, so an unbound `T_`
        # is refused rather than spelled `auto` (see `template_param`).
        self._reject_unbound_parameters = False
        # Set for the function enclosing a local name, whose return type GNU c++filt omits:
        # parsed, then discarded.
        self._drop_return = False
        # Set by a conversion operator, which encodes no return type at all. Unlike
        # `_drop_return`, there is nothing in the input to read.
        self._no_return_type = False
        # Whether the name just read declares its object parameter explicitly, `N H ...`.
        # C++23 lets a member function name the object it is called on, and the
        # reference marks that parameter `this`.
        self._explicit_object = False
        # Where an unread conversion operator's type starts, and how many substitution
        # entries there were before it: `(position, mark)`, or None. See
        # `_reread_conversion`.
        self._pending_conversion = None
        #: The requires-clause in the entity's own template arguments, `I ... Q <constraint> E`,
        #: spelled under the gnu style, which prints it after the parameters; None otherwise.
        self._argument_constraint = None
        #: Whether the conversion operator's type just read resolved a template
        #: parameter with nothing in scope -- a spelling that stands only until the
        #: operator's own arguments bind it. See `_reread_conversion`.
        self._conversion_unbound = False
        self._ambiguous_unresolved_name = False
        self._old_unresolved_names = False
        #: True while an expression is being read as a template argument under the
        #: llvm style and no bracket has opened since: where a `>` would be taken for
        #: the end of the list, and `BinaryExpr::printLeft` wraps one. See
        #: `_bracketed_expression`.
        self._bare_angle = False
        # True while that type is being read for the first time, before the arguments
        # exist. The one place a `<template-param>` may resolve to nothing with no
        # <template-args> in scope at all -- see `bind_template_param`.
        self._reading_conversion_type = False
        # True while a `<closure-type-name>`'s signature is read: a generic lambda's `auto`
        # parameters are unbound `<template-param>`s (ABI 5.1.8). See `bind_template_param`.
        self._reading_closure_signature = False
        self._closure_level = None
        #: Whether an argument pack was read from the pre-`J` form, `I <template-arg>* E`.
        #: Read by tools/enumerate.py, which knows `llvm-cxxfilt` refuses that form.
        self._legacy_pack_used = False
        #: Whether such a pack stood as a direct member of another pack, `J ... I ... E
        #: ... E`, a shape no compiler writes. Read by tools/enumerate.py as well.
        self._legacy_pack_nested = False
        #: Whether an `<expr-primary>` used the g++ bare `Z` rather than `_Z`, which llvm-cxxfilt
        #: refuses inside an expression. Read by tools/enumerate.py. See `expr_primary`.
        self._bare_entity_prefix_used = False
        #: Whether a `Dk`/`DK` placeholder was recorded as a candidate (it is a `<type>`, 5.1.10);
        #: llvm-cxxfilt 18 records nothing for it. Read by tools/enumerate.py. See `type_`.
        self._constrained_placeholder_recorded = False
        #: Whether a lambda declared a template parameter after a pack, which no
        #: compiler writes -- a pack must be last -- and where `c++filt` 2.42 stops the
        #: list rather than refusing the name. Read by tools/enumerate.py.
        self._declaration_after_a_pack = False
        # Whether the last component of the <prefix> being read was a `<substitution>`, which
        # cannot end a <nested-name> -- see `nested_name`.
        self._prefix_ended_on = ""
        # The last <prefix> component before its template arguments, or None where it was not
        # an <unqualified-name>: what a constructor repeats. See `enclosing_class_name`.
        self._prefix_bare = None
        # Whether a head `<template-param>` may take template arguments. False in a conversion
        # operator's type, where a following `I` opens the operator's own list (`cvT_I4MerpE`).
        self._try_template_args = True
        # Whether the arguments in scope include a pack. If so a `Dp` expansion is spelled as
        # the members and already has its ellipsis; if not, the ellipsis is printed.
        self._scope_has_pack = False
        # Per-kind counters for the synthetic `$T` / `$N` / `$TT` names a generic
        # lambda's declared template parameters are spelled with.
        self._parameter_counts = {}
        # True while reading a requires-clause, where parameters are spelled by name.
        self._in_constraint = False
        # Set when a <template-param> resolved to an empty pack: the only way to tell that a
        # `Dp` pattern expands to nothing.
        self._saw_empty_pack = False
        # Whether the type just read mentioned a pack-bound parameter, so is already expanded
        # and takes no ellipsis.
        self._saw_pack = False
        # The arity of the pack a pattern mentions, and the member being spelled: `Dp unary<T_>`
        # becomes `unary<int>, unary<float>`, not `unary<int, float>`.
        self._pack_arity = None
        self._pack_index = None
        # Whether the list just read ended in an empty pack, which is dropped: GNU c++filt then
        # writes `A<B<int>>` rather than `A<B<int> >`.
        self._trailing_empty_pack = False

    # The depth guard is inlined at each of its seven sites: this is the hottest path, and a
    # call is two frames. The restore is absolute, `self._depth = depth - 1`, because when the
    # guard itself raises no `finally` has undone its increment.

    def parse(self):
        """<mangled-name> ::= _Z <encoding> [. <vendor-specific suffix>]"""
        reader = self.reader
        mangled = reader.text
        if mangled.startswith(("___Z", "____Z")):
            return self.block_invocation()
        # See `_ALLOC_TOKEN`. `__alloc_token_malloc` is not one: what follows must be a mangled
        # name itself.
        alloc_token = ""
        if mangled.startswith(_ALLOC_TOKEN):
            after = reader.pos + len(_ALLOC_TOKEN)
            digits = after
            while digits < reader.length and reader.text[digits] in DIGITS:
                digits += 1
            if digits > after and digits < reader.length and reader.text[digits] == "_":
                after = digits + 1
            if reader.text.startswith(("_Z", "__Z"), after):
                reader.pos = after
                alloc_token = " (.alloc_token)"
        # A Mach-O symbol carries the linker's extra underscore. Strip it only before a second
        # one, or `_Z1fv` loses the underscore the grammar needs.
        if reader.startswith("__Z"):
            reader.take()
        if not reader.eat("_Z"):
            raise NotMangledError(self._mangled, "not an Itanium mangled name")

        result = self.encoding()

        if not reader.eof:
            suffix = reader.remaining
            # A clone suffix (`.cold`, `.part.0`, `.llvm.<hash>`) is only identifiable as what the
            # grammar did not consume: `.` also occurs inside Clang's coroutine frame identifiers.
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
        reader = self.reader
        pos = reader.pos
        if pos < reader.length and reader.text[pos] in "TG":
            special = self.special_name()
            if special is not None:
                self._entity_shape = ("special", None)
                return special
        elif self._depth >= self._max_depth:
            # The guard `special_name` would have applied.
            raise LimitExceeded(self._mangled, "recursion depth", self._max_depth)

        name, quals, ref_qualifier, is_template = self.name()

        pos = reader.pos
        if pos >= reader.length or reader.text[pos] in "E.":
            # A data symbol: a name and nothing after it, and so no return type for a
            # conversion operator's name to have suppressed.
            self._drop_return = False
            self._no_return_type = False
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
        pos = reader.pos
        if pos < reader.length and reader.text[pos] == "U" and reader.startswith("Ua9enable_ifI"):
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
            if self._no_return_type:
                # A conversion operator encodes no return type: `_ZN1ScviEiv` is `S::operator int(int, void)`.
                self._no_return_type = False
                returns = None
            else:
                returns = self.type_() if is_template else None
                if self._drop_return:
                    # In the input but not printed; see `_drop_return`.
                    returns = None
                    self._drop_return = False

            parameters = []
            read = 0
            # Noted before reading: a `T_` bound to `void` also spells `void`, and does not mean
            # an empty list.
            wrote_void = False
            keep_empty = self.options.gnu_empty_pack_spelling
            text = reader.text
            size = self._size
            while True:
                pos = reader.pos
                if pos >= reader.length:
                    break
                char = text[pos]
                if char == "E" or char == "." or char == "Q":
                    break
                if not read:
                    wrote_void = char == "v"
                parameter = self.type_()
                read += 1
                if not size(parameter):
                    # An expansion over an empty pack is not the explicit object parameter. c++filt still
                    # prints it as an empty entry, `f(, int)`, unless it ends the list.
                    if keep_empty:
                        parameters.append(parameter)
                    continue
                if explicit_object:
                    explicit_object = False
                    parameter = builder.raw("this " + builder.spell(parameter))
                parameters.append(parameter)
            if keep_empty:
                self._drop_trailing_empties(parameters)
            if not read:
                # `<signature type>+`: a specialisation spends its first type on the return, so
                # `_Z1fIiEi` has no parameters, and both references refuse it. Counted, because `v` and
                # empty expansions legitimately leave `parameters` empty.
                raise ParseError(self._mangled, reader.pos, "a function signature has no parameter types")
        finally:
            self._naming = was_naming

        if read == 1 and wrote_void:
            # `f(void)` is `f()` only when the one signature type was written `v`, as both
            # references read it: `_Z1fIvEvT_` is `f<void>(void)`, and `_Z1fvv` is `f(void, void)` to
            # c++filt (llvm-cxxfilt refuses it).
            parameters = []

        suffix = ""
        if quals:
            suffix += " " + " ".join(quals)
        if ref_qualifier:
            suffix += " " + ref_qualifier
        suffix += attributes
        pos = reader.pos
        if pos < reader.length and reader.text[pos] == "Q":
            reader.pos = pos + 1
            # The requires-clause closes the declaration, after the qualifiers.
            suffix += " requires " + builder.spell(self.constraint_expression())
        if self._argument_constraint is not None:
            # c++filt then writes the argument list's clause: `requires D<int> requires C<int>` for
            # `IiQ1CIT_EE ... Q1DIT_E`. Only for the function's own list; c++filt refuses the other.
            if is_template:
                suffix += " requires " + self._argument_constraint
            self._argument_constraint = None
        # GNU prints `&Name` only for a function that is a bare name and parameter list; as a
        # callee it prints the name with its suffix. See `gnu_entity_operand_spelling`.
        self._entity_shape = ("function", name, returns is None and not suffix, suffix, is_template)
        return builder.function(returns, parameters, suffix, name)

    # -- 5.1.4 special names ---------------------------------------------------

    def special_name(self):
        """Vtables, typeinfo, thunks, guard variables. None if this is not one.

        Guarded: several of these productions contain an `<encoding>`, which can be
        another special name, so `_Z` followed by `GA` repeated is unbounded recursion.
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
            return self.builder.special(SPECIAL_TYPE_NAMES[code], self._special_operand_type())

        if code == "GI":
            # <special-name> ::= GI <module-name>
            reader.pos += 2
            module = self.module_name()
            if not module:
                # Not optional: the reference refuses `_ZGI`.
                raise ParseError(self._mangled, reader.pos, "a module initializer with no module")
            return self.builder.special("initializer for module ", self.builder.raw(module))

        if code == "TA":
            # <special-name> ::= TA <template-arg>
            #
            # A <template-arg>, not a <type>: `_ZTAX...E` carries an expression.
            reader.pos += 2
            argument, _ = self.template_arg()
            if argument is None:
                raise ParseError(self._mangled, reader.pos, "template parameter object without an argument")
            return self.builder.special("template parameter object for ", argument)

        if code == "GR":
            # GR <object name> _  /  GR <object name> <seq-id> _
            reader.pos += 2
            inner = self._object_name()
            # No seq-id means no `_` either (`_ZGRZN1N1gEvE1a`).
            label = self._encoding_special_label("GR")
            if not reader.eof:
                index = reader.seq_id()
                if self.options.gnu_special_name_spelling:
                    # c++filt numbers the temporary by its seq-id, `reference temporary
                    # #0 for f()::x` for `_ZGRZ1fvE1x_`; llvm-cxxfilt does not.
                    label = f"reference temporary #{index} for "
            return self.builder.special(label, inner)

        if code in SPECIAL_ENCODING_NAMES:
            reader.pos += 2
            # GA (GNU) takes any <encoding>, even another special name (`_ZGATW1x`); GV, TH and
            # TW take only an <object name>.
            inner = self.encoding() if code == "GA" else self._object_name()
            return self.builder.special(self._encoding_special_label(code), inner)

        if code == "GT":
            reader.pos += 2
            marker = reader.take()
            label = "transaction clone for " if marker == "t" else "non-transaction clone for "
            return self.builder.special(label, self.encoding())

        if code == "TC":
            # <special-name> ::= TC <type> <offset number> _ <base type>
            #
            # Spelled `<base>-in-<derived>`; the offset is not printed. Both types are ordinary
            # candidates: `_ZTCSt9strstream16_Si` uses `Si` because `St` was recorded.
            reader.pos += 2
            derived = self._special_operand_type()
            reader.number()
            reader.expect("_")
            base = self._special_operand_type()
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
        pos = reader.pos
        char = reader.text[pos] if pos < reader.length else ""

        if char == "N":
            return self.nested_name(as_type)
        if char == "Z":
            return self.local_name(as_type), (), "", False

        if char == "S":
            # Either an abbreviation or a back-reference, each of which may be an
            # <unscoped-template-name> that template arguments then attach to.
            candidate = reader.ahead(1) == "t"
            if candidate:
                reader.pos += 2
                inner = self.unqualified_name()
                base = self.builder.qualified([self.builder.name("std"), inner])
            else:
                base = self.substitution()
                named = self._module_of(base)
                if named is not None:
                    base = self.unqualified_name(module=named)
                    candidate = True
            if reader.peek() == "I":
                # An <unscoped-template-name> is a candidate (5.1.10), recorded before its arguments:
                # in `_ZSt4sortIPiEvT_S_`, `S_` is `std::sort`. A <substitution> is not recorded again
                # (llvm-cxxfilt refuses `_ZSbIwEvS_`).
                if candidate:
                    self.subs.remember(base, "unscoped-template-name")
                    self._note_entry(self._template_name_entries)
                specialised = self.apply_template_args(base)
                self._specialised_handles[id(specialised)] = specialised
                return specialised, (), "", True
            if not candidate:
                # A back-reference is a <name> only as an <unscoped-template-name>, so needs
                # <template-args>. llvm-cxxfilt refuses `_ZZ1fPiES_`; c++filt's `d_name` does not check.
                raise ParseError(self._mangled, reader.pos, "a substitution as a name must carry template arguments")
            return base, (), "", False

        base = self.unqualified_name()
        if reader.peek() == "I":
            self.subs.remember(base, "unscoped-template-name")
            self._note_entry(self._template_name_entries)
            specialised = self.apply_template_args(base)
            self._specialised_handles[id(specialised)] = specialised
            return specialised, (), "", True
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

    def _expand_pattern(self, start, mark, arity, read=None):
        """Read a pack expansion's pattern once per member of the pack it ranges over.

        The reader and the substitution table are put back exactly as the first reading
        left them, so a back-reference later in the name still counts what that reading
        entered and nothing the re-readings did. `read` is what reads the pattern: a
        type for `Dp`, an expression for `sp`.
        """
        reader = self.reader
        resume = reader.pos
        recorded = self.subs.capture(mark)
        outer_index = self._pack_index
        read = read or self.type_
        members = []
        try:
            for index in range(arity):
                spent = self._productions
                reader.pos = start
                self.subs.restore_from(mark, [])
                self._pack_index = index
                members.append(read())
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
        # Not naming: the arguments are already in scope, and a list nested in the type
        # (`Muncher<T_*...>`) must not install itself over them.
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
        text = reader.text
        # Only `block_invocation` moves the end, and not while a name is being read.
        end = reader.length
        pos = reader.pos
        if pos >= end or text[pos] != "N":
            raise ParseError(text, pos, "expected 'N'")
        pos += 1
        reader.pos = pos
        # `N H`: a C++23 explicit object member function, which has no qualifiers of its own.
        quals = ()
        ref_qualifier = ""
        char = text[pos] if pos < end else ""
        if char == "H":
            # Licensed by `peek`: the character it just returned is the one consumed.
            reader.pos += 1
            # `H` marks an explicit object parameter for the entity's own name only;
            # `parseNestedName` ignores it in a type.
            if not as_type:
                self._explicit_object = True
        else:
            if char in QUALIFIER_LETTERS:
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
        outer_has_args = self._prefix_has_args
        self._prefix_has_args = False
        # A nested name in a template argument keeps its own prefix state, or `A<B::C>::A` comes
        # back as `A<B::C>::C`; likewise the no-base-name flag (`NSbIw...IEC2E`).
        outer_bare = self._prefix_bare
        outer_bare_has_no_base_name = self._prefix_bare_has_no_base_name
        self._prefix_bare = None
        self._prefix_bare_has_no_base_name = False
        builder = self.builder
        subs = self.subs
        try:
            max_depth = self._max_depth
            while True:
                pos = reader.pos
                if pos >= end:
                    raise ParseError(self._mangled, pos, "unterminated nested name")
                char = text[pos]
                if char == "E":
                    reader.pos = pos + 1
                    break
                if char in DIGITS:
                    # `prefix_component` for its commonest case, a bare <source-name>, inlined. It
                    # neither recurses nor carries a module name on, and nothing here raises the depth.
                    if self._depth >= max_depth:
                        raise LimitExceeded(self._mangled, "recursion depth", max_depth)
                    self._prefix_ended_on = ""
                    self._component_has_no_base_name = False
                    # `plain_source_name`, inlined but for the anonymous namespace.
                    start = pos
                    pos += 1
                    if pos < end and text[pos] in DIGITS:
                        while pos < end and text[pos] in DIGITS:
                            pos += 1
                            if pos - start > MAX_NUMBER_DIGITS:
                                raise ParseError(text, start, "number too long")
                        if char == "0":
                            reader.padded_length = True
                        length = int(text[start:pos])
                    else:
                        length = ord(char) - 48
                    stop = pos + length
                    if stop > end:
                        raise TruncatedError(text, pos)
                    if not length or text[pos] == "_":
                        reader.pos = start
                        name = self.plain_source_name()
                    else:
                        reader.pos = stop
                        name = self._last_source_name = text[pos:stop]
                    if module:
                        name = f"{name}@{module}"
                        module = ""
                    pos = reader.pos
                    following = text[pos] if pos < end else ""
                    if following == "B":
                        name += self.abi_tags()
                        pos = reader.pos
                        following = text[pos] if pos < end else ""
                    component = builder.name(name)
                    parts.append(component)
                    self._prefix_bare = component
                    self._prefix_bare_has_no_base_name = False
                    self._prefix_has_args = False
                    is_template = False
                    if following == "E":
                        continue
                    if following == "M":
                        self._closure_prefix_seen = True
                        if not self._closure_prefix_substitutes:
                            continue
                    combined = parts[0] if len(parts) == 1 else builder.qualified(parts)
                    self._prefixes -= self._size(combined)
                    if self._prefixes < 0:
                        raise LimitExceeded(self._mangled, "output length", self._max_output)
                    if subs.recording:
                        # `SubstitutionTable.remember(combined, "prefix")`, inlined.
                        entries = subs._entries
                        if len(entries) >= subs._limit:
                            raise LimitExceeded(self._mangled, "substitution", subs._limit)
                        entries.append(combined)
                    if following == "M":
                        self._note_entry(self._closure_prefix_entries)
                    elif following == "I":
                        self._note_entry(self._template_name_entries)
                    continue
                depth = self._depth = self._depth + 1
                if depth > max_depth:
                    raise LimitExceeded(self._mangled, "recursion depth", max_depth)
                try:
                    is_template, module = self.prefix_component(parts, as_type, module)
                finally:
                    self._depth = depth - 1

            if not parts:
                raise ParseError(self._mangled, reader.pos, "empty nested name")
            if self._prefix_ended_on:
                # `<nested-name> ::= N ... <prefix> <unqualified-name> E`: the last component cannot be
                # a <substitution> (`_ZNSaEv`) or an `M` closing a data-member or closure prefix
                # (`_Z1fN1aME`). c++filt refuses both; llvm-cxxfilt accepts only `_Z1fN1aME`. As interior
                # components (`_ZNSaC1Ev`) they are fine.
                raise ParseError(self._mangled, reader.pos, f"a nested name ending in {self._prefix_ended_on}")
            # A template constructor has no return type to encode, so the signature's leading type
            # is a parameter.
            if self._ctor_dtor:
                is_template = False
        finally:
            self._ctor_dtor = outer_ctor_dtor
            self._prefix_bare = outer_bare
            self._prefix_bare_has_no_base_name = outer_bare_has_no_base_name
            self._prefix_has_args = outer_has_args
        name = parts[0] if len(parts) == 1 else self.builder.qualified(parts)
        if is_template:
            # The enclosing <type> production records this name; a back reference to
            # it takes no arguments either.
            self._specialised_handles[id(name)] = name
        return name, quals, ref_qualifier, is_template

    def _only_a_base_production(self, parts, what):
        """Refuse `what` where a `<prefix>` already has a component.

        ```
        <prefix> ::= <unqualified-name> | <prefix> <unqualified-name>
                   | <template-prefix> <template-args> | <closure-prefix>
                   | <template-param> | <decltype> | <substitution>
        ```

        Only two of those seven take a `<prefix>` on the left. The other five are bases:
        a `<substitution>`, a `<template-param>` and a `<decltype>` can *open* a prefix
        and cannot follow one, and the same is true of `<template-prefix>`. Reading them
        anywhere spelled a scope inside a scope that cannot contain it --
        `_ZN1aSt1bEv` as `a::std::b()`, `_ZN1aSa1bEv` as `a::std::allocator::b()`,
        `_ZN1a1bS_1cEv` as `a::b::a::c()`, `_ZN1aDtfp_E1bEv` as
        `a::decltype(fp)::b()` -- each of them a declaration a person would believe and
        none of them a name any compiler writes. Both references refuse every one.

        The abbreviations are the shape that occurs and are untouched at the front:
        `_ZNSt1a1bEv`, `_ZNSaIwE1bEv` and `_ZNSt3maxIiEEvv` all still read.
        """
        if parts:
            raise ParseError(self._mangled, self.reader.pos, f"{what} after the start of a nested name")

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
        self._prefix_ended_on = ""
        text = reader.text
        pos = reader.pos
        char = text[pos] if pos < reader.length else ""

        # `nested_name` reads a <source-name> itself; one set test skips the markers below.
        if char in _PREFIX_MARKERS:
            if char == "S":
                expanded = text[pos : min(pos + 2, reader.length)] in STD_ABBREVIATIONS and (
                    self._abbreviation_scopes_a_structor()
                )
                component = self.substitution(expanded=expanded)
                named = self._module_names.get(id(component)) if self._module_names else None
                if named is not None:
                    # A module name, not a scope: it belongs to the component that
                    # follows, and it decorates that component wherever it stands. The
                    # position rule below is about a substitution used as a *scope*.
                    return False, named
                if parts:
                    self._only_a_base_production(parts, "a substitution")
                self._prefix_ended_on = "a substitution"
                self._prefix_bare = None
                self._prefix_has_args = id(component) in self._specialised_handles
                parts.append(component)
                return False, module

            if char == "I":
                # <template-prefix> <template-args>: the arguments attach to the component
                # just read, and the pair becomes one substitutable component.
                if not parts:
                    raise ParseError(self._mangled, reader.pos, "template arguments with no name")
                if self._prefix_has_args:
                    # A specialisation takes no further arguments: llvm-cxxfilt refuses `_Z1fN1AIiEIcEE`.
                    raise ParseError(self._mangled, reader.pos, "template arguments on a specialisation")
                pending = self._conversion_pending()
                # Captured before the arguments are read: an argument may be a nested
                # name of its own, and reading it moves `_prefix_bare` on.
                bare = parts[-1]
                bare_is_conversion = self._prefix_bare_has_no_base_name
                arguments = self.template_arguments(install_scope=True)
                angle_space = not self._trailing_empty_pack
                if pending is not None:
                    bare = parts[-1] = builder.name(self._reread_conversion(pending))
                self._prefix_bare = bare
                self._prefix_bare_has_no_base_name = bare_is_conversion
                parts[-1] = builder.template(parts[-1], arguments, angle_space)
                combined = parts[0] if len(parts) == 1 else builder.qualified(parts)
                self._prefix_has_args = True
                self._specialised_handles[id(parts[-1])] = parts[-1]
                self._specialised_handles[id(combined)] = combined
                # Only an interior <template-prefix> <template-args> is a separate candidate. Before the
                # closing `E` the enclosing <type> records it, and recording twice shifts every index.
                if reader.peek() != "E":
                    self.subs.remember(self._spend_prefix(combined), "prefix")
                return True, module

            if char == "T":
                self._only_a_base_production(parts, "a template parameter")
                self._prefix_bare = None
                component, reference = self.template_param_binding()
                parts.append(component)
                self.subs.remember(reference if reference is not None else component, "template-template-param")
                self._note_template_template_param()
                return False, module

            if char == "D" and reader.ahead(1) in ("t", "T"):
                self._only_a_base_production(parts, "a decltype")
                self._prefix_bare = None
                parts.append(self.decltype_())
                return False, module

            if char == "M":
                # <closure-prefix> / <data-member-prefix> terminator; carries no spelling
                # of its own. It ends a <prefix>, so an <unqualified-name> still has to
                # follow before the `E` -- see `nested_name`.
                if self._prefix_bare is None:
                    # The prefix before `M` must be a spelled name: llvm-cxxfilt refuses `_ZNStM1xE`, which
                    # c++filt reads as `std::x`. `tools/mutate.py --seed 2`.
                    raise ParseError(self._mangled, reader.pos, "a data member or closure prefix over no spelled name")
                reader.take()
                self._prefix_ended_on = "a data member or closure prefix"
                self._prefix_bare = None
                return False, module

            # A <nested-name> has no requires-clause: `Q` falls through to `unqualified_name`, which
            # refuses it, as both references refuse `_ZN4llvm12_GLOBAL__N_1L1UQ13_SuperRegsSetE`.

        component = self.unqualified_name(scope=parts, module=module)
        parts.append(component)
        self._prefix_bare = component
        self._prefix_bare_has_no_base_name = self._component_has_no_base_name
        self._prefix_has_args = False
        pos = reader.pos
        following = text[pos] if pos < reader.length else ""
        if following != "E":
            if following == "M":
                # The prefix of a closure or data member: a candidate under the ABI, clang and GCC 13,
                # not under GCC 12 and earlier. See `ItaniumOptions.closure_prefix_substitution`.
                self._closure_prefix_seen = True
                if not self._closure_prefix_substitutes:
                    return False, ""
            combined = parts[0] if len(parts) == 1 else builder.qualified(parts)
            # `_spend_prefix`, inlined.
            self._prefixes -= self._size(combined)
            if self._prefixes < 0:
                raise LimitExceeded(self._mangled, "output length", self._max_output)
            self.subs.remember(combined, "prefix")
            if following == "M":
                self._note_entry(self._closure_prefix_entries)
            elif following == "I":
                self._note_entry(self._template_name_entries)
        return False, ""

    def _note_entry(self, entries):
        """Mark the entry just recorded as one of `entries`, where one was recorded.

        Nothing is recorded while a deferred production is re-read, and then there is
        nothing to mark: the entry that reading contributed was marked the first time.
        """
        if self.subs.recording:
            entries.add(len(self.subs) - 1)

    def _note_template_template_param(self):
        """Remember where the entry a `<template-template-param>` just took sits.

        Everything at or after it is numbered differently by `llvm-cxxfilt`, which does
        not record the parameter. See `_template_template_param_floor`.
        """
        if self.subs.recording:
            self._template_template_param_floor = min(self._template_template_param_floor, len(self.subs) - 1)

    def _as_special_operand(self, read):
        """Run `read` as a special name's operand: data, not a function, and no `T_`."""
        outer = self._in_special_name
        self._in_special_name = True
        try:
            return read()
        finally:
            self._in_special_name = outer

    def _special_operand_type(self):
        """The `<type>` a `TV`/`TI`/`TS`/`TC` special name operates on."""
        return self._as_special_operand(self.type_)

    def _object_name(self):
        """A special name's <object name>: a <name>, with nothing of a function about it.

        `GV`, `TH`, `TW` and `GR` name data. `parseSpecialName` reads the name and
        returns; both references refuse the leftover, so the name is read with no
        function type after it.
        """
        return self._as_special_operand(lambda: self.name()[0])

    def local_name(self, as_type=False):
        """<local-name> ::= Z <function encoding> E <entity name> [<discriminator>]
        | Z <function encoding> E s [<discriminator>]
        | Z <function encoding> Ed [<parameter number>] _ <entity name>
        """
        reader = self.reader
        builder = self.builder
        reader.expect("Z")
        # `Z <encoding> E` has template parameters of its own: without a fresh naming context its
        # `T_` would resolve against an enclosing template's (a lambda passed between templates).
        outer_naming = self._naming
        outer_scope = self.targs.snapshot()
        self._naming = True
        outer_drop_return = self._drop_return
        outer_no_return_type = self._no_return_type
        outer_explicit_object = self._explicit_object
        self._drop_return = not self.options.local_name_return_type
        self._no_return_type = False
        self._explicit_object = False
        # `_in_special_name` is about the entity after the `E`, not the enclosing function; left
        # set, `_ZGVZZN1A1fEvENKUlvE_clEvE1y` lost its `const`.
        outer_special = self._in_special_name
        self._in_special_name = False
        try:
            outer = self.encoding()
        finally:
            self._in_special_name = outer_special
            self._drop_return = outer_drop_return
            self._no_return_type = outer_no_return_type
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
                if (
                    not entity_is_type
                    and not self._in_special_name
                    and not reader.eof
                    and reader.peek() not in ("E", "_", ".")
                ):
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
            # A closure or unnamed type is a type, so no signature follows it; otherwise
            # `Z1gvEUlvE_S_` reads the next parameter as the lambda's signature.
            entity_is_type = as_type or reader.peek() == "U"
            inner, quals, ref_qualifier, is_template = self.name()
            # The signature is applied to the *combined* name, not to the entity alone:
            # a return type belongs at the front of the whole declaration, so a generic
            # lambda's `operator()` reads `auto f()::'lambda'<...>::operator()(...)` and
            # not `f()::auto 'lambda'...`.
            combined = builder.qualified([outer, inner])
            # A `.` is a clone suffix (`_ZZ1fvE1x.0`), which belongs to `parse`, not to a
            # signature.
            if (
                not entity_is_type
                and not self._in_special_name
                and not reader.eof
                and reader.peek() not in ("E", "_", ".")
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

    def unqualified_name(self, scope=None, module="", internal=False):
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
        self._component_has_no_base_name = False
        char = reader.peek()
        if char == "W":
            module = self.module_name(module)
            char = reader.peek()
            if (char == "C" and reader.ahead(1) in CONSTRUCTOR_KINDS) or (
                char == "D" and reader.ahead(1) in DESTRUCTOR_KINDS
            ):
                # `<unqualified-name> ::= [<module-name>] <operator-name> | <ctor-dtor-name> | ...`: a
                # structor's module is on the class name (`_ZNW4llvm6ModuleC1Ev`), so llvm-cxxfilt refuses
                # `_ZNStW9rGPRClassC2Ev`. `tools/mutate.py --seed 20`.
                raise ParseError(self._mangled, reader.pos, "a module name on a constructor or destructor")
        # `F` marks a friend declared inside the class it is a friend of. The scope is
        # already in `parts`, which `qualified` joins with `::`, so the marker decorates
        # the name that follows the last `::` -- see `_befriended` for where it goes.
        friend = False
        if char == "F" and scope and not internal:
            # `F` before `L`, as `parseUnqualifiedName` reads them: llvm-cxxfilt refuses
            # `_ZN1ALF3fooEv`. Licensed by `peek`.
            reader.pos += 1
            friend = True
            char = reader.peek()

        if char in DIGITS:
            name = self.plain_source_name()
            # `_in_module` is a call for a question that is almost always no, on the
            # production every component of every qualified name goes through.
            if module:
                name = f"{name}@{module}"
            tags = self.abi_tags() if reader.peek() == "B" else ""
            return builder.name(self._befriended(name + tags) if friend else name + tags)

        if char == "L":
            # At most one `L`: `<unqualified-name> ::= [<module-name>] [L] <name body> [<abi-tags>]`,
            # and both references refuse `_Z1fLL1A`.
            if internal:
                raise ParseError(self._mangled, reader.pos, "a second internal-linkage marker")
            reader.take()
            depth = self._depth = self._depth + 1
            if depth > self._max_depth:
                raise LimitExceeded(self._mangled, "recursion depth", self._max_depth)
            try:
                inner = self.unqualified_name(scope, module, internal=True)
            finally:
                self._depth = depth - 1
            return builder.name(self._befriended(builder.spell(inner))) if friend else inner

        if char == "C":
            # A constructor can be a friend too: `_ZN1AFC1Ev`.
            self._component_has_no_base_name = True
            name = self.constructor_name(scope)
            return builder.name(self._befriended(builder.spell(name))) if friend else name

        if char == "D":
            following = reader.ahead(1)
            if following in DESTRUCTOR_KINDS:
                reader.pos += 2
                self._ctor_dtor = True
                # The bare class name, with no module attached; see `constructor_name`.
                spelled = "~" + self.enclosing_class_name(scope)
                # `<ctor-dtor-name> [<abi-tags>]`: libc++ 18 tags destructors, `D2B8ne180100`.
                if reader.peek() == "B":
                    spelled += self.abi_tags()
                self._component_has_no_base_name = True
                return builder.name(self._befriended(spelled) if friend else spelled)
            if following == "C":
                # A structured binding declaration: DC <source-name>+ E
                reader.pos += 2
                names = []
                while not reader.eat("E"):
                    if reader.eof:
                        raise ParseError(self._mangled, reader.pos, "unterminated structured binding")
                    names.append(self.source_name())
                if not names:
                    # `<source-name>+`: both references refuse `DCE`.
                    raise ParseError(self._mangled, reader.pos, "a structured binding with no names")
                self._component_has_no_base_name = True
                spelled = self._in_module("[" + ", ".join(names) + "]", module)
                return builder.name(self._befriended(spelled) if friend else spelled)

        if char == "U":
            name = self.unnamed_type_name()
            # Set after the closure's signature, whose parameter types may clear it
            # (`_ZN1AUlN1XEE_D1Ev`).
            self._component_has_no_base_name = True
            return builder.name(self._befriended(builder.spell(name))) if friend else name

        # A vendor extended operator, `v <digit> <source-name>`, is a
        # `ConversionOperatorType` to `llvm-cxxfilt`, and a literal operator has no base
        # name either; see `enclosing_class_name`.
        code = reader.peek2()
        has_no_base_name = code in ("cv", "li") or (code[:1] == "v" and code[1:2].isdigit())
        spelled = self._in_module(self.operator_name(), module) + self.abi_tags()
        # Assigned after the operator is read: its type may be a class name, whose reading clears
        # the flag (`_ZN1Scv1AC2Ev` is `S::operator A::()` to llvm-cxxfilt).
        # `tools/mutate.py --seed 55`.
        self._component_has_no_base_name = has_no_base_name
        operator = builder.name(self._befriended(spelled) if friend else spelled)
        if reader.peek() != "I":
            # Nothing will bind a conversion operator's template parameters here; cleared because the
            # next argument list in the name is somebody else's.
            self._pending_conversion = None
            if self._conversion_unbound:
                # Both references refuse `_Zcv1BIRT_E`, whose parameter nothing binds. See
                # `bind_template_param`.
                raise ParseError(
                    self._mangled, reader.pos, "a conversion operator's template parameter with no arguments"
                )
        return operator

    def _befriended(self, text):
        """`text`, marked as a friend declared inside the class it is a friend of.

        The two references put the marker in different places. llvm-cxxfilt writes the
        word before the name, `A::friend f()`; GNU c++filt writes a bracketed suffix
        after the name and after its ABI tags, `A::f[abi:xyz][friend]()`, and before the
        template arguments, which attach to this component afterwards -- so
        `A::f[friend]<int>()`.
        """
        return text + "[friend]" if self.options.gnu_friend_spelling else "friend " + text

    def constructor_name(self, scope):
        """A constructor name.

        ```
        <ctor-dtor-name> ::= C1 | C2 | C3 | CI1 <base class type> | CI2 <base class type>
        ```

        The class's bare name, with no module attached: `_ZNW4llvm6ModuleC1Ev` is
        `Module@llvm::Module()` to both references -- `CtorDtorName` prints the scope's
        `getBaseName()`, and a `ModuleEntity`'s base name is the name inside it -- where
        this wrote `Module@llvm::Module@llvm()`. The destructor the same. Found by
        mutating real symbols.
        """
        reader = self.reader
        reader.expect("C")
        self._ctor_dtor = True
        if reader.eat("I"):
            # `CI1` to `CI5` only, the variants an ordinary constructor has: `_ZN1BCIT1AEi` is
            # refused.
            marker = reader.take()
            if marker not in CONSTRUCTOR_KINDS:
                raise ParseError(self._mangled, reader.pos, f"unknown constructor variant {marker!r}")
            # Read off the scope before the base type, a <type> whose own nested name would
            # otherwise stand where the class should.
            spelled = self.enclosing_class_name(scope)
            # The base type is a 5.1.10 candidate that g++ 13.3 enters (`_ZN1DCI21CENS0_4KindES1_`)
            # and clang++ 18.1.3 does not. Clang's rule first, g++'s on retry; see
            # `ItaniumOptions.inherited_constructor_substitution`.
            self._inherited_base_seen = True
            mark = self.subs.mark()
            self.type_()
            if not self._inherited_base_substitutes:
                self.subs.drop_last(mark)
            if reader.peek() == "B":
                spelled += self.abi_tags()
            return self.builder.name(spelled)
        marker = reader.take()
        if marker not in CONSTRUCTOR_KINDS:
            raise ParseError(self._mangled, reader.pos, f"unknown constructor variant {marker!r}")
        return self.builder.name(self.enclosing_class_name(scope) + self.abi_tags())

    def enclosing_class_name(self, scope):
        """The bare class name that a constructor or destructor repeats.

        `Foo::Foo` is encoded as "the class, then a constructor marker" -- the marker
        has no spelling of its own, so the name is read back off the scope we are
        standing in. A specialisation's constructor drops the arguments: the constructor
        of `Foo<int>` is spelled `Foo<int>::Foo`, not `Foo<int>::Foo<int>`.
        """
        if not scope:
            raise ParseError(self._mangled, self.reader.pos, "constructor outside any class scope")
        bare = self._prefix_bare
        if bare is not None and self._prefix_bare_has_no_base_name:
            # No base name (conversion, vendor or literal operator, structor, closure, unnamed type,
            # structured binding): llvm-cxxfilt's `CtorDtorName` prints nothing, `A::operator int::~()`
            # for `_ZN1AcviD0Ev`; libiberty uses the last <source-name> read.
            return self._last_source_name if self.options.gnu_closure_spelling else ""
        if bare is not None:
            # The component before its template arguments; cutting the spelling at `<` breaks
            # operator-named classes (`_ZNssC1Ev`).
            spelled = _ABI_TAGS_AT_END.sub("", self.builder.spell(bare))
        else:
            # A scope not from an <unqualified-name> (substitution, `<template-param>`, `<decltype>`):
            # the arguments and tags come off the spelling, which has no `<` or `[` of its own.
            spelled = self.builder.spell(scope[-1])
            cut = min((index for index in (spelled.find("<"), spelled.find("[")) if index > 0), default=-1)
            if cut > 0:
                spelled = spelled[:cut]
        # An abbreviation is one part (`Sa` is `std::allocator`), so only its tail is the class
        # name.
        separator = spelled.rfind("::")
        spelled = spelled[separator + 2 :] if separator >= 0 else spelled
        # The module comes off too: `_ZNW4llvm6ModuleC1Ev` is `Module@llvm::Module()` to both
        # references.
        module = spelled.find("@")
        spelled = spelled[:module] if module > 0 else spelled
        # Both references drop the friend marker from the repeated name: `_ZN1AF3fooC1Ev` is
        # `A::friend foo::foo()`. `tools/mutate.py --seed 15`.
        if self.options.gnu_friend_spelling:
            if spelled.endswith("[friend]"):
                spelled = spelled[: -len("[friend]")]
        elif spelled.startswith("friend "):
            spelled = spelled[len("friend ") :]
        return spelled

    def source_name(self):
        """<source-name> ::= <positive length number> <identifier>"""
        text = self.plain_source_name()
        return text + self.abi_tags() if self.reader.peek() == "B" else text

    def plain_source_name(self):
        """A <source-name> without the ABI tags that may follow it.

        Split out because a module name goes *between* the two: `3FooB3ABI` inside
        module `MOD` is `Foo@MOD[abi:ABI]`, not `Foo[abi:ABI]@MOD`.
        """
        # `Reader.length_prefixed` inlined, errors and all: this runs for nearly every
        # component of every name.
        reader = self.reader
        mangled, end = reader.text, reader.length
        start = pos = reader.pos
        while pos < end and mangled[pos] in DIGITS:
            pos += 1
            if pos - start > MAX_NUMBER_DIGITS:
                raise ParseError(mangled, start, "number too long")
        if pos == start:
            raise ParseError(mangled, start, "expected a number")
        if mangled[start] == "0":
            reader.padded_length = True
        length = int(mangled[start:pos])
        stop = pos + length
        if stop > end:
            raise TruncatedError(mangled, pos)
        reader.pos = stop
        text = mangled[pos:stop]
        self._last_source_name = text
        if length <= 0:
            raise ParseError(self._mangled, stop, "source name of non-positive length")
        if text[0] == "_" and text.startswith("_GLOBAL_"):
            if text.startswith("_GLOBAL__N"):
                return "(anonymous namespace)"
            if self.options.gnu_expression_spelling and len(text) >= 10 and text[8] in "._$" and text[9] == "N":
                # `d_source_name` takes any of the three markers assemblers have used
                # between `_GLOBAL_` and the `N` -- `_`, `.` and `$` -- where llvm-cxxfilt
                # knows the underscore alone and prints the others as written.
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
            # Strong reference plus identity, as for packs.
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
            # A generic lambda's declared parameters replace the enclosing `T_` scope: by ABI 5.1.8 an
            # undeclared `T_` in its signature is an `auto`, not the enclosing template's argument.
            declarations = []
            saved_counts = self._parameter_counts
            saved_scope = self.targs.snapshot()
            self._parameter_counts = {}
            if self._naming:
                # A lambda that is the named entity starts from nothing. One inside a type or expression
                # keeps the levels around it with its own on top, so `TL0__` reaches the lambda while `T_`
                # reaches the enclosing function's arguments.
                self.targs.clear()
            declared = []
            # The level the lambda's own parameters occupy, and the only one a reference
            # that binds nothing may be an `auto` at. See `bind_template_param`.
            was_closure_level = self._closure_level
            self._closure_level = self.targs.depth()
            self.targs.push(declared)
            constraint = ""
            saw_pack = False
            was_reading_closure = self._reading_closure_signature
            self._reading_closure_signature = True
            try:
                while reader.peek2() in _PARAMETER_DECLARATIONS:
                    # A pack must be last; `c++filt` 2.42 stops the list at `Tp Ty Ty` rather than refuse.
                    # Kept here, so a `TL0_<n>_` can still name it. See `_declaration_after_a_pack`.
                    if reader.peek2() == "Tp":
                        saw_pack = True
                    elif saw_pack:
                        self._declaration_after_a_pack = True
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
                        if not parameters:
                            raise ParseError(self._mangled, reader.pos, "a lambda signature with no parameter types")
                        trailing = self.builder.spell(self.constraint_expression())
                        reader.expect("E")
                        return self._closure(
                            declarations, constraint, parameters, f" requires {trailing}", lambda_expression
                        )
                    parameters.append(self.builder.spell(self.type_()))
                if not parameters:
                    # `<parameter type>+`: a lambda taking nothing is written `v`; both references refuse
                    # `UlE_`.
                    raise ParseError(self._mangled, reader.pos, "a lambda signature with no parameter types")
            finally:
                self._reading_closure_signature = was_reading_closure
                self._closure_level = was_closure_level
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
            # The lambda expression, not the closure; the discriminator has no spelling in it.
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
            # The type may use the operator's own template parameters, written after it; position
            # and table mark are kept for `_reread_conversion`. No return type is encoded.
            reader.pos += 2
            start = reader.pos
            mark = self.subs.mark()
            was_trying = self._try_template_args
            was_reading = self._reading_conversion_type
            self._try_template_args = False
            # The one reading that may resolve a template parameter with nothing in scope;
            # `_reread_conversion` redoes it once the arguments are. See `bind_template_param`.
            self._reading_conversion_type = True
            self._conversion_unbound = False
            try:
                spelled = "operator " + self.builder.spell(self.type_())
            finally:
                self._reading_conversion_type = was_reading
                self._try_template_args = was_trying
            self._pending_conversion = (start, mark)
            # Only the entity's own name suppresses a return type, not a conversion operator inside
            # an expression (`&A::operator int` as a template argument).
            if self._naming:
                self._no_return_type = True
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
        if following[:1] == "L":
            # An `L` between scope and structor: llvm-cxxfilt reads `_ZNSiLD1Ev` like `_ZNSiD1Ev`;
            # c++filt refuses it. `tools/enumerate.py --length 6`.
            following = reader.ahead2(3)
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
        text, end = reader.text, reader.length
        pos = reader.pos
        if pos >= end or text[pos] != "S":
            raise ParseError(text, pos, "expected 'S'")
        pos += 1
        reader.pos = pos
        code = "S" + text[pos] if pos < end else "S"
        table = self._abbrev_expanded if expanded else self._abbrev
        self._last_entry_index = None
        if code in table:
            reader.pos = pos + 1
            tags = self.abi_tags() if pos + 1 < end and text[pos + 1] == "B" else ""
            if tags:
                # 5.1.2: an abbreviation carrying ABI tags is substitutable as the tagged whole; the bare
                # abbreviation is not.
                return self.subs.remember(self.builder.raw(table[code] + tags), "type")
            return self.builder.raw(table[code])
        index = reader.seq_id()
        if index >= self._template_template_param_floor:
            self._template_template_shifted = True
        entries = self.subs._entries
        entry = entries[index] if index < len(entries) else self.subs.lookup(index)
        kind = type(entry)
        try:
            if kind is ParameterReference:
                # The entry is the parameter, not its binding where recorded: the back-reference may be
                # read under a different scope. `bind_template_param` does the pack handling, so
                # `_pack_aware` is not applied twice. See `ParameterReference`.
                try:
                    return self._note_pack_binding(self.bind_template_param(entry.index, entry.level))
                except ParseError:
                    if entry.symbolic is None:
                        raise
                    return self.builder.raw(entry.symbolic)
            if kind is DeferredProduction:
                # The same, for a component built *over* a parameter. See
                # `DeferredProduction`.
                return self._pack_aware(self._reread(index, entry))
            if self.builder.members(entry) is None and id(entry) not in self._expansion_handles:
                # `_pack_aware`'s answer for anything that is not a pack.
                return entry
            return self._pack_aware(entry)
        finally:
            # Set on the way out: re-reading a deferred production resolves back-references through
            # this method too, and the caller asks about its own.
            self._last_entry_index = index

    def _note_pack_binding(self, bound):
        """Record that a back-reference named a `<template-param>` entry holding a pack.

        Only where the pack is still a pack: inside an expansion `bind_template_param`
        has already picked the member being read, and that reading is not in question.
        See `_pack_named_through_a_back_reference`.
        """
        if self.builder.members(bound) is not None:
            self._pack_named_through_a_back_reference = True
        return bound

    def _expansion(self, handle):
        """Record `handle` as the result of a `Dp`, and return it."""
        self._expansion_handles[id(handle)] = handle
        return handle

    def _over_a_pack(self, inner):
        """`inner`, the operand a type modifier is about to wrap -- unless it is an
        empty parameter pack standing outside an expansion, which no modifier can wrap.

        `_Z1fIJEPT_E` writes `P` over `T_`, and `T_` is the empty pack `J E`. There is
        nothing to point to: `c++filt` refuses the name; `llvm-cxxfilt` prints `f<*>`,
        the modifier alone; this printed `f<>`, the argument dropped as an empty pack is
        dropped, which reads as a name with one argument fewer than it has. The same
        for a reference, a qualifier, an array, a member pointer, a complex. An
        expansion over an empty pack is a different thing -- `DpT_` spells nothing on
        purpose -- and is left alone. Found by `tools/enumerate.py --length 6`.
        """
        if self._reading_pattern:
            return inner
        members = self.builder.members(inner)
        if members is not None and not members and id(inner) not in self._expansion_handles:
            raise ParseError(self._mangled, self.reader.pos, "a type built over an empty parameter pack")
        return inner

    def _pack_aware(self, handle):
        """Report a pack, and stand in for one of its members while one is being read.

        A pack reaches a pattern as a `<template-param>` most of the time, but it can
        also arrive as a back-reference -- `Dp N S3_ 4type E` names its members through
        `S3_` -- and an expansion has to range over it either way.

        An entry that *is* an expansion is not a pack to range over: it spells its
        members and is otherwise a type like any other, so `Dp S1_` over the entry
        `DpT_` made is `int, char...` -- the child, then the dots, as
        `ParameterPackExpansion::printLeft` writes it after the inner expansion has
        restored the pack context.
        """
        if id(handle) in self._expansion_handles:
            return handle
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
        if members and not self._reading_pattern:
            self._bare_pack_used = True
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

        # `TL <level-1> _` names the level; a bare `T` is level 0, the innermost <template-args>.
        # Higher levels are what generic lambdas and template template parameters declare.
        # Only `L` may follow here: `Tp` and `Ts` are other productions, and both references
        # refuse `_Z1fIiEvTp_`.
        level = 0
        if reader.eat("L"):
            level = int(reader.digits()) + 1
            reader.expect("_")
        index = 0 if reader.peek() == "_" else reader.integer(allow_negative=False) + 1
        reader.expect("_")

        if self._in_constraint:
            # Inside a requires-clause a parameter is spelled by its own mangled name (`T_` is `T`,
            # `TL0__` is `TL0_`) rather than by its binding.
            symbolic = reader.text[begin : reader.pos - 1]
            if self.options.symbolic_constraint_parameters:
                return self.builder.raw(symbolic), None
            # GNU substitutes the binding, but a clause may name an enclosing template's parameter
            # with nothing bound (`TL0__` in `Q` on a member of `A<int>`). The style must not decide
            # whether a name parses, so that falls back to the symbolic spelling, and the recorded
            # reference carries it for a later `S_`. See `substitution`.
            uses = self._parameter_uses
            try:
                bound = self.bind_template_param(index, level)
            except ParseError:
                bound = self.builder.raw(symbolic)
                reference = None
            else:
                reference = ParameterReference(index, level, symbolic)
            # Put back either way, so a production inside a clause is not deferred; a deferred one
            # re-read under the signature's scope spelled `typename 1234`.
            self._parameter_uses = uses
            return bound, reference
        return self.bind_template_param(index, level), ParameterReference(index, level)

    def bind_template_param(self, index, level=0):
        """What `TL<level>_<index>_` names under the arguments currently in scope."""
        reader = self.reader
        if self._in_special_name:
            # Both references refuse `_ZTVN1AIcT_EE`; compilers write `_ZTVN1AIccEE`.
            # `tools/mutate.py --seed 12`.
            raise ParseError(self._mangled, reader.pos, "a template parameter in a special name")
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
                    if members and not self._reading_pattern:
                        self._bare_pack_used = True
            return bound

        if self._reject_unbound_parameters:
            # A bare `<type>` can never bind this parameter; `c++filt -t` and `__cxa_demangle`
            # refuse it.
            raise ParseError(self._mangled, reader.pos, "template parameter with nothing to bind it")

        if level and level >= self.targs.depth():
            # A level not in scope at all (`TL8_1_`): the reference refuses it. Inside a
            # requires-clause it took the symbolic branch above.
            raise ParseError(self._mangled, reader.pos, f"no template parameter level {level} in scope")

        if not (self._reading_closure_signature or self._reading_conversion_type):
            # No <template-args> in scope, and none will come: both references refuse `_Z1f1AT_`.
            # The two readings that legitimately find nothing bound (a generic lambda's level, a
            # conversion operator's type) are excluded above.
            raise ParseError(self._mangled, reader.pos, "template parameter with no arguments in scope")
        if self._reading_conversion_type and not self._reading_closure_signature:
            # Provisional: `operator_name` refuses the name if no arguments follow.
            self._conversion_unbound = True
        elif level != self._closure_level:
            # A lambda's `auto` is a parameter of its own level (ABI 5.1.8): llvm-cxxfilt refuses
            # `_Z1fIEvDTLUlT_E_EE` and `_Z1fIiEvDTLUlT0_E_EE`.
            raise ParseError(
                self._mangled, reader.pos, "template parameter with no argument at a level that is not the lambda's"
            )

        # A generic lambda's `auto` (ABI 5.1.8), or a conversion operator's type read before its
        # arguments. llvm-cxxfilt spells `auto`; GNU numbers by parameter index, so
        # `Ul T0_ T_ E` is `(auto:2, auto:1)`.
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
        expression = self._bracketed_expression()
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
        text, end = reader.text, reader.length
        pos = reader.pos
        mask = 0
        if pos < end and text[pos] == "r":
            mask = 1
            pos += 1
        if pos < end and text[pos] == "V":
            mask |= 2
            pos += 1
        if pos < end and text[pos] == "K":
            mask |= 4
            pos += 1
        reader.pos = pos
        return CV_COMBINATIONS[mask]

    def type_(self):
        self._productions += 1
        reader = self.reader
        pos = reader.pos
        char = reader.text[pos] if pos < reader.length else ""
        if char:
            builtin = BUILTIN_TYPES.get(char)
            if builtin is not None and self._depth < self._max_depth:
                # `_type`'s first arm, without the frame: a builtin records nothing and binds nothing.
                reader.pos = pos + 1
                # A builtin renders as its spelling, so its size is that spelling's length.
                if len(builtin) > self._max_output:
                    raise LimitExceeded(self._mangled, "output length", self._max_output)
                return self.builder.builtin(builtin)
        depth = self._depth = self._depth + 1
        if depth > self._max_depth:
            raise LimitExceeded(self._mangled, "recursion depth", self._max_depth)
        subs = self.subs
        start = pos
        uses, entries = self._parameter_uses, len(subs._entries)
        try:
            if char == "N":
                # `_type`'s commonest arm, without the frame.
                result = self.nested_name(True)[0]
                if subs.recording:
                    recorded = subs._entries
                    if len(recorded) >= subs._limit:
                        raise LimitExceeded(self._mangled, "substitution", subs._limit)
                    recorded.append(result)
            else:
                result = self._type(char)
            # Checked on every type: `M S_ S_` doubles, so a few hundred bytes can describe
            # gigabytes. `size()` is O(1).
            if self._size(result) > self._max_output:
                raise LimitExceeded(self._mangled, "output length", self._max_output)
            # A template parameter was resolved here, so this production's entry (the last one) must
            # be re-read under a later back-reference's scope; see `DeferredProduction`. The tests
            # are ordered by how often each is false.
            if (
                self._parameter_uses != uses
                and subs.recording
                and len(subs._entries) > entries
                and type(subs.last) is not ParameterReference
            ):
                subs.defer_last(start, reader.pos)
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
        # Not memoised inside a pack expansion: `Dp` re-reads per member under the same scope,
        # which would get the first member's answer each time.
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

    def _type(self, char):
        """The `<type>` opening with `char`, which `type_` has already read at the cursor."""
        reader = self.reader
        builder = self.builder
        subs = self.subs

        # A <builtin-type> and a <nested-name> never reach here: `type_` reads them. Arms ordered
        # by how often each is taken over the Itanium symbols of Ubuntu 24.04.
        if char in _CLASS_ENUM_START:
            return subs.remember(self.class_enum_type(), "type")

        if char == "S":
            if reader.ahead(1) == "t":
                # `St <unqualified-name>` is an <unscoped-name>, not a bare
                # abbreviation: the name that follows belongs to it.
                return subs.remember(self.class_enum_type(), "type")
            component = self.substitution()
            index = self._last_entry_index
            as_template_argument = self._template_name_argument
            self._template_name_argument = False
            if index is not None and (
                index in self._closure_prefix_entries
                or (index in self._template_name_entries and reader.peek() != "I" and not as_template_argument)
            ):
                # A <substitution> is a <type> only through <class-enum-type>, so a closure prefix or
                # a bare template (outside `template_arg`) cannot stand here. Reaching one means GCC 12's
                # numbering, which `parse` retries, or not a name.
                raise SubstitutionMisuse(
                    self._mangled, reader.pos, f"substitution S{index}_ names nothing a type can be"
                )
            named = self._module_names.get(id(component)) if self._module_names else None
            if named is not None:
                # `S1_ 1A` is `A@FOO.BAR`: the pair is a <type> candidate; the module entry alone is
                # not one a later `S<n>_` can mean.
                component = subs.remember(self.unqualified_name(module=named), "type")
            if reader.peek() == "I":
                arguments = self.template_arguments()
                return subs.remember(builder.template(component, arguments, not self._trailing_empty_pack), "type")
            return component

        if char == "R":
            reader.pos += 1
            return subs.remember(builder.reference(self._over_a_pack(self.type_())), "type")
        if char == "P":
            reader.pos += 1
            inner = self._over_a_pack(self.type_())
            protocol = self._objc_protocols.get(id(inner)) if self._objc_protocols else None
            if protocol is not None:
                # A pointer to protocol-qualified `objc_object` is `id<A>`; a second pointer is
                # ordinary.
                return subs.remember(builder.raw(f"id<{protocol}>"), "type")
            return subs.remember(builder.pointer(inner), "type")
        if char in QUALIFIER_LETTERS:
            if self._at_function_type():
                # 5.1.5.3: <function-type> ::= [<CV-qualifiers>] [<exception-spec>] [Dx]
                # F [Y] <bare-function-type> [<ref-qualifier>] E. `KFbvE` is one component; recording
                # the unqualified type too would shift every later index.
                return self.function_type_production()
            qualifiers = self.cv_qualifiers()
            inner = self.type_()
            return subs.remember(builder.qualify(self._over_a_pack(inner), qualifiers), "type")

        if char == "T":
            following = reader.ahead(1)
            if following in _ELABORATED_KEYWORDS:
                # <class-enum-type> ::= Ts <name> | Tu <name> | Te <name>
                #
                # A dependent type spelled out: `PTsNT_5InnerE` is `struct T::Inner*`, `Ts3Foo` is
                # `struct Foo`.
                keyword = _ELABORATED_KEYWORDS[following]
                reader.pos += 2
                return subs.remember(builder.raw(f"{keyword} {builder.spell(self.class_enum_type())}"), "type")

            component, reference = self.template_param_binding()
            recorded = reference if reference is not None else component
            if reader.peek() == "I" and self._try_template_args:
                # <template-template-param> <template-args>: the parameter and the application are
                # both 5.1.10 candidates. g++ 13.3 and clang++ 18.1.3 both emit `...T_IT0_Li3EES5_`,
                # whose `S5_` needs both.
                subs.remember(recorded, "template-template-param")
                self._note_template_template_param()
                arguments = self.template_arguments()
                return subs.remember(builder.template(component, arguments, not self._trailing_empty_pack), "type")
            # A <template-param> reached through <type> is a <type>, and <type> is a
            # candidate. Confirmed by `_ZSt4sortIPiEvT_S1_`, where `S1_` resolves to
            # `int*` -- the entry the `T_` parameter itself contributed.
            subs.remember(recorded, "type")
            return component

        if char == "O":
            reader.pos += 1
            return subs.remember(builder.rvalue_reference(self._over_a_pack(self.type_())), "type")
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
            # The one builtin that is a candidate (5.1.10): `_Z1fu3fooS_` is `f(foo, foo)`.
            reader.pos += 1
            spelled = self.source_name()
            if reader.eat("I"):
                # Compilers emit a transformation over one type, `u <source-name> I <type> E`, which
                # llvm-cxxfilt reads (refusing `u7__decayIllE`); c++filt 2.42 refuses the form.
                spelled = f"{spelled}({builder.spell(self.type_())})"
                reader.expect("E")
            return subs.remember(builder.raw(spelled), "type")

        if char == "C" or char == "G":
            # C99 `_Complex`/`_Imaginary`, applied from the right as both references do (`PCd` is a
            # pointer to complex double); the style picks the word.
            reader.pos += 1
            qualifier = _COMPLEX_WORDS[self.options.gnu_complex_spelling][char]
            # Not a cv-qualifier, so a repeat does not collapse: `c++filt` writes
            # `signed char _Imaginary _Imaginary` for `_Z1fGGa`.
            return subs.remember(builder.qualify(self._over_a_pack(self.type_()), (qualifier,), cv=False), "type")

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
                # A candidate, unlike other builtins, since it carries a width: `_Z6myfuncRDB8_S0_` is
                # `myfunc(_BitInt(8)&, _BitInt(8)&)`.
                width = reader.digits() if reader.peek() in DIGITS else self.expression_text()
                reader.expect("_")
                return self.subs.remember(builder.raw(f"{EXTENDED_BUILTIN_TYPES[pair]}({width})"), "type")
            if pair == "Dn" and self.options.gnu_nullptr_spelling:
                return builder.builtin("decltype(nullptr)")
            if self._auto_substitutes and pair in _UNDEDUCED_AUTO:
                # Apple's clang counts an undeduced `auto` among the candidates, as
                # Clang 6.0 did; nothing else does. See
                # `ItaniumOptions.undeduced_auto_substitution`.
                return self.subs.remember(builder.builtin(EXTENDED_BUILTIN_TYPES[pair]), "type")
            return builder.builtin(EXTENDED_BUILTIN_TYPES[pair])

        if pair == "DF":
            # `DF <n> _` is `_FloatN`, `DF <n> x` is `_FloatNx` (no `_` after the `x`), and `DF16b`
            # is `std::bfloat16_t`.
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
            first = reader.peek()
            mark = self.subs.mark()
            outer_empty = self._saw_empty_pack
            outer_pack = self._saw_pack
            outer_arity = self._pack_arity
            self._saw_empty_pack = False
            self._saw_pack = False
            self._pack_arity = None
            self._reading_pattern += 1
            try:
                inner = self.type_()
                over_empty = self._saw_empty_pack
                over_pack = self._saw_pack
                arity = self._pack_arity
            finally:
                self._reading_pattern -= 1
                self._saw_empty_pack = outer_empty
                # An expansion consumes its pack (`ParameterPackExpansion::printLeft`), so an outer
                # pattern reaching a pack only through it takes the dots: `DpPFvDpT_E` is
                # `void (*)(int, char)...`.
                self._saw_pack = outer_pack
                self._pack_arity = outer_arity
            if over_empty:
                # Over an empty pack the pattern expands to nothing, as both references print (libstdc++'s
                # `std::async`); it is still a <type> and enters the table.
                return self.subs.remember(self._expansion(builder.parameter_pack([])), "type")
            if over_pack and arity and len(builder.members(inner) or ()) != arity:
                # A pattern more than a declarator round the parameter is re-read per member:
                # `Dp unary<T_>` over `{int, float}` is `unary<int>, unary<float>`.
                return self.subs.remember(self._expansion(self._expand_pattern(start, mark, arity)), "type")
            if id(inner) in self._pack_ids or over_pack:
                # The expansion is a <type> recorded separately from its pattern (`Dp R T1_` gives two
                # entries), as both references do. `inner`'s members are already spelled, so no ellipsis;
                # and it gets a fresh handle, as an expansion differs from the pack a `T_` entry is.
                return self.subs.remember(self._expansion(builder.parameter_pack(builder.members(inner))), "type")
            # No pack in the pattern: an unexpanded expansion, printed with `...` as
            # `ParameterPackExpansion::printLeft` does (`_Z1fIJifcEEvDpC1E` is `E complex...`). GNU
            # brackets anything but a name, so `Dp i` is `(int)...`; builtins and template parameters
            # are excluded by opening character, as they spell as one identifier too.
            if self.options.gnu_expression_spelling and not (
                first not in BUILTIN_TYPES
                and first != "D"
                and first != "T"
                and _PLAIN_CALLEE.match(builder.spell(inner)) is not None
            ):
                inner = builder.expression("paren", ["(", inner, ")"])
            return self.subs.remember(builder.pack(inner), "type")

        if pair in ("Dk", "DK"):
            # <type> ::= Dk <type-constraint>   # `C auto`
            #          | DK <type-constraint>   # `C decltype(auto)`
            #
            # A <type>, so a candidate; llvm-cxxfilt 18 does not record it. See
            # `_constrained_placeholder_recorded`.
            placeholder = "auto" if pair == "Dk" else "decltype(auto)"
            reader.pos += 2
            constraint = builder.spell(self.name()[0])
            self._constrained_placeholder_recorded = True
            return self.subs.remember(builder.raw(f"{constraint} {placeholder}"), "type")

        if pair == "Dy":
            # <type> ::= Dy <pack> <index> -- C++26 pack indexing.
            reader.pos += 2
            pattern = self.type_()
            index = self.expression()
            return self.subs.remember(builder.raw(f"({builder.spell(pattern)})[{builder.spell(index)}]"), "type")

        if pair == "Dv":
            return self.subs.remember(self.vector_type(), "type")

        # <function-type> ::= [<CV-qualifiers>] [<exception-spec>] [Dx] F ... E: the spec opens a
        # function type, so it spells `void (*)() noexcept`, not `void () noexcept*`.
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
        """<type> ::= Dv <number> _ <type> | Dv _ <expression> _ <type>

        And two shapes the ABI text does not have. Clang writes a dependent size with
        no underscore before it, `Dv <expression> _ <type>` -- `mangleType` for a
        `DependentSizedExtVectorType` is `Out << "Dv"; mangleExpression(Size); Out <<
        '_'` -- which is what `llvm-cxxfilt` reads and `c++filt` refuses; the form with
        the underscore is what `c++filt` reads and `llvm-cxxfilt` refuses. Both are
        read. And AltiVec's `__vector pixel` is `Dv <number> _ p`, a `p` where the
        element type would be; `Dv4_b` is `__vector bool` to the mangler and `bool` to
        every demangler, so it is left as the type letter it also is.
        """
        reader = self.reader
        reader.expect("Dv")
        if reader.eat("_"):
            size = self.expression_text()
        elif reader.peek() in DIGITS:
            size = reader.digits()
        else:
            size = self.expression_text()
        reader.expect("_")
        if reader.eat("p"):
            # Both references refuse `_Z1hDv0_p`, a zero-length AltiVec pixel vector. `Dv0_i` is
            # still read, as c++filt prints `__vector(0)`. `tools/mutate.py --seed 10`.
            if size.isdigit() and int(size) == 0:
                raise ParseError(self._mangled, reader.pos, "a pixel vector of dimension 0")
            spelled = "pixel"
        else:
            inner = self.type_()
            spelled = self.builder.spell(inner)
        if self.options.gnu_vector_spelling:
            # c++filt's `d_vector_type` prints the dimension's value: `Dv07_b` is `__vector(7)`.
            return self.builder.raw(f"{spelled} __vector({int(size) if size.isdigit() else size})")
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
        text, end = reader.text, reader.length
        at = reader.pos
        for letter in ("r", "V", "K"):
            if at < end and text[at] == letter:
                at += 1
        if at >= end:
            return False
        char = text[at]
        if char == "D":
            return at + 1 < end and text[at + 1] in "oOwx"
        return char == "F"

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
            if qualifier.startswith(_OBJC_PROTOCOL) and not self.options.gnu_objc_protocol_spelling:
                # `U <n>objcproto<protocol> <type>`: llvm-cxxfilt writes `NSArray<A>`, c++filt
                # `NSArray objcproto1A` (see `gnu_objc_protocol_spelling`); a qualified `objc_object`
                # becomes `id<A>` in the `P` branch. The protocol is length-prefixed within the
                # qualifier, and `parseQualifiedType` refuses a short one, `objcproto15`.
                protocol = qualifier[len(_OBJC_PROTOCOL) :]
                digits = 0
                while digits < len(protocol) and protocol[digits] in DIGITS:
                    digits += 1
                length = int(protocol[:digits]) if digits else 0
                if not length or digits + length > len(protocol):
                    raise ParseError(self._mangled, reader.pos, "an Objective-C protocol that is not a source name")
                protocol = protocol[digits : digits + length]
                spelled = builder.spell(inner)
                handle = builder.raw(f"{spelled}<{protocol}>")
                if spelled == _OBJC_OBJECT:
                    # `objc_object<A>` alone; the first pointer makes it `id<A>`, and further ones are
                    # ordinary: `id<A>*`.
                    self._objc_ids.append(handle)
                    self._objc_protocols[id(handle)] = protocol
                return handle
            return builder.vendor_qualify(self._over_a_pack(inner), qualifier)
        if self._at_function_type():
            # A cv-qualified function type is its own production either way round.
            return self.type_()
        qualifiers = self.cv_qualifiers()
        inner = self.type_()
        if qualifiers:
            return builder.qualify(self._over_a_pack(inner), qualifiers)
        return inner

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
                # An empty pack expansion contributes no type.
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
        #: One flag per kept parameter: was it written `v`, rather than merely spelling
        #: `void` after a substitution?
        written_void = []
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
            wrote_void = reader.peek() == "v"
            parameter = self.type_()
            if not _drops_out(builder, parameter):
                parameters.append(parameter)
                written_void.append(wrote_void)
            elif self.options.gnu_empty_pack_spelling:
                # See `bare_function_type`: an empty entry to c++filt, unless it ends
                # the list. It is not a `void` written.
                parameters.append(parameter)
                written_void.append(False)
        if self.options.gnu_empty_pack_spelling:
            while parameters and _drops_out(builder, parameters[-1]):
                parameters.pop()
                written_void.pop()

        if parameters and all(written_void):
            # llvm-cxxfilt drops every literal `void` parameter and c++filt keeps them; they agree
            # when all are literal. A `T_` bound to `void` is not one: `_Z1fIvEvPFvT_E` is
            # `void f<void>(void (*)(void))` to both.
            parameters = []
        written = "".join([f" {qualifier}" for qualifier in cv_qualifiers])
        if self.options.gnu_exception_spec_first:
            # c++filt writes the exception specification before the qualifiers,
            # `void (A::*)() noexcept const &`; llvm-cxxfilt writes it last.
            return builder.function(returns, parameters, exception_spec + written + suffix)
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
        return self.builder.array(self._over_a_pack(self.type_()), dimension)

    def member_pointer_type(self):
        """<pointer-to-member-type> ::= M <class type> <member type>"""
        reader = self.reader
        reader.expect("M")
        owner = self._over_a_pack(self.type_())
        member = self._over_a_pack(self.type_())
        return self.builder.member_pointer(owner, member)

    # -- 5.1.5.10 template arguments -------------------------------------------

    def template_param_decl(self, ellipsis="", params=None, named=True):
        """Guarded wrapper: `Tp` and `Tt` both recurse into this production."""
        depth = self._depth = self._depth + 1
        if depth > self._max_depth:
            raise LimitExceeded(self._mangled, "recursion depth", self._max_depth)
        try:
            return self._template_param_decl(ellipsis, params, named)
        finally:
            self._depth = depth - 1

    def _template_param_decl(self, ellipsis="", params=None, named=True):
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

        `named` is False for the declarations inside a `Tt`'s own list under the GNU
        style, which writes those without names -- `template<typename, int> class $TT0`.
        The name is still invented, because a `TL0_<n>_` elsewhere in the signature can
        refer to one; it just is not printed, and it does not advance the counter the
        printed names come from. See `_parameter_name`.

        Under the GNU style the whole production is spelled differently, and every part
        of that was read off `c++filt` 2.42 rather than guessed:

        * the name comes *after* the type rather than where a declarator goes, so `Tn
          PA3_i` is `int (*) [3] $N0` and not `int (*$N0) [3]` -- which is not a
          declaration, but it is what the reference writes;
        * a `Tp` puts its ellipsis on the type rather than on the name, `typename...
          $T0` and `int [3]... $N0`;
        * a `Tt` is spelled `class` rather than `typename`.
        """
        reader = self.reader
        pair = reader.peek2()
        reader.pos += 2
        gnu = self.options.gnu_closure_spelling

        if pair == "Ty":
            binding = self._declare("T", params, named)
            if gnu:
                return binding, f"typename{ellipsis}" + (f" {binding}" if named else "")
            return binding, f"typename {ellipsis}{binding}"
        if pair == "Tk":
            # A constrained parameter: the concept it must satisfy, then the parameter.
            # The concept's own arguments are constraint operands, and the reference
            # spells a parameter inside one by its own mangled name.
            outer = self._in_constraint
            outer_naming = self._naming
            self._in_constraint = True
            self._naming = False
            try:
                concept = self.builder.spell(self.name()[0])
            finally:
                self._in_constraint = outer
                self._naming = outer_naming
            binding = self._declare("T", params, named)
            if gnu:
                return binding, f"{concept}{ellipsis}" + (f" {binding}" if named else "")
            return binding, f"{concept} {ellipsis}{binding}"
        if pair == "Tn":
            # The name goes where a declarator goes, so a parameter of array-of-pointer
            # type is `$T0 (*$N) [3]` and not `$T0 (*) [3] $N`. The GNU style writes the
            # second; see the note above.
            kind = self.type_()
            binding = self._declare("N", params, named)
            if gnu:
                spelled = self.builder.spell(kind) + ellipsis
                return binding, spelled + (f" {binding}" if named else "")
            return binding, self.builder.spell(kind, f"{ellipsis}{binding}")
        if pair == "Tp":
            # A pack: the ellipsis goes right before the name, `$T0 (*...$N0) [3]`.
            return self.template_param_decl("...", params, named)
        if pair == "Tt":
            # Named before the inner list is read, so it belongs to the outer level:
            # `template<typename $T0, ...> typename $TT`.
            binding = self._declare("TT", params, named)
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
                    inner.append(self.template_param_decl(params=declared, named=not gnu)[1])
            finally:
                self.targs.pop()
            if gnu:
                head = f"template<{', '.join(inner)}> class{ellipsis}"
                return binding, head + (f" {binding}" if named else "")
            return binding, f"template<{', '.join(inner)}> typename {ellipsis}{binding}"
        raise ParseError(self._mangled, reader.pos, f"unknown template parameter declaration {pair!r}")

    def _declare(self, kind, params, named=True):
        """Invent a name for a declared parameter, and bind it into `params`."""
        binding = self._parameter_name(kind, named)
        if params is not None:
            params.append(self.builder.raw(binding))
        return binding

    def _parameter_name(self, kind, named=True):
        """The synthetic name for a declared parameter.

        llvm-cxxfilt leaves the first of each kind unsuffixed -- `$T`, `$T0`, `$T1`, and
        `$N` counts separately from `$T` -- while GNU c++filt numbers *every* declaration
        in one sequence from zero, so `Ty Ty Tn i` is `$T0, $T1, $N2` to it and
        `$T, $T0, $N` to llvm-cxxfilt. Verified over every ordering of the three kinds.

        A declaration GNU does not print -- one inside a `Tt`'s own list -- does not
        advance that sequence: `Ty Tt Ty E Ty` is `$T0, $TT1, $T2`, with nothing spent on
        the inner `typename`. Such a declaration still needs a name, because a
        `TL0_<n>_` can refer to one, so it takes the next of its own kind instead; what
        that name is cannot be read off the reference, which refuses every name that
        mentions one.
        """
        if self.options.gnu_closure_spelling and named:
            index = self._parameter_counts.get("", 0)
            self._parameter_counts[""] = index + 1
            return f"${kind}{index}"
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
        # `_scope_has_pack` describes this list; leaked outward, a pack inside an argument
        # (`f<std::tuple<int>>`) suppresses the enclosing `Dp`'s ellipsis.
        outer_has_pack = self._scope_has_pack
        self._scope_has_pack = False
        if install_scope:
            self.targs.install()
        # Arguments are mentioned in passing, not declared, so a nested list must not install
        # its scope: `__normal_iterator<wchar_t*, ...>` would replace a constructor's `T_`.
        was_naming = self._naming
        self._naming = False
        arguments = []
        empties = []
        keep_empty = self.options.gnu_empty_pack_spelling
        # "Preserve the last name we saw -- don't let the template arguments clobber
        # it", as `d_template_args_1` puts it: the constructor of `A<X>::{unnamed
        # type#1}` is `A`, not `X`.
        last_source_name = self._last_source_name
        trailing_empty_pack = False
        seen = False
        # A clause here is the entity's only when this list is the entity's own; c++filt refuses
        # one in a parameter's type.
        outer_constraint = self._argument_constraint
        self._argument_constraint = None
        text = reader.text
        try:
            while True:
                pos = reader.pos
                if pos >= reader.length:
                    raise ParseError(self._mangled, pos, "unterminated template argument list")
                char = text[pos]
                if char == "E":
                    reader.pos = pos + 1
                    break
                if char == "Q" and not seen:
                    # `I <template-arg>+ [Q <constraint>] E`: llvm-cxxfilt refuses
                    # `_ZN5test21jIQ4TrueITL0__EEEvz`.
                    raise ParseError(self._mangled, reader.pos, "a requires-clause with no template arguments")
                seen = True
                depth = self._depth = self._depth + 1
                if depth > self._max_depth:
                    raise LimitExceeded(self._mangled, "recursion depth", self._max_depth)
                try:
                    argument, is_empty_pack = self.template_arg()
                finally:
                    self._depth = depth - 1
                if argument is None:
                    # A <template-param-decl> or a requires-clause: neither prints nor
                    # occupies a slot.
                    continue
                trailing_empty_pack = is_empty_pack
                if not is_empty_pack or keep_empty:
                    arguments.append(argument)
                    empties.append(is_empty_pack)
                if install_scope:
                    self.targs.add(argument)
        finally:
            self._naming = was_naming
            self._last_source_name = last_source_name
            if not install_scope:
                self._scope_has_pack = outer_has_pack
                self._argument_constraint = outer_constraint
        if keep_empty:
            # c++filt prints an empty pack as an empty argument and keeps the comma,
            # `thread<main::{lambda()#1}, , void>`, and drops the empty ones at the end
            # of the list: `f<int, JE>` is `f<int>`. See `gnu_empty_pack_spelling`.
            while empties and empties[-1]:
                arguments.pop()
                empties.pop()
        self._trailing_empty_pack = trailing_empty_pack
        return arguments

    def template_arg(self):
        """One template argument.

        ```
        <template-arg> ::= <type> | X <expression> E | <expr-primary> | J <template-arg>* E
        ```

        An argument pack is also read from `I <template-arg>* E`: the form g++ wrote for
        one under `-fabi-version` 2 through 5, the default of GCC 3.4 through 4.9, and
        still writes as a compatibility alias beside the `J` form when asked for those
        versions. Nothing else can stand where an argument does and begin with `I`, so
        there is nothing to tell it from; libiberty's `d_template_arg` reads `I` and
        `J` alike, and `llvm-cxxfilt` refuses the older one.

        Returns `(handle, is_empty_pack)`. The handle is None for a
        <template-param-decl>, which declares a parameter rather than supplying one.

        Emptiness is returned rather than recorded on the parser because argument lists
        nest: an empty pack inside `AnalysisManager<Module, JE>` would otherwise still be
        flagged when the enclosing `PassManager<Function, AnalysisManager<...>>` finished
        its own argument, and the enclosing argument would be dropped.
        """
        reader = self.reader
        builder = self.builder

        # One lookahead for all five alternatives: this runs once per template argument.
        ahead = reader.peek2()
        char = ahead[:1]

        if ahead in _PARAMETER_DECLARATIONS:
            # A declaration qualifies the next argument (`parseTemplateArg`), so llvm-cxxfilt
            # refuses a list ending on one, `ITyE`.
            self.template_param_decl()
            if reader.peek() == "E":
                raise ParseError(self._mangled, reader.pos, "a template parameter declaration with no argument")
            return None, False

        if char == "Q":
            # A requires-clause closing an argument list: llvm-cxxfilt does not print it; c++filt
            # prints it after the function's parameters (kept under the gnu style).
            reader.take()
            outer_constraint = self._in_constraint
            outer_naming = self._naming
            self._in_constraint = True
            self._naming = False
            try:
                constraint = self.expression()
            finally:
                self._in_constraint = outer_constraint
                self._naming = outer_naming
            if self.options.gnu_expression_spelling:
                self._argument_constraint = builder.spell(constraint)
            return None, False

        if char == "X":
            reader.take()
            # `>`, `>>` and `,` are bracketed inside an argument list, or they read as its end or
            # separator; the opening two characters say which.
            angled = reader.peek2() in ("gt", "rs", "cm")
            if self.options.gnu_expression_spelling:
                # c++filt brackets each operand instead (`enable_if<(4u),(4), void>`) and leaves `>>`
                # bare, `f<(1)>>(2)>`; a `>` is bracketed by its own spelling.
                angled = False
            # llvm-cxxfilt brackets a `>` or `>>` anywhere in an argument list until some bracket
            # opens round it (`(1 > 0) && true`); this wrap is such a bracket.
            outer_bare = self._bare_angle
            self._bare_angle = not angled and not self.options.gnu_expression_spelling
            try:
                expression = self.expression()
            finally:
                self._bare_angle = outer_bare
            reader.expect("E")
            if angled:
                expression = builder.expression("paren", ["(", expression, ")"])
            return expression, False

        if char == "L":
            return builder.raw(self.expr_primary()), False

        if char in ("J", "I"):
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
                if next_char == "I":
                    self._legacy_pack_nested = True
                member, _ = self.template_arg()
                if member is not None:
                    members.append(member)
            self._scope_has_pack = True
            if char == "I":
                self._legacy_pack_used = True
            handle = builder.parameter_pack(members)
            self._packs.append(handle)
            self._pack_ids.add(id(handle))
            # An empty pack takes a `T_` slot but spells nothing (`AnalysisManager<Module, JE>`).
            # The builder decides, as it flattened any nested empty packs.
            return handle, not builder.spell(handle)

        # A template template argument is the template's bare name, and a back-reference
        # to one is legal here and nowhere else a <type> is read.
        self._template_name_argument = char == "S"
        argument = self.type_()
        # An expansion over an empty pack takes no slot either. Checked on the handle: `Dp`
        # records an expansion, not a pack (`1AIDpT_T0_E` over an empty `T_` is `A<int>`).
        return argument, builder.members(argument) is not None and not builder.spell(argument)

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
            # A reference to a declared entity (5.1.6.2), parsed with this parser's state because
            # Clang writes substitutions inside it that index the enclosing table. The bare `Z` is
            # g++'s compatibility form (libcxxabi's `_ZN5test52f2ENS_2t2ILZ4mainEEE`); llvm-cxxfilt
            # accepts it only as a template argument, c++filt anywhere, as here. See
            # `_bare_entity_prefix_used`; `tools/mutate.py --seed 35`.
            if reader.peek() == "Z":
                self._bare_entity_prefix_used = True
            reader.eat("_")
            reader.expect("Z")
            # `Z <encoding> E <entity>`, a local name, detected here because a nested encoding
            # reaches `name()` first.
            local = reader.peek() == "Z"
            was_naming = self._naming
            outer_scope = self.targs.snapshot()
            # `_drop_return` is about the enclosing function, not this embedded encoding
            # (`f<h()>()::{lambda()#1}`).
            outer_drop_return = self._drop_return
            outer_no_return_type = self._no_return_type
            outer_explicit_object = self._explicit_object
            self._naming = True
            self._drop_return = False
            self._no_return_type = False
            self._explicit_object = False
            try:
                handle = self.encoding()
            finally:
                # The table is shared, but the embedded entity's own `T_` scope must not outlive it.
                self._naming = was_naming
                self.targs.restore(outer_scope)
                self._drop_return = outer_drop_return
                self._no_return_type = outer_no_return_type
                self._explicit_object = outer_explicit_object
            reader.expect("E")
            self._entity_local = local
            return builder.spell(handle)

        if reader.peek2() == "Ul":
            # `L <closure-type-name> E`: a closure object as a value. The reference spells the
            # lambda expression and accepts only `Ul` here.
            handle = self.unnamed_type_name(lambda_expression=True)
            reader.expect("E")
            return builder.spell(handle)

        # `L <array-type> E` is a string literal whose contents are not mangled; the reference
        # prints the type in angle brackets inside the quotes.
        was_array = reader.peek() == "A"
        # Decided by the characters written: both references refuse `LSt9nullptr_tE`.
        wrote_nullptr = reader.peek2() == "Dn"
        # Decided by the type code, not the spelling: a class called `float` is not one.
        wrote_float = reader.peek() in ("d", "e", "f")
        kind = self.type_()
        spelling = builder.spell(kind)
        if was_array and reader.eat("E"):
            return f'"<{spelling}>"'

        if reader.eat("E"):
            if not wrote_nullptr:
                # A value is not optional: both references refuse `LaE`.
                raise ParseError(self._mangled, reader.pos, "a literal with no value")
            # `LDnE` is `decltype(nullptr)` to c++filt and `nullptr` to llvm-cxxfilt.
            return spelling if self.options.gnu_nullptr_spelling else "nullptr"

        start = reader.pos
        if wrote_float:
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated literal")
                reader.take()
            return self.spell_float_literal(spelling, reader.text[start : reader.pos - 1])
        # `<value number>`: digits after an optional `n`; llvm-cxxfilt refuses `Li4JE` and `LinE`.
        reader.eat("n")
        if reader.peek() not in DIGITS:
            raise ParseError(self._mangled, reader.pos, "a literal whose value is not a number")
        reader.digits()
        value = reader.text[start : reader.pos]
        reader.expect("E")
        if spelling == "bool" and value not in ("0", "1"):
            # `Lb0E` and `Lb1E` are the two bool literals there are. `llvm-cxxfilt`
            # refuses `Lb2E`; `c++filt` spells `(bool)2`, a value no bool has.
            raise ParseError(self._mangled, reader.pos, "a bool literal that is neither 0 nor 1")
        return self.spell_literal(spelling, value)

    def spell_float_literal(self, kind, value):
        """`L <d|e|f> <hex> E`: the value's bytes, most significant first, in hex.

        `llvm-cxxfilt` decodes them and prints the number with C's `%a`, `f` after a
        float and `L` after a long double; `c++filt` writes the hex as it stands, in
        brackets after the type: `(double)[4048f5c28f5c28f6]`. Each spelling here is one
        of those.

        The width is the type's: eight digits for a float and sixteen for a double, and
        for a long double whichever the target has -- sixteen where it is a double,
        twenty for the x87 extended format and thirty-two for the IEEE quad, plus the
        x87 format as g++ pads it to the type's size, twenty-four digits on i386 and
        thirty-two on x86-64; see `_c_hex_float`. `llvm-cxxfilt` insists on the width
        of the machine it runs on and refuses the rest; `c++filt` brackets any run of
        characters at all. Neither is a reading of `Ld4048E`, which is no value, so the
        width has to be one of those and every character a hex digit -- a lowercase
        one, as the ABI says and as LLVM's main branch requires.
        """
        widths = _FLOAT_WIDTHS[kind]
        if len(value) not in widths or not _HEX.fullmatch(value):
            raise ParseError(self._mangled, self.reader.pos, "a floating-point literal of the wrong width")
        if len(value) == 24 and not value.startswith("0000"):
            # Twenty-four digits are g++'s i386 form, which opens with two zero bytes. Refused
            # under both styles: a style never decides whether a name reads.
            raise ParseError(self._mangled, self.reader.pos, "a long double of twenty-four digits without its padding")
        if self.options.gnu_expression_spelling:
            return f"({kind})[{value}]"
        return _c_hex_float(kind, value)

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
        shape = self._entity_shape or ("literal", None)
        kind = shape[0]
        local = self._entity_local

        if kind == "literal" or (kind == "data" and not local):
            return text
        if operator == "&" and kind == "function" and shape[2] and not local:
            spelled = self.builder.spell(shape[1])
            # Qualified, which is what tells a member or namespace-scope function from
            # `&(f())`: c++filt prints the name only when it has a scope to print.
            if "::" in spelled:
                return spelled
        return "(" + text + ")"

    def _entity_callee(self):
        """GNU's spelling of an embedded `<mangled-name>` that is being called.

        libiberty prints a call whose callee is a typed name -- an encoding with a
        function type -- through the name alone: "function call used in an expression
        should not have printed types of the function arguments". So `clL_Z1hiEfp_E` is
        `h({parm#1})` and `clL_ZN1A1sEiEfp_E` is `A::s({parm#1})`, the parameter types
        the mangling carries dropped and the name bare, qualified or not. The name goes
        through `d_print_subexpr` like any operand, so anything that is not a plain name
        is bracketed: template arguments, `(h<int>)`; the qualifiers of a member, which
        libiberty attaches to the name, `(A::s const)`; a local entity, `(h()::x)`. A
        data name is bare and a special name bracketed, as under any other operator.
        """
        self._entity_shape = None
        self._entity_local = False
        text = self.expr_primary()
        shape = self._entity_shape or ("literal", None)
        kind = shape[0]
        local = self._entity_local
        if kind == "function":
            _, name, _, suffix, is_template = shape
            spelled = self.builder.spell(name) + suffix
            # An unqualified operator function is an operator name, bracketed by c++filt:
            # `(operator+)({parm#1})`, but `A::operator+` stands bare.
            unqualified_operator = "::" not in spelled and _OPERATOR_FUNCTION.match(spelled) is not None
            if is_template or suffix or local or unqualified_operator:
                spelled = "(" + spelled + ")"
            return self.builder.raw(spelled)
        if kind == "literal" or (kind == "data" and not local):
            return self.builder.raw(text)
        return self.builder.raw("(" + text + ")")

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
            return {"0": "false", "1": "true"}[value]
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
        if reader.peek() == "N" and (
            reader.ahead(1) in DIGITS or reader.startswith("NSt") or self.options.gnu_unresolved_scope_substitution
        ):
            # `sr N <prefix>+ E <name>`: the old form's nested-name shape, which g++ 13 writes for a
            # non-dependent class scope with a dependent member (`srN1A1B1CIT_EE1w`), recorded as one
            # type. GNU c++filt reads every `srN` this way; the option says when to follow it.
            levels.append(self.builder.spell(self.type_()))
        elif reader.eat("N"):
            levels.append(self._unresolved_head())
            # `*`, not the ABI's `+`: Clang emits `srN <type> <template-args> E` with no levels, and
            # the reference accepts it.
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated qualifier levels")
                levels.append(self.simple_id())
        elif reader.peek() in DIGITS:
            self._qualifier_levels(levels)
        elif reader.startswith("St") and reader.ahead(2) in DIGITS:
            # The pre-<unresolved-name> form g++ still writes, `sr <type> <unqualified-name>` with a
            # complete type, as libiberty's `d_expression_1` reads it. libstdc++ ships it
            # (`srSt23__is_random_access_iterIT0_...E7__valueE`); llvm-cxxfilt refuses it, but `St`
            # plus a name has no reading under the modern grammar.
            levels.append(self.builder.spell(self.type_()))
        else:
            levels.append(self._unresolved_head())

        levels.append(self.base_unresolved_name(qualified=True))
        if prefix:
            # `::x` is rooted at global scope, which c++filt brackets as an operand.
            self._simple_name = False
        return prefix + "::".join(levels)

    def _qualifier_levels(self, levels):
        """`<unresolved-qualifier-level>+ E`, or the old form the same letters spell.

        `sr 1A 3baz IT_E ...` is ambiguous. Under the modern grammar it opens a list of
        qualifier levels, `A::baz<T>::...`, that runs to an `E`. Under the grammar the
        ABI had before the <unresolved-name> productions it is `sr <type> <name>`, the
        class `A` and the member `baz<T>`, with no `E` at all -- and that is what g++
        13 still writes for every member of a class that is not itself dependent:
        `decltype(A::baz<T> + t)` is `_Z1kIiEDTplsr1A3bazIT_Efp_ES1_`, where Clang
        writes `sr1AE3bazIT_E`. libiberty's `d_unresolved_name` says the same and
        reads the name twice, modern first and old if the whole then fails, because
        the modern reading can run on past the `sr` before anything refuses it --
        `sr1A3bazIT_EE` closes a `decltype` with the `E` the levels took, and it is
        the substitution after that which has nothing to name. `llvm-cxxfilt` 18 and
        20 refuse the old form outright.

        So this reads the modern way and notes that it did; `parse` reads the whole
        name again with `_old_unresolved_names` set if that reading fails. The
        numbering follows either reading: the levels record nothing, the type records
        itself and whatever its arguments record, which is where g++ counts `S1_`
        from.
        """
        reader = self.reader
        if self._old_unresolved_names:
            levels.append(self.builder.spell(self.type_()))
            return
        self._ambiguous_unresolved_name = True
        while not reader.eat("E"):
            if reader.eof:
                raise ParseError(self._mangled, reader.pos, "unterminated qualifier levels")
            levels.append(self.simple_id())

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

    def base_unresolved_name(self, qualified=False):
        """<base-unresolved-name> ::= <simple-id> | on <operator-name> [<template-args>]
        | dn <destructor-name>

        `qualified` says an `sr` scope precedes it, which is what decides whether an
        operator name is a plain operand -- see below.
        """
        reader = self.reader
        if reader.peek() in DIGITS:
            return self.simple_id()
        if reader.eat("dn"):
            # `_simple_name`: whether the whole name is a plain identifier path, which GNU c++filt
            # leaves unbracketed as an operand. Decided by the last component, since arguments on a
            # qualifier do not count.
            text = "~" + self.destructor_name()
            self._simple_name = False
            return text
        # The `on` marker is optional: `srT_pl` names `T::operator+` with nothing to say
        # so, and the reference reads the operator code either way.
        reader.eat("on")
        text = self.operator_name()
        # c++filt brackets an operator name on its own, `&(operator&)`; under an `sr` scope it is
        # a qualified name, `&A::operator&`, unless arguments follow: `&(A::operator&<int>)`.
        simple = qualified and reader.peek() != "I"
        if reader.peek() == "I":
            text += self.spelled_template_arguments()
        self._simple_name = simple
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
        # `on <operator-name>` and `dn <destructor-name>`: a callee named by the operator it is,
        # as in an unresolved `a + b` in `decltype` (`_Z1fI1AEDTclonplfp_fp_EET_`); not the
        # builtin codes `o` and `n`.
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
        if reader.peek2() == "il":
            # `new T{...}`: both compilers write `il <expression>* E` straight after the type, with
            # no `E` of its own. libiberty reads it; llvm-cxxfilt 18 and 20 refuse it.
            return self.expression()
        if not reader.eat("pi"):
            # Only `pi`, `il` or the closing `E`, as `d_expression` and `parseNewExpr` read it:
            # both refuse `nw_icvi_E`.
            raise ParseError(self._mangled, reader.pos, "a new-expression's initialiser must be `pi` or `il`")
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
        outer_naming = self._naming
        self._in_constraint = True
        # A clause names no entity, so a template-id in it must not install its arguments as the
        # `T_` scope (a nested requirement's `1234` became the next one's `T`).
        self._naming = False
        try:
            return self.expression()
        finally:
            self._in_constraint = outer
            self._naming = outer_naming

    def expression_text(self):
        """An expression where the grammar around it needs characters, not a shape.

        An array bound and a `throw(...)` specification are spelled *into* a type by
        productions that take text, so those ask for text. Everything reachable from
        `_expression` keeps the handle.
        """
        return self.builder.spell(self.expression())

    def _function_parameter(self):
        """The rest of `fp` or `fL <level> p`: `<top-level CV-qualifiers> [<number>] _`.

        The qualifiers are read and dropped, as `parseFunctionParam` drops them -- both
        references spell `fpK_` as `fp`. The `_` is not optional: `fp` alone is not a
        parameter, and reading it as one spelled `decltype(fp == nullptr)` for
        `DTeqfpLDnEE`, which both references refuse.
        """
        reader = self.reader
        while reader.peek() in ("r", "V", "K"):
            reader.take()
        index = reader.digits() if reader.peek() in DIGITS else ""
        reader.expect("_")
        self._precedence = SIMPLE_PRECEDENCE
        return self.builder.raw(self._spell_parameter(index))

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

    def _expansion_pattern(self):
        """Read the pattern of a pack expansion in an expression: `sp`, or a fold's pack.

        Under the rule `Dp` reads a type pattern by. A pattern that names a pack -- a
        `T_` bound to one -- is read again once per member, and what comes back is the
        members: `sp sc T_ fp_` over `{int, char}` is `static_cast<int>(fp),
        static_cast<char>(fp)` to both references. Spelling the one reading put the
        whole pack where each member belongs, `static_cast<int, char>(fp)`, a cast of a
        kind C++ has not. A pattern that names an empty pack is nothing. A pattern that
        names no pack is handed back as read, for the caller to write the dots after:
        they are the expansion of something the name does not carry, a function
        parameter pack most often, `g(fp...)` -- whatever the scope holds. Testing the
        scope for a pack, as this once did, dropped the dots from every
        `decltype(g(t...))`.

        Returns the handle and which of the three it is: `"members"`, `"empty"` or
        `"pattern"`.
        """
        reader = self.reader
        start = reader.pos
        mark = self.subs.mark()
        outer_pack = self._saw_pack
        outer_empty = self._saw_empty_pack
        outer_arity = self._pack_arity
        self._saw_pack = False
        self._saw_empty_pack = False
        self._pack_arity = None
        try:
            expanded = self.expression()
            over_pack = self._saw_pack
            over_empty = self._saw_empty_pack
            arity = self._pack_arity
        finally:
            # As for `Dp`: the expansion consumes its pack.
            self._saw_pack = outer_pack
            self._saw_empty_pack = outer_empty
            self._pack_arity = outer_arity
        if over_empty:
            return self.builder.parameter_pack([]), "empty"
        if over_pack and arity:
            return self._expand_pattern(start, mark, arity, self.expression), "members"
        return expanded, "pattern"

    def _fold_pack(self):
        """The pack half of a fold expression, bracketed and expanded.

        llvm-cxxfilt prints it through `ParameterPackExpansion` inside brackets of its
        own: the members when the pattern names a pack, `(sizeof (int), sizeof (char))`
        for `st T_` over `{int, char}` and `()` over none, and the pattern with the
        ellipsis that says it is still a pack when it does not, `(fp...)`.
        """
        builder = self.builder
        if self.options.gnu_expression_spelling:
            # c++filt prints a fold's pack operand through `d_print_subexpr`, with no ellipsis and no
            # per-member reading (`sizeof (int, char)` for `st T_`); llvm-cxxfilt writes `(int...)`.
            expanded = self.expression()
            if builder.members(expanded) is not None or self._precedence < SIMPLE_PRECEDENCE:
                return builder.expression("paren", ["(", expanded, ")"])
            return expanded
        expanded, kind = self._expansion_pattern()
        if kind != "pattern":
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
        keep_empty = self.options.gnu_empty_pack_spelling
        if keep_empty:
            # c++filt keeps the empty entries and their separators, `g(, int)`, and
            # drops those at the end. See `gnu_empty_pack_spelling`.
            items = list(items)
            self._drop_trailing_empties(items)
        for item in items:
            # `size` is O(1) and `spell` is not, on a hot path. Size zero settles an empty pack; only
            # a pack holding an empty pack can be non-zero and spell nothing.
            if not keep_empty:
                if builder.size(item) == 0:
                    continue
                if builder.members(item) is not None and not builder.spell(item):
                    continue
            if parts:
                parts.append(separator)
            parts.append(item)
        return parts

    def _drop_trailing_empties(self, items):
        """Take the expansions over empty packs off the end of a list, in place."""
        builder = self.builder
        while items and (
            builder.size(items[-1]) == 0 or (builder.members(items[-1]) is not None and not builder.spell(items[-1]))
        ):
            items.pop()

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
        other callers of this are list elements, which it prints without asking.
        """
        if self._bare_angle:
            # Whether this operand is bracketed is known from its opening operator, and that bracket
            # counts for llvm-cxxfilt: a `>` inside `!(1 > 0 && 2)` is not wrapped again.
            nominal = _NOMINAL_PRECEDENCE.get(self.reader.peek2())
            if nominal is not None and nominal < binding:
                self._bare_angle = False
                try:
                    operand = self.expression()
                finally:
                    self._bare_angle = True
                return self.builder.expression("paren", ["(", operand, ")"])
        operand = self.expression()
        if subexpression and self.options.gnu_expression_spelling:
            needed = self._precedence < SIMPLE_PRECEDENCE
        else:
            needed = self._precedence < binding
        if needed:
            return self.builder.expression("paren", ["(", operand, ")"])
        return operand

    def _conversion(self, kind):
        """The operand half of `cv <type> <expression>` or `cv <type> _ <expression>* E`."""
        reader = self.reader
        builder = self.builder
        if reader.eat("_"):
            arguments = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated conversion")
                arguments.append(self._element())
            return builder.expression("cast", ["(", kind, ")(", *self._commas(arguments), ")"])
        if self.options.gnu_expression_spelling:
            # c++filt brackets the operand by kind: `(int)x`, `(int){parm#1}` and `(A){1, 2}` bare,
            # `(int)(1)` otherwise. Both compilers write `T(t)` as `cvT_fp_`.
            return builder.expression("cast", ["(", kind, ")", self._operand(UNARY_PRECEDENCE, subexpression=True)])
        return builder.expression("cast", ["(", kind, ")(", self._element(), ")"])

    def _bracketed_expression(self):
        """Read an expression that its construct will print inside brackets of its own.

        A call's arguments, a cast's operand, what `sizeof` measures: inside those a
        `>` is not the end of any template argument list, and llvm-cxxfilt stops
        wrapping one. See `_bare_angle`.
        """
        if not self._bare_angle:
            return self.expression()
        self._bare_angle = False
        try:
            return self.expression()
        finally:
            self._bare_angle = True

    def _callee(self):
        """The thing being called, bracketed on the same rule as any other operand.

        `(std::declval<int>)()` and `(::foo)()` and `(operator+)(...)`, but `foo(int)`,
        `std::foo(int)`, `{parm#1}(int)` and `{1}(2)` -- all four confirmed against
        c++filt. Bracketing every callee that was not a plain identifier path was eight
        of the differences from it over libLLVM.
        """
        reader = self.reader
        if self.options.gnu_entity_operand_spelling and (reader.startswith("L_Z") or reader.startswith("LZ")):
            return self._entity_callee()
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

        # Leaves are returned unwrapped: an `Expression` layer would carry nothing, on the most
        # common productions.
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
                # `fpT`, the implicit object parameter, has no index; the reference spells it `this`.
                reader.pos += 3
                self._precedence = SIMPLE_PRECEDENCE
                return builder.raw("this")
            reader.pos += 2
            return self._function_parameter()
        if pair == "fL" and reader.ahead(2) in DIGITS:
            # `fL <number> p ...` is a parameter of an enclosing function; `fL` followed
            # by an operator code is a left fold with an initialiser, read below.
            reader.pos += 2
            reader.digits()
            reader.expect("p")
            return self._function_parameter()

        if pair == "sr":
            text = self.unresolved_name()
            return self._named_operand(text, self._simple_name)
        if pair in ("on", "dn"):
            # `<expression> ::= <unresolved-name>`, which may be `on <operator-name>` -- not the
            # builtin codes `o` and `n` (`_Z1fI1AEDTclonplfp_fp_EET_`).
            text = self.unresolved_name()
            return self._named_operand(text, self._simple_name)
        if pair == "sZ":
            reader.pos += 2
            # `sZ` takes a <template-param> or a <function-param>; only `fp` and `fL <digit>` are the
            # latter, so a fold falls through and is refused.
            is_function_param = reader.peek() == "f" and (
                reader.ahead(1) == "p" or (reader.ahead(1) == "L" and reader.ahead(2) in DIGITS)
            )
            outer_index = self._pack_index
            outer_pack = self._saw_pack
            outer_arity = self._pack_arity
            self._pack_index = None
            self._saw_pack = False
            self._pack_arity = None
            try:
                inner = self._bracketed_expression() if is_function_param else self.template_param()
                over_pack = self._saw_pack
                arity = self._pack_arity if over_pack and self._pack_arity is not None else 0
            finally:
                self._pack_index = outer_index
                self._saw_pack = outer_pack or self._saw_pack
                self._pack_arity = outer_arity
            if self.options.gnu_expression_spelling:
                # c++filt prints `d_pack_length` for `sZ`: a pack's member count, else 0 (`X<2>` for
                # `sZT_` under `<int, char>`, `decltype (0)` for `sZfp_`).
                self._precedence = PRIMARY_PRECEDENCE
                return builder.expression("sizeof_pack", [str(arity)])
            if is_function_param:
                # A function parameter is wrapped as it stands, with a space and no
                # ellipsis: `sizeof... (fp)`, which is how llvm-cxxfilt spells it.
                self._precedence = PRIMARY_PRECEDENCE
                return builder.expression("sizeof_pack", ["sizeof... (", inner, ")"])
            # `sizeof...` prints through a pack expansion, which adds `...` when it finds no pack:
            # `sizeof...(int...)` for a `T_` bound to `int`, and `sizeof...(T...)` in a
            # requires-clause, which Clang emits for any constrained variadic template.
            ellipsis = [] if over_pack else ["..."]
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("sizeof_pack", ["sizeof...(", inner, *ellipsis, ")"])
        if pair == "sP":
            reader.pos += 2
            members = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated sizeof... pack")
                argument, _ = self.template_arg()
                if argument is not None:
                    members.append(argument)
            self._precedence = PRIMARY_PRECEDENCE
            if self.options.gnu_expression_spelling:
                # `d_args_length`: the number of arguments, an expansion counting the
                # members of the pack it expands. `X<3>` for `sP Dp T_ i E` under
                # `<int, char>`, where llvm-cxxfilt writes `sizeof... (int, char, int)`.
                count = 0
                for member in members:
                    expanded = builder.members(member)
                    count += len(expanded) if expanded is not None else 1
                return builder.expression("sizeof_pack", [str(count)])
            # `sZ` writes `sizeof...(`; `sP`, over a captured pack, writes it with a
            # space. Both references agree, and it is the only thing separating them.
            return builder.expression("sizeof_pack", ["sizeof... (", *self._commas(members), ")"])
        if pair in ("st", "sz", "at", "az", "ti", "te", "nx"):
            # `sizeof (int)`, `alignof (x)`, `typeid (T)`, `noexcept (x)`: never re-bracketed,
            # except as a GNU operand, since none is a name.
            reader.pos += 2
            form, keyword = _MEASURING_OPERATORS[pair]
            gnu = self.options.gnu_expression_spelling
            if pair in _MEASURING_A_TYPE:
                parts = [keyword + " (", self.type_(), ")"]
            elif gnu and pair == "nx":
                # The one c++filt writes with no space and always with brackets:
                # `noexcept({parm#1})`, where it writes `sizeof {parm#1}`.
                parts = [keyword + "(", self._bracketed_expression(), ")"]
            elif gnu and pair in ("sz", "az"):
                # A keyword and then an operand like any other, so the brackets are the
                # operand's: `sizeof (1)` and `sizeof ({parm#1}())`, but `sizeof
                # {parm#1}` and `sizeof std::x`.
                parts = [keyword + " ", self._operand(PRIMARY_PRECEDENCE, subexpression=True)]
            else:
                parts = [keyword + " (", self._bracketed_expression(), ")"]
            # `sizeof`, `alignof` and `noexcept` are unary to llvm-cxxfilt's printer and
            # bracketed as the operand of anything as tight: `!(sizeof (int))` and
            # `(sizeof (int)).m`, where `typeid` is postfix and stands bare in both.
            self._precedence = PRIMARY_PRECEDENCE if pair in ("ti", "te") else UNARY_PRECEDENCE
            return builder.expression(form, parts)

        if pair == "tr":
            # `tr`: a rethrow, a leaf.
            reader.pos += 2
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("throw", ["throw"])
        if pair == "tw":
            # `tw <expression>`: LLVM writes the operand bare after a space; GNU brackets it unless
            # it is a name (`throw {parm#1}`).
            reader.pos += 2
            if self.options.gnu_expression_spelling:
                operand = self._operand(PRIMARY_PRECEDENCE, subexpression=True)
            else:
                operand = self.expression()
            self._precedence = LOOSEST_PRECEDENCE
            return builder.expression("throw", ["throw ", operand])

        if pair == "cl":
            reader.pos += 2
            target = self._callee()
            arguments = []
            outer_bare = self._bare_angle
            self._bare_angle = False
            try:
                while not reader.eat("E"):
                    if reader.eof:
                        raise ParseError(self._mangled, reader.pos, "unterminated call expression")
                    arguments.append(self._element())
            finally:
                self._bare_angle = outer_bare
            # A call closes with a bracket, so llvm-cxxfilt never brackets one again --
            # `*std::begin(x)`, not `*(std::begin(x))`. GNU does when it is an operand,
            # because a call is not a name.
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("call", [target, "(", *self._commas(arguments), ")"])

        if pair == "cv":
            reader.pos += 2
            kind = self.type_()
            outer_bare = self._bare_angle
            self._bare_angle = False
            try:
                cast = self._conversion(kind)
            finally:
                self._bare_angle = outer_bare
            # A cast binds looser than postfix: `((A*)(0))->member`, as the reference prints (and
            # thirteen libcxxabi corpus entries need).
            self._precedence = UNARY_PRECEDENCE
            return cast

        if pair == "tl":
            reader.pos += 2
            kind = self.type_()
            spelled = builder.spell(kind)
            if spelled.startswith("char [") or spelled == "char []":
                # A `char` array in a braced initialiser is a string only if every member is a
                # character literal, so it is tried and backed out of.
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
            # A `d_print_subexpr` position for c++filt: `((a->ua).i)`, but `{parm#1}.i`.
            owner = self._operand(POSTFIX_PRECEDENCE, subexpression=True)
            joiner = "." if pair == "dt" else "->"
            # Ordinarily an <unresolved-name>, and the fast path for it is inside
            # `expression`; but the grammar Clang emits allows any expression on the
            # right, and `ptT_Li4E` -- `4u->4` -- is one of those.
            if self.options.gnu_expression_spelling:
                # The member is a `d_print_subexpr` position as well, and a name with
                # template arguments is a template to it, not a name: `(f<int>)` in
                # `({parm#1}.(f<int>))`, where a bare `f` stands as it is.
                name = self._operand(POSTFIX_PRECEDENCE, subexpression=True)
            else:
                name = self.expression()
            self._precedence = POSTFIX_PRECEDENCE
            return builder.expression("member", [owner, joiner, name])

        if pair == "ix":
            reader.pos += 2
            # Subscript is the one postfix form the reference brackets against another
            # postfix -- `(fp[fp])[fp]`, but `fp.a.b` and `fp++++` unbracketed.
            # The object is a `d_print_subexpr` position to c++filt, `(1)[...]`, and
            # the index is not.
            owner = self._operand(PRIMARY_PRECEDENCE, subexpression=True)
            index = self._bracketed_expression()
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
            expanded, kind = self._expansion_pattern()
            if kind != "pattern":
                # Its members, or nothing: `f(xs...)` over an empty `xs` is `f()`, comma included, as
                # `Dp` does in a type list.
                self._precedence = PRIMARY_PRECEDENCE
                return expanded
            # A pattern naming no pack is `x...` whatever the scope (`g(fp...)`). GNU brackets it
            # unless it is a name: `(1)...` but `{parm#1}...`.
            if self.options.gnu_expression_spelling and self._precedence < SIMPLE_PRECEDENCE:
                expanded = builder.expression("paren", ["(", expanded, ")"])
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("pack_expansion", [expanded, "..."])

        if pair == "nw" or pair == "na":
            reader.pos += 2
            arguments = []
            outer_bare = self._bare_angle
            self._bare_angle = False
            try:
                while not reader.eat("_"):
                    if reader.eof:
                        raise ParseError(self._mangled, reader.pos, "unterminated new expression")
                    arguments.append(self.expression())
                kind = self.type_()
                keyword = "new" if pair == "nw" else "new[]"
                gap = " " if self.options.gnu_expression_spelling else ""
                placement = [gap, "(", *self._commas(arguments), ")"] if arguments else []
                parts = [keyword, *placement, " ", kind]
                if not reader.eat("E"):
                    parts.append(self.initialiser())
            finally:
                self._bare_angle = outer_bare
            self._precedence = UNARY_PRECEDENCE
            return builder.expression("new", parts)

        if pair in ("dl", "da"):
            reader.pos += 2
            keyword = "delete" if pair == "dl" else "delete[]"
            # `delete (4)` and `delete {parm#1}`: the operand is a `d_print_subexpr`
            # position, bracketed by kind, where llvm-cxxfilt writes `delete 4`.
            operand = self._operand(PRIMARY_PRECEDENCE, subexpression=True)
            # Unary to llvm-cxxfilt's printer, `(delete fp).m`, as `new` is.
            self._precedence = UNARY_PRECEDENCE
            return builder.expression("delete", [keyword, " ", operand])

        if pair in ("dc", "sc", "cc", "rc"):
            reader.pos += 2
            casts = {"dc": "dynamic_cast", "sc": "static_cast", "cc": "const_cast", "rc": "reinterpret_cast"}
            kind, inner = self.type_(), self._bracketed_expression()
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

        if pair == "aw":
            # `aw <expression>`, co_await: a keyword, so a space before the operand, which is
            # bracketed like any unary operand (GNU's `co_await (1)`).
            reader.pos += 2
            operand = self._operand(PRIMARY_PRECEDENCE, subexpression=True)
            self._precedence = UNARY_PRECEDENCE
            return builder.expression("unary", ["co_await ", operand])

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
            # Both halves stand inside the fold's own brackets.
            outer_bare = self._bare_angle
            self._bare_angle = False
            # GNU brackets the initialiser by kind: `fL pl Li9E T_` is `((9)+...+(1, 2))` there and
            # `(9 + ... + (1, 2))` here.
            try:
                if marker == "L":
                    # First for a left fold, second for a right one.
                    initialiser = self._operand(UNARY_PRECEDENCE, subexpression=True)
                    pack = self._fold_pack()
                elif marker == "R":
                    pack = self._fold_pack()
                    initialiser = self._operand(UNARY_PRECEDENCE, subexpression=True)
                else:
                    pack = self._fold_pack()
            finally:
                self._bare_angle = outer_bare
            # GNU writes no spaces around the operator, here as everywhere else in an
            # expression: `(...+(1, 2))`, not `(... + (1, 2))`.
            spelt = INFIX_OPERATORS[code]
            operator = spelt if self.options.gnu_expression_spelling else f" {spelt} "
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
                # GNU puts no space after the comma: `decltype ((1),(2))`, per c++filt 2.42.
                separator = "," if self.options.gnu_expression_spelling else ", "
                return builder.expression("comma", [left, separator, right])
            self._precedence = binding
            if pair == "gt" and self.options.gnu_expression_spelling:
                # libiberty's `d_print_comp` wraps any `>` expression in an extra layer of parens,
                # wherever it stands: `decltype (({parm#1}>{parm#1}))`.
                return builder.expression("binary", ["(", left, spelling, right, ")"])
            gap = "" if pair in TIGHT_INFIX or self.options.gnu_expression_spelling else " "
            if self._bare_angle and pair in ("gt", "rs"):
                # `BinaryExpr::printLeft`'s `ParenAll`: in an argument list with no bracket open, the
                # comparison or shift is wrapped, and so becomes primary.
                self._precedence = PRIMARY_PRECEDENCE
                return builder.expression("binary", ["(", left, gap, spelling, gap, right, ")"])
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
            # They chain; only the innermost writes the `=`.
            reader.pos += 2
            if pair == "di":
                designator = ["." + self.source_name()]
            elif pair == "dx":
                designator = ["[", self.expression(), "]"]
            else:
                designator = ["[", self.expression(), " ... ", self.expression(), "]"]
            nested = reader.peek2() in ("di", "dx", "dX")
            if self.options.gnu_expression_spelling:
                # `.n=(42)`, `.n=x`: no spaces round the `=`, and only the innermost value is an operand,
                # bracketed by kind.
                value = self.expression() if nested else self._operand(PRIMARY_PRECEDENCE, subexpression=True)
                self._precedence = PRIMARY_PRECEDENCE
                return builder.expression("designator", [*designator, *([] if nested else ["="]), value])
            value = self.expression()
            self._precedence = PRIMARY_PRECEDENCE
            return builder.expression("designator", [*designator, *([] if nested else [" = "]), value])

        if pair == "so":
            # so <referent type> <expression> [<offset>] <union-selector>* [p] E
            #
            # A subobject of a named object; neither reference prints the selectors or `p`.
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
                # The legacy `__uuidof` mangling puts a `t` or `z` where a <template-arg> goes, with one
                # operand and no `E`; neither can be confused with a <type>.
                marker = reader.take()
                arguments.append(self.type_() if marker == "t" else self.expression())
            else:
                while not reader.eat("E"):
                    if reader.eof:
                        raise ParseError(self._mangled, reader.pos, "unterminated vendor expression")
                    if reader.eat("X"):
                        # Spelled as a call's argument, unbracketed: `__uuidof(a >> b)`.
                        arguments.append(self.expression())
                        reader.expect("E")
                        continue
                    argument, _ = self.template_arg()
                    if argument is not None:
                        arguments.append(argument)
            self._precedence = POSTFIX_PRECEDENCE
            return builder.expression("call", [builder.raw(name), "(", *self._commas(arguments), ")"])

        # An <unresolved-name>, which creates no substitution entry where a <type> would:
        # `Q 5Sized I T_ E` records `T_` and nothing for `Sized`.
        if reader.peek() in DIGITS:
            text = self.unresolved_name()
            return self._named_operand(text, self._simple_name)

        raise ParseError(self._mangled, reader.pos, "unrecognised expression")


#: The two spellings of an undeduced placeholder type, which Apple's clang counts as
#: substitution candidates and no other compiler does.
_UNDEDUCED_AUTO = ("Da", "Dc")


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
    """Parse an Itanium mangled name into `builder`, returning its handle.

    Read twice when the first reading took an `sr` the modern way and then failed:
    the second reads every ambiguous `sr` as the old `sr <type> <name>` that g++ 13
    still writes -- see `_qualifier_levels`. That is libiberty's `unresolved_name_state`
    exactly, a whole-name retry, so a name that reads either way reads as the
    reference reads it. A name that reads neither way reports the first reading's
    error, which is the one the modern grammar gives.

    Read twice, too, when a back-reference ran past the substitution table and the name
    carries an undeduced `auto`: the table was numbered by the other of the two rules
    compilers apply to that type -- see `ItaniumOptions.undeduced_auto_substitution` --
    and the second reading applies it. Only where the option left the rule to the name's
    form; a caller who chose one is not second-guessed.

    And read twice when a back-reference named a closure prefix, or a template with no
    arguments, as a type, in a name that has a closure prefix: GCC 12 and earlier left
    the prefix out of the table, so every reference after it is one entry early -- see
    `ItaniumOptions.closure_prefix_substitution` -- and the second reading leaves it out
    too. The same rule about a caller who chose applies.
    """
    parser = ItaniumParser(mangled, builder, limits, options)
    try:
        return parser.parse()
    except ParseError as error:
        if isinstance(error, LimitExceeded):
            raise
        # Numbering rules a second reading may apply, in order: each is one a compiler is known
        # to apply, tried only where the caller left it open and the name can carry it.
        rules = []
        if isinstance(error, (SubstitutionOverrun, SubstitutionMisuse)):
            if options.closure_prefix_substitution is None and parser._closure_prefix_seen:
                # The other rule for a closure prefix: the ABI's, clang's and GCC 13's
                # against GCC 12's and Apple clang's, which leave it out of the table.
                # See `ItaniumOptions.closure_prefix_substitution`.
                rules.append({"_closure_prefix_substitutes": not parser._closure_prefix_substitutes})
            if options.undeduced_auto_substitution is None and any(pair in mangled for pair in _UNDEDUCED_AUTO):
                # The other rule for an undeduced `auto`; see `ItaniumOptions.undeduced_auto_substitution`.
                # Reached by a misuse too: one entry short lands in range more often than past the end.
                rules.append({"_auto_substitutes": not parser._auto_substitutes})
            if len(rules) == 2:
                # Both at once: upstream clang targeting Darwin counts the closure
                # prefix and not the `auto`, the opposite of Apple's fork on both.
                rules.append({**rules[0], **rules[1]})
            if options.inherited_constructor_substitution is None and parser._inherited_base_seen:
                # g++'s rule for an inheriting constructor's base type, which clang's first reading omits;
                # a g++ name then runs past the table. Not combined with the rules above: no corpus holds
                # both shapes.
                rules.append({"_inherited_base_substitutes": True})
        for overrides in rules:
            retry = ItaniumParser(mangled, builder, limits, options)
            for attribute, value in overrides.items():
                setattr(retry, attribute, value)
            try:
                return retry.parse()
            except ParseError:
                continue
        if not parser._ambiguous_unresolved_name:
            raise
        retry = ItaniumParser(mangled, builder, limits, options)
        retry._old_unresolved_names = True
        try:
            return retry.parse()
        except ParseError:
            raise error from None


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
