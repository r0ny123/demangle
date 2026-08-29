"""Fixed vocabularies of the Itanium C++ ABI mangling scheme.

Every table here is transcribed from the specification rather than inferred from
observed output, and each carries the section it came from. Keeping them apart from the
parser means a spelling correction is a one-line data change that cannot disturb control
flow, and means the tables can be checked against the specification by eye.

Reference: Itanium C++ ABI, section 5.1 (https://itanium-cxx-abi.github.io/cxx-abi/abi.html).
"""

# -- 5.1.5.2 Builtin types ----------------------------------------------------

#: Single-character builtin types. Explicitly *not* substitution candidates (5.1.10):
#: a back-reference could not be shorter than the one character they already occupy.
BUILTIN_TYPES = {
    "v": "void",
    "w": "wchar_t",
    "b": "bool",
    "c": "char",
    "a": "signed char",
    "h": "unsigned char",
    "s": "short",
    "t": "unsigned short",
    "i": "int",
    "j": "unsigned int",
    "l": "long",
    "m": "unsigned long",
    "x": "long long",
    "y": "unsigned long long",
    "n": "__int128",
    "o": "unsigned __int128",
    "f": "float",
    "d": "double",
    "e": "long double",
    "g": "__float128",
    "z": "...",
}

#: Two-character builtin types, all introduced by `D`.
EXTENDED_BUILTIN_TYPES = {
    "Da": "auto",
    "Dc": "decltype(auto)",
    "Dd": "decimal64",
    "De": "decimal128",
    "Df": "decimal32",
    "Dh": "half",
    "Di": "char32_t",
    "Ds": "char16_t",
    "Du": "char8_t",
    "Dn": "std::nullptr_t",
    "DB": "_BitInt",
    "DU": "unsigned _BitInt",
}

# -- 5.1.5.1 Qualified types --------------------------------------------------

QUALIFIER_LETTERS = {"r": "restrict", "V": "volatile", "K": "const"}

#: C++ spells cv-qualifiers in a fixed order regardless of how they were written, so
#: the parser collects them as a set and emits them through this ordering.
QUALIFIER_ORDER = ("const", "volatile", "restrict")

#: Every answer `cv_qualifiers` can give, by which of `r`, `V` and `K` were present.
#: There are eight of them and the production is read once per type: building the tuple
#: each time meant a generator resumed four times to reorder at most three words.
CV_COMBINATIONS = {
    mask: tuple(
        qualifier
        for qualifier in QUALIFIER_ORDER
        if qualifier in {letter for bit, letter in ((1, "restrict"), (2, "volatile"), (4, "const")) if mask & bit}
    )
    for mask in range(8)
}

# -- 5.1.3 Operator encodings -------------------------------------------------

#: code -> (spelling after the word "operator", needs a separating space).
#: `operator new` needs the space; `operator++` does not, and `operator ++` would be
#: wrong rather than merely ugly.
OPERATORS = {
    "nw": ("new", True),
    "na": ("new[]", True),
    "dl": ("delete", True),
    "da": ("delete[]", True),
    "aw": ("co_await", True),
    "ps": ("+", False),
    "ng": ("-", False),
    "ad": ("&", False),
    "de": ("*", False),
    "co": ("~", False),
    "pl": ("+", False),
    "mi": ("-", False),
    "ml": ("*", False),
    "dv": ("/", False),
    "rm": ("%", False),
    "an": ("&", False),
    "or": ("|", False),
    "eo": ("^", False),
    "aS": ("=", False),
    "pL": ("+=", False),
    "mI": ("-=", False),
    "mL": ("*=", False),
    "dV": ("/=", False),
    "rM": ("%=", False),
    "aN": ("&=", False),
    "oR": ("|=", False),
    "eO": ("^=", False),
    "ls": ("<<", False),
    "rs": (">>", False),
    "lS": ("<<=", False),
    "rS": (">>=", False),
    "eq": ("==", False),
    "ne": ("!=", False),
    "lt": ("<", False),
    "gt": (">", False),
    "le": ("<=", False),
    "ge": (">=", False),
    "ss": ("<=>", False),
    "nt": ("!", False),
    "aa": ("&&", False),
    "oo": ("||", False),
    "pp": ("++", False),
    "mm": ("--", False),
    "cm": (",", False),
    "pm": ("->*", False),
    "pt": ("->", False),
    "cl": ("()", False),
    "ix": ("[]", False),
    "qu": ("?", False),
}

# -- 5.1.6 Expressions --------------------------------------------------------

