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
    #: Never lowered silently.
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

    def test_there_is_no_shortfall_in_either_style(self):
        """Every encoding reads exactly, `KKDv4_i` and its two neighbours included.

        No compiler emits `KK` written outright, but the same doubling arrives through
        an already-qualified template argument, `K T_` with `T_` bound to `K i`, and the
        shipped libLLVM has three of those. The collapse for the argument applies to the
        literal spelling too. See `collapse_duplicate_qualifiers`, and
        `TestDuplicateQualifiers` below for the rule and its boundaries.
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
    `signed char _Imaginary _Imaginary` for `_Z1fGGa`, and both styles do the same:
    dropping one would lose a word of the name, and only the gnu style collapses
    anything at all.
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
            # The collapse the gnu style does do: a real duplicate cv-qualifier.
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
    every level above it is an ordinary `*`, so `PPU11objcproto1A11objc_object` is
    `id<A>*` and `PPPU...` is `id<A>**`, with the pointers kept.
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
    `CIT` alike, so `_ZN1BCIT1AEi` is refused rather than read as `B::B(int)`, a
    constructor of a class the encoding does not say is one.
    """

    @pytest.mark.parametrize("mangled", ["_ZN1BCIT1AEi", "_ZN1BCI01AEi", "_ZN1BCI61AEi", "_ZN1BCI_1AEi"])
    def test_a_variant_that_is_not_one_is_refused(self, mangled):
        assert demangle.demangle(mangled) == mangled

    @pytest.mark.parametrize("mangled", ["_ZN1BCI11AEi", "_ZN1BCI21AEi", "_ZN1BCI51AEi"])
    def test_the_five_still_read(self, mangled):
        assert demangle.demangle_strict(mangled, language="itanium") == "B::B(int)"

    def test_the_constructor_is_the_derived_classs_under_both_styles(self):
        """libiberty reads the base type after `CI` through `cplus_demangle_type`, which
        leaves the base's last component as `di->last_name`, and names the constructor
        from that: `c++filt` 2.42 spells g++'s
        `_ZNSt15__uniq_ptr_dataI1KSt14default_deleteIS0_ELb1ELb1EECI1St15__uniq_ptr_implIS0_S2_EEPS0_`
        as `__uniq_ptr_data<...>::__uniq_ptr_impl(K*)`. An inheriting constructor is a
        constructor of the derived class, which is what llvm-cxxfilt prints and what the
        gnu style prints too: a name is not a spelling."""
        mangled = "_ZNSt15__uniq_ptr_dataI1KSt14default_deleteIS0_ELb1ELb1EECI1St15__uniq_ptr_implIS0_S2_EEPS0_"
        assert demangle.demangle_strict(mangled) == (
            "std::__uniq_ptr_data<K, std::default_delete<K>, true, true>::__uniq_ptr_data(K*)"
        )
        assert demangle.demangle_strict(mangled, style="gnu") == (
            "std::__uniq_ptr_data<K, std::default_delete<K>, true, true>::__uniq_ptr_data(K*)"
        )


class TestAStructorMayCarryAbiTags:
    """`<ctor-dtor-name> [<abi-tags>]`.

    libc++ 18 tags its destructors -- the `[abi:ne180100]` every `_LIBCPP_HIDE_FROM_ABI`
    member carries -- so the destructor's branch reads the tags as the constructor's
    does; nine destructors in `libc++.a` depend on it. Both references read all of
    these and spell them alike.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_ZN1AD2B3tagEv", "A::~A[abi:tag]()"),
            ("_ZN1AD0B3tagB4tag2Ev", "A::~A[abi:tag][abi:tag2]()"),
            ("_ZN1AC1B3tagEv", "A::A[abi:tag]()"),
            (
                "_ZNSt3__111unique_lockINS_12shared_mutexEED2B8ne180100Ev",
                "std::__1::unique_lock<std::__1::shared_mutex>::~unique_lock[abi:ne180100]()",
            ),
        ],
    )
    def test_the_tags_follow_the_name(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="itanium") == expected
        assert demangle.demangle_strict(mangled, style="gnu") == expected

    def test_after_an_inheriting_constructors_base_the_tags_are_the_bases(self):
        """`CI2 1A B3tag`: the `B3tag` is read as the base type's own tag, since a
        `<source-name>` takes tags, and the constructor is left plain -- which is how
        llvm-cxxfilt reads it."""
        assert demangle.demangle_strict("_ZN1BCI21AB3tagEi", language="itanium") == "B::B(int)"


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
    putting it inside the brackets. A libstdc++ symbol whose `RKS4_` refers back to a
    function type in the template arguments reaches it.
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
    with nothing after it, refused by `llvm-cxxfilt` rather than spelled `id<15>` with
    the digits taken for the protocol; and `objcproto1ABC` is `A`, the rest of the
    qualifier unread."""

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
    that does not open on 1-9; the llvm style prints the digits it read."""

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
    reads and `llvm-cxxfilt` refuses. Both forms are read, since clang++ 18.1.3 emits
    the first. And AltiVec's `__vector pixel` is `Dv <number> _ p`, a `p`
    where the element type would be: clang++ 18.1.3 writes `_Z1hDv8_p` for
    `void h(__vector pixel)`, which `llvm-cxxfilt` spells `pixel vector[8]`."""

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


