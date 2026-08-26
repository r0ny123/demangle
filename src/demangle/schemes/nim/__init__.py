"""Nim symbol names.

Nim compiles to C, so a Nim symbol is an ordinary C identifier and there is no prefix to
key on. Nothing in the Nim toolchain reads one back either, so the grammar here is a
transcription of the compiler's `mangleutils.mangle` and `modulegraphs.uniqueModuleName`,
and the correctness argument is the one Go's uses: what this reads must re-mangle to the
bytes the compiler wrote.

Measured, over the two shipped Nim compilers (1.6.14 and 2.2.0):

* every routine name declared in either standard library -- 5,946 -- comes back exactly,
  bar 8, and all 8 are the same documented loss (see `_parser.py`);
* every module path in either standard library -- 304 and 310 -- comes back exactly;
* over 2,000 symbols the compilers actually emitted, checked against the name each
  recorded in its own `.ndi` debug-mapping file.

Detection is the whole parse rather than a shape test, because there is nothing else to
test: a reading has to exist that re-mangles to the symbol. Over 59,000 symbols from the
other schemes' corpora and from real C, C++, D and Pascal binaries, that claims none of
them.
"""

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from . import nodes
from ._parser import DemangleFailure, NimSymbol, detect, parse_nim_symbol

#: What each builder class answered to `_wants_structure`, asked once per class.
_STRUCTURED = {}


def _wants_structure(builder):
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=None):
    """Parse a Nim symbol into `builder`."""
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)

    try:
        symbol = parse_nim_symbol(mangled)
    except DemangleFailure as error:
        raise NotMangledError(mangled, str(error)) from error

    if len(symbol.text) > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)

    if _wants_structure(builder):
        return nodes.build(symbol)
    return builder.raw(symbol.text)


PLUGIN = LanguagePlugin(
    name="nim",
    detect=detect,
    parse=parse,
    description="Nim symbol mangling",
    aliases=(),
    # `priority` is ascending: *lower is offered first*. Offered early, alongside Go, and
    # its `detect` carries the weight instead: this scheme has no prefix of its own and
    # has to recognise a whole name, so the predicate is what keeps it off other
    # schemes' symbols. tests/test_core.py pins the order against every corpus.
    priority=10,
)

register(PLUGIN)

__all__ = ["PLUGIN", "NimSymbol", "detect", "parse", "parse_nim_symbol"]
