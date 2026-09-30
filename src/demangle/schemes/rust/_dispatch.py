from enum import Enum

from ._legacy import LegacyDemangler
from ._v0 import _DEFAULT_MAX_DEPTH, V0Demangler


class ManglingType(Enum):
    """Which of Rust's two manglings a name uses: legacy (`_ZN...E`) or v0 (`_R...`)."""

    LEGACY = 0
    V0 = 1


class TypeNotFoundError(Exception):
    def __init__(self, given_str, message="Not able to detect the Type for the given string"):
        self.message = message
        self.given_str = given_str
        super().__init__(self.message)

    def __str__(self):
        return f"[{self.given_str}] {self.message}"


class RustDemangler:
    """Routes a name to the scheme that mangled it.

    Holds no state of its own, and hands out a *fresh* parser per name rather than
    reusing one. That is not fastidiousness: both parsers keep the name they are
    reading, its suffix and its path spans on `self`, so a single shared instance
    lets two threads overwrite each other's parse -- and the failure is silent. It
    does not raise; it returns another symbol's name, which is then memoised under
    the first symbol's key.

    A parser is two attribute stores to allocate, against a parse that is tens of
    microseconds, so per-name construction does not show up in the benchmark.
    """

    def demangle(self, inpstr: str, limit: int, keep_hash: bool = False, max_depth: int = _DEFAULT_MAX_DEPTH) -> str:
        """Spell `inpstr`, in whichever of the two manglings it uses.

        Args:
            inpstr: the mangled name.
            limit: the most characters the printer may write.
            keep_hash: spell the disambiguating hash rather than dropping it.
            max_depth: the deepest v0 nesting to read, as `Limits.max_depth`.
        """
        return self._for(inpstr, keep_hash, max_depth).demangle(inpstr, limit)

    def structure(self, inpstr: str, limit: int, keep_hash: bool = False, max_depth: int = _DEFAULT_MAX_DEPTH):
        """Demangle to a tree rather than to text.

        Same parser, same pass; only what it emits into differs. The tree renders to
        exactly what `demangle` returns for the same input -- `keep_hash` included, since
        both are one stream of fragments.
        """
        return self._for(inpstr, keep_hash, max_depth).structure(inpstr, limit)

    def _for(self, inpstr, keep_hash=False, max_depth=_DEFAULT_MAX_DEPTH):
        if self.determine_type(inpstr) == ManglingType.LEGACY:
            return LegacyDemangler(keep_hash)
        return V0Demangler(keep_hash, max_depth)

    def determine_type(self, inpstr: str) -> ManglingType:
        """Say which of the two manglings `inpstr` uses, by its prefix alone.

        Args:
            inpstr: the mangled name.

        Returns:
            `ManglingType.LEGACY` or `ManglingType.V0`.

        Raises:
            TypeNotFoundError: `inpstr` starts the way neither mangling does.

        Note:
            A bare `R` is accepted here, like the bare `ZN` below it: some symbol tables
            have had the leading underscore stripped before the name got here, and the
            reference reads that form too. It stays out of `detect`, which is deliberately
            narrower -- a bare `R` is too broad a claim to make about every symbol in a
            binary -- so auto-detection still needs `_R` or `__R`.
        """
        if inpstr.startswith(("_ZN", "__ZN", "ZN")):
            return ManglingType.LEGACY
        elif inpstr.startswith(("_R", "__R", "R")):
            return ManglingType.V0
        else:
            raise TypeNotFoundError(inpstr)
