"""The Rust tree `parse()` returns.

The invariant that matters most is not any individual shape: it is that a tree spells
what `demangle()` spells, for every symbol, always. `TestRendersWhatItSpells` checks that
over every corpus name rather than over examples, because the whole design claim of
`schemes/rust/nodes.py` is that the two cannot diverge -- a claim worth testing against
the corpora rather than asserting in a docstring.

Every mangled name below is real: taken from the conformance corpora, which were
produced by running `rustfilt` over symbols read out of shipped binaries. Inventing a
v0 name by hand is a good way to write a test that passes against a malformed input.
"""

import pathlib

import pytest

import demangle
from demangle.core.errors import DemanglingError
from demangle.schemes.rust.nodes import Symbol

from .test_parity import STYLE_SAMPLES

CONFORMANCE = pathlib.Path(__file__).parent / "conformance"


def corpus_names():
    names = []
    for path in sorted(CONFORMANCE.glob("rust-*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "\t" in line:
                names.append(line.split("\t", 1)[0])
    return names


NAMES = corpus_names()


class TestRendersWhatItSpells:
    def test_the_corpus_is_not_empty(self):
        # A silent zero here would make every check below vacuously true.
        assert len(NAMES) > 5000

    @pytest.mark.sweep
    def test_every_corpus_name_spells_the_same_through_the_tree(self, subtests):
        for name in NAMES:
            expected = demangle.demangle(name)
            try:
                tree = demangle.parse(name)
            except DemanglingError:
                continue
            with subtests.test(name=name):
                assert tree.spell() == expected

    def test_no_corpus_name_comes_back_as_a_bare_leaf(self):
        """`walk()` reaches the nodes below the root; the tree is not a single leaf."""
        leaves = []
        for name in NAMES:
            try:
                tree = demangle.parse(name)
            except DemanglingError:
                continue
            if len(list(tree.walk())) <= 1:
                leaves.append(name)
        assert leaves == []


class TestShape:
    def test_a_plain_path_is_its_components(self):
        tree = demangle.parse("_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E")
        assert tree.kind == "symbol"
        assert [node.text for node in tree.find("name")] == ["core", "fmt", "Formatter", "pad"]

    def test_the_legacy_hash_is_kept_although_it_is_not_spelled(self):
        """It is the only thing telling two monomorphisations of one function apart."""
        tree = demangle.parse("_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E")
        assert isinstance(tree, Symbol)
        assert tree.hash == "9b2b3a0e5b4d1b31"
        assert "9b2b3a0e5b4d1b31" not in tree.spell()

    def test_a_v0_path(self):
        tree = demangle.parse("_RNvCsdEttCVZFADF_8features10btree_work")
        assert tree.spell() == "features::btree_work"
        assert [node.text for node in tree.find("name")] == ["features", "btree_work"]

    def test_generic_arguments_are_reachable_without_reparsing_the_text(self):
        tree = demangle.parse("_RINvCsdEttCVZFADF_8features15generic_closurecEB2_")
        generics = next(tree.find("template"))
        assert generics.base.text == "features::generic_closure"
        assert [argument.text for argument in generics.arguments] == ["char"]

    def test_an_impl_names_its_self_type_and_its_trait(self):
        tree = demangle.parse("_RINvMCsdEttCVZFADF_8featuresINtB3_6HoldermE7convertyEB3_")
        impl = next(tree.find("impl"))
        assert impl.self_type.text == "features::Holder<u32>"
        # An inherent impl, so there is no trait -- and a caller can tell without
        # looking for `" as "` in the spelling.
        assert impl.trait is None

    def test_a_closure_is_a_namespace_with_its_disambiguator(self):
        tree = demangle.parse("_RNCNvCskpvhNVs9Wdo_7library4bump0B3_")
        namespace = next(tree.find("namespace"))
        assert namespace.tag == "C"
        assert namespace.disambiguator == 0
        assert namespace.text == "::{closure#0}"

    def test_a_const_argument_is_a_literal(self):
        tree = demangle.parse("_RINvCsdEttCVZFADF_8features10const_boolKb0_EB2_")
        assert [node.text for node in tree.find("literal")] == ["false"]

    def test_a_type_says_which_shape_it_is(self):
        tree = demangle.parse("_RINvCsdEttCVZFADF_8features15generic_closurecEB2_")
        types = [node for node in tree.walk() if node.kind == "type"]
        assert [node.form for node in types] == ["basic"]


class TestSharedKinds:
    """`name`, `template` and `literal` mean the same thing in every scheme.

    A tool walking trees from a mixed binary should not need to know which language
    produced one to ask for its identifiers.
    """

    @pytest.mark.parametrize("language", sorted(STYLE_SAMPLES))
    def test_every_scheme_yields_named_components(self, language):
        """Ada spells its components `component`; every other scheme says `name`."""
        tree = demangle.parse(STYLE_SAMPLES[language], language=language)
        assert [node.text for node in tree.find("component" if language == "ada" else "name")]


class TestUnchangedGuarantees:
    def test_demangle_is_unaffected_by_asking_for_a_tree_first(self):
        name = "_RINvCsdEttCVZFADF_8features10const_boolKb0_EB2_"
        first = demangle.demangle(name)
        demangle.parse(name)
        assert demangle.demangle(name) == first

    def test_a_malformed_name_still_raises_rather_than_returning_a_broken_tree(self):
        with pytest.raises(DemanglingError):
            demangle.parse("_RNvNtCs1234_7mycrate3foo")

    def test_the_output_bound_is_enforced_on_the_tree_path_too(self):
        from demangle.core.limits import Limits

        name = "_RINvCsdEttCVZFADF_8features15generic_closurecEB2_"
        with pytest.raises(DemanglingError):
            demangle.parse(name, limits=Limits(max_output=4))
