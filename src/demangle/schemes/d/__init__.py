"""D mangled names.

Every `_D` symbol is unambiguously D's: no other scheme here uses that prefix, and the
grammar requires a type after the path, so a name that merely starts `_D` but is not one
is refused rather than guessed at.

Conformance is 100% against GNU binutils' D demangler (`c++filt --format=dlang`) over
every symbol it can read in the shipped `libgphobos` and `libgdruntime` -- 16,333 of
19,315 -- with nothing mis-spelled and nothing refused among them. See `_parser.py` for
where the grammar comes from and which of its rules had to be settled by measurement.
"""

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from . import nodes
from ._parser import DemangleFailure, DSymbol, parse_d_symbol

#: What each builder class answered to `_wants_structure`, asked once per class.
_STRUCTURED = {}


def detect(name):
    """`_D` and something after it.

    Narrower than it looks: a D symbol's path components are length-prefixed, so the
    character after `_D` is a digit for every name a compiler emits. That keeps this from
    claiming an ordinary C identifier that happens to begin `_D`.
    """
    return bool(name) and name.startswith("_D") and len(name) > 2 and name[2].isdigit()


def _wants_structure(builder):
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=None):
    """Parse a D mangled name into `builder`."""
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    if not mangled.startswith("_D"):
        raise NotMangledError(mangled, "not a D mangled name")
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)

    try:
        symbol = parse_d_symbol(mangled)
    except DemangleFailure as error:
        raise ParseError(mangled, None, str(error)) from error
    except RecursionError as error:
        raise LimitExceeded(mangled, "recursion depth", limits.max_depth) from error

    if len(symbol.text) > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)

    if _wants_structure(builder):
        return nodes.build(symbol)
    return builder.raw(symbol.text)


PLUGIN = LanguagePlugin(
    name="d",
    detect=detect,
    parse=parse,
    description="D symbol mangling (dlang)",
    aliases=("dlang",),
    # Above Go, whose detection is a shape test, and below the schemes with their own
    # unambiguous prefixes. `_D` collides with nothing here.
    priority=40,
)

register(PLUGIN)

__all__ = ["PLUGIN", "DSymbol", "detect", "parse", "parse_d_symbol"]
