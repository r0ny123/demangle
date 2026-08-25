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

import pathlib

import pytest

import demangle
from demangle.schemes.swift import detect
from demangle.schemes.swift._demangler import demangle_symbol
from demangle.schemes.swift._printer import print_root

CORPUS = pathlib.Path(__file__).parent / "conformance" / "swift-real-world.txt"


def corpus():
    rows = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "\t" in line:
            rows.append(tuple(line.split("\t", 1)))
    return rows


ROWS = corpus()


def spell(mangled):
    return print_root(demangle_symbol(mangled))


class TestConformance:
    def test_the_corpus_covers_the_constructs(self):
        assert len(ROWS) > 8000

    def test_every_recorded_name_still_spells_the_same(self, subtests):
        for mangled, expected in ROWS:
            with subtests.test(name=mangled):
                assert spell(mangled) == expected


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
