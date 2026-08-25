"""D mangled names -- the scheme, and an honest record of how far it goes.

D is implemented and is *not registered* as a language. It reads 88.4% of the 16,333
symbols GNU binutils' D demangler can read across the shipped `libgphobos` and
`libgdruntime`, and spells 327 of them differently. Every other scheme here is at 100% on
its real-binary corpus, and a scheme that mis-spells two names in a hundred would put
exactly the plausible-but-wrong output this library treats as worse than silence in front
of a caller who cannot tell.

So it stays behind the door until it is finished. What these tests do is hold the floor:
the corpus is the names it reads exactly today, and the safety properties below are the
ones that must hold whatever the conformance figure is.
"""

import contextlib
import pathlib

import pytest

from demangle.schemes.d._parser import DemangleFailure, parse_d_symbol

CORPUS = pathlib.Path(__file__).parent / "conformance" / "d-real-world.txt"


def corpus():
    rows = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "\t" in line:
            rows.append(tuple(line.split("\t", 1)))
    return rows


ROWS = corpus()


class TestConformanceFloor:
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
