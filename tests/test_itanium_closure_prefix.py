"""GCC 12 and earlier, and Apple's clang, leave a closure prefix out of the substitution
table; the ABI, upstream clang and GCC 13 count it.

A lambda in a variable's initializer is named through the variable: `ns::g3::'lambda'
(ns::Box<int>, ns::Box<int>)` is written `_ZNK2ns2g3MUlNS_3BoxIiEES?_E_cl...`, where the
`M` ends the <closure-prefix> `ns::g3`. A <prefix> is a substitution candidate, so the
`?` is `2`: `ns` (S_), `ns::g3` (S0_), `ns::Box` (S1_), `ns::Box<int>` (S2_). GCC 12
(`-fabi-version=17`) and every version before it wrote the `M` and skipped the entry, so
it wrote `1`, and every back-reference after the lambda's opening is one lower than the
ABI says. GCC 13 (`-fabi-version=18`) fixed it, and still emits the old spelling as an
alias beside the new, so a binary built today can carry both; a binary built by GCC 12
carries only the old. Apple's clang writes only the old too: Homebrew's macOS bottles
of Apache Arrow, DuckDB and RocksDB carry 663 names that read only with the prefix left
out and none that read only with it in, where upstream clang 13 through 18 -- targeting
Darwin included, checked with `--target=arm64-apple-darwin` -- enter it. So the Mach-O
underscore decides the default, as it does for Apple's `auto` rule.

Every demangler reads the old spelling by the ABI's rule. llvm-cxxfilt 18, LLVM's main
branch and GNU c++filt 2.42 all answer `ns::g3::'lambda'(ns::Box<int>, ns::Box)::
operator()(ns::Box, ns::Box) const` -- a template with no arguments, `ns::Box`, standing
as a parameter type -- and for a generic lambda `operator()<int>(ns::g1, ns::Box,
ns::Box)`, a variable's name as one. No type is either, and that is the signal: a
back-reference that names a closure prefix, or a template with no arguments after it,
as a type has been read by the wrong rule, and the name is read again with the prefix
left out of the table. Every name here was read off an object file g++ 13.3.0 or
clang 18.1.3 wrote; see `tools/corpus_sources/reference_defects/closure_prefix.cpp`.
"""

import pytest

import demangle
from demangle.core.errors import DemanglingError


def read(name, **options):
    return demangle.demangle(name, style=demangle.style("llvm", itanium=options))


def strict(name, **options):
    return demangle.demangle_strict(name, style=demangle.style("llvm", itanium=options))


#: `inline auto g3 = [](Box<int> b, Box<int> c) { return 1; };` in namespace `ns`, as
#: g++ writes it under `-fabi-version=17` and under 18.
OLD = "_ZNK2ns2g3MUlNS_3BoxIiEES1_E_clES1_S1_"
NEW = "_ZNK2ns2g3MUlNS_3BoxIiEES2_E_clES2_S2_"
SPELLED = "ns::g3::'lambda'(ns::Box<int>, ns::Box<int>)::operator()(ns::Box<int>, ns::Box<int>) const"

#: `inline auto g1 = [](auto a, Box<int> b, Box<int> c) { return a; };`, whose old
#: spelling's first parameter lands on the closure prefix itself.
OLD_GENERIC = "_ZNK2ns2g1MUlT_NS_3BoxIiEES2_E_clIiEEDaS0_S2_S2_"
NEW_GENERIC = "_ZNK2ns2g1MUlT_NS_3BoxIiEES3_E_clIiEEDaS1_S3_S3_"
SPELLED_GENERIC = (
    "auto ns::g1::'lambda'(auto, ns::Box<int>, ns::Box<int>)::operator()<int>(int, ns::Box<int>, ns::Box<int>) const"
)


