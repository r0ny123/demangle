"""Microsoft's decorated name scheme.

The format `UnDecorateSymbolName` reverses, emitted by MSVC and by clang-cl. Microsoft
publishes no complete grammar for it, so this implementation was derived by probing
`llvm-undname` and validated against LLVM's own demangler corpus; the reference output
is recorded next to every name in tests/conformance.

Structured output
-----------------
This parser predates the builder protocol -- it was proven in production before this
package existed -- and does not write to a builder. It cannot: MSVC spells a declaration
differently enough that the shared spelling builder has nowhere to put a calling
convention, and the alternative to a scheme-specific renderer would be a scheme-specific
branch in `core`.

What it does instead is build its own tree, of nodes that are `core.ast.Node`s, and
spell that (see `nodes.py`). So `parse()` hands back a real tree to walk and `demangle()`
hands back text, exactly as they do for the other schemes -- the seam is which of the two
this module produces, rather than which builder the parser wrote to.
"""

import re

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from ._parser import _LimitHit, parse_msvc_symbol_strict, parse_msvc_type
from ._parser import render as _render
from .options import DEFAULT_OPTIONS, MsvcOptions

#: An MD5-hashed name (`??@<hash>@`) demangles to itself, as in `llvm-undname`.
_MD5_PREFIX = "??@"

#: The only suffix `llvm-undname` keeps after an MD5 name.
_MD5_RTTI_SUFFIX = "??_R4@"


def _md5_name(mangled):
    """Expand `??@<hash>@`, or None when it is not one.

    MSVC replaces a decorated name too long for the linker with an MD5 hash of it. The
    original spelling is not in the symbol at all, so the expansion is the name itself --
    truncated at the closing `@`, because anything after it is not part of the hashed
    name.
    """
    end = mangled.find("@", len(_MD5_PREFIX))
    if end < 0:
        return None
    base = mangled[: end + 1]
    if mangled.startswith(_MD5_RTTI_SUFFIX, end + 1):
        return base + _MD5_RTTI_SUFFIX
    return base


#: An RTTI type descriptor name: `.` and a bare type encoding.
_TYPE_DESCRIPTOR_NAME = "."

#: `??_R0...@8`, the descriptor object, writes the same words without `Name`.
_TYPE_DESCRIPTOR_SUFFIX = "`RTTI Type Descriptor Name'"


#: ARM64EC: after the qualified name, or `$$h@` before an MD5 name's closing `@`. From
#: LLVM's `getArm64ECMangledFunctionName`; `llvm-undname` does not read it.
_HYBRID_MARKER = "$$h"


def _without_hybrid_marker(name):
    """The ordinary decorated name an ARM64EC one is the hybrid form of, or None.

    LLVM's `getArm64ECDemangledFunctionName`, which is normative here -- it is what the
    compiler emits an `EXPORTAS` directive against, so the answer is the name the linker
    resolves. Its rule is the whole of this: an MD5 name loses a trailing `$$h@`, and any
    other loses the *first* `$$h` wherever it stands.

    Not implemented: the `#name` form, which is the same marker for a symbol that is not
    a C++ name at all. Reading it means claiming every string that opens with a `#` in
    order to strip one character, and `demangle()` is offered every symbol in a binary.
    LLVM applies its rule only to objects it has already established are ARM64EC.
    """
    if not name.startswith("?") or _HYBRID_MARKER not in name:
        return None
    if name.startswith(_MD5_PREFIX) and name.endswith("@$$h@"):
        return name[:-4]
    head, _, tail = name.partition(_HYBRID_MARKER)
    return head + tail


def detect(name):
    """A decorated name opens with `?`; a type descriptor's name opens with `.`.

    The `.` form is claimed although a symbol table is full of `.text`, `.rodata`,
    `.L1234` and `.constprop.0`: claiming is not reading, and what follows the dot has to
    parse as a *whole* type before anything is said about it, which none of those do.
    Measured over every dot-prefixed name in the checked-in corpora and over the section
    and label names a real object file carries: none is claimed. See
    `tests/test_msvc.py`.
    """
    return bool(name) and name[0] in "?."


#: What each builder class answered to `_wants_structure`, asked once per class.
_STRUCTURED = {}


def _wants_structure(builder):
    """Whether `builder` is collecting a tree rather than text.

    The protocol has no flag for this, and adding one would mean changing `core` for the
    sake of one scheme. It does not need one: the two builders already answer the question
    by what they hand back. `AstBuilder` returns `Node`s and `SpellingBuilder` returns
    `Spelling`s, so asking either for the cheapest thing it makes says which it is.

    A builder this module has never seen -- a third-party one emitting JSON, say -- gets
    text unless its products are `Node`s, because text is the answer every builder can
    use and a tree of this scheme's nodes is not.
    """
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


