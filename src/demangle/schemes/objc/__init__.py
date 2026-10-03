"""Objective-C symbol names.

Objective-C is barely mangled, and what mangling there is comes from the compiler rather
than from the language: clang and GCC each write the runtime's data structures into
symbols of their own devising, and only the GNU-family method form is a mangling in the
sense the other schemes here use the word. So the rules are transcribed from clang's
`lib/AST/Mangle.cpp`, `lib/CodeGen/CGObjCMac.cpp` and `lib/CodeGen/CGObjCGNU.cpp`, and
from the symbols GCC's own front end left in the shipped `libobjc.a`.

Four families are read:

    -[NSString stringWithFormat:]     the Apple runtimes, and what every crash log shows
    _i_NSString__stringWithFormat_    the GNU family: GCC's runtime and GNUstep's
    _OBJC_CLASS_$_NSString            Apple's non-fragile ABI
    .objc_class_name_NSString         Apple's fragile ABI, and the GNU runtimes' own

Clang's block invocation functions (`___<len><method>_block_invoke`,
`__block_literal_global`, `__block_descriptor`) are also read. The length prefix before
a method is verified against the method's text; a mismatched count is refused rather
than guessed at.

Correctness rests on re-assembly, as it does for Go, Nim and Free Pascal, and on
agreement with what the compiler emitted for declarations this package wrote: 445 of 445
symbols clang produced for Objective-C written the way Objective-C is written, across
the macOS, fragile and GNUstep ABIs. Over a corpus built to put underscores inside class
names, category names and selectors -- the collision clang's own source warns about --
396 of 422, and `Symbol.ambiguous` marks every name where more than one reading exists.

**The GNU-family method mangling is not injective.** clang says so where it writes it:
"This is the mangling we've always used on the GNU runtimes, but it has obvious
collisions in the face of underscores within class names, category names, and
selectors." A `:` and a separator are both written `_`, so `_i_A_B_c` is `-[A(B) c]` and
`-[A_B c]` and `-[A(B_c) ]` alike. Every reading that re-mangles to the symbol is found;
the one preferred is the one needing no category, because a method outside a category
leaves the field empty and its two separators fall together into a visible doubled
underscore.

**Two GNUstep forms are refused rather than guessed at.** `.objc_category_FooBar` joins
the class and category names with no separator at all, and nothing can say where one
ends. `.objc_selector_<name>_<types>` is split at the rightmost underscore whose left
half is a selector, which is right unless the type encoding names a struct.
"""

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from . import nodes
from ._parser import (
    _METHOD_PREFIXES,
    _SCREEN_MARKERS,
    DemangleFailure,
    ObjcSymbol,
    detect,
    gnu_method_readings,
    mangle_gnu_method,
    parse_objc_symbol,
)

#: What `detect` needs to see, for the registry to screen on without calling it: one of
#: its markers, an Apple method's `-`/`+`, or a method prefix under the decorations
#: `_method_prefixed` strips. See `core.registry._screened`.
DETECT_SCREEN = (
    _SCREEN_MARKERS,
    ("-", "+", *(f"{strip}{prefix}" for strip in ("", ".", "_", "l_", "L_", "._") for prefix in _METHOD_PREFIXES)),
)

#: What each builder class answered to `_wants_structure`, asked once per class.
_STRUCTURED = {}


def _wants_structure(builder):
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=None):
    """Parse an Objective-C symbol into `builder`."""
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)

    try:
        symbol = parse_objc_symbol(mangled)
    except DemangleFailure as error:
        raise NotMangledError(mangled, str(error)) from error

    if len(symbol.text) > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)

    if _wants_structure(builder):
        return nodes.build(symbol)
    return builder.raw(symbol.text)


PLUGIN = LanguagePlugin(
    name="objc",
    node_kinds=("name", "path", "symbol"),
    detect=detect,
    parse=parse,
    description="Objective-C method, class and runtime symbol names",
    aliases=("objective-c", "objectivec"),
    # Lower is offered first: after Nim and Free Pascal, before prefixed schemes, since
    # the `_i_`/`_c_` form looks like a C identifier another scheme might recognise.
    priority=30,
    # Checked against every corpus by `tests/test_core.py`.
    first_characters="-+_.lL",
)
"""The scheme as the registry holds it, registered when this package is imported."""

register(PLUGIN)

__all__ = [
    "PLUGIN",
    "DemangleFailure",
    "ObjcSymbol",
    "detect",
    "gnu_method_readings",
    "mangle_gnu_method",
    "parse",
    "parse_objc_symbol",
]
