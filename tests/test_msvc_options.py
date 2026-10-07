"""Printing less of a decorated name.

A decorated name expands to a great deal more than the name -- `public: virtual int
__cdecl C::f(int) const` is one identifier and five pieces of declaration around it --
and every peer tool lets a caller ask for less of it. `llvm-undname` takes flags;
`UnDecorateSymbolName` takes a mask with more in it than those flags. `MsvcOptions` is
both, and this is them replayed against the answers each reference gave.

Two corpora, because two references. `msvc-suppressions.txt` records what each
`llvm-undname` flag *changes* over the same 609 names `msvc-llvm-corpus.txt` pins
unchanged; `msvc-dbghelp.txt` does the same for the four mask bits `llvm-undname` has no
flag for. Nothing in either is this library's own output.

The last class here is not a score at all. It is the rest of the mask -- the bits that
have no `MsvcOptions` field -- and what it establishes is *why* each one has none, which
is a measurement rather than an omission.
"""

from dataclasses import fields
from typing import ClassVar

import demangle
from demangle.schemes.msvc.options import DEFAULT_OPTIONS, MsvcOptions

from .conftest import load_corpus
from .test_conformance import (
    MSVC_DBGHELP_EXACT,
    MSVC_DBGHELP_TOTAL,
    MSVC_NAME_ONLY_AGREE,
    MSVC_NAME_ONLY_LABELLED,
    MSVC_NAME_ONLY_REDUCES_A_NESTED_SYMBOL,
    MSVC_NAME_ONLY_TOTAL,
    MSVC_NAME_ONLY_WITHOUT_TAGS,
    MSVC_SUPPRESSIONS_EXACT,
    MSVC_SUPPRESSIONS_TOTAL,
    UNDNAME_INERT_BITS,
)

ALL_MSVC_OPTIONS = tuple(field.name for field in fields(MsvcOptions))


class TestPrintingLessOfADecoratedName:
    """`llvm-undname`'s five suppression flags, replayed as `MsvcOptions`.

    A decorated name expands to a great deal more than the name, and every peer tool lets
    a caller ask for less of it -- `UnDecorateSymbolName` takes a mask, `llvm-undname`
    takes flags. `tests/conformance/msvc-suppressions.txt` records what each flag changes
    over the same 609 names the corpus next door pins unchanged, so what is checked here
    is the reference's own answer under the reference's own flag.

    The rules are not "drop a word", and three of them are worth stating because they
    look like bugs until you see the reference do them:

    * A function reached as a *pointer's* pointee keeps its calling convention, because
      the pointer prints it rather than the signature. So `--no-calling-convention` over
      `int (__cdecl * __cdecl fn(void))(int)` drops one of the two and keeps the other,
      and a parameter of function-pointer type keeps its own throughout.
    * A symbol naming a *scope* keeps its full spelling: *these* flags apply to the symbol
      being named, not to the ones saying where it lives. The four in the class below do
      reach it, because their reference does.
    * `extern "C" ` goes with `static` and `virtual` rather than with the access
      specifier, so `--no-member-type` drops all three together.
    """

    FLAGS: ClassVar = {
        "no-calling-convention": "calling_convention",
        "no-access-specifier": "access_specifier",
        "no-member-type": "member_type",
        "no-return-type": "return_type",
        "no-variable-type": "variable_type",
    }

    #: The reference truncates these three rather than spelling them, so they are pinned
    #: as divergences instead of copied. See `test_the_shortfall_is_the_references_own`.
    TRUNCATED: ClassVar = frozenset(
        {
            "?memptrtofun7@@3R8B@@EAAP6AHXZXZEQ1@",
            "?memptrtofun8@@3P8B@@EAAR6AHXZXZEQ1@",
            "?memptrtofun9@@3P8B@@EAAQ6AHXZXZEQ1@",
        }
    )

    def _rows(self):
        for mangled, rest in load_corpus("msvc-suppressions.txt"):
            flag, expected = rest.split("\t", 1)
            yield mangled, flag, expected

    def _score(self):
        return sum(
            1
            for mangled, flag, expected in self._rows()
            if demangle.demangle(mangled, style=demangle.style("llvm", msvc={self.FLAGS[flag]: False})) == expected
        )

    def test_the_score_has_not_gone_backwards(self):
        assert len(list(self._rows())) == MSVC_SUPPRESSIONS_TOTAL, "corpus did not load"
        assert self._score() >= MSVC_SUPPRESSIONS_EXACT

    def test_the_pinned_number_is_still_accurate(self):
        assert self._score() == MSVC_SUPPRESSIONS_EXACT

    def test_the_shortfall_is_the_references_own_truncation(self):
        """Three names where `--no-return-type` leaves `llvm-undname` with an open bracket.

        `int (__cdecl * (__cdecl B::*volatile memptrtofun7)(void)` is not a declaration of
        anything -- the reference suppressed the outer return type and lost the `)(void)`
        that closed it. These keep the balanced spelling, which is also what the reference
        itself prints with no flag.
        """
        missed = {
            mangled
            for mangled, flag, expected in self._rows()
            if demangle.demangle(mangled, style=demangle.style("llvm", msvc={self.FLAGS[flag]: False})) != expected
        }
        assert missed == self.TRUNCATED

    def test_every_flag_is_exercised(self):
        """A flag that changed nothing would score a silent 0/0."""
        seen = {flag for _, flag, _ in self._rows()}
        assert seen == set(self.FLAGS)

    def test_the_default_is_to_print_everything(self):
        """Every field defaults True, so the corpus next door still pins the same text."""
        assert demangle.demangle("?g@C@@UEAAXXZ") == "public: virtual void __cdecl C::g(void)"
        assert demangle.demangle("?g@C@@UEAAXXZ", style=demangle.style("llvm")) == (
            "public: virtual void __cdecl C::g(void)"
        )

    def test_the_flags_compose(self):
        narrow = demangle.style("llvm", msvc={"calling_convention": False, "access_specifier": False})
        assert demangle.demangle("?g@C@@UEAAXXZ", style=narrow) == "virtual void C::g(void)"

    def test_the_tree_spells_what_the_text_spells_under_a_flag(self):
        narrow = demangle.style("llvm", msvc={"calling_convention": False})
        for mangled, flag, _ in self._rows():
            if flag != "no-calling-convention":
                continue
            assert demangle.parse(mangled, style=narrow).spell(style=narrow) == demangle.demangle(
                mangled, style=narrow
            ), mangled


