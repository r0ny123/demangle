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
        # `_ZN1AIXszcvi_EEE1fEv` rather than `_ZN1AIXszcvT__EEE1fEv`, which was here
        # before: that one puts a `T_` inside the very argument list it belongs to, and
        # both references hand it back. This is the same shape with a concrete type, and
        # all three read it alike.
        node = only("_ZN1AIXszcvi_EEE1fEv", "sizeof")
        assert str(node.operands[0]) == "(int)()"

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
            ("_ZN1AIXszcvi_EEE1fEv", "A<sizeof ((int)())>::f()"),
            ("_Z1fILi5EEvv", "void f<5>()"),
        ],
    )
    def test_spelling(self, mangled, expected):
        assert demangle.demangle(mangled) == expected

    def test_a_tree_spells_what_demangle_spells(self):
        for mangled in ("_Z1fIiEvDTplT_T_E", "_Z1fIiEvDTquT_T_T_E", "_ZN1AIXszcvi_EEE1fEv"):
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
        "mangled", ["_ZZ1fPiES_", "_ZZ1fPiES_1g", "_ZZ1fN1A1BEES0_", "_ZZaSFvOEES_", "_ZZeqFvOEES_z"]
    )
    def test_a_bare_substitution_is_not_a_name(self, mangled):
        """`<name> ::= <unscoped-template-name> <template-args>` is the only production
        that lets a back-reference stand where a name goes, and it ends in arguments.

        `llvm-cxxfilt` refuses `S_` alone there. libiberty's `d_name` carries a comment
        saying the grammar does not permit the case and that it does not bother to
        check, so `c++filt` reads `_ZZ1fPiES_` as `f(int*)::int*`, a local entity that
        is a type, and this did too. The last two are a fuzzer's finds in libiberty's
        own test suite, which both references refuse; this answered
        `operator=(void () &&)::void () &&` for the first.
        """
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="itanium")
        assert demangle.demangle(mangled) == mangled
        assert demangle.demangle(mangled, style="gnu") == mangled

    def test_with_arguments_it_is_a_name(self):
        assert demangle.demangle("_ZZ1fPiES_IvE") == "f(int*)::int*<void>"
        assert demangle.demangle("_ZSt4sortIPiEvT_S0_") == "void std::sort<int*>(int*, int*)"

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

    @pytest.mark.parametrize(
        "mangled",
        [
            "_Z1fDTaE",  # a type where `decltype` wants an expression
            "_Z1fIXaEE",  # and where `X ... E` wants one
            "_Z1fAa_a",  # a type as an array bound
            "_Z1fDBa_",  # a type as a bit width
            "_Z1fDTDTfp_EEv",  # a `decltype` inside a `decltype`
        ],
    )
    def test_a_type_is_not_an_expression(self, mangled):
        """`_expression` used to end by reading whatever could open a `<type>` as one.

        Its comment said array bounds and non-type template arguments arrive there.
        Instrumented over the conformance corpora and every Itanium symbol on this
        machine -- 137,561 names -- it fired exactly zero times, because both of those
        productions read their operand themselves: `A <number> _` through `digits`,
        `A _ <expression> _` through `expression_text`, and `<template-arg>`'s type
        alternative in `template_arg`'s own branch.

        What it did do was give malformed input a spelling. `decltype(signed char)` and
        `_BitInt(signed char)` are not things, and `f(signed char [signed char])` is an
        array whose bound is a type. `llvm-cxxfilt`'s `parseExpr` has no type
        alternative at all and `c++filt` reads none of these either -- including the
        nested `decltype`, which looks like it ought to work and does not.
        """
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="itanium")
        assert demangle.demangle(mangled) == mangled

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # The expressions that legitimately mention a type still read.
            ("_Z1fDTfp_Ev", "f(decltype(fp), void)"),
            ("_Z1fDTdtfp_1xEv", "f(decltype(fp.x), void)"),
            ("_Z1fDTngngngfp_Ev", "f(decltype(-(-(-fp))), void)"),
            ("_Z1fA1_iv", "f(int [1], void)"),
            ("_Z1fIiEDTT_Ev", "decltype(int) f<int>()"),
        ],
    )
    def test_what_an_expression_may_still_hold(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="itanium") == expected

    @pytest.mark.parametrize(
        "mangled",
        ["_ZNSaEv", "_ZNKSaE", "_ZN1aSaEv", "_ZN1aS_Ev", "_ZN1a1bS_Ev", "_ZNSaSaEv"],
    )
    def test_a_nested_name_may_not_end_in_a_substitution(self, mangled):
        """`N ... <prefix> <unqualified-name> E`. A `<substitution>` is not one of those.

        It appears in `<prefix>` and nowhere else, so a nested name whose last component
        is a back reference is not a nested name. Both references refuse every shape of
        it, and the readings were the kind a person would believe: `_ZNSaEv` as
        `std::allocator()`, `_ZN1aSaEv` as `a::std::allocator()` -- a `std::` nested
        inside an `a::` -- and `_ZN1aS_Ev` as `a::a()`.
        """
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="itanium")
        assert demangle.demangle(mangled) == mangled

    @pytest.mark.parametrize(
        "mangled",
        ["_Z1fN1aME", "_Z1fN1a1bME", "_Z1fNSaME", "_Z1fN1aIiEME", "_ZN1aMEv"],
    )
    def test_a_nested_name_may_not_end_on_a_member_or_closure_prefix(self, mangled):
        """The same rule, and the other production a `<prefix>` can end with.

        `<data-member-prefix> ::= <member source-name> [<template-args>] M` and
        `<closure-prefix> ::= [<prefix>] <unqualified-name> M` are both `<prefix>`
        productions, so something still has to be named inside the member or the
        closure. The `M` carries no spelling, and skipping it silently made
        `_Z1fN1aME` -- "a member of `a`, and here is which one" -- come back as `f(a)`.
        `c++filt` refuses all of these; `llvm-cxxfilt` reads the ones whose prefix is a
        source name and refuses the one whose prefix is a substitution, which is the
        grammar half-applied.
        """
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="itanium")
        assert demangle.demangle(mangled) == mangled

    @pytest.mark.parametrize(
        "mangled",
        [
            # A <substitution>, anywhere but the front.
            "_ZN1aSt1bEv",
            "_ZN1aSa1bEv",
            "_ZN1aSaIwE1bEv",
            "_ZN1a1bS_1cEv",
            "_ZNSt1NSt14numeric_limitsImE10is_integerE",
            # A <decltype>, and a <template-param>.
            "_ZN1aDtfp_E1bEv",
            "_ZN1aT_1bEv",
        ],
    )
    def test_a_prefix_may_only_open_with_a_substitution_decltype_or_template_parameter(self, mangled):
        """Five of the seven `<prefix>` productions are bases and take no prefix on the left.

        ```
        <prefix> ::= <unqualified-name> | <prefix> <unqualified-name>
                   | <template-prefix> <template-args> | <closure-prefix>
                   | <template-param> | <decltype> | <substitution>
        ```

        Reading a `<substitution>`, a `<decltype>` or a `<template-param>` anywhere in the
        prefix spelled a scope inside a scope that cannot contain it: `_ZN1aSt1bEv` as
        `a::std::b()`, `_ZN1aSa1bEv` as `a::std::allocator::b()`, `_ZN1a1bS_1cEv` as
        `a::b::a::c()`, `_ZN1aDtfp_E1bEv` as `a::decltype(fp)::b()`. Every one is a
        declaration a person would believe and none is a name any compiler writes. Both
        references refuse all of them.

        Found by mutating real libstdc++ symbols: a deleted or duplicated character in a
        long `_ZNSb...` name leaves an abbreviation stranded mid-prefix, and 34 of the
        first sitting's divergences were this one shape.
        """
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="itanium")
        assert demangle.demangle(mangled) == mangled

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # At the front, which is where they occur, all three still read.
            ("_ZNSt1a1bEv", "std::a::b()"),
            ("_ZNSaIwE1bEv", "std::allocator<wchar_t>::b()"),
            ("_ZNSt3maxIiEEvv", "void std::max<int>()"),
            ("_ZNDtfp_E1bEv", "decltype(fp)::b()"),
            ("_Z1fIiEvNT_1aE", "void f<int>(int::a)"),
            (
                "_ZNSbIwSt11char_traitsIwESaIwEE1bEv",
                "std::basic_string<wchar_t, std::char_traits<wchar_t>, std::allocator<wchar_t>>::b()",
            ),
        ],
    )
    def test_a_base_production_at_the_front_of_a_prefix_is_untouched(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="itanium") == expected

    def test_a_closure_prefix_with_its_lambda_after_it_still_reads(self):
        """The shape that occurs: a lambda mangled inside a data member's initialiser."""
        name = "_ZZN1a1bMUlvE_clEvENKUlvE_clEv"
        assert demangle.demangle_strict(name, language="itanium") == (
            "a::b::'lambda'()::operator()()::'lambda'()::operator()() const"
        )

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # An abbreviation as a *prefix* is the shape that occurs, and is untouched.
            ("_ZNSaC1Ev", "std::allocator::allocator()"),
            ("_ZNSa1bEv", "std::allocator::b()"),
            ("_ZNSt3maxIiEEvv", "void std::max<int>()"),
            ("_ZN1a1bEv", "a::b()"),
        ],
    )
    def test_a_substitution_as_a_prefix_is_untouched(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="itanium") == expected

    @pytest.mark.parametrize("mangled", ["_Z1fLL1A", "_Z1fLLL1A", "_ZLL1fv"])
    def test_there_is_one_internal_linkage_marker_and_not_a_run_of_them(self, mangled):
        """`<unqualified-name> ::= [<module-name>] [L] <name body> [<abi-tags>]`.

        The marker carries no spelling of its own, and this read the rest of the name
        recursively -- which accepted a run of them, so `_Z1fLL1A` and `_Z1fLLL1A` both
        came back as `f(A)`, the spelling the well-formed `_Z1fL1A` has. Both references
        refuse the second one.
        """
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="itanium")
        assert demangle.demangle(mangled) == mangled

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fL1A", "f(A)"),
            ("_ZL1fv", "f()"),
            ("_ZN1aL1bEv", "a::b()"),
            # One on the entity and one inside a parameter's type are two names, not a run.
            ("_ZL1fL1A", "f(A)"),
        ],
    )
    def test_one_internal_linkage_marker_per_name_still_reads(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="itanium") == expected

    @pytest.mark.parametrize("mangled", ["_Z1fIET_a", "_Z1fIET_v", "_Zcv1BIRT_EIS1_E"])
    def test_a_template_parameter_in_an_empty_argument_list_binds_to_nothing(self, mangled):
        """`I E` installs a list with nothing in it, and `T_` indexes into it.

        The `auto` fallback is for the two readings where nothing is bound on purpose: a
        generic lambda's invented parameters (ABI 5.1.8) and a conversion operator's type
        read ahead of its arguments. An empty argument list is neither -- it is a list
        that was read and is empty -- and `_Z1fIET_a` came back as `auto f<>(signed
        char)`, which reads as a declaration. Both references refuse it.

        Marked by *where the reading is* rather than by what is in scope, because the two
        are indistinguishable from the tables: a lambda's level and an empty argument
        list are both one level holding nothing.
        """
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="itanium")
        assert demangle.demangle(mangled) == mangled

    def test_the_two_readings_that_may_still_find_nothing_bound(self):
        """A generic lambda's `auto`, and a conversion operator read before its arguments."""
        assert (
            demangle.demangle_strict("_ZZ1fvENKUlT_E_clIiEEDaS_", language="itanium")
            == "auto f()::'lambda'(auto)::operator()<int>(int) const"
        )
        assert (
            demangle.demangle_strict("_ZN1Scv7MuncherIJDpPT_EEIJFivEA_iEEEv", language="itanium")
            == "S::operator Muncher<int (*)(), int (*) []><int (), int []>()"
        )

    @pytest.mark.parametrize("mangled", ["_Z1fILaEE", "_Z1fILbEE", "_Z1fILPiEE", "_Z1fILSt9nullptr_tEE"])
    def test_a_literal_with_no_value_is_not_a_value(self, mangled):
        """`L <type> <value> E`, and the value is not optional.

        `_Z1fILaEE` came back as `f<(signed char)0>` -- a zero that is nowhere in the
        name, and the same spelling the well-formed `_Z1fILa0EE` has, so two manglings
        arrived as one name and one of them was not a mangling. Both references hand
        every one of these back.

        `Dn` is the exception, and it is decided by the two characters written rather
        than by what they spell: `_Z1fILSt9nullptr_tEE` names `std::nullptr_t` the long
        way round and both references refuse it too.
        """
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="itanium")
        assert demangle.demangle(mangled) == mangled

    @pytest.mark.parametrize(
        ("mangled", "llvm", "gnu"),
        [
            # The references disagree about the no-value form and agree about the other.
            ("_Z1fILDnEE", "f<nullptr>", "f<decltype(nullptr)>"),
            ("_Z1fILDn0EE", "f<nullptr>", "f<(decltype(nullptr))0>"),
        ],
    )
    def test_the_two_nullptr_literals_are_two_spellings_under_gnu(self, mangled, llvm, gnu):
        """`LDnE` is the type on its own; `LDn0E` is a value of it.

        `c++filt` writes `decltype(nullptr)` for the first and `(decltype(nullptr))0`
        for the second; `llvm-cxxfilt` writes `nullptr` for both. This wrote
        `(decltype(nullptr))0` for both under `gnu`, which is one of them.
        """
        assert demangle.demangle_strict(mangled, language="itanium", style="llvm") == llvm
        assert demangle.demangle_strict(mangled, language="itanium", style="gnu") == gnu

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

    @pytest.mark.parametrize("mangled", ["_Zcv1BIRT_E", "_Zcv1BIRT_Ev", "_ZcvT_", "_ZN1AcvT_Ev"])
    def test_a_conversion_operator_needs_the_arguments_its_type_ran_ahead_of(self, mangled):
        """The conversion operator's reading is provisional: `_reread_conversion` makes
        it again once the arguments bind it. When none follow, nothing ever will, and
        both references refuse the name where this spelled `operator B<auto&>`.
        `tools/mutate.py --seed 18`."""
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="itanium")

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_ZcvT_IiE", "operator int<int>"),
            ("_Zcv1BIRT_EIiEv", "operator B<int&><int>()"),
            ("_ZN1AcvPT_IcEEv", "A::operator char*<char>()"),
            ("_ZN1AIiEcvT_Ev", "A<int>::operator int()"),
        ],
    )
    def test_a_conversion_operator_whose_arguments_come_still_reads(self, mangled, expected):
        assert demangle.demangle(mangled) == expected

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


