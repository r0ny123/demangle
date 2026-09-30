import re
import string
import unicodedata
from typing import NoReturn

from ...core.limits import DEFAULT_LIMITS
from . import nodes


class OutputTooLong(Exception):
    """The printer was asked to write more than the caller's `max_output` allows.

    Raised *while* printing rather than checked afterwards, which is the difference
    between a bound and a report. `_RMC0FGZZZZ_Eu` is fourteen characters and asks for
    fourteen million bound lifetimes: checking the length of the finished string would mean
    building all of it first, and one more character multiplies that work sixty-two-fold.
    The count comes out of a base-62 field, so the
    input grows by one character while the work grows sixty-two-fold.
    """


_U64_MAX = (1 << 64) - 1


class UnableTov0Demangle(Exception):
    def __init__(self, given_str, message="Not able to demangle the given string using v0Demangler"):
        self.message = message
        self.given_str = given_str
        super().__init__(self.message)

    def __str__(self):
        return f"[{self.given_str}] {self.message}"


class RecursedTooDeep(UnableTov0Demangle):
    """Nesting passed the caller's `Limits.max_depth`: a bound, not a malformed name."""

    def __init__(self, max_depth):
        super().__init__("Recursion limit exceeded")
        self.max_depth = max_depth


#: rustc-demangle holds a fixed `MAX_DEPTH` of 500; this follows the caller's `Limits`.
_DEFAULT_MAX_DEPTH = DEFAULT_LIMITS.max_depth


#: Shared with `_legacy`: both schemes accept a vendor suffix on the same terms.
_PUNCTUATION = frozenset(string.punctuation)

_SYMBOL_LIKE = re.compile(r"[0-9A-Za-z" + re.escape(string.punctuation) + r"]*\Z")

#: The reference's set verbatim: upper-case hex plus `@` (a versioned symbol keeps its
#: `@@VERS` inside the run). A lower-case run is not an LLVM suffix and must not be cut.
_LLVM_HASH_CHARACTERS = frozenset(string.digits + "ABCDEF@")

_LLVM_MARKER = ".llvm."


def _strip_llvm_suffix(text):
    """Drop a `.llvm.<hash>` internalisation suffix, which names no part of the symbol.

    The leading `.` is part of the marker, and dropping it from the search deleted text
    that belongs to the symbol: `_RNvCs1_1a1fllvm.123` has no suffix at all -- the
    reference refuses it, because `llvm.123` is left over and a leftover has to open
    with a `.` -- and this read it as `a::f`, throwing eight characters away to get
    there.

    `find` rather than `rfind`, which is `rustc_demangle::demangle`'s own choice: the
    leftmost `.llvm.` whose tail is a hash is the one that goes, along with everything
    after it.
    """
    marker = text.find(_LLVM_MARKER)
    if marker < 0:
        return text
    if not _LLVM_HASH_CHARACTERS.issuperset(text[marker + len(_LLVM_MARKER) :]):
        return text
    return text[:marker]


def _is_symbol_like(text):
    """Whether every character is one a linker can put in a symbol name.

    The reference's test for a vendor suffix it is willing to carry through: ASCII
    alphanumeric or ASCII punctuation, and nothing else. A space or a control character
    means the trailing text is not a suffix at all, and the name is refused rather than
    half-read.
    """
    return _SYMBOL_LIKE.match(text) is not None


class V0Demangler:
    """Reads one v0 name. Single use: the name, its suffix and the cursor live here.

    `RustDemangler` builds a fresh one per name for that reason -- see its docstring.
    """

    __slots__ = ("inpstr", "keep_hash", "max_depth", "suffix")

    def __init__(self, keep_hash: bool = False, max_depth: int = _DEFAULT_MAX_DEPTH):
        self.max_depth = max_depth
        self.inpstr = ""
        self.suffix = ""
        self.keep_hash = keep_hash

    def demangle(self, inpstr: str, limit: int) -> str:
        """Demangle to text, writing at most `limit` characters."""
        return self._run(inpstr, TextSink(limit)).text()

    def structure(self, inpstr: str, limit: int):
        """Demangle to a tree, which renders to exactly what `demangle` returns.

        The same printer over the same input; only the sink differs. There is no second
        traversal that could disagree with the first.
        """
        sink = self._run(inpstr, TreeSink(limit))
        return nodes.Symbol(sink.parts(), suffix=self.suffix)

    def _run(self, inpstr, sink):
        self.suffix = ""

        if inpstr.startswith("__R"):
            self.inpstr = inpstr[3:]
        elif inpstr.startswith("_R"):
            self.inpstr = inpstr[2:]
        elif inpstr.startswith("R"):
            # On Windows, dbghelp strips leading underscores, so the bare form is
            # accepted too -- the same reason the legacy scheme takes a bare `ZN`.
            self.inpstr = inpstr[1:]
        else:
            raise UnableTov0Demangle(inpstr)
        self.sanity_check(self.inpstr)

        self.inpstr = _strip_llvm_suffix(self.inpstr)

        parser = Parser(self.inpstr, 0, self.keep_hash, self.max_depth)
        try:
            Printer(parser, sink, 0).print_path(True)
        except OutputTooLong:
            self._refuse_if_malformed(inpstr)
            raise
        # An <instantiating-crate> is a second <path>; it is skipped, not printed.
        if (len(parser.inn) > parser.next_val) and parser.inn[parser.next_val].isupper():
            parser.skip_path()

        # The reference keeps unconsumed input only as a `.`-introduced vendor suffix and
        # refuses the name otherwise, so `_RNvC1a1b1b` is not read as `a::b`.
        residual = parser.inn[parser.next_val :]
        if residual and not (residual.startswith(".") and _is_symbol_like(residual)):
            raise UnableTov0Demangle(inpstr)

        if residual:
            self.suffix = residual
            sink.emit(self.suffix)

        return sink

    def _refuse_if_malformed(self, inpstr):
        """Raise `UnableTov0Demangle` if the name would not have read anyway.

        Only reached when printing hit `max_output`, which is where the reading pass
        stops before it can say what follows the symbol path. Reading the path again
        without printing it costs nothing on the path that answers, and keeps a
        malformed name reported as malformed rather than as a bound the caller set.
        """
        probe = Parser(self.inpstr, 0, self.keep_hash, self.max_depth)
        probe.skip_path()
        if (len(probe.inn) > probe.next_val) and probe.inn[probe.next_val].isupper():
            probe.skip_path()
        trailing = probe.inn[probe.next_val :]
        if trailing and not (trailing.startswith(".") and _is_symbol_like(trailing)):
            raise UnableTov0Demangle(inpstr)

    def sanity_check(self, inpstr: str):
        if not inpstr or not inpstr[0].isupper():
            raise UnableTov0Demangle(inpstr)

        # The reference refuses any byte with bit 7 set; v0 spells non-ASCII identifiers in
        # punycode, never literally.
        if not inpstr.isascii():
            raise UnableTov0Demangle(inpstr)


