"""Every `<special-name>` production, in both styles.

ABI 5.1.4 and the GNU extensions libiberty adds to it. These are the productions that
name something *about* an entity -- its vtable, its guard variable, a thunk to it -- and
they are worth pinning together rather than one at a time: the two references word two of
them differently, refuse five of them between them, and the table that drives them is one
place where a wrong entry is invisible until someone meets the name in a binary.

The expected column is what each reference printed on this machine, recorded so the test
needs neither installed:

    llvm-cxxfilt   Ubuntu LLVM version 18.1.3
    c++filt        GNU Binutils for Ubuntu 2.42
"""

import pytest

import demangle
from demangle.core.errors import DemanglingError, LimitExceeded

#: (mangled, llvm-style, gnu-style, which references read it). Where the two spellings
#: differ, both are recorded; where a reference refuses the name, the note says so and
#: the expectation is the other one's.
SPECIAL_NAMES = [
    ("_ZTV1A", "vtable for A", "vtable for A", "both"),
    ("_ZTT1A", "VTT for A", "VTT for A", "both"),
    ("_ZTI1A", "typeinfo for A", "typeinfo for A", "both"),
    ("_ZTS1A", "typeinfo name for A", "typeinfo name for A", "both"),
    ("_ZGV1x", "guard variable for x", "guard variable for x", "both"),
    # c++filt refuses this one but numbers the temporary by its seq-id where it reads
    # one, `_ZGRZ1fvE1x_` being `reference temporary #0 for f()::x`.
    ("_ZGR1x0_", "reference temporary for x", "reference temporary #1 for x", "llvm; c++filt refuses"),
    ("_ZTC1A0_1B", "construction vtable for B-in-A", "construction vtable for B-in-A", "both"),
    ("_ZThn8_N1A1fEv", "non-virtual thunk to A::f()", "non-virtual thunk to A::f()", "both"),
    ("_ZTv0_n24_N1A1fEv", "virtual thunk to A::f()", "virtual thunk to A::f()", "both"),
    (
        "_ZTcv0_n24_v0_n24_N1A1fEv",
        "covariant return thunk to A::f()",
        "covariant return thunk to A::f()",
        "both",
    ),
    # The two the references word differently. They name the same entity: `TH` is the
    # initialisation routine for a thread-local and `TW` the wrapper that calls it.
    (
        "_ZTH1x",
        "thread-local initialization routine for x",
        "TLS init function for x",
        "both, worded differently",
    ),
    (
        "_ZTW1x",
        "thread-local wrapper routine for x",
        "TLS wrapper function for x",
        "both, worded differently",
    ),
    # GNU extensions. llvm-cxxfilt refuses all five; libiberty's `d_special_name` reads
    # them, and so do we -- a name a reference reads and we refuse is a gap.
    ("_ZGTtN1A1fEv", "transaction clone for A::f()", "transaction clone for A::f()", "c++filt only"),
    (
        "_ZGTnN1A1fEv",
        "non-transaction clone for A::f()",
        "non-transaction clone for A::f()",
        "c++filt only",
    ),
    ("_ZTF1A", "typeinfo fn for A", "typeinfo fn for A", "c++filt only"),
    ("_ZTJ1A", "java Class for A", "java Class for A", "c++filt only"),
    ("_ZGA1x", "hidden alias for x", "hidden alias for x", "c++filt only"),
]


@pytest.mark.parametrize(("mangled", "llvm", "gnu", "note"), SPECIAL_NAMES, ids=[n[0] for n in SPECIAL_NAMES])
def test_both_styles(mangled, llvm, gnu, note):
    assert demangle.demangle_strict(mangled) == llvm, note
    assert demangle.demangle_strict(mangled, style="gnu") == gnu, note


def test_the_two_styles_differ_exactly_where_the_references_do():
    """A guard on the option: adding a divergence must be deliberate, not incidental."""
    differ = {name for name, llvm, gnu, _ in SPECIAL_NAMES if llvm != gnu}
    assert differ == {"_ZTH1x", "_ZTW1x", "_ZGR1x0_"}