class TestAVendorExtendedTypeTakesOneTypeArgument:
    """`u <source-name> I <type> E`, as `llvm-cxxfilt` reads it: a type transformation
    over one type, spelled as a call. The ABI writes `[<template-args>]`, but no compiler
    emits anything else there, `llvm-cxxfilt` refuses `u7__decayIllE` outright and
    `c++filt` 2.42 refuses the whole form. Reading a full argument list spelled
    `__decay(long, long, ...)` for a name neither reads; `tools/mutate.py --seed 3`."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z2f5IiEvu7__decayIlE", "void f5<int>(__decay(long))"),
            ("_Z1fu3fooIiE", "f(foo(int))"),
            ("_Z1fu3fooIiES_", "f(foo(int), foo(int))"),
            ("_Z1fu3foo", "f(foo)"),
        ],
    )
    def test_one_type(self, mangled, expected):
        assert demangle.demangle(mangled) == expected

    @pytest.mark.parametrize("mangled", ["_Z2f5IiEvu7__decayIllE", "_Z1fu3fooIiiE", "_Z1fu3fooILi1EE"])
    def test_anything_else_is_refused(self, mangled):
        assert demangle.demangle(mangled) == mangled


class TestAFloatingPointLiteral:
    """`L <d|e|f> <hex> E`: the value's bytes in hex, most significant first.

    `llvm-cxxfilt` decodes them and prints the number with glibc's `%a`; `c++filt`
    brackets the hex after the type. This printed `(double)4048f5c28f5c28f6` in both
    styles -- neither reference's spelling -- and no conformance corpus carries one,
    because the test suites the corpora come from predate floating-point template
    arguments. The llvm spellings here are `llvm-cxxfilt` 18's on x86-64, and the
    printer is checked against it over 4,580 random and boundary values.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fILd4048f5c28f5c28f6EEvv", "void f<0x1.8f5c28f5c28f6p+5>()"),
            ("_Z1fILd3ff0000000000000EEvv", "void f<0x1p+0>()"),
            ("_Z1fILd0000000000000000EEvv", "void f<0x0p+0>()"),
            ("_Z1fILd8000000000000000EEvv", "void f<-0x0p+0>()"),
            ("_Z1fILd000fffffffffffffEEvv", "void f<0x0.fffffffffffffp-1022>()"),
            ("_Z1fILdfff0000000000000EEvv", "void f<-inf>()"),
            ("_Z1fILd7ff8000000000000EEvv", "void f<nan>()"),
            ("_Z1fILf40490fdbEEvv", "void f<0x1.921fb6p+1f>()"),
            # A subnormal float is a normal double once promoted.
            ("_Z1fILf00000001EEvv", "void f<0x1p-149f>()"),
            ("_Z1fILfffc00000EEvv", "void f<-nanf>()"),
            # The x87 format keeps its integer bit, and glibc prints the top four bits of
            # the mantissa as the leading digit rather than normalising.
            ("_Z1fILe3fff8000000000000000EEvv", "void f<0x8p-3L>()"),
            ("_Z1fILe4000c8f5c28f5c28f5c3EEvv", "void f<0xc.8f5c28f5c28f5c3p-2L>()"),
            ("_Z1fILe00000000000000000001EEvv", "void f<0x0.000000000000001p-16385L>()"),
            ("_Z1fILe00018000000000000000EEvv", "void f<0x8p-16385L>()"),
            ("_Z1fILe7fff8000000000000000EEvv", "void f<infL>()"),
            # A set exponent with the integer bit clear is an encoding no operation
            # produces, and glibc prints it as a NaN; so is the pseudo-infinity.
            ("_Z1fILe40004000000000000000EEvv", "void f<nanL>()"),
            ("_Z1fILe7fff0000000000000000EEvv", "void f<nanL>()"),
            # A long double the width of a double, and the IEEE quad.
            ("_Z1fILe3ff0000000000000EEvv", "void f<0x1p+0L>()"),
            ("_Z1fILe3fff0000000000000000000000000000EEvv", "void f<0x1p+0L>()"),
            ("_Z1fILe00000001000000000000000000000000EEvv", "void f<0x0.0001p-16382L>()"),
        ],
    )
    def test_the_llvm_spelling(self, mangled, expected):
        assert demangle.demangle(mangled) == expected

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # g++ on x86-64, `template <long double V> struct LD`: the x87 value in the
            # low ten bytes of a sixteen-byte `long double`, written most significant
            # byte first, so six bytes of zero padding lead. Each expected value is what
            # `printf("%La")` printed for the same literal in the same build.
            ("_ZN2LDILe0000000000003fffc000000000000000EE1sE", "LD<0xcp-3L>::s"),  # 1.5L
            ("_ZN2LDILe000000000000c000d000000000000000EE1sE", "LD<-0xdp-2L>::s"),  # -3.25L
            ("_ZN2LDILe0000000000004000c90fdaa22168c235EE1sE", "LD<0xc.90fdaa22168c235p-2L>::s"),  # pi
            ("_ZN2LDILe0000000000000c179c3d73864f3805c0EE1sE", "LD<0x9.c3d73864f3805cp-13291L>::s"),  # 1e-4000L
            ("_ZN2LDILe00000000000000000000000663278e62EE1sE", "LD<0x0.000000663278e62p-16385L>::s"),  # 1e-4940L
            ("_ZN2LDILe0000000000007fff8000000000000000EE1sE", "LD<infL>::s"),
            ("_ZN2LDILe000000000000ffff8000000000000000EE1sE", "LD<-infL>::s"),
            ("_ZN2LDILe00000000000000000000000000000000EE1sE", "LD<0x0p+0L>::s"),
            ("_ZN2LDILe00000000000080000000000000000000EE1sE", "LD<-0x0p+0L>::s"),
            # g++ on i386, where the type is twelve bytes: two bytes of padding.
            ("_ZN2LDILe00003fffc000000000000000EE1sE", "LD<0xcp-3L>::s"),
            ("_ZN2LDILe0000c000d000000000000000EE1sE", "LD<-0xdp-2L>::s"),
            # clang++ on either, which writes the ten bytes and nothing else.
            ("_ZN2LDILe3fffc000000000000000EE1sE", "LD<0xcp-3L>::s"),
            ("_ZN2LDILec000d000000000000000EE1sE", "LD<-0xdp-2L>::s"),
        ],
    )
    def test_the_x87_format_at_the_width_gplusplus_pads_it_to(self, mangled, expected):
        """Every one of these names was read off a g++ 13 or clang 18 object file.

        The padded forms read as IEEE quads here -- thirty-two digits is a quad's width
        too -- and `1.5L` came back `0x0.000000003fffcp-16382L`, a wrong number.
        `llvm-cxxfilt` on x86-64 refuses them for not being the twenty digits it expects,
        so the reference never saw the defect; `c++filt` brackets the digits unread. The
        twelve zero digits that lead every padded x87 value are what tell it from a quad,
        at the price of one quad: a denormal below 2^-16414, whose leading digits are
        zero too, now reads as the x87 value it also spells. The quad row in
        `test_the_llvm_spelling` is a denormal large enough to keep its leading digits.
        """
        assert demangle.demangle(mangled) == expected

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fILd4048f5c28f5c28f6EEvv", "void f<(double)[4048f5c28f5c28f6]>()"),
            ("_Z1fILf40490fdbEEvv", "void f<(float)[40490fdb]>()"),
            ("_Z1fILe3fff8000000000000000EEvv", "void f<(long double)[3fff8000000000000000]>()"),
            # The padded x87 forms are bracketed as written, like everything else.
            (
                "_ZN2LDILe0000000000003fffc000000000000000EE1sE",
                "LD<(long double)[0000000000003fffc000000000000000]>::s",
            ),
            ("_ZN2LDILe00003fffc000000000000000EE1sE", "LD<(long double)[00003fffc000000000000000]>::s"),
        ],
    )
    def test_the_gnu_spelling(self, mangled, expected):
        assert demangle.demangle(mangled, style="gnu") == expected

    @pytest.mark.parametrize(
        "mangled",
        [
            "_Z1fILdi7EEvv",
            "_Z1fILd4048EEvv",
            "_Z1fILf4049EEvv",
            "_Z1fILe4049EEvv",
            "_Z1fILfzzzzzzzzEEvv",
            # Twenty-four digits are g++'s i386 form and nothing else, and that form
            # opens with two bytes of zero padding.
            "_Z1fILe12343fffc000000000000000EEvv",
        ],
    )
    def test_the_wrong_width_is_refused_in_every_style(self, mangled):
        """`llvm-cxxfilt` refuses these; `c++filt` brackets any run of characters. A
        style chooses a spelling, never whether a name reads, so both refuse."""
        assert demangle.demangle(mangled) == mangled
        assert demangle.demangle(mangled, style="gnu") == mangled

    @pytest.mark.parametrize(
        "mangled", ["_Z1fILf3F800000EEvv", "_Z1fILd3FF0000000000000EEvv", "_Z1fILdABCDEF0123456789EEvv"]
    )
    def test_an_uppercase_digit_is_refused(self, mangled):
        """The ABI says lowercase, and LLVM's main branch refuses anything else. 18.1
        tested with `isxdigit` and then subtracted `'a'` regardless, so `3F800000` came
        back `0x1p-64f` there -- and this read it as `0x1p+0f`, a value no compiler wrote
        under a name none writes. `c++filt` brackets the digits as they stand."""
        assert demangle.demangle(mangled) == mangled
        assert demangle.demangle(mangled, style="gnu") == mangled


