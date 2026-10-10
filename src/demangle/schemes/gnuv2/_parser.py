"""The pre-Itanium C++ demangler, ported from libiberty's `cplus-dem.c`.

One algorithm, five styles. GNU g++ before 3.0, Lucid's `lcc`, the ARM/Annotated
Reference Manual encoding that cfront and its descendants emit, HP's aCC, and EDG's
front end all mangle names the same way up to a handful of flag-guarded differences, and
libiberty demangles all five with one body of code under `GNU_DEMANGLING`,
`LUCID_DEMANGLING`, `ARM_DEMANGLING`, `HP_DEMANGLING` and `EDG_DEMANGLING`. This is that
code, transcribed.

The reference is GCC 8.3.0's `libiberty/cplus-dem.c` -- the last release before the
pre-v3 demangler was dropped -- and the transcription is deliberately literal: the
function names are its function names, the control flow is its control flow, and the
quirks are kept rather than tidied. `demangle_template` returning failure for an empty
argument list, `demangle_template_value_parm` returning -1 where every caller then
treats -1 as success, `do_type` clearing the buffer its caller handed it: all of those
are the reference's behaviour, they are all observable in its own test vectors, and a
cleaner port would be a different demangler.

The two structural things worth knowing before reading it:

* **It builds text by prepending.** There is no tree. `demangle_class` *prepends*
  `foo::` to what has been built so far, which is how `AtEnd__13ivRubberGroup` becomes
  `ivRubberGroup::AtEnd(void)` with the class arriving after the function name has
  already been written. `_Buf` below is the reference's `string`, with the same
  prepend/append/truncate operations, because several places index into the buffer
  (`work.temp_start`) or cut a character off the end (the extra comma in an ARM template
  argument list).

* **It is stateful across the whole name.** `_Work` is the reference's `work_stuff`:
  remembered types for `T`n back-references, the squangling `B` and `K` vectors, the
  constructor and destructor counters that `demangle_prefix` sets and `demangle_class`
  consumes, and `temp_start`, the offset in the buffer where a class's template
  arguments began. `iterate_demangle_function` snapshots the whole of it, because GNU
  mangling is genuinely ambiguous -- there is no token marking where a function name
  ends and its signature begins, only a run of `__` that might be either -- so the
  demangler guesses, and backtracks when the guess does not parse.

Scored against the reference's own vectors: `libiberty/testsuite/demangle-expected` from
the same tree, every case marked `--format=gnu`, `--format=lucid`, `--format=arm` or
`--format=hp`, under both settings of `DMGL_PARAMS`. See `tests/conformance/`.
"""

__all__ = [
    "STYLES",
    "DemangleFailure",
    "GnuV2Symbol",
    "demangle_gnuv2",
]

STYLES = ("auto", "gnu", "lucid", "arm", "hp", "edg")
"""The demangling styles this shares its code with, spelled as `c++filt --format=` does.

`auto` is the reference's `auto_demangling`, which is GNU's reading plus EDG's
parameterised-type prefixes; it is not a search over the other four.
"""

#: g++'s scope marker: `$` where the assembler took it, `.` where it did not.
CPLUS_MARKERS = "$."

#: A bitmask, as in the reference, so `Ci` and `CVi` share one path.
_QUAL_CONST, _QUAL_VOLATILE, _QUAL_RESTRICT = 1, 2, 4
_QUALIFIER_CODES = {"C": _QUAL_CONST, "V": _QUAL_VOLATILE, "u": _QUAL_RESTRICT}
_QUALIFIER_STRINGS = {
    0: "",
    _QUAL_CONST: "const",
    _QUAL_VOLATILE: "volatile",
    _QUAL_RESTRICT: "__restrict",
    _QUAL_CONST | _QUAL_VOLATILE: "const volatile",
    _QUAL_CONST | _QUAL_RESTRICT: "const __restrict",
    _QUAL_VOLATILE | _QUAL_RESTRICT: "volatile __restrict",
    _QUAL_CONST | _QUAL_VOLATILE | _QUAL_RESTRICT: "const volatile __restrict",
}

# The kind of type just read decides how a template value parameter is spelled
# (pointer: a symbol name; bool: `true`). `tk_none` reads as integral at the end.
_TK_NONE, _TK_POINTER, _TK_REFERENCE, _TK_RVALUE_REFERENCE = 0, 1, 2, 3
_TK_INTEGRAL, _TK_BOOL, _TK_CHAR, _TK_REAL = 4, 5, 6, 7

#: In the reference's order, which is load-bearing: `demangle_expression` takes the first
#: prefix match, so `minus` precedes `mi`. The mangling-only `flags` column is dropped.
OPTABLE = (
    ("nw", " new"),
    ("dl", " delete"),
    ("new", " new"),
    ("delete", " delete"),
    ("vn", " new []"),
    ("vd", " delete []"),
    ("as", "="),
    ("ne", "!="),
    ("eq", "=="),
    ("ge", ">="),
    ("gt", ">"),
    ("le", "<="),
    ("lt", "<"),
    ("plus", "+"),
    ("pl", "+"),
    ("apl", "+="),
    ("minus", "-"),
    ("mi", "-"),
    ("ami", "-="),
    ("mult", "*"),
    ("ml", "*"),
    ("amu", "*="),
    ("aml", "*="),
    ("convert", "+"),
    ("negate", "-"),
    ("trunc_mod", "%"),
    ("md", "%"),
    ("amd", "%="),
    ("trunc_div", "/"),
    ("dv", "/"),
    ("adv", "/="),
    ("truth_andif", "&&"),
    ("aa", "&&"),
    ("truth_orif", "||"),
    ("oo", "||"),
    ("truth_not", "!"),
    ("nt", "!"),
    ("postincrement", "++"),
    ("pp", "++"),
    ("postdecrement", "--"),
    ("mm", "--"),
    ("bit_ior", "|"),
    ("or", "|"),
    ("aor", "|="),
    ("bit_xor", "^"),
    ("er", "^"),
    ("aer", "^="),
    ("bit_and", "&"),
    ("ad", "&"),
    ("aad", "&="),
    ("bit_not", "~"),
    ("co", "~"),
    ("call", "()"),
    ("cl", "()"),
    ("alshift", "<<"),
    ("ls", "<<"),
    ("als", "<<="),
    ("arshift", ">>"),
    ("rs", ">>"),
    ("ars", ">>="),
    ("component", "->"),
    ("pt", "->"),
    ("rf", "->"),
    ("indirect", "*"),
    ("method_call", "->()"),
    ("addr", "&"),
    ("array", "[]"),
    ("vc", "[]"),
    ("compound", ", "),
    ("cm", ", "),
    ("cond", "?:"),
    ("cn", "?:"),
    ("max", ">?"),
    ("mx", ">?"),
    ("min", "<?"),
    ("mn", "<?"),
    ("nop", ""),
    ("rm", "->*"),
    ("sz", "sizeof "),
)

#: Keyed by code for `demangle_function_name`'s exact-length lookups; codes are distinct.
_OPS = {}
for _code, _spelling in OPTABLE:
    _OPS.setdefault(_code, _spelling)

ARM_VTABLE_STRING = "__vtbl__"


def _hex_prefix(digits):
    """What `sscanf("%x")` would read: the longest hex prefix, or 0 for none.

    The reference zeroes the destination first, so a code that is not hex at all prints
    `int0_t` rather than failing.
    """
    end = 0
    while end < len(digits) and digits[end] in "0123456789abcdefABCDEF":
        end += 1
    return int(digits[:end], 16) if end else 0


#: The reference has no depth bound and segfaults on a deep enough name.
MAX_DEPTH = 200

#: Whole nested demanglings (HP template literal, EDG qualifier, `__thunk_` target).
MAX_NESTED = 16


class DemangleFailure(Exception):
    """This name is not one of these five manglings."""


class _Overflow(DemangleFailure):
    """A name that nests deeper than `MAX_DEPTH`, or recurses more than `MAX_NESTED`."""


def _isdigit(character):
    return "0" <= character <= "9"


class _Buf:
    """The reference's `string`: a text buffer built from both ends.

    Only the operations `cplus-dem.c` uses. Prepending is O(n) here where it is O(n) in
    the reference too (`string_prepend` memmoves), and these names are short.
    """

    __slots__ = ("s",)

    def __init__(self, s=""):
        self.s = s

    def append(self, text):
        self.s += text

    def prepend(self, text):
        self.s = text + self.s

    def blank(self):
        """`APPEND_BLANK`: a separating space, but not a leading one."""
        if self.s:
            self.s += " "

    def clear(self):
        self.s = ""

    def truncate(self, at):
        self.s = self.s[:at]

    def __len__(self):
        return len(self.s)