#: Operators as they appear inside an expression, where the spelling is infix rather
#: than a name following the word "operator".
INFIX_OPERATORS = {
    "pl": "+",
    "mi": "-",
    "ml": "*",
    "dv": "/",
    "rm": "%",
    "an": "&",
    "or": "|",
    "eo": "^",
    "aS": "=",
    "pL": "+=",
    "mI": "-=",
    "mL": "*=",
    "dV": "/=",
    "rM": "%=",
    "aN": "&=",
    "oR": "|=",
    "eO": "^=",
    "ls": "<<",
    "rs": ">>",
    "lS": "<<=",
    "rS": ">>=",
    "eq": "==",
    "ne": "!=",
    "lt": "<",
    "gt": ">",
    "le": "<=",
    "ge": ">=",
    "ss": "<=>",
    "aa": "&&",
    "oo": "||",
    "cm": ",",
    "ds": ".*",
    "pm": "->*",
}

PREFIX_OPERATORS = {
    "ps": "+",
    "ng": "-",
    "ad": "&",
    "de": "*",
    "co": "~",
    "nt": "!",
    "pp": "++",
    "mm": "--",
}

#: 5.1.6 spells `++` and `--` as *postfix* by default; the prefix forms are `pp_` and
#: `mm_`. Getting this backwards mis-spells one and leaves the other unparseable.
POSTFIX_OPERATORS = {"pp": "++", "mm": "--"}

#: Infix operators the references print without surrounding spaces.
TIGHT_INFIX = frozenset({"pm", "ds"})


#: C++ operator precedence, higher binding tighter ([expr.compound]). Used to decide
#: which sub-expressions need parentheses: an operand is bracketed only when it binds
#: more loosely than the operator applying to it, so `!a && b` and `1 + 2 * 3` are
#: written as they would be in source, and only `(1 + 2) * 3` gets brackets.
#: Both reference demanglers do this; printing every operand bracketed is unambiguous
#: but does not match, and is much harder to read.
PRECEDENCE = {
    "cm": 1,
    "aS": 2,
    "pL": 2,
    "mI": 2,
    "mL": 2,
    "dV": 2,
    "rM": 2,
    "aN": 2,
    "oR": 2,
    "eO": 2,
    "lS": 2,
    "rS": 2,
    "qu": 3,
    "oo": 4,
    "aa": 5,
    "or": 6,
    "eo": 7,
    "an": 8,
    "eq": 9,
    "ne": 9,
    "lt": 10,
    "gt": 10,
    "le": 10,
    "ge": 10,
    "ss": 10,
    "ls": 11,
    "rs": 11,
    "pl": 12,
    "mi": 12,
    "ml": 13,
    "dv": 13,
    "rm": 13,
    "pm": 14,
    "pt": 14,
    "ds": 14,
}

#: Postfix and subscript expressions bind tighter than any prefix operator.
POSTFIX_PRECEDENCE = 16

#: Right-associative operators. Associativity decides which side needs brackets at
#: *equal* precedence: `1 + (2 - 3)` keeps them because `+` groups left, so an unbracketed
#: `1 + 2 - 3` would mean `(1 + 2) - 3`.
RIGHT_ASSOCIATIVE = frozenset({"aS", "pL", "mI", "mL", "dV", "rM", "aN", "oR", "eO", "lS", "rS", "qu"})

#: Precedence of a unary prefix operator, and of anything that needs no brackets at all.
UNARY_PRECEDENCE = 15
PRIMARY_PRECEDENCE = 17

#: Tighter than `PRIMARY_PRECEDENCE`, and only GNU c++filt can tell the two apart. Its
#: `d_print_subexpr` brackets *every* operand of a unary, binary or ternary operator
#: except four kinds -- a name, a qualified name, a braced initialiser list and a
#: function parameter -- so it writes `(1)+(2)` and `!(x<int>)` where LLVM writes `1 + 2`
#: and `!x<int>`, but leaves `std::x+(2)`, `{1}+(2)` and `{parm#1}+(2)` alone. Under
#: llvm-style spelling this behaves exactly as `PRIMARY_PRECEDENCE`, because no operator
#: binds tighter than either.
SIMPLE_PRECEDENCE = 18

#: Looser than anything in `PRECEDENCE`, so an operand of this kind is always bracketed
#: under an operator. `throw x` is the only expression that binds this loosely -- it is
#: the whole of an assignment-expression and cannot be an operand of anything else
#: without brackets -- and the reference gives it `Precedence::Default`, which sits
#: below `Comma` for the same reason.
LOOSEST_PRECEDENCE = 0

# -- 5.1.10 Abbreviations -----------------------------------------------------

#: The `Sx` catalogue. These are pre-defined substitutions: referring to one does *not*
#: add a dictionary entry, because the encoder never had to add one either.
#:
#: The two reference demanglers disagree on how to spell four of these, and the
#: disagreement is a deliberate style choice on both sides rather than a bug in either.
#: For `_Z1fSs`, llvm-cxxfilt prints `f(std::string)` while GNU c++filt prints
#: `f(std::basic_string<char, std::char_traits<char>, std::allocator<char> >)`. Both are
#: correct; they are the same type. So the spelling is a policy the caller selects, and
#: both tables ship.