class TestTheMaskBitsLlvmUndnameHasNoFlagFor:
    """`UnDecorateSymbolName`'s four other suppressions, replayed as `MsvcOptions`.

    Microsoft's mask has considerably more in it than `llvm-undname`'s five flags, and for
    the rest there is one reference and it runs on Windows. `dbghelp.dll` is asked, once
    per bit, over the same 609 names; `tests/conformance/msvc-dbghelp.txt` records what
    each bit changed, and `tools/generate_msvc_dbghelp_corpus.py` says how the reference's
    spelling is rewritten into this one -- and proves, name by name -- before it is
    written down.

    These four do not reach the way the five next door reach, and the difference is
    measured rather than assumed. `llvm-undname`'s five are declaration-level: they apply
    to the declaration and to the types written inside it, and stop at the edge of the
    symbol. `UnDecorateSymbolName`'s four are lexical: they reach every occurrence of what
    they name, including the ones in a template argument, in a parameter of
    function-pointer type, and in the enclosing symbol a local name is scoped by. So
    `--no-calling-convention` over `int (__cdecl * __cdecl fn(void))(int)` drops one of the
    two conventions and `ms_keywords` drops both.
    """

    FLAGS: ClassVar = {
        "no-leading-underscores": "leading_underscores",
        "no-ms-keywords": "ms_keywords",
        "no-this-type": "this_type",
        "no-tag-kind": "tag_kind",
    }

    def _rows(self):
        for mangled, rest in load_corpus("msvc-dbghelp.txt"):
            flag, expected = rest.split("\t", 1)
            yield mangled, flag, expected

    def _score(self):
        return sum(
            1
            for mangled, flag, expected in self._rows()
            if demangle.demangle(mangled, style=demangle.style("llvm", msvc={self.FLAGS[flag]: False})) == expected
        )

    def test_the_pinned_number_is_still_accurate(self):
        assert len(list(self._rows())) == MSVC_DBGHELP_TOTAL, "corpus did not load"
        assert self._score() == MSVC_DBGHELP_EXACT

    def test_every_flag_is_exercised(self):
        """A flag that changed nothing would score a silent 0/0."""
        assert {flag for _, flag, _ in self._rows()} == set(self.FLAGS)

    def test_the_tree_spells_what_the_text_spells_under_each_flag(self):
        for flag, field in self.FLAGS.items():
            narrow = demangle.style("llvm", msvc={field: False})
            for mangled, row_flag, _ in self._rows():
                if row_flag != flag:
                    continue
                assert demangle.parse(mangled, style=narrow).spell(style=narrow) == demangle.demangle(
                    mangled, style=narrow
                ), (mangled, flag)

    def test_the_defaults_leave_every_spelling_alone(self):
        """All four default True, so the corpora next door still pin the same text."""
        every = demangle.style("llvm", msvc=dict.fromkeys(self.FLAGS.values(), True))
        for mangled, _, _ in self._rows():
            assert demangle.demangle(mangled, style=every) == demangle.demangle(mangled), mangled

    def test_a_keyword_is_dropped_wherever_it_stands(self):
        """The point of `ms_keywords`: a function-pointer parameter loses its own too.

        `calling_convention` is the declaration's own, which is why the two are separate
        fields rather than one field with a wider reach.
        """
        name = "?h3@@YAP6APAHPAH0@ZP6APAH00@Z10@Z"
        declaration_only = demangle.style("llvm", msvc={"calling_convention": False})
        everywhere = demangle.style("llvm", msvc={"ms_keywords": False})
        assert demangle.demangle(name, style=declaration_only) == (
            "int * (__cdecl * h3(int * (__cdecl *)(int *, int *), "
            "int * (__cdecl *)(int *, int *), int *))(int *, int *)"
        )
        assert demangle.demangle(name, style=everywhere) == (
            "int * (*h3(int * (*)(int *, int *), int * (*)(int *, int *), int *))(int *, int *)"
        )

    def test_a_scope_loses_the_four_and_keeps_the_five(self):
        """Each flag reaches into an enclosing symbol exactly as far as its own
        reference does.

        `?NS@?1??SN@?$NS@H@0@QEAAHXZ@4HA` is a static local of a member function, so its
        spelling holds a whole second symbol. `llvm-undname` leaves that one alone under
        `--no-access-specifier` and `UnDecorateSymbolName` does not under
        `UNDNAME_NO_MS_KEYWORDS`; both are followed, each for its own flag. See
        `UNDNAME_REACH_DIVERGENCES` in tests/test_conformance.py.
        """
        name = "?NS@?1??SN@?$NS@H@0@QEAAHXZ@4HA"
        assert demangle.demangle(name) == "int `public: int __cdecl NS::NS<int>::SN(void)'::`2'::NS"
        kept = demangle.style("llvm", msvc={"access_specifier": False})
        assert demangle.demangle(name, style=kept) == "int `public: int __cdecl NS::NS<int>::SN(void)'::`2'::NS"
        lost = demangle.style("llvm", msvc={"ms_keywords": False})
        assert demangle.demangle(name, style=lost) == "int `public: int NS::NS<int>::SN(void)'::`2'::NS"

    def test_this_type_drops_the_whole_group_and_not_just_the_cv(self):
        """`const`, `__restrict`, `__unaligned` and the ref-qualifier all describe `this`."""
        without = demangle.style("llvm", msvc={"this_type": False})
        for mangled, spelled in (
            ("?f@S@@QEGBAHXZ", "public: int __cdecl S::f(void) const &"),
            ("?f@S@@QEIAAHXZ", "public: int __cdecl S::f(void) __restrict"),
            ("?f@S@@QEFAAHXZ", "public: int __cdecl S::f(void) __unaligned"),
        ):
            assert demangle.demangle(mangled) == spelled
            assert demangle.demangle(mangled, style=without) == "public: int __cdecl S::f(void)"

    def test_a_suppressed_convention_takes_its_space_and_a_missing_one_does_not(self):
        """`int ( *)()` is a convention spelled with nothing; `int (*)()` is one dropped.

        The reference prints the first with the space its convention would have filled and
        the second without it, which is the one place in the scheme where "spelled with
        nothing" and "not spelled at all" come out differently.
        """
        assert demangle.demangle("?f@@YAXP6ZHXZ@Z") == "void __cdecl f(int ( *)(void))"
        name = "?foo_p6ahxz@@YAXP6AHXZ@Z"
        assert demangle.demangle(name) == "void __cdecl foo_p6ahxz(int (__cdecl *)(void))"
        without = demangle.style("llvm", msvc={"ms_keywords": False})
        assert demangle.demangle(name, style=without) == "void foo_p6ahxz(int (*)(void))"

    def test_llvms_attribute_spelling_keeps_its_underscores(self):
        """`leading_underscores` strips a Microsoft keyword, and that is not one.

        The reference spells the two Swift conventions `__swift_1` and `__swift_3`, and
        strips those to `swift_1` and `swift_3`. It has no answer for LLVM's spelling,
        which is what this library prints, so the underscores stay rather than being taken
        off a word the reference never wrote. Dropping the convention outright is not
        ambiguous, and is what `ms_keywords` does.
        """
        name = "?swift_func@@YSXXZ"
        assert demangle.demangle(name) == "void __attribute__((__swiftcall__)) swift_func(void)"
        stripped = demangle.style("llvm", msvc={"leading_underscores": False})
        assert demangle.demangle(name, style=stripped) == "void __attribute__((__swiftcall__)) swift_func(void)"
        dropped = demangle.style("llvm", msvc={"ms_keywords": False})
        assert demangle.demangle(name, style=dropped) == "void swift_func(void)"

    def test_int64_is_a_types_name_rather_than_a_keyword(self):
        """Both flags leave it alone, which is what the reference does."""
        for field in ("ms_keywords", "leading_underscores"):
            narrow = demangle.style("llvm", msvc={field: False})
            assert demangle.demangle("?f@@YAX_J@Z", style=narrow).endswith("f(__int64)")

    def test_the_four_compose_with_the_five(self):
        narrow = demangle.style(
            "llvm", msvc={"tag_kind": False, "calling_convention": False, "access_specifier": False}
        )
        assert demangle.demangle("?f@@YAXPEAU?$C@H@@@Z", style=narrow) == "void f(C<int> *)"

    def test_all_nine_at_once_over_the_whole_corpus(self):
        """Nine fields is 512 combinations, and the interesting one is all of them.

        Each flag is scored on its own next door. What this adds is that composing them
        does not raise, does not leave the tree and the text disagreeing, and still spells
        something: a name reduced to nothing at all would pass every per-flag check.
        """
        every = demangle.style("llvm", msvc=dict.fromkeys(ALL_MSVC_OPTIONS, False))
        names = [mangled for mangled, _ in load_corpus("msvc-llvm-corpus.txt")]
        assert names, "corpus did not load"
        for mangled in names:
            text = demangle.demangle(mangled, style=every)
            assert text, mangled
            assert demangle.parse(mangled, style=every).spell(style=every) == text, mangled

    def test_a_run_leaving_nothing_out_is_the_default_object(self):
        """`for_a_scope` hands back the shared default rather than an equal copy of it."""
        assert MsvcOptions().for_a_scope() is DEFAULT_OPTIONS
        assert MsvcOptions(access_specifier=False).for_a_scope() is DEFAULT_OPTIONS
        assert MsvcOptions(tag_kind=False).for_a_scope() == MsvcOptions(tag_kind=False)

    def test_exactly_the_lexical_fields_survive_into_a_scope(self):
        """Read off the dataclass, so a field added without a decision fails here.

        A scope keeps the five `llvm-undname` flags at their defaults and carries the four
        `UnDecorateSymbolName` ones through, because each flag follows the reference that
        defines it. A tenth field would be carried or dropped by whichever branch of
        `for_a_scope` happens to name it, and silence is the wrong answer either way.
        """
        carried = {name for name in ALL_MSVC_OPTIONS if not getattr(MsvcOptions(**{name: False}).for_a_scope(), name)}
        assert carried == set(self.FLAGS.values())


