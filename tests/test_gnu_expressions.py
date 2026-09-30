"""How GNU c++filt brackets an expression, which is not how llvm-cxxfilt does.

llvm-cxxfilt brackets by precedence, the way a C++ compiler would print an expression
back: `1 + 2`, `!x`, `sizeof (int) + 2`. GNU c++filt brackets by *kind*. Its
`d_print_subexpr` leaves four things bare -- a name, a qualified name, a braced
initialiser list and a function parameter -- and wraps every other operand of a unary,
binary or ternary operator, and every callee, in brackets. So it writes `(1)+(2)` and
`!(x<int>)` and `(std::declval<int>)()` while still writing `std::x+(2)`, `{1}+(2)` and
`{parm#1}+(2)`.

Neither is more correct: both are printing the same expression. Which is why this is an
option and both spellings are pinned.

Every expected column here was produced by running the mangled name through the named
reference on this machine:

    llvm-cxxfilt   Ubuntu LLVM version 18.1.3
    c++filt        GNU Binutils for Ubuntu 2.42

A name refused by a reference is marked, and its column is left as the other's answer to
show what this reads it as.
"""

import pytest

import demangle


def wrap(expression):
    """`expression` as the sole template argument of a name both references read."""
    return f"_Z1gI1AIX{expression}EEEvv"


#: (mangled expression, llvm-style spelling, gnu-style spelling), each inside `A<...>`.
OPERANDS = [
    # The four kinds c++filt prints bare.
    ("plsr3stdE1xLi2E", "std::x + 2", "std::x+(2)"),
    ("plilLi1EELi2E", "{1} + 2", "{1}+(2)"),
    ("pltl1AELi2E", "A{} + 2", "A{}+(2)"),
    ("plfpTLi2E", "this + 2", "this+(2)"),
    # A qualifier may carry template arguments and the name stays a name: what decides
    # is the last component.
    ("ntsr8is_arrayIiEE5value", "!is_array<int>::value", "!is_array<int>::value"),
    # Arguments on the last component make it a template-id, and c++filt brackets it.
    ("plsr3stdE1xIiELi2E", "std::x<int> + 2", "(std::x<int>)+(2)"),
    # A literal is not a name.
    ("plLi1ELi2E", "1 + 2", "(1)+(2)"),
    # Nor is an operator name, a destructor, or a name rooted at global scope.
    ("plonplLi2E", "operator+ + 2", "(operator+)+(2)"),
    ("plgs1xLi2E", "::x + 2", "(::x)+(2)"),
    # Nor anything with structure: a cast, a call, an address-of, a postfix, an
    # expansion, a local or special entity.
    ("plcvi_Li1EELi2E", "(int)(1) + 2", "((int)(1))+(2)"),
    ("plclsr3stdE1xLi1EELi2E", "std::x(1) + 2", "(std::x(1))+(2)"),
    ("pladL_ZN1A1fEvELi2E", "&A::f() + 2", "(&A::f)+(2)"),
    ("plppfp_Li2E", "fp++ + 2", "({parm#1}++)+(2)"),
    ("plspfp_Li2E", "fp... + 2", "({parm#1}...)+(2)"),
    ("plL_ZZ1fvE1xELi2E", "f()::x + 2", "(f()::x)+(2)"),
    ("plL_ZTV1AELi2E", "vtable for A + 2", "(vtable for A)+(2)"),
    # A data symbol named by an embedded encoding *is* a name.
    ("plL_ZN1A1xEELi2E", "A::x + 2", "A::x+(2)"),
    ("plL_ZSt1xELi2E", "std::x + 2", "std::x+(2)"),
    # The callee of a call is bracketed on the same rule, and only on that rule: a
    # braced list and `this` reach it bare.
    ("clsr3stdE1xLi1EE", "std::x(1)", "std::x(1)"),
    ("clsr3stdE1xIiELi1EE", "std::x<int>(1)", "(std::x<int>)(1)"),
    ("clgs1xLi1EE", "::x(1)", "(::x)(1)"),
    ("clilLi1EELi2EE", "{1}(2)", "{1}(2)"),
    ("clfpTLi2EE", "this(2)", "this(2)"),
    # `sizeof` and `alignof` over an expression are a keyword and then an operand, so
    # the brackets are the operand's. `noexcept` is the one c++filt writes with no space
    # and always with brackets, and a *type* operand is bracketed by both references.
    ("szLi1E", "sizeof (1)", "sizeof (1)"),
    ("szfp_", "sizeof (fp)", "sizeof {parm#1}"),
    ("szsr3stdE1x", "sizeof (std::x)", "sizeof std::x"),
    ("szclfp_E", "sizeof (fp())", "sizeof ({parm#1}())"),
    ("azfp_", "alignof (fp)", "alignof {parm#1}"),
    ("nxfp_", "noexcept (fp)", "noexcept({parm#1})"),
    ("sti", "sizeof (int)", "sizeof (int)"),
    # c++filt refuses `at` and `ti` over a type; the column is what this reads it as.
    ("ati", "alignof (int)", "alignof (int)"),
    ("tii", "typeid (int)", "typeid (int)"),
    # `co_await`, `sizeof...` over a function parameter, `throw`, and an unexpanded
    # pack expansion. The first three are keyword operators whose GNU operand follows
    # the same kind rule as every other one: a name stays bare and anything else is
    # bracketed. The last is an expansion with no pack in it, bracketed the same way.
    ("awLi1E", "co_await 1", "co_await (1)"),
    ("awfp_", "co_await fp", "co_await {parm#1}"),
    # c++filt does not print `sizeof...` at all: it prints the length of the pack it
    # finds behind it, and a function parameter has no pack to count.
    ("sZfp_", "sizeof... (fp)", "0"),
    ("twfp_", "throw fp", "throw {parm#1}"),
    ("twsr3stdE1x", "throw std::x", "throw std::x"),
    ("twLi1E", "throw 1", "throw (1)"),
    ("twclfp_E", "throw fp()", "throw ({parm#1}())"),
    ("spLi1E", "1...", "(1)..."),
    # The conditional: `?` hard against its operands, `:` spaced off them.
    ("quLi1ELi2ELi3E", "1 ? 2 : 3", "(1)?(2) : (3)"),
    ("qufp_sr3stdE1xsr3stdE1y", "fp ? std::x : std::y", "{parm#1}?std::x : std::y"),
]


@pytest.mark.parametrize(("expression", "llvm", "gnu"), OPERANDS)
def test_each_style_brackets_the_way_its_reference_does(expression, llvm, gnu):
    mangled = wrap(expression)
    assert demangle.demangle_strict(mangled, style="llvm") == f"void g<A<{llvm}>>()"
    assert demangle.demangle_strict(mangled, style="gnu") == f"void g<A<{gnu}> >()"


@pytest.mark.parametrize("expression", [row[0] for row in OPERANDS])
@pytest.mark.parametrize("style", ["llvm", "gnu"])
def test_the_tree_renders_what_the_text_path_spells(expression, style):
    mangled = wrap(expression)
    assert demangle.parse(mangled, style=style).spell(style=style) == demangle.demangle(mangled, style=style)


