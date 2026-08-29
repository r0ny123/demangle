"""Reading Go symbol names.

Go does not mangle in the sense the other schemes do -- there is no grammar compressing
a declaration into an encoded string. What it does is build a symbol name by joining a
package path, a receiver and a function name with dots, and *escape* the bytes that would
make that ambiguous. The escaping is the part that has to be undone, and it is not
cosmetic: `example.com/x/v2%2e5.T.M` splits into a package and a name at a different
place than `example.com/x/v2.5.T.M` would, so a tool that reads the raw form and splits
on `.` attributes the symbol to the wrong package.

The rules here are taken from the Go distribution itself, `cmd/internal/objabi/path.go`,
which is the code that produced the names -- not from a third-party demangler's reading
of them. `PathToPrefix` escapes a byte when it is `<= ' '`, or `'.'` after the last
`'/'`, or `'%'`, or `'"'`, or `>= 0x7F`; `PrefixToPath` reverses that and rejects a `%`
not followed by two hex digits. Both are reproduced below, and the round trip is checked
against every symbol in a real Go binary rather than against examples.

What a name looks like, all verified against go1.24.7 output:

    example.com/m/pkg.Function                  a plain function
    example.com/m/pkg.Type.Method               a value receiver
    example.com/m/pkg.(*Type).Method            a pointer receiver
    example.com/m/pkg.Function[go.shape.int]    a generic instantiation
    example.com/m/pkg..dict.Function[int]       its dictionary
    example.com/m/pkg.Function.func1            a closure inside it
    go:itab.*errors.errorString,error           an itab: concrete type, interface
    type:.eq.example.com/m/pkg.Type             a generated equality routine
"""

from ...core.errors import NotMangledError, ParseError

__all__ = [
    "GoSymbol",
    "escape_path",
    "parse_go_symbol",
    "unescape_path",
]

#: Prefixes the linker gives its own generated symbols. `go:` and `type:` are current;
#: `go.` and `type.` are what Go wrote before 1.20 and still appear in older binaries.
GENERATED_PREFIXES = ("go:", "type:", "go.", "type.")

_HEX = "0123456789abcdefABCDEF"


def escape_path(path):
    """Escape a package path the way `objabi.PathToPrefix` does.

    Operates on UTF-8 *bytes*, as the Go implementation does. This is not a detail: `ü`
    is one Python character but two bytes, and Go writes it `%c3%bc`. Escaping the
    character would produce `%fc`, which is not what any Go binary contains.

    Kept beside the decoder because it is what makes the decoder testable -- for every
    symbol in a real binary, escaping what we decoded must give back the original bytes.
    A decoder checked only against examples passes while quietly mishandling whatever
    the examples missed.
    """
    raw = path.encode("utf-8", "surrogateescape")
    slash = raw.rfind(b"/")
    out = []
    for index, byte in enumerate(raw):
        if byte <= 0x20 or (byte == 0x2E and index > slash) or byte in (0x25, 0x22) or byte >= 0x7F:
            out.append(f"%{byte:02x}")
        else:
            out.append(chr(byte))
    return "".join(out)


def unescape_path(prefix):
    """Reverse `escape_path`, as `objabi.PrefixToPath` does.

    Raises `ValueError` on a malformed escape, matching the Go implementation, which
    refuses rather than passing the `%` through: a symbol carrying one is not a symbol
    the Go linker wrote.
    """
    if "%" not in prefix:
        return prefix
    raw = prefix.encode("utf-8", "surrogateescape")
    out = bytearray()
    index = 0
    while index < len(raw):
        if raw[index] != 0x25:
            out.append(raw[index])
            index += 1
            continue
        # Go's own bound: `if i+2 >= len(s)` is the error case, so two hex digits must
        # both be present.
        if index + 2 >= len(raw):
            raise ValueError("escape sequence must contain two hex digits")
        digits = raw[index + 1 : index + 3].decode("ascii", "replace")
        if digits[0] not in _HEX or digits[1] not in _HEX:
            raise ValueError(f"escape sequence {chr(0x25) + digits!r} must contain two hex digits")
        out.append(int(digits, 16))
        index += 3
    return out.decode("utf-8", "surrogateescape")


