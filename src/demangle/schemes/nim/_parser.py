"""Reading Nim's C symbol names.

Nim compiles to C, so what reaches the linker is whatever the code generator chose to
call a thing. There is no reference demangler -- nothing in the Nim toolchain reads one of
these back -- so the grammar here is a transcription of the compiler's own
`compiler/mangleutils.nim` and `compiler/modulegraphs.nim`, and the correctness argument
is the same shape as Go's: **re-mangling what we read must reproduce the bytes the
compiler wrote**, checked over every symbol.

A routine or a global is written

    <mangled identifier> "__" <mangled module path> "_" ["u"] <id>

where the `u` appeared in Nim 2 and marks the mangling of the module path as well: Nim 1
encodes digits in a path and Nim 2 leaves them alone, so the same `pureZmd53` is
`pure/md5` under one and `pure/md53` under the other. The `u` is the only thing that says
which, which is why it is read as a version marker rather than skipped.

**Two things are not recoverable, and both are the compiler's own doing.**

`mangle` drops an underscore that precedes a digit, because it reserves `_<digit>` for its
own disambiguation: `len0_16` and `len016` are the same symbol. Over every routine name
declared in either shipped standard library -- 5,946 of them -- that is the *only* cause
of a name this cannot return exactly, and it accounts for all 8 of them.

The escaping is also not injective. `$` becomes `dollar` and `<` becomes `lt`, so
`result` -- which contains `lt` -- could be read as `resu<`. What settles it is that Nim
names have a grammar: a routine is an identifier, or a name made *entirely* of operator
characters, or an identifier with `=` after it (a setter), or one of the compiler's own
`:tmp`/`=destroy` forms. Reading `result` as `resu<` is not any of those. The decoder
proposes the readings the grammar allows, in that order, and takes the first that
re-mangles to the input.
"""

import re

__all__ = ["DemangleFailure", "NimSymbol", "detect", "mangle", "mangle_module", "parse_nim_symbol"]

#: Characters `mangle` spells as words rather than escaping. From the compiler's own
#: table; the order they are tried in matters, so the reverse map is built once.
SPECIALS = {
    "dollar": "$",
    "percent": "%",
    "amp": "&",
    "roof": "^",
    "emark": "!",
    "qmark": "?",
    "star": "*",
    "plus": "+",
    "minus": "-",
    "slash": "/",
    "backslash": "\\",
    "eq": "=",
    "lt": "<",
    "gt": ">",
    "tilde": "~",
    "colon": ":",
    "dot": ".",
    "at": "@",
    "bar": "|",
}
_BY_CHARACTER = {character: word for word, character in SPECIALS.items()}
#: Longest first: `backslash` contains `slash`, and `amp` would swallow the front of one.
_BY_LENGTH = sorted(SPECIALS, key=len, reverse=True)

_HEX = frozenset("0123456789ABCDEF")

#: The characters a Nim operator name is made of. A name mixing these with letters is not
#: one, which is what rules out reading `result` as `resu<`.
OPERATOR_CHARACTERS = frozenset("=+-*/<>@$~&%|!?^.:\\[]{}")

#: A Nim identifier: a letter, then letters, digits and underscores. Backticks appear in
#: the compiler's `x`gensym12` names, which are identifiers as far as this is concerned.
_IDENTIFIER = re.compile(r"^[^\W\d_][\w`]*$", re.UNICODE)

#: `<identifier> "__" <module> "_" ["u"] <id>`. The module part is lazy so that the split
#: falls at the *last* `__`, which is where it belongs: an identifier may contain one.
_SYMBOL = re.compile(r"^(.+)__([A-Za-z0-9]*?)_(u?)([0-9]+)$")

#: `uniqueModuleName` emits only these. No underscore and no upper case but `Z` and `O`,
#: which is what makes the shape narrow enough to detect on.
_MODULE_CHARACTERS = re.compile(r"^[a-z0-9ZO]+$")

#: `ty<Kind>` optionally `_<name>`, then the signature hash. The hash is a digest and
#: does not come back.
_TYPE_NAME = re.compile(r"^ty([A-Z][A-Za-z]*?)(?:_(.+?))?__([A-Za-z0-9_]+)$")
_TYPE_INFO = re.compile(r"^NTI(v2)?(.*?)__([A-Za-z0-9_]+)_$")
_MARKER = re.compile(r"^Marker_(.+)$")
_MODULE_TEMPORARY = re.compile(r"^TM__?([A-Za-z0-9_]+)_([0-9]+(?:\.[0-9]+)?)$")