def test_the_shape_as_it_appears_in_a_shipped_library():
    """From libclang-cpp 18: `!is_array<T>::value` inside an `enable_if`.

    Both columns are the references' own output. The name matters because the two ways
    of getting the brackets wrong here are opposite: bracketing the whole qualified name
    because a qualifier carries arguments, or bracketing nothing because the printed
    text happens to start with an identifier.
    """
    mangled = (
        "_ZSt11make_sharedIN5clang4ento30PathDiagnosticControlFlowPieceEJRNS1_22PathDiagnosticLocationES4_"
        "RA20_KcEESt10shared_ptrINSt9enable_ifIXntsr8is_arrayIT_EE5valueESA_E4typeEEDpOT0_"
    )
    for style in ("llvm", "gnu"):
        assert "<!is_array<clang::ento::PathDiagnosticControlFlowPiece>::value," in demangle.demangle_strict(
            mangled, style=style
        )


def test_an_unexpanded_type_pack_expansion_is_bracketed_under_gnu():
    """`Dp` with no pack in it: `(int)...` to c++filt, `int...` to llvm-cxxfilt.

    A name stays bare under both, on the same rule as any other operand.
    """
    assert demangle.demangle_strict("_Z1fDpiv", style="llvm") == "f(int..., void)"
    assert demangle.demangle_strict("_Z1fDpiv", style="gnu") == "f((int)..., void)"
    assert demangle.demangle_strict("_Z1fDp1Av", style="llvm") == "f(A..., void)"
    assert demangle.demangle_strict("_Z1fDp1Av", style="gnu") == "f(A..., void)"


def test_an_unexpanded_pack_over_a_declarator_type_puts_the_ellipsis_after_the_whole_type():
    """`ParameterPackExpansion` prints its child whole and then the dots.

    Putting them in the declarator's name slot -- `void (*...)()`, `int... [3]` --
    would print what neither reference prints. GNU brackets the type first, which
    avoids the problem.
    """
    for mangled, llvm, gnu in (
        ("_Z1fDpFvvEv", "f(void ()..., void)", "f((void ())..., void)"),
        ("_Z1fDpPFvvEv", "f(void (*)()..., void)", "f((void (*)())..., void)"),
        ("_Z1fDpRFvvEv", "f(void (&)()..., void)", "f((void (&)())..., void)"),
        ("_Z1fDpA3_iv", "f(int [3]..., void)", "f((int [3])..., void)"),
        ("_Z1fDpPA3_iv", "f(int (*) [3]..., void)", "f((int (*) [3])..., void)"),
        ("_Z1fDpM1AFvvEv", "f(void (A::*)()..., void)", "f((void (A::*)())..., void)"),
        ("_Z1fDpPKFvvEv", "f(void (*)() const..., void)", "f((void (*)() const)..., void)"),
        ("_Z1fDpFvDpFvvEEv", "f(void (void ()...)..., void)", "f((void ((void ())...))..., void)"),
        ("_Z1fDpPiv", "f(int*..., void)", "f((int*)..., void)"),
    ):
        assert demangle.demangle_strict(mangled, style="llvm") == llvm, mangled
        assert demangle.demangle_strict(mangled, style="gnu") == gnu, mangled
        assert demangle.parse(mangled).spell() == llvm, mangled


def test_the_option_is_what_selects_it():
    mangled = wrap("plLi1ELi2E")
    plain = demangle.style("gnu", itanium={"gnu_expression_spelling": False})
    assert demangle.demangle_strict(mangled, style=plain) == "void g<A<1 + 2> >()"
    bracketed = demangle.style("llvm", itanium={"gnu_expression_spelling": True})
    assert demangle.demangle_strict(mangled, style=bracketed) == "void g<A<(1)+(2)>>()"


@pytest.mark.parametrize(
    ("mangled", "llvm", "gnu"),
    [
        # GNU writes no space after any operator, the comma included.
        ("_Z1fDTcmLi1ELi2EEv", "f(decltype(1, 2), void)", "f(decltype ((1),(2)), void)"),
        (
            "_Z1fDTcmfp_fp0_Eii",
            "f(decltype(fp, fp0), int, int)",
            "f(decltype ({parm#1},{parm#2}), int, int)",
        ),
        (
            "_Z1fIiEDTcmT_T_Ev",
            "decltype(int, int) f<int>()",
            "decltype ((int),(int)) f<int>()",
        ),
    ],
)
def test_a_comma_expression_has_no_space_after_the_comma_under_gnu(mangled, llvm, gnu):
    assert demangle.demangle_strict(mangled, style="llvm") == llvm
    assert demangle.demangle_strict(mangled, style="gnu") == gnu


def test_a_comma_separated_list_keeps_its_space_under_gnu():
    """The separator in a call's arguments is a list's, not the comma *operator*."""
    assert demangle.demangle_strict("_Z1fIiEvT_S0_", style="gnu") == "void f<int>(int, int)"
    assert demangle.demangle_strict("_Z1fILi1ELi2EEvv", style="gnu") == "void f<1, 2>()"


@pytest.mark.parametrize(
    ("mangled", "llvm", "gnu"),
    [
        # A unary fold, both directions: no spaces around the operator or the ellipsis.
        (
            "_Z5foldlIJLi1ELi2EEEv1AIXflplT_EE",
            "void foldl<1, 2>(A<(... + (1, 2))>)",
            "void foldl<1, 2>(A<(...+(1, 2))>)",
        ),
        (
            "_Z5foldrIJLi1ELi2EEEv1AIXfrplT_EE",
            "void foldr<1, 2>(A<((1, 2) + ...)>)",
            "void foldr<1, 2>(A<((1, 2)+...)>)",
        ),
        # A binary fold's initialiser is an operand like any other, so GNU brackets it by
        # kind: a literal gets brackets, a function parameter does not.
        (
            "_Z6foldl1IJLi1ELi2EEEv1AIXfLplLi9ET_EE",
            "void foldl1<1, 2>(A<(9 + ... + (1, 2))>)",
            "void foldl1<1, 2>(A<((9)+...+(1, 2))>)",
        ),
        (
            "_Z6foldr1IJLi1ELi2EEEv1AIXfRplT_Li9EEE",
            "void foldr1<1, 2>(A<((1, 2) + ... + 9)>)",
            "void foldr1<1, 2>(A<((1, 2)+...+(9))>)",
        ),
        (
            "_Z6foldr1IJLi1EEEv1AIXfRplT_fp_EE",
            "void foldr1<1>(A<((1) + ... + fp)>)",
            "void foldr1<1>(A<((1)+...+{parm#1})>)",
        ),
        (
            "_Z6foldl1IJLi1EEEv1AIXfLplplLi1ELi2ET_EE",
            "void foldl1<1>(A<((1 + 2) + ... + (1))>)",
            "void foldl1<1>(A<(((1)+(2))+...+(1))>)",
        ),
        # An expansion over a pack with no members, which still prints its brackets.
        ("_Z5foldlIJEEv1AIXflplT_EE", "void foldl<>(A<(... + ())>)", "void foldl<>(A<(...+())>)"),
    ],
)
def test_a_fold_expression_is_spelled_the_way_each_reference_spells_it(mangled, llvm, gnu):
    """The fold branch must not print llvm's spacing under both styles.

    Every column here is what the named reference answers on this machine. The GNU
    spelling differs in both halves -- no spaces around the operator, and the initialiser
    bracketed by kind rather than by precedence.
    """
    assert demangle.demangle_strict(mangled, style="llvm") == llvm
    assert demangle.demangle_strict(mangled, style="gnu") == gnu