class Ident:
    __slots__ = ("ascii", "disp", "out", "out_len", "punycode", "small_punycode_len")

    def __init__(self, ascii: str, punycode: str) -> None:
        self.ascii = ascii
        self.punycode = punycode
        self.small_punycode_len = 128
        self.disp = ""
        self.out: list = []
        self.out_len = 0

    def try_small_punycode_decode(self) -> bool:
        """Decode into the fixed buffer and append the result, reporting success."""
        self.out = ["\0"] * self.small_punycode_len
        self.out_len = 0
        if not self.punycode_decode():
            return False
        self.disp += "".join(self.out[: self.out_len])
        return True

    def insert(self, i: int, c: str) -> bool:
        """Insert character at position i, shifting existing chars right.

        Returns True on success, False if buffer overflow would occur.
        """
        if self.out_len >= self.small_punycode_len:
            return False
        j = self.out_len
        self.out_len += 1

        while j > i:
            self.out[j] = self.out[j - 1]
            j -= 1
        self.out[i] = c
        return True

    def punycode_decode(self) -> bool:
        """Decode `self.punycode` into `self.out`, reporting whether it succeeded.

        RFC 3492 decoding, as Rust's v0 mangling uses it for non-ASCII identifiers
        (`u` followed by a punycode-encoded name). Returns True only when the whole
        input was consumed and every code point placed.
        """
        count = 0
        punycode_bytes = self.punycode
        try:
            punycode_bytes[count]
        except IndexError:
            return False

        lent = 0
        for c in self.ascii:
            if not self.insert(lent, c):
                return False
            lent += 1

        base = 36
        t_min = 1
        t_max = 26
        skew = 38
        damp = 700
        bias = 72
        i = 0
        n = 0x80
        while True:
            delta = 0
            w = 1
            k = 0
            while True:
                k += base
                t = min(max((k - bias), t_min), t_max)
                if count >= len(punycode_bytes):
                    return False
                d = punycode_bytes[count]
                count += 1
                if d in string.ascii_lowercase:
                    d = ord(d) - ord("a")
                elif d in string.digits:
                    d = 26 + (ord(d) - ord("0"))
                else:
                    return False

                delta = delta + (d * w)
                if d < t:
                    break
                w *= base - t

            lent += 1
            i += delta
            n += i // lent
            i %= lent

            try:
                c = chr(n)
            except (ValueError, OverflowError):
                return False

            # `char::from_u32` refuses surrogates, which `chr` accepts; the reference falls
            # back to `punycode{...}`.
            if 0xD800 <= n <= 0xDFFF:
                return False

            if not self.insert(i, c):
                return False
            i += 1

            try:
                punycode_bytes[count]
            except IndexError:
                # Input exhausted with every code point placed: the only success exit.
                return True

            delta = delta // damp
            damp = 2

            delta += delta // lent
            k = 0
            while delta > ((base - t_min) * t_max) // 2:
                delta = delta // (base - t_min)
                k += base
            bias = k + ((base - t_min + 1) * delta) // (delta + skew)

    def display(self) -> None:
        # Most identifiers are plain ASCII: skip allocating the decode buffer.
        if not self.punycode:
            self.disp += self.ascii
            return
        if self.try_small_punycode_decode():
            return
        else:
            if self.punycode:
                self.disp += "punycode{"

                if self.ascii:
                    self.disp += self.ascii
                    self.disp += "-"
                self.disp += self.punycode
                self.disp += "}"
            else:
                self.disp += self.ascii


#: Unicode general categories whose members Rust refuses to print literally: libcore's
#: `printable.rs` is generated from these, with the space carved back out.
_UNPRINTABLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp", "Zs"})

#: `Grapheme_Extend`, which `escape_debug` escapes, is `Mn | Me | Other_Grapheme_Extend`.
#: The third is not derivable from `unicodedata`, so it is carried as an explicit list:
#: every code point rustc-demangle escapes that the two categories do not, found by
#: sweeping all scalar values through the reference.
_GRAPHEME_EXTEND_CATEGORIES = frozenset({"Mn", "Me"})
_OTHER_GRAPHEME_EXTEND = frozenset(
    [
        0x09BE,
        0x09D7,
        0x0B3E,
        0x0B57,
        0x0BBE,
        0x0BD7,
        0x0CC0,
        0x0CC2,
        0x0CC7,
        0x0CC8,
        0x0CCA,
        0x0CCB,
        0x0CD5,
        0x0CD6,
        0x0D3E,
        0x0D57,
        0x0DCF,
        0x0DDF,
        0x1715,
        0x1734,
        0x1B35,
        0x1B3B,
        0x1B3D,
        0x1B43,
        0x1B44,
        0x1BAA,
        0x1BF2,
        0x1BF3,
        0x302E,
        0x302F,
        0xA953,
        0xA9C0,
        0xFF9E,
        0xFF9F,
        0x111C0,
        0x11235,
        0x1133E,
        0x1134D,
        0x11357,
        0x114B0,
        0x114BD,
        0x115AF,
        0x116B6,
        0x11930,
        0x1193D,
        0x11F41,
        0x16FF0,
        0x16FF1,
        0x1D165,
        0x1D166,
        0x1D16D,
        0x1D16E,
        0x1D16F,
        0x1D170,
        0x1D171,
        0x1D172,
    ]
)

#: `char::escape_debug`'s short escapes. Both quotes are here because the reference
#: escapes each inside its own kind of literal; `print_quoted_escaped_chars` undoes that.
_SHORT_ESCAPES = {
    "\0": r"\0",
    "\t": r"\t",
    "\r": r"\r",
    "\n": r"\n",
    "\\": "\\\\",
    '"': r"\"",
    "'": r"\'",
}


def escape_debug(character: str) -> str:
    """One character as `char::escape_debug` would render it.

    Const generic arguments of type `char` and `&str` are printed as Rust source, and the
    reference demangler reaches for the same escaping the standard library uses in
    `{:?}`. Getting this wrong is not cosmetic: an unescaped newline or an unassigned
    codepoint in a symbol name would be copied straight into whatever the caller is
    writing. Follows rustc-demangle 0.1.28 `v0.rs::print_quoted_escaped_chars`, which
    calls `char::escape_debug` per character.
    """
    short = _SHORT_ESCAPES.get(character)
    if short is not None:
        return short
    category = unicodedata.category(character)
    if category in _GRAPHEME_EXTEND_CATEGORIES or ord(character) in _OTHER_GRAPHEME_EXTEND:
        return f"\\u{{{ord(character):x}}}"
    if character != " " and category in _UNPRINTABLE_CATEGORIES:
        return f"\\u{{{ord(character):x}}}"
    return character


