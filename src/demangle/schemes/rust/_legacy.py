import string

from ._v0 import _is_symbol_like, _strip_llvm_suffix

#: Sets rather than `in string.digits` (a substring search); tested per character.
_DIGITS = frozenset(string.digits)
_HEXDIGITS = frozenset(string.hexdigits)

#: Lowercase only, as the reference's `'0'..='9' | 'a'..='f'`: it leaves `$u00AB$` literal.
_LOWER_HEXDIGITS = frozenset(string.digits + "abcdef")

_UNESCAPED = {"SP": "@", "BP": "*", "RF": "&", "LT": "<", "GT": ">", "LP": "(", "RP": ")", "C": ","}


class UnableToLegacyDemangle(Exception):
    def __init__(self, given_str, message="Not able to demangle the given string using LegacyDemangler"):
        self.message = message
        self.given_str = given_str
        super().__init__(self.message)

    def __str__(self):
        return f"[{self.given_str}] {self.message}"


class LegacyDemangler:
    def __init__(self, keep_hash: bool = False):
        self.keep_hash = keep_hash

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
        if inpstr.startswith("__ZN"):
            inpstr = inpstr[4:]
        elif inpstr.startswith("_ZN"):
            inpstr = inpstr[3:]
        elif inpstr.startswith("ZN"):
            # Some symbol tables have already had the leading underscore stripped.
            inpstr = inpstr[2:]
        else:
            raise UnableToLegacyDemangle(original_inpstr)
        self.sanity_check(inpstr)

        inpstr = _strip_llvm_suffix(inpstr)

        inn = inpstr
        for ele in range(self.elements):
            # By index: `rest = rest[1:]` per digit is quadratic in the symbol's length.
            prefix = 0
            limit = len(inn)
            while prefix < limit and inn[prefix] in _DIGITS:
                prefix += 1

            if not prefix:
                # No length prefix remains: the element count came from the pre-strip string.
                raise UnableToLegacyDemangle(original_inpstr)

            num = int(inn[:prefix])

            rest = inn[prefix:]
            inn = rest[num:]
            rest = rest[:num]

            # The trailing hash disambiguates monomorphisations; `rustfilt` and Ghidra
            # omit it.
            is_hash = ele + 1 == self.elements and self.is_rust_hash(rest)

            if ele != 0 and not is_hash:
                disp += "::"

            if is_hash:
                self.hash = rest[1:]
                break

            component = len(disp)

            if rest[:2] == "_$":
                rest = rest[1:]

            # Dispatched on the first character: the hot loop over every component.
            while rest:
                head = rest[0]
                if head == ".":
                    if rest[1:2] == ".":
                        disp += "::"
                        rest = rest[2:]
                    else:
                        disp += "."
                        rest = rest[1:]

                elif head == "$":
                    # Below 2 is no closing `$` or an empty escape; both stop the loop
                    # where the reference does.
                    closing = rest.find("$", 1)
                    if closing < 2:
                        break
                    escape = rest[1:closing]
                    after_escape = rest[closing + 1 :]

                    if escape[0] == "u":
                        digits = escape[1:]
                        if not digits:
                            break

                        if not _LOWER_HEXDIGITS.issuperset(digits):
                            break

                        c = int(digits, 16)
                        # `char::from_u32` refuses surrogates (which `chr` accepts) and
                        # anything past U+10FFFF; the reference also keeps controls literal.
                        if c > 0x10FFFF or 0xD800 <= c <= 0xDFFF:
                            break
                        if c < 0x20 or 0x7F <= c <= 0x9F:
                            break
                        disp += chr(c)

                        rest = after_escape
                        continue

                    else:
                        if escape not in _UNESCAPED:
                            break
                        disp += _UNESCAPED[escape]
                        rest = after_escape
                        continue

                else:
                    # With no `$` left the remainder is taken as it stands, dots and all,
                    # as the reference does.
                    dollar = rest.find("$")
                    if dollar == -1:
                        break
                    dot = rest.find(".")
                    if dot == -1 or dollar < dot:
                        disp += rest[:dollar]
                        rest = rest[dollar:]
                    else:
                        disp += rest[:dot]
                        rest = rest[dot:]
            disp += rest
            self.spans.append((component, len(disp)))

        # The hash, spelled as rustc-demangle's `{}` does (`{:#}` suppresses it), before
        # the vendor suffix. `is not None`: a bare `h` is spelled `::h`.
        if self.keep_hash and self.hash is not None:
            disp += f"::h{self.hash}"

        # What follows the closing `E` is kept only as a `.`-introduced vendor suffix, as
        # the reference does; otherwise `_ZN3fooE.llvm moocow` would read as `foo`.
        if inn[:1] != "E":
            raise UnableToLegacyDemangle(original_inpstr)
        self.suffix = inn[1:]
        if self.suffix:
            if not (self.suffix.startswith(".") and _is_symbol_like(self.suffix)):
                raise UnableToLegacyDemangle(original_inpstr)
            disp += self.suffix

        return disp

    def is_rust_hash(self, s):
        """`h` followed by hex digits, which is the reference's whole test.

        `s` has already had its length prefix consumed, so the hash component of
        `_ZN3foo17h0123456789abcdefE` arrives here as `h0123456789abcdef`. There is no
        arm for the length-prefixed spelling, testing `s` for `17h`; it could never fire.
        The prefix scan takes the *maximal* run of digits, so `s` never begins with one --
        `19` followed by `17h...` reads as the length 1917, and the name is refused before
        this is reached at all.
        """
        return s.startswith("h") and _HEXDIGITS.issuperset(s[1:])

    def sanity_check(self, inpstr: str):
        # The reference refuses any byte with bit 7 set.
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

        # The path was never terminated: `_ZN3std` is not `std`.
        if c >= limit:
            raise UnableToLegacyDemangle(inpstr)