class TestAStringLiteralArgument:
    """A `char` array in a braced initialiser is a string: `tl A3_c Lc104E Lc105E E` is
    `"hi"`, which is how LLVM's main branch prints it; 18.1 and `c++filt` spell the
    array, `char [3]{(char)104, (char)105}`. Both say the same thing and one is readable.

    Every g++ and clang++ row was read off an object file: g++ writes the bytes of
    `"hé"` unsigned, `Lc195ELc169E`, and clang writes them signed, `Lcn61ELcn87E`, and
    they are the same two bytes.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fIXtlA3_cLc104ELc105EEEEvv", 'void f<"hi">()'),
            ("_Z1fIXtl2FStlA4_cLc104ELc195ELc169EEEEEvv", 'void f<FS{"hé"}>()'),
            ("_Z1fIXtl2FStlA4_cLc104ELcn61ELcn87EEEEEvv", 'void f<FS{"hé"}>()'),
            # The C escapes, the octal digit below them, and the hex escape for the rest
            # of the control characters -- closed and reopened before a hex digit, so
            # that `\xF` followed by `A` does not read as `\xFA`.
            ("_Z1fIXtlA3_cLc104ELc10EEEEvv", 'void f<"h\\n">()'),
            ("_Z1fIXtlA3_cLc1ELc2EEEEvv", 'void f<"\\1\\2">()'),
            ("_Z1fIXtlA3_cLc127ELc31EEEEvv", 'void f<"\\x7F\\x1F">()'),
            ("_Z1fIXtlA3_cLc15ELc65EEEEvv", 'void f<"\\xF""A">()'),
            ("_Z1fIXtlA3_cLc104ELc34ELc92EEEEvv", 'void f<"h\\"\\\\">()'),
            # A nul inside, and one written out where the array had room for it.
            ("_Z1fIXtlA3_cLc104ELc0ELc105EEEEvv", 'void f<"h\\0i">()'),
            ("_Z1fIXtlA3_cLc104ELc105ELc0EEEEvv", 'void f<"hi\\0">()'),
            ("_Z1fIXtlA3_cEEEvv", 'void f<"">()'),
            # A byte that is not UTF-8 is escaped as the byte it is. This came back
            # `"hÈ"`, a Latin-1 reading of a byte that was never Latin-1.
            ("_Z1fIXtlA3_cLc104ELc200EEEEvv", 'void f<"h\\xC8">()'),
            ("_Z1fIXtlA3_cLc200ELc65EEEEvv", 'void f<"\\xC8""A">()'),
            ("_Z1fIXtlA3_cLc255ELc255EEEEvv", 'void f<"\\xFF\\xFF">()'),
            # Only `char` is a string: the other character types keep the array form.
            ("_Z1fIXtlA3_iLi104ELi105EEEEvv", "void f<int [3]{104, 105}>()"),
            ("_Z1fIXtlA3_wLw104ELw105EEEEvv", "void f<wchar_t [3]{(wchar_t)104, (wchar_t)105}>()"),
            ("_Z1fIXtlA3_hLh104EEEEvv", "void f<unsigned char [3]{(unsigned char)104}>()"),
        ],
    )
    def test_the_spelling(self, mangled, expected):
        assert demangle.demangle(mangled) == expected
        assert demangle.demangle(mangled, style="gnu") == expected


class TestALiteralsValueIsANumber:
    """`L <type> <value number> E`: digits, with `n` in front of a negative value.
    Taking whatever stood before the `E` read `Li4JE` as `4J` and `LinE` as `-`, which
    is what `c++filt` prints and `llvm-cxxfilt` refuses; neither is a number.
    `tools/mutate.py --seed 6`."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fILi4EEvv", "void f<4>()"),
            ("_Z1fILin4EEvv", "void f<-4>()"),
            ("_Z1fILi04EEvv", "void f<04>()"),
            ("_Z1fIL4Enum1EEvv", "void f<(Enum)1>()"),
            ("_Z1fIXtl1Edi1nLi4EEEEvv", "void f<E{.n = 4}>()"),
        ],
    )
    def test_a_number(self, mangled, expected):
        assert demangle.demangle(mangled) == expected

    @pytest.mark.parametrize("mangled", ["_Z1fILi4JEEvv", "_Z1fILinEEvv", "_Z1fIXtl1Edi1nLi4JEEEEvv", "_Z1fILi4xEEvv"])
    def test_anything_else_is_refused(self, mangled):
        assert demangle.demangle(mangled) == mangled

    @pytest.mark.parametrize(
        ("mangled", "expected"), [("_Z1fILb0EEvv", "void f<false>()"), ("_Z1fILb1EEvv", "void f<true>()")]
    )
    def test_a_bool_is_false_or_true(self, mangled, expected):
        assert demangle.demangle(mangled) == expected

    @pytest.mark.parametrize("mangled", ["_Z1fILb6EEvv", "_Z1fILb01EEvv", "_ZN1S1fILb6EEEv1XILUlvE0_EE"])
    def test_any_other_bool_value_is_refused(self, mangled):
        """`Lb6E` was spelled `true`. `llvm-cxxfilt` refuses a bool that is neither `0`
        nor `1`, and no compiler writes one; `c++filt` prints `(bool)6`. Seed 11."""
        assert demangle.demangle(mangled) == mangled


class TestAModifierOverAnEmptyPack:
    """`_Z1fIJEPT_E` writes `P` over `T_`, and `T_` is the empty pack `J E`: there is
    nothing to point to. `c++filt` refuses the name; `llvm-cxxfilt` prints `f<*>`, the
    modifier alone; this printed `f<>`, the argument dropped as an empty pack is
    dropped -- a name with one argument fewer than it has. Found by
    `tools/enumerate.py --length 6`, whose gate-length run never reaches the shape."""

    @pytest.mark.parametrize(
        "mangled",
        [
            "_Z1fIJEPT_E",
            "_Z1fIJERT_E",
            "_Z1fIJEOT_E",
            "_Z1fIJEGT_E",
            "_Z1fIJECT_E",
            "_Z1fIJEKT_E",
            "_Z1fIJEA3_T_E",
            "_Z1fIJEM1AT_E",
            "_Z1fIJEEvPT_",
        ],
    )
    def test_refused(self, mangled):
        assert demangle.demangle(mangled) == mangled
        assert demangle.demangle(mangled, style="gnu") == mangled

    def test_an_expansion_over_an_empty_pack_still_spells_nothing(self):
        assert demangle.demangle("_Z1fIJEEvDpT_") == "void f<>()"
        assert demangle.demangle("_Z1fIJEEvDpPT_") == "void f<>()"

    def test_a_modifier_over_a_pack_with_members_reads_as_before(self):
        assert demangle.demangle("_Z1fIJiEPT_E") == "f<int, int*>"


class TestAQualifiedFunctionTypeReturningAnArray:
    """`KFA_iE` / `FA_iRE` as a parameter. C++ has no function returning an array;
    llvm-cxxfilt writes the qualifier after the `[]`, this before them, `c++filt`
    refuses. The types job already accepts the bare encodings; length six under
    `_Z1f` reaches them as a parameter. Found by `tools/enumerate.py --length 6`,
    twelve unexplained names."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fKFA_iE", "f(int () const [])"),
            ("_Z1fFA_iRE", "f(int () & [])"),
            ("_Z1fFA_iOE", "f(int () && [])"),
        ],
    )
    def test_the_qualifier_stands_before_the_brackets(self, mangled, expected):
        assert demangle.demangle_strict(mangled) == expected


class TestImaginaryOverASubstitutedOrMemberPointerDeclarator:
    """`G` over a substitution that names a function or array, or over a member pointer
    to a function. llvm-cxxfilt drops the `()` / `[]` or leaves a parenthesis unclosed;
    `c++filt` refuses. This keeps the declarator, as it does for the written-out
    `_Z1fGFaE` / `_Z1fGPFvE` shapes the tool already accepts. Found by
    `tools/enumerate.py --length 6` under `_Z1f`, twenty-four unexplained names."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fFiEGS_", "f(int (), int () imaginary)"),
            ("_Z1fA_iGS_", "f(int [], int imaginary [])"),
            ("_Z1fGMiFiE", "f(int (int::* imaginary)())"),
        ],
    )
    def test_the_declarator_is_kept(self, mangled, expected):
        assert demangle.demangle_strict(mangled) == expected
        assert demangle.demangle_strict(mangled, style="gnu") == expected.replace("imaginary", "_Imaginary")


