import string

from ._v0 import _is_symbol_like, _strip_llvm_suffix

#: Character-class tests written as set membership rather than `in string.digits`, which
#: is a substring search, and rebuilt-per-call concatenations like `string.hexdigits +
#: "@"`. These run once per character of every symbol offered to the scheme.
_DIGITS = frozenset(string.digits)
_HEXDIGITS = frozenset(string.hexdigits)


class UnableToLegacyDemangle(Exception):
    def __init__(self, given_str, message="Not able to demangle the given string using LegacyDemangler"):
        self.message = message
        self.given_str = given_str
        super().__init__(self.message)

    def __str__(self):
        return f"[{self.given_str}] {self.message}"


class LegacyDemangler:
    _UNESCAPED = {"SP": "@", "BP": "*", "RF": "&", "LT": "<", "GT": ">", "LP": "(", "RP": ")", "C": ","}

    def demangle(self, inpstr: str, limit: int) -> str:
        """Demangle to text.

        `limit` is accepted for one signature across both grammars; this one builds its
        output linearly in the length of the input, so there is nothing here that can
        outrun the bound the caller checks afterwards.
        """
        return self._run(inpstr)

    def structure(self, inpstr: str, limit: int):
        """Demangle to a tree, which renders to exactly what `demangle` returns.

        The same pass builds both. `_run` records where each path component begins and
        ends in the string it is assembling, and the tree is those spans with the text
        between them kept as it stands -- so concatenating the tree reproduces the
        string character for character rather than approximating it.
        """
        from . import nodes

        text = self._run(inpstr)
        parts = []
        at = 0
        for start, end in self.spans:
            if start > at:
                parts.append(text[at:start])
            parts.append(nodes.RustName((text[start:end],)))
            at = end
        if at < len(text):
            parts.append(text[at:])
        path = nodes.Path(parts)
        return nodes.Symbol((path,), hash=self.hash, suffix=self.suffix)

    def _run(self, inpstr: str) -> str:
        self.elements = 0
        self.spans = []
        self.hash = None

        original_inpstr = inpstr
        disp = ""
        # By prefix, not by the first `N` anywhere in the name -- see the note on the
        # same change in `_v0`.
        if inpstr.startswith("__ZN"):
            inpstr = inpstr[4:]
        elif inpstr.startswith("_ZN"):
            inpstr = inpstr[3:]
        else:
            raise UnableToLegacyDemangle(original_inpstr)
        self.sanity_check(inpstr)

        inpstr = _strip_llvm_suffix(inpstr)

        inn = inpstr
        for ele in range(self.elements):
            # Scan the length prefix by index. Stripping it a character at a time with
            # `rest = rest[1:]` copies the whole remainder per digit, which is quadratic
            # in the length of the symbol -- and a release binary's symbols are long.
            prefix = 0
            limit = len(inn)
            while prefix < limit and inn[prefix] in _DIGITS:
                prefix += 1

            if not prefix:
                # no length prefix remains: the element count came from the pre-strip string
                raise UnableToLegacyDemangle(original_inpstr)

            num = int(inn[:prefix])

            rest = inn[prefix:]
            inn = rest[num:]
            rest = rest[:num]

            # The trailing hash disambiguates monomorphisations; it is not part of the
            # path a reader wants, and neither `rustfilt` nor Ghidra prints it.
            is_hash = ele + 1 == self.elements and self.is_rust_hash(rest)

            if ele != 0 and not is_hash:
                disp += "::"

            if is_hash:
                # The component reads `h<16 hex digits>` once its length prefix has been
                # consumed. What a caller wants is the value, not the marker.
                self.hash = rest[1:]
                break

            component = len(disp)

            if rest.startswith("_$"):
                rest = rest[1:]

            while True:
                if rest.startswith("."):
                    if rest[1:].startswith("."):
                        disp += "::"
                        rest = rest[2:]
                    else:
                        disp += "."
                        rest = rest[1:]

                elif rest.startswith("$"):
                    end = rest[1:].find("$")
                    if end == -1:
                        raise UnableToLegacyDemangle(original_inpstr)
                    escape = rest[1 : end + 1]
                    after_escape = rest[end + 2 :]
                    if not escape:
                        raise UnableToLegacyDemangle(original_inpstr)

                    if escape.startswith("u"):
                        digits = escape[1:]
                        if not digits:
                            raise UnableToLegacyDemangle(original_inpstr)

                        if not _HEXDIGITS.issuperset(digits):
                            raise UnableToLegacyDemangle(original_inpstr)

                        try:
                            c = int(digits, 16)
                            disp += chr(c)
                        except (OverflowError, ValueError):
                            raise UnableToLegacyDemangle(original_inpstr) from None

                        rest = after_escape
                        continue

                    else:
                        if escape not in self._UNESCAPED:
                            raise UnableToLegacyDemangle(original_inpstr)
                        disp += self._UNESCAPED[escape]
                        rest = after_escape
                        continue

                elif ("$") in rest:
                    dollar = rest.find("$")
                    dot = rest.find(".")

                    if dot == -1:
                        disp += rest[:dollar]
                        rest = rest[dollar:]
                        continue

                    if dollar < dot:
                        disp += rest[:dollar]
                        rest = rest[dollar:]
                    else:
                        disp += rest[:dot]
                        rest = rest[dot:]
                else:
                    break
            disp += rest
            self.spans.append((component, len(disp)))

        # `inn` is positioned on the `E` that closes the path, so what follows it is
        # whatever the grammar did not account for. The reference carries it only when
        # it is a vendor suffix -- introduced by `.`, and spelled in characters a symbol
        # can hold -- and refuses the name otherwise. Dropping it instead, which is what
        # this did, meant `_ZN3fooE.llvm moocow` read as plain `foo`: a name that is not
        # the symbol and not the truth.
        self.suffix = inn[1:]
        if self.suffix:
            if not (self.suffix.startswith(".") and _is_symbol_like(self.suffix)):
                raise UnableToLegacyDemangle(original_inpstr)
            disp += self.suffix

        return disp

    def is_rust_hash(self, s):
        """`h` followed by hex digits, which is the reference's whole test.

        `s` has already had its length prefix consumed, so the hash component of
        `_ZN3foo17h0123456789abcdefE` arrives here as `h0123456789abcdef`. There used to
        be an arm here for the length-prefixed spelling as well, testing `s` for `17h`;
        it could never fire. The prefix scan takes the *maximal* run of digits, so `s`
        never begins with one -- `19` followed by `17h...` reads as the length 1917, and
        the name is refused before this is reached at all.
        """
        return s.startswith("h") and len(s) > 1 and _HEXDIGITS.issuperset(s[1:])

    def sanity_check(self, inpstr: str):
        # The reference reads the symbol as *bytes* and rejects it outright if any of
        # them has bit 7 set, so a non-ASCII name is refused whatever it contains. This
        # was written as a per-character `ord(i) & 0x80` loop, which is not the same
        # test: U+0100 is one character whose value has bit 7 clear, so it passed here
        # and its identifier was printed -- where `rustfilt` echoes the symbol back
        # unread. `str.isascii` is the reference's test, and runs in C.
        if not inpstr.isascii():
            raise UnableToLegacyDemangle(inpstr)

        self.elements = 0
        c = 0
        limit = len(inpstr)
        while c < limit and inpstr[c] != "E":
            length = 0
            if inpstr[c] not in _DIGITS:
                raise UnableToLegacyDemangle(inpstr)

            while c < limit and inpstr[c] in _DIGITS:
                length = length * 10 + (ord(inpstr[c]) - 48)
                c += 1

            if c + length > limit:
                raise UnableToLegacyDemangle(inpstr)

            c += length
            self.elements += 1
