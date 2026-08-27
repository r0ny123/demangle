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


class TestOperatorNamesAsCallees:
    """`on <operator-name>` -- a callee named by the operator it is.

    This is how an unresolved `a + b` inside a `decltype` is written. The two letters
    were falling through to the type productions, which read them as the builtin codes
    they happen also to be: `o` is `unsigned __int128` and `n` is `__int128`. The result
    was a signature naming two types that appear nowhere in the symbol, produced without
    any error to say so -- the worst way for a demangler to be wrong.
    """

    def test_a_binary_operator_callee(self):
        assert demangle.demangle("_Z1fI1AEDTclonplfp_fp_EET_") == "decltype(operator+(fp, fp)) f<A>(A)"

    def test_the_gnu_spelling_of_the_same_name(self):
        assert (
            demangle.demangle("_Z1fI1AEDTclonplfp_fp_EET_", style="gnu")
            == "decltype ((operator+)({parm#1}, {parm#1})) f<A>(A)"
        )

    def test_no_int128_appears_where_the_symbol_names_none(self):
        """The property, stated without reference to the right answer."""
        assert "__int128" not in demangle.demangle("_Z1fI1AEDTclonplfp_fp_EET_")

    def test_the_tree_agrees(self):
        name = "_Z1fI1AEDTclonplfp_fp_EET_"
        assert demangle.parse(name).spell() == demangle.demangle(name)


