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
    """The `T_` dictionary: the template arguments currently in scope.

    Separate from `SubstitutionTable` because it is a separate mechanism with separate
    numbering -- `T_` indexes the enclosing template's argument list, `S_` indexes every
    substitutable component seen so far. Conflating them is a classic source of wrong
    output, so they do not share a type.

    Scopes nest: a lambda inside a template function has its own parameters while the
    enclosing ones remain visible. `scope()` gives a context manager for that.
    """

    __slots__ = ("_arguments", "_saved")

    def __init__(self):
        self._arguments = []
        self._saved = []

    def add(self, handle):
        self._arguments.append(handle)
        return handle

    def extend(self, handles):
        self._arguments.extend(handles)

    def lookup(self, index):
        """Resolve `T<index>_`, or None when the binding is not in scope.

        Returning None rather than raising is deliberate. A return type is encoded
        before the argument list that binds its parameters, so a name can legitimately
        reference `T_` at a point where we do not yet know it; the parser spells that as
        `auto`, which is what the reference demanglers do.
        """
        if 0 <= index < len(self._arguments):
            return self._arguments[index]
        return None

    def snapshot(self):
        return tuple(self._arguments)

    def restore(self, snapshot):
        self._arguments = list(snapshot)

    def __len__(self):
        return len(self._arguments)

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"TemplateArgumentTable({len(self._arguments)} in scope)"