class TestAConstraintParameterUnderTheGnuStyle:
    """`symbolic_constraint_parameters` decides a spelling, not whether a name parses.

    GNU c++filt substitutes the argument bound to a `<template-param>` inside a
    requires-clause; llvm-cxxfilt spells the parameter by its own mangled name, because
    not every enclosing template's parameters are in scope. Resolving one that is not
    bound must not refuse the whole name, or corpus names would read under `--style llvm`
    and come back mangled under `--style gnu` -- among them `std::pair`'s constrained
    constructor, which is what GCC 13 emits for the real `std::pair`. GNU c++filt 2.42
    refuses every one of these itself, so there is no reference answer to follow here;
    what there is, is the rule that a style cannot decide the grammar.
    """

    PAIR = "_ZNSt4pairIidEC2IidQaacl16_S_constructibleITL0__TL0_0_EEntcl10_S_danglesIS2_S3_EEEEOT_OT0_"

    @pytest.mark.parametrize(
        "mangled",
        [
            PAIR,
            "_ZN5test21hIvEEvzQ4TrueITL0__E",
            "_ZN5test21AIiEF1gIvEEvzQaa4TrueIT_E4TrueITL0__E",
            "_ZN5test21jIvQ4TrueITL0__EEEvz",
        ],
    )
    def test_both_styles_read_it(self, mangled):
        for style in ("llvm", "gnu"):
            assert demangle.demangle_strict(mangled, style=style)

    def test_the_clause_is_not_printed_and_the_declaration_is_the_same(self):
        """The clause never reaches the output, so the two styles agree on the name."""
        assert demangle.demangle_strict(self.PAIR, style="llvm") == (
            "std::pair<int, double>::pair<int, double>(int&&, double&&)"
        )
        assert demangle.demangle_strict(self.PAIR, style="gnu") == (
            "std::pair<int, double>::pair<int, double>(int&&, double&&)"
        )

    def test_a_template_id_inside_a_clause_does_not_become_the_parameter_scope(self):
        """A clause names no entity, so what it mentions must not install a `T_` scope.

        `R 11SmallerThan I Li1234E E` is a nested requirement; its `1234` must not become
        what the *next* requirement's `T_` resolves to, or a type requirement naming `T`
        would print `typename 1234` -- a plausible spelling of something the name does
        not say.
        """
        mangled = (
            "_Z1fIiEviQrqXcvT__EXfp_Xeqfp_cvS0__EXplcvS0__ELi1ER5SmallXmicvS0__ELi1ENXmlcvS0__ELi2EN"
            "R11SmallerThanILi1234EETS0_T1XIS0_ETNS3_4typeETS2_IiEQ11SmallerThanIS0_Li256EEE"
        )
        gnu = demangle.demangle_strict(mangled, style="gnu")
        assert "typename int; typename X<int>; typename X<int>::type; typename X<int>;" in gnu
        assert "requires SmallerThan<int, 256>;" in gnu
        assert "1234" in gnu  # the nested requirement itself still says what it says


class TestAConversionToABracedListUnderTheGnuStyle:
    """`cv <type> il ... E`, a functional cast of a braced list: `d_print_comp` writes
    the type in brackets and the list straight after it, `(A){1, 2}`, where the general
    conversion rule would bracket the operand as well, `(A)({1, 2})`.
    `tools/mutate.py --seed 11` reaches it through a `test7` vector of libcxxabi's whose
    `llvm-cxxfilt` spelling, `(test7::C)({1, true})`, is not affected."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fDTcv1AilLi1ELi2EEE", "f(decltype ((A){1, 2}))"),
            ("_Z1fDTcv1AilLi1EEE", "f(decltype ((A){1}))"),
            ("_Z1fDTcv1AilEE", "f(decltype ((A){}))"),
            (
                "_ZN5test73fC2IiEEDTcmcvNS_1CEilLi1ELb1EEcvT__EES2_",
                "decltype (((test7::C){1, true}),((int)())) test7::fC2<int>(int)",
            ),
        ],
    )
    def test_the_gnu_spelling(self, mangled, expected):
        assert demangle.demangle(mangled, style="gnu") == expected

    def test_the_llvm_spelling_is_unchanged(self):
        assert demangle.demangle("_Z1fDTcv1AilLi1ELi2EEE") == "f(decltype((A)({1, 2})))"
        assert (
            demangle.demangle("_ZN5test73fC2IiEEDTcmcvNS_1CEilLi1ELb1EEcvT__EES2_")
            == "decltype((test7::C)({1, true}), (int)()) test7::fC2<int>(int)"
        )


class TestTheObjectOfAMemberAccessIsAnOperand:
    """`d_print_subexpr` runs over the object of a `.` or `->` as over any operand:
    bracketed unless it is a name, a parameter or an initialiser list. Printing the
    object without asking would write `a->ua.i` where `c++filt` writes `(a->ua).i`;
    `tools/mutate.py --seed 6` reaches it through a name only `c++filt` reads."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fDTdtL_Z1aE1iE", "f(decltype (a.i))"),
            ("_Z1fDTdtfp_1iE", "f(decltype ({parm#1}.i))"),
            ("_Z1fDTdtptL_Z1aE2ua1iE", "f(decltype ((a->ua).i))"),
            ("_Z1fDTptdtL_Z1aE2ua1iE", "f(decltype ((a.ua)->i))"),
            ("_Z1fDTdtdtdtL_Z1aE1b1c1dE", "f(decltype (((a.b).c).d))"),
            ("_Z1fDTdtdtfp_1i1jE", "f(decltype (({parm#1}.i).j))"),
            ("_Z1fDTdtclfp_E1iE", "f(decltype (({parm#1}()).i))"),
            ("_Z1fDTdtixfp_Li0E1iE", "f(decltype (({parm#1}[0]).i))"),
        ],
    )
    def test_the_gnu_spelling(self, mangled, expected):
        assert demangle.demangle(mangled, style="gnu") == expected

    def test_the_llvm_spelling_is_unchanged(self):
        assert demangle.demangle("_Z1fDTdtdtdtL_Z1aE1b1c1dE") == "f(decltype(a.b.c.d))"
        assert demangle.demangle("_Z1fDTdtclfp_E1iE") == "f(decltype(fp().i))"