class TestTheRule:
    def test_the_abi_spelling_reads_by_the_abi_rule(self):
        assert read(NEW) == SPELLED
        assert read(NEW_GENERIC) == SPELLED_GENERIC

    def test_gcc_12s_spelling_reads_by_gcc_12s_rule(self):
        assert read(OLD) == SPELLED
        assert read(OLD_GENERIC) == SPELLED_GENERIC

    def test_both_spellings_say_the_same_thing(self):
        assert read(OLD) == read(NEW)
        assert read(OLD_GENERIC) == read(NEW_GENERIC)

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # `[](auto a, auto b, Box<int> c, Box<int> d)`, under `-fabi-version=17`
            (
                "_ZNK2ns2g4MUlT_T0_NS_3BoxIiEES3_E_clIidEEDaS0_S1_S3_S3_",
                "auto ns::g4::'lambda'(auto, auto, ns::Box<int>, ns::Box<int>)"
                "::operator()<int, double>(int, double, ns::Box<int>, ns::Box<int>) const",
            ),
            # `[]<class... Ts>(Ts... ts, Box<int> c, Box<int> d)`
            (
                "_ZNK2ns2g5MUlDpT_NS_3BoxIiEES3_E_clIJiEEEDaS1_S3_S3_",
                "auto ns::g5::'lambda'(auto..., ns::Box<int>, ns::Box<int>)"
                "::operator()<int>(int, ns::Box<int>, ns::Box<int>) const",
            ),
            # `[](Box<int> b) { return [](Box<int> c, Box<int> d) { return 1; }; }`: the
            # outer lambda, and the inner one whose local name repeats the outer's.
            (
                "_ZNK2ns6nestedMUlNS_3BoxIiEEE_clES1_",
                "ns::nested::'lambda'(ns::Box<int>)::operator()(ns::Box<int>) const",
            ),
            (
                "_ZZNK2ns6nestedMUlNS_3BoxIiEEE_clES1_ENKUlS1_S1_E_clES1_S1_",
                "ns::nested::'lambda'(ns::Box<int>)::operator()(ns::Box<int>) const"
                "::'lambda'(ns::Box<int>, ns::Box<int>)::operator()(ns::Box<int>, ns::Box<int>) const",
            ),
            # libcxxabi's own vector `_ZNK1xMUlTyT_E_clIiEEDaS_`, which LLVM's test file
            # expects to read `operator()<int>(x)`: the variable as a parameter type.
            ("_ZNK1xMUlTyT_E_clIiEEDaS_", "auto x::'lambda'<typename $T>($T)::operator()<int>(int) const"),
        ],
    )
    def test_every_old_spelling_g_plus_plus_wrote(self, mangled, expected):
        assert read(mangled) == expected
        assert demangle.demangle(mangled, style="gnu") != mangled


class TestWhatTheRetryKeysOn:
    """The wrong rule shows as a back-reference naming something no type can be."""

    def test_a_closure_prefix_is_not_a_type(self):
        with pytest.raises(DemanglingError, match="names nothing a type can be"):
            strict(OLD_GENERIC, closure_prefix_substitution=True)

    def test_a_template_with_no_arguments_is_not_a_type(self):
        with pytest.raises(DemanglingError, match="names nothing a type can be"):
            strict(OLD, closure_prefix_substitution=True)

    def test_a_template_with_no_arguments_is_not_a_type_anywhere(self):
        """The same signal outside a closure, where there is no other rule to try:
        `_Z1fN2ns3BoxIiEES0_` back-references `ns::Box`, the template, as its second
        parameter. `llvm-cxxfilt` and `c++filt` print `f(ns::Box<int>, ns::Box)`."""
        with pytest.raises(DemanglingError, match="names nothing a type can be"):
            demangle.demangle_strict("_Z1fN2ns3BoxIiEES0_")
        assert demangle.demangle("_Z1fN2ns3BoxIiEES0_") == "_Z1fN2ns3BoxIiEES0_"
        assert demangle.demangle("_Z1fN2ns3BoxIiEES0_IcE") == "f(ns::Box<int>, ns::Box<char>)"

    def test_the_abi_spelling_never_trips_it(self):
        assert strict(NEW, closure_prefix_substitution=True) == SPELLED
        assert strict(NEW_GENERIC, closure_prefix_substitution=True) == SPELLED_GENERIC


class TestForcingARule:
    def test_true_is_the_abi_and_skips_the_retry(self):
        assert read(NEW, closure_prefix_substitution=True) == SPELLED
        assert read(OLD, closure_prefix_substitution=True) == OLD

    def test_false_is_gcc_12_and_skips_the_retry(self):
        assert read(OLD, closure_prefix_substitution=False) == SPELLED
        # An ABI-numbered name under GCC 12's rule overruns the table, or lands on a
        # different type: here `S2_` is past the end.
        assert read(NEW, closure_prefix_substitution=False) == NEW

    def test_the_limit_of_the_retry(self):
        """A shifted reference that lands on a *type* trips nothing, and reads as that
        type. `[](Box<int> b) { return [](Box<int> c, Box<int> d) ...; }`'s inner lambda,
        ABI-numbered, read under GCC 12's rule: its `S2_` is the outer closure type
        rather than `ns::Box<int>`, a plausible declaration that nothing in the name
        contradicts. Nothing in a name says which compiler wrote it; a caller who knows
        the binary is GCC 12's sets the rule."""
        inner = "_ZZNK2ns6nestedMUlNS_3BoxIiEEE_clES2_ENKUlS2_S2_E_clES2_S2_"
        right = (
            "ns::nested::'lambda'(ns::Box<int>)::operator()(ns::Box<int>) const"
            "::'lambda'(ns::Box<int>, ns::Box<int>)::operator()(ns::Box<int>, ns::Box<int>) const"
        )
        assert read(inner) == right
        assert read(inner, closure_prefix_substitution=False) != right
        assert read(inner, closure_prefix_substitution=False) != inner


