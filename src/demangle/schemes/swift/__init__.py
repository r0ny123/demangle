"""Swift mangled names.

A port of the Swift compiler's own demangler -- `lib/Demangling/Demangler.cpp` and
`lib/Demangling/NodePrinter.cpp` -- because nothing smaller is enough. Swift's mangling
is postfix and compresses aggressively against three tables that span a whole name, so a
symbol cannot be read a piece at a time, and how a piece is *spelled* depends on where it
sits in the tree. `_demangler.py` and `_printer.py` are those two files; `nodes.py`
records the structure of the printer's traversal.

Conformance is exact against `swift-demangle` from the 5.10.1 toolchain:

* every `$s` symbol in the shipped Swift runtime and Foundation -- 48,368 of them --
  spelled identically, with nothing refused;
* all 376 cases in the compiler's own `test/Demangle/Inputs/manglings.txt`, which is a
  much harder set: SIL function types, function-signature specialisations, key paths,
  autodiff thunks, macro expansions, and the Swift 3 mangling.

The Swift 3 mangling -- `_T` followed by anything but `0` -- is a different grammar with
its own demangler in the compiler, and `_old_demangler.py` is a port of that one. It
still matters: the ObjC runtime holds a Swift class's name in that form, so it turns up
in any Apple binary with interop in it. It builds the same tree, so the printer spells it
with no idea which mangling it came from, and all 247 of the compiler's own Swift 3 test
cases come out exactly.
"""

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from . import nodes
from ._demangler import MANGLING_PREFIXES, demangle_symbol
from ._old_demangler import demangle_old_symbol
from ._printer import print_root

#: What each builder class answered to `_wants_structure`, asked once per class.
_STRUCTURED = {}

def detect(name):
    """One of the prefixes either mangling uses.

    `_T` covers Swift 3 as well as `_T0`; the two are different grammars and
    `demangle_symbol` dispatches between them, but from the outside they are one scheme.
    """
    return bool(name) and (name.startswith(MANGLING_PREFIXES) or name.startswith(("_T", "__T")))


def _wants_structure(builder):
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=None):
    """Parse a Swift mangled name into `builder`."""
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    if not detect(mangled):
        raise NotMangledError(mangled, "not a Swift mangled name")
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)

    try:
        root = demangle_symbol(mangled)
    except RecursionError as error:
        raise LimitExceeded(mangled, "recursion depth", limits.max_depth) from error
    if root is None:
        raise ParseError(mangled, None, "not a name this reads")

    if _wants_structure(builder):
        tree = nodes.build(root)
        if tree is None:
            raise ParseError(mangled, None, "not a name this reads")
        if tree.size > limits.max_output:
            raise LimitExceeded(mangled, "output length", limits.max_output)
        return tree

    text = print_root(root)
    if not text:
        raise ParseError(mangled, None, "not a name this reads")
    if len(text) > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)
    return builder.raw(text)


PLUGIN = LanguagePlugin(
    name="swift",
    detect=detect,
    parse=parse,
    description="Swift symbol mangling",
    aliases=(),
    # Above D and Go: `$s` and `_T0` collide with nothing, and the check is a prefix test.
    priority=45,
)

register(PLUGIN)

__all__ = ["PLUGIN", "demangle_old_symbol", "demangle_symbol", "detect", "parse", "print_root"]
