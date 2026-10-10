"""Reading JNI native method names.

The one scheme here whose encoding is written down normatively and in full: the JNI
specification's Design Overview, "Resolving Native Method Names". Everything below is
that section, and nothing below is inferred from examples.

    Java_<mangled class name>_<mangled method name>[__<mangled argument signature>]

The mangling is a character escape, not a grammar. Alphanumerics pass through, `/`
becomes `_`, and the four characters that would then be ambiguous get a numbered escape:

    _1  an underscore that was in the name
    _2  `;`, which ends a class name inside a descriptor
    _3  `[`, which opens an array
    _0XXXX  any other character, as four lowercase hex digits

The argument signature is present only where the method is *overloaded*, and it is the
parameter part of the JVM descriptor with the brackets and the return type left off.
Which is what makes the trailing `__` unambiguous: an underscore that was written in a
name is always `_1`, so two in a row can only be the separator.

What cannot be recovered
------------------------
The boundary between the package, the class and the method. All three are joined with
`_`, and `/` is also `_`, so `Java_a_b_c` is `a.b.c` -- and whether that is class `a.b`
method `c` or class `a` method `b_c`... is not in the name. The last component is taken
as the method, which is what every tool that reads these does, and the spelling puts the
whole thing in one dotted path so the guess is not written down as a structure that
claims more than it knows.

An inner class is `Foo$Bar`, mangled `Foo_00024Bar`. That decodes exactly; it is only
the *package* boundary that is lost.
"""

import re

from ...core.limits import DEFAULT_LIMITS

__all__ = ["PREFIX", "JniSymbol", "descriptor_types", "parse_jni_symbol"]

#: `JNI_OnLoad` and `JNI_OnUnload` are plain C names the VM looks up, deliberately not
#: claimed.
PREFIX = "Java_"

#: JVMS 4.3.2.
PRIMITIVES = {
    "B": "byte",
    "C": "char",
    "D": "double",
    "F": "float",
    "I": "int",
    "J": "long",
    "S": "short",
    "V": "void",
    "Z": "boolean",
}

#: `_` followed by anything else is the separator that stood for a `/`.
_ESCAPE = re.compile(r"_(?:([123])|0([0-9a-fA-F]{4}))")

_UNESCAPED = {"1": "_", "2": ";", "3": "["}


class DemangleFailure(Exception):
    """This name is not one this parser can read."""


class JniSymbol:
    """One parsed JNI symbol: its dotted path, and its parameter types where it has any."""

    __slots__ = ("declaring", "method", "parameters", "raw", "text")

    def __init__(self, raw, declaring, method, parameters):
        self.raw = raw
        self.declaring = declaring
        self.method = method
        #: `None` where the symbol carries no signature (only an overloaded native does);
        #: `()` would say the method takes nothing.
        self.parameters = parameters
        joined = f"{declaring}.{method}" if declaring else method
        self.text = joined if parameters is None else f"{joined}({', '.join(parameters)})"


def unescape(text):
    """Undo the character mangling, or raise where it is not well formed."""
    out = []
    at = 0
    while at < len(text):
        char = text[at]
        if char != "_":
            out.append(char)
            at += 1
            continue
        found = _ESCAPE.match(text, at)
        if found is None:
            out.append("/")
            at += 1
            continue
        simple, hexadecimal = found.groups()
        at = found.end()
        if simple:
            out.append(_UNESCAPED[simple])
            continue
        unit = int(hexadecimal, 16)
        if 0xD800 <= unit <= 0xDBFF:
            # A character outside the BMP is two `_0XXXX` code units (`_0d83d_0de00` for
            # U+1F600); decoded singly they give lone surrogates Python cannot encode.
            trailing = _ESCAPE.match(text, at)
            if trailing is None or trailing.group(1) is not None:
                raise DemangleFailure("a high surrogate with no low surrogate after it")
            low = int(trailing.group(2), 16)
            if not 0xDC00 <= low <= 0xDFFF:
                raise DemangleFailure("a high surrogate with no low surrogate after it")
            out.append(chr(0x10000 + ((unit - 0xD800) << 10) + (low - 0xDC00)))
            at = trailing.end()
            continue
        if 0xDC00 <= unit <= 0xDFFF:
            # No Java identifier holds a lone low surrogate.
            raise DemangleFailure("a low surrogate with no high surrogate before it")
        out.append(chr(unit))
    return "".join(out)


