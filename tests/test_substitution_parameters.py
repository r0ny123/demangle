"""A recorded `<template-param>`, and anything built over one, stays scope-dependent.

ABI 5.1.10 makes a `<template-param>` a substitution candidate in its own right. The
entry it contributes is *the parameter* -- `T_`, `T0_` -- not whatever argument happened
to be bound to it at the point the entry was made. The distinction only shows itself when
the back-reference is read under a different template scope from the one that recorded
it, and that happens routinely: a name mentioning a local entity writes the enclosing
function's signature against *that* function's parameters, so a `T_` inside it enters the
table, and a later `S<n>_` naming that entry belongs to the outer template.

The mangler does this because it canonicalises a template type parameter by level and
index: `T_` of one template and `T_` of another are one node to it, so a `T_` already in
the table is reused for a completely different template's first parameter.

The same holds for a component built *over* one -- `R T_`, `P N S1_ I T_ E E` -- for the
same reason and by the same rule, so those are kept as their input span and read again
under the scope in force. A handle is already-built output and there is nothing in it to
re-resolve; where the production was *written* is the only thing worth keeping.

Freezing either produces a type the source disproves -- spelled plausibly, matching no
declaration -- which is the failure this package exists to avoid. llvm-cxxfilt 18.1.3
freezes both, and differs from this on 322 of the 217,730 distinct Itanium symbols in
the shared libraries of a stock Ubuntu 24.04 as a result; GNU c++filt 2.42 mostly does
not, but freezes it for the constructor case below.

Every vector in `TestAgainstTheDeclaration` was produced by a compiler on this machine
from a source in `tools/corpus_sources/reference_defects/`, so what it must spell is
settled by the declaration rather than by a demangler. The corpus of the same name
carries four more taken from shipped libraries, where the declaration is the library's
own header.
"""

import contextlib

import pytest

import demangle
from demangle.core.ast import AST_BUILDER
from demangle.core.errors import DemanglingError, ParseError
from demangle.schemes.itanium.parser import ItaniumParser
from demangle.schemes.itanium.substitutions import DeferredProduction, ParameterReference, SubstitutionTable


class TestAgainstTheDeclaration:
    """What the source says, for names a compiler on this machine emitted."""

    def test_the_parameter_resolves_where_it_is_read_not_where_it_was_recorded(self):
        """`insort(I, I, C)`: the first two parameters are `I`.

        `S8_` is the entry `legalize`'s own signature contributed, where `T_` meant
        `nn::BB*`. Under `insort` it means `nn::Update<nn::BB*>*`, and that is what the
        two parameters are declared as. g++ 13.3.0, insort.cpp.
        """
        name = "_ZN2nn6insortIPNS_6UpdateIPNS_2BBEEENS_4WrapIZNS_8legalizeIS3_EEvPNS1_IT_EEEUlRKS4_SC_E_EEEEvS8_S8_T0_"
        lambda_ = (
            "void nn::legalize<nn::BB*>(nn::Update<nn::BB*>*)::'lambda'"
            "(nn::Update<nn::BB*> const&, nn::Update<nn::BB*> const&)"
        )
        assert demangle.demangle_strict(name) == (
            f"void nn::insort<nn::Update<nn::BB*>*, nn::Wrap<{lambda_}>>"
            f"(nn::Update<nn::BB*>*, nn::Update<nn::BB*>*, nn::Wrap<{lambda_}>)"
        )

    def test_a_generic_lambdas_operator_takes_the_argument_it_was_instantiated_with(self):
        """`[](auto x){}` called with an `int` takes an `int`, not an `auto`.

        The lambda's declared parameter is written `T_` and enters the table there;
        `S0_` in `operator()<int>`'s signature names that entry. g++ 13.3.0,
        generic_lambda.cpp.
        """
        got = demangle.demangle_strict("_ZZN6modern13genericLambdaEvENKUlT_E_clIiEEDaS0_")
        assert got.endswith("::operator()<int>(int) const")

    def test_a_constructor_parameter_is_its_own_template_argument(self):
        """`Prep(Callable&)` takes a reference to the closure `Callable` is deduced as.

        Both references print `void (&)()` here -- the enclosing `callit`'s own `F`,
        which is what `T_` meant where the entry was made. g++ 13.3.0,
        prepare_execution.cpp, the shape libstdc++ ships as
        `std::once_flag::_Prepare_execution`.
        """
        name = "_ZZN5Outer4PrepC4IZ6callitIRFvvEEvOT_EUlvE_EERS5_ENUlvE_4_FUNEv"
        assert demangle.demangle_strict(name) == (
            "Outer::Prep::Prep<void callit<void (&)()>(void (&)())::'lambda'()>"
            "(void callit<void (&)()>(void (&)())::'lambda'()&)::'lambda'()::_FUN()"
        )

    def test_a_composite_built_over_a_parameter_is_scope_dependent_too(self):
        """`insort(Update<I>*, Update<I>*, C)`: the parameters are `Update<I>*`.

        `SA_` is the entry `P N S1_ I T_ E E` contributed inside `legalize`'s signature --
        a composite over the parameter rather than the parameter itself. The mangler
        reuses it across scopes for the same reason it reuses the bare one. g++ 13.3.0,
        insort_composite.cpp.
        """
        name = "_ZN2nn6insortIPNS_6UpdateIPNS_2BBEEENS_4WrapIZNS_8legalizeIS3_EEvPNS1_IT_EEEUlRKS4_SC_E_EEEEvSA_SA_T0_"
        lambda_ = (
            "void nn::legalize<nn::BB*>(nn::Update<nn::BB*>*)::'lambda'"
            "(nn::Update<nn::BB*> const&, nn::Update<nn::BB*> const&)"
        )
        assert demangle.demangle_strict(name) == (
            f"void nn::insort<nn::Update<nn::BB*>*, nn::Wrap<{lambda_}>>"
            f"(nn::Update<nn::Update<nn::BB*>*>*, nn::Update<nn::Update<nn::BB*>*>*, nn::Wrap<{lambda_}>)"
        )

    def test_a_non_type_parameters_type_puts_the_parameter_in_the_table(self):
        """`Tn` writes the parameter's type, and `T_` in it becomes an entry.

        `makeAllOfComposite<am::TemplateName>` returns `am::Bindable<am::TemplateName>`.
        llvm-cxxfilt 18 doubles the wrapper. clang++ 18.1.3, auto_marshall.cpp.
        """
        name = (
            "_ZN2am23makeMatcherAutoMarshallINS_8BindableINS_12TemplateNameEEENS_7MatcherIS2_EE"
            "TnPFT_N5llvmx8ArrayRefIPKT0_EEEXadL_ZNS_18makeAllOfCompositeIS2_EENS1_IS6_EE"
            "NS8_IPKNS4_IS6_EEEEEEEEii"
        )
        got = demangle.demangle_strict(name)
        assert "am::Bindable<am::TemplateName> am::makeAllOfComposite<am::TemplateName>" in got
        assert "am::Bindable<am::Bindable<" not in got