class TestTheBitsWithNoFieldOfTheirOwn:
    """The rest of the mask, and why none of it became an `MsvcOptions` field.

    "We did not implement this" and "the reference does nothing with it" are different
    claims, and for nine of these bits only the second is true: `dbghelp` spells all 609
    names identically with the bit set and with it clear, so there is no answer to score
    a field against. The tenth, `UNDNAME_NAME_ONLY`, does plenty -- and what it does is
    measured below rather than followed. Re-derive either with
    `tools/generate_msvc_dbghelp_corpus.py --report`.
    """

    def test_the_inert_bits_are_recorded_with_their_values(self):
        """A finding has to be the documented values rather than a sketch of them."""
        pair = UNDNAME_INERT_BITS["UNDNAME_NO_MS_THISTYPE"] | UNDNAME_INERT_BITS["UNDNAME_NO_CV_THISTYPE"]
        assert pair == 0x60, "UNDNAME_NO_THISTYPE is the pair of these, and only the pair changes a spelling"
        assert len(set(UNDNAME_INERT_BITS.values())) == len(UNDNAME_INERT_BITS)
        assert not set(UNDNAME_INERT_BITS.values()) & {0x1, 0x2, 0x60, 0x8000}, "a bit cannot be both"

    def _named(self):
        no_tags = demangle.style("llvm", msvc={"tag_kind": False})
        for mangled, spelled in load_corpus("msvc-name-only.txt"):
            yield (
                mangled,
                spelled,
                demangle.signature(mangled).qualified_name,
                demangle.signature(mangled, style=no_tags).qualified_name,
            )

    def _groups(self):
        """Every name, sorted into the four ways `qualified_name` and the bit can relate."""
        groups = {"agree": [], "tags": [], "labelled": [], "nested": []}
        for mangled, spelled, ours, without_tags in self._named():
            if demangle.signature(mangled).special is not None:
                groups["labelled"].append(mangled)
            elif ours == spelled:
                groups["agree"].append(mangled)
            elif without_tags == spelled:
                groups["tags"].append(mangled)
            else:
                groups["nested"].append(mangled)
        return groups

    def test_name_only_is_not_the_qualified_name(self):
        """`UNDNAME_NAME_ONLY` reduces further than `signature().qualified_name` does.

        The bit is not quite reachable through `qualified_name`. The reference reduces
        every symbol *nested*
        inside the name as well -- the function a local name lives in, and a template
        argument that points at one, come back as bare names -- while `qualified_name`
        reads the qualified name off the tree and leaves what is nested inside it alone:

            UNDNAME_NAME_ONLY   `define_lambda'::`1'::<lambda_1>::operator()
            qualified_name      `int __cdecl define_lambda(void)'::`1'::<lambda_1>::operator()

        The four counts are the finding, taken over the names the two references spell
        alike unflagged so that what they measure is the mode differing rather than the two
        houses' typography. A name with a label -- `vftable`, an RTTI descriptor, a dynamic
        initialiser -- is its own group: `qualified_name` is the class or variable the
        label is about and `special` the label, where the reference prints both.
        """
        groups = self._groups()
        assert sum(len(names) for names in groups.values()) == MSVC_NAME_ONLY_TOTAL, "corpus did not load"
        assert len(groups["agree"]) == MSVC_NAME_ONLY_AGREE
        assert len(groups["tags"]) == MSVC_NAME_ONLY_WITHOUT_TAGS
        assert len(groups["labelled"]) == MSVC_NAME_ONLY_LABELLED
        assert len(groups["nested"]) == MSVC_NAME_ONLY_REDUCES_A_NESTED_SYMBOL

    def test_a_labelled_name_is_its_label_and_what_it_is_about(self):
        """Both of which the reference prints: `` Base::`vftable' `` is `Base` and `vftable`."""
        spelled_by_name = {mangled: spelled for mangled, spelled, _, _ in self._named()}
        for mangled in self._groups()["labelled"]:
            parts = demangle.signature(mangled)
            assert parts.base_name in spelled_by_name[mangled], mangled
            assert parts.special in spelled_by_name[mangled], mangled

    def test_the_bit_discards_a_vftables_base_path(self):
        """Which is why it has no field, rather than an oversight.

        `??_7A@B@@6BC@D@@@` and its two longer relatives name three different vtables, and
        `UNDNAME_NAME_ONLY` answers all three with `B::A::`vftable'`. That is the same loss
        `UNDNAME_DIVERGENCES` records `llvm-undname` making with no flag set at all, and it
        is refused here for the same reason: a tool labelling a binary would show three
        vtables as one. A caller who wants the name without the path can have it from
        `base_name` and `namespace`, which say what they are.
        """
        collapsed = {}
        for mangled, spelled, _, _ in self._named():
            collapsed.setdefault(spelled, []).append(mangled)
        for family in ("B::A::`vftable'", "A::`vftable'"):
            assert len(collapsed[family]) == 3, family
        assert len({demangle.demangle(mangled) for mangled in collapsed["B::A::`vftable'"]}) == 3

    def test_every_other_difference_is_the_reference_reducing_something(self):
        """Not a residue: in each one the reference printed strictly less than this does.

        41 names hold a symbol inside the name -- a template argument that points at one,
        or the function a local lives in -- which the reference reduces to a bare name and
        `qualified_name` spells out.
        """
        groups = self._groups()
        spelled_by_name = {mangled: spelled for mangled, spelled, _, _ in self._named()}
        without_tags = {mangled: without for mangled, _, _, without in self._named()}
        for mangled in groups["nested"]:
            assert len(without_tags[mangled]) > len(spelled_by_name[mangled]), mangled


