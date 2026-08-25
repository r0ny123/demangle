"""A bounds-checked cursor over a mangled name.

Every parser in the package reads through this and never indexes the string directly.
That is not ceremony: the schemes are full of places where the next character decides
the production, and a bare `text[pos]` at the end of a truncated name raises
`IndexError` -- which then has to be caught somewhere far away and translated into
something meaningful. Reading past the end here is simply "no character", and the
parser decides whether that is fatal.

Kept deliberately small and `__slots__`-ed: an instance exists per name demangled, and
its methods are the innermost loop in the package.
"""

import string

from .errors import ParseError, TruncatedError

DIGITS = frozenset(string.digits)
#: <seq-id> is base 36 with the digits ordered before the capitals.
SEQ_ID_ALPHABET = string.digits + string.ascii_uppercase

#: Longest <seq-id> worth reading. Base 36 in 12 digits already exceeds 4 * 10**18, far
#: past any real substitution table, and the accumulation is quadratic in the digit
#: count -- so an unbounded run lets a single symbol burn arbitrary time building an
#: integer whose only use is to fail a bounds check.
MAX_SEQ_ID_DIGITS = 12

#: Likewise for decimal runs. CPython refuses `int()` on more than 4300 digits anyway,
#: with a ValueError that is not this package's error type.
MAX_NUMBER_DIGITS = 20
_SEQ_ID_VALUES = {char: index for index, char in enumerate(SEQ_ID_ALPHABET)}


class Reader:
    """A cursor over `text`, with the lookahead and consumption parsers need."""

    __slots__ = ("length", "pos", "text")

    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.length = len(text)

    # -- inspection ------------------------------------------------------------

    @property
    def eof(self):
        return self.pos >= self.length

    @property
    def remaining(self):
        return self.text[self.pos :]

    def peek(self, offset=0):
        """The character `offset` ahead, or `""` past the end.

        Returning a string rather than raising is what lets a parser write
        `if reader.peek() == "N"` without first checking for the end of input.

        A parser that has already had a character out of `peek` may consume it by
        advancing `pos` itself rather than calling `take` -- the character is known to be
        there, so the bounds test `take` would repeat has already happened. That is the
        one place the rule against touching the cursor directly does not apply, and the
        productions that do it say so.
        """
        index = self.pos + offset
        return self.text[index] if index < self.length else ""

    def peek2(self):
        """The next two characters, for the many two-letter codes in these grammars."""
        return self.text[self.pos : self.pos + 2]

    def startswith(self, literal):
        return self.text.startswith(literal, self.pos)

    # -- consumption -----------------------------------------------------------

    def take(self):
        """Consume and return one character. Raises at the end of input."""
        if self.pos >= self.length:
            raise TruncatedError(self.text, self.pos)
        char = self.text[self.pos]
        self.pos += 1
        return char

    def take_exactly(self, count):
        """Consume exactly `count` characters, or raise if they are not all there."""
        end = self.pos + count
        if count < 0 or end > self.length:
            raise TruncatedError(self.text, self.pos)
        chunk = self.text[self.pos : end]
        self.pos = end
        return chunk

    def eat(self, literal):
        """Consume `literal` if it is next, reporting whether it was."""
        if self.text.startswith(literal, self.pos):
            self.pos += len(literal)
            return True
        return False

    def expect(self, literal):
        """Consume `literal`, or raise."""
        # The test is written out rather than delegated to `eat`: this is on the path of
        # every production that has a fixed opening character, and reaching a three-line
        # method through another one costs a whole interpreter frame to save three lines.
        pos = self.pos
        if self.text.startswith(literal, pos):
            self.pos = pos + len(literal)
            return
        raise ParseError(self.text, pos, f"expected {literal!r}")

    # -- numbers ---------------------------------------------------------------

    def digits(self):
        """Consume a run of decimal digits and return it, or raise if there are none.

        Bounded: an unbounded run would reach `int()`, which CPython refuses above 4300
        digits with a `ValueError` -- not this package's error type, and so not something
        a caller of `parse()` can be expected to catch.
        """
        start = self.pos
        text, length = self.text, self.length
        pos = start
        while pos < length and text[pos] in DIGITS:
            pos += 1
            if pos - start > MAX_NUMBER_DIGITS:
                raise ParseError(text, start, "number too long")
        if pos == start:
            raise ParseError(text, start, "expected a number")
        self.pos = pos
        return text[start:pos]

    def number(self, allow_negative=True):
        """A <number>: an optional leading `n` meaning negative, then digits."""
        negative = allow_negative and self.eat("n")
        digits = self.digits()
        return "-" + digits if negative else digits

    def integer(self, allow_negative=True):
        return int(self.number(allow_negative))

    def seq_id(self):
        """A <seq-id> followed by `_`, returned as a zero-based index.

        The encoding is offset by one so that the empty sequence can mean something:
        `S_` is entry 0 and `S0_` is entry 1.
        """
        start = self.pos
        text, length = self.text, self.length
        pos = start
        while pos < length and text[pos] in _SEQ_ID_VALUES:
            pos += 1
            if pos - start > MAX_SEQ_ID_DIGITS:
                raise ParseError(text, start, "substitution index too long")
        raw = text[start:pos]
        self.pos = pos
        self.expect("_")
        if not raw:
            return 0
        value = 0
        for char in raw:
            value = value * 36 + _SEQ_ID_VALUES[char]
        return value + 1

    # -- diagnostics -----------------------------------------------------------

    def fail(self, message):
        raise ParseError(self.text, self.pos, message)

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Reader({self.text[: self.pos]!r} | {self.remaining!r})"
