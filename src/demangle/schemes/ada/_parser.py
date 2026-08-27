"""GNAT's Ada encoding, transcribed from libiberty's `ada_demangle`.

The reference is `ada_demangle` in `libiberty/cplus-dem.c`, reached as
`c++filt --format=gnat`, and it is the only one of the pre-Itanium formats libiberty
kept when the rest were dropped. GCC's `exp_dbug.ads` documents the encoding
normatively; the C is what decides behaviour where the two could be read differently,
because the C is what `c++filt` runs.

The encoding is a dotted path written with `__`, plus a small set of suffixes that say
what *kind* of entity the name is:

    yz__qrs                     yz.qrs
    oper__Oadd                  oper."+"
    p__taskobjTKB               p.taskobj              (a task body)
    prot__lock__getP            prot.lock.get          (a protected subprogram)
    ...__slice_setSR__2         ....slice_set'Read     (a stream operation)
    ...__root_controlledDF      ....root_controlled.Finalize
    p___elabb                   p'Elab_Body

What it does *not* carry is types. An Ada symbol names an entity and stops; there is no
signature to read, so nothing here spells a parameter list. That is why a name this
scheme reads is so nearly an ordinary C identifier, and why detection is the delicate
part rather than the grammar -- see `__init__.py`.

One convention differs from the reference deliberately. Where `ada_demangle` cannot read
a name it returns it wrapped in angle brackets -- `x_E` comes back as `<x_E>` -- which is
`c++filt --format=gnat` saying "not mine" in the only channel it has. This raises
`DemangleFailure` instead, and `demangle()` then returns the name unchanged, which is
this library's way of saying the same thing. The corpus records the reference's `<...>`
rows as the name unchanged for that reason.
"""

__all__ = ["AdaSymbol", "DemangleFailure", "demangle_ada"]


class DemangleFailure(Exception):
    """This is not a name GNAT wrote, or not one this can read.

    The reference's `goto unknown`. Every path that reaches it corresponds to one there.
    """


#: `O`-prefixed operator names, longest first so that `Oadd` is not read as `Oa`.
#: Transcribed from the reference's `operators[][2]`, which is searched in order with a
#: prefix compare -- no entry there is a prefix of another, so order is presentation
#: only, but sorting by length keeps it that way if one is ever added.
_OPERATORS = {
    "Oabs": "abs",
    "Oand": "and",
    "Omod": "mod",
    "Onot": "not",
    "Oor": "or",
    "Orem": "rem",
    "Oxor": "xor",
    "Oeq": "=",
    "One": "/=",
    "Olt": "<",
    "Ole": "<=",
    "Ogt": ">",
    "Oge": ">=",
    "Oadd": "+",
    "Osubtract": "-",
    "Oconcat": "&",
    "Omultiply": "*",
    "Odivide": "/",
    "Oexpon": "**",
}

#: Longest first, so `Oadd` wins over any shorter entry sharing its start.
_OPERATOR_KEYS = sorted(_OPERATORS, key=len, reverse=True)

#: `___`-introduced names for an operation the compiler generates rather than the
#: programmer writing it. The reference's `special[][2]`.
_SPECIAL = {
    "_elabb": "'Elab_Body",
    "_elabs": "'Elab_Spec",
    "_size": "'Size",
    "_alignment": "'Alignment",
    "_assign": '.":="',
}

_SPECIAL_KEYS = sorted(_SPECIAL, key=len, reverse=True)

#: `S` plus one of these is a stream attribute of the entity just named.
_STREAM = {"R": "'Read", "W": "'Write", "I": "'Input", "O": "'Output"}

#: `D` plus one of these is an operation on a controlled type.
_CONTROLLED = {"F": ".Finalize", "A": ".Adjust"}

#: What the library-level subprogram prefix is, and what it means: nothing, except that
#: this really is Ada. Stripped before anything else, exactly as the reference does.
_LIBRARY_PREFIX = "_ada_"


class AdaSymbol:
    """One demangled GNAT name.

    `text` is the whole spelling. `parts` are the dot-separated components in order,
    which is what the tree is built from: an Ada name is a path and nothing else, and
    splitting `text` on `.` would be wrong the moment a component is `.":="` or the
    entity carries a `.Finalize`.
    """

    __slots__ = ("evidence", "parts", "text", "unread")

    def __init__(self, text, parts, evidence, unread=""):
        self.text = text
        self.parts = tuple(parts)
        #: What the reference stopped without reading. Several of its suffixes `break`
        #: out of the loop and abandon whatever follows -- `...controllerDF__2` is
        #: `....Finalize`, the `__2` simply dropped -- so a name can be "read" with
        #: characters to spare. Faithful, and kept faithful; but `detect` uses this,
        #: because a name it cannot fully account for is a name it should not claim.
        self.unread = unread
        #: Which GNAT-specific encodings this name actually carried. Empty for a name
        #: whose whole content was lower-case identifiers joined by `__` -- which is to
        #: say, a name indistinguishable from an ordinary C one. `detect` reads this.
        self.evidence = frozenset(evidence)

    def __repr__(self):
        return f"AdaSymbol({self.text!r})"


def _is_lower(ch):
    return "a" <= ch <= "z"


def _is_digit(ch):
    return "0" <= ch <= "9"