class TestTheOptionsReachABareType:
    """`demangle_type(..., language="msvc")` spells a type under the style it was given.

    It is the entry point `UnDecorateSymbolName`'s `UNDNAME_TYPE_ONLY` corresponds to,
    and it must honour an options object: a flag silently inert there would leave every
    composition of the style without effect. The declaration-level five have nothing to
    say about most bare types -- a function reached as a pointer's pointee keeps its
    convention, and that is what a bare `P6AHXZ` is -- but the lexical four apply to a
    type exactly as they apply inside a declaration.
    """

    def test_a_tag_kind_is_dropped_from_a_bare_type(self):
        narrow = demangle.style("llvm", msvc={"tag_kind": False})
        assert demangle.demangle_type("PEAUS@@", language="msvc") == "struct S *"
        assert demangle.demangle_type("PEAUS@@", language="msvc", style=narrow) == "S *"

    def test_the_descriptor_spelling_of_the_same_encoding_agrees(self):
        """A type descriptor's own symbol writes a leading `.`, and reads the same type."""
        narrow = demangle.style("llvm", msvc={"tag_kind": False})
        assert demangle.demangle_type(".PEAUS@@", language="msvc", style=narrow) == "S *"

    def test_the_descriptor_symbol_reaches_the_flags_as_the_type_does(self):
        """`demangle(".?AVFoo@@")` goes through `parse`, which must hand `parse_msvc_type`
        its options as well as its limits, or every flag is inert on the symbol path
        while `demangle_type` honours it on the same encoding."""
        narrow = demangle.style("llvm", msvc={"tag_kind": False})
        assert demangle.demangle(".?AVFoo@@", language="msvc") == "class Foo `RTTI Type Descriptor Name'"
        assert demangle.demangle(".?AVFoo@@", language="msvc", style=narrow) == "Foo `RTTI Type Descriptor Name'"
        no_keywords = demangle.style("llvm", msvc={"ms_keywords": False})
        assert (
            demangle.demangle(".P6AHXZ", language="msvc", style=no_keywords)
            == "int (*`RTTI Type Descriptor Name')(void)"
        )

    def test_the_lexical_flags_reach_a_function_type(self):
        for field, spelled in (
            ("ms_keywords", "int (*)(void)"),
            ("leading_underscores", "int (cdecl *)(void)"),
        ):
            narrow = demangle.style("llvm", msvc={field: False})
            assert demangle.demangle_type("P6AHXZ", language="msvc", style=narrow) == spelled, field

    def test_this_type_reaches_the_qualifier_a_bare_member_function_type_carries(self):
        narrow = demangle.style("llvm", msvc={"this_type": False})
        assert demangle.demangle_type("$$A8@@BAHXZ", language="msvc") == "int __cdecl(void) const"
        assert demangle.demangle_type("$$A8@@BAHXZ", language="msvc", style=narrow) == "int __cdecl(void)"

    def test_a_pointees_convention_survives_the_declaration_level_flag(self):
        """Which is the same rule a declaration follows, not a special case for types."""
        narrow = demangle.style("llvm", msvc={"calling_convention": False})
        assert demangle.demangle_type("P6AHXZ", language="msvc", style=narrow) == "int (__cdecl *)(void)"

    def test_the_tree_spells_what_the_text_spells(self):
        narrow = demangle.style("llvm", msvc={"tag_kind": False, "ms_keywords": False})
        for encoding in ("PEAUS@@", "P6AHXZ", "$$A8@@BAHXZ", "PEAPEAVFoo@@"):
            tree = demangle.parse_type(encoding, language="msvc", style=narrow)
            assert tree.spell(style=narrow) == demangle.demangle_type(encoding, language="msvc", style=narrow), encoding

    def test_the_default_spelling_is_not_served_to_a_narrower_caller_or_the_other_way(self):
        narrow = demangle.style("llvm", msvc={"tag_kind": False})
        assert demangle.demangle_type("PEAUS@@", language="msvc") == "struct S *"
        assert demangle.demangle_type("PEAUS@@", language="msvc", style=narrow) == "S *"
        assert demangle.demangle_type("PEAUS@@", language="msvc") == "struct S *"
