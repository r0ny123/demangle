"""Borland/Embarcadero Delphi and C++Builder symbol names.

Delphi packages (BPLs) and C++Builder objects use the same mangling: `@Unit@Class@Method`
then `$` and a type encoding. Free Pascal's `$`-delimited scheme is a different one and
is not read here.

There is no Delphi compiler on the platforms this package is developed on, so the grammar
is a transcription of Embarcadero's own unmangler -- `unmangle.c` in the C++Builder RTL,
the same code `tdump -um` runs -- via the comments and control flow preserved in that
file. Spelling is what that unmangler prints, including the C++ `::` qualifier, because
that is the output a Delphi-built PE's exports are compared against by the toolchain.

`tests/conformance/delphi-tdump.txt` is a dump of the real `tdump.exe -q -um` over the
export tables of real BPLs and C++Builder DLLs; its expected column is what that
unmangler printed, and every readable entry is replayed. `delphi-real-world.txt` is a
per-kind sample of the same dump. Independently, a reading must consume the whole symbol
-- a parse that cannot account for the bytes is refused.
"""

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from . import nodes
from ._parser import DelphiSymbol, DemangleFailure, detect, parse_delphi_symbol

#: What each builder class answered to `_wants_structure`, asked once per class.
_STRUCTURED = {}


def _wants_structure(builder):
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=None):
    """Parse a Delphi/C++Builder symbol into `builder`."""
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)

    try:
        symbol = parse_delphi_symbol(mangled, limits)
    except DemangleFailure as error:
        raise NotMangledError(mangled, str(error)) from error

    if len(symbol.text) > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)

    if _wants_structure(builder):
        return nodes.build(symbol)
    return builder.raw(symbol.text)


PLUGIN = LanguagePlugin(
    name="delphi",
    node_kinds=("name", "parameters", "symbol"),
    detect=detect,
    parse=parse,
    description="Borland/Embarcadero Delphi and C++Builder symbol mangling",
    aliases=("borland", "bcc", "c++builder", "embarcadero"),
    # `@` is this scheme's own qualifier. MSVC 32-bit `__fastcall` C decoration is
    # `@name@N` with a decimal byte count; detection refuses that shape rather than
    # truncating at the first `@`.
    first_characters="@",
    # `priority` is ascending: *lower is offered first*. Before Swift, which also lists
    # `@` (for `@__swiftmacro_`) but whose detect is a prefix test that Delphi names
    # fail.
    priority=35,
)
"""The scheme as the registry holds it, registered when this package is imported."""

register(PLUGIN)

__all__ = ["PLUGIN", "DelphiSymbol", "detect", "parse", "parse_delphi_symbol"]