def demangle_ada(mangled):
    """Read a GNAT-encoded name, or raise `DemangleFailure`.

    A transcription of `ada_demangle`, following its control flow rather than
    re-deriving the grammar: the loop, the ordered suffix tests, and every `goto
    unknown`. Comments name the reference's own.
    """
    if not mangled:
        raise DemangleFailure("empty name")

    evidence = set()
    text = mangled
    if text.startswith(_LIBRARY_PREFIX):
        # "Discard leading _ada_, which is used for library level subprograms."
        text = text[len(_LIBRARY_PREFIX) :]
        evidence.add("library-prefix")

    # "All ada unit names are lower-case."
    if not text or not _is_lower(text[0]):
        raise DemangleFailure("an Ada unit name starts with a lower-case letter")

    out = []
    parts = []
    at = 0
    size = len(text)

    def rest(offset=0):
        """The character at `at + offset`, or "" past the end -- the reference's `p[n]`.

        The C reads one past the last character freely, because the string is NUL
        terminated and `p[1] == 0` is a meaningful test. Returning "" keeps those tests
        readable here without a length check at every one.
        """
        index = at + offset
        return text[index] if index < size else ""

    while True:
        # ---- an entity name is expected ----------------------------------------
        start = at
        if _is_lower(rest()):
            # "An identifier, which is always lower case."
            at += 1
            # An underscore continues the identifier only when a lower-case letter or a
            # digit follows it: `x_E` stops at the `_`, which is what makes `_E` reach
            # the suffix tests as an exception marker rather than being eaten here.
            while (
                _is_lower(rest()) or _is_digit(rest()) or (rest() == "_" and (_is_lower(rest(1)) or _is_digit(rest(1))))
            ):
                at += 1
            out.append(text[start:at])
            parts.append(text[start:at])
        elif rest() == "O":
            # "An operator name."
            for key in _OPERATOR_KEYS:
                if text.startswith(key, at):
                    at += len(key)
                    spelled = f'"{_OPERATORS[key]}"'
                    out.append(spelled)
                    parts.append(spelled)
                    evidence.add("operator")
                    break
            else:
                raise DemangleFailure("not a GNAT operator name")
        else:
            # "Not a GNAT encoding."
            raise DemangleFailure("not a GNAT encoding")

        # ---- suffixes, in the reference's order ---------------------------------
        # These are sequential `if`s in the C, not a chain of `else if`s, and two of
        # them can fire for one name. Kept in the same order and with the same
        # independence, because reordering them changes which reading a name gets.
        if rest() == "T" and rest(1) == "K":
            evidence.add("task")
            if rest(2) == "B" and rest(3) == "":
                # "Subprogram for task body."
                at += 3
                break
            if rest(2) == "_" and rest(3) == "_":
                # "Inner declarations in a task."
                at += 4
                out.append(".")
                continue
            raise DemangleFailure("not a GNAT task encoding")

        if rest() == "E" and rest(1) == "":
            # "Exception name." -- the reference declines these.
            raise DemangleFailure("an exception name is not demangled")

        if rest() in ("P", "N") and rest(1) == "":
            # "Protected type subprogram."
            evidence.add("protected")
            at += 1
            break

        if rest() in ("N", "S") and rest(1) == "":
            # "Enumerated type name table." Only `S` can reach here: `N` broke above.
            raise DemangleFailure("an enumerated type name table is not demangled")

        if rest() == "X":
            # "Body nested."
            evidence.add("body-nested")
            at += 1
            while rest() in ("n", "b"):
                at += 1

        if rest() == "S" and rest(1) != "" and rest(2) in ("_", ""):
            # "Stream operations."
            name = _STREAM.get(rest(1))
            if name is None:
                raise DemangleFailure("not a GNAT stream operation")
            at += 2
            out.append(name)
            parts.append(name)
            evidence.add("stream")
        elif rest() == "D":
            # "Controlled type operation."
            name = _CONTROLLED.get(rest(1))
            if name is None:
                raise DemangleFailure("not a GNAT controlled-type operation")
            out.append(name)
            parts.append(name)
            evidence.add("controlled")
            at += 2
            break

        # ---- separators ---------------------------------------------------------
        if rest() == "_":
            if rest(1) == "_":
                # "Standard separator.  Handled first."
                at += 2
                if _is_digit(rest()):
                    # "Overloading number."
                    evidence.add("overload")
                    at += 1
                    while _is_digit(rest()) or (rest() == "_" and _is_digit(rest(1))):
                        at += 1
                    if rest() == "X":
                        evidence.add("body-nested")
                        at += 1
                        while rest() in ("n", "b"):
                            at += 1
                elif rest() == "_" and rest(1) != "_":
                    # "Special names."
                    for key in _SPECIAL_KEYS:
                        if text.startswith(key, at):
                            at += len(key)
                            out.append(_SPECIAL[key])
                            parts.append(_SPECIAL[key])
                            evidence.add("special")
                            break
                    else:
                        raise DemangleFailure("not a GNAT special name")
                    break
                else:
                    out.append(".")
                    continue
            elif rest(1) in ("B", "E"):
                # "Entry Body or barrier Evaluation."
                evidence.add("entry-body")
                at += 2
                while _is_digit(rest()):
                    at += 1
                if rest() == "s" and rest(1) == "":
                    at += 1
                    break
                raise DemangleFailure("not a GNAT entry body")
            else:
                raise DemangleFailure("not a GNAT separator")

        if rest() == "." and _is_digit(rest(1)):
            # "Nested subprogram."
            evidence.add("nested-subprogram")
            at += 2
            while _is_digit(rest()):
                at += 1

        if rest() == "":
            # "End of mangled name."
            break
        raise DemangleFailure("trailing characters after a complete GNAT name")

    return AdaSymbol("".join(out), parts, evidence, text[at:])
