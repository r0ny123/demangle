"""Rust symbol mangling: the legacy `_ZN` scheme and the v0 `_R` scheme.

Rust has used two schemes. The legacy one wraps a hashed path in Itanium mangling, so a
legacy Rust symbol is also a valid Itanium symbol -- which is why this plugin is offered
names first. The v0 scheme, specified in RFC 2603 and stabilised behind
`-Csymbol-mangling-version=v0`, is a scheme of its own.

Derived from Team bi0s' rust_demangler (MIT), with both grammars substantially reworked.
See NOTICE.

Structured output
-----------------
Like the MSVC parser, this one predates the builder protocol and returns a single `Raw`
node from `parse()`. `demangle()` is fully supported. See ROADMAP.md.
"""

from ...core.errors import NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from ._dispatch import ManglingType, RustDemangler, TypeNotFoundError
from ._legacy import UnableToLegacyDemangle
from ._v0 import UnableTov0Demangle

_DEMANGLER = RustDemangler()

#: Legacy Rust appends a hash component, `17h<16 hex digits>`, as the last element of the
#: path. Looking for it is what stops this plugin claiming every C++ symbol in a binary,
#: since the `_ZN` prefix alone does not distinguish the two.
_LEGACY_HASH_MARKER = "17h"
_LEGACY_HASH_DIGITS = 16


def detect(name):
    """Cheap test for a Rust mangled name.

    v0 is unambiguous: nothing else uses `_R`. Legacy shares Itanium's `_ZN` prefix, so
    it additionally requires the hash component Rust always emits -- without that check
    this plugin would claim every C++ symbol it was offered.

    The hash is looked for by its marker rather than at a fixed offset from the end,
    because real symbols carry things after it: a `.0` for a promoted constant, a
    `.llvm.<hash>` from LLVM's internaliser. Anchoring to the end missed every one of
    those, and the C++ demangler then claimed them and produced a plausible-looking but
    quite wrong spelling.
    """
    if not name:
        return False
    if name.startswith(("_R", "__R")):
        return True
    if not name.startswith(("_ZN", "__ZN")):
        return False
    marker = name.rfind(_LEGACY_HASH_MARKER)
    if marker < 0:
        return False
    start = marker + len(_LEGACY_HASH_MARKER)
    digits = name[start : start + _LEGACY_HASH_DIGITS]
    return (
        len(digits) == _LEGACY_HASH_DIGITS
        and _is_hex(digits)
        and name[start + _LEGACY_HASH_DIGITS : start + _LEGACY_HASH_DIGITS + 1] == "E"
    )


def _is_hex(text):
    return all(char in "0123456789abcdef" for char in text)


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=None):
    """Parse a Rust mangled name into `builder`."""
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    try:
        expanded = _DEMANGLER.demangle(mangled)
    except TypeNotFoundError as exc:
        raise NotMangledError(mangled, "not a Rust mangled name") from exc
    except (UnableTov0Demangle, UnableToLegacyDemangle) as exc:
        raise ParseError(mangled, None, str(exc)) from exc
    except RecursionError as exc:
        from ...core.errors import LimitExceeded

        raise LimitExceeded(mangled, "recursion depth", limits.max_depth) from exc
    if len(expanded) > limits.max_output:
        from ...core.errors import LimitExceeded

        raise LimitExceeded(mangled, "output length", limits.max_output)
    return builder.raw(expanded)


PLUGIN = LanguagePlugin(
    name="rust",
    detect=detect,
    parse=parse,
    description="Rust legacy (_ZN) and v0 (_R) symbol mangling",
    aliases=("rs",),
    # Before Itanium: legacy Rust mangling *is* Itanium mangling, and only this plugin
    # knows how to strip the trailing hash and read the path correctly.
    # Safe now that core/decorations.py splits only the ELF version suffix. It must
    # never be taught to split on `.` again: `.` is Rust grammar -- legacy mangling
    # writes `..` for `::` and spells shims `{{vtable.shim}}` -- and rustc-demangle's
    # own suffix rule differs from GCC's anyway (cut after the mangled name's final `E`,
    # drop a `.llvm.<hash>`, append anything else verbatim). This scheme implements that
    # itself.
    symbol_table_decorations=True,
    priority=50,
)

register(PLUGIN)

__all__ = ["PLUGIN", "ManglingType", "RustDemangler", "detect", "parse"]
