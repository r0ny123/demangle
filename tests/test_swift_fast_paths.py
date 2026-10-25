"""The shortcuts the Swift scheme takes, and the properties each of them leans on.

`_demangler.py` and `_printer.py` do less than the reference's own shape would: an
identifier is split into words only when a word substitution asks for the table, a
declaration walks its type for `some P` only once one has been read, the printer asks one
table about a kind where it once asked four, and `Printer.write` is the output list's own
`append`. Each is right because of a property that is easy to lose and that nothing on the
surface shows: with several of them broken the corpus still spells every name the same,
because few names read a word out of a table that was filled late, and in seven of them a
`some P` appears at all -- and nothing prints the link it is given to its declaration. The
tests here pin the properties directly.

Every spelling, link and word table below was recorded from `main`, before any of the
shortcuts, and is written out as a literal; none is derived from the code under test.
Where a test reaches past the public functions to the parser's own state, it is the state
the shortcut is made of: `unsplit`, `words` and `has_opaque_return_type`.
"""

import pathlib
import string
import types

import pytest

import demangle
from demangle.core.errors import DemanglingError
from demangle.schemes.swift import _printer as printer_module
from demangle.schemes.swift import demangle_symbolic, nodes
from demangle.schemes.swift._demangler import Demangler, demangle_symbol
from demangle.schemes.swift._node import Node

from .conftest import load_corpus
from .test_swift_symbolic import ROWS as SYMBOLIC_ROWS


def spell(mangled):
    return demangle.demangle_strict(mangled, language="swift")


def read(mangled):
    """The demangler itself, after reading `mangled`, for the state a shortcut leaves."""
    reader = Demangler(mangled)
    assert reader.demangle_symbol() is not None, mangled
    return reader


#: Twelve identifiers of two words each, then one of three and one of three more. With a
#: one-letter module in front, which has no word, the first twelve fill the table's slots
#: 0 to 23, the thirteenth, `YyZzAa`, takes 24 and 25 and loses its third word, and the
#: fourteenth, `BbCcDd`, loses all of its. The words are `Aa`, `Bb`, `Cc` ... so that a
#: wrong one is recognisable at a glance.
CAPPED = "$s1M4AaBbV4CcDdV4EeFfV4GgHhV4IiJjV4KkLlV4MmNnV4OoPpV4QqRrV4SsTtV4UuVvV4WwXxV6YyZzAaV6BbCcDdV"
CAPPED_TYPES = "M.AaBb.CcDd.EeFf.GgHh.IiJj.KkLl.MmNn.OoPp.QqRr.SsTt.UuVv.WwXx.YyZzAa.BbCcDd"


