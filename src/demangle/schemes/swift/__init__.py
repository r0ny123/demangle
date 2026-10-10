"""Swift mangled names.

A port of the Swift compiler's own demangler -- `lib/Demangling/Demangler.cpp` and
`lib/Demangling/NodePrinter.cpp` -- because nothing smaller is enough. Swift's mangling
is postfix and compresses aggressively against three tables that span a whole name, so a
symbol cannot be read a piece at a time, and how a piece is *spelled* depends on where it
sits in the tree. `_demangler.py` and `_printer.py` are those two files; `nodes.py`
records the structure of the printer's traversal.

Conformance is exact against a reference built from Swift's own demangler at a pinned
`main` commit (see `tools/swift-demangle-reference/README.md`):

* every `$s` symbol in the shipped Swift runtime and Foundation -- 48,368 of them --
  spelled identically, with nothing refused;
* every case in the compiler's own `test/Demangle/Inputs/manglings.txt` -- the 531 rows
  of `tests/conformance/swift-upstream.txt` -- which is a much harder set: SIL function
  types, function-signature specialisations, key paths, autodiff thunks, macro
  expansions, and the Swift 3 mangling.

The Swift 3 mangling -- `_T` followed by anything but `0` -- is a different grammar with
its own demangler in the compiler, and `_old_demangler.py` is a port of that one. It
still matters: the ObjC runtime holds a Swift class's name in that form, so it turns up
in any Apple binary with interop in it. It builds the same tree, so the printer spells it
with no idea which mangling it came from, and the compiler's own Swift 3 test cases come
out exactly.
"""

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from . import nodes
from ._demangler import MANGLING_PREFIXES, async_main_entry_point_length, demangle_symbol, demangle_type
from ._old_demangler import demangle_old_symbol
from ._printer import print_root
from .options import DEFAULT_OPTIONS, SIMPLIFIED_OPTIONS, SwiftOptions
from .resolve import ContextResolver, Image, elf_image, macho_image
from .symbolic import SymbolicReference, end_of_name, scan

#: What each builder class answered to `_wants_structure`, asked once per class.
_STRUCTURED = {}


def detect(name):
    """One of the prefixes either mangling uses.

    `_T` covers Swift 3 as well as `_T0`; the two are different grammars and
    `demangle_symbol` dispatches between them, but from the outside they are one scheme.
    """
    if not name:
        return False
    if name.startswith("__"):
        # Mach-O adds one underscore, which `swift-demangle` takes off.
        name = name[1:]
    if async_main_entry_point_length(name):
        # The reference's `isSwiftSymbol` claims the `async` `@main` entry point by name.
        return True
    return name.startswith(MANGLING_PREFIXES) or name.startswith("_T")


def _wants_structure(builder):
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
    """Parse a Swift mangled name into `builder`."""
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    if not detect(mangled):
        raise NotMangledError(mangled, "not a Swift mangled name")
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)

    # Mach-O adds one leading underscore, which `swift-demangle` strips from a name that
    # opens with two. `_$s` is a prefix in its own right.
    name = mangled[1:] if mangled.startswith("__") else mangled
    try:
        root = demangle_symbol(name)
    except RecursionError as error:
        raise LimitExceeded(mangled, "recursion depth", limits.max_depth) from error
    return _finish(mangled, root, builder, limits, options)


def parse_type(mangled, builder, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
    """Parse a Swift *type* mangling -- `Si`, `SaySiG` -- rather than a whole symbol.

    `Demangler::demangleType` rather than `demangleSymbol`, and what a metadata typeref
    holds: a type mangling carries none of the `$s` a symbol opens with, so nothing about
    it says it is Swift, and it is only readable because the caller said so.

    A type mangling that reads as nothing is refused rather than spelled. The reference
    hands back the whole input wrapped in a `Suffix` node when nothing at all parsed,
    which prints as `with unmangled suffix "..."` -- true, but not a demangling of
    anything, so it comes back here as the `ParseError` it is.
    """
    if not mangled:
        raise NotMangledError(mangled, "empty type")
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)
    try:
        root = demangle_type(mangled)
    except RecursionError as error:
        raise LimitExceeded(mangled, "recursion depth", limits.max_depth) from error
    if root is not None and root.kind == "Suffix":
        raise ParseError(mangled, None, "not a type this reads")
    return _finish(mangled, root, builder, limits, options)


