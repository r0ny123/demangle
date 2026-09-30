"""Pre-Itanium C++: GNU g++ before 3.0, cfront/ARM, Lucid, HP aCC and EDG.

Five manglings, one demangler, because that is how libiberty implements them: the same
2,500 lines of `cplus-dem.c` under five style flags. See `_parser.py` for the port and
for what is measured against the reference's own vectors.

Detection is the hard part, and the reason this scheme is offered last. A GNU v2 symbol
is an ordinary C identifier with a `__` somewhere in it -- `AtEnd__13ivRubberGroup` is a
name a C compiler would have accepted -- so there is no prefix to key on, and a scheme
that guessed would rewrite other people's symbols into plausible lies. Three things keep
it honest:

* it is offered *after* every other scheme, Itanium included, so it only ever sees names
  nothing else claimed;
* `detect` runs the whole parse rather than a shape test, and a name that does not parse
  end to end is not claimed;
* a reading that decodes nothing -- one where the demangler consumed the name and gave
  back what it was given -- is refused, because that is not evidence of anything.

Measured over every corpus in `tests/conformance/` and over the symbol tables of the
system's own C libraries: see `tests/test_gnuv2.py`, which pins the count both ways.

A caller who knows which compiler built the binary should say so, with
`GnuV2Options(style=...)`: the five styles read the same bytes differently and the name
does not say which one wrote it. `style="gnu"` is the default, being the one most likely
to be met.
"""

import re

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from . import nodes
from ._parser import STYLES, DemangleFailure, GnuV2Symbol, demangle_gnuv2
from .options import DEFAULT_OPTIONS, GnuV2Options

__all__ = [
    "DEFAULT_OPTIONS",
    "PLUGIN",
    "STYLES",
    "GnuV2Options",
    "GnuV2Symbol",
    "demangle_gnuv2",
    "detect",
    "parse",
]

#: Longest name `detect` will parse: the parse backtracks once per `__`, so the cost is
#: quadratic. Real names stay under 500; a caller who names the language is not screened.
_DETECT_MAX = 1024


#: `$` and `.` are g++'s scope markers; `<>#,*&` are HP aCC's literal specialisation
#: arguments (`Spec<#1,#1.*>`). Nothing else, which keeps Borland `@Class@method$qqs` out.
_SYMBOL_RE = re.compile(r"[A-Za-z0-9_$.<>#,*&]+")

#: Not `$` or `.`: those are mangled scope markers, so one left in the output is not a name.
_NAME_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")

#: What may follow a leading `__` in a name with no second one: `__vt_`, `__thunk_`,
#: `__ti`/`__tf`, and a constructor's `__Q`, `__K`, `__H` or `__t`.
_SPECIAL_OPENINGS = frozenset("tvQKH")

#: Names written by a compiler this style cannot read. See `_plausible`.
_FOREIGN_MARKERS = frozenset({"__ct", "__dt", "__vt", "__RTTI"})

#: Evidence that a *name* was decoded rather than a run of type letters.
_NAMED_SOMETHING = frozenset({"class", "qualified", "template"})

#: What a reading records when its argument list opened with no `F` and nothing before it.
_UNMARKED_ONLY = frozenset({"unmarked"})


#: What `detect` needs to see, for the registry to screen on without calling it: a `__`,
#: or the special forms' `_` opening with a `$` or `.` after it. See
#: `core.registry._screened`.
DETECT_SCREEN = (("__",), (("_", ("$", ".")),))


