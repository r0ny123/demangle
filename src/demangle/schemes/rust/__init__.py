"""Rust symbol mangling: the legacy `_ZN` scheme and the v0 `_R` scheme.

Rust has used two schemes. The legacy one wraps a hashed path in Itanium mangling, so a
legacy Rust symbol is also a valid Itanium symbol -- which is why this plugin is offered
names first. The v0 scheme, specified in RFC 2603 and stabilised behind
`-Csymbol-mangling-version=v0`, is a scheme of its own.

Derived from MIT-licensed code, with both grammars substantially reworked; see NOTICE.

Structured output
-----------------
`parse()` returns a tree: a `symbol` holding a `path` of `name` components, with `impl`,
`template`, `type` and `literal` nodes for what a path carries. See `nodes.py` for why
the tree cannot spell a symbol differently from `demangle()`.
"""

import re

from ...core.ast import Node
from ...core.decorations import split_decorations
from ...core.errors import LimitExceeded, NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from ._dispatch import ManglingType, RustDemangler, TypeNotFoundError
from ._legacy import UnableToLegacyDemangle
from ._v0 import _LLVM_MARKER, _PATH_TAGS, OutputTooLong, RecursedTooDeep, UnableTov0Demangle, _strip_llvm_suffix

_DEMANGLER = RustDemangler()

#: Legacy Rust's trailing `17h<16 hex digits>` hash component: the `_ZN` prefix alone does
#: not tell it from C++.
_LEGACY_HASH_MARKER = "17h"
_LEGACY_HASH_DIGITS = 16

#: Legacy Rust's escapes (`$LT$`, `$RF$`, `$u20$`), which Itanium never spells. Anchored at
#: both ends so clang's `$_0` lambda names are not claimed.
_LEGACY_ESCAPE = re.compile(r"\$[A-Za-z0-9]{1,8}\$")

#: What follows `_R`: every `<path>` opens with one of these. `B` cannot come first but is
#: left for the parser to refuse.
_V0_PATH_START = frozenset(_PATH_TAGS | {"B"})


def detect(name):
    """Cheap test for a Rust mangled name.

    v0 needs its prefix *and* the letter that opens a `<path>`, which is one of seven.
    `_R` on its own is not enough: CodeWarrior writes `__RTTI__40TObjOwnerDerivedFromIObj
    <12CStringTable>`, and claiming it would make `detect` name the wrong scheme for a
    symbol in this package's own corpus. The parse would fail and `demangle` fall
    through to the scheme that owns it, so the spelling would be right and the label
    would not -- but `detect` is a public answer in its own right, and a caller
    labelling a symbol table gets that answer and no second chance. Nothing is lost by
    the narrower test: a v0 name that does not open a `<path>` is one the parser refuses
    on its next step.

    Legacy shares Itanium's `_ZN` prefix, so it additionally requires evidence that the
    name is Rust's -- without that this plugin would claim every C++ symbol it was
    offered.

    Two kinds of evidence, either of which is enough:

    - the hash component, `17h` and sixteen hex digits, that rustc appends as the last
      element of the path. It is looked for by its marker rather than at a fixed offset
      from the end, because real symbols carry things after it: a `.0` for a promoted
      constant, a `.llvm.<hash>` from LLVM's internaliser. Anchoring to the end would
      miss every one of those, and the C++ demangler would then claim them and produce a
      plausible-looking but quite wrong spelling.
    - a `$...$` escape, which legacy Rust uses for characters the Itanium alphabet has
      no room for. `_ZN8$RF$testE` is `&test`; read as C++ it spells `$RF$test`, which
      is not a name anything has.

    The leading underscore is optional -- some symbol tables have already had it
    stripped, and rustc-demangle reads `ZN4testE` as `test`. Without the underscore
    nothing else claims the name, so the evidence asked for there is the grammar itself:
    `ZN`, a digit, and a path the legacy reader consumes whole. None of the 652,000
    symbols in this box's libraries and binaries starts `ZN` at all.

    Deliberately narrower than rustc-demangle, which accepts any `_ZN` name and treats
    the hash as optional. It can afford to: it is only ever handed names a caller has
    already decided are Rust's. This plugin is offered every symbol in a binary, and the
    same rule here would claim `_ZN1a4hbadE` -- an ordinary C++ `a::hbad` -- and print
    `a`, having read `hbad` as a hash and dropped it. The cost of being narrow is that a
    Rust symbol carrying neither mark reads as C++; the cost of being wide is that C++
    symbols read as Rust. The first loses information, the second invents it.
    """
    if not name:
        return False
    if name.startswith(("_R", "__R")):
        opening = name[3:4] if name[1] == "_" else name[2:3]
        return opening in _V0_PATH_START
    if not name.startswith(("_ZN", "__ZN", "ZN")):
        return False
    version = name.find("@")
    if version > 0:
        name = name[:version]
    marker = name.rfind(_LEGACY_HASH_MARKER)
    if marker >= 0:
        start = marker + len(_LEGACY_HASH_MARKER)
        digits = name[start : start + _LEGACY_HASH_DIGITS]
        if (
            len(digits) == _LEGACY_HASH_DIGITS
            and _is_hex(digits)
            and name[start + _LEGACY_HASH_DIGITS : start + _LEGACY_HASH_DIGITS + 1] == "E"
        ):
            return True
    if _LEGACY_ESCAPE.search(name) is not None:
        return True
    # A bare `ZN` has no marker, so reading it is the only test; bounded like a parse.
    return name[0] == "Z" and name[2:3].isdigit() and len(name) <= DEFAULT_LIMITS.max_input and _reads_as_legacy(name)