class TestAGroupAfterAStarIsTightUnderTheGnuStyle:
    """`d_print_function_type` writes the space before a declarator group's `(` unless
    the last character printed is `(` or `*`, so a pointer to a function returning a
    pointer to a function is `void (*(*)())()` to `c++filt` and `void (* (*)())()` to
    `llvm-cxxfilt`; a `&` keeps its space either way. Each style follows its own
    reference, Skia's `VulkanWindowContext` constructor being a real case."""

    @pytest.mark.parametrize(
        ("mangled", "llvm", "gnu"),
        [
            ("_Z1fPFPFvvEvE", "f(void (* (*)())())", "f(void (*(*)())())"),
            ("_Z1fPFPFPFvvEvEvE", "f(void (* (* (*)())())())", "f(void (*(*(*)())())())"),
            ("_Z1fPFPA3_ivE", "f(int (* (*)()) [3])", "f(int (*(*)()) [3])"),
            ("_Z1fPFM1SFvvEvE", "f(void (S::* (*)())())", "f(void (S::*(*)())())"),
            ("_Z1fPFRA3_ivE", "f(int (& (*)()) [3])", "f(int (& (*)()) [3])"),
            ("_Z1fPFPivE", "f(int* (*)())", "f(int* (*)())"),
            # An array's group is `d_print_array_type`'s, spaced whatever came before.
            ("_Z1fRA3_Pi", "f(int* (&) [3])", "f(int* (&) [3])"),
            ("_Z1fPA3_Pi", "f(int* (*) [3])", "f(int* (*) [3])"),
            (
                "_Z33can_interpret_as_conditional_op_pP6gimplePP9tree_nodeP9tree_codeRA3_S2_S3_",
                "can_interpret_as_conditional_op_p(gimple*, tree_node**, tree_code*, tree_node* (&) [3], tree_node**)",
                "can_interpret_as_conditional_op_p(gimple*, tree_node**, tree_code*, tree_node* (&) [3], tree_node**)",
            ),
            ("_Z1fPPFvvE", "f(void (**)())", "f(void (**)())"),
            (
                "_ZN6sk_app19VulkanWindowContextC1ERKNS_13DisplayParamsESt8functionIFP14VkSurfaceKHR_TP12VkInstance_TEES4_"
                "IFbS8_P18VkPhysicalDevice_TjEEPFPFvvES8_PKcE",
                "sk_app::VulkanWindowContext::VulkanWindowContext(sk_app::DisplayParams const&, "
                "std::function<VkSurfaceKHR_T* (VkInstance_T*)>, std::function<bool (VkInstance_T*, VkPhysicalDevice_T*, "
                "unsigned int)>, void (* (*)(VkInstance_T*, char const*))())",
                "sk_app::VulkanWindowContext::VulkanWindowContext(sk_app::DisplayParams const&, "
                "std::function<VkSurfaceKHR_T* (VkInstance_T*)>, std::function<bool (VkInstance_T*, VkPhysicalDevice_T*, "
                "unsigned int)>, void (*(*)(VkInstance_T*, char const*))())",
            ),
        ],
    )
    def test_the_two_styles(self, mangled, llvm, gnu):
        assert demangle.demangle(mangled) == llvm
        assert demangle.demangle(mangled, style="gnu") == gnu