class _Cur:
    """A cursor over one string, standing in for the reference's `const char **`.

    `do_type` rebinds its `mangled` to a *different* string when it reads a `T`n
    back-reference -- the remembered type is re-parsed in place of the reference -- so a
    cursor has to be a value the caller can replace, not an index into one buffer.
    """

    __slots__ = ("_ascii", "i", "n", "s")

    def __init__(self, s, i=0):
        self.s = s
        self.i = i
        #: Constant: a cursor is replaced, never re-pointed, and `at` is the hot path.
        self.n = len(s)
        self._ascii = s.isascii()

    def at(self, offset=0):
        """The character `offset` ahead, or `""` past the end, which stands in for NUL."""
        index = self.i + offset
        return self.s[index] if 0 <= index < self.n else ""

    def rest(self):
        return self.s[self.i :]

    def done(self):
        return self.i >= self.n

    def advance(self, count=1):
        self.i += count

    def byte_count(self, count):
        """Convert an ABI byte length to this cursor's character count."""
        if self._ascii or count <= 0:
            return count
        start = at = self.i
        remaining = count
        while remaining and at < self.n:
            code = ord(self.s[at])
            if 0xD800 <= code <= 0xDFFF:
                raise DemangleFailure("invalid Unicode in identifier")
            width = 1 if code < 0x80 else 2 if code < 0x800 else 3 if code < 0x10000 else 4
            remaining -= width
            if remaining < 0:
                raise DemangleFailure("identifier ends inside a UTF-8 character")
            at += 1
        if remaining:
            raise DemangleFailure("identifier runs past the end of the name")
        return at - start

    def take(self, count):
        text = self.s[self.i : self.i + count]
        self.i += count
        return text


class _Work:
    """The reference's `work_stuff`, whole.

    Everything here is shared between the productions and mutated as they run, which is
    what makes `iterate_demangle_function`'s backtracking need a deep copy rather than a
    saved cursor.
    """

    __slots__ = (
        "ansi",
        "btypevec",
        "capture",
        "constructor",
        "depth",
        "destructor",
        "dllimported",
        "evidence",
        "forgetting_types",
        "ktypevec",
        "nested",
        "nrepeats",
        "params",
        "previous_argument",
        "proctypevec",
        "return_type",
        "special",
        "static_type",
        "style",
        "suffix",
        "temp_start",
        "tmpl_argvec",
        "trailing",
        "type_quals",
        "typevec",
    )

    def __init__(self, style, params=True, ansi=True):
        self.style = style
        self.params = params
        self.ansi = ansi
        self.typevec = []
        self.ktypevec = []
        self.btypevec = []
        self.proctypevec = []
        self.constructor = 0
        self.destructor = 0
        self.static_type = 0
        self.temp_start = 0
        self.type_quals = 0
        self.dllimported = 0
        self.tmpl_argvec = None
        self.forgetting_types = 0
        self.previous_argument = None
        self.nrepeats = 0
        self.depth = 0
        self.nested = 0
        # Not the reference's: what the parts-in-order tree is built from. See `nodes`.
        self.capture = None
        self.return_type = None
        self.trailing = ()
        self.special = None
        self.suffix = ""
        #: What was decoded as opposed to read as fundamental-type letters; see `detect`.
        self.evidence = set()

    @property
    def auto(self):
        return self.style == "auto"

    @property
    def gnu(self):
        return self.style == "gnu"

    @property
    def lucid(self):
        return self.style == "lucid"

    @property
    def arm(self):
        return self.style == "arm"

    @property
    def hp(self):
        return self.style == "hp"

    @property
    def edg(self):
        return self.style == "edg"

    def remember_type(self, text):
        if not self.forgetting_types:
            self.typevec.append(text)

    def forget_types(self):
        self.typevec.clear()

    def remember_ktype(self, text):
        self.ktypevec.append(text)

    def register_btype(self):
        self.btypevec.append(None)
        return len(self.btypevec) - 1

    def remember_btype(self, text, index):
        self.btypevec[index] = text

    def forget_b_and_k_types(self):
        self.ktypevec.clear()
        self.btypevec.clear()

    def delete_non_bk(self):
        self.typevec.clear()
        self.proctypevec.clear()
        self.tmpl_argvec = None
        self.previous_argument = None

    def delete_all(self):
        self.delete_non_bk()
        self.forget_b_and_k_types()

    def snapshot(self):
        """`work_stuff_copy_to_from`, in the direction that saves."""
        return (
            list(self.typevec),
            list(self.ktypevec),
            list(self.btypevec),
            list(self.proctypevec),
            self.constructor,
            self.destructor,
            self.static_type,
            self.temp_start,
            self.type_quals,
            self.dllimported,
            None if self.tmpl_argvec is None else list(self.tmpl_argvec),
            self.forgetting_types,
            self.previous_argument,
            self.nrepeats,
            self.capture,
            self.return_type,
            self.trailing,
            set(self.evidence),
        )

    def restore(self, saved):
        (
            typevec,
            ktypevec,
            btypevec,
            proctypevec,
            self.constructor,
            self.destructor,
            self.static_type,
            self.temp_start,
            self.type_quals,
            self.dllimported,
            tmpl_argvec,
            self.forgetting_types,
            self.previous_argument,
            self.nrepeats,
            self.capture,
            self.return_type,
            self.trailing,
            evidence,
        ) = saved
        self.typevec = list(typevec)
        self.ktypevec = list(ktypevec)
        self.btypevec = list(btypevec)
        self.proctypevec = list(proctypevec)
        self.tmpl_argvec = None if tmpl_argvec is None else list(tmpl_argvec)
        self.evidence = set(evidence)


class _Depth:
    """Bounds one production's nesting, so a hostile name gives up rather than recurses."""

    __slots__ = ("work",)

    def __init__(self, work):
        self.work = work

    def __enter__(self):
        self.work.depth += 1
        if self.work.depth > MAX_DEPTH:
            raise _Overflow("nested too deeply")
        return self

    def __exit__(self, *exc):
        self.work.depth -= 1
        return False


def consume_count(cur):
    """A run of digits, or -1 where there is not one. -1 also on overflow."""
    if not _isdigit(cur.at()):
        return -1
    start = cur.i
    while _isdigit(cur.at()):
        cur.advance()
    digits = cur.s[start : cur.i]
    # The reference detects `int` overflow and returns -1 having consumed the digits.
    significant = digits.lstrip("0") or "0"
    if len(significant) > 10 or (len(significant) == 10 and significant > "2147483647"):
        return -1
    # Leading zeros do not overflow C's accumulator, even beyond Python's decimal
    # conversion limit. Only the significant digits need to become an integer.
    return int(significant)


def consume_count_with_underscores(cur):
    """`_NNN_` for a count above nine, a bare digit below it, -1 for neither."""
    if cur.at() == "_":
        cur.advance()
        if not _isdigit(cur.at()):
            return -1
        index = consume_count(cur)
        if cur.at() != "_":
            return -1
        cur.advance()
        return index
    if not _isdigit(cur.at()):
        return -1
    index = ord(cur.at()) - 48
    cur.advance()
    return index


def get_count(cur):
    """`(ok, count)`: one digit, or a `NNN_`-delimited run of them.

    The odd rule -- a multi-digit count counts only if an underscore follows it -- is
    what makes the ARM `Nxy` repeat code readable, where `x` is a single digit and `y`
    is the index it repeats from. See the reference's commentary.
    """
    if not _isdigit(cur.at()):
        return False, 0
    count = ord(cur.at()) - 48
    cur.advance()
    if _isdigit(cur.at()):
        scan = cur.i
        value = count
        while _isdigit(cur.s[scan] if scan < len(cur.s) else ""):
            value = value * 10 + (ord(cur.s[scan]) - 48)
            scan += 1
        if scan < len(cur.s) and cur.s[scan] == "_":
            cur.i = scan + 1
            count = value
    return True, count


def do_type(work, cur, result, allow_empty=False):
    """One type encoding. Returns a `_TK_*` kind, or 0 for failure.

    `result` is cleared first, as the reference's `string_init` does to the buffer its
    caller handed over, and emptied again if the parse fails.

    The declarator is built in `decl` and joined on at the end, which is why a pointer to
    an array comes out as `int (*)[3]` -- the `(` and `)` are added when the array sees a
    `*` already sitting at the front of the declarator.

    `allow_empty` lets the base type be nothing at all, which is what the reference does
    everywhere and this does only where a template value argument's type goes. See
    `demangle_fund_type`.
    """
    with _Depth(work):
        return _do_type(work, cur, result, allow_empty)


