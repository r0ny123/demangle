"""Microsoft's decorated name scheme.

The format `UnDecorateSymbolName` reverses, emitted by MSVC and by clang-cl. Microsoft
publishes no complete grammar for it, so this implementation was derived by probing
`llvm-undname` and validated against LLVM's own demangler corpus; the reference output
is recorded next to every name in tests/conformance.

Structured output
-----------------
This parser predates the builder protocol -- it was proven in production before this
package existed -- and still assembles text directly. It is exposed here behind the
same plugin interface as everything else, so `demangle()` is fully supported, but
`parse()` currently yields a single `Raw` node rather than a tree.

Converting it to the builder protocol is the highest-priority item in ROADMAP.md. The
public API does not change when that lands; only the shape of what `parse()` returns
becomes richer.
"""

from ...core.errors import NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from ._parser import demangle_msvc_symbol

#: MSVC replaces a decorated name too long for the linker with an MD5 hash of it,
#: written `??@<hash>@`. Nothing can be recovered -- the original spelling is simply not
#: in the symbol -- so the demangled form of such a name is the name itself, which is
#: what `llvm-undname` prints for it.
_MD5_PREFIX = "??@"

#: The one thing that may follow an MD5 name and still belong to it: the RTTI complete
#: object locator tag. Probing `llvm-undname` shows every other trailing sequence --
#: `??_R0@` through `??_R5@`, `??_C@`, `??_7@`, arbitrary text -- being dropped, and only
#: `??_R4@` kept.
_MD5_RTTI_SUFFIX = "??_R4@"


def _md5_name(mangled):
    """Expand `??@<hash>@`, or None when it is not one.

    MSVC replaces a decorated name too long for the linker with an MD5 hash of it. The
    original spelling is not in the symbol at all, so the expansion is the name itself --
    truncated at the closing `@`, because anything after it is not part of the hashed
    name.
    """
    end = mangled.find("@", len(_MD5_PREFIX))
    if end < 0:
        return None
    base = mangled[: end + 1]
    if mangled.startswith(_MD5_RTTI_SUFFIX, end + 1):
        return base + _MD5_RTTI_SUFFIX
    return base


def detect(name):
    """Every decorated name the scheme produces opens with `?`."""
    return bool(name) and name[0] == "?"


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=None):
    """Parse an MSVC decorated name into `builder`."""
    if not detect(mangled):
        raise NotMangledError(mangled, "not an MSVC decorated name")
    # A decorated name is read from a NUL-terminated string of source-legal characters
    # and cannot hold a control character. One that does is refused outright rather than
    # copied into the output, where it would travel on into a caller's report.
    if any(char < " " or char == "\x7f" for char in mangled):
        raise ParseError(mangled, None, "decorated name contains a control character")
    if mangled.startswith(_MD5_PREFIX):
        # A hashed name carries no recoverable spelling, so it is its own expansion.
        # This is a successful parse, not a failure: there is nothing more to say about
        # the symbol, and the reference demangler agrees.
        hashed = _md5_name(mangled)
        if hashed is None:
            raise ParseError(mangled, None, "unterminated MD5-hashed name")
        return builder.raw(hashed)
    expanded = demangle_msvc_symbol(mangled)
    if expanded == mangled:
        raise ParseError(mangled, None, "not a decorated name this demangler can read")
    if len(expanded) > limits.max_output:
        from ...core.errors import LimitExceeded

        raise LimitExceeded(mangled, "output length", limits.max_output)
    return builder.raw(expanded)


PLUGIN = LanguagePlugin(
    name="msvc",
    detect=detect,
    parse=parse,
    description="Microsoft Visual C++ decorated names (MSVC, clang-cl)",
    aliases=("microsoft", "ms", "vc"),
    priority=100,
)

register(PLUGIN)

__all__ = ["PLUGIN", "detect", "parse"]