class TestAPointerToAMemberOfArrayType:
    """`PointerToMemberType::printLeft` writes its `(` straight after the member type
    where `PointerType::printLeft` writes a space first, so llvm-cxxfilt spells
    `int(A::*) [3]` beside `int (*) [3]`; GNU c++filt spaces both. Each style follows
    its own reference."""

    @pytest.mark.parametrize(
        "mangled, llvm, gnu",
        [
            ("_Z1fM1AA3_i", "f(int(A::*) [3])", "f(int (A::*) [3])"),
            ("_Z1fM1AA3_A2_i", "f(int(A::*) [3][2])", "f(int (A::*) [3][2])"),
            ("_Z1fM1AKA3_i", "f(int const(A::*) [3])", "f(int const (A::*) [3])"),
            ("_Z1fM1AA3_PFvvE", "f(void (*(A::*) [3])())", "f(void (* (A::*) [3])())"),
            ("_Z1fRM1AA3_i", "f(int(A::*&) [3])", "f(int (A::*&) [3])"),
            ("_Z1fM1AA3_S_", "f(A(A::*) [3])", "f(A (A::*) [3])"),
            # A pointer to an array and a pointer to a member function keep their space.
            ("_Z1fPA3_i", "f(int (*) [3])", "f(int (*) [3])"),
            ("_Z1fM1AFvvE", "f(void (A::*)())", "f(void (A::*)())"),
            ("_Z1fM1APA3_i", "f(int (* A::*) [3])", "f(int (* A::*) [3])"),
        ],
    )
    def test_both_styles(self, mangled, llvm, gnu):
        assert demangle.demangle_strict(mangled) == llvm
        assert demangle.demangle_strict(mangled, style="gnu") == gnu


class TestAnArgumentPackWrittenWithI:
    """`I <template-arg>* E` where an argument stands is an argument pack: the form g++
    wrote under `-fabi-version` 2 through 5, the default of GCC 3.4 through 4.9, and
    still writes as a compatibility alias beside the `J` form when asked for those
    versions -- `_Z1fIIicEEvDpT_` and `_Z1fIJicEEvDpT_` both, for `f(1, 'c')` over
    `template <class... T> void f(T...)`. libiberty reads `I` and `J` alike;
    `llvm-cxxfilt` 18 and 20 refuse the older form. Every spelling here is `c++filt`
    2.42's under the gnu style, and the `J` form's under the llvm style."""

    @pytest.mark.parametrize(
        "mangled, llvm, gnu",
        [
            ("_Z1fIIicEEvDpT_", "void f<int, char>(int, char)", "void f<int, char>(int, char)"),
            ("_Z1fIIiEEvDpT_", "void f<int>(int)", "void f<int>(int)"),
            ("_Z1fIIEEvv", "void f<>()", "void f<>()"),
            ("_Z1fIiIicEEvT_DpT0_", "void f<int, int, char>(int, int, char)", "void f<int, int, char>(int, int, char)"),
            ("_Z1fIIicEEvPFvDpT_E", "void f<int, char>(void (*)(int, char))", "void f<int, char>(void (*)(int, char))"),
            (
                "_Z2f1IIicEEDTcl1gspfp_EEDpT_",
                "decltype(g(fp...)) f1<int, char>(int, char)",
                "decltype (g({parm#1}...)) f1<int, char>(int, char)",
            ),
        ],
    )
    def test_both_styles(self, mangled, llvm, gnu):
        assert demangle.demangle_strict(mangled) == llvm
        assert demangle.demangle_strict(mangled, style="gnu") == gnu

    def test_the_j_form_reads_the_same(self):
        assert demangle.demangle_strict("_Z1fIJicEEvDpT_") == demangle.demangle_strict("_Z1fIIicEEvDpT_")


