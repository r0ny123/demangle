"""Fixed vocabularies of the Itanium C++ ABI mangling scheme.

Every table here is transcribed from the specification rather than inferred from
observed output, and each carries the section it came from. Keeping them apart from the
parser means a spelling correction is a one-line data change that cannot disturb control
flow, and means the tables can be checked against the specification by eye.

Reference: Itanium C++ ABI, section 5.1 (https://itanium-cxx-abi.github.io/cxx-abi/abi.html).
"""

# -- 5.1.5.2 Builtin types ----------------------------------------------------

#: Not substitution candidates (5.1.10).
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

#: Output order, whatever the mangled order.
QUALIFIER_ORDER = ("const", "volatile", "restrict")

#: Precomputed by `rVK` bitmask: `cv_qualifiers` runs once per type.
CV_COMBINATIONS = {
    mask: tuple(
        qualifier
        for qualifier in QUALIFIER_ORDER
        if qualifier in {letter for bit, letter in ((1, "restrict"), (2, "volatile"), (4, "const")) if mask & bit}
    )
    for mask in range(8)
}

# -- 5.1.3 Operator encodings -------------------------------------------------

#: code -> (spelling after "operator", needs a separating space).
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

#: 5.1.6: bare `pp`/`mm` are postfix; the prefix forms are `pp_`/`mm_`.
POSTFIX_OPERATORS = {"pp": "++", "mm": "--"}

#: Infix operators the references print without surrounding spaces.
TIGHT_INFIX = frozenset({"pm", "ds"})


#: C++ precedence, higher binds tighter ([expr.compound]). An operand is bracketed only
#: when it binds more loosely than its operator, as both reference demanglers do.
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

#: Decides which side needs brackets at equal precedence.
RIGHT_ASSOCIATIVE = frozenset({"aS", "pL", "mI", "mL", "dV", "rM", "aN", "oR", "eO", "lS", "rS", "qu"})

#: Precedence of a unary prefix operator, and of anything that needs no brackets at all.
UNARY_PRECEDENCE = 15
PRIMARY_PRECEDENCE = 17

#: GNU c++filt's `d_print_subexpr` brackets every operand except a name, qualified name,
#: braced init list or function parameter (`(1)+(2)` but `std::x+(2)`); those get this.
#: Under llvm style it behaves as `PRIMARY_PRECEDENCE`.
SIMPLE_PRECEDENCE = 18

#: `throw x` only: always bracketed as an operand (LLVM's `Precedence::Default`).
LOOSEST_PRECEDENCE = 0

# -- 5.1.10 Abbreviations -----------------------------------------------------

#: The `Sx` catalogue. Referring to one adds no substitution entry. llvm-cxxfilt and GNU
#: c++filt spell four of them differently by design, so the spelling is a caller policy.

#: Short spellings, as llvm-cxxfilt prints them (the default).
STD_ABBREVIATIONS = {
    "St": "std",
    "Sa": "std::allocator",
    "Sb": "std::basic_string",
    "Ss": "std::string",
    "Si": "std::istream",
    "So": "std::ostream",
    "Sd": "std::iostream",
}

#: Also used in either style when the abbreviation scopes a constructor or destructor,
#: which is named for the template, not the typedef: `_ZNSdC1Ev` is
#: `std::basic_iostream<char, ...>::basic_iostream()` even under LLVM style.
STD_ABBREVIATIONS_EXPANDED = {
    "St": "std",
    "Sa": "std::allocator",
    "Sb": "std::basic_string",
    "Ss": "std::basic_string<char, std::char_traits<char>, std::allocator<char>>",
    "Si": "std::basic_istream<char, std::char_traits<char>>",
    "So": "std::basic_ostream<char, std::char_traits<char>>",
    "Sd": "std::basic_iostream<char, std::char_traits<char>>",
}

#: GNU c++filt's pre-C++11 `> >` spacing.
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

#: Special names taking a <type> operand. `TF` and `TJ` are GNU extensions (libiberty
#: `d_special_name`) that llvm-cxxfilt refuses.
SPECIAL_TYPE_NAMES = {
    "TV": "vtable for ",
    "TT": "VTT for ",
    "TI": "typeinfo for ",
    "TS": "typeinfo name for ",
    "TF": "typeinfo fn for ",
    "TJ": "java Class for ",
}

#: Special names taking an <encoding> operand. `GA` is a GNU extension that
#: llvm-cxxfilt refuses.
SPECIAL_ENCODING_NAMES = {
    "TH": "thread-local initialization routine for ",
    "TW": "thread-local wrapper routine for ",
    "GV": "guard variable for ",
    "GR": "reference temporary for ",
    "GA": "hidden alias for ",
}

#: GNU c++filt's wording; every other special name is spelled the same by both.
SPECIAL_ENCODING_NAMES_GNU = {
    "TH": "TLS init function for ",
    "TW": "TLS wrapper function for ",
}

#: Ctor/dtor variant markers (5.1.4.3). The variant never changes the spelling; the keys
#: are how the parser tells a constructor from an operator.
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