class TestAnAbbreviationBeforeAStructorBehindALinkageMarker:
    """`_ZNSiLD1Ev`: an internal-linkage `L` between the abbreviation and its destructor.
    Nothing writes one there; `llvm-cxxfilt` reads it and spells the scope in full, as
    it does without the marker, and this spelled it short. `c++filt` refuses it.
    `tools/enumerate.py --length 6`, the one unexplained name under `_ZN`."""

    def test_the_scope_is_spelled_in_full(self):
        full = "std::basic_istream<char, std::char_traits<char>>::~basic_istream()"
        assert demangle.demangle("_ZNSiLD1Ev") == full
        assert demangle.demangle("_ZNSiD1Ev") == full
        assert demangle.demangle("_ZNSiLC1Ev") == "std::basic_istream<char, std::char_traits<char>>::basic_istream()"

    def test_a_plain_member_behind_the_marker_keeps_the_short_name(self):
        assert demangle.demangle("_ZNSiL3fooEv") == "std::istream::foo()"


class TestAnExpansionWhosePatternNamesNoPack:
    """`Dp <type>` where the type mentions no pack spells `type...` whatever packs the
    enclosing template has. `ParameterPackExpansion::printLeft` prints the child and,
    finding no pack in it, the dots; keying on the enclosing scope dropped them from
    `_Z1fIJifcEEvDpC1E`, which both references spell `E complex...`.
    `tools/mutate.py --seed 7`."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fIJifcEEvDpC1E", "void f<int, float, char>(E complex...)"),
            ("_Z1fIJifcEEvDpKi", "void f<int, float, char>(int const...)"),
            ("_Z1fIJEEvDp1A", "void f<>(A...)"),
            # A pattern over the pack still expands.
            ("_Z1fIJicEEvDpT_", "void f<int, char>(int, char)"),
            ("_Z1fIJicEEvDpPT_", "void f<int, char>(int*, char*)"),
            ("_Z1fIJicEEvDpPFvT_E", "void f<int, char>(void (*)(int), void (*)(char))"),
            # An inner expansion consumes its pack, and the outer pattern, reaching the
            # pack only through it, takes the dots: `ParameterPackExpansion::printLeft`
            # restores the pack context after itself.
            ("_Z1fIJicEEvDpPFvDpT_E", "void f<int, char>(void (*)(int, char)...)"),
            ("_Z1fIJicEEvDp1AIDpT_E", "void f<int, char>(A<int, char>...)"),
            ("_Z1fIJicEEvPFvDpT_EDpS1_", "void f<int, char>(void (*)(int, char), int, char...)"),
        ],
    )
    def test_the_spelling(self, mangled, expected):
        assert demangle.demangle(mangled) == expected


class TestASpecialisationTakesNoFurtherArguments:
    """`<template-prefix>` names a template, and a name that already carries
    `<template-args>` is not one: `llvm-cxxfilt` refuses `_Z1fN1AIiEIcEE` and, through a
    back reference to the specialisation, `_Z1fN1AIiEENS0_IcEE` -- "can't have a name
    with template args followed by template args" -- where this spelled `A<int><char>`.
    `tools/mutate.py --seed 7`."""

    @pytest.mark.parametrize(
        "mangled",
        [
            "_Z1fN1AIiEIcEE",
            "_Z1fN1AIiEENS0_IcEE",
            "_Z1f1AIiEIcE",
            # Through an unscoped specialisation entered as a type.
            "_ZN4llvm8DenseMapIjSt6vectorIPKKNS_12MachineInstrESaIS4_EENS_12DenseMapInfoIjEENS7_IS6_EEE5clearEv",
        ],
    )
    def test_refused(self, mangled):
        assert demangle.demangle(mangled) == mangled

    def test_an_abbreviation_is_not_a_candidate(self):
        """`Sa`, `Sb` and the rest are substitutions, not `<unscoped-template-name>`s
        the encoder entered: `llvm-cxxfilt` refuses `_ZSbIwEvS_`, where recording
        `std::basic_string` at `S_` read it and shifted every later back reference in
        `_ZSbIwSt11char_traitsIwESaIwEEC1EOS2_`. `St` with a name is one, as before:
        `S_` in `_ZSt4sortIPiEvT_S_IcE` is `std::sort`, the template, which a second
        argument list then specialises -- and bare, with no arguments, it is nothing a
        type can be, so `_ZSt4sortIPiEvT_S_` is refused where it once read
        `(int*, std::sort)`."""
        for mangled in ("_ZSbIwEvS_", "_ZSaIwEvS_", "_ZSt4sortIPiEvT_S_"):
            assert demangle.demangle(mangled) == mangled
        assert demangle.demangle("_Z1fSaIwES_") == "f(std::allocator<wchar_t>, std::allocator<wchar_t>)"
        assert demangle.demangle("_ZSt4sortIPiEvT_S_IcE") == "void std::sort<int*>(int*, std::sort<char>)"
        assert demangle.demangle("_Z1f1AIiES_IcES1_") == "f(A<int>, A<char>, A<char>)"
        assert demangle.demangle("_ZSbIwSt11char_traitsIwESaIwEEC1EOS2_") == (
            "E complex std::basic_string<wchar_t, std::char_traits<wchar_t>, std::allocator<wchar_t>>(E&&)"
        )

    def test_a_pack_referred_to_outside_its_expansion_is_a_three_way_disagreement(self):
        """`_Z1fIJicdEEPPFvDpT_EPFvDpRPS0_ES8_S1_DpS4_S6_` names the pack `T_` through
        `S6_` outside any `Dp`, which no declaration does. `llvm-cxxfilt` prints a pack
        so placed as its first member, `c++filt` as its last, and this as all of them;
        recorded rather than followed, because a pack outside an expansion has no
        declaration to be right about. `tools/mutate.py --seed 7`."""
        spelled = demangle.demangle("_Z1fIJicdEEPPFvDpT_EPFvDpRPS0_ES8_S1_DpS4_S6_")
        assert spelled.endswith("void (**)(int, char, double)..., int*&, char*&, double*&))(int, char, double)")

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fN1AIiEENS_IcEE", "f(A<int>, A<char>)"),
            ("_Z1fN1AIiEE1BIiE", "f(A<int>, B<int>)"),
            ("_ZN1AIiE1fES0_", "A<int>::f(A<int>)"),
            ("_ZNSt6vectorIiSaIiEE9push_backERKi", "std::vector<int, std::allocator<int>>::push_back(int const&)"),
        ],
    )
    def test_a_template_name_still_takes_them(self, mangled, expected):
        assert demangle.demangle(mangled) == expected


class TestAFunctionParameterEndsInAnUnderscore:
    """`fp <top-level CV-qualifiers> [<number>] _`, and `fL <level> p` the same. The
    qualifiers are read and dropped as `parseFunctionParam` drops them, so `fpK_` is
    `fp` -- which this refused -- and the `_` is not optional: `fp` alone read as a
    parameter spelled `decltype(fp == nullptr)` for `DTeqfpLDnEE`, which both
    references refuse. `tools/mutate.py --seed 10`."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fDTfp_E", "f(decltype(fp))"),
            ("_Z1fDTfp1_E", "f(decltype(fp1))"),
            ("_Z1fDTfpK_E", "f(decltype(fp))"),
            ("_Z1fDTfL1p_E", "f(decltype(fp))"),
            ("_Z1fDTfL1pK2_E", "f(decltype(fp2))"),
        ],
    )
    def test_the_spelling(self, mangled, expected):
        assert demangle.demangle(mangled) == expected

    @pytest.mark.parametrize("mangled", ["_Z1fDTfpE", "_Z1fDTfL1pE", "_Z1fIiEDTeqfpLDnEEPT_"])
    def test_refused(self, mangled):
        assert demangle.demangle(mangled) == mangled


class TestARequiresClauseConstrainsArguments:
    """`I <template-arg>+ [Q <constraint>] E`: a list of nothing but a clause is not one.
    `llvm-cxxfilt` refuses `_ZN5test21jIQ4TrueITL0__EEEvz`, which this spelled
    `test2::j<>`. `tools/mutate.py --seed 9`."""

    def test_refused(self):
        assert demangle.demangle("_ZN5test21jIQ4TrueITL0__EEEvz") == "_ZN5test21jIQ4TrueITL0__EEEvz"

    def test_with_an_argument(self):
        assert demangle.demangle("_ZN5test21jIiQ4TrueITL0__EEEvz") == "void test2::j<int>(...)"


class TestTheExplicitObjectMarkerBelongsToTheEntity:
    """`N H <prefix> <unqualified-name> E`: the `H` says the function's first parameter
    is its explicit object parameter -- and only for the entity's own name.
    `parseNestedName` takes it in a type as well and does nothing with it; read in a
    template argument, it put `this` on the parameter list of the function the
    argument belonged to. `tools/mutate.py --seed 9`."""

    def test_in_the_entitys_name(self):
        assert demangle.demangle("_ZNH1A1fERKi") == "A::f(this int const&)"

    def test_in_a_template_argument(self):
        mangled = (
            "_ZN4llvm8DenseMapIjPNS_11ImutAVLTreeINS_16ImutKeyValueInfoIPKN5clang4ento10SymbolDataENS_12ImmutableSet"
            "IPNS_6APSIntENS_17ImutContainerInfoISA_EEEEEEEENS_12DenseMapInfoIjEENHS_ISG_EEE16InsertIntoBucketERKjRKSG_PSt4pair"
        )
        spelled = demangle.demangle(mangled)
        assert spelled.endswith(
            "::InsertIntoBucket(unsigned int const&, llvm::ImutAVLTree<llvm::ImutKeyValueInfo<clang::ento::SymbolData const*, llvm::ImmutableSet<llvm::APSInt*, llvm::ImutContainerInfo<llvm::APSInt*>>>>* const&, std::pair*)"
        )
        assert "this " not in spelled


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


