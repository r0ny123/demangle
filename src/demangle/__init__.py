"""demangle -- read mangled symbol names, in pure Python.

    >>> import demangle
    >>> demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi")
    'std::vector<int, std::allocator<int>>::push_back(int const&)'
    >>> demangle.demangle("?f@@YAXH@Z")
    'void __cdecl f(int)'
    >>> demangle.detect("_RNvC6_123foo3bar")
    'rust'

No dependencies, no native code, no compiler required. Reads Itanium C++ (GCC/Clang),
MSVC, Rust, Swift, Objective-C, Go, D, Nim, Free Pascal, Delphi, Ada/GNAT, JNI, and the
pre-Itanium C++ families (g++ 2.x, cfront/ARM, Lucid, HP aCC, CodeWarrior).

`demangle()` never raises over a name: one it cannot read comes back unchanged. Use
`demangle_strict()` or `parse()` when you need to know the difference, and
`signature()` when you want the parts rather than the spelling:

    >>> demangle.signature("_ZNSt6vectorIiSaIiEE9push_backERKi").namespace
    'std::vector<int, std::allocator<int>>'
"""

from ._signature import Signature, signature, signatureb
from .api import (
    cache_clear,
    cache_stats,
    demangle,
    demangle_all,
    demangle_strict,
    demangle_type,
    demangleb,
    demangleb_strict,
    demangleb_type,
    detect,
    detectb,
    languages,
    load_plugins,
    node_kinds,
    parse,
    parse_type,
    parseb,
    parseb_type,
    preload,
    style,
    styles,
)
from .core.ast import Decorated, Node
from .core.errors import (
    DemanglingError,
    LimitExceeded,
    NotMangledError,
    ParseError,
    TruncatedError,
)
from .core.limits import DEFAULT_LIMITS, RELAXED_LIMITS, Limits
from .core.plugin import LanguagePlugin
from .core.registry import register as register_language
from .core.style import Style, register_style
from .filter import Found, demangle_stream, demangle_text, find_symbols

__version__ = "0.5.2"

__all__ = [
    "DEFAULT_LIMITS",
    "RELAXED_LIMITS",
    "Decorated",
    "DemanglingError",
    "Found",
    "LanguagePlugin",
    "LimitExceeded",
    "Limits",
    "Node",
    "NotMangledError",
    "ParseError",
    "Signature",
    "Style",
    "TruncatedError",
    "__version__",
    "cache_clear",
    "cache_stats",
    "demangle",
    "demangle_all",
    "demangle_stream",
    "demangle_strict",
    "demangle_text",
    "demangle_type",
    "demangleb",
    "demangleb_strict",
    "demangleb_type",
    "detect",
    "detectb",
    "find_symbols",
    "languages",
    "load_plugins",
    "node_kinds",
    "parse",
    "parse_type",
    "parseb",
    "parseb_type",
    "preload",
    "register_language",
    "register_style",
    "signature",
    "signatureb",
    "style",
    "styles",
]
