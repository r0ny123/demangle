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


def test_the_option_is_what_selects_it():
    mangled = wrap("plLi1ELi2E")
    plain = demangle.style("gnu", itanium={"gnu_expression_spelling": False})
    assert demangle.demangle_strict(mangled, style=plain) == "void g<A<1 + 2> >()"
    bracketed = demangle.style("llvm", itanium={"gnu_expression_spelling": True})
    assert demangle.demangle_strict(mangled, style=bracketed) == "void g<A<(1)+(2)>>()"
