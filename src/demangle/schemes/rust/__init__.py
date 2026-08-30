"""Rust symbol mangling: the legacy `_ZN` scheme and the v0 `_R` scheme.

Rust has used two schemes. The legacy one wraps a hashed path in Itanium mangling, so a
legacy Rust symbol is also a valid Itanium symbol -- which is why this plugin is offered
names first. The v0 scheme, specified in RFC 2603 and stabilised behind
`-Csymbol-mangling-version=v0`, is a scheme of its own.

Derived from Team bi0s' rust_demangler (MIT), with both grammars substantially reworked.
See NOTICE.

Structured output
-----------------
`parse()` returns a tree: a `symbol` holding a `path` of `name` components, with `impl`,
`template`, `type` and `literal` nodes for what a path carries. See `nodes.py` for why
the tree cannot spell a symbol differently from `demangle()`.
"""

import re

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from ._dispatch import ManglingType, RustDemangler, TypeNotFoundError
from ._legacy import UnableToLegacyDemangle
from ._v0 import OutputTooLong, UnableTov0Demangle

_DEMANGLER = RustDemangler()

#: Legacy Rust appends a hash component, `17h<16 hex digits>`, as the last element of the
#: path. Looking for it is what stops this plugin claiming every C++ symbol in a binary,
#: since the `_ZN` prefix alone does not distinguish the two.
_LEGACY_HASH_MARKER = "17h"
_LEGACY_HASH_DIGITS = 16

#: The escapes legacy Rust writes for characters `_ZN` cannot carry: `$LT$` for `<`,
#: `$RF$` for `&`, `$u20$` for a space. Each is a `$`, a short run of letters and digits,
#: and a closing `$`, and Itanium mangling has no production that spells one -- so a name
#: that holds one is Rust's whatever else it looks like.
#:
#: Deliberately anchored at both ends. Clang writes `$_0` for a lambda inside a local
#: name and that is a `$` in a C++ symbol; it has no closing `$`, so requiring the pair
#: is what keeps this from claiming those.
_LEGACY_ESCAPE = re.compile(r"\$[A-Za-z0-9]{1,8}\$")


def detect(name):
    """Cheap test for a Rust mangled name.

    v0 is unambiguous: nothing else uses `_R`. Legacy shares Itanium's `_ZN` prefix, so
    it additionally requires evidence that the name is Rust's -- without that this plugin
    would claim every C++ symbol it was offered.

    Two kinds of evidence, either of which is enough:

    - the hash component, `17h` and sixteen hex digits, that rustc appends as the last
      element of the path. It is looked for by its marker rather than at a fixed offset
      from the end, because real symbols carry things after it: a `.0` for a promoted
      constant, a `.llvm.<hash>` from LLVM's internaliser. Anchoring to the end missed
      every one of those, and the C++ demangler then claimed them and produced a
      plausible-looking but quite wrong spelling.
    - a `$...$` escape, which legacy Rust uses for characters the Itanium alphabet has
      no room for. `_ZN8$RF$testE` is `&test`; read as C++ it spelled `$RF$test`, which
      is not a name anything has.

    The leading underscore is optional -- some symbol tables have already had it
    stripped -- but only where there is evidence. A bare `ZN...E` with neither mark is
    left alone, because `ZN` is a perfectly ordinary start to a C identifier.

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
        return True
    if not name.startswith(("_ZN", "__ZN", "ZN")):
        return False
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
    return _LEGACY_ESCAPE.search(name) is not None


def _is_hex(text):
    # `not text.strip(set)` is "every character is in set", in one C-level scan, where
    # the generator this replaces was resumed once per character of every hash tested.
    return not text.strip("0123456789abcdef")


#: What each builder class answered to `_wants_structure`, asked once per class rather
#: than once per name: the answer is a property of the builder's type, and this question
#: is on the path every symbol takes.
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
    # The input bound, which this scheme did not enforce. Without it a caller's `Limits`
    # said one thing and the parser did another: an 80,000-character name was read in
    # full under `max_input=32`. It is checked before anything else looks at the string,
    # which is the only place a bound on input size means what it says.
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)
    if _wants_structure(builder):
        tree = _guard(mangled, limits, _DEMANGLER.structure)
        _check_length(mangled, tree.size, limits)
        _refuse_empty(mangled, tree.size)
        return tree
    expanded = _guard(mangled, limits, _DEMANGLER.demangle)
    _check_length(mangled, len(expanded), limits)
    _refuse_empty(mangled, len(expanded))
    return builder.raw(expanded)


def _refuse_empty(mangled, length):
    """A parse that spells to nothing is a failure, not an answer.

    `demangle()` promises the readable spelling or the name unchanged, and there is no
    third outcome -- least of all the empty string, which names no symbol and would have
    a tool label a function with a blank. `_RCCC` reached here: the grammar accepted it
    and printed nothing, and `demangle()` handed back `""`. The reference echoes the
    input, which is what refusing here produces.

    Takes a length rather than the text, so the tree path can answer from `tree.size` --
    which is carried, not computed -- instead of rendering a tree to find out whether it
    is empty. Rendering it would defeat the purpose twice over: it is the expensive half
    of `parse()`, and it is recursive, so a tree near the depth bound overflows the stack
    on the way to discovering it had something in it after all.
    """
    if not length:
        raise ParseError(mangled, None, "read as a Rust name but spells nothing")


def _guard(mangled, limits, demangle_with):
    """Run one of the demanglers, translating its errors into this package's.

    The two entry points fail in exactly the same ways -- they are the same parser --
    so the translation lives here rather than twice.
    """
    try:
        return demangle_with(mangled, limits.max_output)
    except OutputTooLong as exc:
        raise LimitExceeded(mangled, "output length", limits.max_output) from exc
    except TypeNotFoundError as exc:
        raise NotMangledError(mangled, "not a Rust mangled name") from exc
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
    # Before Itanium: legacy Rust mangling *is* Itanium mangling, and only this plugin
    # knows how to strip the trailing hash and read the path correctly.
    # Safe now that core/decorations.py splits only the ELF version suffix. It must
    # never be taught to split on `.` again: `.` is Rust grammar -- legacy mangling
    # writes `..` for `::` and spells shims `{{vtable.shim}}` -- and rustc-demangle's
    # own suffix rule differs from GCC's anyway (cut after the mangled name's final `E`,
    # drop a `.llvm.<hash>`, append anything else verbatim). This scheme implements that
    # itself.
    symbol_table_decorations=True,
    # `_R`, `__R`, `_ZN`, `__ZN` and a bare `ZN` are the only starts `detect` accepts.
    first_characters="_Z",
    priority=50,
)

register(PLUGIN)

__all__ = ["PLUGIN", "ManglingType", "RustDemangler", "detect", "parse"]
