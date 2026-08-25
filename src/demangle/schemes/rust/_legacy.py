import string

#: Character-class tests written as set membership rather than `in string.digits`, which
#: is a substring search, and rebuilt-per-call concatenations like `string.hexdigits +
#: "@"`. These run once per character of every symbol offered to the scheme.
_DIGITS = frozenset(string.digits)
_HEXDIGITS = frozenset(string.hexdigits)
_HEXDIGITS_OR_AT = frozenset(string.hexdigits + "@")
_PUNCTUATION = frozenset(string.punctuation)


class UnableToLegacyDemangle(Exception):
    def __init__(self, given_str, message="Not able to demangle the given string using LegacyDemangler"):
        self.message = message
        self.given_str = given_str
        super().__init__(self.message)

    def __str__(self):
        return f"[{self.given_str}] {self.message}"


class LegacyDemangler:
    _UNESCAPED = {"SP": "@", "BP": "*", "RF": "&", "LT": "<", "GT": ">", "LP": "(", "RP": ")", "C": ","}

    def demangle(self, inpstr: str) -> str:
        """Demangle to text."""
        return self._run(inpstr)

    def structure(self, inpstr: str):
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
        if "N" not in inpstr:
            raise UnableToLegacyDemangle(original_inpstr)
        inpstr = inpstr[inpstr.index("N") + 1 :]
        self.sanity_check(inpstr)

        if ".llvm." in inpstr:
            length = inpstr.find(".llvm.")
            candidate = inpstr[length + 6 :]
            if not _HEXDIGITS_OR_AT.issuperset(candidate):
                raise UnableToLegacyDemangle(original_inpstr)
            inpstr = inpstr[:length]

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

        self.suffix = inn[1:]
        if self.suffix and self.suffix.startswith(".") and self.is_symbol_like(self.suffix):
            disp += self.suffix

        return disp

    def is_symbol_like(self, suffix):
        for i in suffix:
            if i.isalnum() or i in _PUNCTUATION:
                continue
            else:
                return False

        return True

    def is_rust_hash(self, s):
        # The final path element of a legacy Rust symbol is a 16 hex digit hash written
        # `17h<hash>` -- a `<source-name>` of length 17 whose text begins with `h`.
        if len(s) == 19 and s.startswith("17h"):
            return _HEXDIGITS.issuperset(s[3:])
        # Older rustc wrote the same component without the length, so a bare `h` followed
        # by hex is accepted too. The exact form is preferred, and tried first.
        if s.startswith("h") and len(s) > 1:
            return _HEXDIGITS.issuperset(s[1:])
        return False

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