class TestStillRefused:
    """Shapes that look like special names and are not.

    A table lookup one character too eager reads the encoding after it as an operand and
    produces a confident answer for a name nobody wrote.
    """

    @pytest.mark.parametrize(
        "mangled",
        [
            "_ZTX1A",  # no such code
            "_ZGX1x",
            "_ZTH",  # the code with nothing to operate on
            "_ZTF",
            "_ZGA",
            "_ZTJ",
            "_ZTC1A0_",  # a construction vtable missing its base
            "_ZGT",  # a clone marker with no kind and no encoding
            "_ZGVN1A1fEv",  # a guard variable for a function, which has none
            "_ZTHN1A1fEv",  # neither does a thread-local init routine ...
            "_ZTWN1A1fEv",  # ... nor its wrapper
            "_ZGVGV1x",  # a guard variable for a guard variable
        ],
    )
    def test_refused(self, mangled):
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled)
        assert demangle.demangle(mangled) == mangled


def test_a_special_name_over_an_encoding_is_still_bounded():
    """`GA` takes an <encoding>, which may be another special name: `_ZGAGAGA...`."""
    with pytest.raises(LimitExceeded):
        demangle.demangle_strict("_Z" + "GA" * 4000 + "1x")


class TestAModuleInitializerNamesItsModule:
    """`GI <module-name>`: the name is not optional. `parseModuleNameOpt` reads none
    and `llvm-cxxfilt` refuses `_ZGI`, where this spelled `initializer for module `
    with nothing after it. `tools/mutate.py --seed 4`."""

    def test_the_spelling(self):
        assert demangle.demangle("_ZGIW1a") == "initializer for module a"
        assert demangle.demangle("_ZGIW1aWP1b") == "initializer for module a:b"

    def test_no_module_is_refused(self):
        assert demangle.demangle("_ZGI") == "_ZGI"


