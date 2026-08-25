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

#: A legacy Rust symbol ends with the 17-character hash `17h<16 hex digits>E`. Checking
#: for it is what stops this plugin claiming every C++ symbol in a binary, since the
#: `_ZN` prefix alone does not distinguish them.
_LEGACY_HASH_LENGTH = 19


def detect(name):
    """Cheap test for a Rust mangled name.

    v0 is unambiguous: nothing else uses `_R`. Legacy shares Itanium's `_ZN` prefix, so
    it additionally requires the trailing hash component Rust always emits -- without
    that check this plugin would claim every C++ symbol it was offered.
    """
    if not name:
        return False
    if name.startswith(("_R", "__R")):
        return True
    if name.startswith(("_ZN", "__ZN")):
        body = name[:-1] if name.endswith("E") else name
        tail = body[-_LEGACY_HASH_LENGTH:]
        return len(tail) == _LEGACY_HASH_LENGTH and tail.startswith("17h") and _is_hex(tail[3:])
    return False


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
