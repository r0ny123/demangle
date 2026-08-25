"""The Itanium C++ ABI mangling scheme.

Used by GCC, Clang, ICC and essentially every C++ toolchain outside the Microsoft
ecosystem, on every platform they target. Rust's legacy scheme is a dialect of it.

Reference: https://itanium-cxx-abi.github.io/cxx-abi/abi.html section 5.1.
"""

from ...core.plugin import LanguagePlugin
from ...core.registry import register
from .options import DEFAULT_OPTIONS, GNU_OPTIONS, ItaniumOptions
from .parser import ItaniumParser, detect, parse

PLUGIN = LanguagePlugin(
    name="itanium",
    detect=detect,
    parse=parse,
    description="Itanium C++ ABI (GCC, Clang, and compatible toolchains)",
    aliases=("gnu", "gcc", "clang", "cxx", "c++"),
    options_type=ItaniumOptions,
    # After Rust: a legacy Rust symbol is a valid Itanium symbol, so Rust must be
    # offered a name before this plugin claims it.
    symbol_table_decorations=True,
    # `_Z`, `__Z` and `_GLOBAL__` are the only starts `detect` accepts.
    first_characters="_",
    priority=200,
)

register(PLUGIN)

__all__ = ["DEFAULT_OPTIONS", "GNU_OPTIONS", "PLUGIN", "ItaniumOptions", "ItaniumParser", "detect", "parse"]