class TestAnAutoBelongsToTheLambdasOwnLevel:
    """A reference that binds nothing is an `auto` only at the lambda's level.

    ABI 5.1.8: a generic lambda's `auto` parameter is mangled as the artificial template
    parameter of the lambda's own list. `T_` inside a lambda that stands on top of an
    enclosing `<template-args>` is level 0, the enclosing list, and where that list has
    no such argument the reference names nothing: `_Z1fIEvDTLUlT_E_EE` asks `f<>` for an
    argument and `_Z1fIiEvDTLUlT0_E_EE` asks `f<int>` for a second. Both read `(auto)`
    here, an `auto` the lambda did not declare; `llvm-cxxfilt` refuses each and spells
    `auto` only for a miss at the lambda's own level, `TL0__` under `f<int>` or `T_`
    where nothing encloses the lambda at all. `tools/mutate.py --seed 4`.
    """

    @pytest.mark.parametrize(
        "mangled",
        [
            "_Z1fIEvDTLUlT_E_EE",
            "_Z1fIiEvDTLUlT0_E_EE",
            "_ZN1AIiE1fIEEvDTLUlTyTtTyTnTL1__ETL0__T_TL0__E_EE",
        ],
    )
    def test_a_miss_at_an_enclosing_level_is_refused(self, mangled):
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled)

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fIiEvDTLUlT_E_EE", "void f<int>(decltype([](int){...}))"),
            ("_Z1fIiEvDTLUlTL0__E_EE", "void f<int>(decltype([](auto){...}))"),
            ("_Z1fIiEvDTLUlTL0_1_E_EE", "void f<int>(decltype([](auto){...}))"),
            ("_Z1fiDTLUlT_E_EE", "f(int, decltype([](auto){...}))"),
            (
                "_ZN1AIiE1fIcEEvDTLUlTyTtTyTnTL1__ETL0_1_T_TL0__E_EE",
                "void A<int>::f<char>(decltype([]<typename $T, template<typename $T0, $T0 $N> typename $TT>"
                "(auto, char, $T){...}))",
            ),
        ],
    )
    def test_a_miss_at_the_lambdas_level_is_an_auto(self, mangled, expected):
        assert demangle.demangle(mangled) == expected

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # g++ 13 for `template <class A, class T> void h5(T, T)` and `h6(T, T)`, given
            # `[](auto x) { return x; }` from `use3()`: the closure's second mention is
            # `S2_` under `h5<int, ...>` and `S0_` under `h6<...>` -- the entry made for the
            # lambda's `T_`, which resolves against the enclosing arguments where it is
            # read, and which `llvm-cxxfilt` spells `auto`.
            (
                "_Z2h5IiZ4use3vEUlT_E_EvT0_S2_",
                "void h5<int, use3()::'lambda'(auto)>(use3()::'lambda'(auto), use3()::'lambda'(auto))",
            ),
            (
                "_Z2h6IZ4use3vEUlT_E_EvS0_S0_",
                "void h6<use3()::'lambda'(auto)>(use3()::'lambda'(auto), use3()::'lambda'(auto))",
            ),
            (
                "_Z2h5I1QZ4use3vEUlS0_T_E_EvT0_S3_",
                "void h5<Q, use3()::'lambda'(Q, auto)>(use3()::'lambda'(Q, auto), use3()::'lambda'(Q, auto))",
            ),
        ],
    )
    def test_what_the_compiler_writes_still_reads(self, mangled, expected):
        assert demangle.demangle(mangled) == expected


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


class TestTheNameAConstructorRepeats:
    """A constructor spells its class, and the class name was being cut short.

    `Foo<int>::Foo` is right -- a constructor drops the template arguments and the ABI
    tags the class name carries -- and the cut was made by searching the *spelling* for
    the first `<` or `[`. Every class whose name is an operator has one of those inside
    it, so `_ZNssC1Ev` came back as `operator<=>::operator()`: a constructor of a class
    the encoding does not mention. Expectations are `llvm-cxxfilt` 18.1.3's; GNU
    `c++filt` 2.42 refuses most of these and reads `_ZN1XixC1Ev` as `X::operator[]::X()`,
    naming a class that is not the one in scope.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_ZNssC1Ev", "operator<=>::operator<=>()"),
            ("_ZNssD1Ev", "operator<=>::~operator<=>()"),
            ("_ZNltC1Ev", "operator<::operator<()"),
            ("_ZNlsD1Ev", "operator<<::~operator<<()"),
            ("_ZN1XixC1Ev", "X::operator[]::operator[]()"),
            ("_ZNixC2Ev", "operator[]::operator[]()"),
        ],
    )
    def test_an_operator_named_class_keeps_its_whole_name(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="itanium") == expected

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # What the cut was there for, and still does.
            ("_ZN3FooIiEC1Ev", "Foo<int>::Foo()"),
            ("_ZN3FooIiED1Ev", "Foo<int>::~Foo()"),
            ("_ZN3FooB3abcC1Ev", "Foo[abi:abc]::Foo()"),
            ("_ZN3FooB3abcB3defC1Ev", "Foo[abi:abc][abi:def]::Foo()"),
            ("_ZNSaIcEC1Ev", "std::allocator<char>::allocator()"),
            ("_ZNSaC1Ev", "std::allocator::allocator()"),
            ("_ZN1A1BIiEC1Ev", "A::B<int>::B()"),
        ],
    )
    def test_the_arguments_and_the_tags_still_come_off(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="itanium") == expected

    def test_the_class_is_read_before_an_inherited_base(self):
        """`CI <variant> <base>`: the base is a type and may be a nested name itself.

        Read in the other order it leaves the base's own last component standing where
        the class should be. GNU `c++filt` 2.42 does exactly that and answers `A::C()`;
        `llvm-cxxfilt` 18.1.3 answers `A::A()`, which is the class the encoding names.
        """
        assert demangle.demangle_strict("_ZN1ACI1N1B1CEEv", language="itanium") == "A::A()"

    def test_a_nested_name_in_a_template_argument_does_not_become_the_class(self):
        assert demangle.demangle_strict("_ZN1AIN1B1CEEC1Ev", language="itanium") == "A<B::C>::A()"

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_ZNv13fooC1Ev", "operator foo::()"),
            ("_ZNv13fooD1Ev", "operator foo::~()"),
        ],
    )
    def test_a_vendor_extended_operator_has_no_name_to_repeat(self, mangled, expected):
        """`v <digit> <source-name>` as the class, which no compiler writes.

        `llvm-cxxfilt` 18.1.3 writes no name at all -- `operator foo::()` and
        `operator foo::~()` -- because it holds a vendor extended operator in the same
        node as a conversion operator, and that node has no base name to repeat. GNU
        `c++filt` 2.42 drops the `operator` and writes `operator foo::foo()`. This
        followed neither and repeated the name in full, a third reading of a name that
        has no declaration; it follows `llvm-cxxfilt` now, as the conversion operator
        does -- see `TestAConversionOperatorHasNoNameToRepeat`."""
        assert demangle.demangle_strict(mangled, language="itanium") == expected


class TestAVendorExpressionsArgumentIsACallsArgument:
    """`<expression> ::= u <source-name> <template-arg>* E`, spelled as a call. An
    `X <expression> E` argument was spelled as it is inside `<...>`, where a `>>` or a
    `>` is bracketed so it cannot close the list, and `__uuidof((HasMember >> member))`
    came out for Clang's own `_Z15test_uuidofExprI9HasMemberEvDTu8__uuidofXrsT_6memberEEE`.
    A call's argument needs no such bracket and `llvm-cxxfilt` writes none. Seed 11."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            (
                "_Z15test_uuidofExprI9HasMemberEvDTu8__uuidofXrsT_6memberEEE",
                "void test_uuidofExpr<HasMember>(decltype(__uuidof(HasMember >> member)))",
            ),
            ("_Z1fIiEDTu3fooXrsT_6memberEEEv", "decltype(foo(int >> member)) f<int>()"),
            ("_Z1fDTu3fooXplLi1ELi2EEEE", "f(decltype(foo(1 + 2)))"),
            ("_Z1fDTu3fooXfp_EEE", "f(decltype(foo(fp)))"),
            ("_Z1fDTu3fooXLi1EEEE", "f(decltype(foo(1)))"),
            ("_Z1fDTu8__uuidofXplLi1ELi2EEEE", "f(decltype(__uuidof(1 + 2)))"),
        ],
    )
    def test_the_spelling(self, mangled, expected):
        assert demangle.demangle(mangled) == expected

    def test_the_gnu_style_brackets_every_operand_as_it_always_has(self):
        assert (
            demangle.demangle("_Z15test_uuidofExprI9HasMemberEvDTu8__uuidofXrsT_6memberEEE", style="gnu")
            == "void test_uuidofExpr<HasMember>(decltype (__uuidof((HasMember)>>member)))"
        )

    def test_an_unterminated_argument_is_refused(self):
        assert demangle.demangle("_Z1fDTu3fooXiEE") == "_Z1fDTu3fooXiEE"