def _do_type(work, cur, result, allow_empty):
    decl = _Buf()
    result.clear()
    done = False
    success = 1
    is_proctypevec = False
    tk = _TK_NONE

    while success and not done:
        code = cur.at()
        if code in ("P", "p"):
            cur.advance()
            decl.prepend("*")
            if tk == _TK_NONE:
                tk = _TK_POINTER
        elif code == "R":
            cur.advance()
            decl.prepend("&")
            if tk == _TK_NONE:
                tk = _TK_REFERENCE
        elif code == "O":
            cur.advance()
            decl.prepend("&&")
            if tk == _TK_NONE:
                tk = _TK_RVALUE_REFERENCE
        elif code == "A":
            cur.advance()
            if decl.s[:1] in ("*", "&"):
                decl.prepend("(")
                decl.append(")")
            decl.append("[")
            if cur.at() != "_":
                success = demangle_template_value_parm(work, cur, decl, _TK_INTEGRAL)
            if cur.at() == "_":
                cur.advance()
            decl.append("]")
        elif code == "T":
            cur.advance()
            ok, n = get_count(cur)
            if not ok or n < 0 or n >= len(work.typevec):
                success = 0
            else:
                for seen in work.proctypevec:
                    if seen == n:
                        success = 0
                        break
            if success:
                is_proctypevec = True
                work.proctypevec.append(n)
                # The remembered type is re-read in place of the reference, on a new cursor.
                cur = _Cur(work.typevec[n])
        elif code == "F":
            cur.advance()
            if decl.s[:1] in ("*", "&"):
                decl.prepend("(")
                decl.append(")")
            if not demangle_nested_args(work, cur, decl) or (cur.at() not in ("_", "")):
                success = 0
                break
            if cur.at() == "_":
                cur.advance()
        elif code == "M":
            success = _do_type_member(work, cur, decl)
            if not success:
                break
        elif code == "G":
            cur.advance()
        elif code in ("C", "V", "u"):
            if work.ansi:
                if len(decl):
                    decl.prepend(" ")
                decl.prepend(_QUALIFIER_STRINGS[_QUALIFIER_CODES[code]])
            cur.advance()
        else:
            done = True

    if success:
        code = cur.at()
        if code in ("Q", "K"):
            success = demangle_qualified(work, cur, result, 0, 1)
        elif code == "B":
            cur.advance()
            ok, n = get_count(cur)
            if not ok or n < 0 or n >= len(work.btypevec) or work.btypevec[n] is None:
                # Not the reference's, which dereferences a registered-but-unfilled slot.
                success = 0
            else:
                result.append(work.btypevec[n])
        elif code in ("X", "Y"):
            cur.advance()
            index = consume_count_with_underscores(cur)
            if (
                index == -1
                or (work.tmpl_argvec is not None and index >= len(work.tmpl_argvec))
                or consume_count_with_underscores(cur) == -1
            ):
                success = 0
            else:
                if work.tmpl_argvec is not None:
                    result.append(work.tmpl_argvec[index] or "")
                else:
                    result.append(f"T{index}")
                success = 1
        else:
            success = demangle_fund_type(work, cur, result, allow_empty)
            if tk == _TK_NONE:
                tk = success

    if success:
        if len(decl):
            result.append(" ")
            result.append(decl.s)
    else:
        result.clear()

    if is_proctypevec:
        work.proctypevec.pop()

    if success:
        return _TK_INTEGRAL if tk == _TK_NONE else tk
    return 0


def _do_type_member(work, cur, decl):
    """`M`: a pointer to member, whose class name goes inside the parentheses.

    The reference's `case 'M'`. `member` is always set here, as in the reference.
    """
    type_quals = 0
    member = cur.at() == "M"
    cur.advance()
    decl.append(")")

    if cur.at() != "Q":
        decl.prepend("::")

    if _isdigit(cur.at()):
        n = cur.byte_count(consume_count(cur))
        if n == -1 or len(cur.rest()) < n:
            return 0
        decl.prepend(cur.take(n))
    elif cur.at() in ("X", "Y"):
        temp = _Buf()
        do_type(work, cur, temp)
        decl.prepend(temp.s)
    elif cur.at() == "t":
        temp = _Buf()
        if not demangle_template(work, cur, temp, None, 1, 1):
            return 0
        decl.prepend(temp.s)
    elif cur.at() == "Q":
        if not demangle_qualified(work, cur, decl, 0, 0):
            return 0
    else:
        return 0

    decl.prepend("(")
    if member:
        if cur.at() in ("C", "V", "u"):
            type_quals |= _QUALIFIER_CODES[cur.at()]
            cur.advance()
        if cur.at() != "F":
            return 0
        cur.advance()
    if (member and not demangle_nested_args(work, cur, decl)) or cur.at() != "_":
        return 0
    cur.advance()
    if not work.ansi:
        return 1
    if type_quals:
        decl.blank()
        decl.append(_QUALIFIER_STRINGS[type_quals])
    return 1


_FUND_TYPES = {
    "v": ("void", _TK_INTEGRAL),
    "x": ("long long", _TK_INTEGRAL),
    "l": ("long", _TK_INTEGRAL),
    "i": ("int", _TK_INTEGRAL),
    "s": ("short", _TK_INTEGRAL),
    "b": ("bool", _TK_BOOL),
    "c": ("char", _TK_CHAR),
    "w": ("wchar_t", _TK_CHAR),
    "r": ("long double", _TK_REAL),
    "d": ("double", _TK_REAL),
    "f": ("float", _TK_REAL),
}


def demangle_fund_type(work, cur, result, allow_empty=False):
    """A builtin type and the qualifiers in front of it: `CUs` is `const unsigned short`.

    Appends to `result` rather than clearing it -- `do_type` has already prepared the
    buffer. Returns a `_TK_*` kind, or 0.
    """
    tk = _TK_INTEGRAL

    while True:
        code = cur.at()
        if code in ("C", "V", "u"):
            if work.ansi:
                if len(result):
                    result.prepend(" ")
                result.prepend(_QUALIFIER_STRINGS[_QUALIFIER_CODES[code]])
            cur.advance()
        elif code == "U":
            cur.advance()
            result.blank()
            result.append("unsigned")
        elif code == "S":
            cur.advance()
            result.blank()
            result.append("signed")
        elif code == "J":
            cur.advance()
            result.blank()
            result.append("__complex")
        else:
            break

    code = cur.at()
    if code in ("", "_"):
        # The reference's empty `int`, refused where the whole type would be empty
        # (`drm_intel_gem_bo_map__cpu` is C). `allow_empty` is for a template value's
        # type, `_8_`, which a C name cannot reach; see `_demangle_template`.
        return tk if allow_empty or len(result) else 0
    if code in _FUND_TYPES:
        spelling, kind = _FUND_TYPES[code]
        cur.advance()
        result.blank()
        result.append(spelling)
        return kind
    if code in ("G", "I"):
        # `I` is `int<N>_t` with a hex width; `G` the same with a leading digit count.
        if code == "G":
            cur.advance()
            if not _isdigit(cur.at()):
                return 0
        cur.advance()
        if cur.at() == "_":
            cur.advance()
            start = cur.i
            while not cur.done() and cur.at() != "_" and cur.i - start < 36:
                cur.advance()
            if cur.at() != "_":
                return 0
            digits = cur.s[start : cur.i]
            cur.advance()
        else:
            digits = cur.take(min(len(cur.rest()), 2))
        width = _hex_prefix(digits)
        result.blank()
        result.append(f"int{width}_t")
        return tk
    if _isdigit(code):
        index = work.register_btype()
        btype = _Buf()
        if not demangle_class_name(work, cur, btype):
            return 0
        work.remember_btype(btype.s, index)
        result.blank()
        result.append(btype.s)
        work.evidence.add("class")
        return tk
    if code == "t":
        btype = _Buf()
        success = demangle_template(work, cur, btype, None, 1, 1)
        result.append(btype.s)
        return tk if success else 0
    return 0


def demangle_template_template_parm(work, cur, tname):
    """`template <class, class> class`: a template used as a template argument."""
    with _Depth(work):
        tname.append("template <")
        need_comma = False
        success = 1
        ok, count = get_count(cur)
        if ok:
            for _ in range(count):
                if need_comma:
                    tname.append(", ")
                if cur.at() == "Z":
                    cur.advance()
                    tname.append("class")
                elif cur.at() == "z":
                    cur.advance()
                    success = demangle_template_template_parm(work, cur, tname)
                    if not success:
                        break
                else:
                    temp = _Buf()
                    success = do_type(work, cur, temp)
                    if success:
                        tname.append(temp.s)
                    if not success:
                        break
                need_comma = True
        if tname.s.endswith(">"):
            tname.append(" ")
        tname.append("> class")
        return success


def demangle_expression(work, cur, out, tk):
    """`E` ... `W`: an operand, then alternating operators and operands, parenthesised."""
    with _Depth(work):
        need_operator = False
        success = 1
        out.append("(")
        cur.advance()
        while success and cur.at() != "W" and not cur.done():
            if need_operator:
                success = 0
                rest = cur.rest()
                for code, spelling in OPTABLE:
                    if rest.startswith(code):
                        out.append(" ")
                        out.append(spelling)
                        out.append(" ")
                        success = 1
                        cur.advance(len(code))
                        break
                if not success:
                    break
            else:
                need_operator = True
            success = demangle_template_value_parm(work, cur, out, tk)
        if cur.at() != "W":
            return 0
        out.append(")")
        cur.advance()
        return success


def demangle_integral_value(work, cur, out):
    """An integer template argument: a literal, an expression, or a qualified name."""
    if cur.at() == "E":
        return demangle_expression(work, cur, out, _TK_INTEGRAL)
    if cur.at() in ("Q", "K"):
        return demangle_qualified(work, cur, out, 0, 1)

    # The reference's two flags for a `_NN_` or bare count.
    multidigit_without_leading_underscore = False
    leave_following_underscore = False

    if cur.at() == "_":
        if cur.at(1) == "m":
            multidigit_without_leading_underscore = True
            out.append("-")
            cur.advance(2)
        else:
            leave_following_underscore = True
    else:
        if cur.at() == "m":
            out.append("-")
            cur.advance()
        multidigit_without_leading_underscore = True
        leave_following_underscore = True

    value = consume_count(cur) if multidigit_without_leading_underscore else consume_count_with_underscores(cur)

    if value == -1:
        return 0
    out.append(str(value))
    if (value > 9 or multidigit_without_leading_underscore) and not leave_following_underscore and cur.at() == "_":
        cur.advance()
    return 1


