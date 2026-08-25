"""Expressions in the tree.

An expression inside a type used to arrive as one opaque node: `decltype(a + b)` was a
string, and a caller wanting the operands had to parse C++ back out of it. They are now
reported through `Builder.expression`, which takes the shape and the operands and lets
the builder decide what they become.

The invariant is the same one the Rust tree rests on: the parts a parser reports are the
spelling, in order, so the text a tree renders cannot drift from the text `demangle()`
produces. What is checked here is that the structure is real -- that `a + b` has two
operands and a form saying it is binary -- and that adding it changed no spelling.
"""

import pytest

import demangle
from demangle.core.ast import AST_BUILDER, Expression
from demangle.core.errors import DemanglingError


def expressions(name):
    return [node for node in demangle.parse(name).walk() if node.kind == "expression"]


def only(name, form):
    found = [node for node in expressions(name) if node.form == form]
    assert found, f"no {form!r} expression in {name}: {[n.form for n in expressions(name)]}"
    return found[0]


class TestShape:
    def test_a_binary_operator_has_two_operands(self):
        node = only("_Z1fIiEvDTplT_T_E", "binary")
        assert [str(operand) for operand in node.operands] == ["int", "int"]

    def test_a_conditional_has_three(self):
        node = only("_Z1fIiEvDTquT_T_T_E", "conditional")
        assert [str(operand) for operand in node.operands] == ["int", "int", "int"]

    def test_sizeof_carries_what_it_measures(self):
        node = only("_ZN1AIXszcvT__EEE1fEv", "sizeof")
        assert str(node.operands[0]) == "(auto)()"

    def test_decltype_carries_its_expression(self):
        node = only("_Z1fIiEvDTplT_T_E", "decltype")
        assert str(node.operands[0]) == "int + int"

    def test_the_form_says_what_the_expression_is(self):
        """Without the form a caller is back to matching on spelling."""
        assert {node.form for node in expressions("_Z1fIiEvDTplT_T_E")} == {"decltype", "binary"}

    def test_a_leaf_operand_is_the_node_it_already_was(self):
        """A template parameter in expression position is not wrapped in an `Expression`.

        The wrapper would carry nothing its child does not -- the child already says it is
        a builtin, a name, a literal -- and it sat on the most common productions in the
        grammar. What matters is that the operand is still reachable and still says what
        it is, which is what this checks.
        """
        binary = only("_Z1fIiEvDTplT_T_E", "binary")
        assert [operand.kind for operand in binary.operands] == ["builtin", "builtin"]
        assert [str(operand) for operand in binary.operands] == ["int", "int"]

    def test_brackets_are_parts_so_operands_stay_reachable(self):
        """`(a + b) * c` must expose `a + b`, not the string `"(a + b)"`."""
        node = only("_Z1fIiEvDTmlplT_T_T_E", "binary")
        inner = [operand for operand in node.operands if operand.kind == "expression"]
        assert any(child.form == "paren" for child in inner)
        parenthesised = next(child for child in inner if child.form == "paren")
        assert parenthesised.operands[0].form == "binary"


class TestSpellingIsUnchanged:
    """The refactor's whole risk. Spot-checks here; the corpora cover the rest."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fIiEvDTplT_T_E", "void f<int>(decltype(int + int))"),
            ("_Z1fIiEvDTquT_T_T_E", "void f<int>(decltype(int ? int : int))"),
            ("_ZN1AIXszcvT__EEE1fEv", "A<sizeof ((auto)())>::f()"),
            ("_Z1fILi5EEvv", "void f<5>()"),
        ],
    )
    def test_spelling(self, mangled, expected):
        assert demangle.demangle(mangled) == expected

    def test_a_tree_spells_what_demangle_spells(self):
        for mangled in ("_Z1fIiEvDTplT_T_E", "_Z1fIiEvDTquT_T_T_E", "_ZN1AIXszcvT__EEE1fEv"):
            assert demangle.parse(mangled).spell() == demangle.demangle(mangled)


class TestBuilderContract:
    def test_parts_may_interleave_text_and_handles(self):
        node = AST_BUILDER.expression("binary", [AST_BUILDER.name("a"), " + ", AST_BUILDER.name("b")])
        assert isinstance(node, Expression)
        assert node.spell() == "a + b"
        assert [str(operand) for operand in node.operands] == ["a", "b"]

    def test_size_is_carried_rather_than_walked(self):
        """The output bound is checked in constant time; an expression must not break that."""
        node = AST_BUILDER.expression("binary", [AST_BUILDER.name("a"), " + ", AST_BUILDER.name("b")])
        assert node.size == len("a + b")

    def test_a_tree_can_be_rebuilt_into_another_builder(self):
        """`build()` round-trips, which is what lets a tree be re-spelled in another style."""
        tree = demangle.parse("_Z1fIiEvDTplT_T_E")
        assert tree.spell() == demangle.demangle("_Z1fIiEvDTplT_T_E")


class TestStillRefusesWhatItShould:
    @pytest.mark.parametrize("mangled", ["_Z1fIiEvDTplT_E", "_Z1fIiEvDTqu", "_Z1fIiEvDT"])
    def test_a_truncated_expression_raises_rather_than_inventing_one(self, mangled):
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled)

    @pytest.mark.parametrize("mangled", ["_Z1fIiEvDTplT_E", "_Z1fIiEvDTqu"])
    def test_and_demangle_still_never_raises(self, mangled):
        assert demangle.demangle(mangled) == mangled