#: Characters `uniqueModuleName` writes as a decimal code and a path plausibly contains.
#: Under Nim 2 a digit run may equally be part of the path -- `pure/base64` is written
#: `pureZbase64` -- so a two-digit run is only read as a code when the character it names
#: is one that occurs in a module path. Measured: across both shipped standard libraries,
#: the only character ever encoded under Nim 2 is `_`.
_PLAUSIBLE_IN_A_PATH = frozenset(range(65, 91)) | {ord(c) for c in "-_+.~#$"}


class DemangleFailure(Exception):
    """This name is not one this parser reads."""


class NimSymbol:
    """One parsed Nim symbol."""

    __slots__ = ("kind", "module", "name", "raw", "text")

    def __init__(self, raw, text, kind, name="", module=""):
        self.raw = raw
        self.text = text
        #: `routine`, `type`, `type-info`, `marker` or `temporary`.
        self.kind = kind
        self.name = name
        self.module = module


def mangle(name):
    """`mangleutils.mangle`, transcribed.

    Kept so that a reading can be checked by re-mangling it, which is the whole
    correctness argument here.
    """
    out = []
    start = 0
    escaped = False
    if name and name[0].isdigit():
        out.append("X" + name[0])
        start = 1
    for at in range(start, len(name)):
        character = name[at]
        if character.isascii() and character.isalnum():
            out.append(character)
        elif character == "_":
            # Dropped before a digit: the compiler reserves `_<digit>` for its own
            # disambiguation, so `len0_16` and `len016` become the same symbol.
            if not (0 < at < len(name) - 1 and name[at + 1].isdigit()):
                out.append(character)
        elif character in _BY_CHARACTER:
            out.append(_BY_CHARACTER[character])
            escaped = True
        else:
            out.extend(f"X{byte:02X}" for byte in character.encode("utf-8"))
            escaped = True
    if escaped:
        out.append("_")
    return "".join(out)


def _decode_hex(body):
    """Undo the `X<hex><hex>` escapes, which are what carries a non-ASCII identifier."""
    out = []
    at = 0
    found = False
    while at < len(body):
        if body[at] == "X" and at + 3 <= len(body) and body[at + 1] in _HEX and body[at + 2] in _HEX:
            out.append(bytes([int(body[at + 1 : at + 3], 16)]))
            at += 3
            found = True
        else:
            out.append(body[at].encode())
            at += 1
    try:
        return b"".join(out).decode("utf-8"), found
    except UnicodeDecodeError:
        return None, found


def _as_operator(text):
    """The whole name as operator characters, or `None` if a letter gets in the way."""
    out = []
    at = 0
    while at < len(text):
        for word in _BY_LENGTH:
            if text.startswith(word, at):
                out.append(SPECIALS[word])
                at += len(word)
                break
        else:
            if text[at] in OPERATOR_CHARACTERS or not text[at].isascii():
                out.append(text[at])
                at += 1
            else:
                return None
    return "".join(out)


def _as_compiler_name(text):
    """`:tmp`, `=destroy`: the only place an operator character introduces a name."""
    for word, character in (("colon", ":"), ("eq", "=")):
        if text.startswith(word) and _IDENTIFIER.match(text[len(word) :]):
            return character + text[len(word) :]
    return None


def _as_setter(text):
    """`name=`: the only place one follows a name."""
    if text.endswith("eq") and _IDENTIFIER.match(text[:-2]):
        return text[:-2] + "="
    return None


def unmangle(text):
    """The name `text` was mangled from, or `None` if no reading re-mangles to it.

    The readings are proposed in the order Nim's own name grammar makes likely, and the
    re-mangle is what accepts one. A trailing `_` usually marks an escape, but a name
    that *is* `_` has one too, which is why the undecoded text is a candidate as well.
    """
    candidates = []
    if text.endswith("_"):
        decoded, had_hex = _decode_hex(text[:-1])
        if decoded is not None:
            operator = _as_operator(decoded)
            compiler_name = _as_compiler_name(decoded)
            setter = _as_setter(decoded)
            if operator is not None:
                candidates.append(operator)
            if had_hex:
                # The escape that set the marker is accounted for; leave the words alone,
                # or `result` comes back as `resu<`.
                candidates.append(decoded)
            if compiler_name is not None:
                candidates.append(compiler_name)
            if setter is not None:
                candidates.append(setter)
            if not had_hex:
                candidates.append(decoded)
    candidates.append(text)
    for candidate in candidates:
        if mangle(candidate) == text:
            return candidate
    return None


