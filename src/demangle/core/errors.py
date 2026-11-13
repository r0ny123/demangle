"""The exception hierarchy.

Two audiences need different things from a failure. A tool labelling every symbol in a
binary wants the name back and no exception, because most symbols are not mangled and
that is not an error. A tool parsing one specific name the user typed wants to be told
precisely what was wrong with it. The split between `demangle()` and `parse()` in the
public API is exactly this split, and these are the types the second half raises.
"""

__all__ = [
    "OPERATIONAL_EXCEPTIONS",
    "DemanglingError",
    "LimitExceeded",
    "NotMangledError",
    "ParseError",
    "TruncatedError",
    "recursion_error_lost",
    "reraise_if_operational",
]


class DemanglingError(Exception):
    """Base class for every failure this package reports."""

    mangled: str
    """The name that could not be read."""


class NotMangledError(DemanglingError):
    """The name carries no recognised mangling prefix.

    Distinct from `ParseError`: this is "not addressed to us", not "malformed". A plain
    C symbol like `memcpy` is not an error, it is simply not a mangled name.
    """

    def __init__(self, mangled: str, message: str = "not a mangled name") -> None:
        self.mangled = mangled
        super().__init__(f"{message}: {mangled!r}")


class ParseError(DemanglingError):
    """The name carries a known prefix but does not follow the grammar.

    Records where the parser gave up, which is the difference between a usable bug
    report and a shrug.
    """

    def __init__(self, mangled: str, position: int | None = None, message: str = "malformed mangled name") -> None:
        self.mangled = mangled
        self.position = position
        where = f" at offset {position}" if position is not None else ""
        super().__init__(f"{message}{where}: {mangled!r}")


class TruncatedError(ParseError):
    """The name ended in the middle of a production.

    Common enough in real symbol tables -- names get cut by fixed-width fields and by
    tools that assume a length limit -- to be worth telling apart from other malformations.
    """

    def __init__(self, mangled: str, position: int | None = None) -> None:
        super().__init__(mangled, position, "mangled name ends mid-production")


class LimitExceeded(DemanglingError):
    """A bound protecting against pathological input was hit.

    Not a `ParseError`: the name may be well formed, and `except ParseError` should not
    take "too large to read here" for "malformed".

    A mangled name is attacker-controlled input in any tool that opens files it did not
    produce. Recursion depth, output size and substitution count are all capped, and
    hitting a cap is reported rather than being allowed to exhaust the process.
    """

    def __init__(self, mangled: str, limit_name: str, limit_value: int) -> None:
        self.mangled = mangled
        self.limit_name = limit_name
        self.limit_value = limit_value
        super().__init__(f"exceeded {limit_name} limit of {limit_value}: {mangled!r}")


# Never mean "malformed name": a best-effort `except Exception` must let these through,
# or a Ctrl-C inside a parser becomes an unmangled name.
OPERATIONAL_EXCEPTIONS = (KeyboardInterrupt, SystemExit, MemoryError, GeneratorExit)


def reraise_if_operational(exc: BaseException) -> None:
    """Re-raise `exc` if it is an interpreter-level failure rather than a parse failure."""
    if isinstance(exc, OPERATIONAL_EXCEPTIONS):
        raise exc


def recursion_error_lost(exc: BaseException) -> bool:
    """Whether `exc` is PyPy's stand-in for a `RecursionError` it could not raise.

    When a name nests past the interpreter's stack, the `RecursionError` unwinds through
    `finally` blocks that are still at the edge of the stack. On PyPy one of them can
    overflow again, and attaching that second error to the first needs more stack still;
    PyPy then raises `TypeError("couldn't record exception context for exception ...,
    got: RecursionError(...)")` in their place. It is the same fact -- the name nests
    further than this process will follow -- so every boundary that reports a
    `RecursionError` as `LimitExceeded` reports this too, rather than as a parser defect.
    CPython never raises it.
    """
    if type(exc) is not TypeError:
        return False
    message = str(exc)
    return message.startswith(_LOST_CONTEXT) and _GOT_RECURSION in message


_LOST_CONTEXT = "couldn't record exception context for exception "
_GOT_RECURSION = ", got: RecursionError("
