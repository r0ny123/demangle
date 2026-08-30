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

    @pytest.mark.parametrize("mangled", ["_Z1fIiEi", "_Z1fIiEv", "_Z1fI1DEa", "_ZN1a1bIiEEi"])
    def test_a_signature_with_no_parameter_type_is_not_a_declaration(self, mangled):
        """`<bare-function-type> ::= <signature type>+` is one or more, not zero or more.

        A template specialisation spends its first type on the return type -- a plain
        function encodes none, because overloads cannot differ by it -- so `_Z1fIiEi` has
        a return type and then nothing. That is not a declaration of anything, and both
        `c++filt` 2.42 and `llvm-cxxfilt` 18.1 hand it straight back. The reference reads
        the production as a do-while for exactly this reason.

        It was accepted here and spelled `int f<int>()`, which is what the *well formed*
        `_Z1fIiEiv` says: two manglings came back as one name, and one of them was not a
        mangling. Found by enumerating every Itanium name up to five characters over a
        grammar-shaped alphabet and asking both references about each one this reads.
        """
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="itanium")
        assert demangle.demangle(mangled) == mangled

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # The same names with a parameter list, which is the only difference.
            ("_Z1fIiEiv", "int f<int>()"),
            ("_Z1fIiEvv", "void f<int>()"),
            ("_Z1fI1DEav", "signed char f<D>()"),
            # A list that reads to nothing is still a list that was read: `v` is how the
            # grammar spells `()`, and an empty pack expansion drops out after the fact.
            ("_Z1fIJEEvDpT_", "void f<>()"),
            # And a plain function encodes no return type, so its one type is a parameter.
            ("_Z1fi", "f(int)"),
            ("_Z1fv", "f()"),
        ],
    )
    def test_the_rule_counts_types_read_and_not_parameters_kept(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="itanium") == expected

    @pytest.mark.parametrize("mangled", ["_Z1fT_", "_Z1f1AT_", "_Z1fT_i", "_ZN1a1bET_", "_Z1fT0_"])
    def test_a_template_parameter_with_no_arguments_in_scope_names_nothing(self, mangled):
        """`T_` indexes the enclosing `<template-args>`. A plain function has none.

        Both references hand every one of these back. This read them as `auto` -- so
        `_Z1f1AT_` was `f(A, auto)`, which is a declaration a reader would believe, of a
        type the encoding does not contain.

        The fallback that spells `auto` is right for the two readings that legitimately
        find nothing bound, and both of those have something in scope: a generic lambda
        occupies a level even when it declared no parameters, and a conversion operator's
        type is read ahead of the arguments that bind it. Over the 226,402 names of the
        corpora plus every Itanium symbol on this machine, exactly one reaches that
        fallback, and it is a generic lambda.
        """
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="itanium")
        assert demangle.demangle(mangled) == mangled

    def test_a_generic_lambdas_invented_parameter_still_reaches_the_fallback(self):
        """The other side: `Ul T_ E` has a level, and `auto` is the right answer there."""
        mangled = "_ZZ1fvENKUlT_E_clIiEEDaS_"
        assert "auto" in demangle.demangle_strict(mangled, language="itanium")

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # A parameter that *arrived* as `void` is not one that was written `v`.
            ("_Z1fIvEvT_", "void f<void>(void)"),
            ("_Z1fIJvEEvDpT_", "void f<void>(void)"),
            ("_Z1fIvEvPFvT_E", "void f<void>(void (*)(void))"),
            # Nor is a `v` that is not the first signature type.
            ("_Z1fIJEEvDpT_v", "void f<>(void)"),
            ("_Z1fvv", "f(void, void)"),
            ("_Z1fvi", "f(void, int)"),
            # And the shape the rule exists for, which is exactly one written `v`.
            ("_Z1fv", "f()"),
            ("_Z1fIiEvv", "void f<int>()"),
            ("_ZN1ScviEv", "S::operator int()"),
            ("_Z1fPFvvE", "f(void (*)())"),
        ],
    )
    def test_only_a_written_v_spells_the_empty_parameter_list(self, mangled, expected):
        """`f(void)` is `f()` only when the `void` is the `v` the grammar writes.

        Both references read it by position: the first signature type, if it is a
        literal `void`, *is* the empty list. This asked instead whether every parameter
        spelled `void` after the fact, which is a different question and got two shapes
        wrong.

        `_Z1fIvEvT_` is `template <class T> void f(T)` instantiated with `void`. Both
        references spell it `void f<void>(void)`; this spelled `void f<void>()`, dropping
        a parameter that is in the name -- and there is no reference behind that answer,
        which is the part that makes it a defect rather than a choice. A list of several
        voids is not an empty one either: `_Z1fvv` is `f(void, void)` to `c++filt` and
        refused outright by `llvm-cxxfilt`, and came back here as `f()`, which is what
        `_Z1fv` says.

        Every row now matches at least one reference, and matches `llvm-cxxfilt`
        wherever it reads the name at all. The two it does not are the two it refuses.
        """
        assert demangle.demangle_strict(mangled, language="itanium") == expected

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # The case that tells the two apart: an operator that takes a parameter.
            ("_ZN1ScviEiv", "S::operator int(int, void)"),
            ("_ZN1ScviEv", "S::operator int()"),
            ("_ZN1Scv7MuncherIJDpPT_EEIJFivEA_iEEEv", "S::operator Muncher<int (*)(), int (*) []><int (), int []>()"),
        ],
    )
    def test_a_conversion_operator_has_no_return_type_to_read(self, mangled, expected):
        """It encodes none however template it is: what it returns is in its name.

        This read one anyway and threw it away, which spent the first type of the
        signature. Invisible while that type is the `v` of an empty parameter list --
        discarding it and spelling `()` from what was left came to the same thing -- and
        wrong the moment the operator takes a parameter, where `_ZN1ScviEiv` came back
        as `S::operator int()` rather than `S::operator int(int, void)`.

        Distinct from the return type GNU *omits* on the function a local name is scoped
        by: that one is in the input and has to be read before it can be dropped. The two
        shared a flag, so fixing this by not reading turned `_M_construct<char const*>`'s
        `v` into a first parameter under `gnu`. They are separate flags now, and the
        pinned gnu score is what says so.
        """
        assert demangle.demangle_strict(mangled, language="itanium") == expected


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

    @pytest.mark.parametrize(
        "encoding",
        [
            "N1a1bES",  # a substitution whose `_` terminator is missing
            "N1a1bES0",
            "1fPS",
            "N1a1bERS",
        ],
    )
    def test_a_truncated_encoding_cannot_borrow_the_literal_that_bounds_it(self, encoding):
        """The window that stops the encoding before `_block_invoke` is the end of input.

        This shape is the only place a parser moves the end of input: a regex says where
        the encoding stops, `reader.length` is shortened to there, and the literal after
        it must be unreadable. `peek`, `take` and `eof` honoured that; `expect`, `eat`,
        `startswith`, `peek2` and `remaining` indexed the string and did not. So `S` at
        the very end took the `_` of `_block_invoke` as its terminator and
        `___ZN1a1bES_block_invoke` came back as `a::b(a)` -- 4,931 truncated encodings
        across the corpora read as though they were whole, every one of them spelling
        something that looks like a declaration.

        Both references leave every one of these alone.
        """
        windowed = f"___Z{encoding}_block_invoke"
        # Truncated on its own, and the window must not change that.
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(f"_Z{encoding}", language="itanium")
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(windowed, language="itanium")
        assert demangle.demangle(windowed) == windowed

    def test_the_same_encodings_read_when_they_are_not_truncated(self):
        """The other half: the bound refuses what runs past it and nothing else."""
        assert demangle.demangle("___ZN1a1bE_block_invoke") == "invocation function for block in a::b"
        # The `_` this one ends on is its own, and the literal's still follows it.
        assert demangle.demangle("___ZN1a1bES__block_invoke") == "invocation function for block in a::b(a)"
        assert demangle.demangle("_ZN1a1bES_") == "a::b(a)"

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

        The parameter of `operator()` is `int`, not `auto`: `S3_` names the entry the
        closure's own `T0_` contributed, and under `operator()<int, int>` that is the
        second argument. llvm-cxxfilt prints `auto` because it freezes the entry where it
        was made; GNU c++filt refuses this name, but on the same construct without the
        constraint -- `_ZZN5test71fIiEEvvENKUlTyT0_E_clIiiEEDaS1_` -- it prints `(int)`.
        Only the constraint spelling is this test's subject; the parameter is here so
        that a change to it has to be deliberate.
        """
        mangled = "_ZZN5test71fIiEEvvENKUlTyQaa1CIT_E1CITL0__ET0_E_clIiiEEDaS3_Q1CIDtfp_EE"
        assert demangle.demangle(mangled) == (
            "auto void test7::f<int>()::'lambda'<typename $T> requires C<T> && C<TL0_> (auto)"
            "::operator()<int, int>(int) const requires C<decltype(fp)>"
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
