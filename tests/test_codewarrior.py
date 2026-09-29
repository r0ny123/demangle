"""Metrowerks CodeWarrior C++.

The other pre-Itanium C++ mangling, and the one libiberty never read: `cplus-dem.c` has
no CodeWarrior flag and `demangle-expected` has no vectors for it, so the reference is
`encounter/cwdemangle` -- the tool decompilation projects for GameCube and Wii titles
run -- and the corpus is its own test module, transcribed.

Two things are measured beyond the vectors. How often this claims a name that is not
CodeWarrior, which is the same question `gnuv2` has to answer and for the same reason:
these names are ordinary C identifiers with a `__` in them. And how it and `gnuv2` divide
the names that are valid under *both* manglings, which is not a matter of one of them
being wrong -- `AtEnd__13ivRubberGroup` parses either way and the two spell it
differently -- but is a decision, so it is pinned rather than left to fall out of the
priorities.
"""

import pytest

import demangle
from demangle.core.errors import DemanglingError
from demangle.schemes import codewarrior
from demangle.schemes.codewarrior import CodeWarriorOptions, nodes
from demangle.schemes.codewarrior._parser import DemangleFailure, demangle_codewarrior

from .conftest import CONFORMANCE
from .test_conformance import CODEWARRIOR_EXACT, CODEWARRIOR_TOTAL

#: The rest are names `gnuv2` also validly reads and is offered first.
AUTODETECTED_EXACT = 36


def vectors():
    """The corpus, as `(mangled, options, expected)`."""
    rows = []
    for line in (CONFORMANCE / "codewarrior-cwdemangle.txt").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            rows.append(tuple(line.split("\t")))
    return rows


def options_for(flags):
    return CodeWarriorOptions(
        omit_empty_parameters="keep-empty-parameters" not in flags,
        mw_extensions="mw-extensions" in flags,
    )


def read(mangled, flags):
    """What this reads, or the name unchanged -- which is what the reference prints."""
    chosen = options_for(flags)
    try:
        return demangle_codewarrior(
            mangled,
            omit_empty_parameters=chosen.omit_empty_parameters,
            mw_extensions=chosen.mw_extensions,
        ).text
    except DemangleFailure:
        return mangled


class TestAgainstTheReferencesOwnVectors:
    def test_the_corpus_is_the_size_it_was(self):
        assert len(vectors()) == CODEWARRIOR_TOTAL

    def test_every_vector_matches_the_reference(self):
        exact = 0
        wrong = []
        for mangled, flags, expected in vectors():
            got = read(mangled, flags)
            if got == expected:
                exact += 1
            else:
                wrong.append((mangled, flags, expected, got))
        assert exact == CODEWARRIOR_EXACT, wrong[:5]

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # The shape the whole thing exists for.
            ("BuildLight__9CGuiLightCFv", "CGuiLight::BuildLight() const"),
            # A destructor, from the `__dt` special name.
            ("__dt__6CActorFv", "CActor::~CActor()"),
            # A template argument list written literally into the symbol.
            ("destroy<PUi>__4rstlFPUiPUi", "rstl::destroy<unsigned int*>(unsigned int*, unsigned int*)"),
            ("__pl__FRC9CRelAngleRC9CRelAngle", "operator+(const CRelAngle&, const CRelAngle&)"),
            # A pointer to member, with the two hidden parameters that say it is const.
            (
                "SomeFn__Q29Namespace5ClassCFRCMQ29Namespace5ClassFPCvPCvMQ29Namespace5ClassFPCvPCvPCvPv_v_RCMQ29Namespace5ClassFPCvPCvPCvPv_v",
                "Namespace::Class::SomeFn(void (Namespace::Class::*const & (Namespace::Class::*const &)"
                "(void (Namespace::Class::*)(const void*, void*) const) const)(const void*, void*) const) const",
            ),
            # A function-local static, Wii CodeWarrior's spelling.
            (
                "@LOCAL@GetAnmPlayPolicy__Q24nw4r3g3dFQ34nw4r3g3d9AnmPolicy@policyTable",
                "nw4r::g3d::GetAnmPlayPolicy(nw4r::g3d::AnmPolicy)::policyTable",
            ),
            # And GameCube's, written the other way round.
            (
                "skBadString$localstatic3$GetNameByToken__31TTokenSet<18EScriptObjectState>CF18EScriptObjectState",
                "TTokenSet<EScriptObjectState>::GetNameByToken(EScriptObjectState) const::skBadString",
            ),
        ],
    )
    def test_the_shapes_the_scheme_exists_for(self, mangled, expected):
        assert demangle_codewarrior(mangled).text == expected


