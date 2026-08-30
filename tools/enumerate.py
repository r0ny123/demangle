#!/usr/bin/env python3
"""Offer every short name a grammar admits to this library and to a reference.

The checked-in corpora are real symbols, so they cover the shapes compilers *emit*.
They do not cover the shapes a grammar *permits*, and that is where a demangler's
worst defects live: an encoding no compiler writes, read as something that looks
like a declaration a person would believe.

This enumerates instead of sampling. Every string up to `--length` characters over a
per-scheme alphabet is offered to `demangle_strict`; the ones it reads are then put
to the reference demangler, and every disagreement is reported -- including the ones
where the reference hands the name back and this library answers, which is the
direction that matters. A false reading is worse than no reading.

Seventeen defects came out of this in one sitting, in five schemes. Each was a
malformed name spelled as a plausible declaration:

    _Z1fIiEi                  ->  int f<int>()          a signature with no parameters
    _Z1f1AT_                  ->  f(A, auto)            a template parameter with no scope
    _Z1fILaEE                 ->  f<(signed char)0>     a literal with no value
    _RNvC_1f                  ->  ::f                   an identifier with no length
    _D3fooC                   ->  foo                   a class type with no class name
    _D4testFMMfZv             ->  test(scope scope f)   a storage class twice
    ___ZN1a1bES_block_invoke  ->  a::b(a)               a cursor past the end of input

Usage
-----
    tools/enumerate.py                     every scheme with a reference on this box
    tools/enumerate.py --scheme d          one scheme
    tools/enumerate.py --length 7          deeper, and much slower

The alphabets are hand-picked per scheme: the grammar's markers plus enough
identifier and digit characters to build a name, small enough that six characters is
a few million strings rather than a few billion. They are not exhaustive over the
byte range and are not meant to be -- what they cover is the *shape* space.

Exit status is non-zero when anything disagrees that is not listed in `ACCEPTED`.
"""

import argparse
import contextlib
import itertools
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import demangle


#: Per scheme: the reference to ask, a second opinion where one exists, and the
#: (prefix, alphabet) pairs to enumerate over. A prefix costs nothing and buys depth --
#: `_Z1fI` spends five characters on "a template specialisation of `f`" and leaves the
#: whole budget for the part under test.
#:
#: The second opinion is not decoration. Agreeing with one build of one reference is not
#: the same as being right, and where the two disagree with each other, being on one side
#: is not evidence of anything -- so a divergence from the first that the second shares
#: with this library is reported as accepted rather than as a defect.
def _rust_reference():
    """rustc-demangle itself where it has been built, and `llvm-cxxfilt` where it has not.

    LLVM's Rust reader is a port of an older rustc-demangle and binutils' is independent
    of both, so on Rust neither of the two demanglers that ship on this box is the
    implementation this scheme is a port of. `tools/rustc-demangle-reference/` is a
    twenty-line front end over the crate; see its README.
    """
    built = Path(__file__).resolve().parent / "rustc-demangle-reference" / "target" / "release"
    binary = built / "rustc-demangle-reference"
    return str(binary) if binary.exists() else "llvm-cxxfilt"


JOBS = {
    "itanium": (
        "llvm-cxxfilt",
        "c++filt",
        [
            ("_Z1fI", "iTLES_EN1aXKPRDvJBUFMOoAG"),
            ("_Z1f", "iTLES_EN1aXKPRDvJBUFMOoAG"),
            ("_ZN", "1aIiTLES_EKPRDv"),
        ],
    ),
    "rust": (
        _rust_reference(),
        None,
        [
            ("_R", "NvCsMKIY_1a3bcE"),
            ("_RNv", "CsMKIY_1a3bNtIE"),
            ("_RINv", "CsMKIY_1a3bNtEB"),
        ],
    ),
    "d": (
        "c++filt --format=dlang",
        None,
        [
            ("_D", "4test3fooFiZvSC"),
            ("_D4test", "3fooFiZvSCMxNK"),
        ],
    ),
    "ada": (
        "c++filt --format=gnat",
        None,
        [
            ("", "ada__text_ioXbUE0"),
            ("ada__", "text_ioXbUEN0$"),
        ],
    ),
    "msvc": (
        "llvm-undname",
        None,
        [
            ("?f@@", "YAXPEAUHVW@Z$0_"),
            ("??", "0A@$?QEBH1_23456"),
            ("??_B@5", "?0123456789ABC"),
        ],
    ),
}