#: Short spellings, as llvm-cxxfilt prints them. The default: it is what modern
#: toolchains, debuggers and disassemblers show, and it is far more readable.
STD_ABBREVIATIONS = {
    "St": "std",
    "Sa": "std::allocator",
    "Sb": "std::basic_string",
    "Ss": "std::string",
    "Si": "std::istream",
    "So": "std::ostream",
    "Sd": "std::iostream",
}

#: Fully expanded spellings. Needed in two places: when the caller asks for GNU output,
#: and -- in *either* style -- when an abbreviation is the scope of a constructor or
#: destructor, since those are named for the class and the class is the template rather
#: than the typedef. `_ZNSdC1Ev` is `std::basic_iostream<char, ...>::basic_iostream()`
#: even under LLVM's style, which spells the same abbreviation `std::iostream` as a type.
#:
#: Two tables because the closing-bracket spacing is part of the spelling and the two
#: references differ on it. Written out rather than derived, so each matches its
#: reference exactly and by inspection.
STD_ABBREVIATIONS_EXPANDED = {
    "St": "std",
    "Sa": "std::allocator",
    "Sb": "std::basic_string",
    "Ss": "std::basic_string<char, std::char_traits<char>, std::allocator<char>>",
    "Si": "std::basic_istream<char, std::char_traits<char>>",
    "So": "std::basic_ostream<char, std::char_traits<char>>",
    "Sd": "std::basic_iostream<char, std::char_traits<char>>",
}

#: The same expansions with the pre-C++11 spacing GNU c++filt still prints.
STD_ABBREVIATIONS_EXPANDED_GNU = {
    "St": "std",
    "Sa": "std::allocator",
    "Sb": "std::basic_string",
    "Ss": "std::basic_string<char, std::char_traits<char>, std::allocator<char> >",
    "Si": "std::basic_istream<char, std::char_traits<char> >",
    "So": "std::basic_ostream<char, std::char_traits<char> >",
    "Sd": "std::basic_iostream<char, std::char_traits<char> >",
}

# -- 5.1.4 Other special functions and entities -------------------------------

#: Special names taking a <type> operand.
#:
#: `TF` and `TJ` are GNU extensions rather than ABI productions -- libiberty's
#: `d_special_name` reads them as `DEMANGLE_COMPONENT_TYPEINFO_FN` and
#: `DEMANGLE_COMPONENT_JAVA_CLASS` -- and llvm-cxxfilt refuses both. They are here
#: because a name a reference reads and we refuse is a gap, and because reading them
#: costs a table entry: `TJ` is gcj's, which no longer ships, and neither appears in any
#: of the 345,601 symbols in the shared libraries this was measured against.
SPECIAL_TYPE_NAMES = {
    "TV": "vtable for ",
    "TT": "VTT for ",
    "TI": "typeinfo for ",
    "TS": "typeinfo name for ",
    "TF": "typeinfo fn for ",
    "TJ": "java Class for ",
}

#: Special names taking an <encoding> operand.
#:
#: `GA` is a GNU extension -- libiberty reads it as `DEMANGLE_COMPONENT_HIDDEN_ALIAS` --
#: and llvm-cxxfilt refuses it.
SPECIAL_ENCODING_NAMES = {
    # `TH` is the initialisation routine and `TW` the wrapper, not the other way round.
    "TH": "thread-local initialization routine for ",
    "TW": "thread-local wrapper routine for ",
    "GV": "guard variable for ",
    "GR": "reference temporary for ",
    "GA": "hidden alias for ",
}

#: The two of those the references word differently. GNU c++filt writes `TLS init
#: function for x`, llvm-cxxfilt `thread-local initialization routine for x`; they name
#: the same entity. Everything else in the two tables above is spelled identically by
#: both, which is why only these two are listed.
SPECIAL_ENCODING_NAMES_GNU = {
    "TH": "TLS init function for ",
    "TW": "TLS wrapper function for ",
}

#: Constructor and destructor variant markers (5.1.4.3). The variant does not change the
#: spelling -- all of them are written `Foo::Foo` or `Foo::~Foo` -- so it is consumed and
#: discarded. The values are here to document what each marker means, and because a
#: membership test against them is how the parser tells a constructor from an operator.
#: Carrying the variant on the AST would be a reasonable thing to add; nothing does today.
CONSTRUCTOR_KINDS = {
    "1": "complete object constructor",
    "2": "base object constructor",
    "3": "complete object allocating constructor",
    "4": "unified constructor",
    "5": "object constructor closure",
}
DESTRUCTOR_KINDS = {
    "0": "deleting destructor",
    "1": "complete object destructor",
    "2": "base object destructor",
    "4": "unified destructor",
    "5": "object destructor closure",
}
