"""Metrowerks CodeWarrior C++.

The other pre-Itanium C++ mangling, and a scheme of its own rather than a sixth style of
`gnuv2` because libiberty never read it: `cplus-dem.c` has no CodeWarrior flag,
`demangle-expected` has no vectors for it, and binutils has never demangled one. What
built with it: Nintendo GameCube and Wii titles, Palm OS, BeOS, classic Mac OS, and a
great deal of other embedded work -- which is why the reference is a decompilation
project's tool, `encounter/cwdemangle`, rather than a compiler vendor's.

It looks like the ARM encoding and is not. A template argument list is written out
*literally* in the symbol, `single_ptr<10CModelData>`, so finding where the name ends
means counting brackets before looking for the `__`; a pointer-to-member carries two
hidden parameters whose spelling says whether the member function is `const`; and the
type spelling is the reference's own -- `const char*`, not `char const *`.

Detection has the same problem `gnuv2` has and answers it the same way: a CodeWarrior
symbol is an ordinary C identifier with a `__` in it, so `detect` parses the whole name
rather than testing a prefix, and this scheme is offered second-to-last -- after
everything with a marker, and *before* `gnuv2`, which would otherwise read `__dt__6CActorFv`
as a function called `__dt` and be wrong about it. Measured over every corpus in
`tests/conformance/` and over the system's own C libraries: see `tests/test_codewarrior.py`,
which pins the count both ways.
"""

import re

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from . import nodes
from ._parser import CodeWarriorSymbol, DemangleFailure, demangle_codewarrior
from .options import DEFAULT_OPTIONS, CodeWarriorOptions

__all__ = [
    "DEFAULT_OPTIONS",
    "PLUGIN",
    "CodeWarriorOptions",
    "CodeWarriorSymbol",
    "demangle_codewarrior",
    "detect",
    "parse",
]

#: Longest name `detect` will parse; the reference's longest vector is 1,300 characters.
_DETECT_MAX = 4096

#: `<>,` for literal template arguments, `$`/`@` for local statics (`v$localstatic1$f`,
#: `@LOCAL@f@v`), `-` for negative template literals.
_SYMBOL_RE = re.compile(r"[A-Za-z0-9_<>,$@.-]+")

#: A `$` or `@` left in the output is a marker the demangler failed to consume.
_NAME_RE = re.compile(r"[A-Za-z0-9_~]+")


def _screen(name):
    """The cheap necessary conditions, so that `detect` parses almost nothing.

    Every name the reference reads has a `__` separating the name from the signature.
    The `__` test comes first because it is one C-level scan and rejects most of a real
    symbol table outright, and the character test only then.
    """
    if "__" not in name:
        return False
    return _SYMBOL_RE.fullmatch(name) is not None


def _components(name):
    """`name` split at the `::` between components, ignoring any inside brackets.

    `rstl::map<int, rstl::less<int>>::iterator` is three components, not five: the two
    `::` inside the template argument list separate nothing. Splitting on the string
    itself gets that wrong, which is what this is for.
    """
    depth = 0
    start = 0
    at = 0
    while at < len(name):
        character = name[at]
        if character in "<([":
            depth += 1
        elif character in ">)]":
            depth -= 1
        elif depth == 0 and character == ":" and name[at + 1 : at + 2] == ":":
            yield name[start:at]
            at += 2
            start = at
            continue
        at += 1
    yield name[start:]


def _plausible(symbol):
    """Whether what came out reads as a C++ name rather than as another scheme's bytes.

    The same bar `gnuv2` holds a reading to: the demangler can read a run of type letters
    out of anything, but it cannot turn someone else's encoding into an *identifier*.
    """
    parameters = symbol.parameters or ()
    if len(parameters) > 1 and any(parameter == "void" for parameter in parameters):
        # `void` is a parameter list only when it is the whole of it (as in `gnuv2`).
        return False
    name = symbol.qualified_name
    at = name.find("(")
    if at >= 0:
        name = name[:at]
    for component in _components(name):
        bare = component.split("<")[0].strip()
        if not bare:
            continue
        if bare.startswith("operator") or bare.startswith("__"):
            continue
        if not _NAME_RE.fullmatch(bare) or bare[0].isdigit():
            return False
    return True


def detect(name):
    """Whether `name` is a CodeWarrior symbol, decided by reading it.

    There is no prefix that says so, so the whole name is parsed and the answer is
    whether it parsed. Screened first, so that the great majority of symbols cost a
    substring search and nothing else.
    """
    # Every name the reference reads has a `__` in it.
    if "__" not in name:
        return False
    if len(name) > _DETECT_MAX or not _screen(name):
        return False
    try:
        symbol = demangle_codewarrior(name)
    except DemangleFailure:
        return False
    except RecursionError:
        return False
    return symbol.text != name and _plausible(symbol)


#: What each builder class answered to `_wants_structure`, asked once per class.
_STRUCTURED = {}


def _wants_structure(builder):
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
    """Parse a CodeWarrior symbol into `builder`."""
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)
    if options is None:
        options = DEFAULT_OPTIONS

    try:
        symbol = demangle_codewarrior(
            mangled,
            omit_empty_parameters=options.omit_empty_parameters,
            mw_extensions=options.mw_extensions,
        )
    except DemangleFailure as error:
        raise ParseError(mangled, None, str(error)) from error
    except RecursionError as error:
        raise LimitExceeded(mangled, "recursion depth", limits.max_depth) from error

    if symbol.text == mangled:
        raise ParseError(mangled, None, "the name decodes to itself")
    if len(symbol.text) > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)

    if _wants_structure(builder):
        return nodes.build(symbol)
    return builder.raw(symbol.text)


PLUGIN = LanguagePlugin(
    name="codewarrior",
    node_kinds=("name", "parameters", "symbol", "type"),
    detect=detect,
    parse=parse,
    description="Metrowerks CodeWarrior C++ symbol names",
    options_type=CodeWarriorOptions,
    aliases=("cw", "metrowerks", "mwcc"),
    symbol_table_decorations=True,
    # Behind `gnuv2` (290) so the commoner mangling wins where both read a name;
    # tests/test_core.py pins the order.
    priority=300,
)
"""The scheme as the registry holds it, registered when this package is imported."""

register(PLUGIN)
