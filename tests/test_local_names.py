"""`<local-name>`: an entity declared inside another entity.

`Z <function encoding> E <entity>` covers a function-local static, a lambda written in a
function body, a string literal. The third form -- `Z <encoding> Ed [<number>] _ <entity>`
-- covers one written in a *default argument*, and it is where the two references part
company: GNU c++filt names the scope and llvm-cxxfilt does not.

The expected columns are what each reference printed on this machine:

    llvm-cxxfilt   Ubuntu LLVM version 18.1.3
    c++filt        GNU Binutils for Ubuntu 2.42
"""

import pytest

import demangle

#: (mangled, llvm-style, gnu-style). The number is a compact one -- absent is zero --
#: and GNU counts the arguments from one, so `d_` is `#1` and `d0_` is `#2`.
DEFAULT_ARGUMENTS = [
    ("_ZZ1fidEd_1x", "f(int, double)::x", "f(int, double)::{default arg#1}::x"),
    ("_ZZ1fidEd0_1x", "f(int, double)::x", "f(int, double)::{default arg#2}::x"),
    ("_ZZ1fidEd1_1x", "f(int, double)::x", "f(int, double)::{default arg#3}::x"),
]


@pytest.mark.parametrize(("mangled", "llvm", "gnu"), DEFAULT_ARGUMENTS)
def test_each_style_spells_the_scope_the_way_its_reference_does(mangled, llvm, gnu):
    assert demangle.demangle_strict(mangled, style="llvm") == llvm
    assert demangle.demangle_strict(mangled, style="gnu") == gnu


@pytest.mark.parametrize("mangled", [row[0] for row in DEFAULT_ARGUMENTS])
@pytest.mark.parametrize("style", ["llvm", "gnu"])
def test_the_tree_renders_what_the_text_path_spells(mangled, style):
    assert demangle.parse(mangled, style=style).spell(style=style) == demangle.demangle(mangled, style=style)


def test_a_lambda_in_a_default_argument_of_a_shipped_library():
    """From libclang-cpp 18. Both columns are the references' own output.

    Worth pinning because the scope is the only thing separating this lambda from one
    written in `printJson`'s body: without it the two entities demangle to one name.
    """
    mangled = "_ZTSZNK5clang15LocationContext9printJsonERN4llvm11raw_ostreamEPKcjbSt8functionIFvPKS0_EEEd_UlS8_E_"
    signature = (
        "clang::LocationContext::printJson(llvm::raw_ostream&, char const*, unsigned int, bool, "
        "std::function<void (clang::LocationContext const*)>) const"
    )
    assert demangle.demangle_strict(mangled, style="llvm") == (
        f"typeinfo name for {signature}::'lambda'(clang::LocationContext const*)"
    )
    assert demangle.demangle_strict(mangled, style="gnu") == (
        f"typeinfo name for {signature}::{{default arg#1}}::{{lambda(clang::LocationContext const*)#1}}"
    )


def test_the_option_is_what_selects_it():
    mangled = "_ZZ1fidEd_1x"
    off = demangle.style("gnu", itanium={"gnu_default_argument_scope": False})
    assert demangle.demangle_strict(mangled, style=off) == "f(int, double)::x"
    on = demangle.style("llvm", itanium={"gnu_default_argument_scope": True})
    assert demangle.demangle_strict(mangled, style=on) == "f(int, double)::{default arg#1}::x"


def test_the_other_two_forms_are_unchanged():
    assert demangle.demangle_strict("_ZZ1fvE1x") == "f()::x"
    assert demangle.demangle_strict("_ZZ1fvEs") == "f()::string literal"