class TestAConversionOperatorHasNoNameToRepeat:
    """`CtorDtorName` prints the scope's `getBaseName()`, which only a source name, an
    ordinary operator and a nested or tagged name over one define. A conversion
    operator, a vendor or literal operator, a constructor or destructor, a closure, an
    unnamed type and a structured binding leave it empty, so `llvm-cxxfilt` spells
    `_ZN1AcviD0Ev` as `A::operator int::~()` and `_ZN1AD1IiED0Ev` as `A::~A<int>::~()`.
    No compiler writes one, and repeating the name in full was a third reading beside
    the two references' -- `c++filt` writes the type's own name. `tools/mutate.py
    --seed 2` and `--seed 8`."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_ZN1AcviD0Ev", "A::operator int::~()"),
            ("_ZN1AcviC2Ev", "A::operator int::()"),
            ("_ZN1AcviIiED0Ev", "A::operator int<int>::~()"),
            ("_ZNStcvtD1Ev", "std::operator unsigned short::~()"),
            ("_ZN1AD1IiED0Ev", "A::~A<int>::~()"),
            ("_ZN1AC1IiEC1Ev", "A::A<int>::()"),
            ("_ZNUlvE_C1Ev", "'lambda'()::()"),
            ("_ZN1AUlvE_D1Ev", "A::'lambda'()::~()"),
            ("_ZNUt_C1Ev", "'unnamed'::()"),
            ("_ZNDC1a1bEC1Ev", "[a, b]::()"),
            ("_ZNli3_kmC1Ev", 'operator"" _km::()'),
            # Any other operator name, and a class, are repeated in full, as before.
            ("_ZNplD0Ev", "operator+::~operator+()"),
            ("_ZNplC1Ev", "operator+::operator+()"),
            ("_ZN1A1BC1Ev", "A::B::B()"),
            ("_ZNSaIcED1Ev", "std::allocator<char>::~allocator()"),
        ],
    )
    def test_the_spelling(self, mangled, expected):
        assert demangle.demangle(mangled) == expected


class TestAFriendDeclaredInsideItsClass:
    """`<unqualified-name> ::= F <name>`, and the marker was being dropped.

    It was read and spelled for a source name and an operator, and read and silently
    discarded for a constructor, a destructor and an unnamed type -- so `_ZN1AFC1Ev` came
    back as `A::A()`, which is a different declaration from the one the encoding spells.
    The two references put the marker in different places, so both spellings are pinned:
    `llvm-cxxfilt` 18.1.3 writes the word before the name and GNU `c++filt` 2.42 writes a
    bracketed suffix, after the ABI tags and before the template arguments.
    """

    @pytest.mark.parametrize(
        ("mangled", "llvm", "gnu"),
        [
            ("_ZN1AF1fEv", "A::friend f()", "A::f[friend]()"),
            ("_ZN1AFC1Ev", "A::friend A()", "A::A[friend]()"),
            ("_ZN1AFD1Ev", "A::friend ~A()", "A::~A[friend]()"),
            ("_ZN1AFUt_Ev", "A::friend 'unnamed'()", "A::{unnamed type#1}[friend]()"),
            ("_ZN1AFltERKS_", "A::friend operator<(A const&)", "A::operator<[friend](A const&)"),
            ("_ZN1AFDC1a1bEEv", "A::friend [a, b]()", "A::[a, b][friend]()"),
            ("_ZN1AFL1fEv", "A::friend f()", "A::f[friend]()"),
            ("_ZN1AF1fB3xyzEv", "A::friend f[abi:xyz]()", "A::f[abi:xyz][friend]()"),
            ("_ZN1AF1fIiEEvv", "void A::friend f<int>()", "void A::f[friend]<int>()"),
        ],
    )
    def test_both_references_are_pinned(self, mangled, llvm, gnu):
        assert demangle.demangle_strict(mangled, language="itanium", style="llvm") == llvm
        assert demangle.demangle_strict(mangled, language="itanium", style="gnu") == gnu

    def test_the_marker_needs_a_class_to_be_a_friend_of(self):
        """`F` outside a nested name is a type letter, not a friend marker."""
        assert demangle.demangle_strict("_Z1fFvvE", language="itanium") == "f(void ())"

    @pytest.mark.parametrize("mangled", ["_ZN1ALF3fooEv", "_ZN5cluleInfoELFD0Ev", "_ZN1ALFC1Ev"])
    def test_the_marker_goes_before_the_internal_linkage_one(self, mangled):
        """`parseUnqualifiedName` consumes `F` and then `L`, in that order, and
        `llvm-cxxfilt` 18 and 20 refuse `LF` where `FL` reads. Reading the marker after
        `L` spelled `A::friend foo()` for a name neither reference reads.
        `tools/mutate.py --seed 15` and `--seed 16`."""
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="itanium")


class TestAnObjectiveCMethodAsALocalScope:
    """`Z <n>-[Class selector:]E` -- a C++ template instantiated inside an ObjC method.

    Clang emits these. Every *shipped* reference refuses the shape wholesale:
    `llvm-cxxfilt` 18.1.3 and 20.1.2 and GNU `c++filt` 2.42 hand back every one unread.
    libcxxabi's own `DemangleTestCases.inc` carries two of them with the answer recorded,
    and this library matches both -- so the file the reference is tested against says the
    reading is right and the binaries built from it are behind it. That is the reason
    `tools/enumerate.py` accepts a mutant of the shape rather than reporting it: a
    refusal that covers the whole family says nothing about any member of it.
    """

    LOCAL_SCOPE = "_ZZ10+[Foo bar]E3Baz"

    def test_the_short_one_reads(self):
        assert demangle.demangle_strict(self.LOCAL_SCOPE, language="itanium") == "+[Foo bar]::Baz"

    def test_both_shipped_references_refuse_what_libcxxabi_records(self):
        """Not a claim about the references' source -- about the binaries that ship."""
        import shutil
        import subprocess

        for tool in ("llvm-cxxfilt", "c++filt"):
            if shutil.which(tool) is None:
                pytest.skip(f"{tool} is not installed")
            answer = subprocess.run(
                [tool, "--no-strip-underscore"], input=self.LOCAL_SCOPE + "\n", capture_output=True, text=True
            ).stdout.strip()
            assert answer == self.LOCAL_SCOPE, f"{tool} now reads it; the accept rule needs re-examining"


class TestADeclarationInsideAnArgumentListQualifiesAnArgument:
    """`<template-arg> ::= <template-param-decl> <template-arg>`: `parseTemplateArg`
    reads the declaration and then an argument as one `TemplateParamQualifiedArg`. A
    list ending on the declaration, `ITyE`, is refused by `llvm-cxxfilt` 18 and 20 where
    this read `unary<>` -- and `Str<>` for a `cv` inside a decltype. `tools/mutate.py
    --seed 20`."""

    @pytest.mark.parametrize(
        "mangled", ["_Z1fIJifcEEvDp5unaryITyE", "_Z1fIcEvDTcv3StrITyELA6_KcEE", "_Z1f5unaryITk4TrueE"]
    )
    def test_a_list_ending_on_a_declaration_is_refused(self, mangled):
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="itanium")

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [("_Z1f5unaryITyiE", "f(unary<int>)"), ("_Z1f5unaryITk4TrueiE", "f(unary<int>)")],
    )
    def test_a_qualified_argument_still_reads(self, mangled, expected):
        assert demangle.demangle(mangled) == expected


class TestARequiresClauseHasNoPlaceInsideANestedName:
    """The grammar puts a requires-clause in two places: an argument list's
    `I ... Q <constraint> E` and an encoding's, after the parameters. A <nested-name>
    has none, and `llvm-cxxfilt` refuses `_ZN4llvm12_GLOBAL__N_1L1UQ13_SuperRegsSetE`
    where this read the clause between two components, threw it away, and answered
    `llvm::(anonymous namespace)::U`. `tools/mutate.py --seed 20`."""

    def test_the_clause_between_two_components_is_refused(self):
        with pytest.raises(DemanglingError):
            demangle.demangle_strict("_ZN4llvm12_GLOBAL__N_1L1UQ13_SuperRegsSetE", language="itanium")

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_ZN5test21jIvQ4TrueITL0__EEEvz", "void test2::j<void>(...)"),
            ("_ZN5test21hIvEEvzQ4TrueITL0__E", "void test2::h<void>(...) requires True<TL0_>"),
        ],
    )
    def test_the_two_places_it_belongs_still_read(self, mangled, expected):
        assert demangle.demangle(mangled) == expected


class TestTheOldFormOfSrThatGccStillWrites:
    """`sr <type> <unqualified-name>`, the production the ABI had before the
    <unresolved-name> forms, with a complete type where the modern grammar allows only
    a parameter, a decltype or a substitution. g++ still writes it, and libstdc++ ships
    it in `std::__copy_move_a1`'s return type -- `srSt23__is_random_access_iterIT0_...E
    7__valueE` -- sixteen symbols on one Ubuntu 24.04 machine. libiberty's
    `d_expression_1` reads `sr` that way, `cplus_demangle_type` and then
    `d_unqualified_name`; `llvm-cxxfilt` 18 and 20 refuse every one of them. `St`
    followed by a name has no reading under the modern grammar, so the old one is the
    only reading it can have, and the substitution table comes out as libiberty's: the
    sixteen match `c++filt` byte for byte, back references included."""

    NAME = (
        "_ZSt14__copy_move_a1ILb0EPiiEN9__gnu_cxx11__enable_ifIXsrSt23__is_random_access_iterIT0_"
        "NSt15iterator_traitsIS4_E17iterator_categoryEE7__valueESt15_Deque_iteratorIT1_RSA_PSA_EE6__typeES4_S4_SD_"
    )

    def test_the_gnu_spelling_is_cxxfilts(self):
        assert demangle.demangle(self.NAME, style="gnu") == (
            "__gnu_cxx::__enable_if<std::__is_random_access_iter<int*, std::iterator_traits<int*>::iterator_category>"
            "::__value, std::_Deque_iterator<int, int&, int*> >::__type std::__copy_move_a1<false, int*, int>"
            "(int*, int*, std::_Deque_iterator<int, int&, int*>)"
        )

    def test_the_llvm_spelling_follows(self):
        assert demangle.demangle(self.NAME) == (
            "__gnu_cxx::__enable_if<std::__is_random_access_iter<int*, std::iterator_traits<int*>::iterator_category>"
            "::__value, std::_Deque_iterator<int, int&, int*>>::__type std::__copy_move_a1<false, int*, int>"
            "(int*, int*, std::_Deque_iterator<int, int&, int*>)"
        )

    def test_a_reduced_shape(self):
        assert demangle.demangle("_Z1fIiEvDTsrSt1AIT_E5valueE") == "void f<int>(decltype(std::A<int>::value))"
        assert demangle.demangle("_Z1fIiEvDTsrSt1AIT_E5valueIcEE") == "void f<int>(decltype(std::A<int>::value<char>))"

    def test_the_modern_forms_are_unchanged(self):
        assert demangle.demangle("_Z1fIiEvDTsrT_5valueE") == "void f<int>(decltype(int::value))"
        assert demangle.demangle("_Z1fIiEvDTsrNT_1BE5valueE") == "void f<int>(decltype(int::B::value))"


