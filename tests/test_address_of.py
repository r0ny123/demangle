"""`&entity` as a template argument, where the two references spell it differently.

`X ad L _Z... E E` is how a pointer to a function or to a member becomes a non-type
template argument, and it is common: the shape appears 60 times in the Itanium symbols of
the shared libraries shipped on Ubuntu 24.04, all of them in LLVM's `sandboxir`.

llvm-cxxfilt prints the whole declaration the mangling carries -- `&A::f(int)`. GNU
c++filt prints what the source wrote, `&A::f`, and brackets the declaration for every
shape where it cannot: `&(A::f() const)`, `&(void A::f<int>())`, `&(f())`. The rule is
read off c++filt rather than guessed, which is what the vectors below are: each one was
run through both references on this machine and the two columns are what they printed.

    llvm-cxxfilt   Ubuntu LLVM version 18.1.3
    c++filt        GNU Binutils for Ubuntu 2.42
"""

import pytest

import demangle

#: (mangled, llvm-style, gnu-style). Every one of these is a template argument list
#: holding one expression, so the difference is visible with nothing else around it.
VECTORS = [
    # The shape the rule exists for: a plain qualified function. GNU drops the parameter
    # list because `&A::f` is a pointer to member and the parameters are not part of it.
    ("_Z1gI1AIXadL_ZN1A1fEvEEEEvv", "void g<A<&A::f()>>()", "void g<A<&A::f> >()"),
    ("_Z1gI1AIXadL_ZN1A1fEiEEEEvv", "void g<A<&A::f(int)>>()", "void g<A<&A::f> >()"),
    # A namespace-scope function is qualified too, and so is an `St` one.
    ("_Z1gI1AIXadL_ZN1N1fEiEEEEvv", "void g<A<&N::f(int)>>()", "void g<A<&N::f> >()"),
    ("_Z1gI1AIXadL_ZSt1fiEEEEvv", "void g<A<&std::f(int)>>()", "void g<A<&std::f> >()"),
    # Constructors, destructors and operators are names like any other.
    ("_Z1gI1AIXadL_ZN1AC1EvEEEEvv", "void g<A<&A::A()>>()", "void g<A<&A::A> >()"),
    ("_Z1gI1AIXadL_ZN1AD1EvEEEEvv", "void g<A<&A::~A()>>()", "void g<A<&A::~A> >()"),
    # `S_` inside the embedded encoding indexes the *enclosing* name's substitution
    # table -- entry 0 is `g` -- which is why both references print `g&` for it. The
    # shared table is deliberate; see `expr_primary`.
    ("_Z1gI1AIXadL_ZN1AplERS_EEEEvv", "void g<A<&A::operator+(g&)>>()", "void g<A<&A::operator+> >()"),
    # Unqualified: there is no scope to print, so GNU brackets the declaration instead.
    # `N 1f E` is a nested name with one component and counts as unqualified, the same as
    # the bare `_Z1fv` -- both references were asked and both say so.
    ("_Z1gI1AIXadL_Z1fvEEEEvv", "void g<A<&f()>>()", "void g<A<&(f())> >()"),
    ("_Z1gI1AIXadL_ZN1fEvEEEEvv", "void g<A<&f()>>()", "void g<A<&(f())> >()"),
    # A cv- or ref-qualifier is part of the pointer's type and cannot be dropped, so the
    # whole declaration is printed -- and bracketed, because it is no longer a name.
    ("_Z1gI1AIXadL_ZNK1A1fEvEEEEvv", "void g<A<&A::f() const>>()", "void g<A<&(A::f() const)> >()"),
    ("_Z1gI1AIXadL_ZNV1A1fEvEEEEvv", "void g<A<&A::f() volatile>>()", "void g<A<&(A::f() volatile)> >()"),
    ("_Z1gI1AIXadL_ZNR1A1fEvEEEEvv", "void g<A<&A::f() &>>()", "void g<A<&(A::f() &)> >()"),
    # So is a requires-clause, and so is the return type a template specialisation
    # carries -- `&(void A::f<int>())`, not `&A::f`.
    (
        "_Z1gI1AIXadL_ZN1A1fEvQ4trueEEEEvv",
        "void g<A<&A::f() requires true>>()",
        "void g<A<&(A::f() requires true)> >()",
    ),
    (
        "_Z1gI1AIXadL_ZN1A1fIiEEvvEEEEvv",
        "void g<A<&void A::f<int>()>>()",
        "void g<A<&(void A::f<int>())> >()",
    ),
    # Data, not a function: a qualified name is already a name and GNU leaves it alone.
    ("_Z1gI1AIXadL_ZN1A1xEEEEEvv", "void g<A<&A::x>>()", "void g<A<&A::x> >()"),
    ("_Z1gI1AIXadL_Z1xEEEEvv", "void g<A<&x>>()", "void g<A<&x> >()"),
    # A local entity is not a name either -- it spells with the enclosing function --
    # and neither is a special name.
    ("_Z1gI1AIXadL_ZZ1fvE1xEEEEvv", "void g<A<&f()::x>>()", "void g<A<&(f()::x)> >()"),
    ("_Z1gI1AIXadL_ZTV1AEEEEvv", "void g<A<&vtable for A>>()", "void g<A<&(vtable for A)> >()"),
    (
        "_Z1gI1AIXadL_ZThn8_N1A1fEvEEEEvv",
        "void g<A<&non-virtual thunk to A::f()>>()",
        "void g<A<&(non-virtual thunk to A::f())> >()",
    ),
    # Other unary operators get the brackets and never the name: dropping a parameter
    # list is specific to taking an address.
    ("_Z1gI1AIXdeL_ZN1A1fEvEEEEvv", "void g<A<*A::f()>>()", "void g<A<*(A::f())> >()"),
    ("_Z1gI1AIXntL_ZN1A1fEvEEEEvv", "void g<A<!A::f()>>()", "void g<A<!(A::f())> >()"),
]


