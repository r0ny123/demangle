"""Demangling a bare *type* encoding rather than a whole symbol.

`c++filt -t`, libiberty's `DMGL_TYPES`, `UnDecorateSymbolName`'s `UNDNAME_TYPE_ONLY`
and Swift's `demangleTypeAsString` all read one, and every one of them makes the caller
ask for it rather than detecting. That is not an interface wart: a whole symbol
announces its scheme -- `_Z`, `?`, `$s` -- and a type encoding announces nothing at all.
`i` is a valid Itanium type, a valid Swift type and an ordinary C identifier, so there
is no evidence to detect on and `language` has to be given.

The Itanium expectations are both references verbatim, over the same 1,076 encodings:
`c++filt -t` (binutils 2.42) in `conformance/itanium-types.txt` and `llvm-cxxfilt
--types` (18.1.3) in `conformance/itanium-types-llvm.txt`. The pair is what pins the
style split -- the same encoding, two spellings, one file each.

MSVC has no type-only tool here (llvm-undname 18.1.3 has no `--types`), so there the
standing-in property is agreement with the symbol path: whatever `f(T)` spells between
its brackets is what `demangle_type` must spell for `T` alone.
"""

from typing import ClassVar

import pytest

import demangle
from demangle.core.errors import DemanglingError, NotMangledError, ParseError

from .conftest import load_corpus
from .test_conformance import TYPES_GNU_EXACT, TYPES_LLVM_EXACT, TYPES_TOTAL


class TestTheItaniumTypeGrammar:
    #: Raised as gaps close, never lowered silently. Both styles now read every row of
    #: their reference's corpus exactly; `gnu` used to fall three short, on `KK`.
    EXPECTED_EXACT: ClassVar = {"gnu": TYPES_GNU_EXACT, "llvm": TYPES_LLVM_EXACT}

    CORPUS: ClassVar = {"gnu": "itanium-types.txt", "llvm": "itanium-types-llvm.txt"}

    def _score(self, style):
        return sum(
            1 for enc, expected in load_corpus(self.CORPUS[style]) if _spelled(enc, "itanium", style) == expected
        )

    @pytest.mark.parametrize("style", ["llvm", "gnu"])
    def test_the_score_has_not_gone_backwards(self, style):
        total = len(load_corpus(self.CORPUS[style]))
        assert total == TYPES_TOTAL, "corpus did not load; this test would prove nothing"
        assert self._score(style) >= self.EXPECTED_EXACT[style]

    @pytest.mark.parametrize("style", ["llvm", "gnu"])
    def test_the_pinned_number_is_still_accurate(self, style):
        assert self._score(style) == self.EXPECTED_EXACT[style]

    def test_both_corpora_hold_the_same_encodings(self):
        """One input set, two references. A row in one and not the other is a mistake."""
        assert [enc for enc, _ in load_corpus("itanium-types.txt")] == [
            enc for enc, _ in load_corpus("itanium-types-llvm.txt")
        ]

    def test_there_is_no_shortfall_left_in_either_style(self):
        """`KKDv4_i` and its two neighbours were the last three, and they are read now.

        They were left as a recorded divergence on the reasoning that no compiler emits
        `KK`, which was true of `KK` written outright and false of what it means: the
        same doubling arrives through an already-qualified template argument, `K T_`
        with `T_` bound to `K i`, and the shipped libLLVM has three of those. Closing it
        for the argument closed it for the literal spelling too. See
        `collapse_duplicate_qualifiers`, and `TestDuplicateQualifiers` below for the
        rule and its boundaries.
        """
        assert [
            enc for enc, expected in load_corpus("itanium-types.txt") if _spelled(enc, "itanium", "gnu") != expected
        ] == []
        assert [
            enc
            for enc, expected in load_corpus("itanium-types-llvm.txt")
            if _spelled(enc, "itanium", "llvm") != expected
        ] == []

    @pytest.mark.parametrize("style", ["llvm", "gnu"])
    def test_a_type_spells_what_the_same_type_spells_inside_a_symbol(self, style):
        """`demangle_type(T)` and the parameter list of `_Z1f<T>` are the same question."""
        for enc, _ in load_corpus("itanium-types.txt"):
            whole = demangle.demangle(f"_Z1f{enc}", style=style)
            if whole == f"_Z1f{enc}" or not whole.startswith("f("):
                continue
            if whole == "f()":
                # `v` alone is the type `void`; `v` as a parameter list is *no*
                # parameters. Both spellings are right and they are not the same string.
                continue
            assert _spelled(enc, "itanium", style) == whole[len("f(") : -1], enc

    @pytest.mark.parametrize("style", ["llvm", "gnu"])
    def test_the_tree_spells_what_the_text_spells(self, style):
        for enc, _ in load_corpus("itanium-types.txt"):
            tree = demangle.parse_type(enc, language="itanium", style=style)
            assert tree.spell(style=style) == demangle.demangle_type(enc, language="itanium", style=style), enc