class TestTheEntryIsStillOneEntry:
    """Deferring what an entry *means* must not change how many entries there are.

    Getting the count wrong shifts every later back-reference in the name, which is the
    failure mode the whole module exists to prevent.
    """

    @pytest.mark.parametrize(
        "name,expected",
        [
            # ABI 5.1.10: a <template-param> reached through <type> is a <type>, and a
            # <type> is a candidate. `S1_` is the entry `T_` itself contributed.
            ("_ZSt4sortIPiEvT_S1_", "void std::sort<int*>(int*, int*)"),
            # And it is a *separate* entry from the application of it: for
            # `template<template<class, int> class H, class T> H<T,3> f(H<T,3>)` both
            # g++ 13.3 and clang++ 18.1.3 emit `S5_` for the parameter, which is only
            # reachable if `T_` took an index of its own.
            (
                "_Z16templateTemplateIN5outer5inner6HolderEiET_IT0_Li3EES5_",
                "outer::inner::Holder<int, 3> templateTemplate<outer::inner::Holder, int>"
                "(outer::inner::Holder<int, 3>)",
            ),
        ],
    )
    def test_numbering_is_unchanged(self, name, expected):
        assert demangle.demangle_strict(name) == expected

    def test_a_deferred_entry_is_what_the_table_holds(self):
        """The table holds the reference; the parser is what turns it back into a type."""
        parser = ItaniumParser("_ZSt4sortIPiEvT_S1_", AST_BUILDER)
        parser.parse()
        assert any(isinstance(entry, ParameterReference) for entry in parser.subs)