#: Disagreements that are the reference's and are kept on purpose. Each is a predicate
#: over `(mangled, ours, theirs)` rather than a list of names, because the shapes are
#: families and a list would go stale the moment the alphabet changes.
ACCEPTED = {
    # `llvm-undname`'s `insertSpaceIfNeeded` emits a space only after an alphanumeric
    # character, so a tag name ending in `$` or `_` is glued to the variable it declares:
    # `struct _x` for `struct _` and `x`, which is the declaration of something else.
    # `struct _ {};` is ordinary C++, so this is reachable, and the space is kept.
    "msvc": lambda name, ours, first, second: (
        (
            first is not None
            and (
                ours.replace(" ", "") == first.replace(" ", "")
                # Or a vftable or vbtable base path with more than one element.
                # `llvm-undname` reads the first element and drops the rest, so
                # `??_7A@B@@6BC@D@@@`, `...E@F@@@` and `...E@F@@G@H@@@` -- three symbols
                # naming three different vtables -- all come back from it as one spelling.
                # Checked against llvm-undname 16, 18 and 20; the head of
                # `tests/conformance/msvc-llvm-corpus.txt` carries the whole finding. The
                # same for an RTTI Complete Object Locator, which carries the same path.
                or ("'s `" in ours and "'{for `" in ours)
                # Or a placement delete closure, which `llvm-undname` spells with no name at
                # all -- `void __cdecl (void *)`. Microsoft's own `undname` writes
                # `` `placement delete closure' ``, and a declaration with no name in it is
                # not a spelling to follow. Recognised by putting the name back.
                or _PLACEMENT_CLOSURE.sub("", ours) == first
            )
        )
        # Or `__int128`, which `llvm-undname` 18.1 cannot read and its own compiler
        # emits: `clang++ --target=x86_64-pc-windows-msvc` writes `_L` for `__int128`
        # and `_M` for `unsigned __int128`, and `demanglePrimitiveType` has neither.
        # Checked by compiling one rather than read off a table.
        or (first is None and ("__int128" in ours))
    ),
    # `c++filt --format=dlang` writes a path separator for a component that spells
    # nothing. An anonymous component and a `__S<n>` compiler scope are left out of the
    # spelling by both -- that much is measured, and pinned by `tests/test_d.py` -- but
    # the reference still writes the dot that would have gone before it, so a name comes
    # back `TypeInfoArrayGeneric!(...)..compare(...)`, or `startsWith!(...).(...)`, or
    # with a trailing `.` and nothing after it. Recognised by deleting a separator that
    # has nothing between it and the next one, the parameter list, or the end.
    "d": lambda name, ours, first, second: (
        (first is not None and _EMPTY_COMPONENT.sub("", first) == ours)
        # Or the anonymous `<SymbolName>` carrying a *member* function type.
        # `SymbolFunctionName` is `SymbolName | SymbolName TypeFunctionNoReturn |
        # SymbolName "M" TypeModifiers? TypeFunctionNoReturn`, and `SymbolName` is
        # `LName | TemplateInstanceName | IdentifierBackRef | "0"` -- so `0 M F Z` is in
        # the grammar. libiberty reads the two neighbouring shapes and refuses this one:
        # `_D1a0i` and `_D1a0FZv` are both `a` to it, and `_D1a0MFZv` is unreadable,
        # which makes the refusal an inconsistency inside the reference rather than a
        # rule. No compiler writes it -- the two corpus names matching this shape carry
        # the `0` inside an identifier, and both references agree on them -- so it is
        # reachable only by mutation. See `tests/test_d.py`.
        or (first is None and _ANONYMOUS_MEMBER.search(name) is not None)
    ),
    "itanium": lambda name, ours, first, second: (
        # `llvm-cxxfilt` refuses a parameter list whose first type is a literal `void`
        # followed by anything -- its leading `void` means "empty list, and nothing may
        # follow it". `c++filt` reads those, so following LLVM would mean refusing a
        # name the other reference reads. Recognised from the answer rather than from
        # the name, because that is where the condition actually is: a first parameter
        # spelled `void` with another after it. Where `c++filt` refuses one of these
        # too it is refusing the *second* type for its own reasons -- it reads no bare
        # `F...E` parameter at all, `_Z1fFaE` included -- which is a limit of that
        # reference and not a second opinion about the leading `void`.
        "(void, " in ours
        # Or the two references simply agree with this library, each under its own
        # style: `imaginary` to LLVM and `_Imaginary` to GNU are the same reading.
        or (second[0] is not None and second[0] == second[1])
        # Or a production newer than both references. The N1169 fixed-point types went
        # into the ABI in 2023, and `Dk`/`DK` and `Dy` are newer still; binutils 2.42 and
        # LLVM 18.1 know none of them, so both hand these back and this library is simply
        # ahead. Anything else both refuse is a defect and is reported.
        or (first is None and second[0] is None and _newer_than_the_references(name))
        # Or the same reading with a space `llvm-cxxfilt` does not print. It runs the
        # return type into the name when the return type is an array -- `signed
        # charf<>(signed char) []` for `_Z1fIEA_aa`, a function returning an array,
        # which is not a declaration C++ has and which no compiler emits. `c++filt`
        # parenthesises instead. Compared without spaces so the difference has to be
        # only that.
        or (first is not None and ours.replace(" ", "") == first.replace(" ", ""))
        # Or `_Complex`/`_Imaginary` applied to something with a declarator, where the
        # two references lose it in different ways and neither answer is the
        # declaration. `_Z1fGA_a` is an imaginary array of `signed char`: `llvm-cxxfilt`
        # drops the `[]` and answers `signed char imaginary`, `c++filt` writes
        # `signed char ( _Imaginary) []` with the brackets round the wrong thing and a
        # space inside them, and `_Z1fGFaE` -- imaginary applied to a function type --
        # loses the `()` to LLVM and is refused outright by GNU. This keeps the
        # declarator.
        #
        # The condition is the *shape*: the `G` or `C` marker, any cv-qualifiers, and
        # then an array or a function. Two narrower attempts each missed a family of it
        # -- `"GA" in name` missed `_Z1fGKA_a`, where a `K` sits between, and
        # `"[]" in ours or "()" in ours` missed `_Z1fGA1_a` and `_Z1fGFaaE`, where the
        # declarator is not empty.
        or (_IMAGINARY_DECLARATOR.search(name) is not None and ("imaginary" in ours or "complex" in ours))
        # Or a `char` array in a braced initialiser, which the *installed*
        # `llvm-cxxfilt` spells element by element -- `Hello{char [6]{(char)72, ...}}`
        # -- and LLVM main spells as a string. The expected column of
        # `tests/conformance/itanium-libcxxabi.txt.gz` is LLVM's own
        # `DemangleTestCases.inc` from main, which says `Hello{"Hello"}`, so this follows
        # the reference's own vectors rather than the older binary that ships beside
        # them. `c++filt` reads these through libiberty's copy of the same code and is
        # behind in the same way.
        or ('{"' in ours and first is not None and "{(char)" in first)
        # Or the CV- and ref-qualifiers of a `<nested-name>` standing where a *type*
        # goes, which the two references treat differently: `llvm-cxxfilt` drops them and
        # `c++filt` applies them, so `_Z1fPNK1a1bE` is `f(a::b*)` to one and
        # `f(a::b const*)` to the other. The ABI gives those qualifiers to a member
        # function's implicit object parameter and no compiler writes an `N K ... E`
        # where a type belongs, so being on either side is a choice rather than a
        # reading. This is on LLVM's, in both styles. Recognised by LLVM agreeing exactly
        # or refusing the name outright -- it refuses most of these for reasons of its
        # own, having no opinion to be on a side of -- the name carrying such a nested
        # name, and GNU's answer differing from this one in nothing but qualifiers.
        or (
            (first is None or first == ours)
            and second[0] is not None
            and _QUALIFIED_NESTED_NAME.search(name) is not None
            and _without_qualifiers(second[0]) == _without_qualifiers(ours)
        )
        # Or an Objective-C method name standing as a `<local-name>`'s function encoding
        # -- `Z53-[DeploymentSetupController handleManualServerEntry:]E`. Clang emits
        # these for a C++ template instantiated inside an Objective-C method, and both
        # *shipped* references refuse the shape wholesale: llvm-cxxfilt 18.1 and 20.1 and
        # GNU c++filt 2.42 hand back every one of them unread. libcxxabi's own vectors
        # carry two, with the answer recorded, and this library matches both exactly --
        # so the file the reference is tested against says the reading is right and the
        # binaries built from it are behind it. Their refusal is not evidence about a
        # mutant of that shape either.
        or (first is None and second[0] is None and _OBJC_METHOD_SCOPE.search(name) is not None)
        # Or a cv-qualifier repeated on a function type, where all three disagree:
        # `_Z1fKKFaE` is `f(signed char () const const)` here, `f(signed char  const()
        # const)` to LLVM -- which puts one of them in the declarator and doubles a
        # space -- and refused by GNU. `const const` on a function type is not a
        # declaration either.
        or (name.count("K") + name.count("V") > 1 and "F" in name and second[0] is None)
        # Or a cv-qualified function type reached through a <substitution>, where each
        # reference contradicts its own answer for the same type written out. `_Z1fKFvvE`
        # is `f(void () const)` to both; `_Z1fFvvEKS_` is `f(void (), void  const())` to
        # LLVM -- the qualifier moved into the declarator, with a doubled space -- and
        # `f(void (), void ( const)())` to GNU. This spells the substituted type the way
        # both references spell the written-out one. Recognised by a qualifier applied
        # directly to a substitution and an answer that differs in nothing but where the
        # qualifier words sit.
        or (
            _QUALIFIED_SUBSTITUTION.search(name) is not None
            and first is not None
            and _without_qualifiers(first) == _without_qualifiers(ours)
        )
    ),
}


