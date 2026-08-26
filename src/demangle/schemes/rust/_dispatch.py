from enum import Enum

from ._legacy import LegacyDemangler
from ._v0 import V0Demangler


class ManglingType(Enum):
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
    the first symbol's key. Measured before this changed: 160 wrong answers out of
    5,710 symbols across eight threads.

    A parser is two attribute stores to allocate, against a parse that is tens of
    microseconds, so per-name construction does not show up in the benchmark.
    """

    def demangle(self, inpstr: str, limit: int) -> str:
        """Demangle the given string

        Args:
            inpstr (str): String to be demangled
            limit (int): most characters the printer may write
        """
        return self._for(inpstr).demangle(inpstr, limit)

    def structure(self, inpstr: str, limit: int):
        """Demangle to a tree rather than to text.

        Same parser, same pass; only what it emits into differs. The tree renders to
        exactly what `demangle` returns for the same input.
        """
        return self._for(inpstr).structure(inpstr, limit)

    def _for(self, inpstr):
        if self.determine_type(inpstr) == ManglingType.LEGACY:
            return LegacyDemangler()
        return V0Demangler()

    def determine_type(self, inpstr: str) -> ManglingType:
        """Determine the type of the given string

        Args:
            inpstr (str): Input String

        Raises:
            TypeNotFoundError: If the string can't be determined

        Returns:
            ManglingType: type of the string

        Note:
            We intentionally exclude bare 'R' and 'ZN' prefixes as they are
            too broad and could match non-Rust symbols.
        """
        if inpstr.startswith(("_ZN", "__ZN")):
            return ManglingType.LEGACY
        elif inpstr.startswith(("_R", "__R")):
            return ManglingType.V0
        else:
            raise TypeNotFoundError(inpstr)