def demangle_real_value(work, cur, out):
    """A floating template argument, digit by digit: `1.5e10`, `m0.5`."""
    if cur.at() == "E":
        return demangle_expression(work, cur, out, _TK_REAL)
    if cur.at() == "m":
        out.append("-")
        cur.advance()
    while _isdigit(cur.at()):
        out.append(cur.at())
        cur.advance()
    if cur.at() == ".":
        out.append(".")
        cur.advance()
        while _isdigit(cur.at()):
            out.append(cur.at())
            cur.advance()
    if cur.at() == "e":
        out.append("e")
        cur.advance()
        while _isdigit(cur.at()):
            out.append(cur.at())
            cur.advance()
    return 1


def demangle_template_value_parm(work, cur, out, tk):
    """One template argument's *value*, spelled the way its type wants it.

    Returns -1 in two places where the reference does, and every caller of the reference
    treats -1 as success, so this does too.
    """
    success = 1
    if cur.at() == "Y":
        cur.advance()
        index = consume_count_with_underscores(cur)
        if (
            index == -1
            or (work.tmpl_argvec is not None and index >= len(work.tmpl_argvec))
            or consume_count_with_underscores(cur) == -1
        ):
            return -1
        if work.tmpl_argvec is not None:
            out.append(work.tmpl_argvec[index] or "")
        else:
            out.append(f"T{index}")
    elif tk == _TK_INTEGRAL:
        success = demangle_integral_value(work, cur, out)
    elif tk == _TK_CHAR:
        if cur.at() == "m":
            out.append("-")
            cur.advance()
        out.append("'")
        value = consume_count(cur)
        if value <= 0:
            success = 0
        else:
            # `(char) val` in the reference: one byte, so a count past 255 wraps.
            out.append(chr(value & 0xFF))
            out.append("'")
    elif tk == _TK_BOOL:
        value = consume_count(cur)
        if value == 0:
            out.append("false")
        elif value == 1:
            out.append("true")
        else:
            success = 0
    elif tk == _TK_REAL:
        success = demangle_real_value(work, cur, out)
    elif tk in (_TK_POINTER, _TK_REFERENCE, _TK_RVALUE_REFERENCE):
        if cur.at() == "Q":
            success = demangle_qualified(work, cur, out, 0, 1)
        else:
            symbol_len = cur.byte_count(consume_count(cur))
            if symbol_len == -1 or symbol_len > len(cur.rest()):
                return -1
            if symbol_len == 0:
                out.append("0")
            else:
                name = cur.s[cur.i : cur.i + symbol_len]
                # Via the public entry point, as the reference does, so no squangling
                # state applies.
                spelled = _demangle_fresh(work, name)
                if tk == _TK_POINTER:
                    out.append("&")
                out.append(spelled if spelled is not None else name)
            cur.advance(symbol_len)
    return success


def demangle_template(work, cur, tname, trawname, is_type, remember):
    """`t`: a template's name and arguments. `trawname` gets the name without them.

    Returns the reference's `success`, which starts at 0 and is only ever set inside the
    argument loop -- so a template with an empty argument list *fails*, and vectors in
    the reference's own test file depend on that.
    """
    with _Depth(work):
        return _demangle_template(work, cur, tname, trawname, is_type, remember)


def _demangle_template(work, cur, tname, trawname, is_type, remember):
    need_comma = False
    success = 0

    cur.advance()
    if is_type:
        if cur.at() == "z":
            # The template's own name is a template parameter.
            cur.advance()
            if cur.done():
                return 0
            cur.advance()
            index = consume_count_with_underscores(cur)
            if (
                index == -1
                or (work.tmpl_argvec is not None and index >= len(work.tmpl_argvec))
                or consume_count_with_underscores(cur) == -1
            ):
                return 0
            spelled = (work.tmpl_argvec[index] or "") if work.tmpl_argvec is not None else f"T{index}"
            tname.append(spelled)
            if trawname is not None:
                trawname.append(spelled)
        else:
            count = cur.byte_count(consume_count(cur))
            if count <= 0 or len(cur.rest()) < count:
                return 0
            name = cur.take(count)
            tname.append(name)
            if trawname is not None:
                trawname.append(name)

    tname.append("<")
    ok, count = get_count(cur)
    if not ok:
        return 0
    if not is_type:
        work.tmpl_argvec = [None] * count

    for at in range(count):
        if need_comma:
            tname.append(", ")
        if cur.at() == "Z":
            cur.advance()
            temp = _Buf()
            success = do_type(work, cur, temp)
            if success:
                tname.append(temp.s)
                if not is_type:
                    work.tmpl_argvec[at] = temp.s
            if not success:
                break
        elif cur.at() == "z":
            cur.advance()
            success = demangle_template_template_parm(work, cur, tname)
            if success:
                count2 = cur.byte_count(consume_count(cur))
                if count2 > 0 and len(cur.rest()) >= count2:
                    tname.append(" ")
                    name = cur.take(count2)
                    tname.append(name)
                    if not is_type:
                        work.tmpl_argvec[at] = name
            if not success:
                break
        else:
            temp = _Buf()
            success = do_type(work, cur, temp, allow_empty=True)
            if not success:
                break
            out = tname if is_type else _Buf()
            success = demangle_template_value_parm(work, cur, out, success)
            if not success:
                success = 0
                break
            if not is_type:
                work.tmpl_argvec[at] = out.s
                tname.append(out.s)
        need_comma = True

    if tname.s.endswith(">"):
        tname.append(" ")
    tname.append(">")

    if is_type and remember:
        index = work.register_btype()
        work.remember_btype(tname.s, index)
    if success:
        work.evidence.add("template")
    return success


def arm_pt(work, cur, n):
    """Find a cfront/EDG parameterised-type marker. Returns `(anchor, args)` or None.

    `anchor` is where the class name ends and the marker begins; `args` is the first
    argument. A marker only counts if the count after it runs exactly to `n`, which is
    what stops `__pt__` inside an ordinary identifier from being read as one.
    """
    base = cur.i
    if work.arm or work.hp:
        anchor = cur.s.find("__pt__", base)
        if anchor >= 0:
            args = _Cur(cur.s, anchor + 6)
            length = consume_count(args)
            if length == -1:
                return None
            if args.i + length == base + n and args.at() == "_":
                args.advance()
                return anchor, args.i
    if work.auto or work.edg:
        anchor = -1
        for marker in ("__tm__", "__ps__", "__pt__"):
            anchor = cur.s.find(marker, base)
            if anchor >= 0:
                break
        if anchor >= 0:
            args = _Cur(cur.s, anchor + 6)
            length = consume_count(args)
            if length == -1:
                return None
            if args.i + length == base + n and args.at() == "_":
                args.advance()
                return anchor, args.i
        else:
            anchor = cur.s.find("__S", base)
            if anchor >= 0:
                args = _Cur(cur.s, anchor + 3)
                length = consume_count(args)
                if length == -1:
                    return None
                if args.i + length == base + n and args.at() == "_":
                    args.advance()
                    return anchor, args.i
    return None


def snarf_numeric_literal(cur, out):
    """A signed run of digits, as an HP cfront template literal writes one."""
    if cur.at() == "-":
        out.append("-")
        cur.advance()
    elif cur.at() == "+":
        cur.advance()
    if not _isdigit(cur.at()):
        return 0
    while _isdigit(cur.at()):
        out.append(cur.at())
        cur.advance()
    return 1


def do_hpacc_template_const_value(work, cur, out):
    """HP aCC's integral template argument: `U`/`S` for unsigned/signed, then a sign."""
    if cur.at() not in ("U", "S"):
        return 0
    unsigned_const = cur.at() == "U"
    cur.advance()
    if cur.at() == "N":
        out.append("-")
        cur.advance()
    elif cur.at() == "P":
        cur.advance()
    elif cur.at() == "M":
        out.append("-2147483648")
        cur.advance()
        return 1
    else:
        return 0
    if not _isdigit(cur.at()):
        return 0
    while _isdigit(cur.at()):
        out.append(cur.at())
        cur.advance()
    if unsigned_const:
        out.append("U")
    return 1


def do_hpacc_template_literal(work, cur, out):
    """HP aCC's `A`: a named constant, written as the address of the thing it names."""
    if cur.at() != "A":
        return 0
    cur.advance()
    literal_len = cur.byte_count(consume_count(cur))
    if literal_len <= 0 or literal_len > len(cur.rest()):
        return 0
    out.append("&")
    name = cur.s[cur.i : cur.i + literal_len]
    spelled = _demangle_fresh(work, name)
    out.append(spelled if spelled is not None else name)
    cur.advance(literal_len)
    return 1


