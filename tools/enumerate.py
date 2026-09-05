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


def _swift_reference():
    """Swift's own demangler, where `tools/swift-demangle-reference/build.sh` has been run.

    Nothing on this box reads a Swift name: `llvm-cxxfilt` and `c++filt` both decline a
    `$s` outright, so there is no fallback and this job is skipped when the reference has
    not been built. See `tools/swift-demangle-reference/README.md`.
    """
    built = Path(__file__).resolve().parent / "swift-demangle-reference" / "build"
    return str(built / "swift-demangle-reference")


def _gnuv2_reference():
    """libiberty's own pre-Itanium demangler, where `tools/cplus-dem-reference/build.sh`
    has been run.

    binutils 2.42's `c++filt` no longer knows `--format=gnu`, `lucid`, `arm` or `hp`, and
    GCC 9 removed the demangler from libiberty, so nothing installed reads one of these
    names at all. The build is GCC 8.3.0's `cplus-dem.c` -- the tree the corpus's own
    expectations came from -- behind a line-per-name front end; see the README there.
    Skipped, like the Swift job, when it has not been built.
    """
    built = Path(__file__).resolve().parent / "cplus-dem-reference" / "build"
    return str(built / "cplus-dem-reference")


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
    "gnuv2": (
        # The style is `gnu`, the scheme's default: the corpus carries four styles and
        # the reference reads one per run, and this is the one a caller who does not
        # know the compiler gets.
        f"{_gnuv2_reference()} gnu",
        None,
        [
            # A function's argument list, which is where the type grammar lives:
            # builtins, the modifiers, a back-reference, a class name and the `e`
            # that ends a list.
            ("foo__F", "icsvlxPCRUQ1_e3bTNAG"),
            # The constructor and the other `__`-prefixed special forms, with the
            # class name and signature that follow.
            ("__ct__3Foo", "FivcPRCe_Q21ATA"),
            # A template function: `H<count>Z<arg>...`, then `_` and the signature.
            ("foo__H1Z", "iZ_X01ct2TAvl3"),
            # Where the name ends and the signature begins.
            ("foo__", "F1AQ23Bivct$_CH"),
        ],
    ),
    "swift": (
        _swift_reference(),
        None,
        [
            # `$s` is the current mangling: a module length, a name, and an entity
            # marker. The alphabet is the operators that end a symbol -- `F` function,
            # `V`/`C`/`O` nominal kinds, `D` type, `M` metadata -- plus what a type
            # position needs.
            ("$s", "1a4mainVCOFDMSiySS_"),
            ("$s1a", "1bVCOFDMSiySSxq_G"),
            # `_T` is Swift 3's, read by a separate demangler in the compiler and still
            # what the ObjC runtime holds for a Swift class.
            ("_T", "tFCVOSiSS_1a3foo"),
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
                # Or a cv-qualified array element in a variable's type, `Y02$$CBN`,
                # whose qualifier `llvm-undname` 18 and 20 print inside a parameter and
                # a pointee -- `double const[3]`, `double const (*)[3]` -- and drop from
                # the variable itself, `double outer::h[3]`. No compiler writes `$$C`
                # there; the reading that keeps the qualifier is the consistent one.
                or _undname_drops_array_element_qualifiers(name, ours, first)
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
                # Or a qualifier in front of a deduced return type. That type is written
                # `?` and a name -- `?A?<auto>@@` -- and takes a qualifier like any other,
                # so `const auto f()` is `?B?<auto>@@`. `CustomTypeNode::outputPre` in
                # LLVM's `MSNodes.cpp` is `Identifier->output(OB, Flags);` and nothing
                # else, where every other type node's writes its qualifiers first, so
                # `?A`, `?B`, `?C` and `?D` in front of one all come back spelled the
                # same. Compiler-emitted and pinned in
                # `tests/conformance/msvc-reference-defects.txt`; recognised here by
                # taking the qualifier words back out, so the two answers have to differ
                # in nothing else.
                or (_QUALIFIED_CUSTOM_TYPE.search(name) is not None and _MSVC_QUALIFIER_WORDS.sub("", ours) == first)
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
        # `llvm-cxxfilt` resolving a generic lambda's substituted parameter to the
        # `auto` (or `$T`) it was declared with where the specialisation binds a type --
        # the defect `tests/conformance/itanium-reference-defects.txt` records, reached
        # here whenever a mutant moves a substitution onto such an entry. Accepted only
        # where the two spellings differ in nothing else.
        _llvm_left_a_lambda_parameter_unresolved(ours, first)
        or
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
        # The condition is the *shape*: the `G` or `C` marker, any cv-qualifiers or
        # pointer sigils, and then an array or a function. Three narrower attempts each
        # missed a family of it -- `"GA" in name` missed `_Z1fGKA_a`, where a `K` sits
        # between; `"[]" in ours or "()" in ours` missed `_Z1fGA1_a` and `_Z1fGFaaE`,
        # where the declarator is not empty; and allowing only cv-qualifiers between the
        # marker and the declarator missed the twelve `_Z1fGPFvE` shapes -- imaginary
        # applied to a pointer or reference *to* a function -- where `llvm-cxxfilt`
        # answers `f(void (* imaginary)`, opening two brackets and closing one. An
        # unbalanced spelling is not a second opinion about anything.
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
    # `Tg` is a generic specialization and the `m` after it is `MetatypeParamsRemoved`,
    # a flag 5.10.1's `demangleSpecAttributes` reads and current `main` -- which is what
    # this reference is built from, see tools/swift-demangle-reference/README.md -- does
    # not, because upstream deleted it. The 5.10.1 runtime shipped names carrying it,
    # `$sSUss17FixedWidthIntegerRzrlEyxqd__cSzRd__lufCSu_SiTgm5` among them, and those
    # binaries are still on disk, so reading it is the answer a demangler pointed at the
    # wild wants. The reference refusing a mangling it has dropped is not evidence about
    # the reading; it is only evidence that it is newer.
    # libiberty reads what it is given. A type code it does not know, a template argument
    # list with nothing in it, a scope with no name, an `operator` with no symbol: each
    # is spelled as the empty string and the surrounding punctuation is printed round the
    # gap -- `T1__pt__2_::__ct(char,  (void))`, `char foo<>(void)`, `T1::::get(void)`,
    # `foo::operator (void)`. None of those is a declaration, so none is evidence about
    # what the name says, and this library either refuses the name or -- since the
    # reference's own `iterate_demangle_function` is what it runs -- moves on to the next
    # `__` and reads the name that split gives. Recognised by the gap.
    #
    # Or the reference read the name at an earlier `__` than this library did. Both run
    # libiberty's `iterate_demangle_function`: guess the first `__`, demangle the whole
    # signature, and on failure move to the next. Where they part is what counts as
    # failure. libiberty skips what it cannot place between a template's arguments and
    # the `_` that opens the return type, so `foo__H1Zit__3iosFP9streambuf` -- `t` where
    # nothing goes -- is `ios foo<int>(streambuf *)` to it; this library refuses that
    # guess and reads the split at `__3ios`, `ios::foo__H1Zit(streambuf *)`. Neither is
    # a name a compiler wrote. Recognised by the function's own name: the reference's
    # is a proper prefix of ours, up to a `__`.
    "gnuv2": lambda name, ours, first, second: (
        first is not None
        and (
            _GNUV2_GAP.search(first) is not None
            or _gnuv2_second_list(first)
            or _gnuv2_function_name(ours).startswith(_gnuv2_function_name(first) + "__")
        )
    ),
    "swift": lambda name, ours, first, second: (
        (first is None and _METATYPE_PARAMS_REMOVED.search(name) is not None)
        # Or an extended existential shape, where `NodePrinter` reads the node one child
        # too high and spells the type as `<null node pointer>` -- a diagnostic rather
        # than a demangling, and the whole of what the name says. It survives upstream
        # because `manglings.txt` has no `Xg`/`XG` vector at any revision, so its own
        # corpus never asks. `tests/conformance/swift-reference-defects.txt` carries the
        # finding and pins what these names must spell.
        or (first is not None and "<null node pointer>" in first)
    ),
}


#: An `N` opening a `<nested-name>` with a CV- or ref-qualifier on it. See `ACCEPTED`.
_QUALIFIED_NESTED_NAME = re.compile(r"N[rVKRO]")

#: A CV-qualifier code in front of an MSVC custom type -- `?B?<auto>@@`, where `?A` is
#: the same shape with no qualifier and so no disagreement. See `ACCEPTED`.
_QUALIFIED_CUSTOM_TYPE = re.compile(r"\?[B-D]\?")
#: A gap where libiberty spelled a component it could not read as nothing: an empty
#: type slot (`( const)`, `,  (void)`, `( *)`), an empty template argument (`<>`, `< *>`,
#: `<int, >`), an empty scope (`::::`, `:: `, a leading `::`), an `operator` with no
#: symbol, an argument list printed in front of the whole declaration, or an argument
#: list with nothing in it at all -- the grammar writes an empty one as `v`, so `()` is a
#: list it could not read, except behind `operator`, where it is the call operator.
#: Checked against every recorded spelling in the corpus, which none of this matches:
#: `> >`, `(*)(char *)` and `operator()` are all real, and were all matched by an earlier
#: draft of this.
_GNUV2_GAP = re.compile(
    r"^\s|^::|\(\s|(?<!operator)\(\)|,\s\s|,\s*[,)>]|<>|<\s|::::|::\s|operator \(|operator\s\s|\s\s|,\.\.\.\)\("
)


def _gnuv2_second_list(spelled):
    """Whether a second argument list follows the first: `foo(...)(long long)`.

    libiberty's `demangle_signature` takes whatever follows a finished argument list for
    the start of another and prints it straight after, which no declaration has. Told
    apart from a function pointer's `(*)(char *)` by depth: that pair sits *inside* the
    parameter list, where this is a second group at the top level. Two more places a
    real spelling has a top-level pair before its parameter list are taken out first:
    a template argument, `T5<int (*)(int)>::~T5(void)`, and the call operator's own
    `operator()(foo &)`. No recorded spelling in the corpus has one after that.
    """
    depth = 0
    groups = 0
    for character in _without_template_arguments(spelled).replace("operator()", "operator"):
        if character == "(":
            if depth == 0:
                groups += 1
                if groups == 2:
                    return True
            depth += 1
        elif character == ")":
            depth -= 1
    return False


def _gnuv2_function_name(spelled):
    """The unqualified name a pre-Itanium spelling declares, without its template arguments.

    The text before the first `(`, with every `<...>` group taken out first -- a nested
    one is spelled `NA<int> >`, spaces and all, so the last word of the raw text can be
    a `>` -- then the last space-separated word of what is left, since a return type
    comes first, with any `scope::` taken off.
    """
    head = _without_template_arguments(spelled.split("(", 1)[0])
    word = head.strip().rsplit(" ", 1)[-1]
    return word.rsplit("::", 1)[-1]


def _without_template_arguments(spelled):
    """`spelled` with every balanced `<...>` group taken out."""
    kept = []
    depth = 0
    for character in spelled:
        if character == "<":
            depth += 1
        elif character == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            kept.append(character)
    return "".join(kept)


#: The qualifier words MSVC writes after a type, so an answer can be compared against one
#: that dropped them. See `ACCEPTED`.
_MSVC_QUALIFIER_WORDS = re.compile(r" (?:const|volatile)\b")

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
_IMAGINARY_DECLARATOR = re.compile(r"[GC][rVKPRO]*[AF]")

#: An Objective-C method name as a `<local-name>`'s function encoding. See `ACCEPTED`.
_OBJC_METHOD_SCOPE = re.compile(r"Z\d+[-+]\[")

#: A Swift generic specialization carrying the `MetatypeParamsRemoved` flag. See the
#: `swift` rule in `ACCEPTED`.
_METATYPE_PARAMS_REMOVED = re.compile(r"T[gGB]m\d")

#: The type codes neither shipped reference reads yet. `DB` and `DU` are read, but not
#: as the substitution candidates Clang 18 makes them -- it emits `_Z6myfuncRDB8_S0_`
#: for `myfunc(_BitInt(8)&, _BitInt(8)&)`, which `llvm-cxxfilt` 18 and 20 and `c++filt`
#: 2.42 all refuse and this library reads as Clang meant it.
_AHEAD_OF_THE_REFERENCES = ("DA", "DR", "DS", "Dk", "DK", "Dy", "DB", "DU")

#: Productions neither shipped reference reads that `llvm-cxxfilt` 20 does, spelled as
#: this library spells them: `cp <base-unresolved-name> <expression>* E`, a call whose
#: callee is parenthesised, and a template parameter inside a constrained parameter
#: declaration's concept arguments, `Tk 4True I T_ E`, which 18 gives up on ("we don't
#: track enclosing template parameter levels well enough").
_AHEAD_PATTERNS = (re.compile(r"cp(?=\d|on|dn|sr|gs)"), re.compile(r"Tk\d"))


_QUALIFIED_ARRAY_ELEMENT = re.compile(r"Y[0-9A-P@]*\$\$C[BCD]")


def _undname_drops_array_element_qualifiers(name, ours, first):
    """Whether `first` is `ours` with the `const`/`volatile` of an array element gone."""
    if _QUALIFIED_ARRAY_ELEMENT.search(name) is None:
        return False
    stripped = re.sub(r" (?:const|volatile)\b", "", ours)
    return stripped != ours and stripped.replace(" ", "") == first.replace(" ", "")


def _llvm_left_a_lambda_parameter_unresolved(ours, first):
    """Whether `first` is `ours` with `auto` or `$T<n>` where `ours` names a type."""
    if first is None or first == ours or "'lambda'" not in first:
        return False
    pieces = re.split(r"\bauto\b|\$T\d*", first)
    if len(pieces) == 1:
        return False
    return re.fullmatch(".+?".join(re.escape(piece) for piece in pieces), ours) is not None


def _newer_than_the_references(mangled):
    return any(code in mangled for code in _AHEAD_OF_THE_REFERENCES) or any(
        pattern.search(mangled) for pattern in _AHEAD_PATTERNS
    )


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


def _capped_at(memory):
    """A `preexec_fn` that caps the child's address space at `memory` bytes.

    Only `mutate.py --refusals` asks for it: binutils' D demangler expands a mutant's
    back references without bound and takes gigabytes before it gives up, which the
    gate never sees because this library refuses such names in milliseconds.
    """

    def cap():
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))

    return cap


def reference_answers(tool, names, timeout=None, memory=None):
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
    proc = subprocess.run(
        command,
        input="\n".join(names) + "\n",
        capture_output=True,
        text=True,
        timeout=3600 if timeout is None else timeout,
        preexec_fn=None if memory is None else _capped_at(memory),
    )
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