class TestTheOldFormOfSrWithAPlainClass:
    """g++ 13 writes the old form for every member of a class that is not itself
    dependent: `decltype(A::baz<T> + t)` is `sr 1A 3baz IT_E`, where Clang writes
    `sr 1A E 3baz IT_E` with the modern grammar's `E`. The letters are ambiguous -- under
    the modern grammar they open a list of qualifier levels -- and the modern reading
    can run on past the `sr` before anything refuses it, so this reads as libiberty's
    `d_unresolved_name` does: the modern way first, and the whole name again the old way
    when that fails. Every name here was compiled with g++ 13 and read through
    `c++filt` 2.42; `llvm-cxxfilt` 18 and 20 refuse all of them. The numbering is the
    compiler's: the type records itself and its arguments record theirs, which is why
    `S1_` after `sr1A3bazIT_E` is `int` and not `A`."""

    @pytest.mark.parametrize(
        "mangled, expected",
        [
            # `decltype(A::baz<T> + t)`, with `A` written again as `S0_` and `int` as `S1_`.
            ("_Z1kIiEDTplsr1A3bazIT_Efp_ES1_S0_Pi", "decltype(A::baz<int> + fp) k<int>(int, A, int*)"),
            # The modern reading takes the decltype's `E` as the end of the levels and
            # fails on the `S1_` after it, not on the `sr`.
            ("_Z2k6IiEDtsr1A3bazIPT_EES1_", "decltype(A::baz<int*>) k6<int>(int)"),
            ("_Z3f13IiEDTadsr1A3bazIT_EES1_", "decltype(&A::baz<int>) f13<int>(int)"),
            # `t.A::v`: a member access whose member is written the old way.
            ("_Z3f11I1DEDtdtfp_sr1A1vET_", "decltype(fp.A::v) f11<D>(D)"),
            # The nested-name shape, a class holding a dependent member: the scope is one
            # type, recorded level by level, so `S3_` is `int` and `S4_` is `C<int>`.
            ("_Z2j4IiEDTplsrN1A1B1CIT_EE1wfp_ES3_S4_", "decltype(A::B::C<int>::w + fp) j4<int>(int, A::B::C<int>)"),
            ("_Z2h4IiEDTplsrNSt2myIiE2inIT_EE1wfp_ES3_", "decltype(std::my<int>::in<int>::w + fp) h4<int>(int)"),
            # Two old-form names in one, the second through a substitution.
            ("_Z2k2IiEDTplsr1A3bazIT_EsrS0_3bazIS1_EES1_", "decltype(A::baz<int> + A::baz<int>) k2<int>(int)"),
        ],
    )
    def test_the_gcc_names_read(self, mangled, expected):
        assert demangle.demangle_strict(mangled) == expected

    def test_the_gnu_style_reads_them_as_cxxfilt(self):
        assert demangle.demangle_strict("_Z1kIiEDTplsr1A3bazIT_Efp_ES1_S0_Pi", style="gnu") == (
            "decltype ((A::baz<int>)+{parm#1}) k<int>(int, A, int*)"
        )
        assert (
            demangle.demangle_strict("_Z2k6IiEDtsr1A3bazIPT_EES1_", style="gnu")
            == "decltype (A::baz<int*>) k6<int>(int)"
        )

    def test_the_modern_reading_comes_first(self):
        # `sr 1A 3baz E 1v` reads as qualifier levels, which record nothing: `S0_` is
        # the decltype, as it is to both references.
        assert demangle.demangle_strict("_Z1fIiEDTsr1A3bazE1vES0_") == "decltype(A::baz::v) f<int>(decltype(A::baz::v))"
        # Clang's form of the same member is unchanged.
        assert demangle.demangle_strict("_Z1kIiEDTplsr1AE3bazIT_Efp_ES0_1APi") == (
            "decltype(A::baz<int> + fp) k<int>(int, A, int*)"
        )

    def test_a_name_neither_reading_takes_reports_the_first(self):
        with pytest.raises(demangle.ParseError, match="expected a number at offset 23"):
            demangle.demangle_strict("_Z1kIiEDTplsr1A3bazIT_Efp_ES9_")


class TestAPackExpansionInAnExpression:
    """`sp <expression>` under the rule `Dp` reads a type pattern by. A pattern that
    names a pack -- a `T_` bound to one -- is read once per member, `sp sc T_ fp_` over
    `{int, char}` being `static_cast<int>(fp), static_cast<char>(fp)`; a pattern that
    names none is `x...` whatever the scope holds, which is how a function parameter
    pack is written, `decltype(g(t...))` being `cl 1g sp fp_ E`. This tested the scope
    for a pack instead of the pattern and, finding one, spelled the pattern once as it
    stood: the dots gone from every `g(fp...)`, and `static_cast<int, char>(fp)` for the
    other. Every name here was compiled with g++ 13 and Clang 18 from the source in
    the comment, and both references print the expected spelling."""

    @pytest.mark.parametrize(
        "mangled, expected",
        [
            # decltype(g(t...))
            ("_Z2f1IJicEEDTcl1gspfp_EEDpT_", "decltype(g(fp...)) f1<int, char>(int, char)"),
            ("_Z2f1IJEEDTcl1gspfp_EEDpT_", "decltype(g(fp...)) f1<>()"),
            # decltype(g((t + 1)...)), decltype(g(sizeof(t)...)), decltype(g(&t...))
            ("_Z2f2IJicEEDTcl1gspplfp_Li1EEEDpT_", "decltype(g(fp + 1...)) f2<int, char>(int, char)"),
            ("_Z2f3IJicEEDTcl1gspszfp_EEDpT_", "decltype(g(sizeof (fp)...)) f3<int, char>(int, char)"),
            ("_Z2f8IJicEEDTcl1gspadfp_EEDpT_", "decltype(g(&fp...)) f8<int, char>(int, char)"),
            # decltype(g(static_cast<T>(t)...)), decltype(g(T(t)...)), decltype(g(T{}...)),
            # decltype(g(sizeof(T)...)): the pattern names the pack and expands.
            (
                "_Z2f4IJicEEDTcl1gspscT_fp_EEDpS0_",
                "decltype(g(static_cast<int>(fp), static_cast<char>(fp))) f4<int, char>(int, char)",
            ),
            ("_Z2f5IJicEEDTcl1gspcvT_fp_EEDpS0_", "decltype(g((int)(fp), (char)(fp))) f5<int, char>(int, char)"),
            ("_Z2f6IJicEEDTcl1gsptlT_EEEDpS0_", "decltype(g(int{}, char{})) f6<int, char>(int, char)"),
            ("_Z2f7IJicEEDTcl1gspstT_EEDpS0_", "decltype(g(sizeof (int), sizeof (char))) f7<int, char>(int, char)"),
            # An expansion among other arguments, two packs in one pattern, and an
            # expansion over an empty pack, which is no argument at all.
            (
                "_Z1fIJicEEDTcl1gspscT_fp_Li1EEEDpT_",
                "decltype(g(static_cast<int>(fp), static_cast<char>(fp), 1)) f<int, char>(int, char)",
            ),
            (
                "_Z1fIJicEJfdEEDTcl1gspscT_T0_EEDpT_DpT0_",
                "decltype(g(static_cast<int>(float), static_cast<char>(double))) "
                "f<int, char, float, double>(int, char, float, double)",
            ),
            ("_Z1fIJEEDTcl1gspscT_fp_EEDpT_", "decltype(g()) f<>()"),
            # The same shapes in a conversion, a braced initialiser and a member access.
            ("_Z3q67IJiEEDTcv1Aspfp_EDpT_", "decltype((A)(fp...)) q67<int>(int)"),
            ("_Z3q68IJiEEDTtl1Aspfp_EEDpT_", "decltype(A{fp...}) q68<int>(int)"),
            ("_Z1fIJicEEDTcl1gspdtfp_1mEEDpT_", "decltype(g(fp.m...)) f<int, char>(int, char)"),
            ("_Z1fIJicEEDTcl1gspsrT_1xEEDpT_", "decltype(g(int::x, char::x)) f<int, char>(int, char)"),
        ],
    )
    def test_the_llvm_spelling(self, mangled, expected):
        assert demangle.demangle_strict(mangled) == expected

    def test_a_scope_with_no_pack_is_unchanged(self):
        assert demangle.demangle_strict("_Z1fIiEDTcl1gspfp_EET_") == "decltype(g(fp...)) f<int>(int)"
        assert (
            demangle.demangle_strict("_Z1fIiEDTcl1gspscT_fp_EET_") == "decltype(g(static_cast<int>(fp)...)) f<int>(int)"
        )


class TestANewExpressionsInitialiser:
    """`new T{}` and `new T{t}` are written by both compilers as `il <expression>* E`
    after the type, with no `E` of the new-expression's own -- a form the ABI grammar
    does not have, libiberty reads, and `llvm-cxxfilt` 18 and 20 refuse. `new T()` is
    `pi E`, an empty parenthesised initialiser, which `llvm-cxxfilt` reads and prints as
    `new int`: the other expression, the one that leaves the object indeterminate. See
    `tests/conformance/itanium-reference-defects.txt`."""

    @pytest.mark.parametrize(
        "mangled", ["_Z1fIiEvDTnw_icvi_EEE", "_ZN5Casts5auto_IiEEvDTnw_DpicvT__EEE", "_Z1fIiEvDTnw_iLi1EEE"]
    )
    def test_nothing_else_stands_where_the_initialiser_does(self, mangled):
        """`pi`, the braced form, or the `E` that closes a new-expression with none:
        libiberty's `d_expression` and LLVM's `parseNewExpr` take nothing else, and this
        read any expression there, answering `new int((int)())` for `nw_icvi_E`.
        `tools/mutate.py --count 200000`."""
        assert demangle.demangle(mangled) == mangled
        assert demangle.demangle(mangled, style="gnu") == mangled

    @pytest.mark.parametrize(
        "mangled, llvm, gnu",
        [
            ("_Z2n4IiEDTnw_T_ilEES0_", "decltype(new int{}) n4<int>(int)", "decltype (new int{}) n4<int>(int)"),
            (
                "_Z2n5IiEDTnw_T_ilfp_EES0_",
                "decltype(new int{fp}) n5<int>(int)",
                "decltype (new int{{parm#1}}) n5<int>(int)",
            ),
            (
                "_Z1fIiEDTnw_T_ilfp_fp_EES0_",
                "decltype(new int{fp, fp}) f<int>(int)",
                "decltype (new int{{parm#1}, {parm#1}}) f<int>(int)",
            ),
            (
                "_Z10value_initIiEDTnw_T_piEES0_",
                "decltype(new int()) value_init<int>(int)",
                "decltype (new int()) value_init<int>(int)",
            ),
        ],
    )
    def test_both_styles(self, mangled, llvm, gnu):
        assert demangle.demangle_strict(mangled) == llvm
        assert demangle.demangle_strict(mangled, style="gnu") == gnu