def demangle_arm_hp_template(work, cur, n, declp):
    """The class name of length `n`, and its template arguments if it has any.

    Three shapes share this: HP aCC's `classXt1t2`, the ARM/cfront `class__pt__NN_args`
    that `arm_pt` finds, and a plain name with neither. `work.temp_start` records where
    the arguments began, so that `demangle_class` can build a constructor's name from
    the class *without* them.
    """
    end = cur.i + n

    if work.hp and cur.at(n) == "X":
        start_spec_args = cur.s.find("<", cur.i)
        if 0 <= start_spec_args < end:
            declp.append(cur.s[cur.i : start_spec_args])
        else:
            declp.append(cur.s[cur.i : cur.i + n])
        cur.advance(n + 1)
        if work.temp_start == -1:
            work.temp_start = len(declp)

        # Template arguments are always spelled in full, whatever the caller asked for.
        hold_params = work.params
        work.params = True

        declp.append("<")
        arg = _Buf()
        while True:
            arg.clear()
            code = cur.at()
            if code == "T":
                cur.advance()
                if not do_type(work, cur, arg):
                    break
            elif code in ("U", "S"):
                if not do_hpacc_template_const_value(work, cur, arg):
                    break
            elif code == "A":
                if not do_hpacc_template_literal(work, cur, arg):
                    break
            else:
                break
            declp.append(arg.s)
            if cur.at() in ("", "_"):
                break
            declp.append(",")
        declp.append(">")
        if cur.at() == "_":
            cur.advance()
        work.params = hold_params
        return

    found = arm_pt(work, cur, n)
    if found is not None:
        anchor, args_at = found
        declp.append(cur.s[cur.i : anchor])
        if work.temp_start == -1:
            work.temp_start = len(declp)

        hold_params = work.params
        work.params = True

        declp.append("<")
        args = _Cur(cur.s, args_at)
        arg = _Buf()
        while args.i < end:
            arg.clear()
            if args.at() == "X":
                # A typed constant: the type in parentheses, then the literal after `L`.
                args.advance()
                type_str = _Buf()
                if not do_type(work, args, type_str):
                    break
                arg.append("(")
                arg.append(type_str.s)
                arg.append(")")
                if args.at() != "L":
                    break
                args.advance()
                if not snarf_numeric_literal(args, arg):
                    break
            elif args.at() == "L":
                args.advance()
                if not snarf_numeric_literal(args, arg):
                    break
            else:
                old = args.i
                if not do_type(work, args, arg):
                    break
                if args.i == old:
                    # No progress: the reference bails out rather than spinning.
                    work.params = hold_params
                    return
            declp.append(arg.s)
            declp.append(",")
        if args.i >= end and len(declp):
            # The reference drops it unconditionally at the end, hence the non-empty guard.
            declp.truncate(len(declp) - 1)
        declp.append(">")
        work.params = hold_params
        cur.advance(n)
        return

    rest = cur.rest()
    if (
        n > 10
        and rest.startswith("_GLOBAL_")
        and len(rest) > 10
        and rest[9] == "N"
        and rest[8] == rest[10]
        and rest[8] in CPLUS_MARKERS
    ):
        declp.append("{anonymous}")
    else:
        if work.temp_start == -1:
            work.temp_start = 0
        declp.append(cur.s[cur.i : cur.i + n])
    cur.advance(n)


def demangle_class_name(work, cur, declp):
    """A length-prefixed class name, with template arguments where it has them."""
    n = cur.byte_count(consume_count(cur))
    if n <= 0:
        # Stricter than the reference, which prepends `::` to a zero-length class name.
        return 0
    if len(cur.rest()) >= n:
        demangle_arm_hp_template(work, cur, n, declp)
        return 1
    return 0


def demangle_class(work, cur, declp):
    """A class name, prepended to what has been built so far as `class::`.

    Where a constructor or destructor is pending -- `demangle_prefix` counted one -- the
    class's own name is prepended again as the function's name, and the counter is spent.
    """
    index = work.register_btype()
    class_name = _Buf()
    if not demangle_class_name(work, cur, class_name):
        return 0
    whole = class_name.s
    if (work.constructor & 1) or (work.destructor & 1):
        # A constructor is named for the class without its template arguments.
        bare = whole[: work.temp_start] if work.temp_start and work.temp_start != -1 else whole
        declp.prepend(bare)
        if work.destructor & 1:
            declp.prepend("~")
            work.destructor -= 1
        else:
            work.constructor -= 1
    work.remember_ktype(whole)
    work.remember_btype(whole, index)
    declp.prepend("::")
    declp.prepend(whole)
    work.evidence.add("class")
    return 1


def demangle_qualified(work, cur, result, isfuncname, append):
    """`Q`n or `K`n: a nested name, `Q25Outer5Inner` for `Outer::Inner`."""
    with _Depth(work):
        return _demangle_qualified(work, cur, result, isfuncname, append)


def _demangle_qualified(work, cur, result, isfuncname, append):
    qualifiers = 0
    success = 1
    index = work.register_btype()

    isfuncname = isfuncname and ((work.constructor & 1) or (work.destructor & 1))

    temp = _Buf()
    last_name = _Buf()

    if cur.at() == "K":
        cur.advance()
        idx = consume_count_with_underscores(cur)
        if idx == -1 or idx >= len(work.ktypevec):
            success = 0
        else:
            temp.append(work.ktypevec[idx])
    else:
        second = cur.at(1)
        if second == "_":
            cur.advance()
            qualifiers = consume_count_with_underscores(cur)
            if qualifiers == -1:
                success = 0
        elif "1" <= second <= "9":
            qualifiers = ord(second) - 48
            # cfront writes an underscore after the digit; the ARM does not mention one.
            if cur.at(2) == "_":
                cur.advance()
            cur.advance(2)
        else:
            success = 0

    if not success:
        return 0

    while qualifiers > 0:
        qualifiers -= 1
        remember_k = True
        last_name.clear()

        if cur.at() == "_":
            cur.advance()

        if cur.at() == "t":
            success = demangle_template(work, cur, temp, last_name, 1, 0)
            if not success:
                break
        elif cur.at() == "K":
            cur.advance()
            idx = consume_count_with_underscores(cur)
            if idx == -1 or idx >= len(work.ktypevec):
                success = 0
            else:
                temp.append(work.ktypevec[idx])
            remember_k = False
            if not success:
                break
        elif work.edg:
            # EDG writes a whole mangled name as the qualifier.
            namelength = consume_count(cur)
            if namelength == -1:
                success = 0
                break
            recursively_demangle(work, cur, temp, namelength)
        else:
            last_name.clear()
            success = do_type(work, cur, last_name)
            if not success:
                break
            temp.append(last_name.s)

        if remember_k:
            work.remember_ktype(temp.s)

        if qualifiers > 0:
            temp.append("::")

    work.remember_btype(temp.s, index)

    if isfuncname:
        temp.append("::")
        if work.destructor & 1:
            temp.append("~")
        temp.append(last_name.s)

    if append:
        result.append(temp.s)
    else:
        if len(result):
            temp.append("::")
        result.prepend(temp.s)
    if success:
        work.evidence.add("qualified")
    return success


def recursively_demangle(work, cur, result, namelength):
    """Demangle the next `namelength` characters as a name in their own right."""
    namelength = cur.byte_count(namelength)
    name = cur.s[cur.i : cur.i + namelength]
    spelled = _demangle_fresh(work, name)
    result.append(spelled if spelled is not None else name)
    cur.advance(namelength)


def do_arg(work, cur, result):
    """One argument, remembered afterwards so that a later `T`n can refer back to it."""
    start = cur.i
    result.clear()

    if work.nrepeats > 0:
        work.nrepeats -= 1
        if work.previous_argument is None:
            return 0
        result.append(work.previous_argument)
        return 1

    if cur.at() == "n":
        # A squangling repeat: the next argument, `n` times.
        cur.advance()
        work.nrepeats = consume_count(cur)
        if work.nrepeats <= 0:
            return 0
        if work.nrepeats > 9:
            if cur.at() != "_":
                return 0
            cur.advance()
        return do_arg(work, cur, result)

    # The reference fills `previous_argument` even on failure, and a later repeat reads it.
    previous = _Buf()
    read = do_type(work, cur, previous)
    work.previous_argument = previous.s
    if not read:
        return 0
    result.append(previous.s)
    work.remember_type(cur.s[start : cur.i])
    return 1