class TestTheOtherRuleAndTheLimitOfTheSignal:
    """A reference one entry short lands in range far more often than past the end, so
    the same signal serves Apple's `auto` rule too -- and only where what it lands on is
    nothing a type can be. libceres in Homebrew's bottle, an Apple clang build: the
    second argument of libc++'s `__func<_Fp, _Alloc, _Rp(_ArgTypes...)>` is written
    `SK_`, the closure under Apple's rule and `std::__1::allocator` itself under the
    common one. `allocator<std::__1::allocator>` is the shape a template template
    argument has, so nothing in the grammar calls it wrong: the Mach-O underscore is
    what says which rule, and the name reads right with it and wrong without it, as
    `tests/conformance/itanium-reference-defects.txt` records.
    """

    NAME = (
        "_ZNKSt3__110__function6__funcIZZN5ceres8internal14ParallelInvokeIZNKS3_21PartitionedMatrixViewILi2ELi2ELi2EE"
        "27RightMultiplyAndAccumulateEEPKdPdEUliE_EEvPNS3_11ContextImplEiiiOT_iENKUlRSD_E_clIKSG_EEDaSF_EUlvE_"
        "NS_9allocatorISK_EEFvvEE11target_typeEv"
    )

    def test_with_the_underscore_the_allocator_takes_the_closure(self):
        spelled = demangle.demangle("_" + self.NAME)
        assert "std::__1::allocator<std::__1::allocator>" not in spelled
        assert "::'lambda'(), std::__1::allocator<auto void ceres::internal::ParallelInvoke" in spelled

    def test_without_it_the_shape_is_legal_and_nothing_trips(self):
        assert "std::__1::allocator<std::__1::allocator>" in demangle.demangle(self.NAME)
        assert demangle.demangle(
            self.NAME, style=demangle.style("llvm", itanium={"undeduced_auto_substitution": True})
        ) == demangle.demangle("_" + self.NAME)

    def test_a_bare_template_is_an_argument_but_not_a_parameter(self):
        """`T<ns::Box>` supplies a template template parameter; `T<ns::Box*>` and
        `f(..., ns::Box)` supply nothing a type can be."""
        assert demangle.demangle("_Z1gN2ns3BoxIiEE1TIS0_E") == "g(ns::Box<int>, T<ns::Box>)"
        assert demangle.demangle("_Z1fN2ns3BoxIiEE1TIJS0_iEE") == "f(ns::Box<int>, T<ns::Box, int>)"
        for name in ("_Z1gN2ns3BoxIiEE1TIPS0_E", "_Z1fN2ns3BoxIiEES0_"):
            with pytest.raises(DemanglingError, match="names nothing a type can be"):
                demangle.demangle_strict(name)


