#!/usr/bin/env python3
"""Record what `UnDecorateSymbolName`'s mask bits change, over LLVM's MSVC corpus.

`llvm-undname` has five suppression flags and `MsvcOptions` already carries all five,
scored against it in `tests/conformance/msvc-suppressions.txt`. Microsoft's mask has more
in it than those five, and for the rest there is exactly one reference: `dbghelp.dll` on
a Windows machine. This drives it.

Why the output is not simply what `dbghelp` printed
---------------------------------------------------
The two references do not spell the *same* name alike, so a `dbghelp` answer cannot be
dropped into a corpus whose baseline is `llvm-undname`'s. Of the 605 names it reads, they
differ on 380, in ways that are all about spacing and none about what a flag removes:

    llvm-undname                       dbghelp
    void foo_pad(char *)               void foo_pad(char * __ptr64)
    foo(double (*)[3], double)         foo(double (*)[3],double)
    char *const                        char * const
    char **                            char * *
    Foo::bar(void) const               Foo::bar(void)const
    int (__cdecl *)(void)              int (__cdecl*)(void)
    void (__cdecl * __cdecl f(void))() void (__cdecl*__cdecl f(void))()

So `dbghelp`'s answer is put through the rewrite in `RULES` below -- eight lexical
substitutions and a strip -- and then the rewrite is *proved*, per name, before anything
that name produced is kept: the rewritten baseline has to equal what this library already
prints for it, which is what `msvc-llvm-corpus.txt` pins against `llvm-undname`. A name
where it does not is dropped from the corpus rather than guessed at, and `--report` says
how many and why. What survives is the reference's own decision about what the flag
removes, re-spelled by a rule set that name proved.

Then the rows are checked a second time, against what the flag *promises*: a recorded
answer may differ from the unflagged spelling only in the words that flag names, and may
not carry a spacing no house spelling has. A row that fails is reported rather than
written, so a rule that is wrong shows up as a complaint and never as a wrong expectation.

478 of the 609 names qualify. Four the reference declines to read at all; the other 127
are names where the two genuinely disagree about spelling rather than about spacing --
`char *const __restrict` against `char *__restrict const`, which is an ordering, not a
gap. Inventing a rule to reconcile those would be inventing a spelling, which is the one
thing this corpus exists to avoid.

Usage
-----
    python tools/generate_msvc_dbghelp_corpus.py            # write the corpus
    python tools/generate_msvc_dbghelp_corpus.py --report   # say what was found, write nothing
    python tools/generate_msvc_dbghelp_corpus.py --check    # fail if the checked-in file is stale

Windows only: it needs `dbghelp.dll`. The corpus it writes is checked in, so the test
suite replaying it needs neither Windows nor `dbghelp`.
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import demangle  # noqa: E402

CORPUS = ROOT / "tests" / "conformance" / "msvc-llvm-corpus.txt"
SUPPRESSIONS = ROOT / "tests" / "conformance" / "msvc-suppressions.txt"
TARGET = ROOT / "tests" / "conformance" / "msvc-dbghelp.txt"
NAME_ONLY_TARGET = ROOT / "tests" / "conformance" / "msvc-name-only.txt"

#: `UNDNAME_NAME_ONLY`. Not in `IMPLEMENTED`: it is not a suppression that composes with
#: the others but a whole reduced spelling, and what it reduces to is not what this
#: library's `signature().qualified_name` answers. The file it writes is a *comparison*
#: rather than an expectation -- see `write_name_only`.
NAME_ONLY = 0x1000

#: The mask bits this library implements a field for, and the name the middle column
#: gives each. Named for the `dbghelp` flag rather than for the field, because the
#: reference is what the column records.
#:
#: `UNDNAME_NO_THISTYPE` is 0x60 -- `UNDNAME_NO_MS_THISTYPE | UNDNAME_NO_CV_THISTYPE` --
#: and has to be passed as the pair. Neither half changes anything on its own; see
#: `OTHER_BITS`.
IMPLEMENTED = (
    ("no-leading-underscores", 0x00001, "UNDNAME_NO_LEADING_UNDERSCORES"),
    ("no-ms-keywords", 0x00002, "UNDNAME_NO_MS_KEYWORDS"),
    ("no-this-type", 0x00060, "UNDNAME_NO_THISTYPE"),
    ("no-tag-kind", 0x08000, "UNDNAME_NO_ECSU"),
)

#: The bits that mean what one of `llvm-undname`'s five flags means. Not written to the
#: corpus -- `msvc-suppressions.txt` already scores those five against `llvm-undname` --
#: but compared against it, because two references disagreeing about a flag both claim to
#: implement is a finding rather than a detail. `--report` prints the comparison.
#:
#: `--no-variable-type` is absent because the mask has nothing that means it.
CROSS_CHECKED = (
    ("no-calling-convention", 0x00010, "UNDNAME_NO_ALLOCATION_LANGUAGE"),
    ("no-return-type", 0x00004, "UNDNAME_NO_FUNCTION_RETURNS"),
    ("no-access-specifier", 0x00080, "UNDNAME_NO_ACCESS_SPECIFIERS"),
    ("no-member-type", 0x00200, "UNDNAME_NO_MEMBER_TYPE"),
)

#: Every other documented bit, with what it is supposed to do. `--report` asks each one
#: over the whole corpus and says which changed nothing, so "we did not implement this"
#: is backed by a measurement rather than by an omission.
OTHER_BITS = (
    ("UNDNAME_NO_ALLOCATION_MODEL", 0x00008),
    ("UNDNAME_NO_MS_THISTYPE", 0x00020),
    ("UNDNAME_NO_CV_THISTYPE", 0x00040),
    ("UNDNAME_NO_THROW_SIGNATURES", 0x00100),
    ("UNDNAME_NO_RETURN_UDT_MODEL", 0x00400),
    ("UNDNAME_32_BIT_DECODE", 0x00800),
    ("UNDNAME_NAME_ONLY", 0x01000),
    ("UNDNAME_NO_ARGUMENTS", 0x02000),
    ("UNDNAME_NO_SPECIAL_SYMS", 0x04000),
    ("UNDNAME_NO_IDENT_CHAR_CHECK", 0x10000),
    ("UNDNAME_NO_PTR64", 0x20000),
)

_CONVENTIONS = "cdecl|stdcall|fastcall|thiscall|vectorcall|clrcall|eabi|pascal|swiftcall|regcall"

#: A calling convention or a trailing qualifier, spelled either way. The underscores are
#: optional because `UNDNAME_NO_LEADING_UNDERSCORES` takes them off, and the rules below
#: have to place a `cdecl` the same way they place a `__cdecl` or that flag's answers come
#: out spaced differently from every other flag's. A bare `cdecl` or `restrict` is never
#: anything else here: the reference writes neither without underscores under any other
#: bit, and an identifier of that name would have to stand immediately before a `*`.
_ANY_CONVENTION = rf"(?:__)?(?:{_CONVENTIONS})"
_ANY_QUALIFIER = r"(?:const|volatile|(?:__)?restrict)"

#: How a `dbghelp` spelling is rewritten into the one this library prints, in order. Each
#: is a spacing convention the two references chose differently; none of them adds,
#: removes or reorders anything that is part of the declaration. Every rule is proved on
#: every name it is used for -- see the module docstring -- so a rule that is wrong shows
#: up as names dropped, never as a wrong answer recorded.
RULES = (
    # `llvm-undname` does not print `__ptr64` at all. `UNDNAME_NO_PTR64` would say so, but
    # this `dbghelp` ignores that bit; see `OTHER_BITS` and `--report`.
    ("drop-ptr64", lambda text: text.replace(" __ptr64", "")),
    ("space-after-comma", lambda text: re.sub(r",(?! )", ", ", text)),
    # `char *const`, not `char * const`
    ("abut-qualifier", lambda text: re.sub(rf"(?<=[*&]) (?={_ANY_QUALIFIER}\b)", "", text)),
    # `char **`, not `char * *`
    ("abut-sigil", lambda text: re.sub(r"(?<=[*&]) (?=[*&])", "", text)),
    # `f(void) const`, not `f(void)const `
    ("space-before-member-cv", lambda text: re.sub(r"\)(const|volatile)\b", r") \1", text)),
    # `int (__cdecl *)(void)`, not `int (__cdecl*)(void)`
    ("space-after-convention", lambda text: re.sub(rf"\b({_ANY_CONVENTION})(?=[*&])", r"\1 ", text)),
    # `void (__cdecl * __cdecl fn(void))(int)`, not `void (__cdecl *__cdecl fn(void))(int)`.
    # The convention on the other side of the sigil is the one the *declaration* carries,
    # and it is written there because that is where the declarator goes.
    ("space-before-convention", lambda text: re.sub(rf"(?<=[*&])(?={_ANY_CONVENTION}\b)", " ", text)),
    # The reference leaves standing the space a keyword it dropped occupied: `void (
    # media::C::*&&` where the house spelling is `void (media::C::*&&`. No house spelling
    # writes `(`, a space and then a letter, so closing that up cannot disturb `int ( *)()`
    # -- the space there stands for a calling convention spelled with nothing at all.
    ("close-dropped-keyword", lambda text: re.sub(r"\( (?=[A-Za-z_])", "(", text)),
    ("strip", lambda text: text.rstrip()),
)

#: `UNDNAME_NO_LEADING_UNDERSCORES` respells `__ptr64` as `ptr64`, so the rule that drops
#: the keyword has to know which flag produced the text it is reading or it walks straight
#: past it. Nothing else in the mask respells a keyword, which is why this is a parameter
#: to `normalise` rather than another entry in `RULES`.
_STRIPPED_PTR64 = re.compile(r" ptr64\b")


def load_undecorator():
    """`UnDecorateSymbolName`, bound, plus the `dbghelp.dll` version behind it.

    The version is recorded in the corpus header because these spellings have changed
    between Windows SDK releases, and a column with no version on it is a column nobody
    can re-derive.
    """
    if sys.platform != "win32":
        sys.exit("this needs dbghelp.dll, so it needs Windows")
    import ctypes
    from ctypes import wintypes

    try:
        dbghelp = ctypes.WinDLL("dbghelp.dll")
        undecorate = dbghelp.UnDecorateSymbolName
    except (OSError, AttributeError) as error:
        sys.exit(f"could not reach UnDecorateSymbolName: {error}")
    undecorate.argtypes = [ctypes.c_char_p, ctypes.c_char_p, wintypes.DWORD, wintypes.DWORD]
    undecorate.restype = wintypes.DWORD

    # The buffer is fixed and generous. UnDecorateSymbolName truncates rather than
    # reporting that it needed more room, so a name that fills it would be recorded
    # half-spelled; `undname` below refuses anything that reaches the end instead.
    size = 1 << 16

    def undname(mangled, mask=0):
        """What the reference prints for `mangled` under `mask`, or None if it declined.

        A name it cannot read comes back as the input, which is how it reports failure.
        """
        buffer = ctypes.create_string_buffer(size)
        written = undecorate(mangled.encode("utf-8"), buffer, size, mask)
        if not written:
            return None
        spelled = buffer.value.decode("utf-8", "replace")
        if len(spelled) >= size - 1:
            return None
        return None if spelled == mangled else spelled

    return undname, _dll_version(dbghelp._handle)


def _dll_version(handle):
    """The file version of the `dbghelp.dll` that was actually loaded, or "unknown".

    Asked of the loaded module rather than of `System32`, because `WinDLL` follows the
    ordinary search order and a debugger on PATH may well ship a copy of its own.
    Recording the version of a file the run did not use would be worse than recording
    nothing: the column exists so that someone else can re-derive it.
    """
    import ctypes
    from ctypes import wintypes

    path = ctypes.create_unicode_buffer(1024)
    if not ctypes.windll.kernel32.GetModuleFileNameW(wintypes.HMODULE(handle), path, 1024):
        return "unknown"
    dll = path.value
    version = ctypes.windll.version
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    size = version.GetFileVersionInfoSizeW(dll, None)
    if not size:
        return "unknown"
    block = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(dll, 0, size, block):
        return "unknown"
    info = ctypes.c_void_p()
    length = wintypes.UINT()
    if not version.VerQueryValueW(block, "\\", ctypes.byref(info), ctypes.byref(length)):
        return "unknown"

    class FixedFileInfo(ctypes.Structure):
        _fields_ = [
            (name, wintypes.DWORD)
            for name in (
                "signature",
                "struct_version",
                "file_version_ms",
                "file_version_ls",
                "product_version_ms",
                "product_version_ls",
                "flags_mask",
                "flags",
                "os",
                "type",
                "subtype",
                "date_ms",
                "date_ls",
            )
        ]

    fixed = ctypes.cast(info, ctypes.POINTER(FixedFileInfo)).contents
    high, low = fixed.file_version_ms, fixed.file_version_ls
    return f"{high >> 16}.{high & 0xFFFF}.{low >> 16}.{low & 0xFFFF}"


def normalise(text, flag=None):
    """A `dbghelp` spelling, rewritten into the one this library prints.

    `flag` is the mask bit that produced `text`, where one did. Only
    `no-leading-underscores` needs saying, and only because it respells `__ptr64`.
    """
    if flag == "no-leading-underscores":
        text = _STRIPPED_PTR64.sub("", text)
    for _, rule in RULES:
        text = rule(text)
    return text


def corpus_names():
    """The decorated names of `msvc-llvm-corpus.txt`, in file order."""
    names = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "\t" in line:
            names.append(line.split("\t", 1)[0])
    return names


def llvm_answers():
    """What `llvm-undname` printed under each of its five flags, by (name, flag)."""
    recorded = {}
    for line in SUPPRESSIONS.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and line.count("\t") >= 2:
            mangled, flag, spelled = line.split("\t", 2)
            recorded[(mangled, flag)] = spelled
    return recorded


def survey(undname, names):
    """For every name: what this library prints, and whether the rewrite reproduces it.

    `comparable` is the subset the corpus may draw on -- the names where the rewritten
    `dbghelp` baseline landed exactly on the house spelling, which is the proof that the
    rewrite is right for that name.
    """
    house, reference, comparable, declined = {}, {}, [], []
    for name in names:
        spelled = undname(name)
        if spelled is None:
            declined.append(name)
            continue
        reference[name] = spelled
        house[name] = demangle.demangle(name)
        if normalise(spelled) == house[name]:
            comparable.append(name)
    return house, reference, comparable, declined


#: What each flag is allowed to take out of a spelling, and what it may put back. The
#: check below reads both texts as tokens and compares the multisets, so a rewrite that
#: left a space in the wrong place, or a `ptr64` the rule that drops the keyword walked
#: past, shows up as a token that moved rather than as a file nobody re-read.
#:
#: The point is not to re-derive the reference's answer -- it is to notice when the answer
#: is not the shape the flag promises, which is either an artefact of the rewrite or the
#: reference doing something worth knowing about. Either way it gets reported, never
#: silently recorded.
_KEYWORDS = frozenset(
    {
        "__cdecl",
        "__pascal",
        "__thiscall",
        "__stdcall",
        "__fastcall",
        "__clrcall",
        "__eabi",
        "__vectorcall",
        "__restrict",
        "__unaligned",
        "__ptr64",
    }
)
_STRIPPED = frozenset(word.lstrip("_") for word in _KEYWORDS)
_TAGS = frozenset({"class", "struct", "union", "enum"})
_THIS_GROUP = frozenset({"const", "volatile", "__restrict", "__unaligned", "&", "&&"})
DELTA = {
    "no-ms-keywords": (_KEYWORDS, frozenset()),
    "no-leading-underscores": (_KEYWORDS, _STRIPPED),
    "no-this-type": (_THIS_GROUP, frozenset()),
    "no-tag-kind": (_TAGS, frozenset()),
}

# A bit added to IMPLEMENTED without a line here would record its rows with no check at
# all, which is the one failure mode this file is arranged to prevent. Said out loud
# rather than left to a KeyError halfway through a run.
assert set(DELTA) == {flag for flag, _, _ in IMPLEMENTED}, "every implemented bit needs a DELTA entry"

_TOKEN = re.compile(r"[A-Za-z_$][\w$]*|<<|>>|::|&&|\S")

#: Spacings no house spelling has. A recorded row with one in it is the rewrite having
#: missed something, not the reference having an opinion.
_ARTEFACTS = (
    ("a doubled space", re.compile(r"  ")),
    ("a space before `)`", re.compile(r" \)")),
    ("a space after `(`, before a name", re.compile(r"\( (?=[A-Za-z_])")),
    ("split sigils", re.compile(r"[*&] [*&]")),
    ("a space before `,`", re.compile(r" ,")),
    ("trailing whitespace", re.compile(r"\s$")),
)


def objection(flag, house, spelled):
    """Why `spelled` may not be recorded for `flag`, or None if there is no reason."""
    for label, pattern in _ARTEFACTS:
        if pattern.search(spelled):
            return f"the rewrite left {label}"
    removed = Counter(_TOKEN.findall(house)) - Counter(_TOKEN.findall(spelled))
    added = Counter(_TOKEN.findall(spelled)) - Counter(_TOKEN.findall(house))
    may_remove, may_add = DELTA[flag]
    unexpected = sorted(token for token in removed if token not in may_remove)
    if unexpected:
        return f"it also drops {', '.join(unexpected)}"
    surprising = sorted(token for token in added if token not in may_add)
    if surprising:
        return f"it also adds {', '.join(surprising)}"
    return None


def rows_for(undname, house, reference, comparable, flag, mask):
    """Every comparable name the bit changes, with the reference's answer rewritten.

    Returns the rows and, beside them, the names the check above turned down.
    """
    rows, refused = [], []
    for name in comparable:
        spelled = undname(name, mask)
        if spelled is None or spelled == reference[name]:
            continue
        rewritten = normalise(spelled, flag)
        complaint = objection(flag, house[name], rewritten)
        if complaint is not None:
            refused.append((name, rewritten, complaint))
            continue
        rows.append((name, rewritten))
    return rows, refused


def write_corpus(undname, version, house, reference, comparable, names):
    grouped, turned_down = [], []
    for flag, mask, _ in IMPLEMENTED:
        rows, refused = rows_for(undname, house, reference, comparable, flag, mask)
        grouped.append((flag, rows))
        turned_down.extend((flag, *entry) for entry in refused)
    total = sum(len(rows) for _, rows in grouped)
    lines = [
        "# What each `UnDecorateSymbolName` mask bit changes that `llvm-undname` has no flag for,",
        "# over the same 609 names as msvc-llvm-corpus.txt. Three columns, the same shape as",
        "# msvc-suppressions.txt next door: decorated name, flag, what the reference prints under",
        "# that flag. Only the names a flag actually changes are here.",
        "#",
        f"# reference: dbghelp.dll {version} (UnDecorateSymbolName)",
        "#",
        "# The reference does not spell a name the way `llvm-undname` does -- it prints `__ptr64`,",
        "# closes `char * const` up differently, and puts no space after a comma -- so its answer is",
        "# put through the spacing rewrites in tools/generate_msvc_dbghelp_corpus.py before it is",
        "# recorded. Each rewrite is proved on every name it is used for: a name is in this file only",
        "# if rewriting the reference's *unflagged* answer reproduced this library's, which",
        "# msvc-llvm-corpus.txt pins against `llvm-undname`. Names where the two references disagree",
        "# about spelling rather than spacing are left out rather than guessed at, and every row kept",
        "# is checked to differ from the unflagged spelling only in the words its flag names.",
        "#",
        f"# {len(comparable)} of the {len(names)} names qualified; {total} differences recorded.",
        "#",
        "# Regenerate with tools/generate_msvc_dbghelp_corpus.py, on Windows.",
        "#",
    ]
    for flag, rows in grouped:
        for name, spelled in rows:
            lines.append(f"{name}\t{flag}\t{spelled}")
    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return total, grouped, turned_down


def write_name_only(undname, version, comparable):
    """What `UNDNAME_NAME_ONLY` prints, beside the names it printed it for.

    Two columns rather than three, and an answer this library does not undertake to
    print. `MsvcOptions` has no field for this bit and the file says why: what the flag
    reduces to is not a qualified name, so `signature().qualified_name` is not it either,
    and `tests/test_msvc_options.py` measures the gap rather than closing it.

    Only the names where the two references already spell the symbol alike, so what the
    comparison shows is the *mode* differing rather than the two houses' typography.
    """
    rows = []
    for name in comparable:
        spelled = undname(name, NAME_ONLY)
        if spelled is not None:
            # through the same rewrite as everything else, so what is left to see is the
            # mode differing rather than the reference's spacing
            rows.append((name, normalise(spelled)))
    lines = [
        "# What `UnDecorateSymbolName` prints under `UNDNAME_NAME_ONLY`, over the names of",
        "# msvc-llvm-corpus.txt that this library and the reference already spell alike.",
        "# Two columns: decorated name, what the reference printed.",
        "#",
        f"# reference: dbghelp.dll {version} (UnDecorateSymbolName)",
        "#",
        "# This is a comparison, not an expectation. `MsvcOptions` has no field for this bit,",
        "# because the bit is not a suppression that composes with the others -- it is a whole",
        "# reduced spelling, which also rewrites every symbol *nested* inside the name: a local",
        "# name's enclosing function, and a template argument that points at one, come back as",
        "# names too. `signature().qualified_name` reads the qualified name off the tree and",
        "# leaves what is nested inside it alone, so the two agree on most names and part",
        "# company on the ones with a symbol inside them. tests/test_msvc_options.py pins how",
        "# many of each, so the difference is measured rather than assumed.",
        "#",
        "# Restricted to the names the two references spell alike unflagged, so that what this",
        "# shows is the mode differing rather than the two houses' typography.",
        "#",
        "# Regenerate with tools/generate_msvc_dbghelp_corpus.py, on Windows.",
        "#",
    ]
    lines.extend(f"{name}\t{spelled}" for name, spelled in rows)
    NAME_ONLY_TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


def report(undname, version, house, reference, comparable, declined, names):
    print(f"dbghelp.dll {version}")
    print(f"{len(names)} names; the reference declined {len(declined)}")
    print(f"{len(comparable)} qualified for the corpus after the rewrite\n")

    print("rewrite rules, by how many names each one rescued:")
    running = [name for name in reference if reference[name] == house[name]]
    print(f"  {'(no rewrite)':24s} {len(running):4d}")
    applied = []
    for label, rule in RULES:
        applied.append(rule)
        matched = [name for name in reference if _apply(applied, reference[name]) == house[name]]
        print(f"  {label:24s} {len(matched):4d}")
        running = matched
    print()

    print("bits this library implements a field for:")
    for flag, mask, constant in IMPLEMENTED:
        changed = sum(1 for name in reference if undname(name, mask) not in (None, reference[name]))
        rows, refused = rows_for(undname, house, reference, comparable, flag, mask)
        print(f"  {constant:32s} changes {changed:4d}   recorded {len(rows):4d}   ({flag})")
        for name, spelled, complaint in refused:
            print(f"      turned down {name}: {complaint}")
            print(f"        {spelled}")
    print()

    print("bits that mean what one of `llvm-undname`'s five flags means:")
    recorded = llvm_answers()
    for flag, mask, constant in CROSS_CHECKED:
        agree, differ, examples = 0, [], []
        for name in comparable:
            answer = recorded.get((name, flag))
            spelled = undname(name, mask)
            if spelled is None:
                continue
            here = normalise(spelled)
            there = answer if answer is not None else house[name]
            if here == there:
                agree += 1
            else:
                differ.append(name)
                if len(examples) < 3:
                    examples.append((name, there, here))
        print(f"  {constant:32s} vs --{flag}: agree {agree:4d}, differ {len(differ):4d}")
        for name, there, here in examples:
            print(f"      {name}")
            print(f"        llvm-undname: {there}")
            print(f"        dbghelp     : {here}")
    print()

    print("every other documented bit, over the whole corpus:")
    for constant, mask in OTHER_BITS:
        changed = sum(1 for name in reference if undname(name, mask) not in (None, reference[name]))
        declined_here = sum(1 for name in reference if undname(name, mask) is None)
        note = "" if changed else "   (changes nothing here)"
        print(f"  {constant:32s} changes {changed:4d}, declines {declined_here:4d}{note}")


def _apply(rules, text):
    for rule in rules:
        text = rule(text)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", action="store_true", help="say what the reference does, write nothing")
    parser.add_argument("--check", action="store_true", help="fail if the checked-in corpus is stale")
    arguments = parser.parse_args()

    undname, version = load_undecorator()
    names = corpus_names()
    if not names:
        sys.exit(f"no names in {CORPUS}")
    house, reference, comparable, declined = survey(undname, names)

    if arguments.report:
        report(undname, version, house, reference, comparable, declined, names)
        return 0

    written = {path: path.read_text(encoding="utf-8") if path.exists() else None for path in (TARGET, NAME_ONLY_TARGET)}
    total, grouped, turned_down = write_corpus(undname, version, house, reference, comparable, names)
    reduced = write_name_only(undname, version, comparable)
    if arguments.check:
        stale = [path for path, before in written.items() if before != path.read_text(encoding="utf-8")]
        for path, before in written.items():
            if before is not None:
                path.write_text(before, encoding="utf-8")
        if stale:
            sys.exit(f"stale, regenerate: {', '.join(path.name for path in stale)}")
        print(f"{TARGET.name} and {NAME_ONLY_TARGET.name} are up to date")
        return 0
    for flag, rows in grouped:
        print(f"  {flag:24s} {len(rows):4d}")
    for flag, name, spelled, complaint in turned_down:
        print(f"  turned down {name} under {flag}: {complaint}")
        print(f"    {spelled}")
    print(f"wrote {total} differences over {len(comparable)} names to {TARGET}")
    print(f"wrote {reduced} name-only spellings to {NAME_ONLY_TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