def demangle_args(work, cur, declp, capture=None):
    """The argument list, after any class and the `F` that ARM styles write before it.

    GNU numbers back-references from zero over every type seen so far; ARM and lucid
    number from one and only over the arguments, which is why `demangle_signature`
    forgets the remembered types when it sees the `F`. Both are handled here by the
    subtraction below.
    """
    need_comma = False
    arg = _Buf()

    if work.params:
        declp.append("(")
        if cur.done():
            declp.append("void")

    while (cur.at() not in ("_", "", "e")) or work.nrepeats > 0:
        if cur.at() in ("N", "T"):
            temptype = cur.at()
            cur.advance()

            if temptype == "N":
                ok, repeats = get_count(cur)
                if not ok:
                    return 0
            else:
                repeats = 1

            if (work.hp or work.arm or work.edg) and len(work.typevec) >= 10:
                # Ten or more remembered types: the whole count is consumed, the reference's
                # reading of an ambiguous form.
                index = consume_count(cur)
                if index <= 0:
                    return 0
            else:
                ok, index = get_count(cur)
                if not ok:
                    return 0
            if work.lucid or work.arm or work.hp or work.edg:
                index -= 1
            if index < 0 or index >= len(work.typevec):
                return 0
            while True:
                # `work->nrepeats > 0 || --r >= 0`
                if work.nrepeats <= 0:
                    repeats -= 1
                    if repeats < 0:
                        break
                inner = _Cur(work.typevec[index])
                if need_comma and work.params:
                    declp.append(", ")
                work.proctypevec.append(index)
                if not do_arg(work, inner, arg):
                    work.proctypevec.pop()
                    return 0
                work.proctypevec.pop()
                if work.params:
                    declp.append(arg.s)
                if capture is not None:
                    capture.append(arg.s)
                need_comma = True
        else:
            if need_comma and work.params:
                declp.append(", ")
            if not do_arg(work, cur, arg):
                return 0
            if work.params:
                declp.append(arg.s)
            if capture is not None:
                capture.append(arg.s)
            need_comma = True

    if cur.at() == "e":
        cur.advance()
        if work.params:
            if need_comma:
                declp.append(",")
            declp.append("...")
        if capture is not None:
            capture.append("...")
        if cur.at() not in ("", "_"):
            # `e` ends the list: only the end or a return type may follow.
            return 0

    if work.params:
        declp.append(")")
    return 1


def demangle_nested_args(work, cur, declp):
    """The argument list of a function *type*, which remembers nothing."""
    work.forgetting_types += 1
    saved_previous_argument = work.previous_argument
    saved_nrepeats = work.nrepeats
    work.previous_argument = None
    work.nrepeats = 0

    result = demangle_args(work, cur, declp)

    work.previous_argument = saved_previous_argument
    work.forgetting_types -= 1
    work.nrepeats = saved_nrepeats
    return result


def demangle_function_name(work, cur, declp, scan):
    """Everything before the `__` at `scan`, read as a function name.

    That name may be an operator (`__pl`, `op$plus`, `__opi` for a conversion), an
    ARM-style constructor or destructor marker (`__ct`, `__dt`), or an ordinary
    identifier. Returns 1 unless what came out is unusable.
    """
    declp.append(cur.s[cur.i : scan])
    cur.i = scan + 2

    if work.hp and cur.at() == "X":
        # `foo__Xt1t2_Ft3t4`: an HP template function, whose arguments come first.
        demangle_arm_hp_template(work, cur, 0, declp)

    if work.lucid or work.arm or work.hp or work.edg:
        # An ARM structor's class is not known until the signature has been read.
        if declp.s == "__ct":
            work.constructor += 1
            declp.clear()
            work.evidence.add("structor")
            return 1
        if declp.s == "__dt":
            work.destructor += 1
            declp.clear()
            work.evidence.add("structor")
            return 1

    name = declp.s
    if len(name) >= 3 and name[0] == "o" and name[1] == "p" and name[2] in CPLUS_MARKERS:
        if len(name) >= 10 and name[3:10] == "assign_":
            spelling = _OPS.get(name[10:])
            if spelling is not None:
                work.evidence.add("operator")
                declp.clear()
                declp.append("operator")
                declp.append(spelling)
                declp.append("=")
        else:
            spelling = _OPS.get(name[3:])
            if spelling is not None:
                work.evidence.add("operator")
                declp.clear()
                declp.append("operator")
                declp.append(spelling)
    elif len(name) >= 5 and name[:4] == "type" and name[4] in CPLUS_MARKERS:
        inner = _Cur(name, 5)
        spelled = _Buf()
        if do_type(work, inner, spelled):
            work.evidence.add("operator")
            declp.clear()
            declp.append("operator ")
            declp.append(spelled.s)
    elif name[:4] == "__op":
        inner = _Cur(name, 4)
        spelled = _Buf()
        if do_type(work, inner, spelled):
            work.evidence.add("operator")
            declp.clear()
            declp.append("operator ")
            declp.append(spelled.s)
    elif len(name) >= 4 and name[0] == "_" and name[1] == "_" and name[2].islower() and name[3].islower():
        if len(name) == 4:
            spelling = _OPS.get(name[2:4])
            if spelling is not None:
                work.evidence.add("operator")
                declp.clear()
                declp.append("operator")
                declp.append(spelling)
        elif name[2] == "a" and len(name) == 5:
            spelling = _OPS.get(name[2:5])
            if spelling is not None:
                work.evidence.add("operator")
                declp.clear()
                declp.append("operator")
                declp.append(spelling)

    return 0 if declp.s == "." else 1


def demangle_signature(work, cur, declp):
    """Everything after the function name: the class, the arguments, the qualifiers.

    GNU mangling has no token marking where the argument list starts, so the loop below
    reads whatever it finds and sets `expect_func` when what it read means a function
    must follow. `func_done` records whether an argument list was ever read, because
    `bar__3foo` is `foo::bar(void)` and the `(void)` has to come from somewhere.
    """
    with _Depth(work):
        return _demangle_signature(work, cur, declp)


def _demangle_signature(work, cur, declp):
    success = 1
    func_done = False
    expect_func = False
    expect_return_type = False
    oldmangled = None
    start = cur.i

    while success and not cur.done():
        code = cur.at()
        if code == "Q":
            oldmangled = cur.i
            success = demangle_qualified(work, cur, declp, 1, 0)
            if success:
                work.remember_type(cur.s[oldmangled : cur.i])
            if work.auto or work.gnu:
                expect_func = True
            oldmangled = None
        elif code == "K":
            oldmangled = cur.i
            success = demangle_qualified(work, cur, declp, 1, 0)
            if work.auto or work.gnu:
                expect_func = True
            oldmangled = None
        elif code == "S":
            if oldmangled is None:
                oldmangled = cur.i
            cur.advance()
            work.static_type = 1
        elif code in ("C", "V", "u"):
            work.type_quals |= _QUALIFIER_CODES[code]
            if oldmangled is None:
                oldmangled = cur.i
            cur.advance()
        elif code == "L":
            # HP writes a local class as `Lnnn_`; nobody else writes an `L` here at all.
            if work.hp:
                while not cur.done() and cur.at() != "_":
                    cur.advance()
                if cur.done():
                    success = 0
                else:
                    cur.advance()
            else:
                success = 0
        elif _isdigit(code):
            if oldmangled is None:
                oldmangled = cur.i
            work.temp_start = -1  # uppermost call to demangle_class
            success = demangle_class(work, cur, declp)
            if success:
                work.remember_type(cur.s[oldmangled : cur.i])
            # EDG and others write the `F`, so the loop is left to come round to it.
            if (work.auto or work.gnu or work.edg) and cur.at() != "F":
                expect_func = True
            oldmangled = None
        elif code == "B":
            spelled = _Buf()
            success = do_type(work, cur, spelled)
            if success:
                spelled.append("::")
                declp.prepend(spelled.s)
            oldmangled = None
            expect_func = True
        elif code == "F":
            # ARM and HP write an explicit `F`; GNU implies it.
            oldmangled = None
            func_done = True
            cur.advance()
            if work.lucid or work.arm or work.hp or work.edg:
                work.forget_types()
            success = _demangle_args_capturing(work, cur, declp)
            if success and (work.auto or work.edg) and cur.at() == "_":
                cur.advance()
                # The return type is read and thrown away at this level.
                cur_return = _Buf()
                success = do_type(work, cur, cur_return)
        elif code == "t":
            trawname = _Buf()
            tname = _Buf()
            if oldmangled is None:
                oldmangled = cur.i
            success = demangle_template(work, cur, tname, trawname, 1, 1)
            if success:
                work.remember_type(cur.s[oldmangled : cur.i])
            tname.append("::")
            declp.prepend(tname.s)
            if work.destructor & 1:
                trawname.prepend("~")
                declp.append(trawname.s)
                work.destructor -= 1
            if (work.constructor & 1) or (work.destructor & 1):
                declp.append(trawname.s)
                work.constructor -= 1
            oldmangled = None
            expect_func = True
        elif code == "_":
            if (work.auto or work.gnu) and expect_return_type:
                cur.advance()
                return_type = _Buf()
                success = do_type(work, cur, return_type)
                return_type.blank()
                declp.prepend(return_type.s)
                work.return_type = return_type.s.rstrip()
            elif work.hp:
                # `_nnn` is HP's alternate entry point for a function; skip it.
                cur.advance()
                while _isdigit(cur.at()):
                    cur.advance()
            else:
                # A second `_` at the outermost level: not a name this reads.
                success = 0
        elif code == "H" and (work.auto or work.gnu):
            success = demangle_template(work, cur, declp, None, 0, 0)
            if not (work.constructor & 1):
                expect_return_type = True
            if cur.done():
                success = 0
            else:
                cur.advance()
        else:
            if work.auto or work.gnu:
                # Whatever this is, it is the first argument: GNU marks nothing.
                if cur.i == start:
                    work.evidence.add("unmarked")
                func_done = True
                success = _demangle_args_capturing(work, cur, declp)
            else:
                success = 0

        if success and expect_func:
            func_done = True
            if work.lucid or work.arm or work.edg:
                work.forget_types()
            success = _demangle_args_capturing(work, cur, declp)
            # A template carries its return type: no further argument list.
            expect_func = False

    if success and not func_done and (work.auto or work.gnu):
        # `bar__3foo` is `foo::bar(void)`; under ARM and HP it is a static data member.
        success = _demangle_args_capturing(work, cur, declp)

    if success and work.params:
        trailing = []
        if work.static_type:
            declp.append(" static")
            trailing.append("static")
        if work.type_quals:
            declp.blank()
            spelling = _QUALIFIER_STRINGS[work.type_quals]
            declp.append(spelling)
            trailing.extend(spelling.split())
        work.trailing = tuple(trailing)
    return success