class TestWordsAreSplitWhenASubstitutionAsks:
    """`unsplit` and `_split_words`: identifiers wait unsplit until a word substitution
    needs the table, and are then split in the order they were read.

    Splitting is deferred because about seven names in ten have no word substitution and
    the split is a step per character of every identifier. What keeps the deferral
    invisible is that the table is append-only and read only by a substitution, so the
    words it finds are the ones eager splitting would have put there -- *provided* the
    queue is drained in reading order, drained completely, drained once, and holds the
    identifiers a substitution is entitled to and no others. Each case below fails if one
    of those is not so: the words are reversed, the last or first identifier is lost, the
    queue is split a second time, or something that is not a plain identifier gets in.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # Three identifiers are queued -- AlphaBeta, GammaDelta, EpsilonZeta -- when
            # `0adeF0` asks for words 0, 3, 4 and 5: one from each of them, and the last
            # word of the last. Reversed, it reads EpsilonDeltaAlphaBeta; with the last
            # identifier lost, `F` is past the end of the table and the name is refused.
            pytest.param(
                "$s9AlphaBeta10GammaDeltaV11EpsilonZetaV0adeF0VMn",
                "nominal type descriptor for AlphaBeta.GammaDelta.EpsilonZeta.AlphaDeltaEpsilonZeta",
                id="one substitution, three sources",
            ),
            # The queue drains for `0adeF0`, fills with ThetaIota and KappaLambda, and
            # drains again for `0agJ0`, which reads Alpha from the first batch and Theta
            # and Lambda from the second. A first batch split twice would push Theta from
            # slot 6 to slot 12; the substitution's own result, AlphaDeltaEpsilonZeta, is
            # not an identifier a later one may take words from, and is not queued.
            pytest.param(
                "$s9AlphaBeta10GammaDeltaV11EpsilonZetaV0adeF0V9ThetaIotaV11KappaLambdaV0agJ0VMn",
                "nominal type descriptor for AlphaBeta.GammaDelta.EpsilonZeta.AlphaDeltaEpsilonZeta"
                ".ThetaIota.KappaLambda.AlphaThetaLambda",
                id="drains, refills and drains again",
            ),
            # `5Gamma` is a plain piece of the substitution itself, and the letter after
            # it reads that piece's own word: the queue has to be drained between the
            # pieces of one identifier, not only in front of it.
            pytest.param(
                "$s9AlphaBeta0a5GammaC0VMn",
                "nominal type descriptor for AlphaBeta.AlphaGammaGamma",
                id="a letter reads the piece before it",
            ),
            pytest.param(
                "$s9AlphaBeta0a5Gamma5DeltaD0VMn",
                "nominal type descriptor for AlphaBeta.AlphaGammaDeltaDelta",
                id="a letter reads the second piece before it",
            ),
            # `0012FooBarBz_Gza` is punycode for FooBarBäz, whose three words are not
            # words a substitution can take: `0cdA0` still finds Gamma, Delta and Alpha
            # in slots 2, 3 and 0. Queue the decoded text and they move to 5, 6 and 0.
            pytest.param(
                "$s9AlphaBeta0012FooBarBz_GzaV10GammaDeltaV0cdA0VMn",
                "nominal type descriptor for AlphaBeta.FooBarBäz.GammaDelta.GammaDeltaAlpha",
                id="punycode between the sources",
            ),
            # Words come from the identifier as it was read, in UTF-8 bytes, and the
            # joined result is decoded afterwards. Queue the decoded `Été` and the join
            # is not UTF-8 any more, so the name is refused.
            pytest.param(
                "$s9AlphaBeta8ÉtéFooV0cD0VMn",
                "nominal type descriptor for AlphaBeta.ÉtéFoo.ÉtéFoo",
                id="a non-ASCII identifier is split as read",
            ),
            # The table holds 26 words and an identifier can straddle its end. `0alyZ0`
            # asks for Aa, Ll and the last two slots, 24 and 25: Yy and Zz, the first two
            # words of the thirteenth identifier, whose third is lost and whose successor
            # contributes nothing. Fifteen identifiers wait in the queue when it drains.
            pytest.param(
                CAPPED + "0alyZ0VMn",
                f"nominal type descriptor for {CAPPED_TYPES}.AaLlYyZz",
                id="the cap falls inside a queued identifier",
            ),
            # Once the table is full, later identifiers are queued and split to no effect;
            # a second substitution still reads slots 25 and 24 as it did.
            pytest.param(
                CAPPED + "0alyZ0V6EeFfGgV0zY0VMn",
                f"nominal type descriptor for {CAPPED_TYPES}.AaLlYyZz.EeFfGg.ZzYy",
                id="identifiers after the cap change nothing",
            ),
            # Real names. PropertyListSerialization and `data` are drained as the first
            # substitution begins and its own piece `from` between its pieces; `format`
            # and `options` are queued behind them, and `0bC6Format` reads Property and
            # List from the first drain.
            pytest.param(
                "$s10Foundation25PropertyListSerializationC4data04frombC06format7optionsAA4DataVyp_AC0bC6FormatOSitKFZ",
                "static Foundation.PropertyListSerialization.data(fromPropertyList: Any, "
                "format: Foundation.PropertyListSerialization.PropertyListFormat, "
                "options: Swift.Int) throws -> Foundation.Data",
                id="real: PropertyListSerialization.data",
            ),
            pytest.param(
                "$s10Foundation11FileManagerC13replaceItemAt_04witheF006backupE4Name7optionsAA3URLVSgAI_AISSSgAC0E18ReplacementOptionsVtKF",
                "Foundation.FileManager.replaceItemAt(_: Foundation.URL, withItemAt: Foundation.URL, "
                "backupItemName: Swift.String?, options: Foundation.FileManager.ItemReplacementOptions) "
                "throws -> Foundation.URL?",
                id="real: FileManager.replaceItemAt",
            ),
            # Three words in the table and twelve identifiers queued behind them: that
            # drain takes the table to 23, three one-piece substitutions take it to 26 -- the
            # last of them straddling the cap -- and `0zuB0` reads slot 25.
            pytest.param(
                "$s10Foundation15AttributeScopesO0A10AttributesV4link10morphology7inflect18languageIdentifier"
                "19personNameComponent12numberFormat9dateField16replacementIndex11measurement"
                "21inflectionAlternative9byteCountA2E04LinkB0O_AE010MorphologyB0OAE014InflectionRuleB0O"
                "AE08LanguageiB0OAE06PersonklB0OAE06NumbernD0VAE04DatepB0OAE011ReplacementrB0OAE011MeasurementB0V"
                "AE0zuB0OAE04BytewB0OtcfC",
                "Foundation.AttributeScopes.FoundationAttributes.init("
                "link: Foundation.AttributeScopes.FoundationAttributes.LinkAttribute, "
                "morphology: Foundation.AttributeScopes.FoundationAttributes.MorphologyAttribute, "
                "inflect: Foundation.AttributeScopes.FoundationAttributes.InflectionRuleAttribute, "
                "languageIdentifier: Foundation.AttributeScopes.FoundationAttributes.LanguageIdentifierAttribute, "
                "personNameComponent: Foundation.AttributeScopes.FoundationAttributes.PersonNameComponentAttribute, "
                "numberFormat: Foundation.AttributeScopes.FoundationAttributes.NumberFormatAttributes, "
                "dateField: Foundation.AttributeScopes.FoundationAttributes.DateFieldAttribute, "
                "replacementIndex: Foundation.AttributeScopes.FoundationAttributes.ReplacementIndexAttribute, "
                "measurement: Foundation.AttributeScopes.FoundationAttributes.MeasurementAttribute, "
                "inflectionAlternative: Foundation.AttributeScopes.FoundationAttributes.InflectionAlternativeAttribute, "
                "byteCount: Foundation.AttributeScopes.FoundationAttributes.ByteCountAttribute) "
                "-> Foundation.AttributeScopes.FoundationAttributes",
                id="real: AttributeScopes, the cap across a queue",
            ),
        ],
    )
    def test_a_substitution_finds_the_words_eager_splitting_would_have_made(self, mangled, expected):
        assert spell(mangled) == expected
        assert demangle.parse(mangled, language="swift").spell() == expected

    def test_the_table_holds_exactly_what_was_read_before_the_substitution(self):
        """Both edges of a two-word table. `0bB0` takes slot 1, which is `Bar`, the word
        of the last of two queued identifiers -- lose that identifier and the name is
        refused; `0bC0` takes slot 2, which does not exist, and the name is refused as the
        reference refuses it."""
        assert spell("$s3Foo3BarV0bB0VMn") == "nominal type descriptor for Foo.Bar.BarBar"
        with pytest.raises(DemanglingError):
            spell("$s3Foo3BarV0bC0VMn")

    def test_identifiers_wait_in_the_order_they_were_read(self):
        """The queue holds each plain identifier, whole, and the table stays empty: with
        no substitution in the name, nothing has asked for a word."""
        reader = read("$s9AlphaBeta10GammaDeltaV11EpsilonZetaVMn")
        assert reader.unsplit == ["AlphaBeta", "GammaDelta", "EpsilonZeta"]
        assert reader.words == []

    def test_a_substitution_empties_the_queue_into_the_table_in_reading_order(self):
        reader = read("$s9AlphaBeta10GammaDeltaV11EpsilonZetaV0adeF0VMn")
        assert reader.unsplit == []
        assert reader.words == ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta"]

    @pytest.mark.parametrize(
        ("mangled", "table"),
        [
            ("$s9AlphaBeta10GammaDeltaV11EpsilonZetaVMn", ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta"]),
            (
                "$s9AlphaBeta10GammaDeltaV11EpsilonZetaV0adeF0V5OmegaVMn",
                ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Omega"],
            ),
            (
                "$s9AlphaBeta10GammaDeltaV11EpsilonZetaV0adeF0V9ThetaIotaV11KappaLambdaV0agJ0V5OmegaVMn",
                ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Theta", "Iota", "Kappa", "Lambda", "Omega"],
            ),
            ("$s9AlphaBeta0012FooBarBz_GzaV10GammaDeltaV0cdA0VMn", ["Alpha", "Beta", "Gamma", "Delta"]),
            (CAPPED + "0alyZ0V6EeFfGgVMn", [f"{letter}{letter.lower()}" for letter in string.ascii_uppercase]),
        ],
    )
    def test_the_table_and_the_queue_together_are_what_splitting_as_read_would_have_built(self, mangled, table):
        """What `main` held in `words` at the end of the name, which split every identifier
        the moment it was read. Here the part that nothing has asked for is still in
        `unsplit`; splitting it, in order, completes the same table."""
        reader = read(mangled)
        reader._split_words()
        assert reader.unsplit == []
        assert reader.words == table

    def test_the_queue_fills_again_after_it_is_emptied_and_holds_only_what_came_after(self):
        reader = read("$s9AlphaBeta10GammaDeltaV11EpsilonZetaV0adeF0V9ThetaIotaV11KappaLambdaV0agJ0V5OmegaVMn")
        assert reader.unsplit == ["Omega"]
        assert reader.words == [
            *("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta"),
            *("Theta", "Iota", "Kappa", "Lambda"),
        ]

    def test_the_composite_a_substitution_builds_is_not_queued(self):
        """`0adeF0` makes AlphaDeltaEpsilonZeta out of words that are already in the
        table. It is an identifier and a substitution candidate, but not a source of words:
        queued, it would add Alpha, Delta, Epsilon and Zeta a second time."""
        reader = read("$s9AlphaBeta10GammaDeltaV11EpsilonZetaV0adeF0V5OmegaVMn")
        assert reader.unsplit == ["Omega"]
        assert len(reader.words) == 6

    def test_punycode_is_neither_queued_nor_split(self):
        reader = read("$s9AlphaBeta0012FooBarBz_GzaV10GammaDeltaV0cdA0VMn")
        assert reader.unsplit == []
        assert reader.words == ["Alpha", "Beta", "Gamma", "Delta"]

    def test_the_table_stops_at_twenty_six_words_and_keeps_the_first_twenty_six(self):
        reader = read(CAPPED + "0alyZ0V6EeFfGgVMn")
        assert len(reader.words) == 26
        assert reader.words[-3:] == ["Xx", "Yy", "Zz"]
        assert reader.unsplit == ["EeFfGg"]

    def test_a_fragment_a_resolver_returns_is_read_with_words_of_its_own(self):
        """A symbolic reference's fragment is demangled by a demangler of its own, with a
        table and a queue of its own: Gamma and Delta, read inside `4demo10GammaDeltaV`,
        are not words of the name round it. `0aB0` is AlphaBeta, not demoGamma, and
        `0cB0` -- slot 2 -- does not exist."""
        reference = b"\x01\x00\x00\x00\x00"

        def resolver(symbolic, at):
            return "4demo10GammaDeltaV"

        read_it = b"$s" + reference + b"9AlphaBetaV0aB0VMn"
        assert demangle_symbolic(read_it, resolver) == "nominal type descriptor for demo.GammaDelta.AlphaBeta.AlphaBeta"
        assert demangle_symbolic(read_it.replace(b"0aB0", b"0cB0"), resolver) is None


def link_report(root):
    """Where each `OpaqueReturnType` in the tree points, as the declaration it names.

    The demangler stores the parent as `OpaqueReturnTypeParent(index=id(parent))`, an
    address, so what is reported is the node it addresses -- found by identity among the
    tree's own nodes, so a link to anything outside the tree raises -- as its kind and its
    spelling. A node reached twice is reported once, in the order a depth-first walk first
    reaches it, and `None` stands for one that was never given a parent.
    """
    seen = {}
    order = []

    def walk(node):
        if id(node) in seen:
            return
        seen[id(node)] = node
        order.append(node)
        for child in node.children:
            walk(child)

    walk(root)
    report = []
    for node in order:
        if node.kind == "OpaqueReturnType":
            parents = [child for child in node.children if child.kind == "OpaqueReturnTypeParent"]
            assert len(parents) <= 1, "a parent was added twice"
            if parents:
                parent = seen[parents[0].index]
                report.append(f"{parent.kind} {printer_module.print_root(parent)}")
            else:
                report.append(None)
    return report


#: A symbolic reference to the target of a resolver: marker 1, a four-byte offset.
REFERENCE = b"\x01\x00\x00\x00\x00"


def read_symbolic(raw, fragments):
    """Read `raw` with a resolver that answers each reference with the next fragment."""
    answers = iter(fragments)
    reader = Demangler(raw.decode("latin-1"), lambda reference, at: next(answers), byte_mode=True)
    return reader, reader.demangle_symbol()


class TestOpaqueReturnTypesKnowWhoReturnsThem:
    """`has_opaque_return_type`: a declaration walks its type, pointing each `some P` at
    the declaration whose return type it is, only if the demangler has read one.

    The walk is skipped when the flag is clear, and the flag has to be set at every place
    an `OpaqueReturnType` can come from: `Qr`, `QR` and an index, and a symbolic
    reference whose fragment holds one -- which is read by a demangler of its own, so the
    flag has to travel from it to the one that asked. A site that forgets leaves a
    `some P` with no parent and nothing else wrong: the printer says `some` either way.
    """

    @pytest.mark.parametrize(
        ("mangled", "spelled", "report"),
        [
            pytest.param(
                "$s4main3fooQryF",
                "main.foo() -> some",
                ["Function main.foo() -> some"],
                id="Qr on a function",
            ),
            pytest.param(
                "$s4main3fooQR_yF",
                "main.foo() -> some",
                ["Function main.foo() -> some"],
                id="QR on a function",
            ),
            pytest.param(
                "$s4main3fooQR1_yF",
                "main.foo() -> some",
                ["Function main.foo() -> some"],
                id="QR with an index on a function",
            ),
            pytest.param(
                "$s4main3fooQrSgyF",
                "main.foo() -> (some)?",
                ["Function main.foo() -> (some)?"],
                id="Qr inside another type",
            ),
            pytest.param(
                "$s4main1xQrvp",
                "main.x : some",
                ["Variable main.x : some"],
                id="Qr on a variable",
            ),
            pytest.param(
                "$s4main1xQR0_vp",
                "main.x : some",
                ["Variable main.x : some"],
                id="QR on a variable",
            ),
            pytest.param(
                "$s4main1xQrvg",
                "main.x.getter : some",
                ["Variable main.x : some"],
                id="an accessor points at its variable",
            ),
            pytest.param(
                "$s4main1SVyQrSicip",
                "main.S.subscript(Swift.Int) -> some",
                ["Subscript main.S.subscript(Swift.Int) -> some"],
                id="Qr on a subscript",
            ),
            pytest.param(
                "$s4main1SVyQR_Sicig",
                "main.S.subscript.getter : (Swift.Int) -> some",
                ["Subscript main.S.subscript(Swift.Int) -> some"],
                id="QR on a subscript",
            ),
            # The inner declaration's `some` is its own, and the outer's is the outer's:
            # the walk of `inner`'s type does not reach `foo`, which is only its context.
            pytest.param(
                "$s4main3fooQryF5innerQryF",
                "inner() -> some in main.foo() -> some",
                ["Function main.foo() -> some", "Function inner() -> some in main.foo() -> some"],
                id="a nested function",
            ),
            pytest.param(
                "$s4main3fooQryF1xQrvp",
                "x : some in main.foo() -> some",
                ["Function main.foo() -> some", "Variable x : some in main.foo() -> some"],
                id="a variable nested in a function",
            ),
            pytest.param(
                "$s4main1SVyQrSicig5innerQryF",
                "inner() -> some in main.S.subscript(Swift.Int) -> some",
                [
                    "Subscript main.S.subscript(Swift.Int) -> some",
                    "Function inner() -> some in main.S.subscript(Swift.Int) -> some",
                ],
                id="a function nested in a subscript",
            ),
            # `blah`'s parameter type names `f`'s opaque return type, and `blah` has seen
            # a `Qr` by then, so it does walk its type -- and stops at the nested `f`,
            # whose `some` stays `f`'s.
            pytest.param(
                "$s4test3fooV4blahyAA1SV1fQryFQOy_Qo_AHF",
                "test.foo.blah(<<opaque return type of test.S.f() -> some>>.0) "
                "-> <<opaque return type of test.S.f() -> some>>.0",
                ["Function test.S.f() -> some"],
                id="a type that names another declaration's some",
            ),
            pytest.param(
                "$s4main1SV3barQryFQOHo",
                "opaque type descriptor runtime record for <<opaque return type of main.S.bar() -> some>>",
                ["Function main.S.bar() -> some"],
                id="the descriptor of an opaque type",
            ),
            # The reference parents only functions, variables and subscripts, so an
            # initialiser's `some` is read but left without one.
            pytest.param(
                "$s4main1SVQrycfC",
                "main.S.init() -> some",
                [None],
                id="a declaration the reference does not parent",
            ),
            pytest.param("$s4main3fooSiyF", "main.foo() -> Swift.Int", [], id="no opaque type"),
        ],
    )
    def test_each_opaque_return_type_points_at_the_declaration_that_returns_it(self, mangled, spelled, report):
        root = demangle_symbol(mangled)
        assert printer_module.print_root(root) == spelled
        assert link_report(root) == report

    @pytest.mark.parametrize(
        ("mangled", "flag"),
        [
            ("$s4main3fooQryF", True),
            ("$s4main3fooQR_yF", True),
            ("$s4main3fooQR1_yF", True),
            ("$s4main1xQrvp", True),
            ("$s4main1SVQrycfC", True),
            ("$s4main3fooSiyF", False),
            ("$s4main1xSivp", False),
        ],
    )
    def test_reading_either_spelling_sets_the_flag_and_nothing_else_does(self, mangled, flag):
        assert read(mangled).has_opaque_return_type is flag

    @pytest.mark.parametrize(
        ("raw", "fragments", "spelled", "report"),
        [
            # The fragment is the return type itself, read by a demangler that is gone
            # by the time `F` asks whether there is a `some` to point at the function.
            pytest.param(
                b"$s4main3foo" + REFERENCE + b"yF",
                ["Qr"],
                "main.foo() -> some",
                ["Function main.foo() -> some"],
                id="a function, Qr from a fragment",
            ),
            pytest.param(
                b"$s4main3foo" + REFERENCE + b"yF",
                ["QR0_"],
                "main.foo() -> some",
                ["Function main.foo() -> some"],
                id="a function, QR from a fragment",
            ),
            pytest.param(
                b"$s4main3foo" + REFERENCE + b"yF",
                ["SayQrG"],
                "main.foo() -> [some]",
                ["Function main.foo() -> [some]"],
                id="a function, Qr inside a fragment's generic",
            ),
            pytest.param(
                b"$s4main1x" + REFERENCE + b"vp",
                ["Qr"],
                "main.x : some",
                ["Variable main.x : some"],
                id="a variable",
            ),
            pytest.param(
                b"$s4main1SVy" + REFERENCE + b"Sicip",
                ["Qr"],
                "main.S.subscript(Swift.Int) -> some",
                ["Subscript main.S.subscript(Swift.Int) -> some"],
                id="a subscript",
            ),
            # The fragment is itself a reference: two demanglers deep, and then three.
            pytest.param(
                b"$s4main3foo" + REFERENCE + b"yF",
                [REFERENCE, "Qr"],
                "main.foo() -> some",
                ["Function main.foo() -> some"],
                id="through a fragment that is a reference",
            ),
            pytest.param(
                b"$s4main3foo" + REFERENCE + b"yF",
                [REFERENCE, REFERENCE, "Qr"],
                "main.foo() -> some",
                ["Function main.foo() -> some"],
                id="through two",
            ),
            pytest.param(
                b"$s4main3fooQryF5inner" + REFERENCE + b"yF",
                ["Qr"],
                "inner() -> some in main.foo() -> some",
                ["Function main.foo() -> some", "Function inner() -> some in main.foo() -> some"],
                id="a nested function, the inner one from a fragment",
            ),
            pytest.param(
                b"$s4main3foo" + REFERENCE + b"yF",
                ["Si"],
                "main.foo() -> Swift.Int",
                [],
                id="a fragment with no opaque type",
            ),
        ],
    )
    def test_a_fragment_carries_its_opaque_return_type_to_the_declaration_that_asked(
        self, raw, fragments, spelled, report
    ):
        reader, root = read_symbolic(raw, fragments)
        assert printer_module.print_root(root) == spelled
        assert link_report(root) == report
        assert reader.has_opaque_return_type is bool(report)

    def test_the_flag_belongs_to_one_demangler(self):
        assert read("$s4main3fooQryF").has_opaque_return_type is True
        assert read("$s4main3fooSiyF").has_opaque_return_type is False

    def test_walking_every_declaration_finds_nothing_the_flag_skipped(self, monkeypatch):
        """The flag is only an optimisation if a clear flag means there is nothing to
        find. So run the corpus, the symbolic corpus and a few thousand names made by
        putting a `some` where a `Swift.Int`, `Swift.String` or `Swift.Bool` stood -- in
        accessors, thunks, specialisations, closures, whatever the corpus holds -- twice:
        once as it is, and once with the flag held set so that every declaration walks its
        type as `main` did. The links must be the same."""
        names = [mangled for mangled, _ in load_corpus("swift-real-world.txt") + load_corpus("swift-upstream.txt")]
        names += [
            name[:at] + replacement + name[at + 2 :]
            for name in names
            for standard, replacement in (("Si", "Qr"), ("SS", "QR0_"), ("Sb", "Qr"))
            if (at := name.find(standard)) > 0
        ]
        symbolic = [(raw.decode("latin-1"), fragments) for raw, fragments, _ in SYMBOLIC_ROWS]

        def reports():
            found = [None if (root := demangle_symbol(mangled)) is None else link_report(root) for mangled in names]
            for text, fragments in symbolic:
                root = demangle_symbol(text, lambda reference, at, f=fragments: f.get(at), byte_mode=True)
                found.append(None if root is None else link_report(root))
            return found

        as_it_is = reports()
        monkeypatch.setattr(Demangler, "has_opaque_return_type", property(lambda self: True, lambda self, value: None))
        walking_everything = reports()
        assert as_it_is == walking_everything
        # And the comparison had something to compare: some names with a `some` in them,
        # nearly all of them parented, and a few the reference leaves without.
        assert sum(1 for report in as_it_is if report) > 2000
        assert any(report and None in report for report in as_it_is)
        assert any(report and None not in report for report in as_it_is)


class TestThePrintersTablesAreDisjoint:
    """`print` asks `_HANDLERS` and then `_ENTITY_KINDS`, with `_JUST_TEXT` and
    `_PREFIX_THEN_FIRST_CHILD` folded into the first. That order is only a change of
    speed if no kind is in two of the four, and it is checked where the folding is done."""

    def test_no_kind_is_in_two_of_the_four(self):
        tables = {
            "handlers": printer_module._OWN_HANDLER_KINDS,
            "entities": set(printer_module._ENTITY_KINDS),
            "text": set(printer_module._JUST_TEXT),
            "prefix": set(printer_module._PREFIX_THEN_FIRST_CHILD),
        }
        for first, kinds in tables.items():
            for second, others in tables.items():
                if first < second:
                    assert not kinds & others, f"{sorted(kinds & others)} in both {first} and {second}"

    def test_the_folded_table_is_the_union_of_what_was_folded(self):
        assert set(printer_module._HANDLERS) == (
            printer_module._OWN_HANDLER_KINDS
            | set(printer_module._JUST_TEXT)
            | set(printer_module._PREFIX_THEN_FIRST_CHILD)
        )
        assert set(printer_module._HANDLERS).isdisjoint(printer_module._ENTITY_KINDS)

    @pytest.mark.parametrize(
        ("table", "kind"),
        [
            ("_JUST_TEXT = {", "Function"),  # an entity
            ("_JUST_TEXT = {", "Global"),  # a kind with a handler of its own
            ("_PREFIX_THEN_FIRST_CHILD = {", "Variable"),
            ("_PREFIX_THEN_FIRST_CHILD = {", "Global"),
            # In both of the folded tables, which the second fold finds in `_HANDLERS`.
            ("_JUST_TEXT = {", "Type"),
        ],
    )
    def test_a_kind_in_two_of_them_refuses_the_module(self, table, kind):
        """Load the module's own source again with one kind added to a table where it
        does not belong: it must not import."""
        path = pathlib.Path(printer_module.__file__)
        source = path.read_text(encoding="utf-8")
        assert source.count(table) == 1
        source = source.replace(table, f'{table}\n    "{kind}": "",')
        module = types.ModuleType("demangle.schemes.swift._probe")
        module.__package__ = "demangle.schemes.swift"
        with pytest.raises(AssertionError, match=kind):
            exec(compile(source, str(path), "exec"), module.__dict__)


class TestPrinterWrite:
    """`Printer.write` is `self.out.append`, bound in `__init__`, and an instance
    attribute shadows a method: a subclass whose `write()` is a method would never be
    called. The class refuses to be subclassed that way, so that the mistake is a
    `TypeError` where the class is made and not a spelling that quietly loses its
    pieces."""

    def test_a_subclass_cannot_define_write(self):
        with pytest.raises(TypeError, match=r"assign self\.write in __init__"):

            class Loud(printer_module.Printer):
                def write(self, text):
                    pass

    def test_a_subclass_cannot_inherit_one_from_a_mixin(self):
        class Mixin:
            def write(self, text):
                pass

        with pytest.raises(TypeError, match="Mixed defines write"):

            class Mixed(Mixin, printer_module.Printer):
                pass

    def test_a_subclass_cannot_set_one_as_a_class_attribute(self):
        with pytest.raises(TypeError):

            class Quiet(printer_module.Printer):
                write = None

    def test_a_subclass_that_assigns_it_in_init_is_called_for_every_piece(self):
        """The way `nodes._TreePrinter` does it, and the way the error says."""

        class Counting(printer_module.Printer):
            def __init__(self):
                super().__init__()
                self.pieces = 0
                self.write = self._write

            def _write(self, text):
                self.pieces += 1
                self.out.append(text)

        mangled = "$s10Foundation11FileManagerC13replaceItemAt_04witheF006backupE4Name7optionsAA3URLVSgAI_AISSSgAC0E18ReplacementOptionsVtKF"
        root = demangle_symbol(mangled)
        counting = Counting()
        counting.print(root, 0)
        plain = printer_module.Printer()
        plain.print(root, 0)
        assert counting.result() == plain.result()
        assert counting.pieces == len(plain.out)

    def test_the_tree_printer_binds_its_own_and_still_spells_what_the_text_path_spells(self):
        assert "write" not in vars(nodes._TreePrinter)
        mangled = "$s4main1SVyQrSicig5innerQryF"
        assert demangle.parse(mangled, language="swift").spell() == spell(mangled)

    def test_the_output_is_the_lists_own_append(self):
        printer = printer_module.Printer()
        assert printer.write == printer.out.append
        printer.write("a")
        printer.write("")
        printer.write("b")
        assert printer.result() == "ab"


class TestPopIsGivenAKindOrASetOfKinds:
    """`Demangler.pop` tells a `str` kind from a set of kinds by `want.__class__ is str`,
    which is the cheapest test there is and which takes a `str` subclass for a set."""

    def test_a_kind_is_compared_whole_and_a_set_by_membership(self):
        reader = Demangler("")
        reader.stack = [Node("Module", "m")]
        assert reader.pop("Modul") is None
        assert reader.pop("Module ") is None
        assert reader.pop(frozenset({"Modul"})) is None
        assert reader.pop(frozenset({"Identifier"})) is None
        assert reader.pop({"Identifier"}) is None
        assert reader.pop(frozenset({"Identifier", "Module"})).kind == "Module"
        assert reader.pop() is None

    def test_every_call_over_the_corpus_passes_one_or_the_other(self, monkeypatch):
        """The comment on `pop` promises it; this is the promise kept for every name the
        corpus holds."""
        wanted = set()
        real = Demangler.pop

        def spy(self, want=None):
            wanted.add(want.__class__)
            return real(self, want)

        monkeypatch.setattr(Demangler, "pop", spy)
        for mangled, _ in load_corpus("swift-upstream.txt") + load_corpus("swift-real-world.txt"):
            demangle_symbol(mangled)
        assert wanted <= {type(None), str, frozenset, set}
        assert {str, frozenset} <= wanted
