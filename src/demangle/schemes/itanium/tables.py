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
SPECIAL_TYPE_NAMES = {
    "TV": "vtable for ",
    "TT": "VTT for ",
    "TI": "typeinfo for ",
    "TS": "typeinfo name for ",
}

#: Special names taking an <encoding> operand.
SPECIAL_ENCODING_NAMES = {
    # `TH` is the initialisation routine and `TW` the wrapper, not the other way round.
    "TH": "thread-local initialization routine for ",
    "TW": "thread-local wrapper routine for ",
    "GV": "guard variable for ",
    "GR": "reference temporary for ",
}

#: Constructor and destructor variant markers (5.1.4.3). The variant does not change
#: the spelling -- all of them are written `Foo::Foo` or `Foo::~Foo` -- but it must be
#: consumed, and it is recorded on the AST so tools that care can see it.
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
