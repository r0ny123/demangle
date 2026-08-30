"""Swift mangled names.

Exact against `swift-demangle` from the 5.10.1 toolchain: every one of the 48,368 `$s`
symbols in the shipped runtime and Foundation, and all 376 cases in the compiler's own
`test/Demangle/Inputs/manglings.txt` -- both the current mangling and Swift 3's. Nothing
refused, nothing mis-spelled.

The corpus below is a stratified sample of that -- up to four symbols per distinct set of
demangling-tree node kinds, so each construct that occurs is represented -- with the
compiler's own cases added whole, since those are what exercise the parts a shipped
binary does not: SIL function types, function-signature specialisations, key-path
thunks, autodiff, macro expansions.

What each test below pins is a rule that had to be *measured*. The reference is 8,000
lines of C++ whose behaviour is not all obvious from reading it, and each of these was
wrong on a first pass.
"""

import contextlib
import pathlib

import pytest

import demangle
from demangle.core.errors import DemanglingError
from demangle.schemes.swift import detect
from demangle.schemes.swift._demangler import Demangler, demangle_symbol
from demangle.schemes.swift._printer import print_root

from . import test_conformance as pins
from .conftest import load_corpus

CORPUS = pathlib.Path(__file__).parent / "conformance" / "swift-real-world.txt"


def corpus():
    rows = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "\t" in line:
            rows.append(tuple(line.split("\t", 1)))
    return rows


ROWS = corpus()

#: Swift's own vectors, read through the shared loader so the storage format is the one
#: every other corpus uses.
UPSTREAM = load_corpus("swift-upstream.txt")


def spell(mangled):
    return print_root(demangle_symbol(mangled))


class TestConformance:
    def test_the_corpus_covers_the_constructs(self):
        assert len(ROWS) > 8000

    def test_every_recorded_name_still_spells_the_same(self, subtests):
        for mangled, expected in ROWS:
            with subtests.test(name=mangled):
                assert spell(mangled) == expected


