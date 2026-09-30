"""Borland/Embarcadero Delphi and C++Builder symbol names.

There is no Delphi compiler here, so nothing is a live `tdump` of a BPL in CI. Two
other things stand in:

* **Agreement with Embarcadero's unmangler.** `conformance/delphi-tdump.txt` is a dump
  of the real `tdump.exe -q -um` over the export tables of real BPLs and C++Builder
  DLLs, and its expected column is what that unmangler printed -- never this library's
  own reading, which would measure nothing. `delphi-real-world.txt` is a per-kind sample
  of the same dump for a reader to look at. Both are replayed in
  `tests/test_conformance.py`; all 11,363 readable entries are exact.
* **The whole symbol is accounted for.** A reading that cannot consume the bytes is
  refused, so a parse cannot invent a suffix or drop a type.
"""

import contextlib
import pathlib

import pytest

import demangle
from demangle.core.errors import LimitExceeded
from demangle.core.limits import Limits
from demangle.schemes.delphi._parser import DemangleFailure, detect, parse_delphi_symbol
from demangle.schemes.delphi.nodes import Symbol

CONFORMANCE = pathlib.Path(__file__).parent / "conformance"


def rows(name):
    return [
        tuple(line.split("\t", 1))
        for line in (CONFORMANCE / name).read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "\t" in line
    ]


ROWS = rows("delphi-real-world.txt")
REFUSALS = [
    line
    for line in (CONFORMANCE / "delphi-refusals.txt").read_text(encoding="utf-8").splitlines()
    if line and not line.startswith("#")
]


class TestConformance:
    def test_the_corpus_covers_the_shapes(self):
        assert len(ROWS) >= 60
        kinds = {parse_delphi_symbol(mangled).kind for mangled, _expected in ROWS}
        assert kinds >= {
            "function",
            "constructor",
            "destructor",
            "operator",
            "conversion",
            "data",
            "tpdsc",
            "thunk",
            "linkproc",
        }

    def test_every_recorded_symbol_still_reads_the_same(self, subtests):
        for mangled, expected in ROWS:
            with subtests.test(name=mangled):
                assert parse_delphi_symbol(mangled).text == expected

    def test_every_reading_consumes_the_whole_symbol(self, subtests):
        for mangled, _expected in ROWS:
            with subtests.test(name=mangled):
                parse_delphi_symbol(mangled)

    def test_it_still_refuses_what_is_not_a_delphi_symbol(self, subtests):
        for mangled in REFUSALS:
            with subtests.test(name=mangled):
                assert not detect(mangled)


class TestGrammar:
    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("@Unit@Class@Method$qqrv", "__fastcall Unit::Class::Method()"),
            ("@Hellopas@HelloFromPas$qqsv", "__stdcall Hellopas::HelloFromPas()"),
            ("@foo$qv", "foo()"),
            ("@foo$qi$i", "int foo(int)"),
            ("@Classes@TThread@$bctr$qqrv", "__fastcall Classes::TThread::TThread()"),
            ("@Classes@TThread@$bdtr$qqrv", "__fastcall Classes::TThread::~TThread()"),
            ("@CCriticalSection@$op21_RTL_CRITICAL_SECTION$qv", "CCriticalSection::operator _RTL_CRITICAL_SECTION *()"),
            (
                "@System@TDateTime@$o17System@AnsiString$xqqrv",
                "__fastcall System::TDateTime::operator System::AnsiString() const",
            ),
            (
                "@Classes@TList@Sort$qqrpqqrpvt1$i",
                "__fastcall Classes::TList::Sort(int __fastcall (*)(void *, void *))",
            ),
            (
                "@AllocateHWnd$qqrynpqqrr8TMessage$v",
                "__fastcall AllocateHWnd(void __fastcall __closure(*)(TMessage&))",
            ),
            ("@@InitExe", "__linkproc__ InitExe"),
            ("@Classes@TThread@$bcdtr$qqrv", "__fastcall Classes::TThread::`class destructor`()"),
            ("@System@Var", "System::Var"),
        ],
    )
    def test_the_productions(self, mangled, expected):
        assert parse_delphi_symbol(mangled).text == expected

    def test_msvc_fastcall_c_decoration_is_not_this_scheme(self):
        assert not detect("@func@4")
        with pytest.raises(DemangleFailure):
            parse_delphi_symbol("@func@4")

    def test_an_incomplete_constructor_is_refused(self):
        with pytest.raises(DemangleFailure):
            parse_delphi_symbol("@TTabPage@$bctr")


