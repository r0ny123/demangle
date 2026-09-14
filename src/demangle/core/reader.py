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
    """A cursor over `text`, with the lookahead and consumption parsers need.

    `length` is the end of input and is not always `len(text)`. A parser may shorten it
    to bound a nested encoding it has found the end of by other means -- the Itanium
    scheme does this for `___Z<encoding>_block_invoke`, where a regex says where the
    encoding stops and the parser must not read the literal that follows. *Every* method
    here answers against `length` rather than against the string, because a bound only
    half the class honours is worse than none: `peek` reported the end of input while
    `expect` stepped past it, so a truncated `S` borrowed the `_` of `_block_invoke` for
    its terminator and 4,931 truncated encodings read as though they were whole.
    """

    __slots__ = ("length", "padded_length", "pos", "text")

    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.length = len(text)
        #: Whether a `length_prefixed()` read a length written with a leading zero, which
        #: the grammars spell without one. Recorded rather than refused: `c++filt` 2.42
        #: reads `_Z1f01A` as `f(A)` and `llvm-cxxfilt` 18 refuses it, so a name carrying
        #: one is a place the two references split and no compiler settles. Read by
        #: tools/enumerate.py.
        self.padded_length = False

    # -- inspection ------------------------------------------------------------

    @property
    def eof(self):
        return self.pos >= self.length

    @property
    def remaining(self):
        return self.text[self.pos : self.length]

    def peek(self):
        """The character at the cursor, or `""` past the end.

        Returning a string rather than raising is what lets a parser write
        `if reader.peek() == "N"` without first checking for the end of input.

        A parser that has just had a *non-empty* character out of `peek`, with nothing
        between the two, may consume it by advancing `pos` itself rather than calling
        `take`: the bounds test `take` would repeat has already happened. Nothing weaker
        licenses it. An intervening call may have moved the cursor, and an empty result
        means there was no character to consume -- either way `pos += 1` would step past
        the end, which is the one thing this class exists to prevent. The productions
        that do it say so at the site.

        Takes no argument, where it used to take an offset defaulting to zero. It is
        called around fifty times per name demangled -- more than any other method in
        the package -- and CPython charges for a default it then has to bind: dropping
        the parameter is a fifth off the cost of the call. `ahead` is the offset form.
        """
        pos = self.pos
        return self.text[pos] if pos < self.length else ""

    def ahead(self, offset):
        """The character `offset` past the cursor, or `""` past the end."""
        index = self.pos + offset
        return self.text[index] if index < self.length else ""

    def peek2(self):
        """The next two characters, for the many two-letter codes in these grammars."""
        pos = self.pos
        end = pos + 2
        length = self.length
        return self.text[pos : end if end < length else length]

    def ahead2(self, offset):
        """The two characters `offset` past the cursor, bounded like `peek2`.

        For the lookaheads that decide a branch without consuming anything -- whether an
        abbreviation is followed by a constructor, whether `gs` introduces an allocation.
        They reached into `text` directly before, which is the end of input's one blind
        spot: a bound only half the class honours is worse than none.
        """
        start = self.pos + offset
        end = start + 2
        length = self.length
        return self.text[start : end if end < length else length]

    def startswith(self, literal):
        return self.pos + len(literal) <= self.length and self.text.startswith(literal, self.pos)

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
        pos = self.pos
        end = pos + len(literal)
        if end <= self.length and self.text.startswith(literal, pos):
            self.pos = end
            return True
        return False

    def expect(self, literal):
        """Consume `literal`, or raise."""
        # The test is written out rather than delegated to `eat`: this is on the path of
        # every production that has a fixed opening character, and reaching a three-line
        # method through another one costs a whole interpreter frame to save three lines.
        pos = self.pos
        end = pos + len(literal)
        if end <= self.length and self.text.startswith(literal, pos):
            self.pos = end
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

    def length_prefixed(self):
        """A decimal length and the characters it counts, as one production.

        `<source-name>` is the commonest production in these grammars -- every component
        of every qualified name is one -- and reading it as `int(digits())` followed by
        `take_exactly()` costs three frames for what is one scan and one slice. Raises
        the same way each of those does: no digits, a run past the bound, or a length
        the name is too short for.
        """
        text, length = self.text, self.length
        start = pos = self.pos
        while pos < length and text[pos] in DIGITS:
            pos += 1
            if pos - start > MAX_NUMBER_DIGITS:
                raise ParseError(text, start, "number too long")
        if pos == start:
            raise ParseError(text, start, "expected a number")
        if text[start] == "0":
            # One comparison on the commonest production in the package, and a store
            # only on input no compiler writes. See `padded_length`.
            self.padded_length = True
        count = int(text[start:pos])
        end = pos + count
        if end > length:
            raise TruncatedError(text, pos)
        self.pos = end
        return count, text[pos:end]

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