class TestAnObjectiveCProtocolUnderTheGnuStyle:
    """GNU c++filt knows nothing of the `objcproto` convention and prints the qualifier
    as it prints any vendor qualifier, after the type: `objc_object objcproto3Bar*`
    where llvm-cxxfilt writes `id<Bar>`. The names were compiled by Clang 18 from
    Objective-C++ `id<Bar>` and `Foo<Bar>*` parameters."""

    @pytest.mark.parametrize(
        "mangled, gnu, llvm",
        [
            ("_Z1fPU13objcproto3Bar11objc_object", "f(objc_object objcproto3Bar*)", "f(id<Bar>)"),
            ("_Z1fPU13objcproto3Bar3Foo", "f(Foo objcproto3Bar*)", "f(Foo<Bar>*)"),
            ("_Z1fU11objcproto1A11objc_object", "f(objc_object objcproto1A)", "f(objc_object<A>)"),
        ],
    )
    def test_both_styles(self, mangled, gnu, llvm):
        assert demangle.demangle_strict(mangled, style="gnu") == gnu
        assert demangle.demangle_strict(mangled) == llvm

    def test_the_option_is_what_selects_it(self):
        on = demangle.style("llvm", itanium={"gnu_objc_protocol_spelling": True})
        assert (
            demangle.demangle_strict("_Z1fPU13objcproto3Bar11objc_object", style=on) == "f(objc_object objcproto3Bar*)"
        )


class TestAConstructorOfAnUnnamedType:
    """A closure or unnamed type has no name for its constructor or destructor to
    repeat. llvm-cxxfilt prints none, `A::'unnamed'::~()`; libiberty names it after the
    last source name it read, template arguments aside, so c++filt prints
    `A::{unnamed type#1}::~A()` and `std::vector<X>::{unnamed type#1}::~vector()`, and
    ICU ships `MicroProps::{unnamed type#1}::~MicroProps()`. A parameter type inside
    the closure's signature is a name of its own, and reading it must not clear the
    flag that says the scope has none, or `_ZN1AUlN1XEE_D1Ev` would come back as
    `A::'lambda'(X)::~'lambda'(X)()`."""

    @pytest.mark.parametrize(
        "mangled, llvm, gnu",
        [
            ("_ZN1AUt_D1Ev", "A::'unnamed'::~()", "A::{unnamed type#1}::~A()"),
            ("_ZN1AUt_C1Ev", "A::'unnamed'::()", "A::{unnamed type#1}::A()"),
            ("_ZN1AUt0_C2Ev", "A::'unnamed0'::()", "A::{unnamed type#2}::A()"),
            ("_ZN1A1BUt_D1Ev", "A::B::'unnamed'::~()", "A::B::{unnamed type#1}::~B()"),
            ("_ZN1AUlvE_D1Ev", "A::'lambda'()::~()", "A::{lambda()#1}::~A()"),
            ("_ZN1AUlN1XEE_D1Ev", "A::'lambda'(X)::~()", "A::{lambda(X)#1}::~X()"),
            ("_ZN1AI1XEUt_D1Ev", "A<X>::'unnamed'::~()", "A<X>::{unnamed type#1}::~A()"),
            (
                "_ZNSt6vectorI1XEUt_D1Ev",
                "std::vector<X>::'unnamed'::~()",
                "std::vector<X>::{unnamed type#1}::~vector()",
            ),
            (
                "_ZN6icu_746number4impl10MicroPropsUt_D1Ev",
                "icu_74::number::impl::MicroProps::'unnamed'::~()",
                "icu_74::number::impl::MicroProps::{unnamed type#1}::~MicroProps()",
            ),
            ("_ZN1AcviD0Ev", "A::operator int::~()", "A::operator int::~A()"),
            # A named scope after the unnamed one is its own name.
            ("_ZN1AUt_1BD1Ev", "A::'unnamed'::B::~B()", "A::{unnamed type#1}::B::~B()"),
        ],
    )
    def test_both_styles(self, mangled, llvm, gnu):
        assert demangle.demangle_strict(mangled) == llvm
        assert demangle.demangle_strict(mangled, style="gnu") == gnu


