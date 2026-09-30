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


class SubstitutionOverrun(ParseError):
    """A `S<n>_` past the end of the table: the name was numbered by a rule this reading
    did not apply, or it is not a name. `parse` retries the one rule that is known to
    differ between compilers before giving up; see `ItaniumOptions.undeduced_auto_substitution`."""


class SubstitutionMisuse(ParseError):
    """A `S<n>_` naming an entry that cannot stand where it was read: a closure prefix,
    or a template name with no arguments after it, used as a type. Neither is a type --
    the one is a variable's name and the other a template's -- so the name was numbered
    by a rule this reading did not apply, or it is not a name. `parse` retries the one
    rule known to move a closure prefix's neighbours before giving up; see
    `ItaniumOptions.closure_prefix_substitution`."""


#: 5.1.10: every non-terminal with <substitution> on its right-hand side, the set the
#: parser's `remember()` calls must match.
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

#: 5.1.10's exclusions: "<builtin-type> other than vendor extended types, and function
#: and operator names other than extern "C" functions."
EXCLUDED_PRODUCTIONS = frozenset({"builtin-type", "function-name", "operator-name"})


class ParameterReference:
    """A recorded `<template-param>`, kept as the reference it is rather than resolved.

    A `<template-param>` is a substitution candidate in its own right (5.1.10), and the
    entry it contributes is *the parameter*, not the argument bound to it at the moment
    it was written. The two differ whenever the back-reference is read under a different
    template scope, which happens in any name that mentions a local entity: the
    signature of the enclosing function is written against that function's parameters,
    so a `T_` inside it enters the table, and a later `S<n>_` naming that entry belongs
    to the *outer* template.

    Settled against the mangler rather than a demangler. For

        template <class T> void legalize(Update<T>*);
        template <class I, class C> void insort(I, I, C);

    with `insort<Update<BB*>*, Wrap<lambda-in-legalize>>` instantiated from inside
    `legalize<BB*>`, g++ 13.3 emits `...EEvS8_S8_T0_`, where `S8_` is the entry the `T_`
    inside `legalize`'s signature contributed. The parameters of `insort` are of type
    `I` -- `nn::Update<nn::BB*>*` -- and GNU c++filt prints exactly that. Resolving the
    entry to what `T_` meant where it was recorded gives `nn::BB*` instead: a different
    type, spelled plausibly, which is the failure this package exists to avoid.
    llvm-cxxfilt 18 has that bug, and differs from this on 322 of the 217,730 distinct
    Itanium symbols in the shared libraries of a stock Ubuntu 24.04.

    The level is carried as well as the index, because `TL<k>_<n>_` names a parameter of
    an enclosing template and the entry stands for that parameter, not for level 0's.

    Held opaque by the table -- it is the parser that owns the template scope, so it is
    the parser that turns one of these back into a handle.
    """

    __slots__ = ("index", "level", "symbolic")

    def __init__(self, index, level=0, symbolic=None):
        self.index = index
        self.level = level
        #: Mangled text of a parameter read inside a requires-clause whose scope may be
        #: gone when a later `S_` names it; None outside a clause.
        self.symbolic = symbolic

    def __repr__(self):  # pragma: no cover - debugging aid
        name = "T" + ("" if self.index == 0 else str(self.index - 1))
        return f"ParameterReference({name}_, level {self.level})"


class DeferredProduction:
    """A recorded component built *over* a `<template-param>`, kept as its input span.

    `ParameterReference` covers the bare parameter. This covers everything wrapped
    around one -- `R T_`, `P N S1_ I T_ E E`, a template applied to it -- which is
    scope-dependent for exactly the same reason and which the mangler reuses across
    scopes for exactly the same reason: it canonicalises a template type parameter by
    level and index, so the composite over one canonicalises the same way.

    From `insort(Update<I>*, Update<I>*, C)` instantiated inside `legalize<T>`, g++ 13.3
    emits parameters written `SA_`, the entry `P N S1_ I T_ E E` contributed inside
    `legalize`'s own signature. Under `insort` that is `nn::Update<nn::Update<nn::BB*>*>*`,
    which is what the declaration says and what GNU c++filt prints; frozen where it was
    recorded it is `nn::Update<nn::BB*>*`, which is a different type.

    There is nothing to freeze *or* to defer in the handle itself, because a handle is
    already-built output. What is kept instead is where the production was written, so
    the parser can read it again under the scope now in force. The span is a complete
    `<type>`, and a `<type>` can only back-reference entries recorded before it, so
    re-reading one terminates: each step moves strictly earlier in the table.
    """

    __slots__ = ("end", "start")

    def __init__(self, start, end):
        self.start = start
        self.end = end

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"DeferredProduction({self.start}:{self.end})"


