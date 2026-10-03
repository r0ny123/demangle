"""Apple's clang counts an undeduced `auto` as a substitution candidate; nothing else does.

Take the Homebrew bottles of Boost, folly, Abseil, protobuf, Poco, fmt, TBB, ceres, ICU
and glog -- 108,839 Mach-O symbols, all Apple clang's output. Of the 17,310 names
carrying a `Da`, 2,300 are refused with a back-reference past the substitution table
under the common rule, by this and by both references, and 6,385 more read as a
plausible-looking wrong declaration under all three -- `std::__1::
basic_string_view<char, std::__1::basic_string_view::char_traits<char>>`, a type libc++
does not declare.

The rule is Clang 6.0's: `isTypeSubstitutable` in clang's ItaniumMangle.cpp says "Through
to Clang 6.0, we accidentally treated undeduced auto types as substitution candidates",
and keeps doing so under `-fclang-abi-compat=6`. Apple's clang keeps it in every version.
Checked by compiling the shape below with clang 18 under both settings -- `S5_` by
default, `S6_` under the compat flag -- and against the bottles' own symbols, and
against upstream clang 18 targeting `arm64-apple-darwin`, which writes `S5_`: the rule
is Apple's fork's, not the platform's.
"""

import pytest

import demangle
from demangle.core.errors import DemanglingError

#: `template <class A> auto transfer(A*, S<int>*, S<int>*)` in namespace `n`, as clang 18
#: writes it by default and under `-fclang-abi-compat=6`.
UPSTREAM = "_ZN1n8transferIiEEDaPT_PNS_1SIiEES5_"
APPLE = "_ZN1n8transferIiEEDaPT_PNS_1SIiEES6_"
SPELLED = "auto n::transfer<int>(int*, n::S<int>*, n::S<int>*)"
PROTOBUF_BOTTLE = "__ZNK6google8protobuf20FileDescriptorTables16FindNestedSymbolINS0_12_GLOBAL__N_115ParentNameQueryEEEDaPKvNSt3__117basic_string_viewIcNS8_11char_traitsIcEEEE"
ABSEIL_BOTTLE = "__ZN4absl12lts_2026081718container_internal15map_slot_policyINSt3__16vectorIiNS3_9allocatorIiEEEES7_E8transferINS5_INS3_4pairIKS7_S7_EEEEEEDaPT_PNS1_13map_slot_typeIS7_S7_EESJ_"


def read(name, **options):
    return demangle.demangle(name, style=demangle.style("llvm", itanium=options))


class TestTheRule:
    def test_a_bare_name_is_read_by_the_common_rule(self):
        assert read(UPSTREAM) == SPELLED

    def test_a_mach_o_name_is_read_by_apples(self):
        """The extra leading underscore is the one thing that says Mach-O, and Mach-O
        means Apple's clang far more often than not."""
        assert read("_" + APPLE) == SPELLED

    def test_the_option_forces_either_rule(self):
        assert read(APPLE, undeduced_auto_substitution=True) == SPELLED
        assert read("_" + UPSTREAM, undeduced_auto_substitution=False) == SPELLED
        assert read(APPLE, undeduced_auto_substitution=False) == APPLE
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(
                APPLE, style=demangle.style("llvm", itanium={"undeduced_auto_substitution": False})
            )

    def test_decltype_auto_counts_too(self):
        """clang's `isa<AutoType>` covers both placeholders."""
        assert (
            read("__ZN1n8transferIiEEDcPT_PNS_1SIiEES6_")
            == "decltype(auto) n::transfer<int>(int*, n::S<int>*, n::S<int>*)"
        )
        assert (
            read("_ZN1n8transferIiEEDcPT_PNS_1SIiEES5_")
            == "decltype(auto) n::transfer<int>(int*, n::S<int>*, n::S<int>*)"
        )


class TestTheRetry:
    """A back-reference past the table under one rule is read again under the other,
    because that is the one rule known to move the numbering."""

    def test_an_apple_name_without_its_underscore_still_reads(self):
        assert read(APPLE) == SPELLED

    def test_an_upstream_name_with_an_underscore_still_reads_when_it_overruns(self):
        # Under Apple's rule the table is one longer, so an upstream name overruns only
        # when it references its last entry -- here `S5_` is in range either way and
        # comes back by Apple's numbering, which is the documented limit.
        assert read("_" + UPSTREAM) == "auto n::transfer<int>(int*, n::S<int>*, n::S<int>)"

    def test_a_forced_rule_is_not_second_guessed(self):
        assert read(APPLE, undeduced_auto_substitution=False) == APPLE
        assert (
            read("_" + UPSTREAM, undeduced_auto_substitution=True)
            == "auto n::transfer<int>(int*, n::S<int>*, n::S<int>)"
        )

    def test_an_overrun_with_no_auto_in_the_name_is_still_a_refusal(self):
        assert read("_ZN1n1fIiEEvPT_S9_") == "_ZN1n1fIiEEvPT_S9_"


class TestWhatTheBottlesCarry:
    """Real symbols from the Homebrew bottles, with the reading the source declares."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            # protobuf's descriptor.cc: `auto FindNestedSymbol(const void* parent,
            # absl::string_view name) const`, with libc++'s string_view. Read by the
            # common rule the second argument comes back as `std::__1::basic_string_view<
            # char, std::__1::basic_string_view::char_traits<char>>`.
            (
                PROTOBUF_BOTTLE,
                "auto google::protobuf::FileDescriptorTables::FindNestedSymbol<google::protobuf::(anonymous namespace)::ParentNameQuery>(void const*, std::__1::basic_string_view<char, std::__1::char_traits<char>>) const",
            ),
            # Abseil's raw_hash_map: `template <class Allocator> static auto transfer(
            # Allocator* alloc, slot_type* new_slot, slot_type* old_slot)`. The last
            # parameter's `SJ_` is one past what the common rule numbers.
            (
                ABSEIL_BOTTLE,
                "auto absl::lts_20260817::container_internal::map_slot_policy<std::__1::vector<int, std::__1::allocator<int>>, std::__1::vector<int, std::__1::allocator<int>>>::transfer<std::__1::allocator<std::__1::pair<std::__1::vector<int, std::__1::allocator<int>> const, std::__1::vector<int, std::__1::allocator<int>>>>>(std::__1::allocator<std::__1::pair<std::__1::vector<int, std::__1::allocator<int>> const, std::__1::vector<int, std::__1::allocator<int>>>>*, absl::lts_20260817::container_internal::map_slot_type<std::__1::vector<int, std::__1::allocator<int>>, std::__1::vector<int, std::__1::allocator<int>>>*, absl::lts_20260817::container_internal::map_slot_type<std::__1::vector<int, std::__1::allocator<int>>, std::__1::vector<int, std::__1::allocator<int>>>*)",
            ),
        ],
    )
    def test_the_bottle_symbol_reads_as_declared(self, name, expected):
        assert demangle.demangle(name) == expected

    def test_without_the_underscore_only_an_overrun_is_caught(self):
        """The retry needs a back-reference to run past the table. Abseil's `transfer`
        does; protobuf's `FindNestedSymbol` keeps every reference in range under the
        common rule and comes back as the declaration both references print, which is
        not one libc++ has -- the documented limit, and the reason the Mach-O
        underscore decides the default."""
        assert demangle.demangle(ABSEIL_BOTTLE[1:]) == demangle.demangle(ABSEIL_BOTTLE)
        assert demangle.demangle(PROTOBUF_BOTTLE[1:]).endswith("std::__1::basic_string_view::char_traits<char>>) const")