class TestAnExceptionSpecificationComesFirstToCxxfilt:
    """c++filt writes a function type's exception specification before its qualifiers,
    `void (A::*)() noexcept const &`; llvm-cxxfilt writes it last. libstdc++ 13's
    `<chrono>` ships the member pointer `KDoF...E` in every `time_zone` sort."""

    @pytest.mark.parametrize(
        "mangled, llvm, gnu",
        [
            ("_Z1fM1AKDoFvvE", "f(void (A::*)() const noexcept)", "f(void (A::*)() noexcept const)"),
            ("_Z1fM1AKDoFvvRE", "f(void (A::*)() const & noexcept)", "f(void (A::*)() noexcept const &)"),
            ("_Z1fM1AVKDoFvvE", "f(void (A::*)() const volatile noexcept)", "f(void (A::*)() noexcept const volatile)"),
            ("_Z1fM1AKDwiEFvvE", "f(void (A::*)() const throw(int))", "f(void (A::*)() throw(int) const)"),
            ("_Z1fM1AKDOLi1EEFvvE", "f(void (A::*)() const noexcept(1))", "f(void (A::*)() noexcept(1) const)"),
            (
                "_Z1fM1AKDxFvvRE",
                "f(void (A::*)() const & transaction_safe)",
                "f(void (A::*)() transaction_safe const &)",
            ),
            ("_Z1fPKDoFvvE", "f(void (*)() const noexcept)", "f(void (*)() noexcept const)"),
            ("_Z1fM1ADoFvvRE", "f(void (A::*)() & noexcept)", "f(void (A::*)() noexcept &)"),
            ("_Z1fM1ADoFvvE", "f(void (A::*)() noexcept)", "f(void (A::*)() noexcept)"),
        ],
    )
    def test_both_styles(self, mangled, llvm, gnu):
        assert demangle.demangle_strict(mangled) == llvm
        assert demangle.demangle_strict(mangled, style="gnu") == gnu

    def test_the_option_is_what_selects_it(self):
        on = demangle.style("llvm", itanium={"gnu_exception_spec_first": True})
        assert demangle.demangle_strict("_Z1fM1AKDoFvvE", style=on) == "f(void (A::*)() noexcept const)"


