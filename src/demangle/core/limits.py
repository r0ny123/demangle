"""Bounds on what a parser will do before giving up.

Every one of these exists because a mangled name is untrusted input. Symbol tables are
read from files the tool did not write, and the schemes are recursive: a few dozen bytes
can describe a type nested deeply enough to exhaust the C stack, or one whose spelling
is larger than memory. The defaults are set well above anything a real compiler emits.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Limits:
    """Resource bounds for one parse.

    Frozen so a limit set can be shared between threads and cached without defensive
    copying.
    """

    max_depth: int = 256
    """Nesting depth of recursive productions. Real names rarely pass 20.

    At the defaults this is the bound that decides: an Itanium name nested past it stops
    with `LimitExceeded` naming `max_depth`, whatever the shape. The interpreter's
    recursion limit is a second, independent ceiling. A production costs several Python
    frames, so at the default limit of 1000 it binds only when this number is raised,
    at roughly 140 levels of nested template, 164 of function type and 494 of pointer,
    or when the caller's stack is already deep. It is reported the same way, as
    `LimitExceeded("recursion depth")` naming this bound, and it is then the caller's
    bound in fact. `sys.setrecursionlimit()` moves it.
    """

    max_output: int = 1 << 16
    """Characters of rendered output. Template expansion can amplify enormously."""

    max_substitutions: int = 8192
    """Entries in the back-reference dictionary."""

    max_input: int = 1 << 16
    """Characters of mangled input considered at all."""


DEFAULT_LIMITS = Limits()
"""The bounds every entry point uses unless it is passed others."""

RELAXED_LIMITS = Limits(max_depth=2048, max_output=1 << 22, max_substitutions=1 << 16, max_input=1 << 22)
"""Bounds for callers that trust their input and want the ceiling out of the way.

Still finite: "trusted" is a statement about intent, not about correctness.

`max_depth` here is not reachable at the interpreter's default recursion limit -- see
the note on `Limits.max_depth`. It is left high on purpose: it says what this package
will allow, and a caller who raises `sys.setrecursionlimit()` gets it.
"""