class TestSizeofDotDotDotIsANumberToCxxfilt:
    """`d_print_comp` does not print `sizeof...` at all. For `sZ` it prints
    `d_pack_length` of what `d_find_pack` finds -- the number of members when the
    parameter is bound to a pack, 0 for anything else, a function parameter and an empty
    pack included -- and for `sP` the argument count with expansions counted by their
    members. `llvm-cxxfilt` spells the operator and its operands, `sizeof...(int, char)`,
    which belongs to the llvm style only."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fIJicEEv1XIXsZT_EE", "void f<int, char>(X<2>)"),
            ("_Z1fIJicEEvDTsZT_E", "void f<int, char>(decltype (2))"),
            ("_Z2f0IJfdEEv1XIXsZT_EJDpRT_EE", "void f0<float, double>(X<2, float&, double&>)"),
            ("_Z2f0IJEEv1XIXsZT_EJDpRT_EE", "void f0<>(X<0>)"),
            ("_Z1fIiEvDTsZT_E", "void f<int>(decltype (0))"),
            ("_Z1fIJicEEvDpT_DTsZfp_E", "void f<int, char>(int, char, decltype (0))"),
            ("_Z1fIJicEEv1XIXsPDpT_EEE", "void f<int, char>(X<2>)"),
            ("_Z1fIJicEEv1XIXsPDpT_iEEE", "void f<int, char>(X<3>)"),
            ("_Z1fIJicEEv1XIXsPEEE", "void f<int, char>(X<0>)"),
        ],
    )
    def test_the_gnu_spelling(self, mangled, expected):
        assert demangle.demangle(mangled, style="gnu") == expected

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fIJicEEv1XIXsZT_EE", "void f<int, char>(X<sizeof...(int, char)>)"),
            ("_Z1fIJicEEv1XIXsPDpT_iEEE", "void f<int, char>(X<sizeof... (int, char, int)>)"),
            ("_Z1fIJicEEvDpT_DTsZfp_E", "void f<int, char>(int, char, decltype(sizeof... (fp)))"),
        ],
    )
    def test_the_llvm_spelling_is_unchanged(self, mangled, expected):
        assert demangle.demangle(mangled) == expected


class TestAFoldsPackOperandUnderTheGnuStyle:
    """`d_print_comp` prints a fold's pack operand through `d_print_subexpr` like any
    operand and writes no ellipsis of its own: `(x+...+y)` for a name, `((0)+...+(int))`
    for a literal and a parameter bound to one type. `(y...)` and
    `(int...)` are llvm-cxxfilt's spelling for the one-type case and belong to the
    llvm style only; llvm-cxxfilt refuses the fold with no pack at all."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fIXfLpl1x1yEEvvv", "void f<(x+...+y)>(void, void)"),
            ("_Z1fIXfLplLi1ELi2EEEvvv", "void f<((1)+...+(2))>(void, void)"),
            ("_Z1fIiEvDTfLplLi0ET_E", "void f<int>(decltype (((0)+...+(int))))"),
            ("_Z1fIiEvDTflplT_E", "void f<int>(decltype ((...+(int))))"),
            ("_Z1fIJicEEvDTfLplLi0ET_E", "void f<int, char>(decltype (((0)+...+(int, char))))"),
            ("_Z1fIJicEEvDTflplT_E", "void f<int, char>(decltype ((...+(int, char))))"),
        ],
    )
    def test_the_gnu_spelling(self, mangled, expected):
        assert demangle.demangle(mangled, style="gnu") == expected

    def test_the_llvm_spelling_is_unchanged(self):
        assert demangle.demangle("_Z1fIiEvDTfLplLi0ET_E") == "void f<int>(decltype((0 + ... + (int...))))"
        assert demangle.demangle("_Z1fIJicEEvDTflplT_E") == "void f<int, char>(decltype((... + (int, char))))"


