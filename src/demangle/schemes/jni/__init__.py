"""JNI native method names.

A Java method declared `native` is called through a C function whose name encodes the
method's class, its name, and -- where it is overloaded -- its parameter types. Android
apps ship these by the thousand, and the usual way to read one is by eye.

The encoding is in the JNI specification's Design Overview under "Resolving Native
Method Names", which is unusually good fortune: every other scheme here had to be
transcribed from a reference implementation, and this one is written down normatively.
See `_parser.py` for what it says and for the one thing it does not preserve.

Reference: https://docs.oracle.com/en/java/javase/17/docs/specs/jni/design.html
"""

from ...core.ast import Node
from ...core.errors import LimitExceeded, NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.registry import register
from . import nodes
from ._parser import PREFIX, DemangleFailure, JniSymbol, descriptor_types, parse_jni_symbol

__all__ = ["PLUGIN", "JniSymbol", "descriptor_types", "detect", "parse", "parse_jni_symbol"]


def detect(name):
    """Whether `name` is a JNI native method symbol.

    The prefix is distinctive but not proof: a C function may be called `Java_helper`,
    and claiming one would rewrite a name this library was not sure about. So the name
    must also *decode*: a declaring class as well as a method -- one separator at least,
    which `Java_helper` does not have -- and, where a signature is present, one that is
    a valid JVM descriptor. `Java_C_m__Q` is refused because `Q` names no type, and
    `Java_C_m__` because the separator promises a signature that is not there.

    A malformed *escape* is not a refusal, because it cannot be told from the thing it
    would be malformed as: `_0abc` is `_0` and three hex digits, and it is also a `/`
    followed by `0abc`. The mangling gives `/` no escape of its own, so the second
    reading is always available and the first is never certain.
    """
    if not name.startswith(PREFIX):
        return False
    try:
        return parse_jni_symbol(name) is not None
    except DemangleFailure:
        return False


#: What each builder class answered to `_wants_structure`, asked once per class rather
#: than once per name: the answer is a property of the builder's type.
_STRUCTURED = {}


def _wants_structure(builder):
    cls = type(builder)
    answer = _STRUCTURED.get(cls)
    if answer is None:
        answer = _STRUCTURED[cls] = isinstance(builder.raw(""), Node)
    return answer


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=None):
    """Parse a JNI symbol into `builder`."""
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)
    if not mangled.startswith(PREFIX):
        raise NotMangledError(mangled, "not a JNI native method name")
    try:
        symbol = parse_jni_symbol(mangled, limits)
    except DemangleFailure as failure:
        raise ParseError(mangled, 0, str(failure)) from None
    if len(symbol.text) > limits.max_output:
        raise LimitExceeded(mangled, "output length", limits.max_output)
    if _wants_structure(builder):
        return nodes.build(symbol)
    return builder.raw(symbol.text)


PLUGIN = LanguagePlugin(
    name="jni",
    node_kinds=("name", "parameters", "path", "symbol"),
    detect=detect,
    parse=parse,
    description="JNI native method names (Java_pkg_Class_method)",
    aliases=("java",),
    # `J` is the only start these have, which keeps the parse off every other symbol in
    # a binary. `priority` is ascending -- lower is offered first -- and this goes early
    # because the prefix is unambiguous among the schemes here and the parse itself is
    # the claim: nothing else starts `Java_` and decodes as one of these.
    first_characters="J",
    priority=15,
)

register(PLUGIN)
