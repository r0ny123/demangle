"""Go symbol names.

Go is the one scheme here with no marker saying "this is mine". An Itanium symbol opens
`_Z`, an MSVC one `?`, a Rust v0 one `_R`; a Go one opens with a package path, and
`fmt.Println` is a shape a C or Pascal symbol could have too. So detection asks for
positive evidence and declines otherwise, and a caller who knows the binary is Go -- from
its build info, which is how a tool would know -- passes `language="go"` and gets the
whole scheme regardless.

That asymmetry is deliberate. Failing to claim a Go symbol returns it unchanged, which is
what an unreadable name does anyway. Claiming a symbol that is not Go's would rewrite
someone else's name into a plausible lie, and a plausible lie is the one outcome this
library treats as worse than silence.

See `_parser.py` for where the rules come from: Go's own `cmd/internal/objabi/path.go`,
not another demangler's reading of it.
"""

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from . import nodes
from ._parser import GENERATED_PREFIXES, GoSymbol, escape_path, parse_go_symbol, unescape_path

#: What `detect` needs to see, for the registry to screen on without calling it: a path's
#: `/`, a receiver's `(*`, or a linker-generated opening. See `core.registry._screened`.
DETECT_SCREEN = (("/", "(*"), GENERATED_PREFIXES)


def detect(name):
    """Whether `name` is Go-shaped beyond reasonable doubt.

    Three kinds of evidence, each of which no other scheme here produces:

    - a linker-generated prefix, `go:` or `type:`;
    - a package path with a `/` in it, followed by a `.` -- an import path, which C and
      Pascal symbols do not have and which the C-family schemes encode rather than
      spell;
    - a method receiver written `(*Type).Method`.

    `fmt.Println` and `main.main` are real Go symbols and are deliberately *not* claimed:
    nothing in them distinguishes a Go symbol from any other dotted name, and guessing
    would mean rewriting names this library was not sure about. They demangle when asked
    for by language.
    """
    if not name:
        return False
    if name.startswith(GENERATED_PREFIXES):
        return True
    if "(*" in name and ")." in name:
        return True
    slash = name.rfind("/")
    if slash < 0:
        return False
    # The last slash of the *symbol*: a path inside generic arguments
    # (`main.F[internal/sync.node]`) is evidence of Go too.
    dot = name.find(".", slash + 1)
    return 0 < dot < len(name) - 1


#: `_wants_structure`'s answer per builder class, asked once per class.
_STRUCTURED = {}


def _wants_structure(builder):
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=None):
    """Parse a Go symbol into `builder`."""
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)

    # No second detection test: `detect` claimed the name or the caller named the
    # language. Re-asking refused Go symbols that need no decoding (`bytes.Compare`).
    symbol = parse_go_symbol(mangled)
    spelled = symbol.text
    if len(spelled) > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)

    if _wants_structure(builder):
        return nodes.build(symbol)
    return builder.raw(spelled)


PLUGIN = LanguagePlugin(
    name="go",
    node_kinds=("name", "path", "receiver", "symbol", "template"),
    detect=detect,
    parse=parse,
    description="Go symbol names (package paths, receivers, generic instantiations)",
    aliases=("golang",),
    # Lower is offered first, and Go goes first of all: its shapes (a `/` import path, a
    # `(*T).method` receiver, `go:`/`type:`) no other scheme produces, and Go symbols
    # would otherwise be claimed by whichever prefix scheme they resemble.
    # tests/test_core.py pins the order.
    priority=10,
)
"""The scheme as the registry holds it, registered when this package is imported."""

register(PLUGIN)

__all__ = [
    "GENERATED_PREFIXES",
    "PLUGIN",
    "GoSymbol",
    "detect",
    "escape_path",
    "parse",
    "parse_go_symbol",
    "unescape_path",
]