class SubstitutionTable:
    """The `S_` dictionary for one mangled name.

    One instance per parse. Most entries hold builder handles, so what is stored depends
    on which builder is driving -- the table itself is deliberately ignorant of that. Two
    kinds of entry are not handles at all: `ParameterReference` and
    `DeferredProduction`, which stand for a component whose spelling depends on the
    template scope it is *read* under rather than the one it was written under. The table
    holds them opaquely; it is the parser that owns the scope, so it is the parser that
    turns one back into a handle.

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

    __slots__ = ("_entries", "_limit", "_mangled", "recording")

    def __init__(self, mangled, limit=8192):
        self._entries = []
        self._limit = limit
        self._mangled = mangled
        #: False while re-reading a `DeferredProduction`, whose entries already exist;
        #: adding them again would renumber the table.
        self.recording = True

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
        if self.recording:
            entries = self._entries
            if len(entries) >= self._limit:
                raise LimitExceeded(self._mangled, "substitution", self._limit)
            entries.append(handle)
        return handle

    def defer_last(self, start, end):
        """Replace the entry just recorded with the span it was written at.

        Called by the production itself, once it knows a template parameter was resolved
        while it ran. Nothing is added or removed, so numbering is untouched -- only what
        the entry *is* changes.
        """
        if self._entries:
            self._entries[-1] = DeferredProduction(start, end)

    def drop_last(self, mark):
        """Forget the entry a production just recorded, if it recorded one.

        `mark` is `mark()` taken before the production ran, so nothing is dropped where
        nothing was added. The one caller is an inheriting constructor's base class type
        under the numbering clang uses, which reads that type without entering it while
        still entering the components inside it. See
        `ItaniumOptions.inherited_constructor_substitution`.
        """
        if len(self._entries) > mark:
            del self._entries[-1]

    @property
    def last(self):
        """The entry most recently recorded, or None."""
        return self._entries[-1] if self._entries else None

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
            raise SubstitutionOverrun(
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

    __slots__ = ("_levels", "generation")

    _levels: list[list]

    def __init__(self):
        self._levels = []
        #: Bumped on every change, so the parser can memoise a `DeferredProduction` per
        #: scope; re-reading it per back-reference is quadratic.
        self.generation = 0

    # -- level 0: the innermost <template-args> --------------------------------------

    def install(self):
        """Begin a fresh level 0. These arguments replace every level in scope."""
        self._levels = [[]]
        self.generation += 1

    def add(self, handle):
        """Bind the next argument of level 0, as an argument list is read."""
        if not self._levels:
            self._levels = [[]]
        self._levels[0].append(handle)
        self.generation += 1
        return handle

    def outer(self):
        """Level 0's bindings, for a caller that has to look at all of them at once."""
        return self._levels[0] if self._levels else ()

    # -- levels 1..n: what a lambda or a template template parameter declared ---------

    def push(self, declared):
        """Enter a nested level, which fills up as its declarations are read."""
        self._levels.append(declared)
        self.generation += 1

    def pop(self):
        self._levels.pop()
        self.generation += 1

    def depth(self):
        return len(self._levels)

    def clear(self):
        """Drop every level. A lambda that *is* the entity being named starts fresh."""
        self._levels = []
        self.generation += 1

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
        self.generation += 1

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"TemplateArgumentTable({[len(level) for level in self._levels]})"