class TestTheOptions:
    def test_empty_parameters_are_omitted_by_default_and_kept_on_request(self):
        mangled = "__dt__26__partial_array_destructorFv"
        kept = demangle.style("llvm", codewarrior={"omit_empty_parameters": False})
        assert demangle.demangle(mangled, language="codewarrior") == (
            "__partial_array_destructor::~__partial_array_destructor()"
        )
        assert demangle.demangle(mangled, language="codewarrior", style=kept) == (
            "__partial_array_destructor::~__partial_array_destructor(void)"
        )

    def test_metrowerks_extension_types_are_off_by_default(self):
        # `1` and `2` are `__int128` and `__vec2x32float__` *and* ordinary template
        # argument literals, so the reference leaves them off and so does this.
        mangled = "fn<3,PV2>__FPC2"
        with_extensions = demangle.style("llvm", codewarrior={"mw_extensions": True})
        assert demangle.demangle(mangled, language="codewarrior", style=with_extensions) == (
            "fn<3, volatile __vec2x32float__*>(const __vec2x32float__*)"
        )
        # Off, the `2` is read as a template argument literal and stays a `2`, which is
        # the reading the reference gives and the reason the flag exists.
        assert demangle.demangle(mangled, language="codewarrior") == "fn<3, volatile 2*>(const 2*)"


class TestWhatItRefusesToClaim:
    def test_an_ordinary_c_symbol_with_a_double_underscore_is_not_claimed(self):
        for name in (
            "cfunction",
            "__libc_start_main",
            "__cxa_atexit",
            "__init__",
            "not_mangled__at_all",
            "std__vector",
        ):
            assert not codewarrior.detect(name), name
            assert demangle.demangle(name) == name

    def test_a_name_another_scheme_refused_is_not_claimed(self):
        for name in (
            "_RINvNtCsicaZO8UCM9y_3std2rt10lang_startuECsipD1KD37Gle__6consts",
            "__RNvCs1Y7DaGC1cwg_7ustc__6consts",
            "_ZN3foo__6consts",
        ):
            assert not codewarrior.detect(name), name
            assert demangle.demangle(name) == name
        assert codewarrior.detect("__RTTI__40TObjOwnerDerivedFromIObj<12CStringTable>")

    def test_no_name_from_any_other_scheme_s_corpus_is_read_as_this_one(self):
        """Over every checked-in corpus but the two pre-Itanium ones: none taken."""
        claimed = []
        for path in sorted(CONFORMANCE.glob("*.txt")):
            if path.name.startswith(("gnuv2-", "codewarrior-")):
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#"):
                    continue
                name = line.split("\t")[0]
                try:
                    tree = demangle.parse(name)
                except DemanglingError:
                    continue
                if isinstance(tree, nodes.Symbol):
                    claimed.append(name)
        assert claimed == []


class TestASpellingThatCannotBeADeclaration:
    """`void` among other parameters, which no declaration contains.

    The same bar `gnuv2` holds a reading to, and the same reason: these two grammars read
    the same run of type letters out of the same C names. `f__Fcsv` is `char, short,
    void` to both of them, and `void` is a parameter list only when it is the whole of
    it. Found while closing the `gnuv2` half -- with that one fixed, this scheme picked
    the name up instead.
    """

    def test_void_among_others_is_refused(self):
        for name in ("f__Fcsv", "f__Fcv", "f__Fivi"):
            assert not codewarrior.detect(name), name
            assert demangle.demangle(name) == name

    def test_void_alone_is_still_a_parameter_list(self):
        assert demangle.demangle("__ct__3FooFv") == "Foo::Foo()"

    def test_nothing_follows_an_ellipsis(self):
        """`...` ends the list. With `gnuv2` refusing `foo__Fex`, this scheme picked it
        up instead, as `foo(..., long long)`."""
        for name in ("foo__Fex", "foo__Fei"):
            assert not codewarrior.detect(name), name
            assert demangle.demangle(name) == name
        assert demangle.demangle("foo__Fie", language="codewarrior") == "foo(int, ...)"

    def test_a_qualified_name_in_the_names_own_seat_is_read_or_refused_not_echoed(self):
        """`Q23foo3bar__Fv` is `foo::bar()`; it came back as a function called
        `Q23foo3bar`. A count with too few names behind it is refused, and so is the
        `Q2_` spelling, which this compiler never wrote."""
        assert demangle.demangle("Q23foo3bar__Fv", language="codewarrior") == "foo::bar()"
        for name in ("Q23foo__Fv", "Q2_3foo3bar__Fv"):
            assert demangle.demangle(name, language="codewarrior") == name

    def test_the_reference_corpus_is_untouched_by_the_rule(self):
        """No vector in the reference's own corpus spells `void` among others."""
        for mangled, flags, expected in vectors():
            assert read(mangled, flags) == expected, mangled