class TestADesignatedInitialiserUnderTheGnuStyle:
    """`.n=(42)`, `.n=x`, `[1]=(42)`, `[1 ... 3]=(42)`, `.n.m=(42)`: no spaces round
    the `=`, and the value an operand `d_print_subexpr` brackets by kind. llvm-cxxfilt's
    `.n = 42` belongs to the llvm style only. The libcxxabi vector
    `_Z1fIXtl1Edi1nLi4EEEEvv` carries one."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fIXtl1Edi1nLi42EEEEvv", "void f<E{.n=(42)}>()"),
            ("_Z1fIXtl1Edi1nL_Z1xEEEEvv", "void f<E{.n=x}>()"),
            ("_Z1fIXtl1EdxLi1ELi42EEEEvv", "void f<E{[1]=(42)}>()"),
            ("_Z1fIXtl1EdXLi1ELi3ELi42EEEEvv", "void f<E{[1 ... 3]=(42)}>()"),
            ("_Z1fIXtl1Edi1ndi1mLi42EEEEvv", "void f<E{.n.m=(42)}>()"),
            ("_Z1fIXtl1Edi1ntl1FLi1EEEEEvv", "void f<E{.n=F{1}}>()"),
        ],
    )
    def test_the_gnu_spelling(self, mangled, expected):
        assert demangle.demangle(mangled, style="gnu") == expected

    def test_the_llvm_spelling_is_unchanged(self):
        assert demangle.demangle("_Z1fIXtl1Edi1ndi1mLi42EEEEvv") == "void f<E{.n.m = 42}>()"
        assert demangle.demangle("_Z1fIXtl1EdXLi1ELi3ELi42EEEEvv") == "void f<E{[1 ... 3] = 42}>()"


class TestGnuSpellingsOfLessCommonOperands:
    """Each is `c++filt`'s, checked against it, and each keeps llvm-cxxfilt's spelling
    under the llvm style."""

    @pytest.mark.parametrize(
        ("mangled", "gnu", "llvm"),
        [
            # A comma expression as a template argument is any binary operator to
            # c++filt, each operand bracketed by kind and nothing round the whole.
            (
                "_ZN5Casts8implicitILj4EEEvPN9enable_ifIXcmT_Li4EEvE4typeE",
                "void Casts::implicit<4u>(enable_if<(4u),(4), void>::type*)",
                "void Casts::implicit<4u>(enable_if<(4u, 4), void>::type*)",
            ),
            # A member with template arguments is a template, not a name, and bracketed.
            ("_Z1fDTdtfp_1fIiEE", "f(decltype ({parm#1}.(f<int>)))", "f(decltype(fp.f<int>))"),
            ("_Z1fDTptfp_1fE", "f(decltype ({parm#1}->f))", "f(decltype(fp->f))"),
            (
                "_ZN1A1gIiEEDTcldtptfpT1b1fIT_EEEv",
                "decltype (((this->b).(f<int>))()) A::g<int>()",
                "decltype(this->b.f<int>()) A::g<int>()",
            ),
            # delete's operand is bracketed by kind.
            (
                "_ZN2nFIXgsdlLi4EEXdaLi4EEEEvv",
                "void nF<::delete (4), delete[] (4)>()",
                "void nF<::delete 4, delete[] 4>()",
            ),
            ("_Z1fDTdlfp_E", "f(decltype (delete {parm#1}))", "f(decltype(delete fp))"),
            ("_Z1fDTdaL_Z1pEE", "f(decltype (delete[] p))", "f(decltype(delete[] p))"),
            # `d_source_name` takes any of three markers between `_GLOBAL_` and `N`.
            (
                "_ZN4llvm12_GLOBAL_.N_1L15EFLAGS_OverlapsE",
                "llvm::(anonymous namespace)::EFLAGS_Overlaps",
                "llvm::_GLOBAL_.N_1::EFLAGS_Overlaps",
            ),
            (
                "_ZN4llvm12_GLOBAL_$N_1L15EFLAGS_OverlapsE",
                "llvm::(anonymous namespace)::EFLAGS_Overlaps",
                "llvm::_GLOBAL_$N_1::EFLAGS_Overlaps",
            ),
        ],
    )
    def test_both_styles(self, mangled, gnu, llvm):
        assert demangle.demangle(mangled, style="gnu") == gnu
        assert demangle.demangle(mangled) == llvm


class TestAnArgumentListsRequiresClauseUnderTheGnuStyle:
    """`I ... Q <constraint> E` on the entity's own template arguments: `llvm-cxxfilt`
    prints nothing for it, and `c++filt` prints it after the parameters with the
    arguments bound, `void f<int>(int) requires C<int>` -- and after the encoding's own
    clause where both are present. Printing nothing under both styles would leave
    one name short in the purpose-built gnu corpus: `modern::measured`, whose
    constraint is `Sized<std::__cxx11::basic_string<...>>`."""

    @pytest.mark.parametrize(
        ("mangled", "gnu", "llvm"),
        [
            ("_Z1fIiQ1CIT_EEvT_", "void f<int>(int) requires C<int>", "void f<int>(int)"),
            ("_Z1fIiQaa1CIT_E1DIT_EEvT_", "void f<int>(int) requires (C<int>)&&(D<int>)", "void f<int>(int)"),
            (
                "_Z1fIJicEQ1CIDpT_EEvDpT_",
                "void f<int, char>(int, char) requires C<int, char>",
                "void f<int, char>(int, char)",
            ),
            (
                "_Z1fIiQ1CIT_EEvT_Q1DIT_E",
                "void f<int>(int) requires D<int> requires C<int>",
                "void f<int>(int) requires D<T>",
            ),
            # A clause inside a parameter's type is that type's; c++filt refuses the name.
            ("_Z1f1XIiQ1CIT_EE", "f(X<int>)", "f(X<int>)"),
        ],
    )
    def test_both_styles(self, mangled, gnu, llvm):
        assert demangle.demangle(mangled, style="gnu") == gnu
        assert demangle.demangle(mangled) == llvm


class TestTheScopeOfAnSrNNameIsASubstitutionToGcc:
    """`srN T_ 3foo E 1v` is `T::foo::v`. The ABI says qualifier levels are not
    substitution candidates and Clang writes names that way; g++ mangles the scope as
    a nested-name type and records it, so after `decltype(T::foo::v + 1)` it writes
    the parameter `typename T::foo` as `S2_` where Clang writes `NS1_3fooE`. GNU
    c++filt reads every `srN` as a type and numbers as g++ does; llvm-cxxfilt numbers
    as Clang does, and takes g++'s `S2_` to be the decltype. Both names here were
    compiled from the same source, one with each compiler."""

    GCC = "_Z2e1I1QEDTplsrNT_3fooE1vLi1EES1_S2_"
    CLANG = "_Z2e1I1QEDTplsrNT_3fooE1vLi1EES1_NS1_3fooE"

    def test_the_gnu_style_numbers_as_gcc(self):
        assert demangle.demangle_strict(self.GCC, style="gnu") == "decltype (Q::foo::v+(1)) e1<Q>(Q, Q::foo)"
        assert demangle.demangle_strict(self.CLANG, style="gnu") == "decltype (Q::foo::v+(1)) e1<Q>(Q, Q::foo)"

    def test_the_llvm_style_numbers_as_clang(self):
        assert demangle.demangle_strict(self.CLANG) == "decltype(Q::foo::v + 1) e1<Q>(Q, Q::foo)"
        assert demangle.demangle_strict(self.GCC) == "decltype(Q::foo::v + 1) e1<Q>(Q, decltype(Q::foo::v + 1))"

    def test_the_option_is_what_selects_it(self):
        on = demangle.style("llvm", itanium={"gnu_unresolved_scope_substitution": True})
        assert demangle.demangle_strict(self.GCC, style=on) == "decltype(Q::foo::v + 1) e1<Q>(Q, Q::foo)"
        off = demangle.style("gnu", itanium={"gnu_unresolved_scope_substitution": False})
        assert demangle.demangle_strict(self.GCC, style=off) == (
            "decltype (Q::foo::v+(1)) e1<Q>(Q, decltype (Q::foo::v+(1)))"
        )

    def test_a_substituted_scope_with_arguments(self):
        # `srN S2_ IPS3_E E 1w`: the scope `A::B::C<int*>` is `S6_` to g++.
        mangled = "_Z2j6IiEDTplsrN1A1B1CIT_EE1wsrNS2_IPS3_EE1wES3_S6_"
        assert demangle.demangle_strict(mangled, style="gnu") == (
            "decltype (A::B::C<int>::w+A::B::C<int*>::w) j6<int>(int, A::B::C<int*>)"
        )


class TestAGreaterThanIsBracketedWhereverItStands:
    """libiberty's `d_print_comp` wraps "an expression which uses the greater-than
    operator in an extra layer of parens so that it does not get confused with the '>'
    which ends the template parameters" -- wherever it stands, not only inside an
    argument list, and on top of whatever brackets its position earns. `>>` gets no
    such layer, so at the top of a template argument it stands bare. Every spelling here is `c++filt` 2.42's."""

    @pytest.mark.parametrize(
        "mangled, gnu, llvm",
        [
            ("_Z1fIiEDTgtfp_fp_ET_", "decltype (({parm#1}>{parm#1})) f<int>(int)", "decltype(fp > fp) f<int>(int)"),
            (
                "_Z1fIiEDTplgtfp_fp_fp_ET_",
                "decltype ((({parm#1}>{parm#1}))+{parm#1}) f<int>(int)",
                "decltype((fp > fp) + fp) f<int>(int)",
            ),
            (
                "_Z1fIiEDTplfp_gtfp_fp_ET_",
                "decltype ({parm#1}+(({parm#1}>{parm#1}))) f<int>(int)",
                "decltype(fp + (fp > fp)) f<int>(int)",
            ),
            ("_Z1fIXgtLi1ELi2EEEvv", "void f<((1)>(2))>()", "void f<(1 > 2)>()"),
            ("_Z1fIXplgtLi1ELi2ELi3EEEvv", "void f<(((1)>(2)))+(3)>()", "void f<(1 > 2) + 3>()"),
            ("_Z1fIXgtgtLi1ELi2ELi3EEEvv", "void f<((((1)>(2)))>(3))>()", "void f<(1 > 2 > 3)>()"),
            (
                "_Z1fIiEDTclgtfp_fp_EET_",
                "decltype ((({parm#1}>{parm#1}))()) f<int>(int)",
                # llvm-cxxfilt's own spelling of a bracketed callee, which is no
                # bracket at all.
                "decltype(fp > fp()) f<int>(int)",
            ),
            ("_Z1fIXrsLi1ELi2EEEvv", "void f<(1)>>(2)>()", "void f<(1 >> 2)>()"),
            ("_Z1fIiEDTrsfp_fp_ET_", "decltype ({parm#1}>>{parm#1}) f<int>(int)", "decltype(fp >> fp) f<int>(int)"),
            ("_Z1fIXcmgtLi1ELi2ELi3EEEvv", "void f<(((1)>(2))),(3)>()", "void f<(1 > 2, 3)>()"),
        ],
    )
    def test_both_styles(self, mangled, gnu, llvm):
        assert demangle.demangle_strict(mangled, style="gnu") == gnu
        assert demangle.demangle_strict(mangled) == llvm