class TestWhatSwiftAddedAfterThisWasWritten:
    """The last eight vectors, each transcribed from the reference rather than guessed.

    A grammar fitted to eight examples is how a demangler with no wrong spellings starts
    having them, so every rule here comes from swiftlang/swift's own `Demangler.cpp` and
    `NodePrinter.cpp`: what the mangling means, and what the reference prints for it.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # `E` is `c` for a closure that escapes, and `C<n>` says the closure is the
            # same one an earlier argument carried.
            (
                "$s3foo7closureSSTf1cC0_n",
                "function signature specialization <Arg[0] = [Closure Propagated : closure, "
                "Argument Types : [Swift.String], Arg[1] = [Same As Argument 0]> of foo",
            ),
            (
                "$s3foo7closureSSTf1EC0_n",
                "function signature specialization <Arg[0] = [Escaping Closure Propagated : closure, "
                "Argument Types : [Swift.String], Arg[1] = [Same As Argument 0]> of foo",
            ),
            # `p` is a *run*: one argument, five propagated constants.
            (
                "$s3foo4main1SVs5Int32VSbTf3npSSi3Si0_n",
                "function signature specialization <Arg[1] = [Constant Propagated Struct : main.S]"
                "[Constant Propagated Struct : Swift.Int32][Constant Propagated Integer : 3]"
                "[Constant Propagated Struct : Swift.Bool][Constant Propagated Integer : 0]> of foo",
            ),
        ],
    )
    def test_function_signature_specialization_kinds(self, mangled, expected):
        assert demangle.demangle(mangled, language="swift") == expected

    def test_arguments_the_optimiser_dropped(self):
        """`Ttt1g5`: a `t` per dropped argument, then the specialisation's own letter.

        The reference does not print them -- it says they carry nothing a reader wants --
        so what this has to get right is consuming them.
        """
        name = "$s4test7genFuncyyx_q_tr0_lFSi_SbTtt1g5"
        expected = "generic specialization <Swift.Int, Swift.Bool> of test.genFunc<A, B>(A, B) -> ()"
        assert demangle.demangle(name, language="swift") == expected

    def test_representation_changed_has_no_argument_list(self):
        """`Tfr` says only how the function is represented, so there are no parameters."""
        name = "$s4test4BaseCyxGAA1PA2aEP3fooyyFTWTfr9"
        expected = (
            "representation changed of protocol witness for test.P.foo() -> () "
            "in conformance test.Base<A> : test.P in test"
        )
        assert demangle.demangle(name, language="swift") == expected

    def test_the_embedded_swift_prefix(self):
        """`$e` names an Embedded Swift symbol and is read exactly as `$s` is."""
        embedded = "$e4test4BaseCyxGAA1PA2aEP3fooyyFTW"
        current = "$s4test4BaseCyxGAA1PA2aEP3fooyyFTW"
        assert demangle.demangle(embedded, language="swift") == demangle.demangle(current, language="swift")
        assert demangle.demangle(embedded, language="swift").startswith("protocol witness for")

    def test_a_macro_expansions_source_location(self):
        """`MX436_4_` is line 437 column 5: both are indices, so both read one less."""
        name = "$s9MacroUser0023macro_expandswift_elFCffMX436_4_23bitwidthNumberedStructsfMf_"
        expected = (
            "freestanding macro expansion #1 of bitwidthNumberedStructs "
            "in module MacroUser file macro_expand.swift line 437 column 5"
        )
        assert demangle.demangle(name, language="swift") == expected

    def test_a_pack_protocol_conformance(self):
        """`HX` is one conformance per pack element, and `HC` writes the conditional
        requirements the reference prints after `with conditional requirements:`."""
        name = "$s23variadic_generic_opaque2G2VyAA2S1V_AA2S2VQPGAA1PHPAeA1QHPyHC_AgaJHPyHCHX_HC"
        spelled = demangle.demangle(name, language="swift")
        assert spelled.startswith("concrete protocol conformance variadic_generic_opaque.G2<Pack{")
        assert " to protocol conformance ref (type's module) variadic_generic_opaque.P" in spelled
        assert " with conditional requirements: (pack protocol conformance (" in spelled

    def test_an_opaque_result_types_conformance(self):
        name = "$s3use1xAA3OfPVy3lib1GVyAA1fQryFQOyQo_GAjE1PAAxAeKHD1_AIHO_HCg_Gvp"
        expected = "use.x : use.OfP<lib.G<<<opaque return type of use.f() -> some>>.0>>"
        assert demangle.demangle(name, language="swift") == expected

    def test_the_attribute_the_reference_no_longer_writes_is_still_read(self):
        """`m` was dropped upstream, and the shipped runtime still holds symbols with it."""
        name = "$sSUss17FixedWidthIntegerRzrlEyxqd__cSzRd__lufCSu_SiTgm5"
        assert demangle.demangle(name, language="swift").startswith("generic specialization <Swift.UInt, Swift.Int>")

    def test_a_subset_parameters_thunk_with_nothing_to_thunk_is_refused(self):
        """The four trailing children are the kind and three index subsets; at least one
        ahead of them names the thing being thunked. Without it the walk back through the
        children runs off the front and the "from" clause comes out empty -- a thunk for
        nothing, which is a reading no name has. The reference guards the same count, and
        5.10.1, which did not, takes its printer down with `std::bad_alloc` on this."""
        assert demangle.demangle("$sTJSdSSSpSrSUSP", language="swift") == "$sTJSdSSSpSrSUSP"

    def test_a_dependent_root_conformance_says_what_conforms_to_what(self):
        """The two children are the conforming type and the protocol, with " to " between
        them. Without it `#0 A to lib.P` runs together as `#0 Alib.P`, which reads as one
        name."""
        name = "$s3use1xAA3OfPVy3lib1GVyAA1fQryFQOyQo_GAjE1PAAxAeKHD1_AIHO_HC"
        assert " dependent root protocol conformance #0 A to lib.P of " in demangle.demangle(name, language="swift")

    def test_a_lowered_parameters_markers_follow_the_references_child_count(self):
        """`isolated` and `sil_implicit_leading_param` are spelled at three children and
        at four, and at five or more the reference spells none of them. Following the
        count rather than deciding per marker is what makes all three of these agree."""
        # Four children: differentiability, `isolated`, and the type. Spelled.
        assert "(@guaranteed isolated Builtin.ImplicitActor) -> () to " in spell("$sBAIeNghHgI_BAytIeNghHgILr_TR")
        # Four children with the implicit leading parameter instead. Also spelled.
        assert "(@guaranteed sil_implicit_leading_param Builtin.ImplicitActor)" in spell("$sIeg_BAIegHgL_TR")
        # Five: both markers, and neither is spelled.
        assert spell("$sBAIgHgIL_BAIegHgIL_TR") == (
            "reabstraction thunk helper from "
            "@callee_guaranteed @async (@guaranteed Builtin.ImplicitActor) -> () to "
            "@escaping @callee_guaranteed @async (@guaranteed Builtin.ImplicitActor) -> ()"
        )

    def test_an_extended_existential_shape_spells_its_type(self):
        """Where the reference spells `<null node pointer>`: it reads the node one child
        too high. See tests/conformance/swift-reference-defects.txt."""
        assert demangle.demangle("$sSiXg", language="swift") == "existential shape for any Swift.Int"


class TestAgainstSwiftsOwnCorpus:
    """`test/Demangle/Inputs/manglings.txt` from the swiftlang/swift repository.

    The vectors the reference demangler is developed against, rather than symbols
    scraped from a shipped toolchain: they cover constructs no released runtime emits
    yet, which is exactly what a record of the gaps is for.

    Pinned in both directions. The score can only go up, and it cannot quietly stop
    being accurate -- a name that starts failing is caught by the lower bound, and a
    batch of names that starts passing is caught by the upper one, which is what forces
    this number to be re-read rather than left to rot.
    """

    #: Every vector. Nothing left to raise, and a drop is a regression whatever the
    #: total -- which is what the two tests below are for. Shared with
    #: tests/test_conformance.py, where tests/test_readme.py looks for the README's
    #: counts.
    EXPECTED_EXACT = pins.SWIFT_UPSTREAM_EXACT

    def _score(self):
        return sum(1 for mangled, expected in UPSTREAM if demangle.demangle(mangled) == expected)

    def test_the_corpus_is_the_whole_upstream_file(self):
        assert len(UPSTREAM) == pins.SWIFT_UPSTREAM_TOTAL

    def test_no_name_that_matched_has_stopped_matching(self):
        assert self._score() >= self.EXPECTED_EXACT

    def test_and_the_pinned_number_is_still_accurate(self):
        assert self._score() == self.EXPECTED_EXACT

    def test_nothing_is_answered_with_a_different_spelling(self):
        """A refusal is a gap; a wrong answer is a defect. There are none of the latter."""
        wrong = [
            (mangled, expected, demangle.demangle(mangled))
            for mangled, expected in UPSTREAM
            if demangle.demangle(mangled) not in (expected, mangled)
        ]
        assert wrong == []


class TestTheSwiftThreeMangling:
    """`_T` followed by anything but `0`. A different grammar with its own demangler in
    the compiler, and still what the ObjC runtime holds for a Swift class."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_TtC3foo3bar", "foo.bar"),
            ("_TtGSaSi_", "[Swift.Int]"),
            ("_TF3foog3barSi", "foo.bar.getter : Swift.Int"),
            ("_TFC3foo3bar3basfT3zimCS_3zim_T_", "foo.bar.bas(zim: foo.zim) -> ()"),
            ("_TFC3foo3barCfT_S0_", "foo.bar.__allocating_init() -> foo.bar"),
            ("_TFC3foo3barD", "foo.bar.__deallocating_deinit"),
            ("_TMPC3foo3bar", "generic type metadata pattern for foo.bar"),
            ("_Tv3foo3barSi", "foo.bar : Swift.Int"),
            ("_TF3foooi1pFTCS_3barVS_3bas_OS_3zim", "foo.+ infix(foo.bar, foo.bas) -> foo.zim"),
        ],
    )
    def test_reading(self, mangled, expected):
        assert spell(mangled) == expected

    def test_a_specialisation_payload_is_a_swift_3_name_too(self):
        """The payload of a function-signature specialisation is itself a mangled name.
        Reading it with the current demangler leaves it as raw text, which is the one
        thing that stopped this corpus coming out exactly."""
        assert spell("_TTSf1cl35_TFF7specgen6callerFSiT_U_FTSiSi_T_Si___TF7specgen12take_closureFFTSiSi_T_T_") == (
            "function signature specialization <Arg[0] = [Closure Propagated : closure #1 "
            "(Swift.Int, Swift.Int) -> () in specgen.caller(Swift.Int) -> (), Argument Types : "
            "[Swift.Int]> of specgen.take_closure((Swift.Int, Swift.Int) -> ()) -> ()"
        )

    def test_an_accessor_wraps_its_variable_the_other_way_round(self):
        """Swift 3 mangled the accessor outside and the variable inside; the current one
        is the reverse, and the printer expects the current shape."""
        assert spell("_TF3foos3barSi") == "foo.bar.setter : Swift.Int"