class TestClaimsNothingItShouldNot:
    @pytest.mark.parametrize(
        "name",
        [
            # A `@` and then anything that is not a Borland export. This scheme is offered
            # every `@` name and copies characters through, so it needs a screen. The first
            # three are demangled Swift types; see `TestReadingAnAnswerAgainChangesNothing`.
            "@convention(block) (Swift.Int) -> Swift.UInt",
            "@escaping @differentiable @callee_guaranteed (@unowned Swift.Float) -> ()",
            "@objc SomeClass.method()",
            "@ hello world",
            # MSVC and clang-cl put both of these in every COFF object they emit.
            "@feat.00",
            "@comp.id",
            "main",
            "_Z1fv",
            "?f@@YAXH@Z",
            "MYUNIT_$$_ADD$LONGINT$$LONGINT",
            "@func@4",
            "@__swiftmacro_x",
            "",
            "@",
            "@@",
            "@@bug@@x",
            "@foo$q%",
            "@foo$q$",
            # A calling-convention letter this does not know.
            "@foo$qqzv",
            # `void` beside another parameter, or under a reference; alone it is the empty list.
            "@foo$qqrgv",
            "@foo$qqrvi",
            "@foo$qqriv",
            "@foo$qqqrv",
        ],
    )
    def test_it_refuses(self, name):
        assert not detect(name)

    def test_void_alone_is_still_the_empty_list(self):
        assert demangle.demangle("@foo$qqrv") == "__fastcall foo()"

    @pytest.mark.parametrize(
        "name",
        [
            "@?0??define_lambda@@YAHXZ@QBE@XZ",
            "@?0??1@YAHXZ@A",
            "@?0??nested_lambdas@hard@@YAHXZ@QEBA",
            "@x?",
            "@a@b?c",
        ],
    )
    def test_a_question_mark_is_msvcs_marker_and_no_borland_production_writes_one(self, name):
        """A fragment of an MSVC symbol is not a Delphi export.

        There is no `?` in any of the 11,363 recorded exports, and this parser copies
        characters through rather than checking an alphabet -- so it read them.
        `demangle_text` over a listing tokenises
        `??R<lambda_1>@?0??define_lambda@@YAHXZ@QBE@XZ` at the angle brackets the token
        cannot hold, and what was left came back as
        `?0??define_lambda::__linkproc__ YAHXZ::QBE::XZ`: a Delphi declaration built out
        of half an MSVC symbol.
        """
        assert not detect(name)
        assert demangle.demangle(name) == name

    def test_the_parser_still_reads_one_when_the_caller_insists(self):
        """`detect` decides what to claim unasked; `language=` is the caller saying so."""
        assert demangle.demangle("@x?", language="delphi") == "x?"

    def test_a_qualified_data_name_without_dollar_is_still_claimed(self):
        assert detect("@System@Var")

    def test_a_bare_linker_procedure_from_a_bpl_is_claimed(self):
        assert detect("@@InitExe")
        assert not detect("@@bug@@x")

    def test_it_claims_nothing_in_the_other_schemes_corpora(self, subtests):
        for path in sorted([*CONFORMANCE.glob("*.txt"), *(CONFORMANCE / "reported").glob("*.txt")]):
            if path.stem.partition("-")[0] == "delphi":
                continue
            with subtests.test(name=path.name):
                claimed = [
                    line.split("\t", 1)[0]
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line and not line.startswith("#") and detect(line.split("\t", 1)[0])
                ]
                assert claimed == []


class TestRegisteredAsALanguage:
    def test_demangle_reaches_it_without_being_told(self):
        assert demangle.demangle("@Unit@Class@Method$qqrv") == "__fastcall Unit::Class::Method()"

    def test_naming_the_language_works(self):
        for name in ("delphi", "borland", "bcc", "c++builder", "embarcadero"):
            assert demangle.demangle("@foo$qv", language=name) == "foo()"

    def test_it_does_not_take_another_scheme_s_names(self):
        assert demangle.demangle("_Z1fv") == "f()"
        assert demangle.demangle("MYUNIT_$$_ADD$LONGINT$$LONGINT") == "MYUNIT.ADD(LONGINT): LONGINT"


