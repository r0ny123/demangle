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
    def __init__(self):
        self.legacy = LegacyDemangler()
        self.v0 = V0Demangler()

    def demangle(self, inpstr: str) -> str:
        """Demangle the given string

        Args:
            inpstr (str): String to be demangled
        """
        return self._for(inpstr).demangle(inpstr)

    def structure(self, inpstr: str):
        """Demangle to a tree rather than to text.

        Same parser, same pass; only what it emits into differs. The tree renders to
        exactly what `demangle` returns for the same input.
        """
        return self._for(inpstr).structure(inpstr)

    def _for(self, inpstr):
        if self.determine_type(inpstr) == ManglingType.LEGACY:
            return self.legacy
        return self.v0

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
