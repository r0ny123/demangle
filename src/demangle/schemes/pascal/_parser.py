"""Reading Free Pascal's symbol names.

Free Pascal builds a symbol out of `$`-delimited parts in `make_mangledname`
(`compiler/symdef.pas`), and nothing in the toolchain reads one back. So the grammar here
is a transcription of that function and of the three others that feed it, and the
correctness argument is the one Go and Nim use: the parts this splits out, rejoined with
the separators the compiler uses, must reproduce the symbol exactly.

The shape, from the compiler's own comment:

    [<kind> "_$"] <unit or "P$"program> ["$_$" <scope>] ["_$$_" <name and parameters>]

* **kind** is one of nine markers saying the symbol is a table rather than code -- `VMT`,
  `RTTI`, `INIT`, `IID`, `IIDSTR`, `RESSTR`, `WRPR` -- or that it is data rather than a
  routine: `U` for a variable and `TC` for a typed constant.
* **scope** is the classes and records the thing is nested in, joined by `_$_`, and then
  any enclosing procedures. A method's scope ends in a bare `_$_`, which is why
  `TWIDGET_$__$$_AREA` has that run of underscores in it.
* **name and parameters** is the routine name, then `$` and a type per parameter, then
  `$$` and the result type.

**Case does not come back.** Pascal is case-insensitive and the compiler upper-cases
identifiers before it mangles them, so `Add` and `ADD` are one symbol. Nothing can undo
that, and this does not pretend to.

**A long parameter list does not come back either.** Over about 12 characters the
compiler replaces the whole list with `$crc` and a checksum, and over 100 characters it
does the same to a scope with `$CRC`. Both are reported as what they are rather than
guessed at.
"""

import re

__all__ = ["DemangleFailure", "PascalSymbol", "detect", "parse_pascal_symbol"]

#: The nine markers the compiler puts in front of a unit name, and what each one is.
#: Transcribed from `rtti_mangledname`, `tstaticvarsym.mangledname`, `TVMTWriter` and the
#: interface-table writers.
KINDS = {
    "RTTI": "run-time type information for",
    "INIT": "initialisation type information for",
    "VMT": "virtual method table for",
    "IID": "interface identifier for",
    "IIDSTR": "interface identifier string for",
    "RESSTR": "resource string",
    "WRPR": "interface wrapper for",
    "TC": "typed constant",
    "U": "variable",
}

#: Longest first, so `IIDSTR` is not read as `IID` with `STR` left over.
_KIND_PATTERN = re.compile(r"^(" + "|".join(sorted(KINDS, key=len, reverse=True)) + r")_\$(?!\$)")

#: A unit's own initialisation and finalisation sections, which carry no other name.
_SECTION = re.compile(r"^(INIT|FINALIZE)\$_\$([A-Za-z0-9_.]+)$")

#: `_$<unit>$_Ld12`: an assembler-local label, not a declaration at all.
_LABEL = re.compile(r"^_\$([A-Za-z0-9_.]+)\$_(L[a-z][0-9]+)$")

#: What the compiler leaves behind when a name got too long to spell out. It appears in
#: two places: as the whole of an over-long parameter list, and inside a generic type's
#: name, where it is preceded by the parameter count.
_CHECKSUM = re.compile(r"^(?:CRC|crc)[0-9A-Fa-f]{8}(?:\.[A-Za-z0-9_.]+)?$|^[0-9]+$")
_ELIDED_PARAMETERS = re.compile(r"^(?:CRC|crc)[0-9A-Fa-f]{8}$")

#: The compiler's own initialisation and finalisation routines. They take no
#: parameters but are still written with the parameter separator, so `AVL_TREE_$$_init$`
#: is `AVL_TREE.init()` while `MYUNIT_$$_ADD$` is an empty parameter type and refused.
_UNIT_SECTIONS = frozenset({"init", "finalize", "init_implicit", "finalize_implicit"})

#: What the compiler calls each overloadable operator, from `overloaded_names` in
#: `compiler/symtable.pas`. A name of one of these forms is written with a `$` in front
#: so that it stays distinct when the mangled name is lower-cased for a section name --
#: which is also what makes it recognisable here.
OPERATORS = {
    "plus": "+",
    "minus": "-",
    "star": "*",
    "slash": "/",
    "equal": "=",
    "greater": ">",
    "lower": "<",
    "greater_or_equal": ">=",
    "lower_or_equal": "<=",
    "not_equal": "<>",
    "sym_diff": "><",
    "starstar": "**",
    "as": "as",
    "in": "in",
    "is": "is",
    "or": "or",
    "and": "and",
    "div": "div",
    "mod": "mod",
    "not": "not",
    "shl": "shl",
    "shr": "shr",
    "xor": "xor",
    "assign": ":=",
    "explicit": "explicit",
    "enumerator": "enumerator",
    "initialize": "initialize",
    "finalize": "finalize",
    "addref": "addref",
    "copy": "copy",
    "inc": "inc",
    "dec": "dec",
}

