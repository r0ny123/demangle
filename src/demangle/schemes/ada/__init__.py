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
reading and abandon the rest. Measured under that rule: **0** claims over the 81,457
names in every other corpus here, and **0** over 339,117 symbols from this machine's
libraries.

The cost is that a name with no such marker -- `yz__qrs`, and 5 of the 34 reference
vectors -- is not auto-detected. It demangles when a caller says `language="ada"`, which
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

#: Above this a name is not offered to the parser at all. `detect` runs the whole parse,
#: and an Ada name is a path of identifiers: the longest of the reference's own vectors
#: is 54 characters, and a GNAT symbol from a real program runs to a few hundred at most.
_DETECT_MAX = 4096

#: A *necessary* condition for carrying any of the evidence `detect` requires, and the
#: screen that keeps this scheme off the hot path. Every encoding in the evidence set
#: needs one of these five: the `_ada_` prefix; an upper-case letter, which is what every
#: suffix marker is (`O`perator, `TK`, `P`/`N`, `S[RWIO]`, `D[FA]`, `X`, `_B`/`_E`); the
#: `___` that introduces a special name; the `__` and digit of an overload number; or the
#: `.` and digit of a nested subprogram.
#:
#: This matters more than it looks. `detect` runs on every symbol a caller offers, most
#: of which are not mangled at all, and an ordinary lower-case C identifier -- `strlen`,
#: `foo_bar` -- matches none of these and is rejected by one C-level scan. Screening on
#: the *character set* instead, which is the obvious thing to do, let every lower-case
#: symbol through to a full parse and cost 5.4x on the negative benchmark.
_MAY_CARRY_EVIDENCE = re.compile(r"_ada_|___|[A-Z]|__[0-9]|\.[0-9]")

#: What may follow a suffix that ends the parse. Several of the reference's suffixes
#: `break` out of its loop and abandon the rest of the name -- `...controllerDF__2` is
#: `....Finalize` with the `__2` dropped -- so a name can be read to the end of its
#: meaning with characters to spare. An overload number is the only thing that ever
#: follows in the reference's own vectors, and requiring that is what stops
#: `rDF16_` (the Itanium encoding of `_Float16 restrict`, which really does parse as
#: `r.Finalize` with `16_` left over) from being claimed as an Ada symbol.
#: exactly the shape the reference's own overload-number branch reads: `__`, digits,
#: further `_`-separated digits, then an optional `X` body-nested marker with its run of
#: `n`s and `b`s. `ada__..._events___alignment__2Xnn` ends in one of these.
_TRAILING_OVERLOAD = re.compile(r"__\d+(_\d+)*(X[nb]*)?")


def detect(name):
    """Whether `name` is a GNAT symbol, decided by reading it *and* by what it carries.

    Two conditions, and the second is the one that matters. The name has to parse, and
    it has to hold at least one encoding GNAT writes and a C compiler does not -- see
    the module docstring for why, and for what the alternative measured at.
    """
    # The cheapest possible reject, inline and first, because this runs on every symbol
    # a caller offers and the great majority are not Ada. A GNAT name is a *qualified*
    # one -- the `__` between a package and what it contains -- or else it is a library
    # level subprogram and carries `_ada_`. All 34 of the reference's vectors have a
    # `__`. An unqualified name with a suffix marker and no `__` at all would be missed
    # here, and reads under `language="ada"`; nothing observed writes one.
    if "__" not in name and not name.startswith(_LIBRARY_PREFIX):
        return False
    if not name or len(name) > _DETECT_MAX:
        return False
    # The cheapest reject first, inline: this is called on every symbol offered to the
    # library, and the great majority are not Ada. A GNAT name is lower-case at the
    # front (after any `_ada_`) and holds a `__`, a `.` or one of the suffix letters --
    # but the run of allowed characters is the test that rejects fastest.
    library = name.startswith(_LIBRARY_PREFIX)
    head = name[5:6] if library else name[:1]
    if not ("a" <= head <= "z"):
        return False
    # Then the full necessary condition. Reached only by a name that already holds a
    # `__` or a `_ada_`, so the alternation's cost -- 0.49us per name, against 0.03us
    # for the membership test above -- is paid by very few.
    if not _MAY_CARRY_EVIDENCE.search(name):
        return False
    try:
        symbol = demangle_ada(name)
    except DemangleFailure:
        return False
    if not symbol.evidence:
        return False
    # Fully accounted for, or with nothing left but an overload number. `parse` stays
    # faithful to the reference and reads a name with anything else trailing; claiming
    # one is a different decision, and this is where it is made.
    return not symbol.unread or _TRAILING_OVERLOAD.fullmatch(symbol.unread) is not None


#: What each builder class answered to `_wants_structure`, asked once per class: the
#: answer is a property of the builder's type and this is on every symbol's path.
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
    # `priority` is ascending: *lower is offered first*. Ahead of the two pre-Itanium C++
    # schemes and behind everything with a marker of its own. Ahead of those two because
    # a GNAT name carrying a `__` is one `gnuv2` will happily read as a C++ function --
    # `p__taskobjTKB` becomes `taskobj(...)` there, a wrong name rather than no name --
    # and behind everything else because this scheme's evidence test, strict as it is,
    # is still weaker than a prefix. tests/test_ada.py pins the order.
    priority=280,
)
"""The scheme as the registry holds it, registered when this package is imported."""

register(PLUGIN)