class GoSymbol:
    """One parsed Go symbol.

    `package` and `name` are decoded; `raw` is what was read. `receiver` is the type a
    method is on, without its `*`, and `pointer_receiver` says which form it was written
    in. `generic` holds the text between the brackets of an instantiation, and
    `generated` names the linker's own prefix for a symbol that has one.
    """

    __slots__ = ("generated", "generic", "name", "package", "pointer_receiver", "raw", "receiver")

    def __init__(self, raw, package="", name="", receiver=None, pointer_receiver=False, generic=None, generated=""):
        self.raw = raw
        self.package = package
        self.name = name
        self.receiver = receiver
        self.pointer_receiver = pointer_receiver
        self.generic = generic
        self.generated = generated

    @property
    def text(self):
        """The symbol with its escapes decoded, and nothing else changed.

        Deliberately not a new spelling. Go's own tooling -- `go tool nm`, `go tool
        objdump`, pprof, delve -- prints these names as they stand, so a reader
        recognises them; inventing a prettier form would mean this library is the only
        thing in the ecosystem showing one. What it adds is the decoding, which no Go
        tool does and which is the one part that is genuinely encoded.
        """
        if not self.package:
            return self.generated + self.name
        return f"{self.generated}{self.package}.{self.name}"


def _is_text(decoded):
    """Whether `decoded` is a string that can be written back out as UTF-8."""
    try:
        decoded.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _split_package(text):
    """Split a symbol into its package path and the rest.

    The package path ends at the first `.` that follows the last `/`. That is exactly
    why the escaping exists: a `.` inside the final path element is written `%2e`, so
    the first literal `.` after the last `/` is unambiguously the separator.
    """
    slash = text.rfind("/")
    dot = text.find(".", slash + 1)
    if dot < 0:
        return None, text
    return text[:dot], text[dot + 1 :]


def parse_go_symbol(symbol):
    """Parse `symbol`, returning a `GoSymbol`, or raise.

    Raises `NotMangledError` for anything without the shape of a Go symbol and
    `ParseError` for something Go-shaped this cannot read.
    """
    if not symbol:
        raise NotMangledError(symbol, "empty name")

    generated = ""
    body = symbol
    for prefix in GENERATED_PREFIXES:
        if symbol.startswith(prefix):
            generated, body = prefix, symbol[len(prefix) :]
            break

    package, rest = _split_package(body)
    if package is None:
        if not generated:
            raise NotMangledError(symbol, "no package separator")
        package, rest = "", body

    try:
        package = unescape_path(package)
    except ValueError as error:
        raise ParseError(symbol, None, str(error)) from error
    if not _is_text(package):
        # `unescape_path` is a faithful port of `PrefixToPath`, which works on bytes and
        # is content to hand back whatever the escapes decoded to. This package returns
        # `str`, so a decoding that is not valid UTF-8 arrives as lone surrogates -- a
        # string Python will not encode, so a caller writing the result to a file, a
        # socket or JSON gets a `UnicodeEncodeError` out of a function documented never
        # to raise. `PathToPrefix` only ever produces this from a path that was not text
        # to begin with, which the module system does not permit, so refusing costs
        # nothing real and the name comes back unchanged instead.
        raise ParseError(symbol, None, "package path does not decode to text")

    receiver, pointer, generic = None, False, None
    name = rest

    if name.startswith("(*") and ")." in name:
        close = name.index(").")
        receiver, pointer = name[2:close], True
        name = name[close + 2 :]

    if "[" in name and name.endswith("]"):
        open_bracket = name.index("[")
        generic = name[open_bracket + 1 : -1]
        name = name[:open_bracket]

    return GoSymbol(
        raw=symbol,
        package=package,
        name=rest,
        receiver=receiver,
        pointer_receiver=pointer,
        generic=generic,
        generated=generated,
    )