class TestADependentElaboratedTypeSpecifier:
    """`<class-enum-type> ::= Ts <name> | Tu <name> | Te <name>`.

    What a writer had to spell out because the type is dependent: `struct T::Inner`.
    Compiled rather than taken from a table -- clang 18 on

        template <class T> void f(struct T::Inner*) {}
        struct Host { struct Inner {}; union Un { int a; }; enum En { E0 }; };
        void use() { f<Host>(nullptr); }

    writes `_Z1fI4HostEvPTsNT_5InnerE`, and the union and enum forms alongside it.
    `c++filt` 2.42 refuses all three; `llvm-cxxfilt` reads them and spells them as here.

    The whole of `<name>` stands after the keyword. A `<source-name>` opens with its
    length, which must not be read as the index of a `Ts <index> _` marker on a
    `<template-param>`, or `Ts3Foo` would be refused. That marker is not a production; see
    `test_a_parameter_carries_no_pack_marker`.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # clang 18, `-std=c++17`, read out of the object file with `nm`.
            ("_Z1fI4HostEvPTsNT_5InnerE", "void f<Host>(struct Host::Inner*)"),
            ("_Z1gI4HostEvPTuNT_2UnE", "void g<Host>(union Host::Un*)"),
            ("_Z1hI4HostEvPTeNT_2EnE", "void h<Host>(enum Host::En*)"),
            # The rest of `<name>`: a plain source-name, one carrying template
            # arguments, and the `St` abbreviation.
            ("_Z1fTs3Foo", "f(struct Foo)"),
            ("_Z1fTu3Foo", "f(union Foo)"),
            ("_Z1fTe3Foo", "f(enum Foo)"),
            ("_Z1fTs3FooIiE", "f(struct Foo<int>)"),
            ("_Z1fTsSt3Foo", "f(struct std::Foo)"),
            ("_Z1fTsN3Foo3BarE", "f(struct Foo::Bar)"),
            # And the specifier is a substitution candidate like any other type.
            ("_Z1fTs3FooS_", "f(struct Foo, struct Foo)"),
        ],
    )
    def test_the_specifier_is_read(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="itanium") == expected

    @pytest.mark.parametrize("mangled", ["_Z1fIiEvTp_", "_Z1fIiEvTs_", "_Z1fIJiEEvDpTs_", "_Z1fIJiEEvDpTp_"])
    def test_a_parameter_carries_no_pack_marker(self, mangled):
        """`<template-param>` is `T_`, `T <index> _` or the `TL` level form, and nothing
        else.

        A `p` and an `s` are not markers on the parameter. Neither is a production:
        `Tp` introduces a `<template-param-decl>`, which `_PARAMETER_DECLARATIONS`
        records as unconfusable with a `<template-param>` and which
        `template_param_decl` reads, and `Ts` opens the elaborated specifier above.
        Reading them as markers would make each of these a second mangling of
        `_Z1fIiEvT_`, `void f<int>(int)`, which is the one answer worse than none. Both
        references refuse every one, no compiler writes them, and there is no
        `T[ps]<index>_` among the checked-in symbols.
        """
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled, language="itanium")
        assert demangle.demangle(mangled) == mangled

    def test_the_parameter_forms_that_are_productions_still_read(self):
        assert demangle.demangle_strict("_Z1fIiEvT_") == "void f<int>(int)"
        assert demangle.demangle_strict("_Z1fIJiEEvDpT_") == "void f<int>(int)"


class TestAConstrainedPlaceholderIsASubstitutionCandidate:
    """`Dk <type-constraint>` and `DK <type-constraint>` are `<type>` productions, and
    5.1.10 makes every `<type>` that is not a builtin a substitution candidate.

    `llvm-cxxfilt` 18.1.3 records nothing for either, which is one entry short: probing
    `_Z1fDKN1A1BE` with `tools/probe_substitutions.py` gives `S_` as `A` to both and
    `S0_` as `A::B decltype(auto)` here and out of range there. One entry moves every
    later back-reference in the name, so the disagreement is not confined to names that
    refer back to the placeholder itself.

    The omission is these two codes and not a rule that reference holds about compound
    types: it records the composite for `Dv2_i` and for `Dpi`, both probed the same way.
    Its `DB` shows the same omission -- `_Z6myfuncRDB8_S0_` is `myfunc(_BitInt(8)&,
    _BitInt(8)&)` in libcxxabi's own corpus, and the shipped `llvm-cxxfilt` 18 refuses
    that vector. Neither `Dk`
    nor `DK` is emitted by g++ 13.3 or clang++ 18.1.3 -- both write `Tk` in the
    `<template-param-decl>` instead -- so no compiler output settles it and the ABI's
    own grammar is the whole of the evidence.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fDkN1A1BE", "f(A::B auto)"),
            ("_Z1fDKN1A1BE", "f(A::B decltype(auto))"),
            # The prefixes of the constraint name come first, then the composite.
            ("_Z1fDkN1A1BES_", "f(A::B auto, A)"),
            ("_Z1fDkN1A1BES0_", "f(A::B auto, A::B auto)"),
            ("_Z1fDKN1A1BES_", "f(A::B decltype(auto), A)"),
            ("_Z1fDKN1A1BES0_", "f(A::B decltype(auto), A::B decltype(auto))"),
            # A three-level constraint contributes its two prefixes and then the type,
            # so the complete name is reachable only through the placeholder that holds
            # it: `A::B::C` alone is a <type-constraint> here and not a <type>.
            ("_Z1fDkN1A1B1CES1_", "f(A::B::C auto, A::B::C auto)"),
        ],
    )
    def test_the_placeholder_is_entered_after_its_constraints_prefixes(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="itanium") == expected

    def test_one_entry_past_the_placeholder_is_out_of_range(self):
        assert demangle.demangle("_Z1fDkN1A1BES1_") == "_Z1fDkN1A1BES1_"


