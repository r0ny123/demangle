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
* every current-mangling case in the compiler's own `test/Demangle/Inputs/manglings.txt`,
  199 of them, which is a much harder set: SIL function types, function-signature
  specialisations, key paths, autodiff thunks, macro expansions.

The Swift 3 mangling (`_T` followed by anything but `0`) is a different grammar with its
own demangler in the compiler, and is not read here: such a name is refused rather than
guessed at. It still occurs in ObjC metadata in shipped binaries.
"""

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from . import nodes
from ._demangler import MANGLING_PREFIXES, demangle_symbol
from ._printer import print_root

#: What each builder class answered to `_wants_structure`, asked once per class.
_STRUCTURED = {}

def detect(name):
    """One of the prefixes this reads.

    `MANGLING_PREFIXES` deliberately does not include a bare `_T`: that is the Swift 3
    mangling, whose grammar is a different one, and claiming it here would turn a name
    another scheme might read into a refusal.
    """
    return bool(name) and name.startswith(MANGLING_PREFIXES)


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

__all__ = ["PLUGIN", "demangle_symbol", "detect", "parse", "print_root"]
