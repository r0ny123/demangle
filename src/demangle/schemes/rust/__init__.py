"""Rust symbol mangling: the legacy `_ZN` scheme and the v0 `_R` scheme.

Rust has used two schemes. The legacy one wraps a hashed path in Itanium mangling, so a
legacy Rust symbol is also a valid Itanium symbol -- which is why this plugin is offered
names first. The v0 scheme, specified in RFC 2603 and stabilised behind
`-Csymbol-mangling-version=v0`, is a scheme of its own.

Derived from Team bi0s' rust_demangler (MIT), with both grammars substantially reworked.
See NOTICE.

Structured output
-----------------
`parse()` returns a tree: a `symbol` holding a `path` of `name` components, with `impl`,
`template`, `type` and `literal` nodes for what a path carries. See `nodes.py` for why
the tree cannot spell a symbol differently from `demangle()`.
"""

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError, ParseError
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


#: What each builder class answered to `_wants_structure`, asked once per class rather
#: than once per name: the answer is a property of the builder's type, and this question
#: is on the path every symbol takes.
_STRUCTURED = {}


def _wants_structure(builder):
    """Whether `builder` is collecting a tree rather than text.

    The same test the MSVC scheme makes, and for the same reason: the protocol has no
    flag for this and the two builders already answer by what they hand back. A builder
    this module has never seen gets text unless its products are `Node`s, because text
    is the answer every builder can use.
    """
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=None):
    """Parse a Rust mangled name into `builder`."""
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    if _wants_structure(builder):
        tree = _guard(mangled, limits, _DEMANGLER.structure)
        _check_length(mangled, tree.size, limits)
        return tree
    expanded = _guard(mangled, limits, _DEMANGLER.demangle)
    _check_length(mangled, len(expanded), limits)
    return builder.raw(expanded)


def _guard(mangled, limits, demangle_with):
    """Run one of the demanglers, translating its errors into this package's.

    The two entry points fail in exactly the same ways -- they are the same parser --
    so the translation lives here rather than twice.
    """
    try:
        return demangle_with(mangled)
    except TypeNotFoundError as exc:
        raise NotMangledError(mangled, "not a Rust mangled name") from exc
    except (UnableTov0Demangle, UnableToLegacyDemangle) as exc:
        raise ParseError(mangled, None, str(exc)) from exc
    except RecursionError as exc:
        raise LimitExceeded(mangled, "recursion depth", limits.max_depth) from exc


def _check_length(mangled, length, limits):
    if length > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)


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
    # `_R`, `__R`, `_ZN` and `__ZN` are the only starts `detect` accepts.
    first_characters="_",
    priority=50,
)

register(PLUGIN)

__all__ = ["PLUGIN", "ManglingType", "RustDemangler", "detect", "parse"]