class TestOutOfScope:
    """A recorded parameter read where fewer arguments are in scope.

    `TemplateArgumentTable.lookup` answers None rather than raising, because a return
    type is encoded before the arguments that bind it. What the parser does with that
    None is its business; the property this class is about is that reaching a parameter
    through a back-reference behaves no differently from reaching it directly.
    """

    def test_a_parameter_past_the_end_is_refused_through_a_substitution_too(self):
        """`f<int>` has one argument, so `T0_` names one that is not in scope.

        Neither answers `void f<int>(auto)`; `S0_` -- the entry `T0_` itself
        contributed -- must not either, which is the property under test. Both
        `c++filt` 2.42 and `llvm-cxxfilt` 18.1 hand both names back: an index past the
        end of the argument list binds to nothing and nothing later will supply it, and
        `auto` is a type the encoding does not contain. The property holds as a refusal.
        """
        for mangled in ("_Z1fIiEvT0_", "_Z1fIiEvT0_S0_"):
            with pytest.raises(DemanglingError):
                demangle.demangle_strict(mangled, language="itanium")

    def test_a_parameter_in_scope_reads_the_same_directly_and_through_a_substitution(self):
        """The other half, and the one that says the back-reference machinery works."""
        assert demangle.demangle_strict("_Z1fIiEvT_S0_") == "void f<int>(int, int)"
        assert demangle.demangle_strict("_Z1fIiEvPT_S0_") == "void f<int>(int*, int)"

    def test_no_table_sentinel_ever_reaches_a_builder(self):
        """The real check, not a substring one.

        A `ParameterReference` or a `DeferredProduction` that escaped would reach a
        builder as an argument, and what came out the far side would be an
        `AttributeError` or a `repr` -- not the class name in the text, which a symbol
        may legitimately contain (`llvm::ms_demangle::TemplateParameterReferenceNode` is
        a real one). So every builder method is wrapped and every argument inspected.
        """
        from .conftest import load_corpus

        seen = []
        builder = AST_BUILDER
        methods = [n for n in dir(type(builder)) if not n.startswith("_") and callable(getattr(builder, n, None))]
        saved = {name: getattr(builder, name) for name in methods}

        def watch(original):
            def wrapper(*arguments, **keywords):
                for value in (*arguments, *keywords.values()):
                    candidates = value if isinstance(value, list | tuple) else (value,)
                    seen.extend(v for v in candidates if isinstance(v, ParameterReference | DeferredProduction))
                return original(*arguments, **keywords)

            return wrapper

        for name, original in saved.items():
            setattr(builder, name, watch(original))
        try:
            for corpus in ("itanium-reference-defects.txt", "reported/itanium.txt", "itanium-libstdcxx.txt"):
                for mangled, _ in load_corpus(corpus):
                    with contextlib.suppress(DemanglingError):
                        demangle.parse(mangled)
        finally:
            for name, original in saved.items():
                setattr(builder, name, original)
        assert seen == []


class TestPacks:
    """A pack expansion reads its pattern once per member, and the scope does not change.

    That is the one place a memo keyed on the scope is wrong: it would hand every member
    of `Dp S4_` in `_Z1fIJicdEEPFvDpT_EPFvDpRPS0_ES8_S1_DpS4_S6_` -- from libcxxabi's own
    corpus -- the first member's answer, and the text and the tree would disagree.
    """

    def test_an_expansion_over_a_deferred_entry_ranges_over_its_members(self):
        name = "_Z1fIJicdEEPFvDpT_EPFvDpRPS0_ES8_S1_DpS4_S6_"
        assert demangle.demangle_strict(name) == (
            "void (*f<int, char, double>(void (*)(int*&, char*&, double*&), "
            "void (*)(int*&, char*&, double*&), int, char, double, int*, char*, double*, "
            "int*&, char*&, double*&))(int, char, double)"
        )

    @pytest.mark.parametrize("style", ["llvm", "gnu"])
    def test_the_tree_agrees_with_the_text(self, style):
        """The text and the tree spell the same thing."""
        name = "_Z1fIJicdEEPFvDpT_EPFvDpRPS0_ES8_S1_DpS4_S6_"
        assert demangle.parse(name, style=style).spell(style=style) == demangle.demangle(name, style=style)

    def test_a_parameter_naming_a_pack_outside_an_expansion_spells_every_member(self):
        """A known divergence from both references, recorded rather than left implicit.

        `_Z1fIJfdEEvT_` binds `T_` to a pack of two and uses it where no `Dp` expands it.
        Both references print the *first* member -- libcxxabi's `ParameterPack::printLeft`
        prints `Data[OB.CurrentPackIndex]`, and `initializePackExpansion` leaves that
        index at 0 -- so they answer `void f<float, double>(float)`. This prints the
        members, because making element 0 the default would break eleven of libcxxabi's
        own vectors: `sizeof...`, the four fold expressions and `sp` all reach a pack
        through the same path and each wants every member of it. No compiler writes
        `T_` for a pack -- only `Dp T_` -- so following the references here would cost
        more than it is worth.
        """
        assert demangle.demangle_strict("_Z1fIJfdEEvT_") == "void f<float, double>(float, double)"