class TestGrammarFacts:
    """Rules the reference states only by doing; each cost a wrong answer to find."""

    def test_the_swift_4_2_prefixes_are_not_old_function_type_mangling(self):
        """`isOldFunctionTypeMangling` is `_T`, not "any prefix that looks old".

        `$S` and `_$S` are Swift 4.2. Treating them as Swift 3 makes
        `pop_function_param_labels` look for labels inside the argument tuple, and the
        labels then come out attached to the wrong things: `foo.barbas.zim(foo.zim)`
        instead of `foo.bar.bas(zim: foo.zim)`.
        """
        assert spell("_$S3foo3barC3bas3zimyAaEC_tFTo") == "@objc foo.bar.bas(zim: foo.zim) -> ()"
        assert spell("$s3foo3barC3bas3zimyAaEC_tFTo") == "@objc foo.bar.bas(zim: foo.zim) -> ()"

    def test_a_node_that_writes_nothing_is_not_output(self):
        """`printEntity` decides whether to write a `.` by whether the context produced
        text -- measured in *characters*. A `LabelList` prints nothing at all, and
        counting calls rather than characters puts a second dot in every initialiser:
        `Foundation.FileHandle..init(...)`.
        """
        assert spell("$s10Foundation10NSIndexSetC5coderACSgAA7NSCoderC_tcfc") == (
            "Foundation.NSIndexSet.init(coder: Foundation.NSCoder) -> Foundation.NSIndexSet?"
        )

    def test_a_module_is_a_substitution_candidate(self):
        """Not only nominal types. Both `AA` references here are the `Foundation` module,
        once to qualify `DataProtocol` and once for the trailing `in Foundation`."""
        assert spell("$s10Foundation4DataVAA0B8ProtocolAAWP") == (
            "protocol witness table for Foundation.Data : Foundation.DataProtocol in Foundation"
        )

    def test_an_identifier_may_be_built_from_words_of_earlier_ones(self):
        """Swift's word substitutions, which no other mangling here has: a digit run
        inside a length prefix names a *word* of an identifier already seen.

        `0c6ToHostD0` is `PluginToHostMessage` assembled out of words of the module name
        in front of it, and none of those words appears in the symbol at that point.
        """
        assert spell("$s013CompilerSwiftA21PluginMessageHandling0c6ToHostD0OMn") == (
            "nominal type descriptor for CompilerSwiftCompilerPluginMessageHandling.PluginToHostMessage"
        )

    def test_the_standard_type_table_is_the_compilers(self):
        """A table written from memory had `SB` as `UnsafeRawBufferPointer` (it is
        `BinaryFloatingPoint`) and `SJ` as `AnyKeyPath` (it is `Character`). Both
        produced plausible, wrong output."""
        assert spell("$sSBD") == "Swift.BinaryFloatingPoint"
        assert spell("$sSJD") == "Swift.Character"
        assert spell("$sSWD") == "Swift.UnsafeRawBufferPointer"

    def test_sugar_is_on_by_default(self):
        """`swift-demangle` sets `SynthesizeSugarOnTypes` unless `--disable-sugar` is
        given, which the struct's own default does not -- so reading the default off
        `DemangleOptions` gives `Swift.Array<Swift.Int>` where the tool gives
        `[Swift.Int]`."""
        assert spell("$sSaySiGD") == "[Swift.Int]"
        assert spell("$sSDySSSiGD") == "[Swift.String : Swift.Int]"
        assert spell("$sSiSgD") == "Swift.Int?"

    def test_a_symbol_may_name_several_things(self):
        text = spell("$s10Foundation10CocoaErrorV4CodeVSQAAMc")
        assert text == (
            "protocol conformance descriptor for Foundation.CocoaError.Code : Swift.Equatable in Foundation"
        )

    def test_outlined_copy_may_carry_a_generic_signature(self):
        """Alone among the outlined value operations, `WOy` and `WOe` take an optional
        second child. Printing only the first drops it silently."""
        assert spell("_T0SqWOy.17") == 'outlined copy of Swift.Optional with unmangled suffix ".17"'

    def test_a_generic_signature_names_its_parameters_by_position(self):
        assert spell("$s4main1fyyxlF") == "main.f<A>(A) -> ()"