def _demangle_args_capturing(work, cur, declp):
    """`demangle_args`, recording what the outermost call contributed.

    Not the reference's: it builds text and nothing else. The name as it stands *before*
    the arguments are appended is what `--no-params` and `Signature.qualified_name` want,
    and it cannot be recovered from the finished spelling, because a parameter type may
    contain parentheses of its own. The exact text the call appended is kept too, so
    that `nodes.build` can slice the tree out of the spelling rather than re-derive it.
    """
    if work.capture is not None:
        return demangle_args(work, cur, declp)
    before = declp.s
    capture = work.capture = {"name": before, "args": [], "text": ""}
    success = demangle_args(work, cur, declp, capture["args"])
    if declp.s.startswith(before):
        capture["text"] = declp.s[len(before) :]
    return success


def _find_double_underscore(s, start=0):
    at = s.find("__", start)
    return None if at < 0 else at


def demangle_prefix(work, cur, declp):
    """Consume everything before the signature, and read the function name out of it."""
    s = cur.s
    rest = cur.rest()

    if len(rest) > 6 and (rest.startswith("_imp__") or rest.startswith("__imp_")):
        # PE DLL import: `_imp__` (dlltool) or `__imp_` (older).
        cur.advance(6)
        work.dllimported = 1
    elif len(rest) >= 11 and rest.startswith("_GLOBAL_"):
        # A global constructor or destructor: the marker is written around `D` or `I`.
        if rest[8] in CPLUS_MARKERS and rest[8] == rest[10]:
            if rest[9] == "D":
                cur.advance(11)
                work.destructor = 2
                if gnu_special(work, cur, declp):
                    return 1
            elif rest[9] == "I":
                cur.advance(11)
                work.constructor = 2
                if gnu_special(work, cur, declp):
                    return 1
    elif (work.arm or work.hp or work.edg) and rest.startswith("__std__"):
        cur.advance(7)
        work.destructor = 2
    elif (work.arm or work.hp or work.edg) and rest.startswith("__sti__"):
        cur.advance(7)
        work.constructor = 2

    scan = _find_double_underscore(s, cur.i)

    if scan is not None:
        # Start at the *last* pair of a run of underscores.
        run = 0
        while scan + run < len(s) and s[scan + run] == "_":
            run += 1
        if run > 2:
            scan += run - 2

    if scan is None:
        return _prefix_fallback(work, cur, declp, 0)

    def ch(at):
        return s[at] if 0 <= at < len(s) else ""

    if work.static_type:
        # Reached only from a nested demangling that had already seen an `S`.
        if not _isdigit(ch(scan)) and ch(scan) != "t":
            return _prefix_fallback(work, cur, declp, 0)
        return 1
    if scan == cur.i and (_isdigit(ch(scan + 2)) or ch(scan + 2) in ("Q", "t", "K", "H")):
        if (work.lucid or work.arm or work.hp) and _isdigit(ch(scan + 2)):
            # cfront's `__<nesting level>` before a local variable; not in the ARM.
            cur.i = scan + 2
            consume_count(cur)
            declp.append(cur.rest())
            cur.i = len(s)
            return 1
        # GNU constructor `__[0-9Qt]` or `__H`; cfront's `__Q2_3foo3bar` is a nested type.
        if not (work.lucid or work.arm or work.hp or work.edg):
            work.constructor += 1
            work.evidence.add("structor")
        cur.i = scan + 2
    elif (work.arm and ch(scan + 2) == "p" and ch(scan + 3) == "t") or (
        # cfront `__pt__`, EDG `__tm__`/`__ps__`/`__pt__`: a parameterised type.
        work.edg and ch(scan + 2) + ch(scan + 3) in ("tm", "ps", "pt")
    ):
        demangle_arm_hp_template(work, cur, len(cur.rest()), declp)
        return 1
    elif scan == cur.i and not _isdigit(ch(scan + 2)) and ch(scan + 2) != "t":
        # Skip the leading underscores, then find the separating `__`.
        if not (work.arm or work.lucid or work.hp or work.edg) or arm_special(work, cur, declp) == 0:
            while ch(scan) == "_":
                scan += 1
            found = _find_double_underscore(s, scan)
            if found is None or found + 2 >= len(s):
                # `__not_mangled`, or `__not_mangled_either__`.
                return _prefix_fallback(work, cur, declp, 0)
            return iterate_demangle_function(work, cur, declp, found)
        return 1
    elif scan + 2 < len(s):
        # A `__` somewhere in the middle with something after it: a global function.
        return iterate_demangle_function(work, cur, declp, scan)
    else:
        return _prefix_fallback(work, cur, declp, 0)

    return 1


def _prefix_fallback(work, cur, declp, success):
    """A global constructor or destructor keeps its key even when nothing else parsed."""
    if not success and (work.constructor == 2 or work.destructor == 2):
        declp.append(cur.rest())
        cur.i = len(cur.s)
        return 1
    return success


def iterate_demangle_function(work, cur, declp, scan):
    """Try each `__` in turn as the one separating the name from the signature.

    GNU mangling is ambiguous: a name may contain `__` and so may a signature, and
    nothing says which run of underscores is the separator. The reference guesses at the
    first, demangles the whole signature to find out, and backtracks over the entire
    `work_stuff` when the guess does not parse.
    """
    s = cur.s
    mangle_init = cur.i
    success = 0

    if scan + 2 >= len(s):
        return 0

    # Most names have only one `__`, and the ARM-family styles are unambiguous anyway.
    if work.arm or work.lucid or work.hp or work.edg or s.find("__", scan + 2) < 0:
        return demangle_function_name(work, cur, declp, scan)

    decl_init = declp.s
    work_init = work.snapshot()

    while scan + 2 < len(s):
        if demangle_function_name(work, cur, declp, scan):
            success = demangle_signature(work, cur, declp)
            if success:
                break

        cur.i = mangle_init
        declp.clear()
        declp.append(decl_init)
        work.restore(work_init)

        # Leave this run of underscores, find the next, and move to its last pair.
        scan += 2
        while scan < len(s) and not (s[scan] == "_" and scan + 1 < len(s) and s[scan + 1] == "_"):
            scan += 1
        while scan < len(s) and s[scan] == "_":
            scan += 1
        scan -= 2

    return success