def parse_hex_uint(nibbles: str) -> int | None:
    """A `<hex-digits>` run as an integer, or None when it will not fit in 64 bits.

    Leading zeroes are stripped *before* the width test, so a padded encoding of a small
    value still reads as that value. The reference then prints anything wider verbatim
    rather than failing, because a const that large is legal `u128`. From
    rustc-demangle 0.1.28 `v0.rs::HexNibbles::try_parse_uint`.
    """
    trimmed = nibbles.lstrip("0")
    if len(trimmed) > 16:
        return None
    return int(trimmed, 16) if trimmed else 0


def parse_hex_str(nibbles: str) -> str | None:
    """A `<hex-digits>` run as the UTF-8 string it encodes, or None if it is not UTF-8.

    Each byte is a pair of nibbles, so an odd count cannot be a byte string at all. The
    reference validates the whole sequence before printing anything, to avoid emitting
    half a string literal and then failing; decoding eagerly here has the same effect.
    From rustc-demangle 0.1.28 `v0.rs::HexNibbles::try_parse_str_chars`.
    """
    if len(nibbles) % 2 != 0:
        return None
    try:
        return bytes.fromhex(nibbles).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


#: `<const>` leaf tags by body shape (RFC 2603): unsigned integers take bare
#: `<hex-digits>`, signed ones an optional leading `n`; `bool`, `char` and `str` are
#: rendered differently, so they are kept apart.
_CONST_UNSIGNED = ("h", "t", "m", "y", "o", "j")
_CONST_SIGNED = ("a", "s", "l", "x", "n", "i")
_CONST_DATA_ONLY = ("b", "c", "e")


#: What opens a `<path>`. `B` is deliberately absent: in a type position it is a
#: backref to a *type*, which each caller handles itself.
_PATH_TAGS = frozenset("CNMXYI")

_BASIC_TYPES = {
    "b": "bool",
    "c": "char",
    "e": "str",
    "u": "()",
    "a": "i8",
    "s": "i16",
    "l": "i32",
    "x": "i64",
    "n": "i128",
    "i": "isize",
    "h": "u8",
    "t": "u16",
    "m": "u32",
    "y": "u64",
    "o": "u128",
    "j": "usize",
    "f": "f32",
    "d": "f64",
    "z": "!",
    "p": "_",
    "v": "...",
}


_BASE_10 = {char: index for index, char in enumerate(string.digits)}
_BASE_62 = dict(_BASE_10)
_BASE_62.update({char: 10 + index for index, char in enumerate(string.ascii_lowercase)})
_BASE_62.update({char: 36 + index for index, char in enumerate(string.ascii_uppercase)})