class TestABackReferenceToAPackBoundParameter:
    """The entry a `<template-param>` contributes is the parameter, and a parameter bound
    to a pack is the pack.

    That is the same rule note 17 of CONFORMANCE.md establishes against the compilers'
    output for the unpacked case -- the entry is the parameter, not the argument bound to
    it where the entry was made -- and a pack parameter is not a different kind of
    parameter. `_Z1fIiJbcdEEvT_DpT0_` makes the three readings visible side by side:
    entry one is what `T0_` contributed and entry two is the `Dp` expansion's own. All
    three demanglers agree entry two is the whole pack. On entry one, `llvm-cxxfilt` 18
    says `bool` -- the first member -- and `c++filt` 2.42 says `double` -- the last --
    while this says the pack, and reads a pack standing where one type goes as one type
    per member, which is what `TestAnArgumentPackWrittenWithI` pins elsewhere.

    Two references that disagree with each other about which member to record are not a
    second opinion about whether to record one; no compiler writes a back-reference to
    such an entry, and `tools/enumerate.py` accepts the disagreement on the shape rather
    than on either answer.
    """

    NAME = "_Z1fIiJbcdEEvT_DpT0_"
    SIGNATURE = "void f<int, bool, char, double>(int, bool, char, double"

    def test_the_pack_parameters_entry_is_the_pack(self):
        assert demangle.demangle_strict(self.NAME + "S1_") == f"{self.SIGNATURE}, bool, char, double)"

    def test_the_expansions_own_entry_is_the_pack_too_and_all_three_agree(self):
        assert demangle.demangle_strict(self.NAME + "S2_") == f"{self.SIGNATURE}, bool, char, double)"

    def test_the_entries_before_it_are_the_template_name_and_the_first_argument(self):
        assert demangle.demangle_strict(self.NAME + "S0_") == f"{self.SIGNATURE}, int)"
        # `S_` is the template name, which a `<type>` may not name without its arguments.
        assert demangle.demangle(self.NAME + "S_") == self.NAME + "S_"

    def test_one_entry_past_the_expansion_is_out_of_range(self):
        assert demangle.demangle(self.NAME + "S3_") == self.NAME + "S3_"


class TestATemplateTemplateParameterTakesAnEntryOfItsOwn:
    """`T_ I ... E` is two grammar components, and 5.1.10 makes each a candidate.

    The parameter is one -- `<template-template-param>` is named in the list -- and the
    specialisation built over it is a `<type>`, so the application contributes two
    entries. `_Z1gI1A1BEvT_IT0_E` shows all six: `g`, `A`, `B`, then `T_` (which is `A`),
    `T0_` (which is `B`), then `A<B>`. `c++filt` 2.42 agrees entry by entry.
    `llvm-cxxfilt` 18 records the parameter's alone, so its table is one short and every
    index at or after it names something else.

    Settled against the manglers rather than a demangler, and both write the shape
    without being asked for anything unusual: for `f(C<T>, C<T>, C<T>)` g++ 13.3.0 and
    clang++ 18.1.3 both emit `_Z1fI1AiEvT_IT0_ES3_S3_`, which the reference refuses
    because the shift runs off the end of its table, and for a `g(C<T>, C<int>)` beside
    it `_Z1gI1AcEvT_IT0_ES1_IiE`, which it answers `char<int>`.
    `tools/corpus_sources/reference_defects/template_template_param.cpp` is the source
    and `tests/conformance/itanium-reference-defects.txt` pins both against it.
    """

    NAME = "_Z1gI1A1BEvT_IT0_E"

    @pytest.mark.parametrize(
        ("token", "entry"),
        [("S0_", "A"), ("S1_", "B"), ("S2_", "A"), ("S3_", "B"), ("S4_", "A<B>")],
    )
    def test_each_entry(self, token, entry):
        assert demangle.demangle_strict(self.NAME + token) == f"void g<A, B>(A<B>, {entry})"

    def test_one_past_the_last_is_out_of_range(self):
        assert demangle.demangle(self.NAME + "S5_") == self.NAME + "S5_"

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fI1AiEvT_IT0_ES3_S3_", "void f<A, int>(A<int>, A<int>, A<int>)"),
            ("_Z1gI1AcEvT_IT0_ES1_IiE", "void g<A, char>(A<char>, A<int>)"),
        ],
    )
    def test_the_names_both_compilers_emit(self, mangled, expected):
        assert demangle.demangle_strict(mangled) == expected