class TestApplesRule:
    """A `__Z` name is read with the closure prefix left out, which is what Apple's
    clang wrote. Each name here is from a Homebrew macOS bottle, and each expected value
    is the declaration: RocksDB's `barrier_func_ = [](const Endpoint&, const Endpoint&)
    -> bool`, cpp-httplib's `std::function<bool()> is_connection_closed = []() { return
    true; }`, and libc++'s `__func<_Fp, _Alloc, _Rp(_ArgTypes...)>` with `_Alloc` an
    `allocator<_Fp>` -- the closure, not the member it was initialised in."""

    def test_rocksdb(self):
        assert (
            demangle.demangle(
                "__ZNKSt3__110__function6__funcIN7rocksdb20RangeTreeLockManager13barrier_func_MUlRKNS2_8EndpointES6_E_ENS_9allocatorIS7_EEFbS6_S6_EE7__cloneEv"
            )
            == "std::__1::__function::__func<rocksdb::RangeTreeLockManager::barrier_func_::'lambda'(rocksdb::Endpoint const&, rocksdb::Endpoint const&), std::__1::allocator<rocksdb::RangeTreeLockManager::barrier_func_::'lambda'(rocksdb::Endpoint const&, rocksdb::Endpoint const&)>, bool (rocksdb::Endpoint const&, rocksdb::Endpoint const&)>::__clone() const"
        )

    def test_duckdb(self):
        assert (
            demangle.demangle(
                "__ZNKSt3__110__function6__funcIN14duckdb_httplib7Request20is_connection_closedMUlvE_ENS_9allocatorIS4_EEFbvEE7__cloneEv"
            )
            == "std::__1::__function::__func<duckdb_httplib::Request::is_connection_closed::'lambda'(), std::__1::allocator<duckdb_httplib::Request::is_connection_closed::'lambda'()>, bool ()>::__clone() const"
        )

    def test_the_abi_rule_reads_the_same_names_as_lies(self):
        """What llvm-cxxfilt, LLVM's main branch and c++filt print for them, and what
        the ABI's rule gives here: a parameter dropped to `Endpoint const`, and an
        allocator of a reference rather than of the closure."""
        spelled = read(
            "__ZNKSt3__110__function6__funcIN7rocksdb20RangeTreeLockManager13barrier_func_MUlRKNS2_8EndpointES6_E_ENS_9allocatorIS7_EEFbS6_S6_EE7__cloneEv",
            closure_prefix_substitution=True,
        )
        assert "'lambda'(rocksdb::Endpoint const&, rocksdb::Endpoint const)" in spelled
        assert "std::__1::allocator<rocksdb::Endpoint const&>" in spelled

    def test_the_underscore_is_what_decides(self):
        """Without it the name reads by the ABI's rule, and this one lands in range
        under both, so nothing trips and the lie above is the answer: a shifted reference
        that lands on a type reads as that type. A caller who knows the binary is Apple
        clang's and has lost the underscore sets the rule."""
        bare = "__ZNKSt3__110__function6__funcIN7rocksdb20RangeTreeLockManager13barrier_func_MUlRKNS2_8EndpointES6_E_ENS_9allocatorIS7_EEFbS6_S6_EE7__cloneEv"[
            1:
        ]
        assert demangle.demangle(bare) == read(
            "__ZNKSt3__110__function6__funcIN7rocksdb20RangeTreeLockManager13barrier_func_MUlRKNS2_8EndpointES6_E_ENS_9allocatorIS7_EEFbS6_S6_EE7__cloneEv",
            closure_prefix_substitution=True,
        )
        assert (
            read(bare, closure_prefix_substitution=False)
            == "std::__1::__function::__func<rocksdb::RangeTreeLockManager::barrier_func_::'lambda'(rocksdb::Endpoint const&, rocksdb::Endpoint const&), std::__1::allocator<rocksdb::RangeTreeLockManager::barrier_func_::'lambda'(rocksdb::Endpoint const&, rocksdb::Endpoint const&)>, bool (rocksdb::Endpoint const&, rocksdb::Endpoint const&)>::__clone() const"
        )

    def test_an_abi_numbered_mach_o_name_is_caught_by_the_overrun(self):
        """Upstream clang targeting Darwin counts the prefix; under Apple's rule its
        `S2_` runs past the table, and the retry reads it the other way."""
        assert demangle.demangle("_" + NEW) == SPELLED
        assert read("_" + NEW, closure_prefix_substitution=False) == "_" + NEW

    def test_both_rules_at_once(self):
        """Upstream clang targeting Darwin counts the closure prefix and not the
        `auto`, the opposite of Apple's fork on both. Under Apple's pair this name's
        `S1_` is the function template `transfer`, which is nothing a type can be, and
        neither single flip reads it: with the prefix counted but the `auto` too the
        indices cancel and `S1_` is still `transfer`; with neither counted `S6_` is
        past the end. Under the other pair `S1_` is the closure and `S6_` is `n::S<int>`."""
        name = "__ZN1n2ctMUlvE_8transferIiEEDaPT_PNS_1SIiEES6_S1_"
        assert (
            demangle.demangle(name)
            == "auto n::ct::'lambda'()::transfer<int>(int*, n::S<int>*, n::S<int>, n::ct::'lambda'())"
        )
        assert read(name, closure_prefix_substitution=False) == name
        assert read(name, undeduced_auto_substitution=True) == name