class TestSharingTheOverlapWithGnuV2:
    """Which of the two pre-Itanium schemes reads a name both of them can."""

    def test_gnu_v2_is_offered_first(self):
        from demangle.core.registry import available

        order = [plugin.name for plugin in available()]
        assert order.index("gnuv2") < order.index("codewarrior")
        assert order[-1] == "codewarrior"

    def test_a_name_valid_under_both_goes_to_gnu_v2(self):
        # Both readings parse. They differ only in spelling, and nothing in the name
        # says which compiler wrote it, so the commoner mangling takes it.
        mangled = "__pl__FRC9CRelAngleRC9CRelAngle"
        assert demangle.demangle(mangled) == "operator+(CRelAngle const &, CRelAngle const &)"
        assert demangle.demangle(mangled, language="codewarrior") == "operator+(const CRelAngle&, const CRelAngle&)"

    def test_a_name_only_codewarrior_writes_reaches_this_scheme(self):
        for mangled, expected in (
            # A literal template argument list: no GNU v2 compiler writes one.
            ("destroy<PUi>__4rstlFPUiPUi", "rstl::destroy<unsigned int*>(unsigned int*, unsigned int*)"),
            # `__dt` is a marker the *gnu* style does not know; reading it there gives
            # `CActor::__dt(void)`, which is a wrong name rather than no name.
            ("__dt__6CActorFv", "CActor::~CActor()"),
            # A function-local static.
            (
                "@GUARD@GetAnmPlayPolicy__Q24nw4r3g3dFQ34nw4r3g3d9AnmPolicy@policyTable",
                "nw4r::g3d::GetAnmPlayPolicy(nw4r::g3d::AnmPolicy)::policyTable guard",
            ),
        ):
            assert demangle.demangle(mangled) == expected, mangled

    def test_how_many_vectors_auto_detection_gets_exactly_right(self):
        exact = sum(
            1
            for mangled, flags, expected in vectors()
            if flags == "-" and expected != mangled and demangle.demangle(mangled) == expected
        )
        assert exact == AUTODETECTED_EXACT


class TestTheTree:
    def test_the_tree_renders_to_exactly_what_the_text_path_spells(self):
        for mangled, flags, _expected in vectors():
            try:
                symbol = demangle_codewarrior(
                    mangled,
                    omit_empty_parameters=options_for(flags).omit_empty_parameters,
                    mw_extensions=options_for(flags).mw_extensions,
                )
            except DemangleFailure:
                continue
            assert nodes.build(symbol).render() == symbol.text, mangled

    def test_the_argument_list_comes_back_separated(self):
        tree = demangle.parse("execCommand__12JASSeqParserFP8JASTrackM12JASSeqParserFPCvPvP8JASTrackPUl_lUlPUl")
        types = [node.render() for node in tree.walk() if node.kind == "type"]
        assert types == [
            "JASTrack*",
            "long (JASSeqParser::*)(JASTrack*, unsigned long*)",
            "unsigned long",
            "unsigned long*",
        ]

    def test_the_signature_view_splits_the_name_the_way_the_parser_did(self):
        parts = demangle.signature("BuildLight__9CGuiLightCFv", language="codewarrior")
        assert parts.qualified_name == "CGuiLight::BuildLight"
        assert parts.base_name == "BuildLight"
        assert parts.namespace == "CGuiLight"


class TestBounds:
    def test_a_name_that_nests_past_the_bound_is_refused_rather_than_recursed(self):
        deep = "f__F" + "PF" * 400 + "v"
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(deep, language="codewarrior")

    def test_the_empty_name_is_refused(self):
        with pytest.raises(DemanglingError):
            demangle.demangle_strict("", language="codewarrior")
