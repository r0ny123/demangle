import string
import unicodedata
from functools import lru_cache
from typing import NoReturn, Optional


class UnableTov0Demangle(Exception):
    def __init__(self, given_str, message="Not able to demangle the given string using v0Demangler"):
        self.message = message
        self.given_str = given_str
        super().__init__(self.message)

    def __str__(self):
        return f"[{self.given_str}] {self.message}"


class V0Demangler:
    def __init__(self):
        self.disp = ""
        self.suffix = ""

    def demangle(self, inpstr: str) -> str:
        # Reset state for each call to ensure independent demangling
        self.suffix = ""
        self.disp = ""

        if "R" not in inpstr:
            raise UnableTov0Demangle(inpstr)
        self.inpstr = inpstr[inpstr.index("R") + 1 :]
        self.sanity_check(self.inpstr)

        if ".llvm." in inpstr:
            length = self.inpstr.find(".llvm.")
            candidate = self.inpstr[length + 6 :]
            for i in candidate:
                if i not in string.hexdigits + "@":
                    raise UnableTov0Demangle(inpstr)
            self.inpstr = self.inpstr[:length]

        parser = Parser(self.inpstr, 0)
        # Validate the path structure
        parser.skip_path()
        if (len(parser.inn) > parser.next_val) and parser.inn[parser.next_val].isupper():
            parser.skip_path()

        # Reset parser position for printing
        parser.next_val = 0
        printer = Printer(parser, self.disp, 0)
        printer.print_path(True)

        if "." in self.inpstr:
            self.suffix = self.inpstr[self.inpstr.index(".") : len(self.inpstr)]

        return printer.out + self.suffix

    def sanity_check(self, inpstr: str):
        if not inpstr or not inpstr[0].isupper():
            raise UnableTov0Demangle(inpstr)

        for i in inpstr:
            if ord(i) & 0x80 != 0:
                raise UnableTov0Demangle(inpstr)


class Ident:
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

            if not self.insert(i, c):
                return False
            i += 1

            try:
                punycode_bytes[count]
            except IndexError:
                # Input exhausted with every code point placed: this is the *success*
                # exit. It returned a bare `None` before, which `try_small_punycode_decode`
                # could not tell from the failure exits -- so a correctly decoded
                # identifier was discarded and every non-ASCII name fell back to
                # `punycode{...}`.
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


#: Unicode general categories whose members Rust refuses to print literally. libcore's
#: `printable.rs` table is generated from exactly this set of categories, with the space
#: character carved back out, and `char::escape_debug` renders anything the table rejects
#: as `\u{...}`. Reproducing the categories rather than the table means our answer tracks
#: whichever Unicode version CPython was built against, which can differ from rustc's for
#: codepoints assigned in between; nothing a compiler emits lives in that gap.
_UNPRINTABLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp", "Zs"})

#: Categories standing in for the `Grapheme_Extend` property, which `escape_debug`
#: escapes so that a combining mark cannot silently attach itself to the opening quote.
#: The real property is `Mn | Me | Other_Grapheme_Extend`, and the last is not derivable
#: from anything the standard library exposes -- it is an explicit list in Unicode's
#: `PropList.txt`, most of whose members are category `Mc` (U+09BE BENGALI VOWEL SIGN AA
#: and U+09D7 among them). Those few print literally here where the reference escapes
#: them. Carrying a hand-copied Unicode table to close the gap would cost more than it
#: buys: a `char` or `&str` const holding a bare combining mark is not something a
#: compiler emits, and a stale table would drift in both directions.
_GRAPHEME_EXTEND_CATEGORIES = frozenset({"Mn", "Me"})

#: The characters `char::escape_debug` gives a short escape rather than `\u{...}`.
#: Both quote characters are here because the reference escapes each one inside its own
#: kind of literal; `print_quoted_escaped_chars` undoes that for the opposite quote.
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
    if category in _GRAPHEME_EXTEND_CATEGORIES:
        return f"\\u{{{ord(character):x}}}"
    if character != " " and category in _UNPRINTABLE_CATEGORIES:
        return f"\\u{{{ord(character):x}}}"
    return character


def parse_hex_uint(nibbles: str) -> Optional[int]:
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


def parse_hex_str(nibbles: str) -> Optional[str]:
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


