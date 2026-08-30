"""Reading Borland/Embarcadero mangled names.

Transcribed from `unmangle.c` in the C++Builder RTL -- the unmangler TDUMP, the linker
and the debugger all run -- as ported with its comments intact. The control flow is that
file's, not a reconstruction from examples.

A name is `@`-delimited qualifiers, then optionally `$` and a type. Calling conventions
are extra `q` letters after the function marker: `qqr` is `__fastcall` (Delphi's
`register`), `qqs` is `__stdcall`, `qqc` is `__cdecl`. Types are single letters, or a
decimal length followed by an `@`-qualified name. `tN` is a back-reference to an earlier
parameter, 1-based in base 36.

Microsoft's 32-bit `__fastcall` C decoration is `@name@bytes` with a decimal count and
no `$`. It is refused here, because it is not this scheme.
"""

from __future__ import annotations

import re
import string

from ...core.errors import LimitExceeded
from ...core.limits import DEFAULT_LIMITS

__all__ = ["DelphiSymbol", "DemangleFailure", "detect", "parse_delphi_symbol"]

QUALIFIER = "@"
ARGLIST = "$"
TMPLCODE = "%"

DIGITS = frozenset(string.digits)

#: Single-character code sets, every one a frozenset rather than a string. `peek` and
#: `advance` answer `""` at end of input, and `"" in "xw"` is True for a *string* -- an
#: empty substring is a substring of everything. Written that way, the qualifier loop in
#: `copy_args` never terminated on a name whose argument list ended in `x` or `w`, and
#: the indirection test read the end of the input as a pointer. `"" in frozenset("xw")`
#: is False, which is the question these are actually asking.
_CV_CODES = frozenset("xw")
_INDIRECTION_CODES = frozenset("Mrhp")
_CLOSURE_CODES = frozenset("fn")
_TPDSC_CODES = frozenset("pt")
_TEMPLATE_VALUE_CODES = frozenset("jge")
_THUNK_CODES = frozenset("vd")
_STRUCTOR_CODES = frozenset("cd")
_FUNCTION_CODES = frozenset("qxw")
#: What `int(digit, 36)` accepts. A back-reference index is one base-36 digit, and any
#: other byte there is a malformed name, not a `ValueError` out of the parser.
_BASE36 = frozenset(string.digits + string.ascii_letters)

KIND_FUNCTION = "function"
KIND_CONSTRUCTOR = "constructor"
KIND_DESTRUCTOR = "destructor"
KIND_OPERATOR = "operator"
KIND_CONVERSION = "conversion"
KIND_DATA = "data"
KIND_TPDSC = "tpdsc"
KIND_VTABLE = "vtable"
KIND_THUNK = "thunk"
KIND_LINKPROC = "linkproc"

#: Special names after `$b`, from `unmangle.c`. Class constructor/destructor are written
#: the way IDA spells them, after stripping the `operator ` the unmangler first emits.
OPERATORS = {
    "add": "+",
    "adr": "&",
    "and": "&",
    "asg": "=",
    "land": "&&",
    "lor": "||",
    "call": "()",
    "cmp": "~",
    "fnc": "()",
    "dec": "--",
    "div": "/",
    "eql": "==",
    "geq": ">=",
    "gtr": ">",
    "inc": "++",
    "ind": "*",
    "leq": "<=",
    "lsh": "<<",
    "lss": "<",
    "mod": "%",
    "mul": "*",
    "neq": "!=",
    "new": "new",
    "not": "!",
    "or": "|",
    "rand": "&=",
    "rdiv": "/=",
    "rlsh": "<<=",
    "rmin": "-=",
    "rmod": "%=",
    "rmul": "*=",
    "ror": "|=",
    "rplu": "+=",
    "rrsh": ">>=",
    "rsh": ">>",
    "rxor": "^=",
    "subs": "[]",
    "sub": "-",
    "xor": "^",
    "arow": "->",
    "nwa": "new[]",
    "dele": "delete",
    "dla": "delete[]",
    "cctr": "`class constructor`",
    "cdtr": "`class destructor`",
}

