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
    # is the last component. `!is_array<T>::value` appears in every `enable_if` in
    # libstdc++ and was two of the differences from c++filt over the shipped libraries.
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
    ("sZfp_", "sizeof... (fp)", "sizeof... ({parm#1})"),
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


def test_the_option_is_what_selects_it():
    mangled = wrap("plLi1ELi2E")
    plain = demangle.style("gnu", itanium={"gnu_expression_spelling": False})
    assert demangle.demangle_strict(mangled, style=plain) == "void g<A<1 + 2> >()"
    bracketed = demangle.style("llvm", itanium={"gnu_expression_spelling": True})
    assert demangle.demangle_strict(mangled, style=bracketed) == "void g<A<(1)+(2)>>()"


@pytest.mark.parametrize(
    ("mangled", "llvm", "gnu"),
    [
        # The comma is the one infix operator whose GNU spelling had a space in it here.
        # GNU writes no space after any operator, this one included -- `(1),(2)` beside
        # `(1)+(2)` -- and llvm-cxxfilt writes `1, 2`, which is a list and does have one.
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
    """Found by mutation: the fold branch printed llvm's spacing under both styles.

    Every column here is what the named reference answers on this machine. The GNU
    spelling differs in both halves -- no spaces around the operator, and the initialiser
    bracketed by kind rather than by precedence -- and this printed neither.
    """
    assert demangle.demangle_strict(mangled, style="llvm") == llvm
    assert demangle.demangle_strict(mangled, style="gnu") == gnu


class TestAConstraintParameterUnderTheGnuStyle:
    """`symbolic_constraint_parameters` decides a spelling, not whether a name parses.

    GNU c++filt substitutes the argument bound to a `<template-param>` inside a
    requires-clause; llvm-cxxfilt spells the parameter by its own mangled name, because
    not every enclosing template's parameters are in scope. Resolving one that is not
    bound used to refuse the whole name, so twelve corpus names read under `--style llvm`
    and came back mangled under `--style gnu` -- among them `std::pair`'s constrained
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

        `R 11SmallerThan I Li1234E E` is a nested requirement; its `1234` became what the
        *next* requirement's `T_` resolved to, so a type requirement naming `T` printed
        `typename 1234` -- a plausible spelling of something the name does not say.
        """
        mangled = (
            "_Z1fIiEviQrqXcvT__EXfp_Xeqfp_cvS0__EXplcvS0__ELi1ER5SmallXmicvS0__ELi1ENXmlcvS0__ELi2EN"
            "R11SmallerThanILi1234EETS0_T1XIS0_ETNS3_4typeETS2_IiEQ11SmallerThanIS0_Li256EEE"
        )
        gnu = demangle.demangle_strict(mangled, style="gnu")
        assert "typename int; typename X<int>; typename X<int>::type; typename X<int>;" in gnu
        assert "requires SmallerThan<int, 256>;" in gnu
        assert "1234" in gnu  # the nested requirement itself still says what it says