class TestAQualifiedOperatorNameIsAPlainOperand:
    """`d_print_subexpr` brackets everything but a name, a qualified name, a function
    parameter and an initialiser list. `sr T_ on an` is the qualified name `A::operator&`
    and stands bare under `&`, as a callee and after `.`; the same operator unqualified
    is an operator and is bracketed, and with template arguments it is a template-id
    and is bracketed again. `_Z1mI1AEDTadsrT_onanES1_` is `decltype(&T::operator&)`
    compiled by both g++ 13 and Clang 18."""

    @pytest.mark.parametrize(
        "mangled, gnu, llvm",
        [
            ("_Z1mI1AEDTadsrT_onanES1_", "decltype (&A::operator&) m<A>(A)", "decltype(&A::operator&) m<A>(A)"),
            ("_Z1mI1AEDTadsrT_anES1_", "decltype (&A::operator&) m<A>(A)", "decltype(&A::operator&) m<A>(A)"),
            (
                "_Z1mI1AEDTadsrNT_1BEonanES1_",
                "decltype (&A::B::operator&) m<A>(A)",
                "decltype(&A::B::operator&) m<A>(A)",
            ),
            (
                "_Z1mI1AEDTclsrT_onanfp_EES1_",
                "decltype (A::operator&({parm#1})) m<A>(A)",
                "decltype(A::operator&(fp)) m<A>(A)",
            ),
            (
                "_Z1mI1AEDTdtfp_srT_onanES1_",
                "decltype ({parm#1}.A::operator&) m<A>(A)",
                "decltype(fp.A::operator&) m<A>(A)",
            ),
            (
                "_Z1mI1AEDTadsrT_onanIiEES1_",
                "decltype (&(A::operator&<int>)) m<A>(A)",
                "decltype(&A::operator&<int>) m<A>(A)",
            ),
            (
                "_Z1mI1AEDTadonanES1_",
                "decltype (&(operator&)) m<A>(decltype (&(operator&)))",
                "decltype(&operator&) m<A>(decltype(&operator&))",
            ),
        ],
    )
    def test_both_styles(self, mangled, gnu, llvm):
        assert demangle.demangle_strict(mangled, style="gnu") == gnu
        assert demangle.demangle_strict(mangled) == llvm


class TestAPackExpansionInAnExpressionUnderTheGnuStyle:
    """The gnu side of `TestAPackExpansionInAnExpression`: an expanded pattern is its
    members, and an unexpanded one is bracketed by kind, `{parm#1}...` bare and
    `(sizeof {parm#1})...` not."""

    @pytest.mark.parametrize(
        "mangled, expected",
        [
            ("_Z2f1IJicEEDTcl1gspfp_EEDpT_", "decltype (g({parm#1}...)) f1<int, char>(int, char)"),
            ("_Z2f2IJicEEDTcl1gspplfp_Li1EEEDpT_", "decltype (g(({parm#1}+(1))...)) f2<int, char>(int, char)"),
            ("_Z2f3IJicEEDTcl1gspszfp_EEDpT_", "decltype (g((sizeof {parm#1})...)) f3<int, char>(int, char)"),
            ("_Z2f8IJicEEDTcl1gspadfp_EEDpT_", "decltype (g((&{parm#1})...)) f8<int, char>(int, char)"),
            (
                "_Z2f4IJicEEDTcl1gspscT_fp_EEDpS0_",
                "decltype (g(static_cast<int>({parm#1}), static_cast<char>({parm#1}))) f4<int, char>(int, char)",
            ),
            (
                "_Z2f5IJicEEDTcl1gspcvT_fp_EEDpS0_",
                "decltype (g((int){parm#1}, (char){parm#1})) f5<int, char>(int, char)",
            ),
            ("_Z2f7IJicEEDTcl1gspstT_EEDpS0_", "decltype (g(sizeof (int), sizeof (char))) f7<int, char>(int, char)"),
            ("_Z3q67IJiEEDTcv1Aspfp_EDpT_", "decltype ((A)({parm#1}...)) q67<int>(int)"),
            ("_Z1fIJicEEDTcl1gspdtfp_1mEEDpT_", "decltype (g(({parm#1}.m)...)) f<int, char>(int, char)"),
        ],
    )
    def test_the_gnu_spelling(self, mangled, expected):
        assert demangle.demangle_strict(mangled, style="gnu") == expected


class TestACastsOperandIsBracketedByKind:
    """`cv <type> <expression>` prints its operand through `d_print_subexpr`: a name, a
    parameter or a braced list bare, anything else in brackets. `T(t)` is written
    `cvT_fp_` by both compilers, so `(int){parm#1}` is the common case, and it is not
    bracketed."""

    @pytest.mark.parametrize(
        "mangled, expected",
        [
            ("_Z1fIiEDTcvT_fp_ES0_", "decltype ((int){parm#1}) f<int>(int)"),
            ("_Z1fIiEDTcvT_1xES0_", "decltype ((int)x) f<int>(int)"),
            ("_Z1fIiEDTcvT_srT_1xES0_", "decltype ((int)int::x) f<int>(int)"),
            ("_Z1fIiEDTcvT_tlT_EES0_", "decltype ((int)int{}) f<int>(int)"),
            ("_Z1fIiEDTcvT_ilLi1EEES0_", "decltype ((int){1}) f<int>(int)"),
            ("_Z1fIiEDTcvT_Li1EES0_", "decltype ((int)(1)) f<int>(int)"),
            ("_Z1fIiEDTcvT_plfp_fp_ES0_", "decltype ((int)({parm#1}+{parm#1})) f<int>(int)"),
            ("_Z1fIiEDTcvT_ngfp_ES0_", "decltype ((int)(-{parm#1})) f<int>(int)"),
            ("_Z1fIiEDTcvT_3fooIiEES0_", "decltype ((int)(foo<int>)) f<int>(int)"),
            ("_Z1fIiEDTdtcvT_fp_1mES0_", "decltype (((int){parm#1}).m) f<int>(int)"),
            # The list form keeps its brackets, as the braces keep theirs.
            ("_Z1fIiEDTcvT__fp_fp_EES0_", "decltype ((int)({parm#1}, {parm#1})) f<int>(int)"),
        ],
    )
    def test_the_gnu_spelling(self, mangled, expected):
        assert demangle.demangle_strict(mangled, style="gnu") == expected

    def test_the_llvm_spelling_is_unchanged(self):
        assert demangle.demangle_strict("_Z1fIiEDTcvT_fp_ES0_") == "decltype((int)(fp)) f<int>(int)"