class TestTree:
    def test_the_tree_spells_what_demangle_spells(self, subtests):
        for mangled, expected in ROWS:
            with subtests.test(name=mangled):
                assert demangle.parse(mangled).spell() == expected

    def test_parameters_are_reachable(self):
        tree = demangle.parse("@myclass@func$qil")
        assert isinstance(tree, Symbol)
        assert tree.delphi_kind == "function"
        parameters = next(tree.find("parameters"))
        assert [node.text for node in parameters.children()] == ["int", "long"]

    def test_a_function_pointer_parameter_is_one_child(self):
        tree = demangle.parse("@foo$qpqfi$d")
        assert tree.spell() == "foo(double (*)(float, int))"
        parameters = next(tree.find("parameters"))
        assert [node.text for node in parameters.children()] == ["double (*)(float, int)"]


class TestLimitsAndRefusal:
    def test_a_percent_in_the_arglist_is_refused_rather_than_hanging(self):
        with pytest.raises(DemangleFailure, match="unknown type"):
            parse_delphi_symbol("@foo$q%")
        assert not detect("@foo$q%")
        assert demangle.demangle("@foo$q%") == "@foo$q%"

    @pytest.mark.parametrize(
        "mangled",
        [
            "@a$qx",
            "@Graphics@TFont@$bctr$qqrx",
            "@a@$bctr$qqrw",
        ],
    )
    def test_an_argument_list_ending_in_a_qualifier_is_refused_rather_than_hanging(self, mangled):
        """`while char in "xw"` never ended when `char` was `""`.

        An empty string is a substring of every string, so at the end of the input the
        loop matched, emitted another `volatile `, advanced nothing, and matched again.
        `demangle()` and `detect()` -- both documented never to raise, and both run over
        every symbol in a table -- ran until the buffer exhausted memory.
        """
        with pytest.raises(DemangleFailure):
            parse_delphi_symbol(mangled)
        assert not detect(mangled)
        assert demangle.demangle(mangled) == mangled

    def test_a_truncated_indirection_is_refused_rather_than_recursing(self):
        """`"" in "Mrhp"` was True too, so a name ending in `p` read the end of the
        input as another pointer, all the way down to `max_depth`. The bound caught it,
        but a truncated name is not a name that was too deep."""
        with pytest.raises(DemangleFailure, match="unknown type"):
            parse_delphi_symbol("@a$qp")

    @pytest.mark.parametrize("mangled", ["@oo$qt$i", "@a$qit$", "@a$qit%"])
    def test_a_malformed_back_reference_index_is_refused_not_a_valueerror(self, mangled):
        """`int(digit, 36)` on `$` raised `ValueError` straight out of `detect`, which
        the scheme documents as raising `DemangleFailure` and nothing else."""
        with pytest.raises(DemangleFailure, match="back-reference"):
            parse_delphi_symbol(mangled)
        assert detect(mangled) is False

    def test_every_truncation_of_every_recorded_name_terminates(self, subtests):
        """The bug class, rather than the three names that happened to expose it.

        Every prefix of a real symbol is a name some tool will eventually hand this --
        a stripped table, a truncated read -- and each one must come back with an answer
        or a refusal.
        """
        for mangled, _expected in ROWS:
            for cut in range(1, len(mangled)):
                prefix = mangled[:cut]
                with subtests.test(prefix=prefix), contextlib.suppress(DemangleFailure, LimitExceeded):
                    parse_delphi_symbol(prefix)

    def test_max_depth_is_enforced(self):
        with pytest.raises(LimitExceeded) as caught:
            demangle.parse("@foo$qpi", limits=Limits(max_depth=1))
        assert caught.value.limit_name == "recursion depth"
        assert caught.value.limit_value == 1

    def test_max_substitutions_is_enforced(self):
        with pytest.raises(LimitExceeded) as caught:
            demangle.parse("@foo$qiit1", limits=Limits(max_substitutions=1))
        assert caught.value.limit_name == "substitution"
        assert caught.value.limit_value == 1

    def test_default_limits_still_read_ordinary_names(self):
        assert parse_delphi_symbol("@foo$qpi").text == "foo(int *)"
        assert parse_delphi_symbol("@foo$qiit1").text == "foo(int, int, int)"