def mangle_module(path, nim2):
    """`modulegraphs.uniqueModuleName`, transcribed.

    Nim 2 leaves digits alone; Nim 1 writes them as codes like everything else that is
    not a lower-case letter.
    """
    out = []
    for character in path:
        if "a" <= character <= "z" or (nim2 and character.isascii() and character.isdigit()):
            out.append(character)
        elif character in "/\\":
            out.append("Z")
        elif character == ".":
            out.append("O")
        else:
            out.append(str(ord(character)))
    return "".join(out)


def unmangle_module(text, nim2):
    """Undo `uniqueModuleName`.

    Under Nim 1 every digit is part of a code, so this is exact. Under Nim 2 a digit may
    be either, and the tie is broken towards what a real path holds -- see
    `_PLAUSIBLE_IN_A_PATH`. Verified over every module in both shipped standard
    libraries: 304 of 304 and 310 of 310.
    """
    out = []
    at = 0
    while at < len(text):
        character = text[at]
        if character == "Z":
            out.append("/")
            at += 1
            continue
        if character == "O":
            out.append(".")
            at += 1
            continue
        if character.isdigit():
            # Only `{`, `|`, `}` and `~` need three digits; every other encoded character
            # is below 100, so two digits are the common case and a three-digit read is
            # only correct in that narrow range.
            if at + 3 <= len(text) and text[at : at + 3].isdigit() and 123 <= int(text[at : at + 3]) <= 126:
                out.append(chr(int(text[at : at + 3])))
                at += 3
                continue
            if at + 2 <= len(text) and text[at : at + 2].isdigit():
                value = int(text[at : at + 2])
                # Under Nim 1 everything but a lower-case letter is a code, so any
                # two-digit run is one. Under Nim 2 a digit may be the path's own.
                in_range = 32 <= value <= 96 and not ("a" <= chr(value) <= "z")
                if value in _PLAUSIBLE_IN_A_PATH if nim2 else in_range:
                    out.append(chr(value))
                    at += 2
                    continue
            if nim2:
                out.append(character)
                at += 1
                continue
        out.append(character)
        at += 1
    return "".join(out)


def _routine(name):
    """`<name>__<module>_[u]<id>`, which is 86% of what a Nim binary defines."""
    found = _SYMBOL.match(name)
    if found is None:
        return None
    identifier, module, marker, _identity = found.groups()
    if not module or not _MODULE_CHARACTERS.match(module):
        return None
    nim2 = marker == "u"
    spelled = unmangle(identifier)
    if spelled is None:
        # No reading re-mangles to what is there, so this is not a Nim symbol.
        return None
    path = unmangle_module(module, nim2)
    if mangle_module(path, nim2) != module:
        return None
    return NimSymbol(name, f"{path}.{spelled}", "routine", name=spelled, module=path)


def _type_name(name):
    """`ty<Kind>[_<name>]__<signature hash>`. The hash is a digest and does not return."""
    found = _TYPE_NAME.match(name)
    if found is None:
        return None
    kind, spelled, _hash = found.groups()
    if spelled is not None:
        spelled = unmangle(spelled)
        if spelled is None:
            return None
        return NimSymbol(name, spelled, "type", name=spelled)
    return NimSymbol(name, kind[0].lower() + kind[1:], "type", name=kind)


def _type_info(name):
    """`NTI<name>__<hash>_`.

    The name comes from the compiler's `typeToC`, which lower-cases and drops spaces and
    says of itself that "the result doesn't have to be unique" -- so it is a readable
    label, not the type's spelling, and this does not pretend otherwise.
    """
    found = _TYPE_INFO.match(name)
    if found is None:
        return None
    _version, label, _hash = found.groups()
    if not label:
        return None
    return NimSymbol(name, f"type information for {label}", "type-info", name=label)


def _marker(name):
    """`Marker_<type name>`: the traversal function the collector calls for a type."""
    found = _MARKER.match(name)
    if found is None:
        return None
    inner = _type_name(found.group(1))
    if inner is None:
        return None
    return NimSymbol(name, f"garbage-collector marker for {inner.text}", "marker", name=inner.name)


def _module_temporary(name):
    """`TM__<hash>_<n>`: a module-level temporary. The hash names the module and is a
    digest, so which module it is does not come back."""
    found = _MODULE_TEMPORARY.match(name)
    if found is None:
        return None
    return NimSymbol(name, f"module temporary #{found.group(2)}", "temporary")