class TestAResolvedCalleeIsPrintedByName:
    """`decltype(h(t))` with `h` resolved is `clL_Z1hiEfp_E`, the callee an encoding
    with a function type. libiberty prints such a callee through its name alone --
    "function call used in an expression should not have printed types of the
    function arguments" -- and the name as an operand, bracketed unless it is a plain
    one. The first three names were compiled with g++ 13 and Clang 18."""

    @pytest.mark.parametrize(
        "mangled, gnu, llvm",
        [
            ("_Z2c3IiEDTclL_Z1hiEfp_EET_", "decltype (h({parm#1})) c3<int>(int)", "decltype(h(int)(fp)) c3<int>(int)"),
            (
                "_Z2c2IiEDTclL_ZN1A1sEiEfp_EET_",
                "decltype (A::s({parm#1})) c2<int>(int)",
                "decltype(A::s(int)(fp)) c2<int>(int)",
            ),
            (
                "_Z2c3IiEDTcladL_Z1hiEfp_EET_",
                "decltype ((&(h(int)))({parm#1})) c3<int>(int)",
                "decltype(&h(int)(fp)) c3<int>(int)",
            ),
            ("_Z1fIiEDTclL_Z1hiEEET_", "decltype (h()) f<int>(int)", "decltype(h(int)()) f<int>(int)"),
            # `S0_` is the `A` the callee's own nested name entered; `S_` would be the
            # function template `f`, which no type can be.
            (
                "_Z1fIiEDTclL_ZN1AplERKS0_Efp_EET_",
                "decltype (A::operator+({parm#1})) f<int>(int)",
                "decltype(A::operator+(A const&)(fp)) f<int>(int)",
            ),
            (
                "_Z1fIiEDTclL_ZN1AC1EvEfp_EET_",
                "decltype (A::A({parm#1})) f<int>(int)",
                "decltype(A::A()(fp)) f<int>(int)",
            ),
            # Bracketed: template arguments, a member's qualifiers, a local entity, an
            # operator function at namespace scope.
            (
                "_Z1fIiEDTclL_Z1hIiEiT_Efp_EET_",
                "decltype ((h<int>)({parm#1})) f<int>(int)",
                "decltype(int h<int>(int)(fp)) f<int>(int)",
            ),
            (
                "_Z1fIiEDTclL_ZNK1A1sEiEfp_EET_",
                "decltype ((A::s const)({parm#1})) f<int>(int)",
                "decltype(A::s(int) const(fp)) f<int>(int)",
            ),
            (
                "_Z1fIiEDTclL_ZZ1hvE1xEfp_EET_",
                "decltype ((h()::x)({parm#1})) f<int>(int)",
                "decltype(h()::x(fp)) f<int>(int)",
            ),
            (
                "_Z1fIiEDTclL_ZplRK1AS1_Efp_EET_",
                "decltype ((operator+)({parm#1})) f<int>(int)",
                "decltype(operator+(A const&, A const)(fp)) f<int>(int)",
            ),
            # A data name and a special name, as under any other operator.
            ("_Z1fIiEDTclL_ZN1A1xEEfp_EET_", "decltype (A::x({parm#1})) f<int>(int)", "decltype(A::x(fp)) f<int>(int)"),
            (
                "_Z1fIiEDTclL_ZTV1AEfp_EET_",
                "decltype ((vtable for A)({parm#1})) f<int>(int)",
                "decltype(vtable for A(fp)) f<int>(int)",
            ),
        ],
    )
    def test_both_styles(self, mangled, gnu, llvm):
        assert demangle.demangle_strict(mangled, style="gnu") == gnu
        assert demangle.demangle_strict(mangled) == llvm

    def test_the_option_is_what_selects_it(self):
        off = demangle.style("gnu", itanium={"gnu_entity_operand_spelling": False})
        assert (
            demangle.demangle_strict("_Z2c3IiEDTclL_Z1hiEfp_EET_", style=off)
            == "decltype ((h(int))({parm#1})) c3<int>(int)"
        )


class TestAnEmptyPackIsAnEmptyEntryToCxxfilt:
    """c++filt prints what an empty pack expands to, which is nothing, and keeps the
    comma: `std::thread::thread<main::{lambda()#1}, , void>`, a name every program that
    starts a thread on a no-argument callable carries, compiled by g++ 13. It does the
    same in every comma-separated list -- a parameter list, a call's arguments, a
    braced initialiser, a new-expression's -- and drops the empty entries at the end of
    a list, so `f<int, JE>` is `f<int>`. llvm-cxxfilt drops every one, and so does the
    llvm style. See `gnu_empty_pack_spelling`."""

    @pytest.mark.parametrize(
        "mangled, gnu, llvm",
        [
            (
                "_ZNSt6threadC1IZ4mainEUlvE_JEvEEOT_DpOT0_",
                "std::thread::thread<main::{lambda()#1}, , void>(main::{lambda()#1}&&)",
                "std::thread::thread<main::'lambda'(), void>(main::'lambda'()&&)",
            ),
            ("_Z1fIJEiEvv", "void f<, int>()", "void f<int>()"),
            ("_Z1fIiJEiEvv", "void f<int, , int>()", "void f<int, int>()"),
            ("_Z1fIJEJEiEvv", "void f<, , int>()", "void f<int>()"),
            # Trailing empty entries go, however many.
            ("_Z1fIiJEEvv", "void f<int>()", "void f<int>()"),
            ("_Z1fIiJEJEEvv", "void f<int>()", "void f<int>()"),
            ("_Z1fIJEJEEvv", "void f<>()", "void f<>()"),
            # A parameter list, in the encoding and in a function type.
            ("_Z1fIJEiEvDpT_T0_", "void f<, int>(, int)", "void f<int>(int)"),
            ("_Z1fIJEiEvT0_DpT_", "void f<, int>(int)", "void f<int>(int)"),
            ("_Z1fIJEiEvPFvDpT_T0_E", "void f<, int>(void (*)(, int))", "void f<int>(void (*)(int))"),
            ("_Z1fIJEiEvPFvT0_DpT_E", "void f<, int>(void (*)(int))", "void f<int>(void (*)(int))"),
            # A call, a braced initialiser, a new-expression.
            ("_Z1fIJEiEDTcl1gspT_T0_EEDpT_T0_", "decltype (g(, int)) f<, int>(, int)", "decltype(g(int)) f<int>(int)"),
            ("_Z1fIJEiEDTcl1gspT_EEDpT_T0_", "decltype (g()) f<, int>(, int)", "decltype(g()) f<int>(int)"),
            ("_Z1fIJEiEDTtl1AspT_T0_EEDpT_T0_", "decltype (A{, int}) f<, int>(, int)", "decltype(A{int}) f<int>(int)"),
            (
                "_Z1fIJEiEDTnw_T0_pispT_T0_EEDpT_T0_",
                "decltype (new int(, int)) f<, int>(, int)",
                "decltype(new int(int)) f<int>(int)",
            ),
            # An expansion inside a type's argument list.
            ("_Z1fIJEiEv1AIDpT_T0_E", "void f<, int>(A<, int>)", "void f<int>(A<int>)"),
            ("_Z1fIJEiEv1AIT0_DpT_E", "void f<, int>(A<int>)", "void f<int>(A<int>)"),
            ("_Z1fIJEiEv1AIDpT_E", "void f<, int>(A<>)", "void f<int>(A<>)"),
        ],
    )
    def test_both_styles(self, mangled, gnu, llvm):
        assert demangle.demangle_strict(mangled, style="gnu") == gnu
        assert demangle.demangle_strict(mangled) == llvm

    def test_the_option_is_what_selects_it(self):
        off = demangle.style("gnu", itanium={"gnu_empty_pack_spelling": False})
        assert demangle.demangle_strict("_Z1fIJEiEvDpT_T0_", style=off) == "void f<int>(int)"
        on = demangle.style("llvm", itanium={"gnu_empty_pack_spelling": True})
        assert demangle.demangle_strict("_Z1fIJEiEvDpT_T0_", style=on) == "void f<, int>(, int)"


