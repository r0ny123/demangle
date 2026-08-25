"""Microsoft's decorated name scheme.

The format `UnDecorateSymbolName` reverses, emitted by MSVC and by clang-cl. Microsoft
publishes no complete grammar for it, so this implementation was derived by probing
`llvm-undname` and validated against LLVM's own demangler corpus; the reference output
is recorded next to every name in tests/conformance.

Structured output
-----------------
This parser predates the builder protocol -- it was proven in production before this
package existed -- and does not write to a builder. It cannot: MSVC spells a declaration
differently enough that the shared spelling builder has nowhere to put a calling
convention, and the alternative to a scheme-specific renderer would be a scheme-specific
branch in `core`.

What it does instead is build its own tree, of nodes that are `core.ast.Node`s, and
spell that (see `nodes.py`). So `parse()` hands back a real tree to walk and `demangle()`
hands back text, exactly as they do for the other schemes -- the seam is which of the two
this module produces, rather than which builder the parser wrote to.
"""

from ...core.ast import Node
from ...core.errors import NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from ._parser import demangle_msvc_symbol, parse_msvc_symbol

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


#: What each builder class answered to `_wants_structure`. Asked once per class rather
#: than once per name: the answer is a property of the builder's type, and this question
#: is on the path every symbol takes.
_STRUCTURED = {}


def _wants_structure(builder):
    """Whether `builder` is collecting a tree rather than text.

    The protocol has no flag for this, and adding one would mean changing `core` for the
    sake of one scheme. It does not need one: the two builders already answer the question
    by what they hand back. `AstBuilder` returns `Node`s and `SpellingBuilder` returns
    `Spelling`s, so asking either for the cheapest thing it makes says which it is.

    A builder this module has never seen -- a third-party one emitting JSON, say -- gets
    text unless its products are `Node`s, because text is the answer every builder can
    use and a tree of this scheme's nodes is not.
    """
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


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
    if _wants_structure(builder):
        tree = parse_msvc_symbol(mangled)
        if tree is None:
            raise ParseError(mangled, None, "not a decorated name this demangler can read")
        _check_length(mangled, tree.spell(), limits)
        return tree
    expanded = demangle_msvc_symbol(mangled)
    if expanded == mangled:
        raise ParseError(mangled, None, "not a decorated name this demangler can read")
    _check_length(mangled, expanded, limits)
    return builder.raw(expanded)


def _check_length(mangled, spelled, limits):
    if len(spelled) > limits.max_output:
        from ...core.errors import LimitExceeded

        raise LimitExceeded(mangled, "output length", limits.max_output)


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
