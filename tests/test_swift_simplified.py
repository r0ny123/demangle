"""Swift's simplified spelling: what Xcode and LLDB show a user.

`swift-demangle --simplified` prints a name the way a Swift programmer refers to it
rather than the way the type system writes it: `Either` for `Monads.Either`, `(_:)` for
`(Swift.Int) -> Swift.UInt`, `specialized f()` for a page of specialisation arguments.
Upstream's `DemangleOptions::SimplifiedUIDemangleOptions()` is a bundle of fourteen
flags, and `test/Demangle/Inputs/simplified-manglings.txt` is 217 vectors with the
expected output beside each one -- so this is measurable exactly as every other scheme's
conformance is, without the toolchain being installed.

`SwiftOptions` carries the flags the bundle actually changes here. A flag no vector
exercises is a flag with no reference behind it, so the ones that change nothing over the
217 are left out rather than guessed at.
"""

import pytest

import demangle
from demangle.schemes.swift import SIMPLIFIED_OPTIONS, SwiftOptions

from .conftest import load_corpus
from .test_conformance import SWIFT_SIMPLIFIED_EXACT, SWIFT_SIMPLIFIED_TOTAL

SIMPLIFIED = demangle.style("llvm", swift=SIMPLIFIED_OPTIONS)


class TestAgainstSwiftsOwnVectors:
    def test_the_corpus_is_the_whole_vector_list(self):
        assert len(load_corpus("swift-simplified.txt")) == SWIFT_SIMPLIFIED_TOTAL

    def test_every_vector_matches(self):
        for mangled, expected in load_corpus("swift-simplified.txt"):
            assert demangle.demangle(mangled, style=SIMPLIFIED) == expected, mangled

    def test_the_pinned_number_is_still_accurate(self):
        score = sum(
            1
            for mangled, expected in load_corpus("swift-simplified.txt")
            if demangle.demangle(mangled, style=SIMPLIFIED) == expected
        )
        assert score == SWIFT_SIMPLIFIED_EXACT

    def test_the_tree_spells_what_the_text_spells(self):
        """One printer drives both paths, so the simplified spelling has to reach both."""
        for mangled, _ in load_corpus("swift-simplified.txt"):
            try:
                tree = demangle.parse(mangled, style=SIMPLIFIED)
            except demangle.DemanglingError:
                continue
            assert tree.spell(style=SIMPLIFIED) == demangle.demangle(mangled, style=SIMPLIFIED), mangled