def _finish(mangled, root, builder, limits, options=DEFAULT_OPTIONS):
    """Turn a reference-demangler root into what `builder` collects, or refuse it."""
    if root is None:
        raise ParseError(mangled, None, "not a name this reads")
    options = options or DEFAULT_OPTIONS

    if _wants_structure(builder):
        tree = nodes.build(root, options)
        if tree is None:
            raise ParseError(mangled, None, "not a name this reads")
        if tree.size > limits.max_output:
            raise LimitExceeded(mangled, "output length", limits.max_output)
        return tree

    text = print_root(root, options)
    if not text:
        raise ParseError(mangled, None, "not a name this reads")
    if len(text) > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)
    return builder.raw(text)


PLUGIN = LanguagePlugin(
    name="swift",
    node_kinds=(
        "extension",
        "function",
        "generics",
        "module",
        "name",
        "symbol",
        "template",
        "type",
        "variable",
    ),
    detect=detect,
    parse=parse,
    parse_type=parse_type,
    description="Swift symbol mangling",
    options_type=SwiftOptions,
    aliases=(),
    # Lower is offered first: after D, before Rust. `_$S` collides with Free Pascal,
    # which is offered earlier and wins.
    first_characters="$_@a",
    priority=45,
)
"""The scheme as the registry holds it, registered when this package is imported."""

register(PLUGIN)


def demangle_symbolic(name, resolver=None, *, whole_symbol=None):
    """Read a mangled name that may hold symbolic references, and spell it.

    `name` is `bytes`, not `str`, because a name holding a symbolic reference is not
    text: the reference's four-byte offset is arbitrary bytes. This is the entry a
    metadata typeref takes, and the only one that can take it.

    `resolver` is called with `(reference, offset-of-its-offset-field-within-name)` and
    returns a *mangled fragment* naming what the reference points at, or None to decline.
    Return a Unicode `str` for textual fragments, or `bytes` when a fragment contains
    binary symbolic references. For compatibility, a `str` whose grammar reaches a symbolic
    reference token or alignment padding is treated as a Latin1 byte view.

    `resolve.ContextResolver` is one, over an `Image`; a caller with a debugger or a
    memory dump writes its own. With no resolver at all, a name holding a reference is
    refused -- which is what the reference demangler does, and better than inventing a
    name for something the bytes do not carry.

    `whole_symbol` says whether to read the name as a whole symbol or as a type. Left
    None it is decided by the mangling prefix, which is what tells the two apart: a
    symbol carries `$s` or `_T`, a typeref carries nothing.

    Returns the spelling, or None if the name could not be read.
    """
    if isinstance(name, str):
        raise TypeError("demangle_symbolic reads bytes; a name holding a reference is not text")
    # Latin1 maps each byte to one code point, preserving binary resolver offsets.
    text = name.decode("latin-1")
    if whole_symbol is None:
        whole_symbol = detect(text)
    try:
        root = (
            demangle_symbol(text, resolver, byte_mode=True)
            if whole_symbol
            else demangle_type(text, resolver, byte_mode=True)
        )
    except RecursionError:
        return None
    if root is None:
        return None
    if root.kind == "Suffix":
        # A whole-input `Suffix` node is the reference saying it could not read the name.
        return None
    spelled = print_root(root)
    return spelled or None


def typerefs(blob):
    """Split a metadata blob of NUL-terminated mangled names into those names.

    Not `blob.split(b"\\0")`: a symbolic reference's offset is arbitrary bytes and very
    often holds a zero, so splitting cuts names in half. See `symbolic.end_of_name`.
    """
    from .symbolic import names

    return names(blob)


__all__ = [
    "DEFAULT_OPTIONS",
    "PLUGIN",
    "SIMPLIFIED_OPTIONS",
    "ContextResolver",
    "Image",
    "SwiftOptions",
    "SymbolicReference",
    "demangle_old_symbol",
    "demangle_symbol",
    "demangle_symbolic",
    "demangle_type",
    "detect",
    "elf_image",
    "end_of_name",
    "macho_image",
    "parse",
    "parse_type",
    "print_root",
    "scan",
    "typerefs",
]