@pytest.mark.parametrize(("mangled", "llvm", "gnu"), VECTORS)
def test_each_style_spells_it_the_way_its_reference_does(mangled, llvm, gnu):
    assert demangle.demangle_strict(mangled, style="llvm") == llvm
    assert demangle.demangle_strict(mangled, style="gnu") == gnu


@pytest.mark.parametrize("mangled", [row[0] for row in VECTORS])
@pytest.mark.parametrize("style", ["llvm", "gnu"])
def test_the_tree_renders_what_the_text_path_spells(mangled, style):
    assert demangle.parse(mangled, style=style).spell(style=style) == demangle.demangle(mangled, style=style)


def test_the_shape_as_it_appears_in_a_shipped_library():
    """One of the 60, from libLLVM 20.1's sandboxir. Both columns are the references'."""
    mangled = (
        "_ZTIN4llvm9sandboxir13GenericSetterIXadL_ZNKS0_10SwitchInst12getConditionEvEE"
        "XadL_ZNS2_12setConditionEPNS0_5ValueEEEEE"
    )
    assert demangle.demangle_strict(mangled, style="llvm") == (
        "typeinfo for llvm::sandboxir::GenericSetter<&llvm::sandboxir::SwitchInst::getCondition() const, "
        "&llvm::sandboxir::SwitchInst::setCondition(llvm::sandboxir::Value*)>"
    )
    assert demangle.demangle_strict(mangled, style="gnu") == (
        "typeinfo for llvm::sandboxir::GenericSetter<&(llvm::sandboxir::SwitchInst::getCondition() const), "
        "&llvm::sandboxir::SwitchInst::setCondition>"
    )


def test_the_option_is_what_selects_it_and_llvm_style_is_unaffected():
    """Turning it off leaves the operand to the general rule, which brackets it.

    That is `gnu_expression_spelling`, still on in the style: with neither, the spelling
    is llvm-cxxfilt's. The two options answer different questions -- "may the parameter
    list be dropped" and "does this operand need brackets" -- and this is what shows it.
    """
    mangled = "_Z1gI1AIXadL_ZN1A1fEvEEEEvv"
    gnu_off = demangle.style("gnu", itanium={"gnu_entity_operand_spelling": False})
    assert demangle.demangle_strict(mangled, style=gnu_off) == "void g<A<&(A::f())> >()"
    plain = demangle.style("gnu", itanium={"gnu_entity_operand_spelling": False, "gnu_expression_spelling": False})
    assert demangle.demangle_strict(mangled, style=plain) == "void g<A<&A::f()> >()"
    llvm_on = demangle.style("llvm", itanium={"gnu_entity_operand_spelling": True})
    assert demangle.demangle_strict(mangled, style=llvm_on) == "void g<A<&A::f>>()"
