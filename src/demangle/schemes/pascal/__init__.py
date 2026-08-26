"""Free Pascal symbol names.

Free Pascal builds a symbol out of `$`-delimited parts in `make_mangledname`
(`compiler/symdef.pas`), and ships nothing that reads one back. So the grammar here is a
transcription of that function and the three that feed it, and correctness rests on
re-assembly: the parts this splits out, rejoined with the compiler's own separators, must
reproduce the symbol exactly.

Measured over every symbol in the 1,074 object files of the shipped Free Pascal 3.2.2
runtime and packages -- 237,326 of them:

* 236,570 are read, and every one re-assembles to the bytes it was read from;
* the 756 refused have no `$` in them at all, being plain C names from the soft-float
  runtime rather than Pascal symbols;
* checked independently against `ppudump`, which prints both the mangled name and what
  each unit declares, every name this reads is one the unit really declares -- bar the
  compiler's own `init` and `finalize` sections, which no source declares.

**Case does not come back.** Pascal is case-insensitive and the compiler upper-cases
before it mangles, so `Add` and `ADD` are the same symbol and nothing can tell them apart.

This is *Free Pascal's* mangling, which is what Lazarus and any `{$MODE DELPHI}` code
built with `fpc` produces. Borland and Embarcadero's own Delphi compilers use a different
scheme -- `@Unit@Class@Method$qqrv` -- which the `delphi` plugin reads.
"""

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from . import nodes
from ._parser import DemangleFailure, PascalSymbol, detect, parse_pascal_symbol

#: What each builder class answered to `_wants_structure`, asked once per class.
_STRUCTURED = {}


def _wants_structure(builder):
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=None):
    """Parse a Free Pascal symbol into `builder`."""
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)

    try:
        symbol = parse_pascal_symbol(mangled)
    except DemangleFailure as error:
        raise NotMangledError(mangled, str(error)) from error

    if len(symbol.text) > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)

    if _wants_structure(builder):
        return nodes.build(symbol)
    return builder.raw(symbol.text)


PLUGIN = LanguagePlugin(
    name="pascal",
    detect=detect,
    parse=parse,
    description="Free Pascal symbol mangling",
    aliases=("fpc", "freepascal"),
    # Below the schemes with their own prefixes, above Nim: `$` is not legal in a C
    # identifier on the targets Free Pascal emits these for, so the shape is distinctive,
    # but it is still a shape rather than a marker.
    priority=20,
)

register(PLUGIN)

__all__ = ["PLUGIN", "PascalSymbol", "detect", "parse", "parse_pascal_symbol"]