def _spelled(encoding, language, style):
    try:
        return demangle.demangle_type(encoding, language=language, style=style)
    except DemanglingError:
        return None


class TestDuplicateQualifiers:
    """`const const` is a spelling no declaration has, and one reference prints it.

    [basic.type.qualifier] gives a type at most one of each cv-qualifier, so applying
    `const` to something already const adds nothing. It reaches a mangled name written
    outright, `K K i`, and through an already-qualified template argument, `K T_` with
    `T_` bound to `K i` -- which is where all three instances in the shipped libraries
    come from. llvm-cxxfilt prints both twice; GNU c++filt collapses them, and the gnu
    style follows it.

    The order the survivors print in is decided by the outer qualifier winning, which is
    why `K V K i` and `K K V i` both come out `volatile const`. Checked here against the
    references over every sequence of one to three qualifiers, and against the boundary
    cases: an array passes them through, every other declarator stops them.
    """

    @pytest.mark.parametrize(
        ("mangled", "llvm", "gnu"),
        [
            ("_Z1fKKi", "f(int const const)", "f(int const)"),
            ("_Z1fKVKi", "f(int const volatile const)", "f(int volatile const)"),
            ("_Z1fKKVi", "f(int volatile const const)", "f(int volatile const)"),
            ("_Z1fVKKi", "f(int const const volatile)", "f(int const volatile)"),
            ("_Z1frKri", "f(int restrict const restrict)", "f(int const restrict)"),
            ("_Z1fKrVi", "f(int volatile restrict const)", "f(int volatile restrict const)"),
            # Through a template argument, which is the shape the libraries carry.
            ("_Z1fIKiEvKT_", "void f<int const>(int const const)", "void f<int const>(int const)"),
            ("_Z1fIKiEvRKT_", "void f<int const>(int const const&)", "void f<int const>(int const&)"),
            ("_Z1fIKiEvVT_", "void f<int const>(int const volatile)", "void f<int const>(int const volatile)"),
            # An array is qualified through to its elements; a pointer is not.
            ("_Z1fKA3_Ki", "f(int const const [3])", "f(int const [3])"),
            ("_Z1fKPKi", "f(int const* const)", "f(int const* const)"),
            ("_Z1fPKKi", "f(int const const*)", "f(int const*)"),
            # A function type's qualifiers belong to its implicit object parameter and
            # are not part of this at all.
            ("_Z1fKFvvE", "f(void () const)", "f(void () const)"),
        ],
    )
    def test_each_style_spells_it_the_way_its_reference_does(self, mangled, llvm, gnu):
        assert demangle.demangle_strict(mangled, style="llvm") == llvm
        assert demangle.demangle_strict(mangled, style="gnu") == gnu

    @pytest.mark.parametrize("style", ["llvm", "gnu"])
    @pytest.mark.parametrize("mangled", ["_Z1fKVKi", "_Z1fKA3_Ki", "_Z1fIKiEvKT_", "_Z1fPKKi"])
    def test_the_tree_renders_what_the_text_path_spells(self, mangled, style):
        assert demangle.parse(mangled, style=style).spell(style=style) == demangle.demangle(mangled, style=style)

    def test_collapsing_does_not_change_what_the_substitution_table_holds(self):
        """Each `K` is its own `<type>` and so its own candidate, whatever it spells.

        libiberty reads a run of cv-qualifiers as one production and records one entry
        for it, which is why c++filt refuses `_Z1fKKiS_S0_` -- it has only one entry
        where the ABI has two. Collapsing the *spelling* must not do that here.
        """
        assert demangle.demangle_strict("_Z1fKKiS_S0_") == "f(int const const, int const, int const const)"
        assert demangle.demangle_strict("_Z1fKKiS_S0_", style="gnu") == "f(int const, int const, int const)"


