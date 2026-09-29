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

Escapes are not confined to the leading package path. A generic instantiation, a
receiver and a linker-generated symbol all carry *type strings*, and a type string
writes each named type with its package path escaped the same way: the linker emits
`main..dict.Gen[example.com/tag/v2%2e5.K]` and `type:.eq.main.Box[example.com/tag/v2%2e5.K]`
for a package directory called `v2.5`. The one place a `%` is not an escape is inside
a struct tag, which a type string quotes verbatim: `type:.eq.struct { S string
"json:\\"50%\\""; K example.com/tag/v2%2e5.K }` is a real symbol (all of these are
go1.24.7 output) and decoding the tag's `%` would either rewrite it or refuse the name.
So escapes decode everywhere outside double quotes, and nowhere inside them.
"""

from ...core.errors import NotMangledError, ParseError

__all__ = [
    "GoSymbol",
    "escape_path",
    "parse_go_symbol",
    "unescape_path",
]

GENERATED_PREFIXES = ("go:", "type:", "go.", "type.")
"""Prefixes the linker gives its own generated symbols.

`go:` and `type:` are current; `go.` and `type.` are what Go wrote before 1.20 and still
appear in older binaries.
"""

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
    `generated` names the linker's own prefix for a symbol that has one -- such a symbol
    has no package, receiver or instantiation of its own: `name` is the linker's text.
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


def _unescape_outside_quotes(text):
    """Decode every escape in `text` that is not inside a double-quoted string.

    A symbol's package paths -- the leading one, and any inside a type string -- were
    escaped by `PathToPrefix`, and a struct tag inside a type string was written with
    `strconv.Quote`, which escapes `"` and `\\` and leaves `%` alone. So a `%` outside
    quotes is always an escape and a `%` inside them never is. An unterminated quote
    runs to the end of the text, which is also what a Go reader of it would do.

    Raises `ValueError` as `unescape_path` does, for an escape outside quotes that is
    not two hex digits.
    """
    if "%" not in text:
        return text
    out = []
    index = 0
    while True:
        quote = text.find('"', index)
        if quote < 0:
            out.append(unescape_path(text[index:]))
            return "".join(out)
        out.append(unescape_path(text[index:quote]))
        end = quote + 1
        while end < len(text):
            if text[end] == "\\":
                end += 2
            elif text[end] == '"':
                end += 1
                break
            else:
                end += 1
        out.append(text[quote:end])
        index = end


def _package_boundary(text):
    """Where the package path ends: the index of its last `/` and of the `.` after it.

    The package path ends at the first `.` that follows the last `/`. That is exactly
    why the escaping exists: a `.` inside the final path element is written `%2e`, so
    the first literal `.` after the last `/` is unambiguously the separator.

    "The last `/`" is the last one *the path* could contain. A receiver's parentheses,
    a generic argument list's brackets and a struct tag's quotes can each carry a type
    string with slashes of its own -- `example.com/x.F[go.shape.[]internal/sync.node]`
    is in package `example.com/x`, not `example.com/x.F[go.shape.[]internal/sync` --
    and an import path can contain none of those three characters, so the search for
    the slash stops at the first of them. Returns `(-1, -1)` for text with no slash and
    `(slash, -1)` for one with no separator after it.
    """
    limit = len(text)
    for stop in '(["':
        index = text.find(stop)
        if 0 <= index < limit:
            limit = index
    slash = text.rfind("/", 0, limit)
    return slash, text.find(".", slash + 1)


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

    if generated:
        # What follows the prefix is the linker's own text -- a type string, a pair of
        # them, an object's name -- and not a package-qualified declaration. It is not
        # split: `type:.eq.[2]string` has no package, and reading its leading `.` as
        # the separator dropped the dot. Its escapes are decoded where they stand.
        package, rest = "", body
    else:
        _, dot = _package_boundary(body)
        if dot < 0:
            raise NotMangledError(symbol, "no package separator")
        package, rest = body[:dot], body[dot + 1 :]
        if not package:
            raise NotMangledError(symbol, "empty package")
    if not rest:
        raise NotMangledError(symbol, "empty name")

    try:
        package = unescape_path(package)
        name = _unescape_outside_quotes(rest)
    except ValueError as error:
        raise ParseError(symbol, None, str(error)) from error
    if not _is_text(package) or not _is_text(name):
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
    if not generated:
        declared = name
        if declared.startswith("(*") and ")." in declared:
            close = declared.index(").")
            receiver, pointer = declared[2:close], True
            declared = declared[close + 2 :]
        if "[" in declared and declared.endswith("]"):
            generic = declared[declared.index("[") + 1 : -1]

    return GoSymbol(
        raw=symbol,
        package=package,
        name=name,
        receiver=receiver,
        pointer_receiver=pointer,
        generic=generic,
        generated=generated,
    )
