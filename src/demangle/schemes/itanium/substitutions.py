"""Back-reference tables for the Itanium ABI.

The scheme compresses a name by letting any component that has already appeared be
written back as `S_`, `S0_`, `S1_`. Nothing in the name says what those indices refer
to: the numbering is implicit, determined entirely by which components the *encoder*
considered substitutable and the order it met them. Get the candidate set wrong by one
entry and every later back-reference in that name resolves to the wrong component --
usually producing output that still looks like a plausible C++ name, which is the worst
kind of wrong.

So this is not a cache. It is a load-bearing part of the grammar, and it lives in its
own module, with the specification quoted at each decision, so it can be tested directly
rather than only through whole-name round trips.

Reference: Itanium C++ ABI section 5.1.10, "Compression".
"""

from ...core.errors import LimitExceeded, ParseError

#: Section 5.1.10: "Each non-terminal in the grammar above for which <substitution>
#: appears on the right-hand side is both a source of future substitutions and a
#: candidate for being substituted." Enumerated here so the parser's `remember()` calls
#: can be checked against the specification rather than against intuition.
CANDIDATE_PRODUCTIONS = frozenset(
    {
        "type",
        "prefix",
        "template-prefix",
        "unscoped-template-name",
        "template-template-param",
        "unresolved-type",
        "module-name",
    }
)

#: Section 5.1.10 names two exclusions that look like candidates in the grammar but are
#: not: "<builtin-type> other than vendor extended types, and function and operator
#: names other than extern "C" functions."
EXCLUDED_PRODUCTIONS = frozenset({"builtin-type", "function-name", "operator-name"})


class SubstitutionTable:
    """The `S_` dictionary for one mangled name.

    One instance per parse. Entries hold builder handles, so what is stored depends on
    which builder is driving -- the table itself is deliberately ignorant of that.

    On not deduplicating
    --------------------
    The specification says "No entity is added to the dictionary twice", which reads
    like an instruction to deduplicate. For a *demangler* it is the opposite.

    The encoder only spells a component out in full when it has not seen it before; a
    repeat is written as a back-reference. So a well-formed name never spells the same
    component twice, and every spelled-out component we meet is one the encoder had just
    added. Appending unconditionally reproduces its numbering exactly.

    Deduplicating on our side would actively break names the specification calls out:
    "The type of a non-static member function is considered to be different, for the
    purposes of substitution, from the type of a namespace-scope or static member
    function whose type appears similar." Those two spell identically and both get
    entries. Keyed on spelling, a deduplicating table would collapse them and shift
    every subsequent index.
    """

    __slots__ = ("_entries", "_limit", "_mangled")

    def __init__(self, mangled, limit=8192):
        self._entries = []
        self._limit = limit
        self._mangled = mangled

    def remember(self, handle, production="type"):
        """Record a substitutable component and return it unchanged.

        `production` names the grammar non-terminal being recorded. It is checked
        against the specification's candidate set, so a parser that records something
        the ABI does not fails loudly here instead of silently renumbering the table.
        """
        if production not in CANDIDATE_PRODUCTIONS:
            raise AssertionError(
                f"{production!r} is not a substitution candidate under ABI 5.1.10; "
                f"candidates are {sorted(CANDIDATE_PRODUCTIONS)}"
            )
        if len(self._entries) >= self._limit:
            raise LimitExceeded(self._mangled, "substitution", self._limit)
        self._entries.append(handle)
        return handle

    def mark(self):
        """How many entries there are, for `rewind`."""
        return len(self._entries)

    def capture(self, mark):
        """Everything recorded since `mark`, for `restore_from`."""
        return self._entries[mark:]

    def restore_from(self, mark, entries):
        """Put back exactly what `capture` took, discarding whatever replaced it.

        Used to read one span of a name twice. A conversion operator's type is written
        before the template arguments that bind its parameters, so it cannot be spelled
        until they have been read -- and the second reading must leave the table exactly
        as the first did, or every later back-reference in the name shifts.
        """
        self._entries[mark:] = entries

    def lookup(self, index):
        """Resolve `S<index>_`."""
        if index < 0 or index >= len(self._entries):
            raise ParseError(
                self._mangled, None, f"substitution S{index}_ refers past the {len(self._entries)} known entries"
            )
        return self._entries[index]

    def __len__(self):
        return len(self._entries)

    def __iter__(self):
        return iter(self._entries)

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"SubstitutionTable({len(self._entries)} entries)"


class TemplateArgumentTable:
    """The `T_` dictionary: a *stack* of the parameter lists currently in scope.

    Separate from `SubstitutionTable` because it is a separate mechanism with separate
    numbering -- `T_` indexes the enclosing template's argument list, `S_` indexes every
    substitutable component seen so far. Conflating them is a classic source of wrong
    output, so they do not share a type.

    A stack rather than one list, because `<template-param>` names a *level* as well as
    an index. `T_` and `T<n>_` mean level 0, the innermost enclosing `<template-args>`.
    `TL<k>_<n>_` means level `k + 1`, which is a list a generic lambda or a template
    template parameter declared -- and those nest, so a name can reach past one to the
    one outside it:

        []<typename $T, template<typename $T0, $T $N> typename $TT>(...)

    Here `$T` is level 0 index 0 and `$T0` is level 1 index 0, and the `$N` declaration
    reaches both. Held flat, the two levels overwrote each other and the parameter came
    out spelled `T`, which names nothing.

    A level with nothing in it is still a level: a generic lambda that declared no
    parameters occupies one, because its `auto` parameters are numbered against it and a
    `TL` reference from inside it counts through it.

    Scopes nest in the other direction too -- a local entity has parameters of its own
    while the enclosing ones stay out of reach -- so `snapshot()` and `restore()` save
    and put back the whole stack.
    """

    __slots__ = ("_levels",)

    _levels: list[list]

    def __init__(self):
        self._levels = []

    # -- level 0: the innermost <template-args> --------------------------------------

    def install(self):
        """Begin a fresh level 0. These arguments replace every level in scope."""
        self._levels = [[]]

    def add(self, handle):
        """Bind the next argument of level 0, as an argument list is read."""
        if not self._levels:
            self._levels = [[]]
        self._levels[0].append(handle)
        return handle

    def outer(self):
        """Level 0's bindings, for a caller that has to look at all of them at once."""
        return self._levels[0] if self._levels else ()

    # -- levels 1..n: what a lambda or a template template parameter declared ---------

    def push(self, declared):
        """Enter a nested level, which fills up as its declarations are read."""
        self._levels.append(declared)

    def pop(self):
        self._levels.pop()

    def depth(self):
        return len(self._levels)

    def clear(self):
        """Drop every level. A lambda that *is* the entity being named starts fresh."""
        self._levels = []

    def lookup(self, index, level=0):
        """Resolve `TL<level>_<index>_`, or None when the binding is not in scope.

        Returning None rather than raising is deliberate. A generic lambda's `auto`
        parameter is mangled as a reference to a parameter it never declared (ABI
        5.1.8), so a miss is a normal reading and the parser decides what it means.
        """
        if not 0 <= level < len(self._levels):
            return None
        params = self._levels[level]
        if not 0 <= index < len(params):
            return None
        return params[index]

    def snapshot(self):
        return tuple(tuple(level) for level in self._levels)

    def restore(self, snapshot):
        self._levels = [list(level) for level in snapshot]

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"TemplateArgumentTable({[len(level) for level in self._levels]})"