CALLING = {
    "c": "__cdecl ",
    "p": "__pascal ",
    "r": "__fastcall ",
    "f": "__fortran ",
    "s": "__stdcall ",
    "y": "__syscall ",
    "i": "__interrupt ",
}

DELPHI4_TEMPLATE = re.compile(r"^(Set|DynamicArray|SmallString|DelphiInterface)\$")

#: `@name@12` is MSVC 32-bit fastcall, not a Delphi export.
_MSVC_FASTCALL = re.compile(r"^@[^$]*@\d+$")
#: `@@InitExe` is a linker procedure with no type encoding. `@@bug@@x` is not.
_LINKPROC_BARE = re.compile(r"^@@[A-Za-z_][A-Za-z0-9_]*$")

_TABLE_KIND = {
    "FL": "frndl",
    "CH": "chtbl",
    "DC": "odtbl",
    "TL": "thrwl",
    "EC": "ectbl",
}


class DemangleFailure(Exception):
    """This name is not one this parser reads."""


class DelphiSymbol:
    """One parsed Delphi/C++Builder symbol."""

    __slots__ = ("kind", "raw", "text")

    def __init__(self, raw, text, kind):
        self.raw = raw
        self.text = text
        self.kind = kind


class _Parser:
    """Cursor plus output buffer; methods follow `unmangle.c` names."""

    __slots__ = (
        "adjust_quals",
        "base_end",
        "base_name",
        "buf",
        "depth",
        "kind",
        "length",
        "limits",
        "mangled",
        "namebase",
        "pos",
        "prevqual",
        "qualend",
        "savechar",
        "set_qual",
        "src",
        "vtbl_flags",
    )

    def __init__(self, src, mangled, limits):
        self.src = src
        self.mangled = mangled
        self.limits = limits
        self.length = len(src)
        self.pos = 0
        self.depth = 0
        self.buf = ""
        self.kind = ""
        self.savechar = ""
        self.set_qual = True
        self.adjust_quals = False
        self.namebase = 0
        self.qualend = None
        self.prevqual = None
        self.base_name = None
        self.base_end = None
        self.vtbl_flags = []

    def _push(self):
        self.depth += 1
        if self.depth > self.limits.max_depth:
            raise LimitExceeded(self.mangled, "recursion depth", self.limits.max_depth)

    def _pop(self):
        self.depth -= 1

    def peek(self):
        return self.src[self.pos] if self.pos < self.length else ""

    def take(self):
        if self.pos >= self.length:
            raise DemangleFailure("truncated")
        char = self.src[self.pos]
        self.pos += 1
        return char

    def advance(self):
        if self.pos < self.length:
            self.pos += 1
        return self.peek()

    def expect(self, char):
        if self.peek() != char:
            raise DemangleFailure(f"expected {char!r}")
        return self.take()

    def append(self, text):
        self.buf += text

    def _shift(self, at, delta):
        if delta == 0 or not self.adjust_quals:
            return
        if self.namebase is not None and self.namebase >= at:
            self.namebase += delta
        if self.qualend is not None and self.qualend >= at:
            self.qualend += delta
        if self.prevqual is not None and self.prevqual >= at:
            self.prevqual += delta
        if self.base_name is not None and self.base_name >= at:
            self.base_name += delta
        if self.base_end is not None and self.base_end >= at:
            self.base_end += delta

    def insert_at(self, index, text):
        if not text:
            return
        self.buf = self.buf[:index] + text + self.buf[index:]
        self._shift(index, len(text))

    def copy_until(self, *stops):
        start = self.pos
        while self.pos < self.length and self.src[self.pos] not in stops:
            self.pos += 1
        chunk = self.src[start : self.pos]
        self.buf += chunk
        return chunk

    def copy_op(self, encoded):
        self.buf += OPERATORS.get(encoded, f"?{encoded}?")

    def copy_return_type(self, start, callconv, regconv, process_return):
        mark = len(self.buf)
        if process_return:
            self.copy_type(len(self.buf), arglvl=False)
            self.buf += " "
        if callconv:
            self.buf += callconv
        if regconv:
            self.buf += regconv
        inserted = self.buf[mark:]
        self.buf = self.buf[:mark]
        self.insert_at(start, inserted)

    def copy_type(self, start, arglvl):
        self._push()
        try:
            self._copy_type(start, arglvl)
        finally:
            self._pop()

    def _copy_type(self, start, arglvl):
        is_const = is_volatile = is_signed = is_unsigned = False
        char = self.peek()
        while True:
            if char == "u":
                is_unsigned = True
            elif char == "z":
                is_signed = True
            elif char == "x":
                is_const = True
            elif char == "w":
                is_volatile = True
            elif char == "y":
                char = self.advance()
                if char not in _CLOSURE_CODES:
                    raise DemangleFailure("bad closure")
                self.buf += "__closure"
            else:
                break
            char = self.advance()

        if char in DIGITS:
            length = 0
            while char in DIGITS:
                length = length * 10 + (ord(char) - 48)
                char = self.advance()
            if self.pos + length > self.length:
                raise DemangleFailure("truncated class name")
            inner = self.src[self.pos : self.pos + length]
            saved_src, saved_pos, saved_len = self.src, self.pos, self.length
            self.src, self.pos, self.length = inner, 0, len(inner)
            self.copy_name(tmplname=False)
            self.src, self.pos, self.length = saved_src, saved_pos + length, saved_len
            return

        self.savechar = char
        member_class = ""
        if char == "M":
            name_at = len(self.buf)
            self.advance()
            self.copy_type(len(self.buf), arglvl=False)
            member_class = self.buf[name_at:]
            self.buf = self.buf[:name_at]

        tname = None
        if char == "v":
            tname = "void"
        elif char == "c":
            tname = "char"
        elif char == "b":
            tname = "wchar_t"
        elif char == "s":
            tname = "short"
        elif char == "i":
            tname = "int"
        elif char == "l":
            tname = "long"
        elif char == "f":
            tname = "float"
        elif char == "d":
            tname = "double"
        elif char == "g":
            tname = "long double"
        elif char == "j":
            tname = "long long"
        elif char == "o":
            tname = "bool"
        elif char == "e":
            tname = "..."
        elif char == "C":
            wide = self.advance()
            if wide == "s":
                tname = "char16_t"
            elif wide == "i":
                tname = "char32_t"
            else:
                raise DemangleFailure("unknown wide char")
        elif char in _INDIRECTION_CODES:
            if self.savechar == "M":
                inner = self.peek()
                if inner == "x":
                    is_const = True
                    self.advance()
                elif inner == "w":
                    is_volatile = True
                    self.advance()
            else:
                self.advance()
            if self.peek() == "q":
                self.buf += "("
                if self.savechar == "M":
                    self.buf += member_class + "::"
                self.buf += "*)"
                self.savechar = self.peek()
            saved = self.savechar
            self.copy_type(start, arglvl=False)
            self.savechar = saved
            if self.savechar == "r":
                self.buf += "&"
            elif self.savechar == "h":
                self.buf += "&&"
            elif self.savechar == "p":
                self.buf += " *"
            elif self.savechar == "M":
                self.buf += " " + member_class + "::*"
        elif char == "a":
            dims = []
            while True:
                char = self.advance()
                dims.append("[")
                if char == "0":
                    char = self.advance()
                while char and char != "$":
                    dims.append(char)
                    char = self.advance()
                if char != "$":
                    raise DemangleFailure("truncated array")
                char = self.advance()
                dims.append("]")
                if char != "a":
                    break
            self.copy_type(len(self.buf), arglvl=False)
            self.buf += "".join(dims)
        elif char == "q":
            callconv = ""
            regconv = ""
            while self.advance() == "q":
                code = self.advance()
                if code == "g":
                    regconv = "__saveregs "
                else:
                    callconv = CALLING.get(code, "")
            saved_adj = self.adjust_quals
            self.adjust_quals = False
            self.buf += "("
            self.copy_args("$", tmplargs=False)
            self.buf += ")"
            self.adjust_quals = saved_adj
            hasret = self.peek() == "$"
            if hasret:
                self.advance()
            if hasret or callconv or regconv:
                self.copy_return_type(start, callconv, regconv, hasret)
        elif char in (ARGLIST, TMPLCODE) or not char:
            # `$` and `%` terminate an argument list; they are not types. Treating them
            # as a no-op left the cursor unmoved, so `copy_args` called us forever.
            raise DemangleFailure("unknown type")
        else:
            raise DemangleFailure(f"unknown type {char!r}")

        if tname is not None:
            if is_const:
                self.buf += "const "
            if is_volatile:
                self.buf += "volatile "
            if is_signed:
                self.buf += "signed "
            if is_unsigned:
                self.buf += "unsigned "
            if not arglvl or self.savechar != "v":
                self.buf += tname
            self.advance()
        else:
            if is_const:
                self.buf += " const"
            if is_volatile:
                self.buf += " volatile"

    def copy_args(self, end, tmplargs):
        first = True
        table = []
        char = self.peek()
        while char and char != end:
            if first:
                first = False
            else:
                self.buf += ", "
            begin = self.pos
            start = len(self.buf)
            table.append([start, 0])
            if len(table) > self.limits.max_substitutions:
                raise LimitExceeded(self.mangled, "substitution", self.limits.max_substitutions)
            scanned = False
            while char in _CV_CODES:
                self.buf += "const " if char == "x" else "volatile "
                scanned = True
                char = self.advance()
            if scanned and char != "t":
                self.pos = begin
            if char == "t":
                self.advance()
                digit = self.peek()
                if digit not in _BASE36:
                    raise DemangleFailure("bad back-reference")
                index = int(digit, 36) - 1
                self.advance()
                if index < 0 or index >= len(table) - 1:
                    raise DemangleFailure("back-reference out of range")
                ref_start, ref_len = table[index]
                self.buf += self.buf[ref_start : ref_start + ref_len]
            else:
                before = self.pos
                self.copy_type(len(self.buf), arglvl=not tmplargs)
                if self.pos == before:
                    raise DemangleFailure("unknown type")
            table[-1][1] = len(self.buf) - table[-1][0]
            char = self.peek()
            if tmplargs and char == "$":
                termchar = ""
                self.buf = self.buf[:start]
                char = self.advance()
                self.advance()
                while True:
                    if char == "T":
                        self.buf += "<type "
                        termchar = ">"
                        char = "i"
                        continue
                    if char == "i":
                        if self.src[begin : begin + 5] == "4bool":
                            self.buf += "false" if self.peek() == "0" else "true"
                            self.advance()
                            break
                        char = "j"
                        continue
                    if char in _TEMPLATE_VALUE_CODES:
                        self.copy_until("$", TMPLCODE)
                        self.buf += termchar
                        break
                    if char == "m":
                        self.copy_until("$")
                        self.buf += "::*"
                        self.copy_until("$", TMPLCODE)
                        break
                    raise DemangleFailure("unknown template arg kind")
                if self.peek() != "$":
                    raise DemangleFailure("truncated template arg")
                char = self.advance()

    def copy_delphi4args(self, end):
        first = True
        char = self.peek()
        while char and char != end:
            if first:
                first = False
            else:
                self.buf += ", "
            begin = self.pos
            char = self.take()
            while True:
                if char == "t":
                    self.copy_type(len(self.buf), arglvl=False)
                    break
                if char == "T":
                    self.buf += "<type "
                    char = "i"
                    continue
                if char == "i":
                    if self.src[begin : begin + 5] == "4bool":
                        self.buf += "false" if self.peek() == "0" else "true"
                        self.advance()
                        break
                    char = "j"
                    continue
                if char in _TEMPLATE_VALUE_CODES:
                    type_at = len(self.buf)
                    self.copy_type(len(self.buf), arglvl=False)
                    self.buf = self.buf[:type_at]
                    self.expect("$")
                    self.copy_until("$", TMPLCODE)
                    break
                if char == "m":
                    type_at = len(self.buf)
                    self.copy_type(len(self.buf), arglvl=False)
                    self.buf = self.buf[:type_at]
                    self.expect("$")
                    self.copy_until("$")
                    self.buf += "::*"
                    self.copy_until("$", TMPLCODE)
                    break
                raise DemangleFailure("unknown Delphi template arg")
            char = self.peek()
            if char != end:
                if char != "$":
                    raise DemangleFailure("bad Delphi template arg separator")
                char = self.advance()

    def copy_tmpl_args(self):
        is_delphi4 = self.peek() in "SD" and DELPHI4_TEMPLATE.match(self.src[self.pos :])
        self.copy_name(tmplname=True)
        self.expect(ARGLIST)
        if self.buf.endswith("<"):
            self.buf += " "
        self.buf += "<"
        saved = self.set_qual
        self.set_qual = False
        if is_delphi4:
            self.copy_delphi4args(TMPLCODE)
        else:
            self.copy_args(TMPLCODE, tmplargs=True)
        self.set_qual = saved
        if self.buf.endswith(">"):
            self.buf += " "
        self.buf += ">"
        self.expect(TMPLCODE)

    def _thunk_field(self):
        chars = []
        while True:
            char = self.advance()
            if char == "$":
                break
            if not char:
                raise DemangleFailure("truncated thunk")
            chars.append(char)
        return "".join(chars)

    def copy_name(self, tmplname):
        self._push()
        try:
            self._copy_name(tmplname)
        finally:
            self._pop()

    def _copy_name(self, tmplname):
        while True:
            if self.set_qual:
                self.base_name = len(self.buf)
            char = self.peek()
            if char in DIGITS:
                flags = ord(char) - 48 + 1
                if flags & 1:
                    self.vtbl_flags.append("huge")
                if flags & 2:
                    self.vtbl_flags.append("fastthis")
                if flags & 4:
                    self.vtbl_flags.append("rtti")
                self.kind = KIND_VTABLE
                char = self.advance()
                if char not in ("", "$"):
                    raise DemangleFailure("bad vtable flags")
            if char == "#":
                char = self.advance()
                if char == "$":
                    if self.advance() != "c" or self.advance() != "f" or self.advance() != "$" or self.advance() != "@":
                        raise DemangleFailure("bad virdef flag")
                    self.buf += "__vdflg__ "
                    self.advance()
                    self.copy_name(False)
                    return
                return
            if char == QUALIFIER:
                self.advance()
                self.buf += "__linkproc__ "
                # `System::__linkproc__ __fastcall AbstractError()` is what the
                # unmangler prints: the convention is inserted at `namebase`, and the
                # marker is part of the qualification in front of it, so the base moves
                # past it. Left where `finish` set it, the convention landed in front of
                # the whole name on all 163 `__linkproc__` exports in a TDUMP dump.
                self.namebase = len(self.buf)
                self.copy_name(False)
                self.kind = KIND_LINKPROC
                return
            if char == TMPLCODE:
                self.advance()
                self.copy_tmpl_args()
            elif char == ARGLIST:
                if tmplname:
                    return
                char = self.advance()
                if char == "x":
                    char = self.advance()
                    if char in _TPDSC_CODES:
                        if self.advance() != ARGLIST:
                            raise DemangleFailure("bad type descriptor")
                        self.advance()
                        self.buf += "__tpdsc__ "
                        self.copy_type(len(self.buf), arglvl=False)
                        self.kind = KIND_TPDSC
                        return
                    raise DemangleFailure("bad type descriptor")
                if char == "b":
                    char = self.advance()
                    start = self.pos
                    if (
                        char in _STRUCTOR_CODES
                        and self.advance() == "t"
                        and self.advance() == "r"
                        and self.advance() == ARGLIST
                    ):
                        self.kind = KIND_CONSTRUCTOR if char == "c" else KIND_DESTRUCTOR
                    else:
                        self.pos = start
                        encoded = self.copy_until(ARGLIST)
                        self.buf = self.buf[: len(self.buf) - len(encoded)]
                        if encoded not in OPERATORS:
                            raise DemangleFailure("unknown special name")
                        if encoded in ("cctr", "cdtr"):
                            self.copy_op(encoded)
                        else:
                            self.buf += "operator "
                            self.copy_op(encoded)
                        self.kind = KIND_OPERATOR
                elif char == "o":
                    self.advance()
                    self.buf += "operator "
                    saved = self.set_qual
                    self.set_qual = False
                    self.copy_type(len(self.buf), arglvl=False)
                    self.set_qual = saved
                    # Leave the following `$` for `finish`, the same way a constructor
                    # leaves `$qqrv`. Consuming it here made `qv` look like junk in the
                    # name, so conversion operators from real BPLs never parsed.
                    if self.peek() != ARGLIST:
                        raise DemangleFailure("bad conversion operator")
                    self.kind = KIND_CONVERSION
                elif char in _THUNK_CODES:
                    tkind = char
                    char = self.advance()
                    if tkind == "v" and char == "s":
                        char = self.advance()
                        if char not in "fn":
                            raise DemangleFailure("bad thunk")
                        self.advance()
                        self.buf += "__vdthk__"
                        self.kind = KIND_THUNK
                    elif char == "c":
                        if self.advance() not in DIGITS:
                            raise DemangleFailure("bad thunk")
                        if self.advance() != "$":
                            raise DemangleFailure("bad thunk")
                        first = self.advance()
                        self.buf += f"__thunk__ [{first},"
                        self.buf += self._thunk_field() + ","
                        self.buf += self._thunk_field() + ","
                        self.buf += self._thunk_field() + "]"
                        self.kind = KIND_THUNK
                        self.advance()
                        return
                    else:
                        raise DemangleFailure("unknown special name")
                else:
                    raise DemangleFailure("unknown special name")
            elif char == "_":
                start = self.pos
                if self.advance() == "$":
                    self.advance()
                    self.buf += "__"
                    code = self.src[self.pos : self.pos + 2]
                    if code in _TABLE_KIND:
                        self.buf += _TABLE_KIND[code]
                    self.buf += "__ "
                    char = self.peek()
                    while "A" <= char <= "Z":
                        char = self.advance()
                    if char != "$":
                        raise DemangleFailure("bad special table")
                    if self.advance() != "@":
                        raise DemangleFailure("bad special table")
                    self.advance()
                    self.copy_name(False)
                    return
                self.pos = start
                self.copy_until(QUALIFIER, ARGLIST)
            else:
                self.copy_until(QUALIFIER, ARGLIST)

            char = self.peek()
            if char and char not in (QUALIFIER, ARGLIST):
                raise DemangleFailure("junk in name")
            if char == QUALIFIER:
                self.advance()
                if self.set_qual:
                    self.prevqual = self.qualend
                    self.qualend = len(self.buf)
                self.buf += "::"
                if not self.peek():
                    self.kind = KIND_VTABLE
            else:
                break

    def finish(self, do_args):
        self.set_qual = True
        self.namebase = len(self.buf)
        self.copy_name(False)
        self.set_qual = False
        self.base_end = len(self.buf)

        if self.kind in (KIND_CONSTRUCTOR, KIND_DESTRUCTOR):
            if self.kind == KIND_DESTRUCTOR:
                self.buf += "~"
            if self.qualend is not None:
                start = (self.prevqual + 2) if self.prevqual is not None else self.namebase
                self.buf += self.buf[start : self.qualend]
            else:
                self.buf += "unknown"

        if self.peek() == ARGLIST and do_args:
            if self.advance() not in _FUNCTION_CODES:
                raise DemangleFailure("bad function type")
            self.set_qual = False
            self.adjust_quals = True
            self.copy_type(self.namebase, arglvl=False)
            if not self.kind:
                self.kind = KIND_FUNCTION
        elif not self.kind:
            self.kind = KIND_DATA
        elif self.vtbl_flags:
            self.buf += " (" + ", ".join(self.vtbl_flags) + ")"

        # No rewriting of the finished text. `copy_return_type` already inserts the
        # calling convention where the unmangler puts it, and a duplicated qualifier --
        # `SetFlat(const const bool)` for `qqrxo` -- is what the unmangler prints, from
        # `copy_args` emitting `const ` and the type spelling it again. Collapsing it
        # here, and hoisting `__fastcall` to the front afterwards, moved 692 of 11,363
        # real exports away from the reference; both also ran `str.replace` over the
        # whole spelling, where an identifier holding the same text is not safe.
        text = self.buf
        if self.pos != self.length:
            raise DemangleFailure("unconsumed input")
        return text