def gnu_special(work, cur, declp):
    """The GNU forms that are not names with signatures at all.

    `_$_3foo` is a destructor, `__vt_foo` a virtual table, `_3foo$var` a static data
    member, `__thunk_8__$_7ostream` a thunk, `__ti3foo` a `type_info` node.
    """
    s = cur.s
    rest = cur.rest()
    success = 1

    if len(rest) > 2 and rest[0] == "_" and rest[1] in CPLUS_MARKERS and rest[2] == "_":
        cur.advance(3)
        work.destructor += 1
        return 1

    if rest.startswith("__vt_") or (rest[:3] == "_vt" and len(rest) > 3 and rest[3] in CPLUS_MARKERS):
        cur.advance(5 if rest[2] == "v" else 4)
        while not cur.done():
            code = cur.at()
            if code in ("Q", "K"):
                success = demangle_qualified(work, cur, declp, 0, 1)
            elif code == "t":
                success = demangle_template(work, cur, declp, None, 1, 1)
            else:
                n = None
                if _isdigit(code):
                    n = consume_count(cur)
                    if not cur._ascii and 0 < n <= len(cur.rest().encode("utf-8", errors="surrogatepass")):
                        n = cur.byte_count(n)
                    # Too large: a `.<digits>` static local marker. The reference's
                    # `break` leaves only the `switch`, so the loop goes on: `_vt.6i` is
                    # `i virtual table`.
                    if n > len(cur.rest()):
                        success = 1
                        n = None
                    elif n == -1:
                        success = 0
                        break
                else:
                    n = 0
                    while cur.i + n < len(s) and s[cur.i + n] not in CPLUS_MARKERS:
                        n += 1
                if n is not None:
                    declp.append(cur.take(n))

            marker = _find_marker(s, cur.i)
            if success and (marker is None or marker == cur.i):
                if marker is not None:
                    declp.append("::")
                    cur.advance()
            else:
                success = 0
                break
        if not success:
            _refuse_special("a virtual table whose class does not read")
        declp.append(" virtual table")
        work.suffix = " virtual table"
        return 1

    if rest[:1] == "_" and rest[1:2] and rest[1] in "0123456789Qt" and _find_marker(s, cur.i) is not None:
        marker = _find_marker(s, cur.i)
        cur.advance()
        code = cur.at()
        if code in ("Q", "K"):
            success = demangle_qualified(work, cur, declp, 0, 1)
        elif code == "t":
            success = demangle_template(work, cur, declp, None, 1, 1)
        else:
            n = cur.byte_count(consume_count(cur))
            if n < 0 or n > len(cur.rest()):
                return 0
            here = cur.rest()
            if (
                n > 10
                and here.startswith("_GLOBAL_")
                and len(here) > 10
                and here[9] == "N"
                and here[8] == here[10]
                and here[8] in CPLUS_MARKERS
            ):
                # the anonymous namespace; its key only makes the symbol unique
                declp.append("{anonymous}")
                cur.advance(n)
                marker = _find_marker(s, cur.i)
            else:
                declp.append(cur.take(n))
        if success and marker == cur.i:
            cur.advance()
            declp.append("::")
            declp.append(cur.rest())
            cur.i = len(s)
            return 1
        return 0

    if rest.startswith("__thunk_"):
        cur.advance(8)
        # gcc 2.95's `make_thunk` marks a positive delta with `n` (`__thunk_n8_`), which
        # libiberty misreads as part of the method name; the compiler is followed.
        positive = cur.at() == "n"
        if positive:
            cur.advance()
        delta = consume_count(cur)
        if delta == -1 or cur.at() != "_":
            _refuse_special("a thunk with no delta")
        cur.advance()
        method = _demangle_nested(work, cur.rest())
        if method is None:
            _refuse_special("a thunk whose method does not read")
        phrase = f"virtual function thunk (delta:{delta if positive else -delta}) for "
        declp.append(phrase)
        declp.append(method)
        work.special = phrase
        cur.i = len(s)
        return 1

    if rest.startswith("__ti") or rest.startswith("__tf"):
        suffix = " type_info node" if rest[3] == "i" else " type_info function"
        cur.advance(4)
        code = cur.at()
        if code in ("Q", "K"):
            success = demangle_qualified(work, cur, declp, 0, 1)
        elif code == "t":
            success = demangle_template(work, cur, declp, None, 1, 1)
        else:
            success = do_type(work, cur, declp)
        if success and not cur.done():
            success = 0
        if not success:
            _refuse_special("a type_info name whose type does not read")
        declp.append(suffix)
        work.suffix = suffix
        return 1

    return 0


def _refuse_special(reason):
    """Refuse a name whose special-form prefix is unambiguous but whose body does not read.

    `gnu_special` advances the cursor as it reads, and the reference goes on from
    wherever a failed attempt stopped, so `demangle_prefix` then reads the *tail* of the
    name as a function: `_vt$t8BDDHookV1__pt__2_cFv` is `_c::_pt(void)` to libiberty,
    and `_vt$t3Foo1Z_bar__Fi` is `_bar(int)`. A function named after the end of a
    virtual table's symbol is not a reading of that symbol. A `_vt`, `__vt_`, `__thunk_`,
    `__ti` or `__tf` prefix says what the name is, so a body that does not read as that
    is refused rather than read as something else. No vector in the corpus is touched.
    """
    raise DemangleFailure(reason)


def _find_marker(s, start):
    """The next `$` or `.` at or after `start`, or None."""
    for at in range(start, len(s)):
        if s[at] in CPLUS_MARKERS:
            return at
    return None


def arm_special(work, cur, declp):
    """`__vtbl__3foo__3bar`: the ARM and lucid spelling of a virtual table."""
    s = cur.s
    if not cur.rest().startswith(ARM_VTABLE_STRING):
        return 0

    # Check the whole thing reads before writing any of it.
    scan = _Cur(s, cur.i + len(ARM_VTABLE_STRING))
    while not scan.done():
        n = consume_count(scan)
        if n == -1:
            return 0
        scan.advance(scan.byte_count(n))
        if scan.at() == "_" and scan.at(1) == "_":
            scan.advance(2)

    cur.advance(len(ARM_VTABLE_STRING))
    while not cur.done():
        n = cur.byte_count(consume_count(cur))
        if n == -1 or n > len(cur.rest()):
            return 0
        declp.prepend(cur.take(n))
        if cur.at() == "_" and cur.at(1) == "_":
            declp.prepend("::")
            cur.advance(2)
    declp.append(" virtual table")
    work.evidence.add("special")
    work.suffix = " virtual table"
    return 1


class GnuV2Symbol:
    """What one of these names says, spelled and in pieces.

    `text` is the whole of it, and the rest is what the parts-in-order tree is built
    from. `parameters` is None where the name encodes no argument list -- a static data
    member, a virtual table -- which is not the same as an empty one.
    """

    __slots__ = (
        "arguments_text",
        "evidence",
        "parameters",
        "qualified_name",
        "qualifiers",
        "return_type",
        "special",
        "style",
        "suffix",
        "text",
    )

    def __init__(
        self,
        text,
        style,
        qualified_name,
        parameters,
        arguments_text,
        return_type,
        qualifiers,
        special,
        suffix,
        evidence,
    ):
        self.text = text
        self.style = style
        self.qualified_name = qualified_name
        self.parameters = parameters
        #: What the argument list contributed to `text`, or ""; `nodes.build` slices at it.
        self.arguments_text = arguments_text
        self.return_type = return_type
        self.qualifiers = qualifiers
        self.special = special
        self.suffix = suffix
        #: `class`, `qualified`, `template`, `operator`, `structor`, `special`; empty
        #: is no evidence to `detect`. `unmarked` alone (arguments with no `F` and no
        #: class before them) is evidence against.
        self.evidence = evidence

    def __repr__(self):
        return f"GnuV2Symbol({self.text!r}, style={self.style!r})"


def _internal_demangle(work, mangled):
    """`internal_cplus_demangle`: one name, against the state `work` already carries."""
    saved = (work.constructor, work.destructor, work.static_type, work.type_quals)
    work.constructor = 0
    work.destructor = 0
    work.type_quals = 0
    work.dllimported = 0

    demangled = None
    if mangled:
        declp = _Buf()
        cur = _Cur(mangled)
        success = 0

        # GNU special forms first, since `_$_5__foo` has a `__`. One cursor throughout, as
        # the reference does, except where `_refuse_special` refuses.
        if work.auto or work.gnu:
            success = gnu_special(work, cur, declp)
            if success:
                work.evidence.add("special")
            if not success:
                work.delete_all()
                declp.clear()

        if not success:
            success = demangle_prefix(work, cur, declp)
        if success and not cur.done():
            success = demangle_signature(work, cur, declp)

        if work.constructor == 2:
            phrase = "global constructors keyed to "
            declp.prepend(phrase)
            work.special = phrase
            work.evidence.add("special")
            work.constructor = 0
        elif work.destructor == 2:
            phrase = "global destructors keyed to "
            declp.prepend(phrase)
            work.special = phrase
            work.evidence.add("special")
            work.destructor = 0
        elif work.dllimported == 1:
            phrase = "import stub for "
            declp.prepend(phrase)
            work.special = phrase
            work.evidence.add("special")
            work.dllimported = 0

        work.delete_non_bk()
        if success:
            demangled = declp.s

    work.constructor, work.destructor, work.static_type, work.type_quals = saved
    return demangled


def _demangle_fresh(work, name):
    """A whole separate name, demangled with nothing carried over but the style.

    The reference calls the public `cplus_demangle` here rather than recursing, so that
    none of the remembered types or squangling vectors apply to it.
    """
    if work.nested >= MAX_NESTED:
        raise _Overflow("nested demangling too deep")
    inner = _Work(work.style, params=work.params, ansi=work.ansi)
    inner.nested = work.nested + 1
    inner.depth = work.depth
    return _internal_demangle(inner, name)


def _demangle_nested(work, name):
    """`__thunk_`'s target: the same `work`, as the reference passes it."""
    if work.nested >= MAX_NESTED:
        raise _Overflow("nested demangling too deep")
    work.nested += 1
    try:
        return _internal_demangle(work, name)
    finally:
        work.nested -= 1


def demangle_gnuv2(mangled, style="gnu", params=True, ansi=True):
    """Read one pre-Itanium C++ name. Raises `DemangleFailure` where it is not one.

    An unknown `style` is the caller's mistake rather than the name's, and raises
    `ValueError`.
    """
    if style not in STYLES:
        raise ValueError(f"unknown style {style!r}; expected one of {', '.join(STYLES)}")
    work = _Work(style, params=params, ansi=ansi)
    try:
        text = _internal_demangle(work, mangled)
    except RecursionError as error:
        raise _Overflow("nested too deeply") from error
    if text is None:
        raise DemangleFailure(f"not a {style} C++ name")
    capture = work.capture
    return GnuV2Symbol(
        text=text,
        style=style,
        qualified_name=(capture["name"] if capture else text),
        parameters=(tuple(capture["args"]) if capture else None),
        arguments_text=(capture["text"] if capture else ""),
        return_type=work.return_type,
        qualifiers=work.trailing,
        special=work.special,
        suffix=work.suffix,
        evidence=frozenset(work.evidence),
    )
