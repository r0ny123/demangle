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

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from ._parser import _LimitHit, parse_msvc_symbol_strict, parse_msvc_type
from ._parser import render as _render
from .options import DEFAULT_OPTIONS, MsvcOptions

#: MSVC replaces a decorated name too long for the linker with an MD5 hash of it,
#: written `??@<hash>@`. Nothing can be recovered -- the original spelling is simply not
#: in the symbol -- so the demangled form of such a name is the name itself, which is
#: what `llvm-undname` prints for it.
_MD5_PREFIX = "??@"

#: The one thing that may follow an MD5 name and still belong to it: the RTTI complete
#: object locator tag. Probing `llvm-undname` shows every other trailing sequence --
#: `??_R0@` through `??_R5@`, `??_C@`, `??_7@`, arbitrary text -- being dropped, and only
#: `??_R4@` kept.
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


#: An RTTI *type descriptor name*: a `.` and a bare type encoding, which is how the
#: linker spells the string a `type_info` points at. Nothing else in this scheme opens
#: with a `.`, and nothing in any other scheme here does either.
_TYPE_DESCRIPTOR_NAME = "."

#: What the reference writes after the type. Its sibling `??_R0...@8` -- the descriptor
#: *object* rather than the name in it -- writes the same words without `Name`.
_TYPE_DESCRIPTOR_SUFFIX = "`RTTI Type Descriptor Name'"


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


#: What each builder class answered to `_wants_structure`. Asked once per class rather
#: than once per name: the answer is a property of the builder's type, and this question
#: is on the path every symbol takes.
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


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
    """Parse an MSVC decorated name into `builder`."""
    options = options or DEFAULT_OPTIONS
    if not detect(mangled):
        raise NotMangledError(mangled, "not an MSVC decorated name")
    # The input bound, which this scheme did not enforce at all. A caller asking for
    # `max_input=32` had a 100,000-character name read in full and then rejected on
    # output length, 185ms later; a bound on input size that is checked after the input
    # has been read is not one.
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)
    # A decorated name is read from a NUL-terminated string of source-legal characters
    # and cannot hold a control character. One that does is refused outright rather than
    # copied into the output, where it would travel on into a caller's report.
    if any(char < " " or char == "\x7f" for char in mangled):
        raise ParseError(mangled, None, "decorated name contains a control character")
    if mangled.startswith(_TYPE_DESCRIPTOR_NAME):
        # `.PEAX` is the string a `type_info` points at: a bare type encoding with a `.`
        # in front of it. It is not a declaration, so it has no tree of its own beyond
        # the type -- the reference spells the type and appends what it is.
        tree = parse_msvc_type(mangled, limits)
        if tree is None:
            raise ParseError(mangled, None, "not a type descriptor name this demangler can read")
        # The marker goes where a *declarator* goes. For anything that wraps its name
        # that is not the same place as after the type: a pointer to an array of two
        # reads `int (*`RTTI Type Descriptor Name')[2]`.
        spelled = _render(tree, _TYPE_DESCRIPTOR_SUFFIX, options=options)
        _check_length(mangled, len(spelled), limits)
        return builder.raw(spelled)
    if mangled.startswith(_MD5_PREFIX):
        # A hashed name carries no recoverable spelling, so it is its own expansion.
        # This is a successful parse, not a failure: there is nothing more to say about
        # the symbol, and the reference demangler agrees.
        hashed = _md5_name(mangled)
        if hashed is None:
            raise ParseError(mangled, None, "unterminated MD5-hashed name")
        return builder.raw(hashed)
    try:
        if _wants_structure(builder):
            tree = parse_msvc_symbol_strict(mangled, limits, options)
            if tree is None:
                raise ParseError(mangled, None, "not a decorated name this demangler can read")
            # No length check here, and that is not an omission. The parser bounds its
            # own output as it builds -- `_Demangler.rendered` refuses past
            # `min(limits.max_output, ...)`, and the top-level declaration goes through
            # it -- so by the time there is a tree the bound has already been enforced.
            # Checking again meant calling `tree.spell()` and throwing the string away:
            # the expensive half of `parse()` run for a number that was already settled.
            return tree
        tree = parse_msvc_symbol_strict(mangled, limits, options)
        if tree is None:
            raise ParseError(mangled, None, "not a decorated name this demangler can read")
        expanded = _render(tree, options=options)
        _check_length(mangled, len(expanded), limits)
        return builder.raw(expanded)
    except _LimitHit as hit:
        # A bound stopped the parse. Reported as a `ParseError` this said the name could
        # not be read, which is a different claim: the name may be well formed and
        # merely larger than this caller allowed.
        raise LimitExceeded(mangled, str(hit), getattr(limits, f"max_{str(hit).split()[-1]}", 0)) from hit


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
        tree = parse_msvc_type(mangled, limits)
        if tree is None:
            raise ParseError(mangled, None, "not a type this demangler can read")
        if _wants_structure(builder):
            return tree
        expanded = _render(tree)
    except _LimitHit as hit:
        raise LimitExceeded(mangled, str(hit), getattr(limits, f"max_{str(hit).split()[-1]}", 0)) from hit
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
    # A decorated name opens with `?`; an RTTI type descriptor name opens with `.`.
    first_characters="?.",
    priority=100,
)

register(PLUGIN)

__all__ = ["DEFAULT_OPTIONS", "PLUGIN", "MsvcOptions", "detect", "parse", "parse_type"]