def parse_delphi_symbol(name, limits=DEFAULT_LIMITS):
    """Parse `name`, returning a `DelphiSymbol`, or raise `DemangleFailure`."""
    if not name or name[0] != "@":
        raise DemangleFailure("not a Delphi mangled name")
    if _MSVC_FASTCALL.match(name):
        raise DemangleFailure("MSVC fastcall decoration")
    if not any(char.isalpha() for char in name):
        raise DemangleFailure("no identifier")
    if len(name) > limits.max_input:
        raise LimitExceeded(name, "input length", limits.max_input)
    parser = _Parser(name[1:], name, limits)
    try:
        text = parser.finish(do_args=True)
    except RecursionError as error:
        raise LimitExceeded(name, "recursion depth", limits.max_depth) from error
    if not text:
        raise DemangleFailure("empty result")
    if len(text) > limits.max_output:
        raise LimitExceeded(name, "output length", limits.max_output)
    return DelphiSymbol(name, text, parser.kind or KIND_DATA)


def detect(name):
    """Whether `name` is a Delphi/C++Builder export, not MSVC `@name@N`.

    `@__swiftmacro_` is Swift's macro mangling and is left for that scheme: the two
    share a first character, and guessing would rewrite a Swift name.

    A leading `@@` without a `$` encoding is `__linkproc__` in the unmangler. Real BPL
    exports of that shape are a single identifier (`@@InitExe`). `@@bug@@x` is not, and
    is left unclaimed so a broken plugin's fixture is not rewritten. A qualified data
    name such as `@System@Var` has no `$` and is still claimed.

    `@LOCAL@` and `@GUARD@` are left for the CodeWarrior scheme, which is where they come
    from: Wii CodeWarrior spells a function-local static `@LOCAL@<function>@<variable>`
    and its guard `@GUARD@...`. Both parse here as a unit called `LOCAL` or `GUARD`,
    which is a name Borland never wrote.

    A `?` is refused for the same reason and a stronger one: no Borland production
    writes one -- there is none in any of the 11,363 recorded exports -- and it is *the*
    MSVC marker, so a name carrying one that reaches this scheme is a piece of somebody
    else's. This copies characters through rather than checking an alphabet, so it read
    them: `demangle_text` over a listing tokenises `??R<lambda_1>@?0??define_lambda@@YAHXZ@QBE@XZ`
    at the angle brackets it cannot hold, and the `@?0??define_lambda@@YAHXZ@QBE@XZ` left
    over came back as `?0??define_lambda::__linkproc__ YAHXZ::QBE::XZ` -- a Delphi
    declaration built out of half an MSVC symbol. Asked for by language it is still read;
    what this decides is whether to claim a name nobody said was Delphi's.
    """
    if not name or name[0] != "@" or name.startswith("@__swift") or _MSVC_FASTCALL.match(name):
        return False
    if "?" in name:
        return False
    if name.startswith(("@LOCAL@", "@GUARD@")):
        return False
    if name.startswith("@@") and "$" not in name and not _LINKPROC_BARE.match(name):
        return False
    if not any(ch.isalpha() for ch in name):
        return False
    try:
        parse_delphi_symbol(name)
    except (DemangleFailure, LimitExceeded):
        return False
    return True