class TestAGreaterThanInsideATemplateArgumentList:
    """`BinaryExpr::printLeft` wraps a `>` or `>>` that stands inside a template
    argument list with no bracket yet opened round it, so it cannot be read as the end
    of the list: `(1 > 0) && true`, `(1 >> 2) == 3`, `1 ? (2 > 3) : 4`. This wrapped
    one only at the top of the argument, so `enable_if<(N > 0) && C>` came out
    `N > 0 && C`. Every bracket a construct opens ends the rule inside it -- a call's
    arguments, a cast, `sizeof`, the operand brackets an operator earns -- and braces
    do not. Every spelling here is `llvm-cxxfilt` 18's, and 20 agrees."""

    @pytest.mark.parametrize(
        "mangled, expected",
        [
            ("_Z1fIXaagtLi1ELi0ELb1EEEvv", "void f<(1 > 0) && true>()"),
            ("_Z1fIXaagtLi1ELi0EgtLi2ELi1EEEvv", "void f<(1 > 0) && (2 > 1)>()"),
            ("_Z1fIXoogtLi1ELi2EgtLi3ELi4EEEvv", "void f<(1 > 2) || (3 > 4)>()"),
            ("_Z1fIXeqrsLi1ELi2ELi3EEEvv", "void f<(1 >> 2) == 3>()"),
            ("_Z3t11IiEv2BoIXeqrsstT_Li1ELi2EEE", "void t11<int>(Bo<(sizeof (int) >> 1) == 2>)"),
            ("_Z1fIXquLi1EgtLi2ELi3ELi4EEEvv", "void f<1 ? (2 > 3) : 4>()"),
            ("_Z1fIXqugtLi1ELi2ELi3ELi4EEEvv", "void f<(1 > 2) ? 3 : 4>()"),
            ("_Z1fIXaSLi1EgtLi2ELi3EEEvv", "void f<1 = (2 > 3)>()"),
            ("_Z1fIXtwgtLi1ELi2EEEvv", "void f<throw (1 > 2)>()"),
            ("_Z1fIXspgtLi1ELi2EEEvv", "void f<(1 > 2)...>()"),
            ("_Z1fIXdlgtLi1ELi2EEEvv", "void f<delete (1 > 2)>()"),
            # Braces are not a bracket.
            ("_Z1fIXtl1AgtLi1ELi2EEEEvv", "void f<A{(1 > 2)}>()"),
            ("_Z1fIXilgtLi1ELi2EEEEvv", "void f<{(1 > 2)}>()"),
            # The operand brackets an operator earns are, so the `>` is not wrapped
            # again inside them, and a `>` that is itself an operand wraps once.
            ("_Z1fIXntaagtLi1ELi0ELi2EEEvv", "void f<!(1 > 0 && 2)>()"),
            ("_Z1fIXmlaagtLi1ELi2ELi3ELi4EEEvv", "void f<(1 > 2 && 3) * 4>()"),
            ("_Z1fIXplcmgtLi1ELi2ELi3ELi4EEEvv", "void f<(1 > 2, 3) + 4>()"),
            ("_Z1fIXplgtLi1ELi2ELi3EEEvv", "void f<(1 > 2) + 3>()"),
            ("_Z1fIXmlgtLi1ELi2ELi3EEEvv", "void f<(1 > 2) * 3>()"),
            ("_Z1fIXgtgtLi1ELi2ELi3EEEvv", "void f<(1 > 2 > 3)>()"),
            ("_Z1fIXcmgtLi1ELi2ELi3EEEvv", "void f<(1 > 2, 3)>()"),
            # So are the brackets of a call, a cast, `sizeof`, `noexcept`, a subscript,
            # a new-expression, a fold and a decltype.
            ("_Z1fIiEv1IIXcl1ggtLi1ELi2EEEE", "void f<int>(I<g(1 > 2)>)"),
            ("_Z1fIXcvigtLi1ELi2EEEvv", "void f<(int)(1 > 2)>()"),
            ("_Z1fIXscigtLi1ELi2EEEvv", "void f<static_cast<int>(1 > 2)>()"),
            ("_Z1fIXszgtLi1ELi2EEEvv", "void f<sizeof (1 > 2)>()"),
            ("_Z1fIXnxgtLi1ELi2EEEvv", "void f<noexcept (1 > 2)>()"),
            ("_Z1fIXixLi1EgtLi1ELi2EEEvv", "void f<1[1 > 2]>()"),
            ("_Z1fIXnw_ipigtLi1ELi2EEEEvv", "void f<new int(1 > 2)>()"),
            ("_Z1fIXnwgtLi1ELi2E_iEEEvv", "void f<new(1 > 2) int>()"),
            ("_Z1fIXfLplgtLi1ELi2ELi3EEEvv", "void f<((1 > 2) + ... + (3...))>()"),
            ("_Z1fIXcvDtgtLi1ELi2EELi3EEEvv", "void f<(decltype(1 > 2))(3)>()"),
            # A list nested inside the expression is a list of its own.
            ("_Z1fIiEv1IIXsrT_IXgtLi1ELi2EEE1vEE", "void f<int>(I<int<(1 > 2)>::v>)"),
        ],
    )
    def test_the_llvm_spelling(self, mangled, expected):
        assert demangle.demangle_strict(mangled) == expected

    def test_outside_a_template_argument_list_nothing_changes(self):
        assert demangle.demangle_strict("_Z1fIiEDTgtfp_Li1EET_") == "decltype(fp > 1) f<int>(int)"
        assert demangle.demangle_strict("_Z1fIiEDTaagtfp_Li1ELb1EET_") == "decltype(fp > 1 && true) f<int>(int)"

    def test_the_gnu_spelling_is_unchanged(self):
        assert demangle.demangle_strict("_Z1fIXaagtLi1ELi0ELb1EEEvv", style="gnu") == "void f<(((1)>(0)))&&(true)>()"
        assert demangle.demangle_strict("_Z1fIXixLi1EgtLi1ELi2EEEvv", style="gnu") == "void f<(1)[((1)>(2))]>()"


class TestSizeofNoexceptAndDeleteAreUnaryOperands:
    """`sizeof`, `alignof`, `noexcept`, `new` and `delete` are unary to llvm-cxxfilt's
    printer, and an operand position as tight brackets them: `!(sizeof (int))` and
    `(sizeof (int)).m`, where `typeid` is postfix and stands bare. This left them
    primary, `!sizeof (int)`. `_Z3t18IiEv1IIXntstT_EE` was compiled with Clang 18 from
    `I<!sizeof(T)>`."""

    @pytest.mark.parametrize(
        "mangled, expected",
        [
            ("_Z3t18IiEv1IIXntstT_EE", "void t18<int>(I<!(sizeof (int))>)"),
            ("_Z1fIiEDTntstT_ET_", "decltype(!(sizeof (int))) f<int>(int)"),
            ("_Z1fIiEDTdtstT_1mET_", "decltype((sizeof (int)).m) f<int>(int)"),
            ("_Z1fIiEDTplntstT_Li1EET_", "decltype(!(sizeof (int)) + 1) f<int>(int)"),
            ("_Z1fIiEDTntnxfp_ET_", "decltype(!(noexcept (fp))) f<int>(int)"),
            ("_Z1fIiEDTdtnxfp_1mET_", "decltype((noexcept (fp)).m) f<int>(int)"),
            ("_Z1fIiEDTntazfp_ET_", "decltype(!(alignof (fp))) f<int>(int)"),
            ("_Z1fIiEDTdtdlfp_1mET_", "decltype((delete fp).m) f<int>(int)"),
            # Where nothing binds as tight, nothing changes.
            ("_Z1fIiEDTplstT_Li1EET_", "decltype(sizeof (int) + 1) f<int>(int)"),
            ("_Z1fIiEDTmlstT_stT_ET_", "decltype(sizeof (int) * sizeof (int)) f<int>(int)"),
            ("_Z1fIiEDTszstT_ET_", "decltype(sizeof (sizeof (int))) f<int>(int)"),
            ("_Z1fIiEDTntsZT_ET_", "decltype(!sizeof...(int...)) f<int>(int)"),
            ("_Z1fIiEDTdttiT_4nameET_", "decltype(typeid (int).name) f<int>(int)"),
        ],
    )
    def test_the_llvm_spelling(self, mangled, expected):
        assert demangle.demangle_strict(mangled) == expected


class TestAFoldsPackIsExpanded:
    """llvm-cxxfilt prints a fold's pack through `ParameterPackExpansion` inside
    brackets of its own: the pattern once per member when it names a pack, and the
    pattern with its ellipsis when it does not. This spelled a pattern that names a
    pack once, with the whole pack inside it, `(sizeof (int, char)...)`. The first
    three names were compiled with Clang 18 from `(sizeof(T) + ...)` and its
    relatives; c++filt spells the pattern once, and the gnu style still does."""

    @pytest.mark.parametrize(
        "mangled, expected",
        [
            ("_Z2t6IJicEEv1IIXfrplstT_EE", "void t6<int, char>(I<((sizeof (int), sizeof (char)) + ...)>)"),
            ("_Z2t7IJicEEv1IIXflplstT_EE", "void t7<int, char>(I<(... + (sizeof (int), sizeof (char)))>)"),
            ("_Z2t8IJicEEv1IIXfRplstT_Li1EEE", "void t8<int, char>(I<((sizeof (int), sizeof (char)) + ... + 1)>)"),
            ("_Z1fIJEEv1IIXfrplstT_EE", "void f<>(I<(() + ...)>)"),
            ("_Z1fIJicEEv1IIXfrplT_EE", "void f<int, char>(I<((int, char) + ...)>)"),
            ("_Z1fIJicEEDTfrplfp_EDpT_", "decltype(((fp...) + ...)) f<int, char>(int, char)"),
            ("_Z1fIJicEEDTfrplszfp_EDpT_", "decltype(((sizeof (fp)...) + ...)) f<int, char>(int, char)"),
        ],
    )
    def test_the_llvm_spelling(self, mangled, expected):
        assert demangle.demangle_strict(mangled) == expected

    def test_the_gnu_spelling_is_cxxfilts(self):
        assert demangle.demangle_strict("_Z2t6IJicEEv1IIXfrplstT_EE", style="gnu") == (
            "void t6<int, char>(I<((sizeof (int, char))+...)>)"
        )


class TestDivisionKeepsItsPrecedence:
    """llvm-cxxfilt 18 and 20 carry `dv` in their operator table at the precedence of
    an assignment, so `(sizeof(T) + 1) / 2` -- `_Z9half_moreIiEv1IIXdvplstT_Li1ELi2EEE`,
    compiled identically by g++ 13 and Clang 18 -- prints there as
    `sizeof (int) + 1 / 2`, a different expression, and `a / b - c` as `(a / b) - c`.
    c++filt and this bracket by the precedence `/` has. See
    `tests/conformance/itanium-reference-defects.txt`."""

    def test_the_sum_it_divides_is_bracketed(self):
        assert demangle.demangle_strict("_Z9half_moreIiEv1IIXdvplstT_Li1ELi2EEE") == (
            "void half_more<int>(I<(sizeof (int) + 1) / 2>)"
        )

    def test_and_nothing_else_is(self):
        assert (
            demangle.demangle_strict("_Z7quarterIiEv1IIXdvdvstT_Li2ELi2EEE")
            == "void quarter<int>(I<sizeof (int) / 2 / 2>)"
        )
        assert demangle.demangle_strict("_Z1fIXmidvLi1ELi2ELi3EEEvv") == "void f<1 / 2 - 3>()"
