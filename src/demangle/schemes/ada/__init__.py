"""Ada, as GNAT encodes it.

The last of the pre-Itanium formats libiberty still carries, reached as
`c++filt --format=gnat` and dropped from nothing: when the GNU v2, lucid, ARM and HP
styles were removed from the default, `gnat` stayed. Narrow but concentrated -- avionics,
rail, defence -- and the reference is `ada_demangle` in `libiberty/cplus-dem.c`, with
GCC's own `exp_dbug.ads` documenting the encoding normatively.

**Detection is the whole difficulty, and it was measured rather than argued.** An Ada
symbol carries no types and no marker: `yz__qrs` is a package and a subprogram, and it is
also exactly what a C program writes. Parsing the name and claiming whatever parses --
what a demangler for a scheme *with* a marker can afford -- reads 797 names from the other
schemes' corpora and **6,764 real symbols** from this machine's own libraries as Ada. That
is not detection, it is a coin toss with a confident voice.

So a name is claimed only when it carries something GNAT wrote *and a C compiler would
not*: the `_ada_` prefix, an `O`-operator, a `TK` task suffix, a `P`/`N` protected
subprogram, a stream `S[RWIO]`, a controlled `D[FA]`, an `X` body-nested marker, a
`___elabb`-style special name, a `_B`/`_E` entry body, or an overload number -- *and* the
whole name has to be accounted for, because several of the reference's suffixes stop
reading and abandon the rest. Measured under that rule: **0** claims over every other
checked-in corpus, and **0** over 339,117 symbols from this machine's libraries.

The cost is that a name with no such marker -- `yz__qrs`, and four of the 34
reference vectors -- is not auto-detected (`x_E` is not counted: the reference itself
declines it). It demangles when a caller says `language="ada"`, which
is the same bargain the Go scheme makes and for the same reason: failing to claim a name
returns it unchanged, which is what an unreadable name does anyway, while claiming
someone else's rewrites it into a plausible lie. `tests/test_ada.py` pins both numbers,
so neither can drift quietly.
"""

import re

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from . import nodes
from ._parser import _LIBRARY_PREFIX, AdaSymbol, DemangleFailure, demangle_ada

__all__ = ["PLUGIN", "AdaSymbol", "demangle_ada", "detect", "parse"]

#: Longer names are not offered to the parser; real GNAT symbols run to a few hundred.
_DETECT_MAX = 4096

#: A necessary condition for any of the evidence `detect` requires: `_ada_`, an
#: upper-case suffix marker, `___`, an overload number or a nested subprogram. Screening
#: on the character set instead let every lower-case C symbol through to a full parse.
_MAY_CARRY_EVIDENCE = re.compile(r"_ada_|___|[A-Z]|__[0-9]|\.[0-9]")

#: What may follow a suffix that ends the parse: only an overload number (`__`, digits,
#: optional `X[nb]*`). Otherwise `rDF16_` (Itanium `_Float16 restrict`) would be claimed
#: as `r.Finalize` with `16_` to spare.
_TRAILING_OVERLOAD = re.compile(r"__\d+(_\d+)*(X[nb]*)?")


#: What `detect` needs to see, for the registry to screen on without calling it: a `__`,
#: or the library-level `_ada_` opening. See `core.registry._screened`.
DETECT_SCREEN = (("__",), (_LIBRARY_PREFIX,))


def detect(name):
    """Whether `name` is a GNAT symbol, decided by reading it *and* by what it carries.

    Two conditions, and the second is the one that matters. The name has to parse, and
    it has to hold at least one encoding GNAT writes and a C compiler does not -- see
    the module docstring for why, and for what the alternative measured at.
    """
    # Cheapest reject first: a GNAT name holds a `__` or starts `_ada_` (all 34 of the
    # reference's vectors have a `__`).
    if "__" not in name and not name.startswith(_LIBRARY_PREFIX):
        return False
    if not name or len(name) > _DETECT_MAX:
        return False
    library = name.startswith(_LIBRARY_PREFIX)
    head = name[5:6] if library else name[:1]
    if not ("a" <= head <= "z"):
        return False
    if not _MAY_CARRY_EVIDENCE.search(name):
        return False
    try:
        symbol = demangle_ada(name)
    except DemangleFailure:
        return False
    if not symbol.evidence:
        return False
    # `parse` stays faithful to the reference and tolerates trailing text; claiming such
    # a name is decided here.
    return not symbol.unread or _TRAILING_OVERLOAD.fullmatch(symbol.unread) is not None


#: `_wants_structure`'s answer per builder class, asked once per class.
_STRUCTURED = {}


def _wants_structure(builder):
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=None):
    """Parse a GNAT symbol into `builder`.

    No second detection test. `parse` is reached either because `detect` claimed the
    name or because a caller named the language, and in the second case the caller has
    said what it is -- which is how `yz__qrs`, correctly not auto-detected, is still
    readable on request.
    """
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)

    try:
        symbol = demangle_ada(mangled)
    except DemangleFailure as error:
        raise NotMangledError(mangled, str(error)) from error

    if len(symbol.text) > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)

    if _wants_structure(builder):
        return nodes.build(symbol)
    return builder.raw(symbol.text)


PLUGIN = LanguagePlugin(
    name="ada",
    node_kinds=("attribute", "component", "symbol"),
    detect=detect,
    parse=parse,
    description="Ada symbol names as GNAT encodes them (c++filt --format=gnat)",
    aliases=("gnat",),
    symbol_table_decorations=True,
    # Lower is offered first: ahead of `gnuv2`, which reads `p__taskobjTKB` as a wrong
    # C++ name, and behind everything with a prefix. tests/test_ada.py pins the order.
    priority=280,
)
"""The scheme as the registry holds it, registered when this package is imported."""

register(PLUGIN)
