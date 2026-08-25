"""The contract a mangling scheme implements.

A language is a `LanguagePlugin`. Nothing in `core` imports a language module, and no
language imports another, so a scheme can be developed, tested, replaced or shipped
separately from everything else here.

Adding one means providing three things and registering them. It does not mean
understanding the rest of the codebase, which is the point: the project should be
approachable to a contributor who knows one ABI well and nothing else about it.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class LanguagePlugin:
    """One mangling scheme."""

    name: str
    """Stable identifier, used in the API and on the command line: `itanium`, `msvc`."""

    detect: Callable[[str], bool]
    """Cheap test for "is this plausibly mine".

    Called on every symbol a caller offers, including the great majority that are not
    mangled at all, so it must not allocate or parse. A prefix comparison is the
    expected shape. False negatives lose symbols; false positives only cost a failed
    parse, so err towards accepting.
    """

    parse: Callable[..., Any]
    """`parse(mangled, builder, limits=..., options=...) -> handle`.

    Raises `NotMangledError` or `ParseError` on failure; never returns a partial result.
    """

    description: str = ""
    """One line, shown by `demangle --list-languages`."""

    aliases: Sequence[str] = field(default_factory=tuple)
    """Other names callers may use: `gnu` and `gcc` both mean `itanium`."""

    options_type: Any = None
    """The dataclass this language accepts as `options`, if it takes any."""

    symbol_table_decorations: bool = False
    """Whether names in this scheme may carry symbol-table decorations.

    True for schemes that appear in ELF and Mach-O symbol tables, where the linker
    appends version suffixes and the compiler appends clone suffixes. It must stay False
    for MSVC, whose decorated names use `@` as their own scope separator and would be
    truncated at the first one.
    """

    first_characters: str = ""
    """The characters a name of this scheme may begin with, if the set is small.

    An optimisation, and one the registry checks rather than trusts: a name whose first
    character is not in here is never offered to `detect`. Leave it empty -- the default
    -- for a scheme whose names have no fixed start, which costs nothing but a call.

    Getting it wrong loses symbols silently, so the rule is narrow: put a character here
    only if `detect` returns False for *every* name that does not begin with one of
    them. `tests/test_core.py` checks the built-in schemes against their own corpora.
    """

    priority: int = 100
    """Detection order, lower first.

    Matters only where two schemes share a prefix. Rust's legacy scheme *is* Itanium
    mangling, so Rust must be offered the name first or every Rust symbol demangles as
    a C++ one.
    """
