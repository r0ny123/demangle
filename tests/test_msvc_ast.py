"""What `parse()` hands back for an MSVC name.

`demangle()` is covered against llvm-undname in test_msvc.py, name by name. What is
covered here is the other half: that the same parse is available as a tree, that the tree
says the true thing about the declaration -- which access, which convention, how many
parameters and of what types -- and that walking it and spelling it agree with the text
the fast path produces. A tree that spells something the fast path does not is worse than
no tree at all, because the two answers are both presented as this library's.
"""

from pathlib import Path

import pytest

import demangle
from demangle.core.ast import Node
from demangle.schemes.msvc.nodes import Declaration, FunctionType, Indirection

CORPUS = Path(__file__).parent / "conformance" / "msvc-llvm-corpus.txt"


def corpus():
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "\t" in line:
            yield line.split("\t", 1)[0]


def tree(mangled):
    return demangle.parse(mangled, language="msvc")


class TestShape:
    def test_a_free_function_is_a_declaration_over_a_function_type(self):
        parsed = tree("?f@@YAXH@Z")
        assert isinstance(parsed, Declaration)
        assert parsed.prefix == ""
        assert parsed.declarator.text == "f"
        signature = parsed.type
        assert isinstance(signature, FunctionType)
        assert signature.convention == "__cdecl"
        assert signature.returns.text == "void"
        assert [parameter.text for parameter in signature.parameters] == ["int"]

    def test_the_tree_is_more_than_one_node(self):
        """`parse()` must yield real structure, not a single opaque `Raw` leaf."""
        assert len(list(tree("?f@@YAXH@Z").walk())) > 1

    def test_a_member_function_keeps_its_access_and_its_cv_apart_from_its_type(self):
        parsed = tree("?f@S@@QBEHH@Z")
        assert parsed.spell() == "public: int __thiscall S::f(int) const"
        # access opens the spelling and the qualifier closes it; neither is part of the
        # type, and a caller filtering by access should not have to find it in the text.
        # Access and storage are separate fields because each can be suppressed alone.
        assert parsed.access == "public: "
        assert parsed.member_type == ""
        assert parsed.prefix == ""
        assert parsed.suffix == " const"
        assert parsed.declarator.text == "S::f"
        assert parsed.type.convention == "__thiscall"

    def test_a_pointer_to_member_function_nests_the_function_inside_the_pointer(self):
        parsed = tree("?g@@3P8B@@AEHXZQ1@")
        assert parsed.spell() == "int (__thiscall B::*g)(void)"
        pointer = parsed.type
        assert isinstance(pointer, Indirection)
        # the owner is part of the declarator in MSVC's spelling, which is why it is on
        # the indirection rather than being a type of its own
        assert pointer.sigil == "B::*"
        assert pointer.points_into_class
        assert isinstance(pointer.inner, FunctionType)
        assert pointer.inner.returns.text == "int"

    def test_a_template_reaches_the_tree_as_the_name_it_was_spelled_into(self):
        """Template arguments are resolved before this layer sees them; see nodes.py."""
        parsed = tree("??$fn@H@Foo@@QAEXH@Z")
        assert parsed.declarator.text == "Foo::fn<int>"
        assert parsed.spell() == "public: void __thiscall Foo::fn<int>(int)"

    def test_a_data_symbol_declares_a_type_rather_than_a_signature(self):
        parsed = tree("?d@foo@@0FB")
        assert (parsed.access, parsed.member_type) == ("private: ", "static ")
        assert not parsed.declares_a_function
        assert parsed.declarator.text == "foo::d"
        assert parsed.type.kind == "raw"
        assert next(parsed.find("function"), None) is None

    def test_an_array_nests_one_dimension_per_extent(self):
        parsed = tree("?arr@@3QAY01HB")
        assert parsed.spell() == "int const (*const arr)[2]"
        assert [node.dimension for node in parsed.find("array")] == ["2"]

    def test_a_symbol_with_nothing_to_take_apart_stays_a_leaf(self):
        """An RTTI locator names no declaration, so there is no shape to invent for it."""
        parsed = tree("??_R4Foo@@6B@")
        assert parsed.kind == "raw"
        assert parsed.spell() == "const Foo::`RTTI Complete Object Locator'"

    def test_every_node_is_a_core_node(self):
        """Structured consumers are written against `core.ast`, not against this scheme."""
        for node in tree("?g@@3P8B@@AEHXZQ1@").walk():
            assert isinstance(node, Node)


class TestAgreement:
    @pytest.mark.parametrize(
        "mangled",
        [
            "?f@@YAXH@Z",
            "?j@@3P6GHCE@ZA",
            "?f@@YAPAY01HXZ",
            "??0foo@@QAE@XZ",
            "?mbb@S@@QAEX_N0@Z",
            "?color3@@3QAY02$$CBNA",
            "?f@@YAXHZZ",
            "??_7type_info@@6B@",
        ],
    )
    def test_the_tree_spells_what_the_fast_path_spells(self, mangled):
        assert tree(mangled).spell() == demangle.demangle_strict(mangled, language="msvc")

    def test_the_two_agree_on_every_name_in_the_corpus(self):
        """The invariant that matters, checked where it is cheapest to check it."""
        disagreements = []
        for mangled in corpus():
            text = demangle.demangle(mangled, language="msvc")
            try:
                spelled = tree(mangled).spell()
            except demangle.DemanglingError:
                spelled = mangled
            if spelled != text:
                disagreements.append((mangled, text, spelled))
        assert disagreements == []


class TestDegradation:
    @pytest.mark.parametrize(
        "mangled",
        [
            "?",
            "?f@@YAXH@Z" + "@" * 40,
            "?f@@YAXH@",
            "?f@@YAX" + "P" * 200,
            "??@only_a_hash",
            "?\x01@@YAXH@Z",
            "not mangled at all",
        ],
    )
    def test_a_name_that_cannot_be_read_comes_back_unchanged(self, mangled):
        """`demangle()` never raises, whatever the tree layer decides about a name."""
        assert demangle.demangle(mangled) == mangled

    def test_truncation_at_every_offset_leaves_both_paths_agreeing(self):
        full = "?g@@3P8B@@AEHXZQ1@"
        for end in range(len(full) + 1):
            cut = full[:end]
            text = demangle.demangle(cut, language="msvc")
            try:
                parsed = tree(cut)
            except demangle.DemanglingError:
                assert text == cut
                continue
            assert parsed.spell() == text