REFUSALS = [
    line
    for line in (pathlib.Path(__file__).parent / "conformance" / "swift-refusals.txt")
    .read_text(encoding="utf-8")
    .splitlines()
    if line and not line.startswith("#")
]


class TestRefusesRatherThanGuesses:
    @pytest.mark.parametrize(
        "mangled",
        ["$s", "_T", "notaswiftsymbol"],
    )
    def test_it_refuses(self, mangled):
        assert demangle.demangle(mangled) == mangled

    def test_detection_covers_both_manglings(self):
        assert detect("_TtC3foo3bar")
        assert detect("$s10Foundation6NSDataCfd")
        assert not detect("_Z1fv")

    def test_it_refuses_exactly_what_the_reference_refuses(self, subtests):
        """The reference echoes back a name it cannot read; so does this."""
        for mangled in REFUSALS:
            with subtests.test(name=mangled):
                assert demangle.demangle(mangled) == mangled


class TestTheCursorNeverGoesBackwardsOverACharacterItDidNotRead:
    """`push_back` has to be the exact inverse of `next_char`, including at the end.

    It was not. `next_char` returned `""` past the end *without* moving, so a caller
    that reached the end, got nothing, and put it back moved the cursor onto the last
    character of the name -- and read it again. In a loop that is a loop that never
    advances: `demangle_func_spec_param` reads a run of propagated constants and puts
    back the letter that ends the run, so a name ending in `p` grew one specialisation
    parameter per iteration until the process ran out of memory.

    Found by mutating the checked-in corpora. It is the failure mode this package is
    least able to absorb -- `demangle()` is documented never to raise for a string, and
    what it did instead was take the process with it.
    """

    def test_reading_past_the_end_and_putting_it_back_stays_at_the_end(self):
        reader = Demangler("$s2ab")
        reader.pos = reader.end
        assert reader.next_char() == ""
        reader.push_back()
        assert reader.pos == reader.end
        # And the ordinary case is unchanged: what was read is what comes back.
        reader.pos = reader.end - 1
        assert reader.next_char() == "b"
        reader.push_back()
        assert reader.pos == reader.end - 1
        assert reader.next_char() == "b"

    def test_a_specialisation_that_ends_where_a_constant_run_begins(self):
        """The name that found it, and the shapes either side of it."""
        for mangled in ("_T03foo4_123ABTf3psbp", "_T03foo4_123ABTf3psb", "_T03foo4_123ABTf3psbpi"):
            assert demangle.demangle(mangled) == mangled

    @pytest.mark.parametrize(
        "full",
        [
            "_T0SS3fooySSSgFTf3npk_n",
            "_T03foo4_123ABTf3psbpSSi3Si0",
            "_T03fooABTf4g_n",
            "$s3foo3barSSyFTf4x_n",
        ],
    )
    def test_every_prefix_of_a_specialisation_is_answered(self, full):
        """Cut short at every offset. A specialisation is the production that loops."""
        for cut in range(len(full) + 1):
            name = full[:cut]
            assert isinstance(demangle.demangle(name), str)
            with contextlib.suppress(DemanglingError):
                demangle.demangle_strict(name)


