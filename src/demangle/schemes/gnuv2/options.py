"""Which of the five pre-Itanium manglings to read, and how much of the name to spell.

The reference is one demangler with a style flag, not five demanglers, and the flag has
to be supplied: `AtEnd__13ivRubberGroup` is a valid name under every one of them and they
do not all read it the same way. There is no marker in the name to key on, which is why
this is an option rather than something detection could work out.

`DMGL_PARAMS` and `DMGL_ANSI` are the reference's other two flags and are carried for the
same reason: they change what comes out, and the vectors in `demangle-expected` pin both
settings of the first.
"""

from dataclasses import dataclass

from ._parser import STYLES

__all__ = ["DEFAULT_OPTIONS", "GnuV2Options"]


@dataclass(frozen=True, slots=True)
class GnuV2Options:
    """How to read a pre-Itanium C++ name."""

    style: str = "gnu"
    """Which compiler wrote it: `gnu`, `lucid`, `arm`, `hp`, `edg`, or `auto`.

    `gnu` is g++ before 3.0 and the default, being the one of the five that a binary in
    the wild is most likely to hold. `arm` is the encoding the Annotated Reference Manual
    describes, which cfront and its descendants emit; `lucid` is Lucid's `lcc`; `hp` is
    HP aCC; `edg` is the Edison Design Group front end. `auto` is the reference's
    `auto_demangling`: GNU's reading plus EDG's parameterised-type prefixes, and not a
    search over the other four.
    """

    params: bool = True
    """Spell the argument list, and the qualifiers that follow it.

    `DMGL_PARAMS`. False gives what `c++filt -p` prints: `ivTSolver::AddAlignment` rather
    than `ivTSolver::AddAlignment(unsigned int, ivInteractor *, ivTGlue *)`.
    """

    ansi: bool = True
    """Spell ANSI C++ qualifiers -- `const`, `volatile`, `__restrict` -- on types.

    `DMGL_ANSI`. False drops them, which is what a demangler predating the standard did.
    """

    def __post_init__(self):
        if self.style not in STYLES:
            raise ValueError(f"unknown style {self.style!r}; expected one of {', '.join(STYLES)}")


#: What `demangle()` uses when a caller names no options: GNU's reading, spelled in full.
DEFAULT_OPTIONS = GnuV2Options()