_READERS = (_routine, _type_info, _marker, _type_name, _module_temporary)


def parse_nim_symbol(name):
    """Parse `name`, returning a `NimSymbol`, or raise `DemangleFailure`."""
    for reader in _READERS:
        found = reader(name)
        if found is not None:
            return found
    raise DemangleFailure("not a Nim symbol")


#: What a name must begin with to be one of the compiler-generated forms. Everything
#: else this reads is a `<name>__<module>_[u]<id>`.
_COMPILER_PREFIXES = ("NTI", "Marker_", "TM_", "ty")

#: Prefixes that belong to another language, and that this scheme must decline whatever
#: else the name looks like.
#:
#: Nim has no marker of its own -- a Nim symbol is an ordinary C identifier -- so this
#: scheme recognises names by shape, and `<name>__<module>_<id>` is a shape other
#: compilers produce too. OCaml's is the one that collides: it writes
#: `caml<Module>__<name>_<id>`, which splits at the last `__` into a name of
#: `camlStdlib__Int`, a module of `compare` (all lower case, so it passes the module
#: test) and an id of 296 -- and re-mangles to exactly the symbol it came from, so even
#: the re-mangling property this scheme relies on says yes. It read
#: `camlStdlib__Int__compare_296` as `compare.camlStdlib__Int`.
#:
#: Every symbol the OCaml compiler emits carries this prefix, and no Nim symbol does, so
#: declining it costs nothing and settles the collision the shape cannot. A caller who
#: knows the binary is Nim can still pass `language="nim"` and get the whole scheme.
_FOREIGN_PREFIXES = ("caml",)

#: How every `<name>__<module>_[u]<id>` ends. A *necessary* condition of `_SYMBOL` -- the
#: same `_ (u?) ([0-9]+) $` that pattern requires -- so screening on it turns away only
#: names `_routine` would have refused anyway. It is worth testing separately because
#: `_SYMBOL` opens with a greedy `(.+)__`, which walks back through every `__` in the
#: name before it can fail; `std::__cxx11` puts one in a large share of a C++ binary.
_ROUTINE_TAIL = re.compile(r"_u?[0-9]+$")


def detect(name):
    """Whether `name` is one this reads.

    Deliberately the whole parse rather than a shape test. A Nim symbol is an ordinary C
    identifier -- there is no prefix to key on -- so the only honest test is whether a
    reading exists that re-mangles to it, and that is what `parse_nim_symbol` establishes.

    What comes before that parse is a *necessary* condition rather than a guess, which is
    what makes it safe to screen on: a name that is not one of the compiler's own forms
    can only be `<name>__<module>_[u]<id>`, and `<id>` is a run of decimal digits at the
    very end. This plugin declares no first character -- a Nim symbol is an ordinary C
    identifier -- so it is offered every symbol in a binary, and running five anchored
    regular expressions over each of them made it four times the cost of any other
    scheme's detection. `__` alone does not screen -- `std::__cxx11` has one, and so does
    a fifth of the shipped libstdc++ -- and neither does a trailing digit, which 95% of
    those symbols also have. The tail does: none of them survives it.

    Both halves of that screen are necessary, so the order between them is free to be
    chosen on cost -- and it matters more than it looks. `"__" in name` is one C-level
    scan; `_ROUTINE_TAIL.search` is a regular expression whose `$` anchor does not stop
    `search` trying every position first. Testing the regex first spent it on every name
    that has no `__` at all, which is most of them: 61% of the shipped libstdc++, and
    every ordinary C identifier. Measured, with the verdict identical on every name of
    both corpora:

        synthetic non-symbols   0.484us -> 0.161us   (3.0x)
        real libstdc++ symbols  0.501us -> 0.342us   (1.5x)

    It wins on the real symbols too, despite 39% of them carrying a `__`, because the
    other 61% now stop at the membership test. This scheme declares no first character,
    so it is offered *every* symbol in a binary and was half the cost of detection across
    all six schemes that see a lower-case name.

    `_FOREIGN_PREFIXES` is the other half, and it is a refusal rather than a screen: the
    shape this scheme reads is one another compiler also produces, and where a name
    carries that compiler's marker the marker wins. See the note there.
    """
    if not name:
        return False
    if name.startswith(_FOREIGN_PREFIXES):
        return False
    if not name.startswith(_COMPILER_PREFIXES) and not ("__" in name and _ROUTINE_TAIL.search(name)):
        return False
    try:
        parse_nim_symbol(name)
    except DemangleFailure:
        return False
    return True
