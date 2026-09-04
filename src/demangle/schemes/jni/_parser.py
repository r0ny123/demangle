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

#: What every native method's symbol starts with. `JNI_OnLoad` and `JNI_OnUnload` are
#: plain C names the VM looks up literally and are deliberately not claimed.
PREFIX = "Java_"

#: The JVM's primitive type descriptors, from the class file format (JVMS 4.3.2).
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

#: A well-formed escape: `_1`, `_2`, `_3`, or `_0` and four hex digits. `_` followed by
#: anything else is the separator that stood for a `/`.
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
        #: `None` where the symbol carries no signature at all, which is the common case:
        #: only an *overloaded* native carries one. Not `()`, which would say the method
        #: takes nothing.
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
            # A bare `_` is a `/`: the package separator, which the mangling writes with
            # no escape at all because it is the one character it can spare.
            out.append("/")
            at += 1
            continue
        simple, hexadecimal = found.groups()
        out.append(_UNESCAPED[simple] if simple else chr(int(hexadecimal, 16)))
        at = found.end()
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
            # `V` is the return type, not a parameter type (JVMS 4.3.2). The JNI
            # overload signature carries only parameters, so it never appears here --
            # neither bare nor as an array element.
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

    # The overload separator, which is not simply the first `__`. A `_` written in a
    # name is mangled `_1`, so a name never contributes one on its own -- but a `/`
    # *immediately before an escape* does: `com/example/Foo/\u03c0` mangles to
    # `com_example_Foo__003c0`, where the `__` is a package separator and the start of a
    # character escape rather than the overload marker.
    #
    # What tells them apart is what follows: after the real separator comes a JVM
    # descriptor. So each candidate is tried in turn and the first whose tail is one is
    # taken, which is the only reading that can be checked rather than guessed.
    path = None
    parameters = None
    at = body.find("__")
    while at >= 0:
        head, tail = body[:at], body[at + 2 :]
        if head:
            try:
                head_path = unescape(head)
                if "//" in head_path:
                    raise DemangleFailure("no component between separators")
                # An *empty* tail is a valid signature: an overloaded method taking no
                # arguments is written `Java_pkg_C_m__`, with the empty argument list
                # the long name promises.
                candidate = descriptor_types(unescape(tail))
            except DemangleFailure:
                candidate = None
            if candidate is not None:
                path, parameters = head_path, candidate
                break
        at = body.find("__", at + 1)
    if path is None:
        # No separator, or none whose tail is a descriptor: the whole run is the name.
        path = unescape(body)
        if "//" in path:
            # Which it can only be if every component is a real one. A `__` that is not
            # the overload separator comes from a `/` before an escape, and that leaves
            # no empty component behind -- so an empty one means the `__` *was* the
            # separator and what followed it was not a descriptor.
            raise DemangleFailure("no component between separators")
    declaring, _, method = path.rpartition("/")
    if not method:
        raise DemangleFailure("no method name")
    if not declaring:
        # A native method is always declared in a class, so there is always at least one
        # separator. Without this, any C function whose name merely starts `Java_` --
        # `Java_helper`, say -- would be claimed and rewritten, which is the one outcome
        # this library treats as worse than declining.
        raise DemangleFailure("no declaring class")
    return JniSymbol(name, declaring.replace("/", "."), method, parameters)