#: An `N` opening a `<nested-name>` with a CV- or ref-qualifier on it. See `ACCEPTED`.
_QUALIFIED_NESTED_NAME = re.compile(r"N[rVKRO]")

#: A CV-qualifier applied directly to a `<substitution>`. See `ACCEPTED`.
_QUALIFIED_SUBSTITUTION = re.compile(r"[rVK]S")

#: Every word that spells a qualifier, and the reference sigils, so two answers can be
#: compared for "differs in nothing else".
_QUALIFIER_WORDS = re.compile(r"\b(?:const|volatile|restrict)\b|&&?")


def _without_qualifiers(text):
    """`text` with every qualifier word and sigil gone, and no spaces left to compare."""
    return _QUALIFIER_WORDS.sub("", text).replace(" ", "")


#: The anonymous `<SymbolName>` `0`, then the `M` member-function form. See `ACCEPTED`.
_ANONYMOUS_MEMBER = re.compile(r"0M[A-Za-z]{0,6}F")

#: A path separator with nothing before the next one, the parameter list, or the end.
_EMPTY_COMPONENT = re.compile(r"\.(?=[.(]|$)")

#: The name `llvm-undname` leaves out of a placement delete closure. See `ACCEPTED`.
_PLACEMENT_CLOSURE = re.compile(r"`placement delete(\[\])? closure'")