class TestTree:
    def test_the_tree_spells_what_demangle_spells(self):
        for mangled, expected in ROWS[:500]:
            assert demangle.parse(mangled).spell() == expected

    def test_the_tree_carries_the_structure(self):
        tree = demangle.parse("$s10Foundation4DataV5countSivg")
        assert [(node.kind, node.text) for node in tree.children()] == [
            ("type", "Foundation.Data"),
            ("name", "count"),
            ("type", "Swift.Int"),
        ]

    def test_find_name_works_as_it_does_for_the_other_schemes(self):
        tree = demangle.parse("$s10Foundation4DataV5countSivg")
        assert [node.text for node in tree.find("name")] == ["Data", "count", "Int"]

    def test_a_module_is_reachable(self):
        tree = demangle.parse("$s10Foundation4DataV5countSivg")
        assert [node.text for node in tree.find("module")] == ["Foundation", "Swift"]

    def test_parse_and_demangle_refuse_together(self):
        with pytest.raises(demangle.DemanglingError):
            demangle.demangle_strict("$sTk")
        with pytest.raises(demangle.DemanglingError):
            demangle.parse("$sTk")


class TestRegisteredAsALanguage:
    def test_demangle_reaches_it_without_being_told(self):
        assert demangle.demangle("$s10Foundation6NSDataCfd") == "Foundation.NSData.deinit"

    def test_naming_the_language_works(self):
        assert demangle.demangle("$s10Foundation6NSDataCfd", language="swift") == ("Foundation.NSData.deinit")

    def test_it_does_not_claim_another_scheme_s_names(self):
        assert demangle.demangle("_Z1fv") == "f()"
        assert demangle.demangle("_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E") == ("core::fmt::Formatter::pad")
