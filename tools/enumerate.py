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
from demangle.core.decorations import split_decorations
from demangle.core.errors import DemanglingError
from demangle.core.spelling import SPELLING_BUILDER
from demangle.schemes.d._parser import _Parser as _DParser
from demangle.schemes.itanium.parser import ItaniumParser


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
    # Bare `<type>` encodings, the `demangle_type` entry point, against each reference's
    # own type mode. The alphabet is the type grammar's: pointers and references, the
    # qualifiers, a function, `D` for the extended codes, a source name, a template
    # parameter and a substitution.
    "types": (
        "llvm-cxxfilt --types",
        "c++filt -t",
        [
            ("", "iPRKVOFvEDA1aTLS_"),
            ("P", "iPRKVOFvEDA1aTLS_"),
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
            # The *type* grammar, in the one position that holds a bare one: a variable's
            # own type, `?x@@3 <type> <cv>`. The other three jobs reach a type only
            # through a signature, where the calling convention and the return type have
            # to be read first and most of the alphabet is spent before the type starts.
            ("?x@@3", "PAQBHXNDUJ@_$Y6C"),
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
                # Or a pointer to a member whose two qualifier letters disagree. A
                # member pointer states its member's qualifiers twice -- `PESB@@R6AHXZ`
                # is `S`, a volatile member, and `R`, a volatile pointer, for
                # `int (__cdecl *volatile B::*)(void)`, as clang-cl writes it -- and a
                # mutant that changes one leaves two answers. `llvm-undname` keeps the
                # member's and drops the pointer's; this keeps both. No compiler
                # writes them apart.
                or _undname_keeps_one_member_pointer_qualifier(name, ours, first)
                # Or the MS extension qualifiers on the pointer a member pointer points
                # at, which `llvm-undname` prints on that pointer everywhere else and
                # drops here: `PEQExt@@PEIFAH` is `int __unaligned *__restrict Ext::*`
                # to the compiler that wrote it and `int *Ext::*` to the reference,
                # where `PEIFAH` alone is the two words to both. Compiler-emitted;
                # `tests/conformance/msvc-reference-defects.txt` pins it against the
                # source in `tools/corpus_sources/msvc/msvc.cpp`. `tools/mutate.py
                # --seed 42` reached it wearing an ARM64EC marker.
                or _undname_drops_a_member_pointees_extension_qualifiers(name, ours, first)
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
                # so `const auto f()` is `?B?<auto>@@`. A later one can be a back
                # reference to the first, `?C?4@`, which is the same gap: the qualifier
                # is on the node and the printer writes none of a custom type's.
                # `CustomTypeNode::outputPre` in LLVM's `MSNodes.cpp` is
                # `Identifier->output(OB, Flags);` and nothing
                # else, where every other type node's writes its qualifiers first, so
                # `?A`, `?B`, `?C` and `?D` in front of one all come back spelled the
                # same. Compiler-emitted and pinned in
                # `tests/conformance/msvc-reference-defects.txt`; recognised here by
                # taking the qualifier words back out, so the two answers have to differ
                # in nothing else. `tools/mutate.py --seed 15`.
                or (
                    _QUALIFIED_CUSTOM_TYPE.search(name) is not None
                    and _CUSTOM_TYPE_QUALIFIERS.sub(r"\1", ours) == first
                )
                # Or a `$$C` qualifier over a pointer that already carries the same one,
                # `$$CBQAH` -- const over `int *const` -- which no compiler writes: the
                # reference spells the qualifier twice, `int *const const`, and this
                # once. `$$CBSAH` is the same over `int *const volatile`, which it
                # prints `int *const volatile const`. Accepted where collapsing the
                # extra word gives this answer. `tools/mutate.py --seed 23`.
                or (first is not None and first != ours and _collapse_doubled_qualifier(first) == ours)
                # Or the same qualifier words on a pointer in the other order, which
                # C++ leaves free: `char *const __restrict` here and
                # `char *__restrict const` to `llvm-undname` for
                # `?r1@Q@ns@@QEBAAEAY03$$CBPIAD@Z`. They part only where an outer `$$C`
                # is applied over a pointer that already carries `I`, and agree on every
                # shape a compiler writes -- `?x@@3QIADA` is `char *const __restrict x`
                # to both. `tools/mutate.py --seed 42`.
                or _qualifier_words_on_a_pointer_reordered(ours, first)
            )
        )
        # Or `__int128`, which `llvm-undname` 18.1 cannot read and its own compiler
        # emits: `clang++ --target=x86_64-pc-windows-msvc` writes `_L` for `__int128`
        # and `_M` for `unsigned __int128`, and `demanglePrimitiveType` has neither.
        # Checked by compiling one rather than read off a table.
        or (first is None and ("__int128" in ours))
        # Or a deduced type, `_P` for `auto` and `_T` for `decltype(auto)`, which MSVC
        # 14.3 writes for a function declared with one and not yet defined -- 606 of
        # Boost 1.84's symbols -- and `llvm-undname` 18 cannot read. LLVM's main branch
        # reads both, and this spells them as it does.
        or (first is None and _DEDUCED_TYPE.search(name) is not None and _DEDUCED_WORD.search(ours) is not None)
        # Or a dynamic initialiser over a nested symbol name that spells a *function*.
        # `??__E` takes a name, and a nested symbol `?<encoding>@` is one: MSVC writes
        # `??__E?i@C@@0HA@@YAXXZ` for a static data member, which both read, and
        # `tests/conformance/msvc-arm64ec.txt` carries that shape from a real binary.
        # `llvm-undname` 18 reads the nested encoding only where it is a *variable* and
        # refuses it where it is a function, which nothing initialises -- so the shape is
        # reachable only by damaging one of the real ones, and this reads the text as it
        # stands rather than deciding what a name may be initialised for.
        # `tools/mutate.py --seed 65`.
        or (first is None and _NESTED_FUNCTION_INITIALISER.search(ours) is not None)
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
        # Or a `return scope` parameter written return first, `NkM`, which DMD 2.104
        # began writing and libiberty -- `M` then `Nk` and nothing else -- refuses. Read
        # here as D's own `core.demangle` reads it; see `tests/test_d.py`. Where the
        # parameter sits in the function type that qualifies a function-local symbol,
        # libiberty does not refuse but backtracks: `dlang_parse_qualified` puts the
        # position back before the `F` it could not read and stops, and a type back
        # reference resolved through `dlang_type_backref` keeps whatever was read
        # before the stop. So a name it does read is this library's with that
        # parameter list, and the rest of that qualified name, gone. See
        # `_libiberty_dropped_a_return_scope_qualifier`. `tools/mutate.py --seed 30`.
        or ("NkM" in name and (first is None or _libiberty_dropped_a_return_scope_qualifier(ours, first)))
        # Or a symbol template argument that opens on a template instance, `S__T`.
        # `TemplateArgX` is `S Number_opt QualifiedName`, and a QualifiedName may be a
        # TemplateInstanceName with no length in front of it. `dlang_template_args`
        # has no arm for that -- `dlang_identifier` wants a number -- and refuses the
        # name. Pinned in `tests/test_d.py`; `tools/mutate.py --seed 9` is what
        # reached it, a mutant of the 44-character `testexpansion.s!(...)` instance
        # with `S` where a length `8` was.
        or (first is None and _SYMBOL_ARG_OPENS_ON_A_TEMPLATE.search(name) is not None)
        # Or a back reference whose target lands strictly inside an identifier's
        # characters: c++filt resolves it, and no compiler writes one.
        or (first is not None and _d_back_reference_inside_identifier(name))
    ),
    "itanium": lambda name, ours, first, second: (
        # `llvm-cxxfilt` resolving a generic lambda's substituted parameter to the
        # `auto` (or `$T`) it was declared with where the specialisation binds a type --
        # the defect `tests/conformance/itanium-reference-defects.txt` records, reached
        # here whenever a mutant moves a substitution onto such an entry. Accepted only
        # where the two spellings differ in nothing else.
        _llvm_left_a_lambda_parameter_unresolved(ours, first)
        # Or both references resolving a `<template-param>` in the substitution table to
        # the argument bound where the entry was made rather than where the back
        # reference is read -- the defect `tests/conformance/itanium-reference-defects.txt`
        # records for `insort` and `prepare_execution` -- reached where a mutant reads
        # under one local function's template scope an entry made under another's.
        # Accepted only where both references read the name; they spell it under their
        # own styles, so they are not asked to agree with each other.
        or (first is not None and second[0] is not None and _CROSS_SCOPE_BACK_REFERENCE.search(name) is not None)
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
        # Or the old form of `sr` that g++ still writes and `llvm-cxxfilt` refuses,
        # which `c++filt` reads as this does. See `_OLD_SR_FORM`.
        or (first is None and _OLD_SR_FORM.search(name) is not None)
        # Or a pack with members standing as a type outside any expansion, an encoding
        # no compiler writes: this reads one type per member, `llvm-cxxfilt` the first
        # member alone, `c++filt` nothing -- or, when both print the first member,
        # `_Z2f3IJifEE3DpPKT_` is `DpP f3<int, float>(int const)` to them and
        # `(int const, float const)` here. See `_uses_a_pack_outside_an_expansion`.
        or (first is not None and _uses_a_pack_outside_an_expansion(name) and (second[0] is None or first == second[0]))
        # Or an argument pack in the `I <template-arg>* E` form g++ wrote before `J`,
        # which `llvm-cxxfilt` refuses and `c++filt` reads -- accepted where `c++filt`
        # refuses the name for a reason of its own, since with both references silent
        # the pack is the only thing on this library's side of the disagreement that
        # is known to be one; and where the pack is nested inside a `J` pack, a shape
        # no compiler writes, over which c++filt applies a following declarator to
        # the last member only -- `_Z1fIJIivEEEvDpPT_` is `(int, void*)` to it and
        # `(int*, void*)` here -- and spells an empty one as an empty member --
        # `_Z1fIJiIEcEEvDpT_` is `f<int, , char>(int, , char)` to it and
        # `f<int, char>(int, char)` here, as an empty `J` pack in the same place is
        # to both references -- and neither reading has any authority. The nested
        # pack is asked of the parser rather than looked for as `JI`, which sees only
        # a pack standing first. c++filt's reading of a non-nested pack is a second
        # opinion and is kept as one -- unless that pack also stands where a single
        # type goes, outside any expansion, which is the disagreement the rule above
        # already describes: there c++filt prints one member where this library prints
        # one type per member, and it is doing so only because `llvm-cxxfilt`, which
        # refuses the `I ... E` form outright, is not there to be the reference that
        # does it. `_ZSt16__insertion_sortIN9__gnu_cxx17__normal_iteratorIPSt4pairIjfE
        # St6vectorIS3_SaIS3_EEEEI12_GLOBAL__N_113WeightCompareEEvT_SB_T0_` is a
        # mutant of a libstdc++ symbol whose `T0_` names such a pack: `(anonymous
        # namespace)` alone to c++filt, `(anonymous namespace), WeightCompare` here.
        # Two encodings no compiler writes in one name, and neither reading has any
        # authority. See `_uses_a_legacy_argument_pack`.
        # `tools/mutate.py --seed 18`, `--seed 30` and `--seed 37`.
        or (
            first is None
            and (second[0] is None or _nests_a_legacy_argument_pack(name) or _uses_a_pack_outside_an_expansion(name))
            and _uses_a_legacy_argument_pack(name)
        )
        # Or an entity named with a bare `Z` rather than `_Z` inside an *expression*.
        # `L Z <encoding> E` is g++'s compatibility spelling, and both references read it
        # where it stands as a template argument -- six of libcxxabi's own vectors are
        # that shape. LLVM accepts it only there: it is in `parseTemplateArg` and not in
        # `parseExprPrimary`, so `_Z1xILZ1yEEvv` reads and `_Z1xIXLZ1yEEEvv`, the same
        # entity one level down, does not. `c++filt` reads both and so does this, which
        # is what keeps the vectors. Asked of the parser rather than looked for as `LZ`
        # in the text, since an `L` ending one production and a `Z` opening the next
        # spell the same two characters. `tools/mutate.py --seed 35`, which found it
        # wearing a pointer-to-member conversion that looked like the disagreement.
        or (first is None and _names_an_entity_with_a_bare_z(name))
        # Or a `Dk`/`DK` constrained placeholder recorded as a substitution candidate,
        # which it is -- `Dk <type-constraint>` is a `<type>` and 5.1.10 makes every
        # non-builtin one a candidate -- and which `llvm-cxxfilt` 18 does not record.
        # `_Z1fDKN1A1BE` shows the whole disagreement: `S_` is `A` to both, and `S0_` is
        # `A::B decltype(auto)` here and out of range there. One entry moves every later
        # back-reference, which is why a mutant of `clang::driver::tools::openbsd::Link`
        # comes back `llvm::SmallVector<llvm, 4u>` from the reference. That reference
        # records the composite for `Dv` and for `Dp`, so the omission is these two codes
        # and not a rule about placeholders; the same omission its `DB` had until
        # libcxxabi's own corpus pinned `_Z6myfuncRDB8_S0_`, which the shipped binary
        # still refuses. Asked of the parser, since `Dk` and `DK` spell two characters a
        # <source-name> may hold. `tools/mutate.py --seed 40`.
        or _records_a_constrained_placeholder(name)
        # Or a back-reference naming the entry a `<template-param>` bound to a pack
        # contributed. The entry is the parameter -- which is what note 17 of
        # CONFORMANCE.md establishes against the compilers' output for the unpacked
        # case, and a pack parameter is not a different kind of parameter -- so this
        # resolves it to the pack, and a pack standing where one type goes is read one
        # type per member, as the arm above describes. Both references instead record one
        # *member* there, and not the same one: for `_Z1fIiJbcdEEvT_DpT0_S1_`,
        # `llvm-cxxfilt` says `bool` and `c++filt` says `double`, while all three agree
        # that the *next* entry, the `Dp` expansion's own, is the whole pack. Two
        # references that disagree with each other about one entry are not a second
        # opinion about this one, and no compiler writes the shape. Asked of the parser,
        # which is the only thing that knows which entry an `S<n>_` landed on.
        # `tools/mutate.py --seed 39`, and seeds 42, 43 and 48 reach it too.
        or _back_reference_names_a_pack_bound_parameter(name)
        # Or a back-reference numbered past the entry a `<template-template-param>` took,
        # which `llvm-cxxfilt` 18 does not record: `T_ I ... E` is two components and
        # 5.1.10 makes each a candidate, so `_Z1fI1AiEvT_IT0_ES3_S3_` -- what g++ 13.3
        # and clang++ 18.1.3 both emit for `f(C<T>, C<T>, C<T>)` -- is readable only with
        # both, and the reference refuses it. `tests/conformance/itanium-reference-defects.txt`
        # pins that name and `_Z1gI1AcEvT_IT0_ES1_IiE`, where the shift lands one short
        # and the reference answers `char<int>`, against the source in
        # `tools/corpus_sources/reference_defects/template_template_param.cpp`. Asked
        # only where `c++filt` is silent as well, since where it reads such a name it
        # agrees with this library and the arm at the top has already accepted it.
        # `tools/mutate.py --seed 43`.
        or (second[0] is None and _shifted_by_a_template_template_param(name))
        # Or a lambda that declares a template parameter after a pack, which is
        # ill-formed -- a pack must be last -- and where `c++filt` stops the declaration
        # list rather than refusing the name, so `Tp Ty Ty` is `typename... $T0` to it
        # and `typename... $T0, typename $T1` here. Keeping the declaration is what keeps
        # a `TL0_1_` elsewhere in the signature readable, and an option must not change
        # which names read. See `_declares_a_parameter_after_a_pack`.
        or _declares_a_parameter_after_a_pack(name)
        # Or a `<source-name>` whose length was written with a leading zero. A number in
        # these grammars has none, so `_Z1f01A` is not a well-formed name; `c++filt`
        # reads it as `f(A)` and `llvm-cxxfilt` refuses it, and this reads it as
        # `c++filt` does. Accepted whichever way the references fall, since where only
        # `llvm-cxxfilt` refuses the arm for "both agree with this library" has already
        # taken the name, and where both refuse they are refusing two different things:
        # `_ZN6modern11constrainedITkNS_8IntegralEiEET_01_` is the zero to one of them
        # and the `Tk` to the other. `tools/mutate.py --seed 66`.
        or _reads_a_length_written_with_a_leading_zero(name)
        # Or a function type returning a function type, which C++ has not, with a cv-
        # or ref-qualifier on the outer one: `llvm-cxxfilt` writes the qualifier after
        # the inner one's `()` and this before it, the same placement the two give a
        # function returning an array. See `_QUALIFIED_FUNCTION_RETURNING_A_FUNCTION`.
        or (first is not None and _QUALIFIED_FUNCTION_RETURNING_A_FUNCTION.search(first) is not None)
        # Or a function type whose return is a reference to a pack expansion.
        # llvm-cxxfilt prints the outer `R`/`O` only on the last member --
        # `_Z1fIJicdEEPFvDpT_EFRDpRPS0_E` is `int*&, char*&, double*& ()` here
        # and `int*&, char*&, double*&& ()` there -- stacking a second `&`
        # instead of collapsing. c++filt refuses. A declarator over a pack
        # applies to every member, and `R` over `T&` is `T&`. No compiler
        # writes a function that returns a pack. `tools/mutate.py --seed 19`.
        or (
            first is not None
            and second[0] is None
            and _FUNCTION_RETURNING_A_REFERENCED_EXPANSION.search(name) is not None
            and ours.replace("&", "") == first.replace("&", "")
        )
        # Or a function type returning an array, which C++ has not. The types job
        # already accepts the bare encodings `KFA_iE` / `FA_iRE`; length six under
        # `_Z1f` reaches them as a parameter -- `_Z1fKFA_iE` is `f(int () const [])`
        # here and `f(int () [] const)` to llvm-cxxfilt, `c++filt` refuses. See
        # `_QUALIFIED_FUNCTION_RETURNING_AN_ARRAY`.
        or (first is not None and _QUALIFIED_FUNCTION_RETURNING_AN_ARRAY.search(name) is not None)
        # Or a name the references number by the ABI's closure-prefix rule and the
        # common `auto` rule where this, by the name's form or by a retry, applied GCC
        # 12's or Apple's -- and agrees with them under theirs. See
        # `_differs_only_by_the_numbering_rule`.
        or _differs_only_by_the_numbering_rule(name, first, second)
        # Or a name both references refuse for a back-reference past the table that
        # reads under Apple's rule, where an undeduced `auto` is a substitution
        # candidate -- `_Z1fDaS_` is `f(auto, auto)` to Apple's clang, and to this on
        # the retry `ItaniumOptions.undeduced_auto_substitution` describes. Neither
        # reference knows the rule, so their refusal is not evidence about the name.
        or (first is None and second[0] is None and _UNDEDUCED_AUTO.search(name) is not None)
        # Or an argument pack written `I <template-arg>* E`, g++'s form under
        # `-fabi-version` 2 through 5, which `c++filt` reads and `llvm-cxxfilt` refuses.
        # See `_OLD_PACK`.
        or (first is None and _OLD_PACK.search(name) is not None)
        # Or a braced initialiser after a new-expression's type, which both compilers
        # write and `llvm-cxxfilt` refuses. See `_BRACED_NEW`.
        or (first is None and _BRACED_NEW.search(name) is not None)
        # Or an empty parenthesised initialiser after one, which `llvm-cxxfilt` reads
        # and does not print. See `_VALUE_INIT_NEW`.
        or (first is not None and _VALUE_INIT_NEW.search(name) is not None)
        # Or a division, which `llvm-cxxfilt` brackets as an assignment. See `_DIVISION`.
        or (first is not None and "dv" in name and "/" in first and "/" in ours)
        # Or a name neither reads because `llvm-cxxfilt` refuses its `LZ` external name
        # and `c++filt` refuses something else in it. See `_LEGACY_EXTERNAL_NAME`.
        or (first is None and second[0] is None and _LEGACY_EXTERNAL_NAME.search(name) is not None)
        # Or a back reference after a `_BitInt`, which the two sides count differently.
        # See `_BIT_INT`.
        or (first is not None and _BIT_INT.search(name) is not None)
        # Or qualifiers before a function type out of order or repeated, which every
        # implementation spells its own way. See `_MISORDERED_FUNCTION_QUALIFIERS`.
        or (first is not None and _MISORDERED_FUNCTION_QUALIFIERS.search(name) is not None)
        # Or a `v` among a function *type*'s parameters, which `parseFunctionType`
        # steps over wherever it stands -- `PFiivE` is `int (*)(int)` to it -- where
        # `c++filt` and this spell the `void` that is written. No compiler writes one
        # anywhere but alone.
        or _llvm_skips_a_void_parameter(ours, first)
        # Or a name neither tool read whole, because it splits its input on a space, a
        # bracket or a sign before demangling. See `_CLI_SPLITS`.
        or (first is None and second[0] is None and _CLI_SPLITS.search(name) is not None)
        # Or a `char` array in a braced initialiser, which this library spells as the
        # string it is. See `_spelled_as_a_string`.
        or _spelled_as_a_string(ours, first)
        # Or a byte that is not UTF-8, which this escapes as `\xD0` and llvm-cxxfilt 21
        # writes as the raw byte -- U+DC80..U+DCFF under surrogateescape. The byte is
        # what the name says; emitting it unescaped is not a spelling of a declaration.
        # `tools/mutate.py --seed 9`. A following hex digit is split the same way
        # `_string_literal` splits it -- seed 17's `Lc155E` then `e` is `"\x9B""ello"`.
        # See `_llvm_wrote_a_raw_high_byte`.
        or _llvm_wrote_a_raw_high_byte(ours, first)
        # Or a function type as the target of a cast, where llvm-cxxfilt drops the
        # parameter list and the grouping parenthesis with it: `const_cast<void
        # (*)()>(0)` becomes `const_cast<void (*>(0)`. c++filt prints the list on the
        # short names and refuses the nested `decltype(fp())` one. An unbalanced
        # spelling is not a declaration. `tools/mutate.py --seed 14`.
        or _llvm_dropped_a_cast_function_type(ours, first)
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
        #
        # Length six under `_Z1f` found two more of the same: the declarator named by a
        # substitution, `_Z1fFiEGS_` / `_Z1fA_iGS_`, which is `GFaE` / `GA_i` with the
        # function or array written `S_` -- llvm-cxxfilt drops the `()` or `[]` the
        # same way -- and a member pointer to a function, `_Z1fGMiFiE`, which is `M`
        # then the class then `F`, so the `F` is not next to the marker.
        # llvm-cxxfilt opens a parenthesis it does not close, `int (int::* imaginary)`.
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
        # Or a cv-qualified function type reached through a <substitution> or a
        # <template-param>, where each reference contradicts its own answer for the
        # same type written out. `_Z1fKFvvE`
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
        # Or a pointer to member whose class is a function type, which is not a type at
        # all and which the three implementations read three ways.
        or (
            first is not None
            and second[0] is not None
            and first != second[0]
            and _MEMBER_OF_A_FUNCTION_TYPE.search(name) is not None
        )
        # Or a pack named outside any expansion -- `T_` for a pack with no `Dp`, which no
        # declaration does. `llvm-cxxfilt` prints such a pack as its first member,
        # `c++filt` as its last, and this as all of them, recorded in
        # `tests/test_expressions.py` rather than followed; accepted only where the two
        # references disagree with each other as well, which is the signature of a
        # shape with no answer to be right about.
        or (
            first is not None
            and second[0] is not None
            and first != second[0]
            and _DECLARES_A_PACK.search(name) is not None
        )
        # Or a function returning a function, which is not a type, read by LLVM alone.
        or (second[0] is None and _FUNCTION_RETURNING_A_FUNCTION.search(name) is not None)
        # Or `sizeof...` over expansions of a pack a mutation has corrupted: LLVM refuses
        # the name and GNU counts the corrupted pack's members its own way -- `[6]`
        # against `[7]` here -- with no declaration behind either count.
        or (first is None and _SIZEOF_PACKS.search(name) is not None)
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
    #
    # Or a function whose name is `__op` and nothing more. `__op<type>` is how g++ 2.x
    # writes a conversion operator -- `__opi__3Foo` is `Foo::operator int()` -- and
    # libiberty takes the marker before it looks for the type, so `__op__Fi`, a
    # function called `__op`, is `operator (int)` to it: an operator converting to
    # nothing. This library reads the identifier, `__op(int)`. Recognised by the
    # reference's empty operator, which no real name spells.
    "gnuv2": lambda name, ours, first, second: (
        first is not None
        and (
            _GNUV2_GAP.search(first) is not None
            or _gnuv2_second_list(first)
            or _gnuv2_function_name(ours).startswith(_gnuv2_function_name(first) + "__")
            or _GNUV2_EMPTY_OPERATOR.search(first) is not None
        )
    ),
    # A bare type is read under every Itanium rule, and two more: a vendor extended
    # qualifier over a function type, which the three implementations place three ways,
    # and a cv- or ref-qualified function type returning an array, which `c++filt`
    # refuses and `llvm-cxxfilt` and this place two ways.
    "types": lambda name, ours, first, second: (
        ACCEPTED["itanium"](name, ours, first, second)
        or (first is not None and second[0] is not None and _VENDOR_QUALIFIED_FUNCTION.search(name) is not None)
        or (first is not None and _QUALIFIED_FUNCTION_RETURNING_AN_ARRAY.search(name) is not None)
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


#: `llvm-cxxfilt`'s spelling of a cv- or ref-qualified function type returning a
#: function type: the qualifier after the inner `()`. See `ACCEPTED`.
_QUALIFIED_FUNCTION_RETURNING_A_FUNCTION = re.compile(r"\)\(\) (?:const|volatile|&)")

#: A function type whose return is a reference to a pack expansion: `FRDp` / `FODp`.
#: See `ACCEPTED`.
_FUNCTION_RETURNING_A_REFERENCED_EXPANSION = re.compile(r"F[RO]Dp")


#: libiberty's `operator ` with no operator after it: the `__op` conversion-operator
#: marker taken off a function that is merely called `__op`. See `ACCEPTED`.
_GNUV2_EMPTY_OPERATOR = re.compile(r"operator [(<]")

#: An `N` opening a `<nested-name>` with a CV- or ref-qualifier on it. See `ACCEPTED`.
_QUALIFIED_NESTED_NAME = re.compile(r"N[rVKRO]")

#: A CV-qualifier code in front of an MSVC custom type -- `?B?<auto>@@`, where `?A` is
#: the same shape with no qualifier and so no disagreement, or `PB?<decltype-auto>@@`,
#: a pointer to one, or `?C?4@`, the same qualifier in front of a back reference to an
#: earlier `<auto>`. `llvm-undname` reads the qualifier onto the node and its printer
#: writes none of a custom type's qualifiers, so `<decltype-auto> const *` here is
#: `<decltype-auto> *` there; other qualifiers in the name are not in question, which
#: is why only those after the angle brackets are taken off. See `ACCEPTED`.
_QUALIFIED_CUSTOM_TYPE = re.compile(r"[B-D]\?(?:<|[0-9])")
_CUSTOM_TYPE_QUALIFIERS = re.compile(r"(<[^<>]*>)(?: (?:const|volatile))+")
#: The same qualifier word written twice in a row by `llvm-undname`. See `ACCEPTED`.
_DOUBLED_QUALIFIER = re.compile(r"\b(const|volatile) \1\b")
#: A run of qualifier words directly after a `*`, whose order C++ leaves free. See
#: `_qualifier_words_on_a_pointer_reordered`.
_POINTER_QUALIFIER_RUN = re.compile(r"\*(?:const|volatile|__restrict)(?: (?:const|volatile|__restrict))+")


def _collapse_doubled_qualifier(text):
    """`text` with a cv-qualifier llvm-undname spelled twice collapsed to one.

    `$$CBQAH` is const over `int *const`, which it prints `int *const const`.
    `$$CBSAH` is const over `int *const volatile`, which it prints
    `int *const volatile const` -- the extra word after the pair, not next to
    the first `const`. Both are the same type spelled once. `tools/mutate.py
    --seed 23`.
    """
    text = _DOUBLED_QUALIFIER.sub(r"\1", text)
    text = text.replace("const volatile const volatile", "const volatile")
    text = text.replace("const volatile const", "const volatile")
    text = text.replace("volatile const volatile", "volatile const")
    return text


def _qualifier_words_on_a_pointer_reordered(ours, first):
    """Whether two answers differ only in the order of the qualifier words on a pointer.

    C++ leaves that order free -- `char *const __restrict` and `char *__restrict const`
    are one declaration -- and the two demanglers write it differently where an outer
    `$$C` qualifier is applied over a pointer that already carries `I`, a shape no
    compiler writes. `?r1@Q@ns@@QEBAAEAY03$$CBPIAD@Z` is the first here and the second
    to `llvm-undname`. They agree on every shape a compiler does write: `?x@@3QIADA`
    is `char *const __restrict x` to both.

    Sorting the words in each run keeps the multiset, so a word one side dropped or
    added still differs and is still reported. `tools/mutate.py --seed 42`.
    """

    def sorted_runs(text):
        return _POINTER_QUALIFIER_RUN.sub(lambda run: "*" + " ".join(sorted(run.group()[1:].split())), text)

    return ours != first and sorted_runs(ours) == sorted_runs(first)


#: A gap where libiberty spelled a component it could not read as nothing: an empty
#: type slot (`( const)`, `(,`, `,  (void)`, `( *)`), an empty template argument (`<>`,
#: `< *>`, `<int, >`), an empty scope (`::::`, `:: `, a leading `::`), an `operator`
#: with no symbol, an argument list printed in front of the whole declaration, or an
#: argument list with nothing in it at all -- the grammar writes an empty one as `v`,
#: so `()` is a list it could not read, except behind `operator`, where it is the call
#: operator. Checked against every recorded spelling in the corpus, which none of this
#: matches: `> >`, `(*)(char *)` and `operator()` are all real, and were all matched by
#: an earlier draft of this. `(,` is `tools/mutate.py --seed 9`:
#: `__dl__17T5__pt____3fooiRT0iT2iT2` comes back
#: `T5__pt____3fooiRT::operator delete(, int, int, int, int)` there.
_GNUV2_GAP = re.compile(
    r"^\s|^::|\(\s|\(,|(?<!operator)\(\)|,\s\s|,\s*[,)>]|<>|<\s|::::|::\s|operator \(|operator\s\s|\s\s|,\.\.\.\)\("
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
    """`spelled` with every balanced `<...>` group taken out.

    An unbalanced `<` -- a mutant's class name, `Spec<ow__F7compl`, which the reference
    reads as any other run of characters -- is not a group, and taking everything after
    it out hid a second argument list from `_gnuv2_second_list`. The text is returned
    as it stands when a `<` is never closed.
    """
    kept = []
    depth = 0
    for character in spelled:
        if character == "<":
            depth += 1
        elif character == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            kept.append(character)
    return spelled if depth else "".join(kept)


#: A CV-qualifier applied directly to a `<substitution>`. See `ACCEPTED`.
#: A qualifier applied to a <substitution> or to a <template-param>: either may stand
#: for a function type, which both references then qualify differently from the same
#: type written out. `_Z1fIJFivEEEvDpRKT_` -- `const T&` over a pack holding a function
#: type, which lldb's instrumentation really writes -- is `f<int ()>(int  const(&)())` to
#: LLVM and `f<int ()>(int ( const&)())` to GNU, against `int (&)() const` from both for
#: `_Z1fRKFivE`.
_QUALIFIED_SUBSTITUTION = re.compile(r"[rVK](?:S|T\d*_)")

#: Every word that spells a qualifier, and the reference sigils, so two answers can be
#: compared for "differs in nothing else".
_QUALIFIER_WORDS = re.compile(r"\b(?:const|volatile|restrict)\b|&&?")


def _without_qualifiers(text):
    """`text` with every qualifier word and sigil gone, and no spaces left to compare."""
    return _QUALIFIER_WORDS.sub("", text).replace(" ", "")


#: The anonymous `<SymbolName>` `0`, then the `M` member-function form. See `ACCEPTED`.
_ANONYMOUS_MEMBER = re.compile(r"0M[A-Za-z]{0,6}F")
#: A symbol template argument that opens on a template instance. See `ACCEPTED`.
_SYMBOL_ARG_OPENS_ON_A_TEMPLATE = re.compile(r"S__[TU]")

#: A pointer to member whose *class* is a function type: `M` and then, where a class type
#: must stand, `F` -- or a cv-qualified `F`. No declaration has one, and the three
#: implementations agree on nothing about it: `_Z1fM1XMFivOEMS_FOKivEMS_VFS3_RS2_E` is
#: `int () &&::*` to LLVM. See `ACCEPTED`.
_MEMBER_OF_A_FUNCTION_TYPE = re.compile(r"M[rVK]*F")

#: A template argument pack, `IJ`, in a name that is not reading one of the packs the
#: rules above explain. See `ACCEPTED`: a pack named outside any expansion.
_DECLARES_A_PACK = re.compile(r"IJ")

#: A function type whose return type is a function type, `FFvvE`: not a type C++ has, and
#: read two ways by the two that read it -- `void  (**)()() volatile` to LLVM for
#: `PPVFFvvEE`, refused by GNU. See `ACCEPTED`.
_FUNCTION_RETURNING_A_FUNCTION = re.compile(r"F[rVK]*F")

#: `sP`, `sizeof...` over a list of expansions. See `ACCEPTED`.
_SIZEOF_PACKS = re.compile(r"sP")

#: A path separator with nothing before the next one, the parameter list, or the end.
_EMPTY_COMPONENT = re.compile(r"\.(?=[.(]|$)")

#: The name `llvm-undname` leaves out of a placement delete closure. See `ACCEPTED`.
_PLACEMENT_CLOSURE = re.compile(r"`placement delete(\[\])? closure'")

#: Qualifiers before a function type out of the ABI's `r V K` order, or repeated --
#: `KV`, `VKK` -- which the grammar reads as one qualifier applied to a qualified
#: function type. No compiler writes them, and the three implementations spell the
#: result three ways: `c++filt` stacks them in the mangled order after the exception
#: specification, `llvm-cxxfilt` moves the outer one onto the return type, and this
#: writes the outer one after the ref-qualifier.
_MISORDERED_FUNCTION_QUALIFIERS = re.compile(r"(?:KV|VV|KK|rr|Vr|Kr)[rVK]*(?:Do|DO.*?E|Dw.*?E|Dx)?F")

#: A vendor extended qualifier, `U <source-name>`, applied to a function type: the same
#: three-way shape as the imaginary declarator below, which no compiler writes.
_VENDOR_QUALIFIED_FUNCTION = re.compile(r"U\d+[A-Za-z_][A-Za-z0-9_$.]*?[rVK]*(?:Do|DO.*?E|Dw.*?E|Dx)?F")

#: A cv- or ref-qualified function type whose return type is an array -- `KFA_iE`,
#: `FA_iRE`, and the same as a parameter of `_Z1f` -- which C++ has not. `c++filt`
#: refuses it; `llvm-cxxfilt` writes the qualifier after the array's brackets,
#: `int () [] const`, and this before them, `int () const []`, where each is the
#: order its printer gives every function type. Found at length six by
#: `tools/enumerate.py`; nothing at the gate's length reaches it. Unanchored so a
#: symbol carrying the type matches the same way a bare encoding does.
_QUALIFIED_FUNCTION_RETURNING_AN_ARRAY = re.compile(r"(?:[rVK]+FA_[^E]*E)|(?:FA_[^E]*[RO]E)")

#: `G` (imaginary) or `C` (complex), any cv-qualifiers, then a declarator: an array, a
#: function, a substitution that names one, or a member pointer to one. The one shape
#: where all three implementations write something different.
_IMAGINARY_DECLARATOR = re.compile(r"[GC][rVKPRO]*(?:Do|DO.*?E|Dw.*?E|Dx)?(?:[AF]|S|M.*?[AF])")

#: Characters both reference *tools* split their input on before demangling anything --
#: a space, a bracket, a `+` or a `-` -- so a name carrying one reaches neither
#: demangler whole. The demanglers themselves read any byte in a <source-name>, which is
#: how libcxxabi's own vectors carry `-[Foo bar]` scopes. Their refusal is not evidence.
_CLI_SPLITS = re.compile(r"[-+\[\] ]")

#: `llvm-cxxfilt`'s spelling of a `char` array in a braced initialiser, whose values
#: this library joins into the string they spell. See `_spelled_as_a_string`.
_CHAR_ARRAY = re.compile(r"char \[(\d*)\]\{((?:\(char\)-?\d+(?:, )?)+)\}")

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
_AHEAD_PATTERNS = (
    re.compile(r"cp(?=\d|on|dn|sr|gs)"),
    re.compile(r"Tk\d"),
    # `sy <pack reference> <expression>`, a C++26 pack-index expression, which Clang's
    # `mangleExpression` writes for `PackIndexingExpr` and neither shipped
    # `llvm-cxxfilt` reads (the type form, `Dy`, is in the list above).
    re.compile(r"sy(?:T[L\d_]|f[pL])"),
)

#: `sr <type> <unqualified-name>`, the form of `sr` the ABI had before the
#: <unresolved-name> productions and g++ 13 still writes for every member of a class
#: that is not itself dependent: a plain class, `sr1A3bazIT_E`, a nested one,
#: `srN1A1B1CIT_EE1w`, or a `std::` one, `srSt1AIT_E5value`. `llvm-cxxfilt` 18 and 20
#: refuse all three; libiberty reads them, the plain one by reading the whole name
#: again when the modern grammar fails, and so does this. See the parser.
_OLD_SR_FORM = re.compile(r"sr(?:N?St|N?\d)")

#: `I <template-arg>* E` where an argument stands: an argument pack in the form g++ wrote
#: under `-fabi-version` 2 through 5 -- the default of GCC 3.4 through 4.9 -- and still
#: writes as a compatibility alias beside the `J` form when asked for those versions.
#: libiberty reads it; `llvm-cxxfilt` 18 and 20 refuse the name. Two `I`s in a row is
#: the shape, since nothing else that can follow an opening `I` begins with one.
_OLD_PACK = re.compile(r"II")

#: `nw <expression>* _ <type> il <expression>* E`: a new-expression initialised with
#: braces, `new T{t}`, which g++ 13 and Clang 18 both write this way. The ABI grammar has
#: only `pi`, the parenthesised form; libiberty reads `il` too, `llvm-cxxfilt` 18 and 20
#: refuse the name, and this reads it as libiberty does.
_BRACED_NEW = re.compile(r"n[wa]\w*?_\w*?il")

#: `nw <expression>* _ <type> pi E`: `new T()`, value-initialised, which both compilers
#: write with an empty parenthesised initialiser and `llvm-cxxfilt` prints as `new T`,
#: the other expression. `c++filt` and this print the brackets the name carries. See
#: `tests/conformance/itanium-reference-defects.txt`.
_VALUE_INIT_NEW = re.compile(r"n[wa]\w*?_\w*?piE")

#: `_DIVISION`: `dv`, which `llvm-cxxfilt` 18 and 20 carry in their operator table at
#: the precedence of an assignment, so `(sizeof(T) + 1) / 2` prints as
#: `sizeof (int) + 1 / 2` -- a different expression -- and `a / b - c` as `(a / b) - c`.
#: `c++filt` and this bracket by the precedence `/` has. See
#: `tests/conformance/itanium-reference-defects.txt`. Tested on the text rather than by
#: a pattern: `dv` is two letters that occur in identifiers too, and a divergence this
#: rule explains has a `/` on both sides.

#: `L Z <encoding> E` -- an external name with the `_` missing, which G++ once emitted
#: (libiberty's `d_expr_primary` carries the workaround as "bug 375") and `c++filt`
#: reads where `llvm-cxxfilt` refuses. A name that both refuse and carries one is
#: refused by `llvm-cxxfilt` for this and by `c++filt` for something else.
_LEGACY_EXTERNAL_NAME = re.compile(r"LZ\d")

#: A `_BitInt` with a width or a parameter: Clang 18 makes it a substitution candidate
#: and this library records it; `llvm-cxxfilt` reads `DB` without recording it, so every
#: later back reference in the name resolves one entry apart. `_Z6myfuncRDB8_S_` is
#: `myfunc(_BitInt(8)&, _BitInt(8))` here and `..., _BitInt(8)&)` there.
_BIT_INT = re.compile(r"D[BU](?:\d+_|T)")


_QUALIFIED_ARRAY_ELEMENT = re.compile(r"Y[0-9A-P@]*\$\$C[BCD]")

#: A dynamic initialiser or atexit destructor whose operand is a nested symbol name
#: that spells a *function*. See the `msvc` rule in `ACCEPTED`.
_NESTED_FUNCTION_INITIALISER = re.compile(r"`dynamic (?:initializer|atexit destructor) for `[^`']*\(")

#: A local name inside a template function's encoding, then another local name reading
#: a back reference: `Z1fIiE...E...Z1gIdE...S2_...E`. See the `itanium` rule.
_CROSS_SCOPE_BACK_REFERENCE = re.compile(r"Z\d+\w*?I[^Z]*?E[^Z]*?Z[^Z]*?S\d*_")


#: `P`, extension qualifiers, a member qualifier letter (`Q`/`R`/`S`/`T`), the class,
#: then the pointee's own pointer letter (`P`/`Q`/`R`/`S`) and a function. See the rule.
_MEMBER_POINTER_LETTERS = re.compile(r"[PQRS][EFGHI]*([QRST])[A-Za-z0-9_$@?]*?@@[EFGHI]*([PQRS])6")

#: The same, where the pointee is itself a *pointer* carrying `F` (`__unaligned`) or `I`
#: (`__restrict`) rather than a function. See
#: `_undname_drops_a_member_pointees_extension_qualifiers`.
#: The class is matched up to a single `@` rather than the `@@` that ends a fully
#: written one, since a namespace in it may be a back reference: `PEQExt@1@PEIFAH`.
_MEMBER_POINTEE_EXTENSIONS = re.compile(r"[PQRS][EFGHI]*[QRST][A-Za-z0-9_$@?]*?@[PQRS][EGH]*[FI][EFGHI]*[A-D]")

#: The qualifier words the MS extension letters spell. See the rule above.
_EXTENSION_QUALIFIER = re.compile(r"\b__(?:unaligned|restrict)\b")


def _strip_qualifiers(text):
    return re.sub(r"\b(?:const|volatile) ", "", text).replace(" ", "")


def _llvm_skips_a_void_parameter(ours, first):
    """Whether `first` is `ours` with a `void` parameter dropped from a list."""
    return first is not None and first != ours and re.sub(r"\bvoid, |, void\b", "", ours) == first


_DEDUCED_TYPE = re.compile(r"_[PT]")
_UNDEDUCED_AUTO = re.compile(r"D[ac]")
_DEDUCED_WORD = re.compile(r"\bauto\b")


def _undname_keeps_one_member_pointer_qualifier(name, ours, first):
    """Whether `ours` and `first` differ only in the qualifiers of a member pointer whose
    member letter and pointer letter disagree."""
    found = _MEMBER_POINTER_LETTERS.search(name)
    if found is None or "QRST".index(found.group(1)) == "PQRS".index(found.group(2)):
        return False
    return ours != first and _strip_qualifiers(ours) == _strip_qualifiers(first)


def _undname_drops_a_member_pointees_extension_qualifiers(name, ours, first):
    """Whether `first` is `ours` with `__unaligned` or `__restrict` gone from the pointer
    a member pointer points at.

    `llvm-undname` prints both words on a pointer and drops both when that pointer is a
    member pointer's pointee, so `?extended_member@ns@@YAPEQExt@1@PEIFAHXZ` and a
    `?extended_plain@ns@@YAPEIFAHXZ` written from the same declarator come back from it
    as one spelling and two. Compiler-emitted, and pinned against the source in
    `tests/conformance/msvc-reference-defects.txt`.

    Both sides have their words taken out before comparing, so a name that carries the
    shape *and* some second disagreement still differs; and the reference has to be the
    side printing fewer of them, so this never explains a word this library dropped.
    """
    if first is None or _MEMBER_POINTEE_EXTENSIONS.search(name) is None:
        return False
    if len(_EXTENSION_QUALIFIER.findall(first)) >= len(_EXTENSION_QUALIFIER.findall(ours)):
        return False
    without = _EXTENSION_QUALIFIER.sub("", ours).replace(" ", "")
    return without == _EXTENSION_QUALIFIER.sub("", first).replace(" ", "")


def _undname_drops_array_element_qualifiers(name, ours, first):
    """Whether `first` is `ours` with the `const`/`volatile` of an array element gone."""
    if _QUALIFIED_ARRAY_ELEMENT.search(name) is None:
        return False
    stripped = re.sub(r" (?:const|volatile)\b", "", ours)
    return stripped != ours and stripped.replace(" ", "") == first.replace(" ", "")


#: A C++ cast whose target is a function type. See `_llvm_dropped_a_cast_function_type`.
_CAST_WITH_ARGUMENT = re.compile(r"((?:const|static|dynamic|reinterpret)_cast)<(.+)>\(([^()]*)\)")


def _llvm_dropped_a_cast_function_type(ours, first):
    """Whether `first` is `ours` with a cast's function-type parameter list cut out.

    llvm-cxxfilt prints `const_cast<void (*>(0)` for `const_cast<void (*)()>(0)`, and
    the same cut for `(int)`, `(**)()`, `(*&)()`, `(* const)()`. Restricted to the
    cast's own target so `h<float (*)()>` in the same name is left alone. The grouping
    parenthesis plus the parameter list sit at the end of the target; a function type
    not reached through a pointer is ` ()` there instead.
    """
    if first is None or first == ours or "cast<" not in first:
        return False

    def collapse(match):
        kind, target, argument = match.group(1), match.group(2), match.group(3)
        cut = re.sub(r"\)\([^()]*\)$", "", target)
        if cut == target:
            cut = re.sub(r" \(\)$", " ", target)
        return f"{kind}<{cut}>({argument})"

    return _CAST_WITH_ARGUMENT.sub(collapse, ours) == first


def _llvm_wrote_a_raw_high_byte(ours, first):
    """Whether `first` is `ours` with each `\\xHH` (HH >= 0x80) written as that byte.

    llvm-cxxfilt 21 emits the raw byte of a non-UTF-8 string-literal element; this
    library escapes it, which is what the name says. Recognised by putting the
    escaped form back: a lone surrogate U+DC80..U+DCFF is how a non-UTF-8 byte
    arrives under `surrogateescape`. After that, a hex digit following the
    escape is split the way `_string_literal` splits it -- `\\x9Bello` is
    `"\\x9B""ello"` here, so that `e` is not a third hex digit.
    """
    if first is None or first == ours:
        return False
    rewritten = []
    for character in first:
        code = ord(character)
        if 0xDC80 <= code <= 0xDCFF:
            rewritten.append(f"\\x{code - 0xDC00:X}")
        else:
            rewritten.append(character)
    text = "".join(rewritten)
    text = re.sub(r"(\\x[0-9A-Fa-f]{2})(?=[0-9A-Fa-f])", r'\1""', text)
    return text == ours


def _spelled_as_a_string(ours, first):
    """Whether `first` is `ours` with a string written out as the char array it is.

    `tl A6_c Lc72E Lc101E ...` is `"Hello"` here and `char [6]{(char)72, (char)101,
    ...}` to both references; one of them is readable, and they say the same thing.
    Joined with the same spelling function, so the two agree to the escape.
    """
    if first is None or "char [" not in first:
        return False
    from demangle.schemes.itanium.parser import _string_literal

    def join(match):
        values = [int(v) for v in re.findall(r"\(char\)(-?\d+)", match.group(2))]
        return _string_literal(values)

    return re.sub(_CHAR_ARRAY, join, first) == ours


def _llvm_left_a_lambda_parameter_unresolved(ours, first):
    """Whether `first` is `ours` with `auto` or `$T<n>` where `ours` names a type."""
    if first is None or first == ours or "'lambda" not in first:
        return False
    pieces = re.split(r"\bauto\b|\$T\d*", first)
    if len(pieces) == 1:
        return False
    return re.fullmatch(".+?".join(re.escape(piece) for piece in pieces), ours) is not None


def _libiberty_dropped_a_return_scope_qualifier(ours, first):
    """Whether `first` is `ours` with one or more runs cut out, each starting at a `(`
    and spelling `return scope` somewhere inside it.

    A function-local symbol's qualifier that libiberty could not read is dropped whole:
    the parameter list from its `(`, and every component after it in the same qualified
    name, up to wherever the two spellings meet again -- which only trying each end can
    find, so each is tried. `a.f(return scope int).c.d` cut to `a.f` is the shape;
    `a.f(int)` cut to `a.f` is not, since nothing in the run was unreadable to it.
    """
    if ours == first:
        return True
    agree = 0
    while agree < min(len(ours), len(first)) and ours[agree] == first[agree]:
        agree += 1
    for start in range(min(agree, len(ours) - 1), -1, -1):
        if ours[start] != "(":
            continue
        for end in range(start + 1, len(ours) + 1):
            if "return scope" in ours[start:end] and _libiberty_dropped_a_return_scope_qualifier(
                ours[end:], first[start:]
            ):
                return True
    return False


def _itanium_parser_after_reading(mangled):
    """The `ItaniumParser` that read `mangled`, its state intact, or None if none did.

    The parser reads the encoding alone; what a symbol table wrote around it -- an ELF
    version, a clone suffix -- comes off first, as it does on the library's own path.
    """
    encoding, _ = split_decorations(mangled)
    parser = ItaniumParser(encoding, SPELLING_BUILDER)
    try:
        parser.parse()
    except DemanglingError:
        return None
    return parser


def _uses_a_pack_outside_an_expansion(mangled):
    """Whether this library's reading of `mangled` put a pack with members where a
    type goes, outside any `Dp` -- `_Z1fIJicEPT_E`, `f<int, char, int*, char*>` here
    and `f<int, char, int*>` to `llvm-cxxfilt`, which prints the first member; both
    are readings of an encoding no compiler writes, and `c++filt` refuses it."""
    parser = _itanium_parser_after_reading(mangled)
    return parser is not None and parser._bare_pack_used


def _uses_a_legacy_argument_pack(mangled):
    """Whether this library's reading of `mangled` took an argument pack from `I ... E`.

    The form g++ wrote for a pack under `-fabi-version` 2 through 5 and still writes as
    a compatibility alias; `llvm-cxxfilt` refuses it and `c++filt` reads it. Nested
    inside a `J` pack, c++filt also applies a following declarator only to the last
    member of that nested pack -- `DpPT_` over `JIivE` is `(int, void*)` to it and
    `(int*, void*)` here. The `II` regex cannot see `JI`. Where `c++filt` refuses
    such a name too, it is refusing something else in it -- an `enable_if` attribute,
    a `T_` with nothing to bind it -- and the pack is not what the two are disagreeing
    about.
    """
    parser = _itanium_parser_after_reading(mangled)
    return parser is not None and parser._legacy_pack_used


def _names_an_entity_with_a_bare_z(mangled):
    """Whether this library's reading of `mangled` took an `<expr-primary>` entity from
    `L Z <encoding> E` rather than `L _Z <encoding> E`.

    The flag is set wherever the bare form was read, including the template-argument
    position both references accept; the rule that consults it also asks that
    `llvm-cxxfilt` refused the name, which is what narrows it to the expression position
    where the two references actually part.
    """
    parser = _itanium_parser_after_reading(mangled)
    return parser is not None and parser._bare_entity_prefix_used


def _declares_a_parameter_after_a_pack(mangled):
    """Whether a lambda in `mangled` declared a template parameter after a pack.

    A pack must be the last parameter a template declares, so nothing a compiler writes
    reaches this. `c++filt` 2.42 stops the declaration list at the pack rather than
    refusing the name -- `Tp Ty Ty` is `typename... $T0` to it, with the second dropped
    -- and this library keeps every declaration, because dropping one is dropping a name
    a `TL0_<n>_` elsewhere in the signature can refer to.
    """
    parser = _itanium_parser_after_reading(mangled)
    return parser is not None and parser._declaration_after_a_pack


def _reads_a_length_written_with_a_leading_zero(mangled):
    """Whether a `<source-name>` in `mangled` had its length written with a leading zero.

    `<source-name>` is a *positive length number* and an identifier, and a number in
    these grammars has no leading zero -- so `01A` is not a source name. `c++filt` 2.42
    reads it anyway, because libiberty's `d_number` consumes digits and calls `atoi`;
    `llvm-cxxfilt` 18 refuses it. This library reads it as `c++filt` does, which is the
    same side it takes on the legacy `I ... E` argument pack and the old `sr`. No
    compiler writes one, so there is nothing to settle the split against.
    """
    parser = _itanium_parser_after_reading(mangled)
    return parser is not None and parser.reader.padded_length


def _shifted_by_a_template_template_param(mangled):
    """Whether a back-reference in `mangled` resolved at or after the entry a
    `<template-template-param>` took.

    `T_ I ... E` records the parameter *and* the specialisation built over it: two
    grammar components, two candidates by 5.1.10, and `_Z1fI1AiEvT_IT0_ES3_S3_` from
    both g++ 13.3 and clang++ 18.1.3 is readable only with both. `llvm-cxxfilt` 18
    records the second alone, so it refuses that name and answers `char<int>` for
    `_Z1gI1AcEvT_IT0_ES1_IiE`; `c++filt` 2.42 agrees with the declarations and with this.
    Every index at or after the parameter's is one entry out, which is why the test is
    on where the back-reference landed rather than on what it named.
    """
    parser = _itanium_parser_after_reading(mangled)
    return parser is not None and parser._template_template_shifted


def _back_reference_names_a_pack_bound_parameter(mangled):
    """Whether an `S<n>_` in `mangled` named the entry a `<template-param>` bound to a
    pack contributed, and this library resolved it to the pack.

    The three answers are visible in a minimal name. For `_Z1fIiJbcdEEvT_DpT0_`, entry
    one is what `T0_` contributed and entry two is the `Dp` expansion's own; all three
    demanglers agree entry two is the whole pack, and on entry one `llvm-cxxfilt` says
    `bool`, `c++filt` says `double` and this says `bool, char, double`. The references
    pick a member and not the same one -- the first and the last -- which is what says
    neither has a rule to follow here.
    """
    parser = _itanium_parser_after_reading(mangled)
    return parser is not None and parser._pack_named_through_a_back_reference


def _records_a_constrained_placeholder(mangled):
    """Whether this library's reading of `mangled` entered a `Dk`/`DK` type in the table.

    The flag is set wherever one was read, because reading one *is* recording one: the
    composite is a `<type>`. No compiler emits either code, so any name carrying one is
    a name only demanglers read, and the two read its table differently by exactly this
    entry.
    """
    parser = _itanium_parser_after_reading(mangled)
    return parser is not None and parser._constrained_placeholder_recorded


def _nests_a_legacy_argument_pack(mangled):
    """Whether an `I ... E` pack stood as a direct member of another pack.

    `JiIEcE` as much as `JIivEE`: the member need not come first. Asked of the parser,
    which saw the `I` open where a member was expected, because no regex over the name
    can tell an `I` opening a member from one opening a template argument list inside
    a member.
    """
    parser = _itanium_parser_after_reading(mangled)
    return parser is not None and parser._legacy_pack_nested


def _d_back_reference_inside_identifier(mangled):
    """Whether `mangled` contains a back reference targeting strictly inside an LName.

    `_D83TypeInfo_...6__ZQDlFNaNfkbQDkQDnQByZi` has two type back references pointing
    into the 83-character identifier, and both this library and libiberty resolve
    them; no compiler writes one. A target strictly inside an LName is refused by this
    library, while c++filt resolves it.
    """
    parser = _DParser(mangled)
    with contextlib.suppress(Exception):
        parser.parse()
    return parser._backref_inside_lname


def _differs_only_by_the_numbering_rule(mangled, first, second):
    """Whether a reference's answer is this library's own reading under that
    reference's rules -- the ABI's for a closure prefix and the common one for an
    undeduced `auto` -- so that the two disagree on nothing but which compiler wrote
    the name.

    A `__Z` name is read here by Apple's clang's rules, a `_Z` name by the ABI's with
    a retry under the other where a back-reference shows the numbering was wrong; the
    references know only the ABI's closure rule and the common `auto` rule. Where a
    reference reads the name, its spelling is compared with this library's under those
    rules, in the reference's own style. Where this library refuses the name under
    those rules -- a back-reference landing on the closure prefix, or on a template
    with no arguments, which the references print regardless -- or where the reference
    refuses it, the question is whether this library read the name at all, which it
    then did by the other rule. See `ItaniumOptions.closure_prefix_substitution` and
    `undeduced_auto_substitution`.
    """
    theirs = {"closure_prefix_substitution": True, "undeduced_auto_substitution": False}

    def agrees(style_name, reference):
        under_theirs = demangle.demangle(mangled, style=demangle.style(style_name, itanium=theirs))
        if under_theirs == mangled:
            return demangle.demangle(mangled) != mangled
        return under_theirs == reference

    if first is not None:
        return agrees("llvm", first)
    gnu_reference, _ = second
    if gnu_reference is not None:
        return agrees("gnu", gnu_reference)
    try:
        demangle.demangle_strict(mangled, style=demangle.style("llvm", itanium=theirs))
    except demangle.DemanglingError:
        return demangle.demangle(mangled) != mangled
    return False


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
                found[name] = library_reading(scheme, name, style)
    return found


def library_reading(scheme, name, style=None):
    """What this library says about `name` under `scheme`'s entry point, or raises."""
    if scheme == "types":
        return demangle.demangle_type(name, language="itanium", style=style)
    return demangle.demangle_strict(name, language=scheme, style=style)


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
    # Bytes rather than text mode, because text mode translates newlines: libiberty
    # spells a template argument of type `char` as the raw byte, so `foo__H1c13_v` is
    # `foo<'\r'>(void)` with a carriage return in it, and under `text=True` that came
    # back as a line break and threw every answer after it out of step with its name.
    # The reference and this library agree byte for byte on the raw form; a byte that is
    # not UTF-8 is kept as a lone surrogate so the comparison sees it rather than a crash.
    proc = subprocess.run(
        command,
        input=("\n".join(names) + "\n").encode("utf-8", "surrogateescape"),
        capture_output=True,
        timeout=3600 if timeout is None else timeout,
        preexec_fn=None if memory is None else _capped_at(memory),
    )
    lines = proc.stdout.decode("utf-8", "surrogateescape").split("\n")
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