class TestAReferenceOverAPackReturnCollapsesOnEveryMember:
    """A function type whose return is `R Dp ...`. llvm-cxxfilt prints the outer
    reference only on the last member of the expansion, stacking a second `&`
    instead of collapsing: `_Z1fIJicdEEPFvDpT_EFRDpRPS0_E` is
    `int*&, char*&, double*& ()` here and `int*&, char*&, double*&& ()` there.
    c++filt refuses. A declarator over a pack applies to every member, and `R`
    over `T&` is `T&`. No compiler writes a function that returns a pack. The
    corpus neighbour with `v` where `R` is still agrees with both references.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            (
                "_Z1fIJicdEEPFvDpT_EPFRDpRPS0_ES8_S1_DpS4_S6_",
                "void (*f<int, char, double>(int*&, char*&, double*& (*)(), "
                "int*&, char*&, double*& (), int, char, double, int*, char*, double*, "
                "int*&, char*&, double*&))(int, char, double)",
            ),
            (
                "_Z1fIJicdEEPFvDpT_EFRDpRPS0_E",
                "void (*f<int, char, double>(int*&, char*&, double*& ()))(int, char, double)",
            ),
            (
                "_Z1fIJicdEEPFvDpT_EPFvDpRPS0_ES8_S1_DpS4_S6_",
                "void (*f<int, char, double>(void (*)(int*&, char*&, double*&), "
                "void (*)(int*&, char*&, double*&), int, char, double, int*, char*, double*, "
                "int*&, char*&, double*&))(int, char, double)",
            ),
        ],
    )
    def test_the_spelling(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="itanium") == expected


def test_a_table_rejects_a_production_the_abi_does_not_call_a_candidate():
    """Unchanged by deferral: the guard is on the production, not on what is stored."""
    table = SubstitutionTable("_Z1fv")
    with pytest.raises(AssertionError):
        table.remember(ParameterReference(0), "builtin-type")


def test_an_index_past_the_table_is_still_a_parse_error():
    with pytest.raises(ParseError):
        demangle.demangle_strict("_Z1fIiEvS9_")


class TestTheReReadStaysFlat:
    """Re-reading a span is work an attacker can ask for, so it is memoised.

    A chain of entries each built over the one before -- `T_`, `P S0_`, `P S1_`, ... --
    costs one walk of the whole chain per back-reference without a memo, which is
    quadratic in the length of the name. The memo is keyed on the entry and the scope,
    because the answer only changes when the scope does.

    What is pinned here is the count, not the wall clock: a mis-keyed memo is a
    performance defect that no output comparison would ever show.
    """

    @staticmethod
    def sequence(index):
        alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        if index == 0:
            return "S_"
        remaining, text = index - 1, ""
        while True:
            text = alphabet[remaining % 36] + text
            remaining = remaining // 36 - 1
            if remaining < 0:
                break
        return "S" + text + "_"

    def chain(self, length):
        """`T_`, then `length` pointers each recorded over the entry before it."""
        return "T_" + "".join("P" + self.sequence(index) for index in range(1, length + 1))

    def rereads(self, mangled):
        """How many spans the parser had to read again for `mangled`."""
        counted = []
        original = ItaniumParser._reread

        def watch(self, index, span):
            counted.append(index)
            return original(self, index, span)

        ItaniumParser._reread = watch
        try:
            demangle.demangle_strict(mangled)
        finally:
            ItaniumParser._reread = original
        return len(counted)

    def read_back(self, length):
        """A chain of `length` entries, then every one of them read back."""
        reads = "".join(self.sequence(index) for index in range(1, length + 1))
        return "_Z1fIiEv" + self.chain(length) + reads

    def test_reading_a_chain_back_does_not_grow_with_its_length(self):
        """Four times the chain, four times the reads, the same number of re-reads.

        Without the memo each of the `length` back-references walks the whole chain
        behind it, so the work is quadratic. Asserted as *equality* between two sizes
        rather than as a bound, because a bound generous enough to pass would also pass
        for something growing slowly.
        """
        assert demangle.demangle_strict(self.read_back(200)).startswith("void f<int>(int, int*, int**")
        assert self.rereads(self.read_back(200)) == self.rereads(self.read_back(800)) < 32

    def test_the_answer_is_still_right_at_every_link(self):
        """A memo that returned a stale answer would be silent, so check the spelling."""
        got = demangle.demangle_strict("_Z1fIiEv" + self.chain(4) + "".join(self.sequence(i) for i in (1, 2, 3, 4)))
        assert got == "void f<int>(int, int*, int**, int***, int****, int, int*, int**, int***)"