class TestProductionsTakenFromLibcxxabi:
    """Four shapes libcxxabi's own vectors found, each read from its parser.

    They are grouped because what they have in common is the source: LLVM's
    `ItaniumDemangle.h`, rather than the ABI document, which describes none of the
    four the way the reference actually reads them.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("___Z10blocksNRVOv_block_invoke", "invocation function for block in blocksNRVO()"),
            # The number distinguishes several blocks in one function and is not printed.
            ("___Z3foov_block_invoke_2", "invocation function for block in foo()"),
            ("___Z3foov_block_invoke2", "invocation function for block in foo()"),
            # Nor is a `.` suffix, where the reference discards the rest of the name.
            ("___Z3foov_block_invoke.25", "invocation function for block in foo()"),
            # `____Z` is the same thing with the symbol table's own underscore as well.
            ("____Z3foov_block_invoke", "invocation function for block in foo()"),
        ],
    )
    def test_a_block_written_in_a_cxx_function(self, mangled, expected):
        assert demangle.demangle(mangled) == expected

    @pytest.mark.parametrize("mangled", ["___Z3foov_block_invoke_", "___Z3foov_block_invokex"])
    def test_what_is_not_a_block_invocation(self, mangled):
        """An underscore with no number after it, and trailing text that is not a suffix."""
        assert demangle.demangle(mangled) == mangled

    def test_an_objective_c_block_is_still_objective_cs(self):
        """Only `__` and an *Itanium* encoding is claimed here; `__foo_block_invoke` is not."""
        assert demangle.demangle("___cfunc_block_invoke") == "block #1 in cfunc"

    def test_a_vendor_qualifier_and_its_cv_qualifiers_are_one_component(self):
        """`U3AS1Ki` enters one substitution, so `S0_` is the pointer and not the type.

        Two entries put `S0_` on `int const AS1` instead, and the second parameter lost
        its pointer -- a wrong answer rather than a refusal, which is worse.
        """
        assert demangle.demangle("_Z1fPU3AS1KiS0_") == "f(int const AS1*, int const AS1*)"
        assert demangle.demangle("_Z1fPU3AS1KiS_") == "f(int const AS1*, int const AS1)"
        assert demangle.demangle("_Z1fU3AS1KiS_") == "f(int const AS1, int const AS1)"

    def test_a_vendor_qualifier_goes_after_the_whole_declarator(self):
        """`void () block_pointer`, not `void block_pointer()`."""
        assert demangle.demangle("_Z1fU13block_pointerFvvE") == "f(void () block_pointer)"
        assert demangle.demangle("_Z1fPU13block_pointerFvvE") == "f(void () block_pointer*)"

    def test_a_tagged_abbreviation_is_substitutable_and_a_bare_one_is_not(self):
        """ABI 5.1.2: the tags are appended and *the result* is a component."""
        assert demangle.demangle("_Z1fSsB1XS_") == "f(std::string[abi:X], std::string[abi:X])"
        assert demangle.demangle("_Z1fSsS_") == "_Z1fSsS_"

    @pytest.mark.parametrize("mangled", ["_ZN1S1fILb1EEEv1XILUlvE_EE", "_ZN1S1fILb1EEEv1XILUlvE0_EE"])
    def test_a_lambda_written_as_a_template_argument(self, mangled):
        """The expression that made the closure, not the type it has."""
        assert demangle.demangle(mangled) == "void S::f<true>(X<[](){...}>)"

    def test_an_unnamed_type_is_not_a_value(self):
        """Only `Ul` is a lambda expression; `Ut` names a type and cannot be one."""
        assert demangle.demangle("_ZN1S1fILb1EEEv1XILUt_EE") == "_ZN1S1fILb1EEEv1XILUt_EE"


class TestTemplateParameterLevels:
    """`TL<level>_<index>_` names a parameter of an *enclosing* template, not the innermost.

    The table behind `T_` is a stack: level 0 is the innermost `<template-args>`, and
    each generic lambda and each template template parameter declaration opens a level
    of its own. Held flat, the levels overwrote each other and a `TL` reference came out
    as the numbering it carried -- `T`, `T1` -- which names nothing at all.

    All three vectors are libcxxabi's, and the expectations are `llvm-cxxfilt`'s.
    """

    def test_a_template_template_parameter_reaches_the_level_outside_it(self):
        """`$T` is the lambda's, `$T0` is the inner list's, and one declaration uses both."""
        assert demangle.demangle("_ZNK1xMUlTyTtTyTnT_TpTnPA3_TL0__ETpTyvE_clIi1XJfEEEDav") == (
            "auto x::'lambda'<typename $T, template<typename $T0, $T $N, $T0 (*...$N0) [3]> "
            "typename $TT, typename ...$T1>()::operator()<int, X, float>() const"
        )

    def test_a_lambda_in_a_type_keeps_the_enclosing_arguments_reachable(self):
        """`T_` is the function's `float`; `TL0__` is the lambda's own `$T`.

        A lambda that *is* the entity being named starts from nothing -- its parameters
        are level 0. One written inside a type or an expression stacks on top of what is
        already in scope, which is what lets these two references mean different things
        in the same signature.
        """
        assert demangle.demangle("_ZN1AIiE1fIfEEvDTLUlTyTtTyTnTL1__ETL0_1_T_TL0__E_EE") == (
            "void A<int>::f<float>(decltype([]<typename $T, "
            "template<typename $T0, $T0 $N> typename $TT>(auto, float, $T){...}))"
        )

    def test_a_lambda_that_declared_nothing_still_occupies_a_level(self):
        """`T_` is the function's `int` and `TL0__` is the lambda's `auto` (ABI 5.1.8)."""
        assert demangle.demangle("_ZN1C1fIiEEvDTtlNS_UlT_TL0__E_EEE") == (
            "void C::f<int>(decltype(C::'lambda'(int, auto){}))"
        )

    @pytest.mark.parametrize("mangled", ["_Z1fIiEvDTsrTL8_1_1xE", "_Z1fIiEvDTTL8_1_E"])
    def test_a_level_that_is_not_in_scope_is_refused(self, mangled):
        """Nothing is eight templates deep, so there is no parameter for this to be.

        `llvm-cxxfilt` refuses both of these. Naming one anyway -- the numbering used to
        be printed as `T1` -- is a type that appears nowhere in the symbol, which is the
        one thing worse than declining.
        """
        assert demangle.demangle(mangled) == mangled
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled)

    def test_a_requires_clause_spells_such_a_reference_instead_of_refusing_it(self):
        """Inside a constraint a reference is spelled by its own mangled text.

        `C<T> && C<TL0_>` -- not `C<int> && C<$T>`. The reference does the same and says
        why: not every enclosing template's parameters are tracked well enough to
        substitute reliably inside a `<constraint-expression>`, so it prints the
        numbering rather than a guess. That branch is taken before the level is looked
        up, so a level out of scope is spelled here rather than refused.
        """
        mangled = "_ZZN5test71fIiEEvvENKUlTyQaa1CIT_E1CITL0__ET0_E_clIiiEEDaS3_Q1CIDtfp_EE"
        assert demangle.demangle(mangled) == (
            "auto void test7::f<int>()::'lambda'<typename $T> requires C<T> && C<TL0_> (auto)"
            "::operator()<int, int>(auto) const requires C<decltype(fp)>"
        )


class TestAnExpansionOverAnEmptyPack:
    """`sp` over a pack with no members produces no argument, not an empty one.

    `Dp` already did this in a type list. In an expression the argument stayed, so
    `getT<$_5>()()(std::forward<>(fp))` was printed where the reference prints
    `getT<$_5>()()()` -- and the comma that would have preceded it has to go too.
    """

    def test_the_argument_and_its_comma_both_disappear(self):
        mangled = (
            "_ZNK3Ncr6Silver7Utility6detail12CallOnThreadIZ53-[DeploymentSetupController "
            "handleManualServerEntry:]E3$_5EclIJEEEDTclclL_ZNS2_4getTIS4_EERT_vEEspclsr3stdE"
            "7forwardIT_Efp_EEEDpOSA_"
        )
        assert demangle.demangle(mangled).startswith(
            "decltype(-[DeploymentSetupController handleManualServerEntry:]::$_5& "
            "Ncr::Silver::Utility::detail::getT<-[DeploymentSetupController "
            "handleManualServerEntry:]::$_5>()()())"
        )

    def test_an_expansion_over_a_pack_that_has_members_still_prints(self):
        assert demangle.demangle("_Z1fIJiiEEvDpT_") == "void f<int, int>(int, int)"


class TestTheQualifiedFormOfAnUnresolvedName:
    """`srN <unresolved-type> [<template-args>] <level>* E <base-unresolved-name>`.

    Two things the ABI's own grammar does not say and the reference does: the argument
    list is allowed after the type in the `N` form, and the qualifier levels after it
    may be *zero*, because the arguments are the whole qualification.
    """

    def test_template_arguments_may_follow_the_type_with_no_levels_after_them(self):
        mangled = "_ZN5test71XIiEC1IdEEPT_PNS_5int_cIXplL_ZNS_4metaIiE5valueEEsrNS6_IS3_EE5valueEE4typeE"
        assert demangle.demangle(mangled) == (
            "test7::X<int>::X<double>(double*, "
            "test7::int_c<test7::meta<int>::value + test7::meta<double>::value>::type*)"
        )

    def test_the_arguments_are_not_part_of_the_substitution_the_type_records(self):
        """The reference records the bare parameter, so a later `S_` names it alone."""
        assert demangle.demangle("_Z1fIiEvDTsrT_1xES0_") == "void f<int>(decltype(int::x), int)"