class Parser:
    __slots__ = ("depth", "end", "inn", "keep_hash", "max_depth", "next_val")

    def __init__(self, inn: str, next_val: int, keep_hash: bool = False, max_depth: int = _DEFAULT_MAX_DEPTH) -> None:
        self.inn = inn
        self.next_val = next_val
        # On the parser rather than the printer because a backref builds a new printer from
        # it, and a crate root reached through one must spell its disambiguator the same way.
        self.keep_hash = keep_hash
        self.max_depth = max_depth
        self.depth = 0
        self.end = len(inn)

    def eat(self, b: str) -> bool:
        at = self.next_val
        if at < self.end and self.inn[at] == b:
            self.next_val = at + 1
            return True
        return False

    def next_func(self) -> str:
        at = self.next_val
        if at >= self.end:
            raise UnableTov0Demangle(self.inn)
        self.next_val = at + 1
        return self.inn[at]

    def hex_nibbles(self) -> str:
        start = self.next_val
        while True:
            n = self.next_func()
            if n in string.digits or (n in "abcdef"):
                continue
            elif n == "_":
                break
            else:
                raise UnableTov0Demangle(self.inn)
        return self.inn[start : self.next_val - 1]

    def integer_62(self) -> int:
        """`<base-62-number> ::= {<0-9a-zA-Z>} "_"`, valued one more than its digits spell.

        Bounded to 64 bits because the reference is. `integer_62` accumulates into a
        `u64` through `checked_mul`/`checked_add`, so a wider field is a parse error to
        rustc-demangle and to LLVM's port of it; RFC 2603 states no bound, and binutils
        reads such a field by wrapping it. Python has no ceiling of its own to hit here,
        so this one is imposed on purpose: `_RNvCsAAAAAAAAAAA_1a1f` is `a::f` to
        binutils and unreadable to rustc-demangle, and this scheme is a port of
        rustc-demangle. rustc writes a disambiguator that is a truncated 64-bit hash, so
        nothing a compiler emits is anywhere near the edge.
        """
        inn, end = self.inn, self.end
        at = self.next_val
        if at >= end:
            raise UnableTov0Demangle(inn)
        if inn[at] == "_":
            self.next_val = at + 1
            return 0
        x = 0
        while True:
            d = _BASE_62.get(inn[at])
            if d is None:
                raise UnableTov0Demangle(inn)
            x = x * 62 + d
            at += 1
            # Nothing reads the cursor after a refusal, so it is written once on the way out.
            if at >= end:
                raise UnableTov0Demangle(inn)
            if inn[at] == "_":
                self.next_val = at + 1
                if x >= _U64_MAX:
                    raise UnableTov0Demangle(inn)
                return x + 1

    def opt_integer_62(self, tag: str) -> int:
        at = self.next_val
        if at >= self.end or self.inn[at] != tag:
            return 0
        self.next_val = at + 1
        value = self.integer_62()
        if value >= _U64_MAX:
            raise UnableTov0Demangle(self.inn)
        return value + 1

    def namespace(self) -> str | None:
        at = self.next_val
        if at >= self.end:
            raise UnableTov0Demangle(self.inn)
        n = self.inn[at]
        self.next_val = at + 1
        if n.isupper():
            return n
        elif n.islower():
            return None
        else:
            raise UnableTov0Demangle(self.inn)

    def backref(self) -> "Parser":
        s_start = self.next_val - 1
        i = self.integer_62()
        if i >= s_start:
            raise UnableTov0Demangle(self.inn)

        return Parser(self.inn, i, self.keep_hash, self.max_depth)

    def ident(self, build=True):
        """A `<identifier>`: an optional `u`, a decimal length, an optional `_`, the text.

        Written out rather than composed from `eat` and `digit_10` because it runs once
        per path component of every symbol -- 78,000 times over the Rust corpus -- and
        each of those helpers is an interpreter frame around a bounds test.

        `build` is False on the skip pass, which validates the production and throws the
        `Ident` away: half of those 78,000 objects, each with a list of its own, were
        allocated to be discarded. What is read, and what is refused, is the same either
        way -- there is one implementation of the production and this is it.
        """
        inn, end = self.inn, self.end
        at = self.next_val
        is_punycode = at < end and inn[at] == "u"
        if is_punycode:
            at += 1

        # The reference reads the first digit unconditionally, so a non-digit is a failure,
        # not a zero length (`_RNvC_1f` must not read as `::f`). A written `0` is legal.
        if at >= end:
            raise UnableTov0Demangle(inn)
        length = _BASE_10.get(inn[at])
        if length is None:
            raise UnableTov0Demangle(inn)
        at += 1
        if length:
            while True:
                if at >= end:
                    raise UnableTov0Demangle(inn)
                digit = _BASE_10.get(inn[at])
                if digit is None:
                    break
                at += 1
                length = length * 10 + digit

        if at < end and inn[at] == "_":
            at += 1

        start = at
        at += length
        if at > end:
            raise UnableTov0Demangle(inn)
        self.next_val = at

        ident = inn[start:at]
        if is_punycode:
            if "_" in ident:
                i = ident.rindex("_")
                ascii_part, punycode = ident[:i], ident[i + 1 :]
            else:
                ascii_part, punycode = "", ident

            if not punycode:
                raise UnableTov0Demangle(inn)

            return Ident(ascii_part, punycode) if build else None

        return Ident(ident, "") if build else None

    def skip_path(self):
        depth = self.depth
        if depth >= self.max_depth:
            raise RecursedTooDeep(self.max_depth)
        self.depth = depth + 1
        try:
            at = self.next_val
            if at >= self.end:
                raise UnableTov0Demangle(self.inn)
            val = self.inn[at]
            self.next_val = at + 1
            if val == "N":
                # `namespace` inlined (hot path). The input is ASCII, so "a letter" is
                # the whole test; the skip pass does not care which case.
                at = self.next_val
                if at >= self.end or not self.inn[at].isalpha():
                    raise UnableTov0Demangle(self.inn)
                self.next_val = at + 1
                self.skip_path()
                at = self.next_val
                if at < self.end and self.inn[at] == "s":
                    self.opt_integer_62("s")
                self.ident(False)

            elif val == "C":
                at = self.next_val
                if at < self.end and self.inn[at] == "s":
                    self.opt_integer_62("s")
                self.ident(False)
            elif val == "B":
                self.backref()

            elif val == "I":
                self.skip_path()
                while not self.eat("E"):
                    self.skip_generic_arg()

            elif val == "X":
                self.opt_integer_62("s")
                self.skip_path()
                self.skip_type()
                self.skip_path()

            elif val == "M":
                self.opt_integer_62("s")
                self.skip_path()
                self.skip_type()

            elif val == "Y":
                self.skip_type()
                self.skip_path()

            else:
                raise UnableTov0Demangle(self.inn)
        finally:
            self.depth = depth

    def skip_generic_arg(self):
        if self.eat("L"):
            self.integer_62()
        elif self.eat("K"):
            self.skip_const()
        else:
            self.skip_type()

    def skip_type(self):
        depth = self.depth
        if depth >= self.max_depth:
            raise RecursedTooDeep(self.max_depth)
        self.depth = depth + 1
        try:
            self.eat("w")
            at = self.next_val
            if at >= self.end:
                raise UnableTov0Demangle(self.inn)
            n = self.inn[at]
            self.next_val = at + 1
            if n in _BASIC_TYPES:
                pass
            elif n in _PATH_TAGS:
                self.next_val = at
                self.skip_path()
            elif n == "R" or n == "Q":
                # The referent is not optional: skipping only the lifetime would
                # desynchronise every later offset.
                if self.eat("L"):
                    self.integer_62()
                self.skip_type()
            elif n == "P" or n == "O" or n == "S":
                self.skip_type()
            elif n == "A":
                self.skip_type()
                self.skip_const()
            elif n == "T":
                while not self.eat("E"):
                    self.skip_type()
            elif n == "F":
                _binder = self.opt_integer_62("G")
                _is_unsafe = self.eat("U")
                if self.eat("K"):
                    c_abi = self.eat("C")
                    if not c_abi:
                        abi = self.ident()
                        if not abi.ascii or abi.punycode:
                            raise UnableTov0Demangle(self.inn)
                while not self.eat("E"):
                    self.skip_type()
                self.skip_type()
            elif n == "D":
                _binder = self.opt_integer_62("G")
                while not self.eat("E"):
                    self.skip_path()
                    while self.eat("p"):
                        self.ident(False)
                        if self.eat("K"):
                            self.skip_const()
                        else:
                            self.skip_type()
                if not self.eat("L"):
                    raise UnableTov0Demangle(self.inn)
                self.integer_62()
            elif n == "B":
                self.backref()
            elif n == "W":
                self.skip_type()
                self.skip_pattern()
            else:
                self.next_val -= 1
                self.skip_path()
        finally:
            self.depth = depth

    def skip_pattern(self):
        """Advance past one `<pattern>`, the value set a pattern type narrows to."""
        depth = self.depth
        if depth >= self.max_depth:
            raise RecursedTooDeep(self.max_depth)
        self.depth = depth + 1
        try:
            at = self.next_val
            if at >= self.end:
                raise UnableTov0Demangle(self.inn)
            n = self.inn[at]
            self.next_val = at + 1
            if n == "R":
                self.skip_const()
                self.skip_const()
            elif n == "O":
                while not self.eat("E"):
                    self.skip_pattern()
            elif n != "N":
                raise UnableTov0Demangle(self.inn)
        finally:
            self.depth = depth

    def skip_const(self):
        """Advance past one `<const>` without rendering it.

        Structural consts nest, so this needs the same depth guard `skip_type` has: the
        skip pass runs before anything is printed, on input nobody has validated yet.
        """
        depth = self.depth
        if depth >= self.max_depth:
            raise RecursedTooDeep(self.max_depth)
        self.depth = depth + 1
        try:
            if self.eat("B"):
                self.backref()
                return

            ty_tag = self.next_func()
            if ty_tag == "p":
                return

            if ty_tag in _CONST_UNSIGNED or ty_tag in _CONST_DATA_ONLY:
                self.hex_nibbles()
            elif ty_tag in _CONST_SIGNED:
                self.eat("n")
                self.hex_nibbles()
            elif ty_tag in ("R", "Q"):
                if ty_tag == "R" and self.eat("e"):
                    self.hex_nibbles()
                else:
                    self.skip_const()
            elif ty_tag in ("A", "T"):
                while not self.eat("E"):
                    self.skip_const()
            elif ty_tag == "V":
                self.skip_path()
                variant = self.next_func()
                if variant == "T":
                    while not self.eat("E"):
                        self.skip_const()
                elif variant == "S":
                    while not self.eat("E"):
                        self.opt_integer_62("s")
                        self.ident(False)
                        self.skip_const()
                elif variant != "U":
                    raise UnableTov0Demangle(self.inn)
            else:
                raise UnableTov0Demangle(self.inn)
        finally:
            self.depth = depth


