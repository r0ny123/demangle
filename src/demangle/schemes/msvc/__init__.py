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