class TestAConstructorInAModuleRepeatsTheBareName:
    """`CtorDtorName` prints the scope's `getBaseName()`, and a `ModuleEntity`'s base
    name is the name inside it: `_ZNW4llvm6ModuleC1Ev` is `Module@llvm::Module()` to
    both references, where this wrote `Module@llvm::Module@llvm()`. The destructor the
    same, and the tags a constructor carries of its own stay. `tools/mutate.py
    --count 200000`."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_ZN1NW4llvm6ModuleC1Ev", "N::Module@llvm::Module()"),
            ("_ZN1NW4llvm6ModuleD2Ev", "N::Module@llvm::~Module()"),
            ("_ZNW4llvm3FooB3ABIC1Ev", "Foo@llvm[abi:ABI]::Foo()"),
            ("_ZNW4llvm3FooC1B3tagEv", "Foo@llvm::Foo[abi:tag]()"),
            ("_ZNW4llvm3FooD2B3tagEv", "Foo@llvm::~Foo[abi:tag]()"),
            ("_ZNW4llvmW3sub3FooC1Ev", "Foo@llvm.sub::Foo()"),
            ("_ZN1NW4llvm6ModuleC1ERKS1_", "N::Module@llvm::Module(N::Module@llvm const&)"),
        ],
    )
    def test_both_styles(self, mangled, expected):
        assert demangle.demangle(mangled) == expected
        assert demangle.demangle(mangled, style="gnu") == expected


class TestAStructuredBindingNamesSomething:
    """`DC <source-name>+ E`: one name at least. Both references refuse `DCE`, and
    spelling `[]` from the empty list read `_ZN12_GLOBAL__N_41ADCED0Ev` as the
    destructor of a binding of nothing. `tools/mutate.py --seed 8`."""

    def test_refused(self):
        for mangled in ("_ZDCE", "_ZN12_GLOBAL__N_41ADCED0Ev"):
            assert demangle.demangle(mangled) == mangled

    def test_the_spelling(self):
        assert demangle.demangle("_ZDC1a1bE") == "[a, b]"
        assert demangle.demangle("_ZN1ADC1aEE") == "A::[a]"


class TestAnObjectNameIsNotAnEncoding:
    """`GV`, `TH`, `TW` and `GR` take an <object name>: data, with no function type after
    a local entity. `parseSpecialName` reads the name and returns, and both references
    refuse what is left over; read through the same path as a function's own local
    name, `_ZGVZ1fvE1gv` came back `guard variable for f()::g()`, a guard for a function.
    `tools/mutate.py --seed 9` and `--seed 10`."""

    @pytest.mark.parametrize(
        "mangled",
        ["_ZGVZ1fvE1gv", "_ZGVZ1fvE1gi", "_ZTHZ1fvE1gv", "_ZGVZ8getMutexvE12HandlesMuttex", "_ZGRZ1fvE1gvE"],
    )
    def test_refused(self, mangled):
        assert demangle.demangle(mangled) == mangled

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_ZGVZ1fvE1g", "guard variable for f()::g"),
            ("_ZGVZ1fvE1g_0", "guard variable for f()::g"),
            ("_ZGVZN1A1fEvE1x", "guard variable for A::f()::x"),
            ("_ZGRZ1fvE1g_", "reference temporary for f()::g"),
            # A function's own local name still carries its signature.
            ("_ZZ1fvE1gv", "f()::g()"),
        ],
    )
    def test_the_spelling(self, mangled, expected):
        assert demangle.demangle(mangled) == expected


class TestAnObjectNamesEnclosingFunctionIsAWholeEncoding:
    """`_ZGVZZN1A1fEvENKUlvE_clEvE1y`: a guard variable for a static inside a lambda's
    call operator, itself inside `A::f()`. `_in_special_name` says the special name's
    object takes no signature, and it was left set while the object's *enclosing
    function* was read -- a whole encoding, and here one holding a local name of its
    own. That inner local name then took no signature either, and the encoding around
    it read the signature back with no qualifiers to put on it: the `const` of
    `operator()` went missing. Every archive on an Ubuntu 24.04 box carries the
    shape; `llvm-cxxfilt` 18 and `c++filt` 2.42 agree on every spelling here."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_ZGVZZN1A1fEvENKUlvE_clEvE1y", "guard variable for A::f()::'lambda'()::operator()() const::y"),
            ("_ZGVZZN1A1fEvENK1B1gEvE1y", "guard variable for A::f()::B::g() const::y"),
            ("_ZGVZZN1A1fEvENKR1B1gEvE1y", "guard variable for A::f()::B::g() const &::y"),
            ("_ZTHZZN1A1fEvENK1B1gEvE1y", "thread-local initialization routine for A::f()::B::g() const::y"),
            (
                "_ZTWZZ1fIiEvT_ENKUlvE_clEvE1x",
                "thread-local wrapper routine for void f<int>(int)::'lambda'()::operator()() const::x",
            ),
            ("_ZGRZZN1A1fEvENKUlvE_clEvE1y_", "reference temporary for A::f()::'lambda'()::operator()() const::y"),
            (
                "_ZGVZZZN1A1fEvENKUlvE_clEvENKUlvE_clEvE1z",
                "guard variable for A::f()::'lambda'()::operator()() const::'lambda'()::operator()() const::z",
            ),
            # The object itself still takes no signature.
            ("_ZGVZZ1fvE1gvE1x", "guard variable for f()::g()::x"),
        ],
    )
    def test_the_qualifiers_stay(self, mangled, expected):
        assert demangle.demangle_strict(mangled) == expected

    def test_a_guard_for_a_function_is_still_refused(self):
        assert demangle.demangle("_ZGVZ1fvE1gv") == "_ZGVZ1fvE1gv"

    def test_cxxfilt_numbers_a_reference_temporary(self):
        assert demangle.demangle_strict("_ZGRZ1fvE1x_", style="gnu") == "reference temporary #0 for f()::x"
        assert demangle.demangle_strict("_ZGRZ1fvE1x0_", style="gnu") == "reference temporary #1 for f()::x"
        assert demangle.demangle_strict("_ZGRZ1fvE1x_") == "reference temporary for f()::x"
