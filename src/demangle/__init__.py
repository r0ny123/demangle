"""demangle -- read mangled symbol names, in pure Python.

    >>> import demangle
    >>> demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi")
    'std::vector<int, std::allocator<int>>::push_back(int const&)'
    >>> demangle.demangle("?f@@YAXH@Z")
    'void __cdecl f(int)'
    >>> demangle.detect("_RNvC6_123foo3bar")
    'rust'

No dependencies, no native code, no compiler required. Supports the Itanium C++ ABI
(GCC and Clang), Microsoft's decorated names, both Rust schemes, Swift, Objective-C,
Go, D, Nim, Free Pascal, Delphi/C++Builder, and JNI.

`demangle()` never raises: a name it cannot read comes back unchanged. Use
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
    parse,
    parse_type,
    parseb,
    parseb_type,
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

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_LIMITS",
    "RELAXED_LIMITS",
    "Decorated",
    "DemanglingError",
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
    "demangle_strict",
    "demangle_type",
    "demangleb",
    "demangleb_strict",
    "demangleb_type",
    "detect",
    "detectb",
    "languages",
    "parse",
    "parse_type",
    "parseb",
    "parseb_type",
    "register_language",
    "register_style",
    "signature",
    "signatureb",
    "styles",
]