#: A regex rather than a per-character generator: 7.8x cheaper on every MSVC symbol.
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
    """Parse an MSVC decorated name into `builder`."""
    options = options or DEFAULT_OPTIONS
    if not detect(mangled):
        raise NotMangledError(mangled, "not an MSVC decorated name")
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)
    # Refused rather than copied into the output and on into a caller's report.
    if _CONTROL_CHARACTER.search(mangled) is not None:
        raise ParseError(mangled, None, "decorated name contains a control character")
    if mangled.startswith(_TYPE_DESCRIPTOR_NAME):
        # Not a declaration: the reference spells the type and appends what it is.
        try:
            tree = parse_msvc_type(mangled, limits, options)
            if tree is None:
                raise ParseError(mangled, None, "not a type descriptor name this demangler can read")
            # The marker goes where a declarator goes: `int (*`RTTI Type Descriptor Name')[2]`.
            spelled = _render(tree, _TYPE_DESCRIPTOR_SUFFIX, options=options)
        except _LimitHit as hit:
            raise LimitExceeded(mangled, hit.what, hit.limit) from hit
        _check_length(mangled, len(spelled), limits)
        return builder.raw(spelled)
    # ARM64EC, as LLVM's `getArm64ECDemangledFunctionName`: a fallback, since a name
    # that already reads is not one to rewrite.
    plain_error = None
    try:
        if mangled.startswith(_MD5_PREFIX):
            # A hashed name carries no recoverable spelling, so it is its own expansion.
            hashed = _md5_name(mangled)
            if hashed is None:
                raise ParseError(mangled, None, "unterminated MD5-hashed name")
            _check_length(mangled, len(hashed), limits)
            return builder.raw(hashed)
        try:
            if _wants_structure(builder):
                tree = parse_msvc_symbol_strict(mangled, limits, options)
                if tree is None:
                    raise ParseError(mangled, None, "not a decorated name this demangler can read")
                # No output check: `_Demangler.rendered` has already bounded it.
                return tree
            tree = parse_msvc_symbol_strict(mangled, limits, options)
            if tree is None:
                raise ParseError(mangled, None, "not a decorated name this demangler can read")
            expanded = _render(tree, options=options)
            _check_length(mangled, len(expanded), limits)
            return builder.raw(expanded)
        except _LimitHit as hit:
            # `hit.limit`, not the caller's: this scheme narrows both bounds (see `_LimitHit`).
            raise LimitExceeded(mangled, hit.what, hit.limit) from hit
    except ParseError as exc:
        plain_error = exc
    hybrid = _without_hybrid_marker(mangled)
    if hybrid is not None:
        if _HYBRID_MARKER in hybrid:
            # LLVM removes only the first marker; two is not something it can produce.
            raise ParseError(mangled, None, "more than one ARM64EC marker")
        return parse(hybrid, builder, limits, options)
    assert plain_error is not None
    raise plain_error


def parse_type(mangled, builder, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
    """Parse a bare *type* encoding -- `PEAX`, `PEAVFoo@@` -- rather than a whole symbol.

    What an RTTI type descriptor carries, and what `UnDecorateSymbolName`'s
    `UNDNAME_TYPE_ONLY` asks for. A leading `.` is accepted and dropped, because that is
    how the descriptor's own symbol spells it.

    None of this is reachable from `parse`, and deliberately: a type encoding opens with
    no `?`, so `detect` cannot see one coming and a demangler that guessed would read
    plain C symbols as types. It is readable only because the caller named the scheme.
    """
    if not mangled:
        raise NotMangledError(mangled, "empty type")
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)
    try:
        tree = parse_msvc_type(mangled, limits, options)
        if tree is None:
            raise ParseError(mangled, None, "not a type this demangler can read")
        if _wants_structure(builder):
            return tree
        expanded = _render(tree, options=options)
    except _LimitHit as hit:
        raise LimitExceeded(mangled, hit.what, hit.limit) from hit
    _check_length(mangled, len(expanded), limits)
    return builder.raw(expanded)


def _check_length(mangled, length, limits):
    if length > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)


PLUGIN = LanguagePlugin(
    name="msvc",
    node_kinds=("array", "declaration", "function", "indirection", "name", "raw"),
    detect=detect,
    parse=parse,
    parse_type=parse_type,
    description="Microsoft Visual C++ decorated names (MSVC, clang-cl)",
    options_type=MsvcOptions,
    aliases=("microsoft", "ms", "vc"),
    first_characters="?.",
    priority=100,
)
"""The scheme as the registry holds it, registered when this package is imported."""

register(PLUGIN)

__all__ = ["DEFAULT_OPTIONS", "PLUGIN", "MsvcOptions", "detect", "parse", "parse_type"]