def descriptor_types(text):
    """The parameter types of a JVM descriptor's argument list, spelled as source.

    `text` is the *unescaped* argument run -- no brackets and no return type, which is
    what JNI mangles. `Ljava/lang/String;I[B` is `java.lang.String`, `int`, `byte[]`.
    """
    found = []
    at = 0
    while at < len(text):
        arrays = 0
        while at < len(text) and text[at] == "[":
            arrays += 1
            at += 1
        if at >= len(text):
            raise DemangleFailure("array descriptor with no element type")
        char = text[at]
        at += 1
        if char == "V":
            # `V` is a return type only (JVMS 4.3.2), never a parameter or array element.
            raise DemangleFailure("void is not a parameter type")
        if char in PRIMITIVES:
            spelled = PRIMITIVES[char]
        elif char == "L":
            end = text.find(";", at)
            if end < 0:
                raise DemangleFailure("class descriptor with no terminator")
            spelled = text[at:end].replace("/", ".")
            if not spelled:
                raise DemangleFailure("class descriptor with no name")
            at = end + 1
        else:
            raise DemangleFailure(f"unknown type descriptor {char!r}")
        found.append(spelled + "[]" * arrays)
    return tuple(found)


def parse_jni_symbol(name, limits=DEFAULT_LIMITS):
    """Parse `name`, returning a `JniSymbol`, or raise `DemangleFailure`."""
    if not name.startswith(PREFIX):
        raise DemangleFailure("not a JNI native method name")
    body = name[len(PREFIX) :]
    if not body:
        raise DemangleFailure("nothing after the prefix")

    # Not simply the first `__`: a `/` before an escape also writes one
    # (`com/example/Foo/π` is `com_example_Foo__003c0`). The real separator is
    # followed by a JVM descriptor, so each candidate is tried in turn.
    path = None
    parameters = None
    at = body.find("__")
    while at >= 0:
        tail_at = at + 2
        if (
            tail_at < len(body)
            and body[tail_at] not in PRIMITIVES
            and body[tail_at] != "L"
            and not body.startswith(("_3", "_0"), tail_at)
        ):
            # A descriptor opens on an ASCII type code or an escaped array bracket.
            # `_0` is retained for callers escaping an ASCII type code explicitly.
            # Unicode names preceded by `/` also make `__`, but their digit-led
            # suffix cannot be a signature; avoid copying/decoding each full tail.
            at = body.find("__", at + 1)
            continue
        head, tail = body[:at], body[tail_at:]
        if head:
            try:
                head_path = unescape(head)
                if "//" in head_path:
                    # An empty component already decoded in the prefix cannot be
                    # repaired by any later separator. The final path check refuses it.
                    break
                # An empty tail is valid: an overloaded method taking no arguments.
                candidate = descriptor_types(unescape(tail))
            except DemangleFailure:
                candidate = None
            if candidate is not None:
                path, parameters = head_path, candidate
                break
        at = body.find("__", at + 1)
    if path is None:
        path = unescape(body)
        if "//" in path:
            # A non-separator `__` leaves no empty component, so an empty one means the
            # `__` was the separator and its tail was not a descriptor.
            raise DemangleFailure("no component between separators")
    declaring, _, method = path.rpartition("/")
    if not method:
        raise DemangleFailure("no method name")
    if not declaring:
        # A native method is always in a class; otherwise any C `Java_helper` is claimed.
        raise DemangleFailure("no declaring class")
    return JniSymbol(name, declaring.replace("/", "."), method, parameters)