#: `<const>` leaf tags, grouped by how the bytes after the tag are read. The tag is the
#: const's *type*, spelled exactly as in `<basic-type>`, and the grammar in RFC 2603
#: gives the unsigned integers a bare `<hex-digits>` body while the signed ones may carry
#: a leading `n` for the sign. `bool`, `char` and `str` share the unsigned shape but are
#: rendered differently, so they are kept apart.
_CONST_UNSIGNED = ("h", "t", "m", "y", "o", "j")
_CONST_SIGNED = ("a", "s", "l", "x", "n", "i")
_CONST_DATA_ONLY = ("b", "c", "e")


@lru_cache(maxsize=32)
def basic_type(tag: str) -> Optional[str]:
    tagval = {
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
    if tag in tagval:
        return tagval[tag]
    else:
        return


class Parser:
    # Bound the mutually recursive skip_* validation pass; must fire well below
    # CPython's own recursion limit (each level consumes several interpreter frames).
    MAX_RECURSION_COUNT = 256

    def __init__(self, inn: str, next_val: int) -> None:
        self.inn = inn
        self.next_val = next_val
        self.depth = 0

    def check_recursion_limit(self):
        if self.depth >= self.MAX_RECURSION_COUNT:
            raise UnableTov0Demangle(self.inn)
        self.depth += 1

    def peek(self) -> str:
        if self.next_val >= len(self.inn):
            raise UnableTov0Demangle(self.inn)
        return self.inn[self.next_val]

    def eat(self, b: str) -> bool:
        if self.next_val >= len(self.inn):
            return False
        if self.inn[self.next_val] == b:
            self.next_val += 1
            return True
        return False

    def next_func(self) -> str:
        if self.next_val >= len(self.inn):
            raise UnableTov0Demangle(self.inn)
        b = self.inn[self.next_val]
        self.next_val += 1
        return b

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

    def digit_10(self) -> Optional[int]:
        d = self.peek()
        if d in string.digits:
            d = int(d)
        else:
            return None
        self.next_val += 1
        return d

    def digit_62(self) -> int:
        d = self.peek()
        if d in string.digits:
            d = int(d)
        elif d.islower():
            d = 10 + (ord(d) - ord("a"))
        elif d.isupper():
            d = 10 + 26 + (ord(d) - ord("A"))
        else:
            raise UnableTov0Demangle(self.inn)
        self.next_val += 1
        return d

    def integer_62(self) -> int:
        if self.eat("_"):
            return 0
        x = 0
        while not self.eat("_"):
            d = self.digit_62()
            x *= 62
            x += d
        return x + 1

    def opt_integer_62(self, tag: str) -> int:
        if not self.eat(tag):
            return 0
        return self.integer_62() + 1

    def disambiguator(self) -> int:
        return self.opt_integer_62("s")

    def namespace(self) -> Optional[str]:
        n = self.next_func()
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

        return Parser(self.inn, i)

    def ident(self):
        is_punycode = self.eat("u")
        length = self.digit_10()
        if length is not None and length != 0:
            while True:
                d = self.digit_10()
                if d is None:
                    break
                length *= 10
                length += d
        if length is None:
            length = 0

        self.eat("_")

        start = self.next_val
        self.next_val += length
        if self.next_val > len(self.inn):
            raise UnableTov0Demangle(self.inn)

        ident = self.inn[start : self.next_val]
        if is_punycode:
            if "_" in ident:
                i = len(ident) - ident[::-1].index("_") - 1
                idt = Ident(ident[:i], ident[i + 1 :])
            else:
                idt = Ident("", ident)

            if not idt.punycode:
                raise UnableTov0Demangle(self.inn)

            return idt

        else:
            idt = Ident(ident, "")
            return idt

    def skip_path(self):
        self.check_recursion_limit()
        try:
            self._skip_path_inner()
        finally:
            self.depth -= 1

    def _skip_path_inner(self):
        val = self.next_func()
        if val.startswith("C"):
            self.disambiguator()
            self.ident()
        elif val.startswith("N"):
            self.namespace()
            self.skip_path()
            self.disambiguator()
            self.ident()

        elif val.startswith("M"):
            self.disambiguator()
            self.skip_path()
            self.skip_type()

        elif val.startswith("X"):
            self.disambiguator()
            self.skip_path()
            self.skip_type()
            self.skip_path()

        elif val.startswith("Y"):
            self.skip_type()
            self.skip_path()

        elif val.startswith("I"):
            self.skip_path()
            while not self.eat("E"):
                self.skip_generic_arg()

        elif val.startswith("B"):
            self.backref()

        else:
            raise UnableTov0Demangle(self.inn)

    def skip_generic_arg(self):
        if self.eat("L"):
            self.integer_62()
        elif self.eat("K"):
            self.skip_const()
        else:
            self.skip_type()

    def skip_type(self):
        self.check_recursion_limit()
        try:
            self._skip_type_inner()
        finally:
            self.depth -= 1

    def _skip_type_inner(self):
        n = self.next_func()
        tag = n
        if basic_type(tag):
            pass
        elif n == "R" or n == "Q":
            if self.eat("L"):
                self.integer_62()
            else:
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
                    self.ident()
                    self.skip_type()
            if not self.eat("L"):
                raise UnableTov0Demangle(self.inn)
            self.integer_62()
        elif n == "B":
            self.backref()
        else:
            self.next_val -= 1
            self.skip_path()

    def skip_const(self):
        """Advance past one `<const>` without rendering it.

        Structural consts nest, so this needs the same depth guard `skip_type` has: the
        skip pass runs before anything is printed, on input nobody has validated yet.
        """
        self.check_recursion_limit()
        try:
            self._skip_const_inner()
        finally:
            self.depth -= 1

    def _skip_const_inner(self):
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
            # `Re<hex>_` is a string literal rather than a reference to a nested const,
            # so only the non-`e` spelling continues into another `<const>`.
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
                    self.disambiguator()
                    self.ident()
                    self.skip_const()
            elif variant != "U":
                raise UnableTov0Demangle(self.inn)
        else:
            raise UnableTov0Demangle(self.inn)


class Printer:
    # Following Ghidra's RustDemanglerV0, we limit recursion to prevent stack overflows
    # or excessive resource usage on malformed inputs.
    # Must fire well below CPython's own recursion limit (default 1000), or a
    # self-referential backref chain raises RecursionError before this guard.
    RUST_MAX_RECURSION_COUNT = 256

    def __init__(self, parser, out, bound, recursion=0):
        self.parser = parser
        self.out = out
        self.bound_lifetime_depth = bound
        self.recursion = recursion

    def check_recursion_limit(self):
        """Check and increment recursion counter. Must be paired with decrement."""
        if self.recursion >= self.RUST_MAX_RECURSION_COUNT:
            raise UnableTov0Demangle("Recursion limit exceeded")
        self.recursion += 1

    def invalid(self) -> NoReturn:
        """Abandon this name.

        Declared `NoReturn` because it always raises: callers treat it as a terminator
        and read on as though the value they were about to use is well-formed, which is
        only sound if control never comes back.
        """
        self.out += "?"
        raise UnableTov0Demangle("Error")

    def parser_mut(self):
        return self.parser

    def eat(self, b):
        par = self.parser_mut()
        return bool(par.eat(b))

    def backref_printer(self):
        p = self.parser_mut()
        # Increment recursion count for backrefs as they involve recursive printing
        return Printer(p.backref(), self.out, self.bound_lifetime_depth, self.recursion + 1)

    def print_lifetime_from_index(self, lt):
        self.out += "'"
        if lt == 0:
            self.out += "_"
            return
        depth = self.bound_lifetime_depth - lt + 1
        if depth <= 0:
            self.invalid()

        if depth < 26:
            c = ord("a") + depth - 1
            self.out += chr(c)
        else:
            self.out += f"_{depth}"

    def in_binder(self, val):
        def f1():
            is_unsafe = self.eat("U")
            if self.eat("K"):
                if self.eat("C"):
                    abi = "C"
                else:
                    ab = self.parser_mut().ident()
                    if not ab.ascii or ab.punycode:
                        self.invalid()
                    abi = ab.ascii
            else:
                abi = None

            if is_unsafe:
                self.out += "unsafe "

            if abi:
                self.out += 'extern "'
                self.out += "-".join(abi.split("_"))
                self.out += '" '

            self.out += "fn("
            self.print_sep_list("print_type", ", ")
            self.out += ")"

            if self.eat("u"):
                pass
            else:
                self.out += " -> "
                self.print_type()

            return ""

        def f2():
            self.print_sep_list("print_dyn_trait", " + ")
            return ""

        bound_lifetimes = self.parser_mut().opt_integer_62("G")

        if bound_lifetimes > 0:
            self.out += "for<"
            for i in range(bound_lifetimes):
                if i > 0:
                    self.out += ", "
                self.bound_lifetime_depth += 1
                self.print_lifetime_from_index(1)

            self.out += "> "

        if val == 1:
            r = f1()
        elif val == 2:
            r = f2()
        else:
            r = ""
        self.bound_lifetime_depth -= bound_lifetimes

        return r

    def print_sep_list(self, f, sep):
        """Print elements until the closing `E`, returning how many there were.

        `f` is either the name of a method on this printer or any callable. Structural
        consts need the callable form: their elements are printed by `print_const` with
        an argument, and the count decides whether a one-element tuple gets its trailing
        comma.
        """
        element = f if callable(f) else getattr(self, f)
        i = 0
        while not self.eat("E"):
            if i > 0:
                self.out += str(sep)
            element()
            i += 1
        return i

    def print_path(self, in_value):
        self.check_recursion_limit()
        try:
            p = self.parser_mut()
            tag = p.next_func()
            if tag == "C":
                p.disambiguator()
                name = p.ident()
                name.display()
                self.out += name.disp

            elif tag == "N":
                ns = p.namespace()
                self.print_path(in_value)
                dis = p.disambiguator()
                name = p.ident()
                if ns:
                    self.out += "::{"
                    if ns == "C":
                        self.out += "closure"
                    elif ns == "S":
                        self.out += "shim"
                    else:
                        self.out += ns
                    if name.ascii or name.punycode:
                        self.out += ":"
                        name.display()
                        self.out += name.disp

                    self.out += "#"
                    self.out += str(dis)
                    self.out += "}"
                else:
                    if name.ascii or name.punycode:
                        self.out += "::"
                        name.display()
                        self.out += name.disp

            elif tag == "M" or tag == "X" or tag == "Y":
                if tag != "Y":
                    p.disambiguator()
                    p.skip_path()

                self.out += "<"
                self.print_type()

                if tag != "M":
                    self.out += " as "
                    self.print_path(False)

                self.out += ">"

            elif tag == "I":
                self.print_path(in_value)
                if in_value:
                    self.out += "::"

                self.out += "<"
                self.print_sep_list("print_generic_arg", ", ")
                self.out += ">"

            elif tag == "B":
                prin = self.backref_printer()
                prin.print_path(in_value)
                self.out = prin.out

            else:
                self.invalid()
        finally:
            self.recursion -= 1

    def print_generic_arg(self):
        if self.eat("L"):
            lt = self.parser_mut().integer_62()
            self.print_lifetime_from_index(lt)
        elif self.eat("K"):
            # Generic argument position: an expression here is not already inside another
            # one, so a structural const has to brace itself to stay unambiguous.
            self.print_const(False)
        else:
            self.print_type()

    def print_type(self):
        self.check_recursion_limit()
        try:
            p = self.parser_mut()
            tag = p.next_func()
            if basic_type(tag):
                ty = basic_type(tag)
                self.out += ty
                return

            if tag == "R" or tag == "Q":
                self.out += "&"
                if self.eat("L"):
                    lt = p.integer_62()
                    if lt != 0:
                        self.print_lifetime_from_index(lt)
                        self.out += " "

                if tag != "R":
                    self.out += "mut "

                self.print_type()

            elif tag == "P" or tag == "O":
                self.out += "*"
                if tag != "P":
                    self.out += "mut "
                else:
                    self.out += "const "
                self.print_type()

            elif tag == "A" or tag == "S":
                self.out += "["
                self.print_type()

                if tag == "A":
                    self.out += "; "
                    # `[T; N]` already reads as an expression context, so the length
                    # never needs braces however structural it is.
                    self.print_const(True)
                self.out += "]"

            elif tag == "T":
                self.out += "("
                count = self.print_sep_list("print_type", ", ")
                if count == 1:
                    self.out += ","
                self.out += ")"

            elif tag == "F":
                self.in_binder(1)

            elif tag == "D":
                self.out += "dyn "
                self.in_binder(2)

                if not self.eat("L"):
                    self.invalid()

                lt = p.integer_62()
                if lt != 0:
                    self.out += " + "
                    self.print_lifetime_from_index(lt)

            elif tag == "B":
                prin = self.backref_printer()
                prin.print_type()
                self.out = prin.out

            else:
                p = self.parser_mut()
                p.next_val -= 1
                self.print_path(False)
        finally:
            self.recursion -= 1

    def print_path_maybe_open_generics(self):
        self.check_recursion_limit()
        try:
            if self.eat("B"):
                prin = self.backref_printer()
                result = prin.print_path_maybe_open_generics()
                self.out = prin.out
                return result

            elif self.eat("I"):
                self.print_path(False)
                self.out += "<"
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
                self.out += "<"
                open = True
            else:
                self.out += ", "

            name = self.parser_mut().ident()
            name.display()
            self.out += name.disp
            self.out += " = "
            self.print_type()

        if open:
            self.out += ">"

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
            parser = self.parser_mut()
            if self.eat("B"):
                # The brace decision belongs to whatever the backref resolves to, so
                # `in_value` is passed through untouched.
                printer = self.backref_printer()
                printer.print_const(in_value)
                self.out = printer.out
                return

            opened_brace = False

            def open_brace_if_outside_expr():
                nonlocal opened_brace
                if in_value:
                    return
                opened_brace = True
                self.out += "{"

            def nested():
                self.print_const(True)

            ty_tag = parser.next_func()
            if ty_tag == "p":
                self.out += "_"
            elif ty_tag in _CONST_UNSIGNED:
                self.print_const_uint()
            elif ty_tag in _CONST_SIGNED:
                self.print_const_int()
            elif ty_tag == "b":
                self.print_const_bool()
            elif ty_tag == "c":
                self.print_const_char()
            elif ty_tag == "e":
                # A bare `str` const, as opposed to the `&str` that `Re..._` encodes.
                # There is no Rust syntax for it, so the reference writes the deref of a
                # string literal and braces the result.
                open_brace_if_outside_expr()
                self.out += "*"
                self.print_const_str_literal()
            elif ty_tag in ("R", "Q"):
                if ty_tag == "R" and self.eat("e"):
                    self.print_const_str_literal()
                else:
                    open_brace_if_outside_expr()
                    self.out += "&"
                    if ty_tag != "R":
                        self.out += "mut "
                    nested()
            elif ty_tag == "A":
                open_brace_if_outside_expr()
                self.out += "["
                self.print_sep_list(nested, ", ")
                self.out += "]"
            elif ty_tag == "T":
                open_brace_if_outside_expr()
                self.out += "("
                count = self.print_sep_list(nested, ", ")
                if count == 1:
                    # `(x)` is parenthesised `x`, not a one-tuple; Rust needs `(x,)`.
                    self.out += ","
                self.out += ")"
            elif ty_tag == "V":
                open_brace_if_outside_expr()
                # `in_value` is True for the path so an enum variant of a generic type
                # comes out as `Option::<usize>::None` rather than `Option<usize>::None`.
                self.print_path(True)
                self.print_const_variant_data()
            else:
                self.invalid()

            if opened_brace:
                self.out += "}"
        finally:
            self.recursion -= 1

    def print_const_variant_data(self):
        """The fields of a `V` const, whose shape follows the ADT it came from."""
        variant = self.parser_mut().next_func()
        if variant == "U":
            return
        if variant == "T":
            self.out += "("
            self.print_sep_list(lambda: self.print_const(True), ", ")
            self.out += ")"
        elif variant == "S":
            self.out += " { "
            self.print_sep_list(self.print_const_field, ", ")
            self.out += " }"
        else:
            self.invalid()

    def print_const_field(self):
        """One `<disambiguator> <ident> <const>` of a struct-shaped `V` const.

        The disambiguator is parsed and dropped: two fields of one struct never share a
        name, so it carries nothing the reader needs.
        """
        parser = self.parser_mut()
        parser.disambiguator()
        name = parser.ident()
        name.display()
        self.out += name.disp
        self.out += ": "
        self.print_const(True)

    def print_const_str_literal(self):
        """A `<hex-digits>` body as a quoted, escaped string literal."""
        text = parse_hex_str(self.parser_mut().hex_nibbles())
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
        self.out += quote
        for character in characters:
            if (quote == "'" and character == '"') or (quote == '"' and character == "'"):
                self.out += character
            else:
                self.out += escape_debug(character)
        self.out += quote

    def print_const_uint(self):
        nibbles = self.parser_mut().hex_nibbles()
        value = parse_hex_uint(nibbles)
        if value is None:
            # Wider than `u64`: the reference gives up on decimal rather than failing,
            # because a `u128` const is perfectly legal, and echoes the nibbles as
            # written -- padding included, since it no longer knows what was padding.
            self.out += "0x"
            self.out += nibbles
            return
        self.out += str(value)

    def print_const_int(self):
        if self.eat("n"):
            self.out += "-"
        self.print_const_uint()

    def print_const_bool(self):
        value = parse_hex_uint(self.parser_mut().hex_nibbles())
        if value == 0:
            self.out += "false"
        elif value == 1:
            self.out += "true"
        else:
            self.invalid()

    def print_const_char(self):
        value = parse_hex_uint(self.parser_mut().hex_nibbles())
        # `char::from_u32` rejects both out-of-range scalars and the surrogate range;
        # Python's `chr` accepts surrogates, so that half has to be checked by hand.
        if value is None or value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
            self.invalid()
        self.print_quoted_escaped_chars("'", chr(value))