#: The other two names the compiler writes with a leading `$`.
CLASS_ROUTINES = {"class_constructor": "class constructor", "class_destructor": "class destructor"}

#: The tables a `RTTI_$` symbol may carry for an enumeration.
_TABLES = {"_o2s": "ordinal-to-string table", "_s2o": "string-to-ordinal table"}

_INDIRECT = "$indirect"


class DemangleFailure(Exception):
    """This name is not one this parser reads."""


class PascalSymbol:
    """One parsed Free Pascal symbol."""

    __slots__ = (
        "elided",
        "indirect",
        "kind",
        "name",
        "parameters",
        "raw",
        "raw_scope",
        "raw_signature",
        "result",
        "scope",
        "text",
        "unit",
        "wrapped",
    )

    def __init__(
        self,
        raw,
        text,
        kind,
        unit="",
        scope=(),
        name="",
        parameters=(),
        result=None,
        indirect=False,
        raw_scope="",
        raw_signature="",
        elided=False,
        wrapped=None,
    ):
        self.raw = raw
        self.text = text
        #: One of `KINDS`, or `routine`, `section`, `label`.
        self.kind = kind
        self.unit = unit
        self.scope = tuple(scope)
        self.name = name
        self.parameters = tuple(parameters)
        self.result = result
        self.indirect = indirect
        #: The scope exactly as it was written. Kept because the split is not reversible:
        #: enclosing classes are joined with `_$_` and enclosing procedures with a bare
        #: `_`, and a method with no enclosing procedure still ends in `_$_`.
        self.raw_scope = raw_scope
        #: True when the compiler replaced the parameter list with a checksum, so the
        #: empty `parameters` means "not recorded" rather than "none".
        self.elided = elided
        #: The name and signature exactly as written, for the same reason as `raw_scope`:
        #: an elided parameter list is a checksum this cannot expand, and dropping it
        #: would make the reading unable to account for the symbol.
        self.raw_signature = raw_signature
        #: For a `WRPR`, the entry index and the method it forwards to, as already-spelled
        #: text. A wrapper's suffix is a whole mangled name rather than a signature, so it
        #: does not fit the other fields.
        self.wrapped = wrapped

    @property
    def qualified(self):
        """`UNIT.CLASS.NAME`, the part a caller usually wants."""
        return ".".join([part for part in (self.unit, *self.scope) if part] + ([self.name] if self.name else []))


def _split_signature(suffix):
    """`NAME` then `$<type>` per parameter then `$$<type>` for the result.

    A generic type is written `NAME$<count>$CRC<hex>`, so a piece that is only a count or
    a checksum belongs to the type in front of it and is not a parameter of its own.
    """
    # A routine written with a leading `$` is an operator or a class
    # constructor/destructor; the `$` is part of its name, not a separator.
    leading = ""
    if suffix.startswith("$"):
        leading, suffix = "$", suffix[1:]
    # Over about twelve characters the compiler replaces the whole parameter list with a
    # checksum, so a name followed by nothing but one is a signature it did not spell.
    at = suffix.find("$")
    if at > 0 and _ELIDED_PARAMETERS.match(suffix[at + 1 :]):
        return leading + suffix[:at], None, None

    pieces = suffix.split("$")
    merged = []
    for piece in pieces:
        if merged and merged[-1] != "" and _CHECKSUM.match(piece):
            merged[-1] += "$" + piece
        else:
            merged.append(piece)
    name, rest = leading + merged[0], merged[1:]
    parameters = []
    result = None
    at = 0
    while at < len(rest):
        # An empty piece is the `$$` that marks the result.
        if rest[at] == "":
            if at + 1 < len(rest):
                result_piece = rest[at + 1]
                if not result_piece:
                    raise DemangleFailure("empty result type")
                if result is not None:
                    raise DemangleFailure("multiple result types")
                result = result_piece
                at += 2
                continue
            if name in _UNIT_SECTIONS:
                # The compiler's own sections end in a lone separator with no
                # parameter behind it; anywhere else that is an empty parameter.
                parameters.append(rest[at])
                at += 1
                continue
            raise DemangleFailure("empty parameter type")
        parameters.append(rest[at])
        at += 1
    return name, parameters, result


def _rejoin(name, parameters, result, elided=None):
    if elided is not None:
        return name + "$" + elided
    out = name
    for parameter in parameters:
        out += "$" + parameter
    if result is not None:
        out += "$$" + result
    return out


def spell_routine_name(name):
    """`$plus` is `operator +`; `$class_constructor` is what it says."""
    if not name.startswith("$"):
        return name
    inner = name[1:]
    if inner in OPERATORS:
        return f"operator {OPERATORS[inner]}"
    if inner in CLASS_ROUTINES:
        return CLASS_ROUTINES[inner]
    return name


def _spell_signature(qualified, parameters, result):
    if parameters is None:
        # The compiler elided the list; saying so beats inventing one.
        return qualified + "(<parameters elided by the compiler>)"
    out = qualified
    if parameters:
        out += "(" + ", ".join(parameters) + ")"
    if result is not None:
        out += ": " + result
    return out