def _impl_fields(seen):
    """`(self_type, trait)` from what an impl's parts printed, padded for a text sink."""
    self_type = seen[0] if seen else None
    trait = seen[1] if len(seen) > 1 else None
    return self_type, trait


def _generic_fields(collected):
    """`(base, arguments)` from a generic application's parts."""
    base = collected[0] if collected else None
    return base, collected[1:]


#: What `Printer.node` returns when the sink keeps no structure; cheaper than
#: `contextlib` on a path entered once per grammar production.
class _NoScope:
    __slots__ = ()

    def __enter__(self):
        return _NO_NODE

    def __exit__(self, *exception):
        return False


_NO_SCOPE = _NoScope()


class _Scope:
    """Brackets one production so a tree sink learns where it began and ended.

    `__enter__` hands back the list that holds the finished node once the block has
    exited, which is how a caller needing the subtree -- an impl wanting its own trait,
    say -- gets hold of it: `with self.node(f) as built: ...` and then `built[0]`.

    `__exit__` closes in every case, exception included, only so the sink's stack stays
    balanced for a caller that catches `UnableTov0Demangle` and carries on with the same
    sink. Nothing in this package does -- an abandoned name discards its sink -- but an
    unbalanced stack would be a silent, later, much stranger failure than the one that
    caused it. Returning false rather than true, so an exception still propagates.
    """

    __slots__ = ("box", "factory", "sink")

    def __init__(self, sink, factory):
        self.sink = sink
        self.factory = factory
        self.box = []

    def __enter__(self):
        self.sink.open()
        return self.box

    def __exit__(self, *exception):
        self.box.append(self.sink.close(self.factory))
        return False


_NO_NODE = (None,)


class TextSink:
    """Collects the printer's fragments as text and nothing else.

    A list joined once at the end rather than repeated `+=`: the printer emits a
    fragment per grammar terminal, and a symbol from a release build can run to a few
    thousand of them.
    """

    #: What each back-referenced subtree spelled, keyed by (offset, bound-lifetime-depth,
    #: in-value), the only printer state that spelling depends on: a `B` is resolved by
    #: printing its target again, and real symbols repeat the same targets many times.
    #: Lives for one symbol.
    __slots__ = ("_parts", "_remaining", "memo")

    def __init__(self, limit):
        self._parts = []
        self._remaining = limit
        self.memo = {}

    def emit(self, text):
        remaining = self._remaining = self._remaining - len(text)
        if remaining < 0:
            raise OutputTooLong
        self._parts.append(text)

    def open(self):
        """Structure boundaries cost nothing here; only the fragments matter."""

    def close(self, factory):
        return None

    def text(self):
        return "".join(self._parts)


class TreeSink:
    """Collects the same fragments, remembering where each production began and ended.

    The printer emits one linear stream either way. This sink records the boundaries as
    it passes them, so the tree it hands back renders to exactly the stream `TextSink`
    would have produced -- not because the two are kept in step, but because there is
    only one stream and this is it with the brackets kept.
    """

    __slots__ = ("_remaining", "_stack")

    def __init__(self, limit):
        self._stack = [[]]
        self._remaining = limit

    def emit(self, text):
        remaining = self._remaining = self._remaining - len(text)
        if remaining < 0:
            raise OutputTooLong
        self._stack[-1].append(text)

    def open(self):
        self._stack.append([])

    def close(self, factory):
        parts = self._stack.pop()
        node = factory(parts)
        self._stack[-1].append(node)
        return node

    def text(self):
        from .nodes import render

        return "".join(render(p) for p in self._stack[0])

    def parts(self):
        return tuple(self._stack[0])