def _screen(name):
    """The cheap necessary conditions, so that `detect` parses almost nothing.

    Every name the reference reads reaches its signature one of two ways: through a `__`
    that separates the name from it, or through one of the GNU special forms, all of
    which begin with `_` and carry a `$` or a `.`. A name with neither cannot be one of
    these, and neither can one holding a character no assembler of the period would have
    put in a symbol, so both are declined without being parsed.

    Order matters here rather than only reading well. This runs on *every* symbol a
    caller offers -- the great majority of which are not mangled at all -- and the
    substring tests are one C-level scan each, while the character test walks the string
    a character at a time. Doing the character test first cost 1.4x on a symbol table of
    ordinary C names, none of which it could have rejected any earlier.
    """
    if "__" not in name and not (name[0] == "_" and ("$" in name or "." in name)):
        return False
    if ("<" in name or ">" in name) and "#" not in name:
        # Only HP aCC writes a literal argument list, and it always carries a `#`; any
        # other bracket is CodeWarrior's.
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

    The demangler will read `Java_..._write__J` as a call taking a `__complex`, and a
    Swift symbol's tail as a parameter list, because a run of fundamental-type letters is
    exactly what those look like. What it cannot do is turn them into a *name*: the
    entity's own name comes back holding characters -- a `$`, a `.`, a `@` -- that no C++
    identifier has, or a component that is not an identifier at all.

    A name reached through one of the special forms is exempt, because those carry a
    marker of their own -- `_$_`, `__vt_`, `__ti`, `__thunk_`, `_GLOBAL_$I$`, `__vtbl__` --
    and the marker is the evidence.
    """
    if "special" in symbol.evidence:
        # `_GLOBAL_$I$` with nothing keyed to it: a label on its own is not a reading.
        body = symbol.text
        if symbol.special and body.startswith(symbol.special):
            body = body[len(symbol.special) :]
        if symbol.suffix and body.endswith(symbol.suffix):
            body = body[: -len(symbol.suffix)]
        return bool(body.strip())
    if "::)" in symbol.text:
        # `int (CGuiWidget::)(...)`: CodeWarrior's `M<class>F` member pointer, not a declaration.
        return False
    parameters = symbol.parameters or ()
    if any("int0_t" in parameter for parameter in parameters):
        # `int0_t` is the reference's output for `I` without hex (`INT` in
        # `g_cclosure_marshal_VOID__INT`); `language="gnuv2"` keeps it, detection does not.
        return False
    if len(parameters) > 1 and any(parameter == "void" for parameter in parameters):
        # `void` is a parameter list only when it is the whole of it (`PyInit__csv`).
        return False
    if symbol.evidence == _UNMARKED_ONLY:
        # `name__<types>` with no `F`, no class and nothing else decoded: g++ 2.x marks
        # every free function with `F`, so this is a C name that ends in type letters
        # (`PyInit__sre`, `drm_intel_gem_bo_map__wc`).
        return False
    if symbol.qualifiers and not (symbol.evidence & _NAMED_SOMETHING):
        # Member qualifiers without a class: an `S` or `C` in a foreign encoding (`_TtU__FQD__Si`).
        return False
    for component in _components(symbol.qualified_name):
        at = component.find("<")
        if at >= 0:
            component = component[:at]
        component = component.lstrip("~")
        if not component or component == "{anonymous}":
            continue
        if component.startswith("operator"):
            continue
        if component in _FOREIGN_MARKERS:
            # ARM/CodeWarrior markers: the reference reads them under `--format=arm` and as a
            # wrong ordinary name under `--format=gnu`. Naming the style still gets them.
            return False
        if not _NAME_CHARACTERS.issuperset(component) or component[0].isdigit():
            return False
    return True


def _reads(name, style):
    try:
        return demangle_gnuv2(name, style=style)
    except DemangleFailure:
        return None
    except RecursionError:
        return None


def _decoded(name, symbol):
    """Whether the reading actually decoded something, rather than echoing the input.

    `demangle_prefix` has a path that appends the rest of the name verbatim, and a
    "successful" demangling that gives back the bytes it was given is not evidence that
    the name was mangled at all. Refusing those is stricter than the reference, and
    deliberately: this library offers the name to every scheme, so a claim has to mean
    something.
    """
    text = symbol.text
    return bool(text) and text != name


def detect(name, style="gnu"):
    """Whether `name` is a pre-Itanium C++ symbol, decided by reading it.

    There is no prefix that says so, so the whole name is parsed and the answer is
    whether it parsed. Screened first, so that the great majority of symbols cost a
    substring search and nothing else.
    """
    # The cheap necessary condition from `_screen`, inline, before any call.
    if "__" not in name and not (name[:1] == "_" and ("$" in name or "." in name)):
        return False
    if name.startswith(("_Z", "__Z")):
        # Itanium refusals fall through here, and `__Z...` is full of `__` separators;
        # no g++ 2.x name opens with `_Z`.
        return False
    if name.startswith(("_R", "__R")) and name[name.index("R") + 1 : name.index("R") + 2].isupper():
        # Rust v0 refusals, likewise: `_R` and a capital is a name reserved to the
        # implementation, and every one met here was a damaged Rust symbol.
        return False
    if (
        name[:2] == "__"
        and style == "gnu"
        and name[2:3].isalpha()
        and name[2] not in _SPECIAL_OPENINGS
        and name.find("__", 3) < 0
        and not name.startswith("__imp_")
    ):
        # `__libc_start_main`: after `__` and a letter that opens no special form,
        # constructor or DLL import, g++ 2.x's reading needs a second `__` to split at.
        return False
    if len(name) > _DETECT_MAX or not _screen(name):
        return False
    symbol = _reads(name, style)
    return symbol is not None and _decoded(name, symbol) and _plausible(symbol)


#: What each builder class answered to `_wants_structure`, asked once per class.
_STRUCTURED = {}


def _wants_structure(builder):
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
    """Parse a pre-Itanium C++ name into `builder`."""
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)
    if options is None:
        options = DEFAULT_OPTIONS

    try:
        symbol = demangle_gnuv2(mangled, style=options.style, params=options.params, ansi=options.ansi)
    except DemangleFailure as error:
        raise ParseError(mangled, None, str(error)) from error
    except RecursionError as error:
        raise LimitExceeded(mangled, "recursion depth", limits.max_depth) from error

    if not _decoded(mangled, symbol):
        # The same bar `detect` holds a name to.
        raise ParseError(mangled, None, "the name decodes to itself")
    if len(symbol.text) > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)

    if _wants_structure(builder):
        return nodes.build(symbol)
    return builder.raw(symbol.text)


PLUGIN = LanguagePlugin(
    name="gnuv2",
    node_kinds=("name", "parameters", "symbol", "type"),
    detect=detect,
    parse=parse,
    description="Pre-Itanium C++ (g++ 2.x, cfront/ARM, Lucid, HP aCC, EDG)",
    options_type=GnuV2Options,
    aliases=("gnu-v2", "cfront", "cplus-dem"),
    symbol_table_decorations=True,
    # Before `codewarrior` (300): `AtEnd__13ivRubberGroup` reads under both, and the
    # commoner mangling wins. tests/test_core.py pins the order.
    priority=290,
)
"""The scheme as the registry holds it, registered when this package is imported."""

register(PLUGIN)