#: `G` (imaginary) or `C` (complex), any cv-qualifiers, then a declarator: an array or a
#: function. The one shape where all three implementations write something different.
_IMAGINARY_DECLARATOR = re.compile(r"[GC][rVK]*[AF]")

#: An Objective-C method name as a `<local-name>`'s function encoding. See `ACCEPTED`.
_OBJC_METHOD_SCOPE = re.compile(r"Z\d+[-+]\[")

#: The type codes neither shipped reference reads yet.
_AHEAD_OF_THE_REFERENCES = ("DA", "DR", "DS", "Dk", "DK", "Dy")


def _newer_than_the_references(mangled):
    return any(code in mangled for code in _AHEAD_OF_THE_REFERENCES)


def readings(scheme, prefix, alphabet, length, style=None):
    """Every enumerated name this library reads, as `{mangled: spelling}`.

    `style` matters where a scheme has two references: `llvm-cxxfilt` and `c++filt`
    spell the same reading differently -- `imaginary` against `_Imaginary`, `<int>>`
    against `<int> >` -- so each has to be asked about the answer given under *its* own
    style, or every name carrying one of those spellings reads as a disagreement.
    """
    found = {}
    for count in range(1, length + 1):
        for tail in itertools.product(alphabet, repeat=count):
            name = prefix + "".join(tail)
            # Any failure means "not read", which is the answer this is asking for.
            with contextlib.suppress(Exception):
                found[name] = demangle.demangle_strict(name, language=scheme, style=style)
    return found


