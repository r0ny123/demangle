"""D mangled names.

100% against GNU binutils' D demangler (`c++filt --format=dlang`) over every symbol it
can read in the shipped `libgphobos` and `libgdruntime` -- 16,333 of 19,315 -- with
nothing mis-spelled and nothing refused among them, and no name anywhere raising anything
but `DemangleFailure`.

The grammar rules that had to be *measured* rather than read off the specification are
pinned below, each with the name that settled it. Every one of them was wrong on the
first reading.
"""

import contextlib
import pathlib

import pytest

import demangle
from demangle.schemes.d._parser import DemangleFailure, parse_d_symbol

from .conftest import load_corpus

CORPUS = pathlib.Path(__file__).parent / "conformance" / "d-real-world.txt"


def corpus():
    rows = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "\t" in line:
            rows.append(tuple(line.split("\t", 1)))
    return rows


ROWS = corpus()


class TestConformance:
    def test_the_corpus_is_not_empty(self):
        assert len(ROWS) > 1000

    def test_every_recorded_name_still_reads_exactly(self, subtests):
        for mangled, expected in ROWS:
            with subtests.test(name=mangled):
                assert parse_d_symbol(mangled).text == expected


class TestGrammar:
    """Constructs settled against the reference or the ABI, each with the name that proved it."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # Compiled with gdc 13.3 for this test; checked against c++filt --format=dlang.
            ("_D5mypkg5mymod5Point4normMFZi", "mypkg.mymod.Point.norm()"),
            ("_D5mypkg5mymod6Widget3getMxFZi", "mypkg.mymod.Widget.get() const"),
            ("_D5mypkg5mymod10takesArrayFAiZv", "mypkg.mymod.takesArray(int[])"),
            ("_D5mypkg5mymod12takesPointerFPiZv", "mypkg.mymod.takesPointer(int*)"),
            # Generated symbols name a thing *about* the rest of the path.
            ("_D5mypkg5mymod6Widget6__vtblZ", "vtable for mypkg.mymod.Widget"),
            ("_D5mypkg5mymod6Widget7__ClassZ", "ClassInfo for mypkg.mymod.Widget"),
            ("_D5mypkg5mymod12__ModuleInfoZ", "ModuleInfo for mypkg.mymod"),
            ("_D5mypkg5mymod5Iface11__InterfaceZ", "Interface for mypkg.mymod.Iface"),
            # A template instance, and the back reference that follows it.
            (
                "_D2rt4util7utility__T8_ComplexTdZQm6__initZ",
                "initializer for rt.util.utility._Complex!(double)._Complex",
            ),
        ],
    )
    def test_spelling(self, mangled, expected):
        assert parse_d_symbol(mangled).text == expected

    def test_back_reference_digits_are_zero_based(self):
        """`Qm` is a distance of 12, not 13.

        Measured rather than read off the spec's wording: in the name below the `Q` sits
        at offset 33 and must resolve to the `8_Complex` at offset 21.
        """
        name = "_D2rt4util7utility__T8_ComplexTdZQm6__initZ"
        assert name.index("Qm") - name.index("8_Complex") == 12
        assert "_Complex!(double)" in parse_d_symbol(name).text


class TestSafety:
    """True regardless of how much of the grammar is modelled."""

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "_D",
            "_D0",
            "_D999999999999x",
            "_DQ",
            "_DQa",
            "_D1a" + "Q" * 200,
            "_D" + "1a" * 500,
            "not a d name",
            "\x00\x01",
            "_D1aQzzzzzzzzz",
        ],
    )
    def test_a_malformed_name_is_refused_rather_than_answered(self, value):
        with pytest.raises(DemangleFailure):
            parse_d_symbol(value)

    def test_nothing_escapes_as_another_exception(self):
        """Only `DemangleFailure` may come out; anything else would escape `demangle()`."""
        for mangled, _ in ROWS[:400]:
            for cut in (len(mangled) // 2, len(mangled) - 1):
                with contextlib.suppress(DemangleFailure):
                    parse_d_symbol(mangled[:cut])


class TestRulesThatHadToBeMeasured:
    """Each of these was wrong on the first reading of the specification."""

    def test_a_scope_function_has_no_return_type(self):
        """The grammar gives a scope `TypeFunctionNoReturn`.

        Reading a return type anyway swallowed the following path component, so a symbol
        declared inside a function lost its own name.
        """
        name = "_D2rt3aaA11fakeEntryTIFNbKSQzQy4ImplxC8TypeInfoxQlZ13tiMangledNameyAa"
        assert parse_d_symbol(name).text.endswith(".tiMangledName")

    def test_a_scope_spells_its_parameters(self):
        name = "_D2rt3aaA11fakeEntryTIFNbKSQzQy4ImplxC8TypeInfoxQlZ13tiMangledNameyAa"
        assert "fakeEntryTI(ref rt.aaA.Impl, const(TypeInfo), const(TypeInfo))" in parse_d_symbol(name).text

    def test_q_is_ambiguous_between_an_identifier_and_a_type(self):
        """The trailing `Qq` here is the *return type*, not another path component.

        Testing only whether the byte could open a name made it look like one and took
        the rest of the symbol with it.
        """
        name = "_D2rt6config13rt_linkOptionFNbNiAyaMDFNbNiQkZQnZQq"
        assert parse_d_symbol(name).text.startswith("rt.config.rt_linkOption(")

    def test_a_value_may_have_more_digits_than_a_length_prefix_may(self):
        """`Vmi3988292384` is a perfectly ordinary `ulong`; a 10-digit *length* is not."""
        name = "_D3std6digest3crc__T3CRCVki32Vmi3988292384ZQx3putMFNaNbNiNeAxhXv"
        assert "CRC!(32u, 3988292384uL)" in parse_d_symbol(name).text

    def test_a_symbol_argument_is_a_whole_mangled_name(self):
        """`S_D...` carries a path *and* its type, and the type has to be consumed."""
        name = "_D3std11concurrency__T8initOnceS_DQBg3net4curl7CurlAPI7_handlePvZQBrFNcLQkZQn"
        assert (
            parse_d_symbol(name).text == "std.concurrency.initOnce!(std.net.curl.CurlAPI._handle).initOnce(lazy void*)"
        )

    def test_variadic_x_takes_no_separator(self):
        """`f(T t...)` for `X`, `f(T t, ...)` for `Y` -- the difference is the comma."""
        name = "_D3std6digest3crc__T3CRCVki32Vmi3988292384ZQx3putMFNaNbNiNeAxhXv"
        assert parse_d_symbol(name).text.endswith("(const(ubyte)[]...)")

    def test_an_array_literal_drops_the_element_suffix(self):
        """`[104, 1281]`, not `[104uL, 1281uL]`, though each element is a `ulong`."""
        name = "_D6object__T10RTInfoImplVAmA2i104i1281ZQBbyG2m"
        assert parse_d_symbol(name).text == "object.RTInfoImpl!([104, 1281]).RTInfoImpl"

    def test_postblit_is_renamed_only_when_the_function_has_no_attributes(self):
        """A reference quirk, not a distinction the language makes.

        Followed exactly rather than approximated in either direction, because the
        corpus is what this library is measured against.
        """
        assert parse_d_symbol("_D3foo3Bar10__postblitMFZv").text == "foo.Bar.this(this)"
        assert parse_d_symbol("_D3foo3Bar10__postblitMFNaNbNiNfZv").text == "foo.Bar.__postblit()"


class TestAgainstLibibertysOwnCorpus:
    """The reference's vectors, not this project's.

    `d-real-world.txt` is a corpus this project assembled, and the ROADMAP's claim of
    100% against `c++filt --format=dlang` is true of it. It is not true of libiberty's
    own `d-demangle-expected`, which is larger and which this project had not adopted:
    149 of its 366 vectors do not match, in a handful of systematic groups (`__T`/`__U`
    template instantiations, `B<n>` tuples spelled `Tuple!(...)`, `Nh` vector types,
    `Nn` as `typeof(*null)`, `_Dmain`).

    Checked in with the score pinned, so the number can only go down. A test that says
    "some of these fail" is worth more than a claim that none do.
    """

    #: Raised as the gaps close. Never lowered silently: a drop means a vector that used
    #: to pass has stopped, which is a regression whatever the total.
    EXPECTED_EXACT = 217

    def _score(self):
        exact = 0
        for mangled, expected in load_corpus("d-libiberty.txt"):
            if demangle.demangle(mangled, language="d") == expected:
                exact += 1
        return exact

    def test_the_score_has_not_gone_backwards(self):
        exact = self._score()
        total = len(load_corpus("d-libiberty.txt"))
        assert exact >= self.EXPECTED_EXACT, f"{exact}/{total}, was {self.EXPECTED_EXACT}"

    def test_the_pinned_number_is_still_accurate(self):
        """So the pin is a fact rather than a floor nobody has looked at."""
        assert self._score() == self.EXPECTED_EXACT

    def test_every_vector_is_answered_promptly(self):
        """The corpus holds a real symbol that took over 35 seconds.

        `std.format.formattedWrite`, 2,695 characters with 441 `Q` back references in
        it. The parser was not looping -- three hundred thousand calls in all -- it was
        assembling a string far larger than any caller would accept, and `max_output`
        was checked on the finished string. A bound observed after the work is a report.
        """
        import time

        for mangled, _ in load_corpus("d-libiberty.txt"):
            started = time.perf_counter()
            demangle.demangle(mangled, language="d")
            elapsed = time.perf_counter() - started
            assert elapsed < 1.0, f"{elapsed:.1f}s for {mangled[:60]}"
