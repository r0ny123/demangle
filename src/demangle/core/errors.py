"""The exception hierarchy.

Two audiences need different things from a failure. A tool labelling every symbol in a
binary wants the name back and no exception, because most symbols are not mangled and
that is not an error. A tool parsing one specific name the user typed wants to be told
precisely what was wrong with it. The split between `demangle()` and `parse()` in the
public API is exactly this split, and these are the types the second half raises.
"""


class DemanglingError(Exception):
    """Base class for every failure this package reports."""


class NotMangledError(DemanglingError):
    """The name carries no recognised mangling prefix.

    Distinct from `ParseError`: this is "not addressed to us", not "malformed". A plain
    C symbol like `memcpy` is not an error, it is simply not a mangled name.
    """

    def __init__(self, mangled, message="not a mangled name"):
        self.mangled = mangled
        super().__init__(f"{message}: {mangled!r}")


class ParseError(DemanglingError):
    """The name carries a known prefix but does not follow the grammar.

    Records where the parser gave up, which is the difference between a usable bug
    report and a shrug.
    """

    def __init__(self, mangled, position=None, message="malformed mangled name"):
        self.mangled = mangled
        self.position = position
        where = f" at offset {position}" if position is not None else ""
        super().__init__(f"{message}{where}: {mangled!r}")


class TruncatedError(ParseError):
    """The name ended in the middle of a production.

    Common enough in real symbol tables -- names get cut by fixed-width fields and by
    tools that assume a length limit -- to be worth telling apart from other malformations.
    """

    def __init__(self, mangled, position=None):
        super().__init__(mangled, position, "mangled name ends mid-production")


class LimitExceeded(ParseError):
    """A bound protecting against pathological input was hit.

    A mangled name is attacker-controlled input in any tool that opens files it did not
    produce. Recursion depth, output size and substitution count are all capped, and
    hitting a cap is reported rather than being allowed to exhaust the process.
    """

    def __init__(self, mangled, limit_name, limit_value):
        self.limit_name = limit_name
        self.limit_value = limit_value
        super().__init__(mangled, None, f"exceeded {limit_name} limit of {limit_value}")


# Never mean "malformed name": a best-effort `except Exception` must let these through,
# or a Ctrl-C inside a parser becomes an unmangled name.
OPERATIONAL_EXCEPTIONS = (KeyboardInterrupt, SystemExit, MemoryError, GeneratorExit)


def reraise_if_operational(exc):
    """Re-raise `exc` if it is an interpreter-level failure rather than a parse failure."""
    if isinstance(exc, OPERATIONAL_EXCEPTIONS):
        raise exc