def reference_answers(tool, names):
    """What `tool` says about each name, or None where it refuses.

    `llvm-undname` writes blank-line-separated records to stdout -- the echoed input,
    then the reading if there is one -- and sends refusals to stderr. So a record of one
    line is a refusal and a record of two is an answer.

    Splitting on the blank line rather than pairing lines up matters. Pairing, and asking
    whether the line after an echo is another name in the batch, reads an *answer* that
    happens to be one of the enumerated names as a refusal: `??@$@0` is read `??@$@`,
    which is itself a name in the sweep, and that reported 512 disagreements where the
    two agree exactly.

    `c++filt` and `llvm-cxxfilt` answer one line per line and echo the input back when
    they cannot read it, which is the same thing said differently.
    """
    command = tool.split()
    proc = subprocess.run(command, input="\n".join(names) + "\n", capture_output=True, text=True, timeout=3600)
    lines = proc.stdout.split("\n")
    if command[0].endswith("undname"):
        answers, record = {}, []
        for line in [*lines, ""]:
            if line:
                record.append(line)
            elif record:
                answers[record[0]] = record[1] if len(record) > 1 else None
                record = []
        return answers
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) != len(names):
        raise SystemExit(f"{tool}: {len(lines)} lines for {len(names)} names")
    return {name: (None if answer == name else answer) for name, answer in zip(names, lines, strict=True)}


def run(scheme, length, quiet, show):
    tool, second_tool, pairs = JOBS[scheme]
    if shutil.which(tool.split()[0]) is None:
        print(f"{scheme}: {tool.split()[0]} not on PATH, skipped")
        return 0
    if second_tool and shutil.which(second_tool.split()[0]) is None:
        second_tool = None
    accepted = ACCEPTED.get(scheme, lambda *_: False)
    unexplained = 0
    for prefix, alphabet in pairs:
        ours = readings(scheme, prefix, alphabet, length)
        theirs_style = readings(scheme, prefix, alphabet, length, style="gnu") if second_tool else {}
        names = sorted(ours)
        offered = sum(len(alphabet) ** n for n in range(1, length + 1))
        if not names:
            print(f"{scheme:8} {prefix!r:10} {offered:>10} strings, none read")
            continue
        theirs = reference_answers(tool, names)
        second = reference_answers(second_tool, names) if second_tool else {}
        # `None` is "the reference did not read it", which for the echoing tools means
        # the name came back unchanged. Where *this* library also answers with the name
        # unchanged the two agree, whatever the reference meant by it -- `c++filt
        # --format=gnat` echoes a bare Ada identifier because that is what it spells, and
        # reading that as a refusal reported 7,928 disagreements where there are none.
        differ = [
            (n, ours[n], theirs.get(n), (second.get(n), theirs_style.get(n)))
            for n in names
            if theirs.get(n) != ours[n] and not (theirs.get(n) is None and ours[n] == n)
        ]
        kept = [row for row in differ if accepted(*row)]
        real = [row for row in differ if not accepted(*row)]
        unexplained += len(real)
        note = f", {len(kept)} accepted" if kept else ""
        print(f"{scheme:8} {prefix!r:10} {offered:>10} strings, {len(names):>7} read, {len(real)} unexplained{note}")
        if not quiet:
            for name, mine, other, also in real[:show]:
                print(f"   {name}\n     ours {mine}\n     {tool.split()[0]:14} {other}")
                if second_tool:
                    print(f"     {second_tool.split()[0]:14} {also[0]}")
                    print(f"     ours (gnu)     {also[1]}")
        sys.stdout.flush()
    return unexplained


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scheme", choices=sorted(JOBS), action="append", help="repeatable; default is all")
    parser.add_argument("--length", type=int, default=5, help="free characters after the prefix (default 5)")
    parser.add_argument("--show", type=int, default=10, help="divergences to print per job")
    parser.add_argument("--quiet", action="store_true", help="counts only")
    args = parser.parse_args(argv)

    total = 0
    for scheme in args.scheme or sorted(JOBS):
        total += run(scheme, args.length, args.quiet, args.show)
    if total:
        print(f"\n{total} unexplained divergence(s)")
        return 1
    print("\nno unexplained divergences")
    return 0


if __name__ == "__main__":
    sys.exit(main())
