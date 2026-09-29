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

#: Above this, a name is not offered to the parser at all. `detect` runs the whole parse,
#: and `iterate_demangle_function` backtracks once per `__` in the name, so the cost of
#: deciding is quadratic in the input rather than constant: `("a__" * 682)` takes 4 ms to
#: refuse, against microseconds for anything real. The longest vector in libiberty's own
#: test file is 122 characters, and the longest of these names in any binary measured
#: here is well under 500, so the bound costs nothing and caps the adversarial case at
#: about a millisecond. A caller who names the language is not screened.
_DETECT_MAX = 1024


#: What a name in one of these manglings is made of. A pattern rather than a set of
#: characters because this is on every symbol's path and `fullmatch` is one C-level scan
#: where `issuperset` walks the string a character at a time. `$` and `.` are in it
#: because they are the two characters g++ used as its scope marker, depending on what
#: the assembler would accept; `<>#,*&` because HP aCC wrote a template specialisation's
#: arguments into the symbol literally, as `Spec<#1,#1.*>`. Nothing else is, which is
#: what keeps the Borland `@Class@method$qqs...` family -- the one other scheme here
#: whose names would otherwise parse as a signature -- from ever reaching the parser.
_SYMBOL_RE = re.compile(r"[A-Za-z0-9_$.<>#,*&]+")

#: What a component of a demangled C++ name may be made of, once template arguments and
#: a destructor's `~` have been taken off it. Deliberately not `$` or `.`: those are
#: markers in the *mangled* name and the demangler turns them into `::`, so one left in
#: the output means what came out was not a name.
_NAME_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")

#: Names that mean the symbol was written by a compiler this style cannot read. See
#: `_plausible`.
_FOREIGN_MARKERS = frozenset({"__ct", "__dt", "__vt", "__RTTI"})

#: The evidence that a *name* was decoded, rather than a run of type letters: a
#: length-prefixed class, a `Q`-qualified name, or a template.
_NAMED_SOMETHING = frozenset({"class", "qualified", "template"})


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
        # None of these five compilers writes a template argument list into the symbol:
        # a template is encoded, as `t8BDDHookV1ZPc`. The one exception is HP aCC's
        # specialisation pseudo-arguments, `Spec<#1,#1.*>`, which carry a `#`. A bracket
        # without one means CodeWarrior wrote the name, and reading it here would give a
        # spelling with the argument list still mangled inside it.
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
        # `_GLOBAL_$I$` is the marker with nothing keyed to it, and `global constructors
        # keyed to ` names nothing. A label on its own is not a reading.
        body = symbol.text
        if symbol.special and body.startswith(symbol.special):
            body = body[len(symbol.special) :]
        if symbol.suffix and body.endswith(symbol.suffix):
            body = body[: -len(symbol.suffix)]
        return bool(body.strip())
    if "::)" in symbol.text:
        # A declarator with an empty pointer slot: `int (CGuiWidget::)(...)`. Nothing in
        # C++ spells that. It is what this reads out of CodeWarrior's pointer-to-member,
        # which writes `M<class>F` where these five write `PM<class>`, and a spelling
        # that cannot be a declaration is not a reading.
        return False
    parameters = symbol.parameters or ()
    if any("int0_t" in parameter for parameter in parameters):
        # `int0_t` is not a type. It is what the reference prints when `I` is followed by
        # something that is not hex: `demangle_fund_type` copies at most two characters,
        # runs `sscanf("%x")` over them and prints `int%u_t` whatever happened, so `INT`
        # comes out `int0_t` with the `NT` swallowed. `_hex_prefix` reproduces that on
        # purpose, and `language="gnuv2"` keeps it -- but a *claim* on a name nobody
        # asked about cannot rest on it. `g_cclosure_marshal_VOID__INT` is GLib's
        # generated marshaller, in every GTK binary, and it is not a C++ symbol.
        return False
    if len(parameters) > 1 and any(parameter == "void" for parameter in parameters):
        # `f(char, short, void)` cannot be a declaration: `void` is a parameter list only
        # when it is the whole of it. `PyInit__csv` is a CPython module initialiser, and
        # `csv` reading as three fundamental types is a coincidence of the letters.
        return False
    if symbol.qualifiers and not (symbol.evidence & _NAMED_SOMETHING):
        # `static`, `const`, `volatile` and `__restrict` qualify a *member* function, and
        # a member function has a class. Where none was read, what was matched was a `S`
        # or a `C` sitting in someone else's encoding -- `_TtU__FQD__Si` is a Swift
        # symbol, not a static function taking an `int`.
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
            # A marker another pre-Itanium compiler writes and the *gnu* style does not
            # know: `__ct` and `__dt` are the ARM family's constructor and destructor,
            # `__vt` and `__RTTI` CodeWarrior's. The reference reads them under
            # `--format=arm` and reads them as an ordinary function name under
            # `--format=gnu`, which is a wrong name rather than no name. A caller who
            # names the style still gets them.
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
    # The cheapest possible reject, inline and first. This is called on every symbol a
    # caller offers the library, most of which are not mangled at all, and `not_a_symbol`
    # should cost one C-level substring search and a character compare -- not a call into
    # `_screen` to find that out. Both halves are the necessary condition `_screen`
    # states; the rest of it only runs for a name that passed this.
    if "__" not in name and not (name[:1] == "_" and ("$" in name or "." in name)):
        return False
    if name.startswith(("_Z", "__Z")):
        # Itanium's own prefix, and the Mach-O form of it. The Itanium reader is
        # offered every such name first; one it refuses is offered on down the list,
        # and a `__Z` name is full of the `__` this grammar reads as a separator:
        # `__ZNKSt3__110__function6__funcI...` read as the method `__ZNKSt3` of a
        # class named after the rest of it. No g++ 2.x name opens with `_Z`.
        return False
    if len(name) > _DETECT_MAX or not _screen(name):
        return False
    symbol = _reads(name, style)
    return symbol is not None and _decoded(name, symbol) and _plausible(symbol)


#: What each builder class answered to `_wants_structure`, asked once per class: the
#: answer is a property of the builder's type and this is on every symbol's path.
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
        # The same bar `detect` holds a name to, applied here as well: a caller who names
        # the language still gets a refusal rather than their own bytes handed back.
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
    # `priority` is ascending: *lower is offered first*. Second-to-last -- after Itanium,
    # which is 200 -- because these names have no marker and can only be told from an
    # ordinary C identifier by reading one. Everything with a prefix of its own gets
    # first refusal.
    #
    # Before `codewarrior` (300), and that is a real decision rather than an arbitrary
    # one. `AtEnd__13ivRubberGroup` is a valid symbol under both manglings, both readings
    # parse, and they differ only in spelling -- `ivRubberGroup::AtEnd(void)` here,
    # `ivRubberGroup::AtEnd()` there. Nothing in the name settles it, so the commoner
    # mangling wins the tie: any g++ before 3.0 wrote these, against CodeWarrior's
    # console and embedded niche. What CodeWarrior writes and this cannot read -- a
    # literal `<...>` argument list, `@LOCAL@`, `$localstatic`, a `__dt` under the
    # default style -- is refused here and falls through to it.
    # tests/test_core.py pins the order against every corpus.
    priority=290,
)
"""The scheme as the registry holds it, registered when this package is imported."""

register(PLUGIN)