class TestComplexAndImaginaryAreNotCvQualifiers:
    """They go through `qualify` and a repeat of one does not collapse.

    `[basic.type.qualifier]` folds a duplicate `const`, which is why the `gnu` style
    collapses one; nothing folds a duplicate `_Imaginary`. `c++filt` 2.42 writes
    `signed char _Imaginary _Imaginary` for `_Z1fGGa` and this wrote one of them, losing
    a word of the name -- and the two styles disagreed with each other about it, since
    only the gnu one collapses at all.
    """

    @pytest.mark.parametrize(
        ("mangled", "llvm", "gnu"),
        [
            ("_Z1fGGa", "f(signed char imaginary imaginary)", "f(signed char _Imaginary _Imaginary)"),
            ("_Z1fGCa", "f(signed char complex imaginary)", "f(signed char _Complex _Imaginary)"),
            ("_Z1fGa", "f(signed char imaginary)", "f(signed char _Imaginary)"),
        ],
    )
    def test_a_repeat_is_kept(self, mangled, llvm, gnu):
        assert demangle.demangle_strict(mangled, language="itanium", style="llvm") == llvm
        assert demangle.demangle_strict(mangled, language="itanium", style="gnu") == gnu

    @pytest.mark.parametrize(
        ("mangled", "llvm", "gnu"),
        [
            # The collapse the gnu style does do, and still does: a real duplicate
            # cv-qualifier, where the outer one wins.
            ("_Z1fKKi", "f(int const const)", "f(int const)"),
            ("_Z1fKVi", "f(int volatile const)", "f(int volatile const)"),
        ],
    )
    def test_a_repeated_cv_qualifier_still_collapses_under_gnu(self, mangled, llvm, gnu):
        assert demangle.demangle_strict(mangled, language="itanium", style="llvm") == llvm
        assert demangle.demangle_strict(mangled, language="itanium", style="gnu") == gnu

    def test_the_tree_spells_what_the_text_path_spells(self):
        """`cv` rides on the node, or the two builders would disagree about the repeat.

        `spell(style=...)` rather than `spell()`: the style is the renderer's, not the
        tree's, and a bare `spell()` uses the default policy whatever the tree was parsed
        under. That is the documented contract and is not what is under test here.
        """
        for mangled in ("_Z1fGGa", "_Z1fGCa", "_Z1fKKi"):
            for style in ("llvm", "gnu"):
                text = demangle.demangle_strict(mangled, language="itanium", style=style)
                tree = demangle.parse(mangled, language="itanium", style=style)
                assert tree.spell(style=style) == text