class TestWhatEachFlagDoes:
    """One vector per flag, so a regression names the flag rather than a count."""

    @pytest.mark.parametrize(
        ("field", "mangled", "full", "simplified"),
        [
            ("display_module_names", "_TtO6Monads6Either", "Monads.Either", "Either"),
            ("show_function_argument_types", "_TtFSiSu", "(Swift.Int) -> Swift.UInt", "(_:)"),
            (
                "display_where_clauses",
                "_TtuR_s8RunciblerFxwx5Mince",
                "<A where B: Swift.Runcible>(A) -> A.Mince",
                "<A>(_:)",
            ),
            ("display_entity_types", "_Tv3foo3barSi", "foo.bar : Swift.Int", "bar"),
            (
                "display_protocol_conformances",
                "_TWPC3foo3barS_8barrables",
                "protocol witness table for foo.bar : foo.barrable in Swift",
                "protocol witness table for bar",
            ),
            (
                "display_generic_specializations",
                "_TTSg5Si___TFSqcfT_GSqx_",
                "generic specialization <Swift.Int> of Swift.Optional.init() -> A?",
                "specialized Optional.init()",
            ),
            (
                "display_extension_contexts",
                "_TFE11ext_structAV11def_structA1A4testfT_T_",
                "(extension in ext_structA):def_structA.A.test() -> ()",
                "A.test()",
            ),
            (
                "show_private_discriminators",
                "_TF13devirt_accessP5_DISC15getPrivateClassFT_CS_12PrivateClass",
                "devirt_access.(getPrivateClass in _DISC)() -> devirt_access.PrivateClass",
                "getPrivateClass()",
            ),
            (
                "shorten_value_witness",
                "_TwxxC3foo3bar",
                "destroy value witness for foo.bar",
                "destroy for bar",
            ),
            # `ShortenThunk` reaches the three autodiff kinds too, which the vectors never
            # showed: a derivative stops at the function it is of, a subset-parameters
            # thunk at what it thunks, and a self-reordering thunk keeps its source type
            # alone. The 6.1.2 runtime's 310 differentiable symbols all spell so.
            (
                "shorten_thunk",
                "$sSdyS2dcfCTJfSUpSr",
                "forward-mode derivative of Swift.Double.init(Swift.Double) -> Swift.Double"
                " with respect to parameters {0} and results {0}",
                "forward-mode derivative of Double.init(_:)",
            ),
            (
                "shorten_thunk",
                "$s4main1fyS2fFTJSpSpSrSUSP",
                "autodiff subset parameters thunk for pullback from main.f(Swift.Float) -> Swift.Float"
                " with respect to parameters {0} and results {0} to parameters {0, 2}",
                "autodiff subset parameters thunk for pullback from f(_:)",
            ),
            (
                "shorten_thunk",
                "$ss5SIMD2VyxGxIeglr_ACxIeglr_SBRzs10SIMDScalarRz16_Differentiation14DifferentiableRz"
                "13TangentVectorAeFPQzRszlTJOpTA",
                "partial apply forwarder for autodiff self-reordering reabstraction thunk for pullback"
                "<A where A: Swift.BinaryFloatingPoint, A: Swift.SIMDScalar, A: _Differentiation.Differentiable,"
                " A == A._Differentiation.Differentiable.TangentVector>  from @escaping @callee_guaranteed"
                " (@inout Swift.SIMD2<A>) -> (@out A) to @escaping @callee_guaranteed (@inout Swift.SIMD2<A>) -> (@out A)",
                "partial apply for autodiff self-reordering reabstraction thunk for @escaping @callee_guaranteed"
                " (@inout SIMD2<A>) -> (@out A)",
            ),
        ],
    )
    def test_a_flag_changes_exactly_what_it_says(self, field, mangled, full, simplified):
        assert demangle.demangle(mangled) == full
        assert demangle.demangle(mangled, style=SIMPLIFIED) == simplified
        # ...and the bundle without this one field leaves that piece in.
        kept = demangle.style("llvm", swift={field: True})
        assert demangle.demangle(mangled, style=kept) == full

    def test_a_derivatives_where_clause_is_gated_on_its_own(self):
        """`if (optionalGenSig && Options.DisplayWhereClauses)`: the ` with <...>` a
        derivative ends in is the generic signature it was differentiated under, and it
        goes with the where clauses, not with the thunk shortening."""
        name = "$s5Glibc3fmayxx_xxtSFRzlFSFRz16_Differentiation14DifferentiableRz13TangentVectorAcDPQzRszlTJfSSSpSr"
        assert demangle.demangle(name) == (
            "forward-mode derivative of Glibc.fma<A where A: Swift.FloatingPoint>(A, A, A) -> A"
            " with respect to parameters {0, 1, 2} and results {0}"
            " with <A where A: Swift.FloatingPoint, A: _Differentiation.Differentiable,"
            " A == A._Differentiation.Differentiable.TangentVector>"
        )
        no_where = demangle.style("llvm", swift={"display_where_clauses": False})
        assert demangle.demangle(name, style=no_where) == (
            "forward-mode derivative of Glibc.fma<A>(A, A, A) -> A with respect to parameters {0, 1, 2} and results {0}"
        )
        short = demangle.style("llvm", swift={"shorten_thunk": False})
        assert (
            demangle.demangle(name, style=short)
            == "forward-mode derivative of Glibc.fma<A where A: Swift.FloatingPoint>(A, A, A) -> A"
        )

    def test_the_default_is_the_toolchains_own_spelling(self):
        """Every field is True by default, so the 8,494-name corpus is untouched."""
        assert SwiftOptions() == SwiftOptions(display_module_names=True)
        assert demangle.demangle("_TtSi") == "Swift.Int"
        assert demangle.demangle("_TtSi", style=demangle.style("llvm")) == "Swift.Int"

    def test_the_word_specialized_is_written_once_however_deep_the_nesting(self):
        """Two specialisation layers, one word. The reference latches it too."""
        assert demangle.demangle("_TTSg5Si___TTSg5Si___TFSqcfT_GSqx_", style=SIMPLIFIED) == (
            "specialized Optional.init()"
        )

    def test_a_name_the_reference_refuses_comes_back_unchanged(self):
        """The corpus writes the mangled name in the expected column for those."""
        for mangled in ("_TtZZ", "_T", "_TWo"):
            assert demangle.demangle(mangled, style=SIMPLIFIED) == mangled
