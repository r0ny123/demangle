"""How much of a CodeWarrior name to spell, and whether to read Metrowerks' extensions.

Both are the reference's own options, kept because they change what comes out and the
vectors in `cwdemangle`'s test module pin both settings of each.
"""

from dataclasses import dataclass

__all__ = ["DEFAULT_OPTIONS", "CodeWarriorOptions"]


@dataclass(frozen=True, slots=True)
class CodeWarriorOptions:
    """How to spell a CodeWarrior name."""

    omit_empty_parameters: bool = True
    """Write `()` for a function that takes none, rather than `(void)`.

    `omit_empty_parameters`. True is the reference's default and what its CLI prints.
    """

    mw_extensions: bool = False
    """Read Metrowerks' extension types, `__int128` and `__vec2x32float__`.

    `mw_extensions`. Off by default, and that is the reference's choice rather than a
    conservative reading of it: the two are spelled `1` and `2`, which collide with a
    template argument literal, so a name carrying either cannot always be told from one
    carrying a small integer. Turning this on is a caller saying they know which.
    """


DEFAULT_OPTIONS = CodeWarriorOptions()
"""What `demangle()` uses when a caller names no options: the reference's own defaults."""