def _reads_as_legacy(name):
    try:
        return bool(_DEMANGLER.demangle(name, DEFAULT_LIMITS.max_output))
    except (UnableToLegacyDemangle, TypeNotFoundError):
        return False


def _is_hex(text):
    # Both cases, as the parser's `is_rust_hash` (like the reference's `is_digit(16)`):
    # the two must agree, or one route reads the name and the other hands it to Itanium.
    return not text.strip("0123456789abcdefABCDEF")


#: `_wants_structure`'s answer per builder class, asked once per class.
_STRUCTURED = {}


def _wants_structure(builder):
    """Whether `builder` is collecting a tree rather than text.

    The same test the MSVC scheme makes, and for the same reason: the protocol has no
    flag for this and the two builders already answer by what they hand back. A builder
    this module has never seen gets text unless its products are `Node`s, because text
    is the answer every builder can use.
    """
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=None):
    """Parse a Rust mangled name into `builder`."""
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    # Checked before anything else looks at the string.
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)
    # rustc-demangle drops a `.llvm.<hash>` before anything else, and its hash alphabet
    # includes `@`: `_RC3foo.llvm.9D1C9369@@16` is `foo`. Splitting the ELF version off
    # first, as `core` does for other schemes, would strand the `@@16` after the name.
    name = mangled
    version = name.find("@")
    if version > 0 and name.find(_LLVM_MARKER, 0, version) >= 0:
        name = _strip_llvm_suffix(name)
    name, decoration = split_decorations(name)
    # `keep_hash` is the one thing a style changes here; see `options.py`.
    keep_hash = bool(getattr(options, "keep_hash", False))
    if _wants_structure(builder):
        handle = _guard(name, limits, _DEMANGLER.structure, keep_hash)
        _check_length(name, handle.size, limits)
        _refuse_empty(name, handle.size)
    else:
        expanded = _guard(name, limits, _DEMANGLER.demangle, keep_hash)
        _check_length(name, len(expanded), limits)
        _refuse_empty(name, len(expanded))
        handle = builder.raw(expanded)
    return builder.decorated(handle, decoration) if decoration else handle


def _refuse_empty(mangled, length):
    """A parse that spells to nothing is a failure, not an answer.

    `demangle()` promises the readable spelling or the name unchanged, and there is no
    third outcome -- least of all the empty string, which names no symbol and would have
    a tool label a function with a blank. The grammar accepts `_RCCC` and
    prints nothing. The reference echoes the input, which is what refusing here
    produces.

    Takes a length rather than the text, so the tree path can answer from `tree.size` --
    which is carried, not computed -- instead of rendering a tree to find out whether it
    is empty. Rendering it would defeat the purpose twice over: it is the expensive half
    of `parse()`, and it is recursive, so a tree near the depth bound overflows the stack
    on the way to discovering it had something in it after all.
    """
    if not length:
        raise ParseError(mangled, None, "read as a Rust name but spells nothing")


def _guard(mangled, limits, demangle_with, keep_hash=False):
    """Run one of the demanglers, translating its errors into this package's.

    The two entry points fail in exactly the same ways -- they are the same parser --
    so the translation lives here rather than twice.
    """
    try:
        return demangle_with(mangled, limits.max_output, keep_hash, limits.max_depth)
    except OutputTooLong as exc:
        raise LimitExceeded(mangled, "output length", limits.max_output) from exc
    except TypeNotFoundError as exc:
        raise NotMangledError(mangled, "not a Rust mangled name") from exc
    except RecursedTooDeep as exc:
        raise LimitExceeded(mangled, "recursion depth", limits.max_depth) from exc
    except (UnableTov0Demangle, UnableToLegacyDemangle) as exc:
        raise ParseError(mangled, None, str(exc)) from exc
    except RecursionError as exc:
        raise LimitExceeded(mangled, "recursion depth", limits.max_depth) from exc


def _check_length(mangled, length, limits):
    if length > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)


PLUGIN = LanguagePlugin(
    name="rust",
    node_kinds=(
        "decorated",
        "impl",
        "literal",
        "name",
        "namespace",
        "path",
        "symbol",
        "template",
        "type",
    ),
    detect=detect,
    parse=parse,
    description="Rust legacy (_ZN) and v0 (_R) symbol mangling",
    aliases=("rs",),
    # False because `parse` splits the ELF version itself, after rustc-demangle's
    # `.llvm.` rule; `detect` looks through the version.
    symbol_table_decorations=False,
    first_characters="_Z",
    priority=50,
)
"""The scheme as the registry holds it, registered when this package is imported."""

register(PLUGIN)

__all__ = ["PLUGIN", "ManglingType", "RustDemangler", "detect", "parse"]