def _parse_wrapper(raw, unit, suffix, indirect):
    """`WRPR`: the thunk that lets a class satisfy an interface.

    Its suffix is not a signature but four parts the compiler joins with `_$_`: the
    class, the interface, which entry of the interface's table this is, and the whole
    mangled name of the method that implements it. So the last part is parsed as a symbol
    in its own right.
    """
    parts = suffix.split("_$_")
    if len(parts) < 4:
        return None
    owner, interface, index, rest = parts[0], parts[1], parts[2], "_$_".join(parts[3:])
    truncated = "$CRC" in rest
    try:
        implementation = parse_pascal_symbol(rest).text
    except DemangleFailure:
        implementation = rest
    tail = " (name truncated by the compiler)" if truncated else ""
    text = f"interface wrapper for {unit}.{owner}.{interface} #{index}: {implementation}{tail}"
    if indirect:
        text += " (indirect reference)"
    return PascalSymbol(
        raw,
        text,
        "WRPR",
        unit=unit,
        scope=(owner, interface),
        name=suffix,
        indirect=indirect,
        raw_signature=suffix,
        wrapped=(index, implementation, tail),
    )


def parse_pascal_symbol(name):
    """Parse `name`, returning a `PascalSymbol`, or raise `DemangleFailure`."""
    found = _LABEL.match(name)
    if found is not None:
        unit, label = found.groups()
        return PascalSymbol(name, f"assembler label {label} in {unit}", "label", unit=unit, name=label)

    found = _SECTION.match(name)
    if found is not None:
        which, unit = found.groups()
        word = "initialisation" if which == "INIT" else "finalisation"
        return PascalSymbol(name, f"{word} of {unit}", "section", unit=unit, name=which)

    indirect = name.endswith(_INDIRECT)
    body = name[: -len(_INDIRECT)] if indirect else name

    table = None
    for ending, description in _TABLES.items():
        if body.endswith(ending):
            table = description
            body = body[: -len(ending)]
            break

    kind = None
    found = _KIND_PATTERN.match(body)
    if found is not None:
        kind = found.group(1)
        body = body[found.end() :]

    at = body.find("_$$_")
    if at < 0:
        raise DemangleFailure("no unit-and-name separator")
    left, suffix = body[:at], body[at + 4 :]
    if not left or not suffix:
        raise DemangleFailure("empty unit or name")

    scope_at = left.find("$_$")
    if scope_at >= 0:
        unit, raw_scope = left[:scope_at], left[scope_at + 3 :]
    else:
        unit, raw_scope = left, ""
    if not unit:
        raise DemangleFailure("empty unit")

    if kind == "WRPR":
        wrapper = _parse_wrapper(name, unit, suffix, indirect)
        if wrapper is None:
            raise DemangleFailure("not a wrapper name")
        return wrapper

    # Enclosing classes are joined with `_$_`; a method with nothing else around it
    # leaves an empty last piece, and anything in that position is instead the chain of
    # enclosing procedures, joined with a bare `_`.
    scope = [piece for piece in raw_scope.split("_$_") if piece] if raw_scope else []

    # A program's own symbols carry `P$` so that a program and a unit of the same name
    # do not collide.
    is_program = unit.startswith("P$")
    spelled_unit = unit[2:] if is_program else unit
    qualified = ".".join([spelled_unit, *scope])

    if kind is None:
        name_part, parameters, result = _split_signature(suffix)
        elided = suffix[len(name_part) + 1 :] if parameters is None else None
        if _rejoin(name_part, parameters, result, elided) != suffix:
            raise DemangleFailure("the signature does not read back")
        text = _spell_signature(f"{qualified}.{spell_routine_name(name_part)}", parameters, result)
        symbol = PascalSymbol(
            name,
            text,
            "routine",
            unit=unit,
            scope=scope,
            name=name_part,
            parameters=parameters if parameters is not None else (),
            result=result,
            indirect=indirect,
            raw_scope=raw_scope,
            raw_signature=suffix,
            elided=parameters is None,
        )
    else:
        symbol = PascalSymbol(
            name,
            f"{KINDS[kind]} {qualified}.{suffix}",
            kind,
            unit=unit,
            scope=scope,
            name=suffix,
            indirect=indirect,
            raw_scope=raw_scope,
            raw_signature=suffix,
        )

    if table is not None:
        symbol.text = f"{table} for {symbol.text}"
    if is_program:
        symbol.text = "program " + symbol.text
    if indirect:
        symbol.text += " (indirect reference)"
    return symbol


def detect(name):
    """Whether `name` is one this reads.

    The `$` in a Free Pascal symbol is not legal in a C identifier on most targets, and
    the separators are distinctive enough that the shape alone is a fair test -- but this
    parses anyway, because the parse is what establishes that the parts rejoin to the
    input.
    """
    if not name or "$" not in name:
        return False
    try:
        parse_pascal_symbol(name)
    except DemangleFailure:
        return False
    return True
