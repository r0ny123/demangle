"""A PE import thunk: `__imp_` before a whole mangled name.

MinGW and clang targeting Windows name the pointer a DLL import goes through after the
function it imports, so `__imp__Z3foov` is the thunk for `foo()` and, where i386 COFF adds
its underscore, `__imp___Z3foov` too. llvm-cxxfilt 18 reads both as `import thunk for
foo()`; GNU c++filt 2.42 hands both back. Behind the prefix a C name is only a C name,
and stays as written -- which is where this parts from llvm-cxxfilt, whose fallback
reads a bare `<type>` there and spells `__imp_i` as `import thunk for int`.

The expected column is what each reference printed on this machine:

    llvm-cxxfilt   Ubuntu LLVM version 18.1.3
    c++filt        GNU Binutils for Ubuntu 2.42
"""

import pytest

import demangle
from demangle.core.ast import Special

#: (mangled, llvm-style, gnu-style). c++filt refuses every one; the gnu style keeps the
#: prefix and spells the name inside it as c++filt spells that name alone.
IMPORT_THUNKS = [
    ("__imp__Z3foov", "import thunk for foo()", "import thunk for foo()"),
    ("__imp___Z3foov", "import thunk for foo()", "import thunk for foo()"),
    ("__imp__ZTV3Foo", "import thunk for vtable for Foo", "import thunk for vtable for Foo"),
    ("__imp__Z3foov.cold", "import thunk for foo() (.cold)", "import thunk for foo() [clone .cold]"),
    (
        "__imp__ZNSt6vectorIiSaIiEE9push_backERKi",
        "import thunk for std::vector<int, std::allocator<int>>::push_back(int const&)",
        "import thunk for std::vector<int, std::allocator<int> >::push_back(int const&)",
    ),
]


@pytest.mark.parametrize(("mangled", "llvm", "gnu"), IMPORT_THUNKS, ids=[row[0] for row in IMPORT_THUNKS])
def test_both_styles(mangled, llvm, gnu):
    assert demangle.detect(mangled) == "itanium"
    assert demangle.demangle_strict(mangled) == llvm
    assert demangle.demangle_strict(mangled, style="gnu") == gnu


@pytest.mark.parametrize(
    "mangled",
    [
        "__imp_ReadFile",
        "__imp__ReadFile@20",
        "__imp_foo",
        "__imp_Z3foov",
        # A bare type, which llvm-cxxfilt's fallback reads and this does not.
        "__imp_i",
        "__imp_3foo",
        # Not a whole mangled name after the prefix.
        "__imp__Z",
        "__imp__Z3foovE",
        # The prefix is read first, and once. An allocation token behind it is what
        # llvm-cxxfilt's `main` reads and 18 does not; neither is followed here.
        "__imp___alloc_token__Z3foov",
        "__alloc_token___imp__Z3foov",
        "__imp___imp__Z3foov",
    ],
)
def test_what_is_not_one_stays_as_written(mangled):
    assert demangle.demangle(mangled) == mangled
    assert demangle.demangle(mangled, style="gnu") == mangled
    assert demangle.demangle(mangled, language="itanium") == mangled


def test_three_underscores_is_not_a_thunk():
    """`__imp____Z3foov` has no `_block_invoke`, so it is no block invocation either; the
    Objective-C scheme's reading is what auto-detection gives it."""
    assert demangle.demangle("__imp____Z3foov", language="itanium") == "__imp____Z3foov"


#: (mangled, spelling). A Rust name behind the prefix reads as it would alone. Neither
#: rustc-demangle nor GNU c++filt knows the prefix; llvm-cxxfilt reads a legacy one as
#: C++, `import thunk for foo::h0123456789abcdef`, with any `$LT$` escapes left in. The
#: hyper name is one of 686 like it in a RustyStealer sample built for windows-gnu.
RUST_IMPORT_THUNKS = [
    ("__imp__ZN3foo17h0123456789abcdefE", "import thunk for foo"),
    ("__imp___ZN3foo17h0123456789abcdefE", "import thunk for foo"),
    ("__imp__ZN11encoding_rs3GBK17h4c22793200e3489fE", "import thunk for encoding_rs::GBK"),
    (
        "__imp__ZN100_$LT$hyper..client..service..Connect$LT$C$C$B$C$T$GT$$u20$as$u20$tower_service..Service$LT$T$GT$$GT$4call17h1234567890abcdefE",
        "import thunk for <hyper::client::service::Connect<C,B,T> as tower_service::Service<T>>::call",
    ),
    ("__imp__RNvCs1_1a1f", "import thunk for a::f"),
    ("__imp___RNvCs1_1a1f", "import thunk for a::f"),
]


@pytest.mark.parametrize(("mangled", "spelling"), RUST_IMPORT_THUNKS, ids=[row[0][:40] for row in RUST_IMPORT_THUNKS])
def test_a_rust_name_behind_the_prefix_reads_as_rust(mangled, spelling):
    assert demangle.detect(mangled) == "rust"
    assert demangle.demangle_strict(mangled) == spelling
    assert demangle.demangle_strict(mangled, style="gnu") == spelling
    tree = demangle.parse(mangled)
    assert isinstance(tree, Special)
    assert tree.label == "import thunk for "


@pytest.mark.parametrize("mangled", ["__imp_ReadFile", "__imp__ReadFile@20", "__imp_foo", "__imp_Z3foov", "__imp_"])
def test_a_c_name_behind_the_prefix_is_not_claimed(mangled):
    assert demangle.detect(mangled) is None


def test_the_signature_reports_the_thunk_as_a_special():
    found = demangle.signature("__imp__ZN2ns3barIiEEvT_")
    assert found.special == "import thunk for"
    assert found.qualified_name == "ns::bar<int>"
    assert found.namespace == "ns"
    assert found.parameters == ("int",)
    assert found.is_function


def test_the_outermost_label_is_the_thunk():
    found = demangle.signature("__imp__ZTV3Foo.cold")
    assert found.special == "import thunk for"
    assert found.qualified_name == "Foo"
    assert found.decoration == ".cold"


def test_the_tree_holds_the_thunk_as_a_special():
    tree = demangle.parse("__imp__Z3foov")
    assert isinstance(tree, Special)
    assert tree.label == "import thunk for "
    assert tree.inner.spell() == "foo()"