class TestASourceNameLengthWrittenWithALeadingZero:
    """`<source-name>` is a *positive length number* and an identifier, and a number in
    these grammars has no leading zero -- so `01A` is not one.

    The two references split on it. `c++filt` 2.42 reads `_Z1f01A` as `f(A)`, because
    libiberty's `d_number` consumes digits and calls `atoi`; `llvm-cxxfilt` 18 refuses
    the name outright. This reads it as `c++filt` does, which is the side it takes on the
    legacy `I ... E` argument pack and on the old `sr` form as well, and no compiler
    writes one, so there is nothing to settle the split against.
    An array *bound* is a different production and is printed as it is written: `A01_i`
    is `int [01]` to all three.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1f01A", "f(A)"),
            ("_Z1f001A", "f(A)"),
            ("_Z1f02AB", "f(AB)"),
            ("_Z1fN01A1BE", "f(A::B)"),
            ("_Z01fv", "f()"),
            ("_ZN01A1BE", "A::B"),
        ],
    )
    def test_the_zero_is_padding_and_the_name_reads(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="itanium") == expected

    def test_a_length_of_zero_is_still_refused(self):
        assert demangle.demangle("_Z1f0v") == "_Z1f0v"

    def test_an_array_bound_keeps_its_zero(self):
        assert demangle.demangle_strict("_Z1fA01_i") == "f(int [01])"


class TestAnInheritingConstructorsBaseType:
    """`CI2 <base class type>` names the base a constructor is inherited from, and the
    two compilers disagree about whether that type takes a substitution entry.

    It is a `<type>`, which 5.1.10 makes a candidate. g++ 13.3.0 enters it and writes
    `_ZN1DCI21CENS0_4KindES1_` -- `S0_` is that entry -- while clang++ 18.1.3 does not,
    and spells `C` again: `_ZN1DCI21CEN1C4KindES1_`, whose `S1_` is one entry further
    along. Both come from `struct D : C { using C::C; }` with `C(Kind, Kind)`, so both
    parameters are `C::Kind` in both names, and a reader applying the wrong rule prints
    the second one as `C`.

    Read by clang's rule, with a retry under g++'s that a run past the table triggers:
    a g++ name refers to the base's entry as the highest index in use where it stands,
    so that reference overruns and the retry catches it, while a clang name gives no
    signal at all and has to be right the first time. `llvm-cxxfilt` 18 implements
    clang's rule and refuses g++'s names; `c++filt` 2.42 reads both parameter lists and
    then names the constructor after the *base*, `D::C`, which is neither compiler's
    declaration. `tools/corpus_sources/reference_defects/inheriting_constructor.cpp` is
    the source and `tests/conformance/itanium-reference-defects.txt` pins all six
    against it.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # clang's numbering, which is the one read first.
            ("_ZN1DCI21CEN1C4KindE", "D::D(C::Kind)"),
            ("_ZN1DCI21CEN1C4KindES1_", "D::D(C::Kind, C::Kind)"),
            # g++'s, which the retry reaches.
            ("_ZN1DCI11CENS0_4KindE", "D::D(C::Kind)"),
            ("_ZN1DCI11CENS0_4KindES1_", "D::D(C::Kind, C::Kind)"),
            ("_ZN1DCI21CENS0_4KindE", "D::D(C::Kind)"),
            ("_ZN1DCI21CENS0_4KindES1_", "D::D(C::Kind, C::Kind)"),
            # The two the corpora already carried, neither of which refers back.
            ("_ZN1BCI21AEi", "B::B(int)"),
            ("_ZN1DCI21CIiEET_", "D::D(int)"),
        ],
    )
    def test_both_numberings_read(self, mangled, expected):
        assert demangle.demangle_strict(mangled, language="itanium") == expected

    def test_forcing_gplusplus_rule_reads_the_clang_name_as_the_wrong_type(self):
        """What the option is for, and why the default is the other way round."""
        forced = demangle.style("llvm", itanium={"inherited_constructor_substitution": True})
        assert demangle.demangle_strict("_ZN1DCI21CEN1C4KindES1_", style=forced) == "D::D(C::Kind, C)"
        assert demangle.demangle_strict("_ZN1DCI21CENS0_4KindES1_", style=forced) == "D::D(C::Kind, C::Kind)"

    def test_forcing_clangs_rule_refuses_the_gplusplus_name(self):
        forced = demangle.style("llvm", itanium={"inherited_constructor_substitution": False})
        assert demangle.demangle_strict("_ZN1DCI21CEN1C4KindES1_", style=forced) == "D::D(C::Kind, C::Kind)"
        with pytest.raises(DemanglingError):
            demangle.demangle_strict("_ZN1DCI21CENS0_4KindES1_", style=forced)