class TestObjectiveCProtocolQualifiedTypes:
    """`U <n>objcproto<protocol> <type>`, and the one pointer the word `id` stands for.

    A protocol-qualified `objc_object` is `objc_object<A>`, and a pointer to it is
    `id<A>` -- `id` *is* the pointer, so it is not written again. Only the first one:
    every level above it is an ordinary `*`. This collapsed the unpointed form to
    `id<A>` as well and then handed the same handle back out of every `P`, so
    `PPU11objcproto1A11objc_object` and `PPPU...` came back `id<A>` too, with the
    pointers gone.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fU11objcproto1A11objc_object", "f(objc_object<A>)"),
            ("_Z1fPU11objcproto1A11objc_object", "f(id<A>)"),
            ("_Z1fPPU11objcproto1A11objc_object", "f(id<A>*)"),
            ("_Z1fPPPU11objcproto1A11objc_object", "f(id<A>**)"),
            # A class that is not `objc_object` takes the brackets and keeps its pointer.
            ("_Z1fPU11objcproto1A7NSArray", "f(NSArray<A>*)"),
            ("_Z1fPKU11objcproto1A7NSArray", "f(NSArray<A> const*)"),
        ],
    )
    def test_only_the_first_pointer_is_the_word_id(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="itanium") == expected


class TestAnInheritingConstructorCarriesAVariant:
    """`CI1` through `CI5`, the same five an ordinary constructor carries.

    `parseCtorDtorName` requires the digit and `llvm-cxxfilt` refuses `CI0`, `CI6` and
    `CIT` alike. Taking whatever character stood there read `_ZN1BCIT1AEi` as
    `B::B(int)`, a constructor of a class the encoding does not say is one.
    """

    @pytest.mark.parametrize("mangled", ["_ZN1BCIT1AEi", "_ZN1BCI01AEi", "_ZN1BCI61AEi", "_ZN1BCI_1AEi"])
    def test_a_variant_that_is_not_one_is_refused(self, mangled):
        assert demangle.demangle(mangled) == mangled

    @pytest.mark.parametrize("mangled", ["_ZN1BCI11AEi", "_ZN1BCI21AEi", "_ZN1BCI51AEi"])
    def test_the_five_still_read(self, mangled):
        assert demangle.demangle_strict(mangled, language="itanium") == "B::B(int)"


class TestWhatIsRefused:
    def test_an_empty_encoding_is_not_a_type(self):
        with pytest.raises(NotMangledError):
            demangle.demangle_type("", language="itanium")

    def test_trailing_input_is_refused_rather_than_ignored(self):
        """`iX` is not `int`. A type entry point that stops early invents a reading."""
        with pytest.raises(ParseError):
            demangle.demangle_type("iX", language="itanium")

    def test_a_template_parameter_has_nothing_to_bind_it(self):
        """`T_` is `auto` in a symbol, where arguments may follow, and nothing here.

        A whole symbol can encode a return type ahead of the arguments that bind it, so
        an unresolved `T_` there is spelled `auto` -- which is what both references do,
        and what generic lambdas rely on. A bare type has no enclosing template and can
        never acquire one, so `auto` would be a type that is not in the encoding.
        `c++filt -t` refuses these too.
        """
        for encoding in ("T_", "PT_", "AT__i"):
            with pytest.raises(ParseError):
                demangle.demangle_type(encoding, language="itanium")

    def test_a_whole_symbol_is_not_a_type(self):
        with pytest.raises(DemanglingError):
            demangle.demangle_type("_Z1fv", language="itanium")

    def test_bytes_are_a_mistake_in_the_calling_code(self):
        with pytest.raises(TypeError):
            demangle.demangle_type(b"Pi", language="itanium")  # ty: ignore[invalid-argument-type]


class TestNamingTheScheme:
    def test_the_language_is_required(self):
        with pytest.raises(TypeError):
            demangle.demangle_type("Pi")  # ty: ignore[missing-argument]

    def test_an_unknown_language_says_which_are_known(self):
        with pytest.raises(ValueError, match="unknown language"):
            demangle.demangle_type("Pi", language="klingon")

    def test_a_scheme_with_no_type_grammar_says_which_have_one(self):
        """Go, Rust, Nim and the rest carry types only as text they already spell."""
        with pytest.raises(ValueError, match="itanium, msvc, swift"):
            demangle.demangle_type("Pi", language="rust")

    def test_the_same_encoding_reads_differently_under_different_schemes(self):
        """Why detection is impossible here, in one assertion."""
        assert demangle.demangle_type("Si", language="itanium") == "std::istream"
        assert demangle.demangle_type("Si", language="swift") == "Swift.Int"


class TestMsvcTypes:
    @pytest.mark.parametrize(
        ("encoding", "expected"),
        [
            ("PEAX", "void *"),
            ("H", "int"),
            ("PEAVFoo@@", "class Foo *"),
            ("V?$A@H@@", "class A<int>"),
            ("PEAPEAH", "int **"),
            ("AEAH", "int &"),
        ],
    )
    def test_a_type_reads_as_llvm_undname_reads_it(self, encoding, expected):
        assert demangle.demangle_type(encoding, language="msvc") == expected

    def test_a_leading_dot_is_the_type_descriptors_own_spelling(self):
        """The linker writes `.PEAX` on an RTTI type descriptor; the type is after it."""
        assert demangle.demangle_type(".PEAX", language="msvc") == "void *"

    def test_a_variables_type_reads_the_same_alone_as_in_its_symbol(self):
        """`?x@@3PEAXEA` is `void * x`, so `PEAX` on its own has to be `void *`."""
        assert demangle.demangle("?x@@3PEAXEA").startswith(demangle.demangle_type("PEAX", language="msvc"))

    def test_nonsense_is_refused(self):
        with pytest.raises(ParseError):
            demangle.demangle_type("QQQ", language="msvc")

    def test_a_decorated_name_is_not_a_type(self):
        with pytest.raises(DemanglingError):
            demangle.demangle_type("?f@@YAXH@Z", language="msvc")


class TestSwiftTypes:
    @pytest.mark.parametrize(
        ("encoding", "expected"),
        [
            ("Si", "Swift.Int"),
            ("SS", "Swift.String"),
            ("SaySiG", "[Swift.Int]"),
            ("Si_Sit", "(Swift.Int, Swift.Int)"),
            ("4main3FooC", "main.Foo"),
        ],
    )
    def test_a_type_reads_as_swift_demangle_reads_it(self, encoding, expected):
        assert demangle.demangle_type(encoding, language="swift") == expected

    def test_an_unreadable_type_is_refused_rather_than_narrated(self):
        """The reference wraps what it could not read in `with unmangled suffix "..."`.

        True, and not a demangling of anything, so it comes back as the failure it is.
        """
        with pytest.raises(DemanglingError):
            demangle.demangle_type("", language="swift")


class TestTheTreeForm:
    def test_parse_type_gives_a_node_the_text_form_agrees_with(self):
        tree = demangle.parse_type("PKFvRiE", language="itanium")
        assert isinstance(tree, demangle.Node)
        assert tree.spell() == demangle.demangle_type("PKFvRiE", language="itanium")

    def test_the_tree_carries_the_declarator_shape(self):
        assert demangle.parse_type("Pi", language="itanium").kind == "pointer"

    @pytest.mark.parametrize("language", ["itanium", "msvc", "swift"])
    def test_every_scheme_that_reads_a_type_gives_both_forms(self, language):
        encoding = {"itanium": "Pi", "msvc": "PEAX", "swift": "Si"}[language]
        assert isinstance(demangle.parse_type(encoding, language=language), demangle.Node)
        assert isinstance(demangle.demangle_type(encoding, language=language), str)


class TestTheLimitsApply:
    def test_an_over_long_encoding_is_refused_before_it_is_read(self):
        for language, encoding in (
            ("itanium", "P" * 400 + "i"),
            ("msvc", "PEA" * 200 + "X"),
            ("swift", "Sa" * 300 + "ySiG"),
        ):
            with pytest.raises(demangle.LimitExceeded):
                demangle.demangle_type(encoding, language=language, limits=demangle.Limits(max_input=32))

    def test_the_relaxed_limits_let_a_deeper_type_through(self):
        deep = "P" * 200 + "i"
        assert demangle.demangle_type(deep, language="itanium", limits=demangle.RELAXED_LIMITS).endswith("*" * 200)


class TestTheBytesForms:
    """A type encoding is read out of a binary as often as a symbol is."""

    def test_bytes_in_bytes_out(self):
        assert demangle.demangleb_type(b"PKFvRiE", language="itanium") == b"void (*)(int&) const"

    def test_the_tree_form_over_bytes(self):
        assert demangle.parseb_type(b"Pi", language="itanium").spell() == "int*"

    def test_undecodable_bytes_do_not_raise_a_unicode_error(self):
        """`surrogateescape`, as everywhere else: a demangling failure, not a decode one."""
        with pytest.raises(DemanglingError):
            demangle.demangleb_type(b"P\xff", language="itanium")

    def test_a_str_is_a_mistake_in_the_calling_code(self):
        with pytest.raises(TypeError):
            demangle.demangleb_type("Pi", language="itanium")  # ty: ignore[invalid-argument-type]


class TestACvQualifiedFunctionTypeReachedThroughASubstitution:
    """The same type, written out and abbreviated, spells the same thing.

    A cv-qualifier on a function type is the ABI's way of writing a member function's
    implicit object parameter, and both references spell one written out as
    `void () const`. Reached through a `<substitution>` they each contradict that:
    `llvm-cxxfilt` 18.1.3 answers `void  const()`, moving the qualifier into the
    declarator and doubling a space, and GNU `c++filt` 2.42 answers `void ( const)()`,
    putting it inside the brackets. Found by mutating a real libstdc++ symbol, whose
    `RKS4_` refers back to a function type in the template arguments.
    """

    @pytest.mark.parametrize(
        ("written_out", "abbreviated", "expected"),
        [
            ("_Z1fFvvEKFvvE", "_Z1fFvvEKS_", "f(void (), void () const)"),
            ("_Z1fFvvERKFvvE", "_Z1fFvvERKS_", "f(void (), void (&)() const)"),
            ("_Z1fFvvEPKFvvE", "_Z1fFvvEPKS_", "f(void (), void (*)() const)"),
            ("_Z1fFvvEVFvvE", "_Z1fFvvEVS_", "f(void (), void () volatile)"),
        ],
    )
    def test_the_substitution_spells_what_the_type_spells(self, written_out, abbreviated, expected):
        assert demangle.demangle_strict(written_out, language="itanium") == expected
        assert demangle.demangle_strict(abbreviated, language="itanium") == expected


class TestAProtocolIsASourceNameInsideItsQualifier:
    """`parseQualifiedType` reads the protocol out of `objcproto...` with
    `parseBareSourceName`: a length and that many characters. `objcproto15` is a length
    with nothing after it, refused by `llvm-cxxfilt`, where this took the digits for
    the protocol and spelled `id<15>`; and `objcproto1ABC` is `A`, the rest of the
    qualifier unread. `tools/mutate.py --seed 23`."""

    @pytest.mark.parametrize("mangled", ["_Z1fPU11objcproto1511objc_object", "_Z1fPU10objcproto011objc_object"])
    def test_a_length_with_too_little_after_it_is_refused(self, mangled):
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="itanium")

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fPU11objcproto1A11objc_object", "f(id<A>)"),
            ("_Z1fPU13objcproto1ABC11objc_object", "f(id<A>)"),
            ("_Z1fPU12objcproto2AB7NSArray", "f(NSArray<AB>*)"),
        ],
    )
    def test_the_first_length_worth_of_characters_is_the_protocol(self, mangled, expected):
        assert demangle.demangle(mangled) == expected


class TestAVectorDimensionIsANumberToCxxfilt:
    """`d_vector_type` reads the dimension with `d_number` and prints the value, so
    `Dv07_b` is `__vector(7)` under the gnu style. An array bound is printed as written
    by both references, `[07]`, and stays so. `llvm-cxxfilt` refuses a vector dimension
    that does not open on 1-9; the llvm style prints the digits it read. Found by the
    `types` job of `tools/mutate.py`."""

    @pytest.mark.parametrize(
        ("mangled", "llvm", "gnu"),
        [
            ("Dv07_b", "bool vector[07]", "bool __vector(7)"),
            ("Dv7_b", "bool vector[7]", "bool __vector(7)"),
            ("Dv0_b", "bool vector[0]", "bool __vector(0)"),
            ("A07_i", "int [07]", "int [07]"),
        ],
    )
    def test_the_two_styles(self, mangled, llvm, gnu):
        assert demangle.demangle_type(mangled, language="itanium") == llvm
        assert demangle.demangle_type(mangled, language="itanium", style="gnu") == gnu


class TestAVectorsSizeAndElementAsTheCompilersWriteThem:
    """Two shapes the ABI text does not have. Clang writes a dependent size with no
    underscore before it, `Dv <expression> _ <type>` -- `mangleType` for a
    `DependentSizedExtVectorType` is `Out << "Dv"; mangleExpression(Size); Out << '_'`
    -- and emits `_Z1gILi2EEvDvmlT_Li4E_i` for `template <int N> void g(int
    __attribute__((vector_size(N * 4))))`; `llvm-cxxfilt` reads that and `c++filt`
    refuses it, while the ABI's own `Dv _ <expression> _ <type>` is what `c++filt`
    reads and `llvm-cxxfilt` refuses. This read only the second, and refused a symbol
    clang++ 18.1.3 emits. And AltiVec's `__vector pixel` is `Dv <number> _ p`, a `p`
    where the element type would be: clang++ 18.1.3 writes `_Z1hDv8_p` for
    `void h(__vector pixel)`, which `llvm-cxxfilt` spells `pixel vector[8]`. Found by
    the `types` job of `tools/mutate.py --refusals`."""

    @pytest.mark.parametrize(
        ("mangled", "llvm", "gnu"),
        [
            ("DvLi4E_i", "int vector[4]", "int __vector(4)"),
            ("Dv_Li4E_i", "int vector[4]", "int __vector(4)"),
            ("Dv4_i", "int vector[4]", "int __vector(4)"),
            ("Dv4_p", "pixel vector[4]", "pixel __vector(4)"),
            ("Dv8_p", "pixel vector[8]", "pixel __vector(8)"),
            ("Dv4_b", "bool vector[4]", "bool __vector(4)"),
        ],
    )
    def test_as_a_type(self, mangled, llvm, gnu):
        assert demangle.demangle_type(mangled, language="itanium") == llvm
        assert demangle.demangle_type(mangled, language="itanium", style="gnu") == gnu

    @pytest.mark.parametrize(
        ("mangled", "llvm", "gnu"),
        [
            ("_Z1gILi2EEvDvmlT_Li4E_i", "void g<2>(int vector[2 * 4])", "void g<2>(int __vector((2)*(4)))"),
            ("_Z1hDv8_p", "h(pixel vector[8])", "h(pixel __vector(8))"),
            ("_Z1bDv4_b", "b(bool vector[4])", "b(bool __vector(4))"),
            ("_ZN1SILi16EE1sEDv4_i", "S<16>::s(int vector[4])", "S<16>::s(int __vector(4))"),
        ],
    )
    def test_as_a_symbol(self, mangled, llvm, gnu):
        assert demangle.demangle(mangled) == llvm
        assert demangle.demangle(mangled, style="gnu") == gnu

    def test_a_dimensionless_vector_is_llvm_cxxfilts_own(self):
        """`Dv_ <type>` with no expression at all is `int vector[]` to `llvm-cxxfilt` and
        nothing to the ABI, to clang's mangler or to `c++filt`."""
        with pytest.raises(DemanglingError):
            demangle.demangle_type("Dv_i", language="itanium")