class TestADeclaredTemplateParameterUnderTheGnuStyle:
    """`Tn`, `Tp`, `Tt` and the numbering: four spellings GNU c++filt does differently.

    A `<template-param-decl>` is what a generic lambda writes for each parameter it
    declares, and the two references spell the whole production apart:

    * **The name goes after the type.** `Tn PA3_i` is `int (*$N) [3]` to llvm-cxxfilt,
      which is where a declarator goes, and `int (*) [3] $N0` to c++filt, which is not a
      declaration anyone can write. Every shape with a declarator parts the same way:
      an array, a reference to one, a function, a pointer to a member function.
    * **A `Tp` puts its ellipsis on the type**, not on the name: `typename... $T0` and
      `int [3]... $N0` against llvm-cxxfilt's `typename ...$T` and `int ...$N [3]`.
    * **A `Tt` is spelled `class`**, and its own declarations are written without their
      names: `template<typename, int> class $TT0`.
    * **The numbering is one sequence across kinds.** llvm-cxxfilt counts each kind
      separately and leaves the first of each unsuffixed, so `Ty Ty Tn i` is
      `$T, $T0, $N`; c++filt numbers every *printed* declaration from zero in one
      sequence, `$T0, $T1, $N2`. A declaration inside a `Tt`, which it does not print,
      spends nothing: `Ty Tt Ty E Ty` is `$T0, $TT1, $T2`.

    Read off `c++filt` 2.42 with probes rather than guessed: every ordering of the three
    kinds, every declarator shape above, and two levels of `Tt` nesting -- 242 names in
    all, agreeing byte for byte. The one place the two lists part is a declaration
    *after* a pack, which is ill-formed C++ and which c++filt drops rather than refuses;
    `TestADeclarationAfterAPackIsKept` below pins that this library keeps it.
    """

    @pytest.mark.parametrize(
        ("declarations", "expected"),
        [
            ("Ty", "typename $T0"),
            ("Tni", "int $N0"),
            ("TnPi", "int* $N0"),
            ("TnA3_i", "int [3] $N0"),
            ("TnPA3_i", "int (*) [3] $N0"),
            ("TnRA3_i", "int (&) [3] $N0"),
            ("TnPFicE", "int (*)(char) $N0"),
            ("TnM1Ai", "int A::* $N0"),
            ("TnM1AFivE", "int (A::*)() $N0"),
            ("TnFivE", "int () $N0"),
            ("TpTy", "typename... $T0"),
            ("TpTni", "int... $N0"),
            ("TpTnA3_i", "int [3]... $N0"),
            ("TpTnPA3_i", "int (*) [3]... $N0"),
            ("TtTyE", "template<typename> class $TT0"),
            ("TtTyTyE", "template<typename, typename> class $TT0"),
            ("TtTyTniE", "template<typename, int> class $TT0"),
            ("TtTyTnPA3_iE", "template<typename, int (*) [3]> class $TT0"),
            ("TtTpTyE", "template<typename...> class $TT0"),
            ("TtTtTyEE", "template<template<typename> class> class $TT0"),
            ("TpTtTyE", "template<typename> class... $TT0"),
            # The numbering is one sequence, and a `Tt`'s own list spends nothing.
            ("TyTyTni", "typename $T0, typename $T1, int $N2"),
            ("TniTyTni", "int $N0, typename $T1, int $N2"),
            ("TyTniTy", "typename $T0, int $N1, typename $T2"),
            ("TyTtTyETy", "typename $T0, template<typename> class $TT1, typename $T2"),
            ("TtTyETy", "template<typename> class $TT0, typename $T1"),
            ("TtTniETni", "template<int> class $TT0, int $N1"),
        ],
    )
    def test_the_gnu_spelling_is_cxxfilts(self, declarations, expected):
        mangled = f"_ZNK1xMUl{declarations}vE_clIiEEDav"
        spelled = demangle.demangle_strict(mangled, style="gnu")
        assert spelled == f"auto x::{{lambda<{expected}>()#1}}::operator()<int>() const"

    @pytest.mark.parametrize(
        ("declarations", "expected"),
        [
            ("TnPA3_i", "int (*$N) [3]"),
            ("TpTnPA3_i", "int (*...$N) [3]"),
            # A type with no right part is spaced off the name; one with a right part
            # is not. `int*$N` is what neither reference writes.
            ("TnPi", "int* $N"),
            ("TnM1Ai", "int A::* $N"),
            ("TnA3_i", "int$N [3]"),
            ("TnFivE", "int $N()"),
            ("TtTyTniE", "template<typename $T, int $N> typename $TT"),
            ("TyTyTni", "typename $T, typename $T0, int $N"),
            ("TyTtTyETy", "typename $T, template<typename $T0> typename $TT, typename $T1"),
        ],
    )
    def test_the_llvm_spelling_is_unchanged(self, declarations, expected):
        mangled = f"_ZNK1xMUl{declarations}vE_clIiEEDav"
        spelled = demangle.demangle_strict(mangled)
        assert spelled == f"auto x::'lambda'<{expected}>()::operator()<int>() const"


class TestADeclarationAfterAPackIsKept:
    """A pack must be the last parameter a template declares, so `Tp Ty Ty` is a shape
    no compiler writes. `c++filt` 2.42 stops the list at the pack and prints
    `typename... $T0`, dropping the second declaration; this keeps both.

    Dropping one is dropping a name: a `TL0_1_` elsewhere in the same signature refers
    to it, and an option chooses how a name is spelled rather than which names read at
    all. `tools/enumerate.py` carries the reason instead.
    """

    @pytest.mark.parametrize(
        ("declarations", "expected"),
        [
            ("TpTyTy", "typename... $T0, typename $T1"),
            ("TpTyTni", "typename... $T0, int $N1"),
            ("TyTpTyTy", "typename $T0, typename... $T1, typename $T2"),
        ],
    )
    def test_every_declaration_is_spelled(self, declarations, expected):
        mangled = f"_ZNK1xMUl{declarations}vE_clIiEEDav"
        spelled = demangle.demangle_strict(mangled, style="gnu")
        assert spelled == f"auto x::{{lambda<{expected}>()#1}}::operator()<int>() const"