class Printer:
    #: `emit` is the sink's own bound method, stored per instance to avoid a forwarding
    #: frame per emitted fragment.
    __slots__ = ("_plain", "bound_lifetime_depth", "emit", "max_depth", "parser", "recursion", "sink")

    def __init__(self, parser, sink, bound, recursion=0):
        self.parser = parser
        self.max_depth = parser.max_depth
        self.sink = sink
        self.emit = sink.emit
        self._plain = not isinstance(sink, TreeSink)
        self.bound_lifetime_depth = bound
        self.recursion = recursion

    def check_recursion_limit(self):
        """Check and increment recursion counter. Must be paired with decrement."""
        if self.recursion >= self.max_depth:
            raise RecursedTooDeep(self.max_depth)
        self.recursion += 1

    def invalid(self) -> NoReturn:
        """Abandon this name.

        Declared `NoReturn` because it always raises: callers treat it as a terminator
        and read on as though the value they were about to use is well-formed, which is
        only sound if control never comes back.
        """
        self.emit("?")
        raise UnableTov0Demangle("Error")

    def node(self, factory):
        """Bracket a production; see `_Scope` for what it does when structure is wanted."""
        if self._plain:
            return _NO_SCOPE
        return _Scope(self.sink, factory)

    def eat(self, b):
        parser = self.parser
        at = parser.next_val
        if at < parser.end and parser.inn[at] == b:
            parser.next_val = at + 1
            return True
        return False

    def backref_printer(self):
        p = self.parser
        return Printer(p.backref(), self.sink, self.bound_lifetime_depth, self.recursion + 1)

    def backref_remembered(self, printer, kind, in_value):
        """What this back reference spelled last time, or None to print it now.

        See `TextSink.memo` for why. Returns `(key, text)`: `text` is None on a miss,
        and the caller prints the subtree and hands the key back to `backref_record`.
        The key carries everything the spelling depends on -- where the target starts,
        which production is being read there, the bound-lifetime depth the names inside
        it resolve against, and whether a value or a type is being written. Every other
        input the printer reads is fixed for the symbol.

        Only under a sink that keeps no structure. A `TreeSink` has to build the nodes
        again, and text is not what it is collecting.
        """
        key = (kind, printer.parser.next_val, self.bound_lifetime_depth, in_value)
        return key, self.sink.memo.get(key)

    def backref_record(self, key, start):
        """Remember the fragments emitted since `start` as this key's spelling."""
        parts = self.sink._parts
        self.sink.memo[key] = "".join(parts[start:])

    def print_lifetime_from_index(self, lt):
        """`'a` through `'z`, then `'_26` and up. The reference's arithmetic exactly.

        `print_lifetime_from_index` takes `depth = bound_lifetime_depth - lt` -- a
        `checked_sub`, so `lt` past the depth is the invalid name -- and writes
        `'a' + depth` while `depth < 26`. This carried a `depth` one larger and undid it
        at the letter, which is the same for the first twenty-five and not for the rest:
        the twenty-sixth bound lifetime came out `'_26` where the reference writes `'z`,
        and every one after it was numbered one too high. It takes a `for<>` binding
        twenty-six lifetimes to reach, which no compiler writes and a mutated symbol
        does.
        """
        self.emit("'")
        if lt == 0:
            self.emit("_")
            return
        depth = self.bound_lifetime_depth - lt
        if depth < 0:
            self.invalid()

        if depth < 26:
            self.emit(chr(ord("a") + depth))
        else:
            self.emit(f"_{depth}")

    def in_binder(self, val):
        def f1():
            is_unsafe = self.eat("U")
            if self.eat("K"):
                if self.eat("C"):
                    abi = "C"
                else:
                    ab = self.parser.ident()
                    if not ab.ascii or ab.punycode:
                        self.invalid()
                    abi = ab.ascii
            else:
                abi = None

            if is_unsafe:
                self.emit("unsafe ")

            if abi:
                self.emit('extern "')
                self.emit("-".join(abi.split("_")))
                self.emit('" ')

            self.emit("fn(")
            self.print_sep_list("print_type", ", ")
            self.emit(")")

            if self.eat("u"):
                pass
            else:
                self.emit(" -> ")
                self.print_type()

            return ""

        def f2():
            self.print_sep_list("print_dyn_trait", " + ")
            return ""

        bound_lifetimes = self.parser.opt_integer_62("G")

        if bound_lifetimes > 0:
            self.emit("for<")
            for i in range(bound_lifetimes):
                if i > 0:
                    self.emit(", ")
                self.bound_lifetime_depth += 1
                self.print_lifetime_from_index(1)

            self.emit("> ")

        if val == 1:
            r = f1()
        elif val == 2:
            r = f2()
        else:
            r = ""
        self.bound_lifetime_depth -= bound_lifetimes

        return r

    def print_sep_list(self, f, sep, collected=None):
        """Print elements until the closing `E`, returning how many there were.

        `f` is either the name of a method on this printer or any callable. Structural
        consts need the callable form: their elements are printed by `print_const` with
        an argument, and the count decides whether a one-element tuple gets its trailing
        comma.

        `collected`, when given, gathers the subtree each element produced, so a generic
        argument list can name its arguments as well as contain them. Elements that
        produced nothing -- every one of them under `TextSink` -- are not collected.
        """
        element = f if callable(f) else getattr(self, f)
        i = 0
        while not self.eat("E"):
            if i > 0:
                self.emit(str(sep))
            produced = element()
            if collected is not None and produced is not None:
                collected.append(produced)
            i += 1
        return i

    def print_path(self, in_value):
        """Print a `<path>`, returning the subtree a tree sink built for it.

        The return value is None under `TextSink` and nobody asks for it there. Where a
        production needs a piece of itself back -- an impl wanting its own self-type and
        trait -- it takes it from here rather than re-reading the input.
        """
        recursion = self.recursion
        if recursion >= self.max_depth:
            raise RecursedTooDeep(self.max_depth)
        self.recursion = recursion + 1
        try:
            p = self.parser
            at = p.next_val
            if at >= p.end:
                raise UnableTov0Demangle(p.inn)
            tag = p.inn[at]
            p.next_val = at + 1
            if tag == "N":
                at = p.next_val
                if at >= p.end:
                    raise UnableTov0Demangle(p.inn)
                ns = p.inn[at]
                p.next_val = at + 1
                if ns.islower():
                    ns = None
                elif not ns.isupper():
                    raise UnableTov0Demangle(p.inn)
                with self.node(nodes.Path) as built:
                    self.print_path(in_value)
                    at = p.next_val
                    dis = p.opt_integer_62("s") if at < p.end and p.inn[at] == "s" else 0
                    name = p.ident()
                    if ns:
                        with self.node(lambda parts: nodes.Namespace(parts, ns, dis)):
                            self.emit("::{")
                            if ns == "C":
                                self.emit("closure")
                            elif ns == "S":
                                self.emit("shim")
                            else:
                                self.emit(ns)
                            if name.ascii or name.punycode:
                                self.emit(":")
                                name.display()
                                with self.node(nodes.RustName):
                                    self.emit(name.disp)
                            self.emit("#")
                            self.emit(str(dis))
                            self.emit("}")
                    elif name.ascii or name.punycode:
                        self.emit("::")
                        name.display()
                        with self.node(nodes.RustName):
                            self.emit(name.disp)
                return built[0]

            if tag == "C":
                at = p.next_val
                disambiguator = 0
                if at < p.end and p.inn[at] == "s":
                    disambiguator = p.opt_integer_62("s")
                name = p.ident()
                name.display()
                with self.node(nodes.RustName) as built:
                    self.emit(name.disp)
                    if p.keep_hash and disambiguator:
                        # Plain unpadded hex, as rustc-demangle's `{}` (not `{:#}`) prints.
                        self.emit(f"[{disambiguator:x}]")
                return built[0]

            if tag == "B":
                printer = self.backref_printer()
                if not self._plain:
                    return printer.print_path(in_value)
                key, remembered = self.backref_remembered(printer, "path", in_value)
                if remembered is not None:
                    self.emit(remembered)
                    return None
                start = len(self.sink._parts)
                result = printer.print_path(in_value)
                self.backref_record(key, start)
                return result
            if tag == "I":
                collected = []
                with self.node(lambda parts: nodes.Generics(parts, *_generic_fields(collected))) as built:
                    collected.append(self.print_path(in_value))
                    if in_value:
                        self.emit("::")
                    self.emit("<")
                    self.print_sep_list("print_generic_arg", ", ", collected)
                    self.emit(">")
                return built[0]

            if tag in ("M", "X", "Y"):
                if tag != "Y":
                    p.opt_integer_62("s")
                    p.skip_path()

                seen = []
                with self.node(lambda parts: nodes.Impl(parts, *_impl_fields(seen))) as built:
                    self.emit("<")
                    seen.append(self.print_type())
                    if tag != "M":
                        self.emit(" as ")
                        seen.append(self.print_path(False))
                    self.emit(">")
                return built[0]

            self.invalid()
        finally:
            self.recursion -= 1

    def print_generic_arg(self):
        """Print one generic argument, returning the subtree built for it.

        A const is bracketed here rather than inside `print_const`, which threads a
        brace decision through every composite spelling and has no single place that
        means "one whole value".
        """
        if self.eat("L"):
            lt = self.parser.integer_62()
            with self.node(lambda parts: nodes.Value(parts, "lifetime")) as built:
                self.print_lifetime_from_index(lt)
            return built[0]
        if self.eat("K"):
            with self.node(lambda parts: nodes.Value(parts, "const")) as built:
                self.print_const(False)
            return built[0]
        return self.print_type()

    def print_type(self):
        """Print a `<type>`, returning the subtree a tree sink built for it.

        Every shape is bracketed with the `form` that says which it is, so a caller can
        ask whether an argument is a reference without parsing `&mut ` back out of the
        spelling. A `<path>` type returns the path's own node rather than wrapping it:
        the path is the type, and a wrapper carrying nothing would only be in the way.
        """
        recursion = self.recursion
        if recursion >= self.max_depth:
            raise RecursedTooDeep(self.max_depth)
        self.recursion = recursion + 1
        try:
            p = self.parser
            # `w` marks a splat argument: `fn(#[splat] (u8, u32))`.
            if self.eat("w"):
                self.emit("#[splat] ")
            at = p.next_val
            if at >= p.end:
                raise UnableTov0Demangle(p.inn)
            tag = p.inn[at]
            p.next_val = at + 1
            ty = _BASIC_TYPES.get(tag)
            if ty is not None:
                with self.node(lambda parts: nodes.Type(parts, "basic")) as built:
                    self.emit(ty)
                return built[0]

            if tag in _PATH_TAGS:
                p.next_val = at
                return self.print_path(False)

            if tag == "R" or tag == "Q":
                with self.node(lambda parts: nodes.Type(parts, "reference")) as built:
                    self.emit("&")
                    if self.eat("L"):
                        lt = p.integer_62()
                        if lt != 0:
                            self.print_lifetime_from_index(lt)
                            self.emit(" ")

                    if tag != "R":
                        self.emit("mut ")

                    self.print_type()
                return built[0]

            if tag == "P" or tag == "O":
                with self.node(lambda parts: nodes.Type(parts, "pointer")) as built:
                    self.emit("*")
                    if tag != "P":
                        self.emit("mut ")
                    else:
                        self.emit("const ")
                    self.print_type()
                return built[0]

            if tag == "A" or tag == "S":
                form = "array" if tag == "A" else "slice"
                with self.node(lambda parts: nodes.Type(parts, form)) as built:
                    self.emit("[")
                    self.print_type()

                    if tag == "A":
                        self.emit("; ")
                        with self.node(lambda parts: nodes.Value(parts, "length")):
                            self.print_const(True)
                    self.emit("]")
                return built[0]

            if tag == "T":
                with self.node(lambda parts: nodes.Type(parts, "tuple")) as built:
                    self.emit("(")
                    count = self.print_sep_list("print_type", ", ")
                    if count == 1:
                        self.emit(",")
                    self.emit(")")
                return built[0]

            if tag == "F":
                with self.node(lambda parts: nodes.Type(parts, "fn")) as built:
                    self.in_binder(1)
                return built[0]

            if tag == "D":
                with self.node(lambda parts: nodes.Type(parts, "dyn")) as built:
                    self.emit("dyn ")
                    self.in_binder(2)

                    if not self.eat("L"):
                        self.invalid()

                    lt = p.integer_62()
                    if lt != 0:
                        self.emit(" + ")
                        self.print_lifetime_from_index(lt)
                return built[0]

            if tag == "B":
                printer = self.backref_printer()
                if not self._plain:
                    return printer.print_type()
                key, remembered = self.backref_remembered(printer, "type", None)
                if remembered is not None:
                    self.emit(remembered)
                    return None
                start = len(self.sink._parts)
                result = printer.print_type()
                self.backref_record(key, start)
                return result

            if tag == "W":
                with self.node(lambda parts: nodes.Type(parts, "pattern")) as built:
                    self.print_type()
                    self.emit(" is ")
                    self.print_pattern()
                return built[0]

            p = self.parser
            p.next_val -= 1
            return self.print_path(False)
        finally:
            self.recursion -= 1

    def print_pattern(self):
        """The value pattern of a pattern type.

        ```
        <pattern> ::= R <const> <const>   # an inclusive range
                    | O <pattern>+ E      # any of several
                    | N                   # not null
        ```
        """
        p = self.parser
        at = p.next_val
        if at >= p.end:
            raise UnableTov0Demangle(p.inn)
        tag = p.inn[at]
        p.next_val = at + 1
        if tag == "R":
            self.print_const(False)
            self.emit("..=")
            self.print_const(False)
            return
        if tag == "O":
            self.check_recursion_limit()
            try:
                self.print_pattern()
                while not self.eat("E"):
                    self.emit(" | ")
                    self.print_pattern()
            finally:
                self.recursion -= 1
            return
        if tag == "N":
            self.emit("!null")
            return
        raise UnableTov0Demangle(p.inn)

    def print_path_maybe_open_generics(self):
        self.check_recursion_limit()
        try:
            if self.eat("B"):
                prin = self.backref_printer()
                result = prin.print_path_maybe_open_generics()
                return result

            elif self.eat("I"):
                self.print_path(False)
                self.emit("<")
                self.print_sep_list("print_generic_arg", ", ")
                return True
            else:
                self.print_path(False)
                return False
        finally:
            self.recursion -= 1

    def print_dyn_trait(self):
        open = self.print_path_maybe_open_generics()

        while self.eat("p"):
            if not open:
                self.emit("<")
                open = True
            else:
                self.emit(", ")

            name = self.parser.ident()
            name.display()
            self.emit(name.disp)
            self.emit(" = ")
            # Associated consts are bound like associated types, with a `K` in front:
            # `dyn Trait<LEN = 1>`. rustc-demangle 0.1.28 `v0.rs::print_dyn_trait`.
            if self.eat("K"):
                self.print_const(False)
            else:
                self.print_type()

        if open:
            self.emit(">")

    def print_const(self, in_value):
        """Print one `<const>`.

        The grammar, from RFC 2603 and rustc-demangle 0.1.28 `v0.rs::print_const`:

            <const> = <type> <const-data>       // a leaf value, tag names its type
                    | "p"                       // a placeholder, printed as `_`
                    | "R" [<const>]             // a shared reference, or `Re<hex>_` for
                    | "Q" <const>               //   a `&str` literal; `Q` is `&mut`
                    | "A" {<const>} "E"         // an array
                    | "T" {<const>} "E"         // a tuple
                    | "V" <path> <variant-data> // a struct or enum-variant value
                    | <backref>

            <variant-data> = "U"                            // unit
                           | "T" {<const>} "E"              // tuple fields
                           | "S" {<disambiguator> <ident> <const>} "E"   // named fields

        `in_value` says whether this const is already nested inside another expression.
        Only a literal can sit in generic argument position unadorned, so every
        composite spelling braces itself when it is the outermost one -- `::<{[1, 2]}>`,
        not `::<[1, 2]>`. String literals are the exception the reference calls out:
        `Re..._` prints as `"abc"` rather than `&*"abc"`, and needs no braces because a
        quoted literal is unambiguous wherever it appears.
        """
        self.check_recursion_limit()
        try:
            parser = self.parser
            if self.eat("B"):
                printer = self.backref_printer()
                printer.print_const(in_value)
                return

            opened_brace = False

            def open_brace_if_outside_expr():
                nonlocal opened_brace
                if in_value:
                    return
                opened_brace = True
                self.emit("{")

            def nested():
                self.print_const(True)

            ty_tag = parser.next_func()
            if ty_tag == "p":
                self.emit("_")
            elif ty_tag in _CONST_UNSIGNED:
                self.print_const_uint()
                self.print_const_type_suffix(ty_tag)
            elif ty_tag in _CONST_SIGNED:
                self.print_const_int()
                self.print_const_type_suffix(ty_tag)
            elif ty_tag == "b":
                self.print_const_bool()
            elif ty_tag == "c":
                self.print_const_char()
            elif ty_tag == "e":
                # A bare `str` const (not the `&str` of `Re..._`) has no Rust syntax, so
                # the reference writes the deref of a string literal, braced.
                open_brace_if_outside_expr()
                self.emit("*")
                self.print_const_str_literal()
            elif ty_tag in ("R", "Q"):
                if ty_tag == "R" and self.eat("e"):
                    self.print_const_str_literal()
                else:
                    open_brace_if_outside_expr()
                    self.emit("&")
                    if ty_tag != "R":
                        self.emit("mut ")
                    nested()
            elif ty_tag == "A":
                open_brace_if_outside_expr()
                self.emit("[")
                self.print_sep_list(nested, ", ")
                self.emit("]")
            elif ty_tag == "T":
                open_brace_if_outside_expr()
                self.emit("(")
                count = self.print_sep_list(nested, ", ")
                if count == 1:
                    # `(x)` is parenthesised `x`, not a one-tuple; Rust needs `(x,)`.
                    self.emit(",")
                self.emit(")")
            elif ty_tag == "V":
                open_brace_if_outside_expr()
                # `in_value` so a variant of a generic enum reads `Option::<usize>::None`.
                self.print_path(True)
                self.print_const_variant_data()
            else:
                self.invalid()

            if opened_brace:
                self.emit("}")
        finally:
            self.recursion -= 1

    def print_const_variant_data(self):
        """The fields of a `V` const, whose shape follows the ADT it came from."""
        variant = self.parser.next_func()
        if variant == "U":
            return
        if variant == "T":
            self.emit("(")
            self.print_sep_list(lambda: self.print_const(True), ", ")
            self.emit(")")
        elif variant == "S":
            self.emit(" { ")
            self.print_sep_list(self.print_const_field, ", ")
            self.emit(" }")
        else:
            self.invalid()

    def print_const_field(self):
        """One `<disambiguator> <ident> <const>` of a struct-shaped `V` const.

        The disambiguator is parsed and dropped: two fields of one struct never share a
        name, so it carries nothing the reader needs.
        """
        parser = self.parser
        parser.opt_integer_62("s")
        name = parser.ident()
        name.display()
        self.emit(name.disp)
        self.emit(": ")
        self.print_const(True)

    def print_const_str_literal(self):
        """A `<hex-digits>` body as a quoted, escaped string literal."""
        text = parse_hex_str(self.parser.hex_nibbles())
        if text is None:
            self.invalid()
        self.print_quoted_escaped_chars('"', text)

    def print_quoted_escaped_chars(self, quote, characters):
        """Write `characters` escaped and wrapped in `quote`.

        The one departure from `escape_debug` is that the quote character *not* being
        used is left alone, so a `char` const holding a double quote is `'"'` and a
        string containing an apostrophe is `"'"` -- matching rustc-demangle 0.1.28
        `v0.rs::print_quoted_escaped_chars`.
        """
        self.emit(quote)
        for character in characters:
            if (quote == "'" and character == '"') or (quote == '"' and character == "'"):
                self.emit(character)
            else:
                self.emit(escape_debug(character))
        self.emit(quote)

    def print_const_type_suffix(self, tag):
        """`0usize`, `-17i32`: an integer const's own type, spelled after its value.

        The same flag that keeps the crate disambiguator, and not a second option:
        rustc-demangle writes both under `{}` and neither under `{:#}`, so a caller
        asking for one gets the other. Integers only -- a `bool` prints `false` and a
        `char` prints `'x'`, and the reference suffixes neither.
        """
        if self.parser.keep_hash:
            self.emit(_BASIC_TYPES[tag])

    def print_const_uint(self):
        nibbles = self.parser.hex_nibbles()
        value = parse_hex_uint(nibbles)
        if value is None:
            # Wider than `u64` (a legal `u128` const): the reference echoes the nibbles
            # as written, padding included.
            self.emit("0x")
            self.emit(nibbles)
            return
        self.emit(str(value))

    def print_const_int(self):
        if self.eat("n"):
            self.emit("-")
        self.print_const_uint()

    def print_const_bool(self):
        value = parse_hex_uint(self.parser.hex_nibbles())
        if value == 0:
            self.emit("false")
        elif value == 1:
            self.emit("true")
        else:
            self.invalid()

    def print_const_char(self):
        value = parse_hex_uint(self.parser.hex_nibbles())
        if value is None or value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
            self.invalid()
        self.print_quoted_escaped_chars("'", chr(value))
