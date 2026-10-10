"""Free Pascal symbol names.

Free Pascal ships no demangler, so nothing here can be a comparison against a reference.
Two other things stand in, and they establish different halves of the answer:

* **Re-assembly.** The parts this splits out, rejoined with the compiler's own
  separators, must reproduce the symbol exactly. Checked over every readable symbol in
  the shipped runtime -- 236,570 of them -- not just the sample recorded here. This is
  what says the reading accounts for the whole name and invents nothing.
* **What the units actually declare.** `ppudump` prints both a unit's mangled names and
  the names it declares. Every name this reads is one the unit declares, bar the
  compiler's own `init` and `finalize` sections, which no source declares.

Neither would do alone: re-assembly says the split is faithful but not that the pieces
mean what we say, and the `ppudump` check says the routine name is right but nothing
about the parameters.
"""

import pathlib
import subprocess

import pytest

import demangle
from demangle.schemes.pascal._parser import (
    DemangleFailure,
    detect,
    parse_pascal_symbol,
)

CONFORMANCE = pathlib.Path(__file__).parent / "conformance"


def rows(name):
    return [
        tuple(line.split("\t", 1))
        for line in (CONFORMANCE / name).read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "\t" in line
    ]


ROWS = rows("pascal-real-world.txt")
REFUSALS = [
    line
    for line in (CONFORMANCE / "pascal-refusals.txt").read_text(encoding="utf-8").splitlines()
    if line and not line.startswith("#")
]


def reassemble(symbol):
    """Put the parts back together the way `make_mangledname` does.

    The raw scope and signature are kept by the parser precisely so this can be exact:
    enclosing classes join with `_$_` and enclosing procedures with a bare `_`, and an
    over-long parameter list is a checksum that cannot be expanded.
    """
    if symbol.kind in ("label", "section"):
        return symbol.raw
    out = "" if symbol.kind == "routine" else symbol.kind + "_$"
    out += symbol.unit
    if symbol.raw_scope:
        out += "$_$" + symbol.raw_scope
    out += "_$$_" + symbol.raw_signature
    return out


def core(mangled):
    """The symbol without the decorations that sit outside the mangling proper."""
    if mangled.endswith("$indirect"):
        mangled = mangled[: -len("$indirect")]
    for table in ("_o2s", "_s2o"):
        if mangled.endswith(table):
            mangled = mangled[: -len(table)]
    return mangled


class TestConformance:
    def test_the_corpus_covers_the_shapes(self):
        assert len(ROWS) > 3800

    def test_every_recorded_symbol_still_reads_the_same(self, subtests):
        for mangled, expected in ROWS:
            with subtests.test(name=mangled):
                assert parse_pascal_symbol(mangled).text == expected

    def test_every_reading_re_assembles_to_what_it_was_read_from(self, subtests):
        """The property. A reading that cannot account for the whole symbol is not one."""
        for mangled, _expected in ROWS:
            with subtests.test(name=mangled):
                assert reassemble(parse_pascal_symbol(mangled)) == core(mangled)

    def test_it_still_refuses_what_is_not_a_pascal_symbol(self, subtests):
        """These sit in the same object files -- soft-float helpers, and a handful of
        Itanium C++ names from the interop units -- and none of them is a Pascal
        mangling. A few *are* readable, by the C++ scheme; what matters is that this one
        does not claim them."""
        for mangled in REFUSALS:
            with subtests.test(name=mangled):
                assert not detect(mangled)


class TestGrammar:
    """Rules taken from `compiler/symdef.pas` and settled against the shipped runtime."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("MYUNIT_$$_ADD$LONGINT$LONGINT$$LONGINT", "MYUNIT.ADD(LONGINT, LONGINT): LONGINT"),
            # A method's scope ends in a bare `_$_`, which is where that run of
            # underscores comes from.
            ("MYUNIT$_$TWIDGET_$__$$_AREA$$LONGINT", "MYUNIT.TWIDGET.AREA: LONGINT"),
            # A procedure nested in a method: the enclosing method's own name and
            # parameters sit in the scope, so that two overloads' nested procedures do
            # not collide.
            ("APP$_$TDESKTOP_$_CASCADE$TRECT_$$_DOCASCADE$PVIEW", "APP.TDESKTOP.CASCADE$TRECT.DOCASCADE(PVIEW)"),
            ("VMT_$MYUNIT_$$_TWIDGET", "virtual method table for MYUNIT.TWIDGET"),
            ("RTTI_$MYUNIT_$$_TCOLOUR", "run-time type information for MYUNIT.TCOLOUR"),
            ("INIT_$MYUNIT_$$_TPOINTREC", "initialisation type information for MYUNIT.TPOINTREC"),
            ("U_$ADVANCEDIPC_$$_X", "variable ADVANCEDIPC.X"),
            ("TC_$APP_$$_X", "typed constant APP.X"),
            ("RESSTR_$ADVANCEDIPC_$$_END", "resource string ADVANCEDIPC.END"),
            ("IID_$CHMREADER_$$_ICOMPARER", "interface identifier for CHMREADER.ICOMPARER"),
            ("FINALIZE$_$ADVANCEDIPC", "finalisation of ADVANCEDIPC"),
            ("INIT$_$ADVANCEDIPC", "initialisation of ADVANCEDIPC"),
            ("_$A52$_Ld1", "assembler label Ld1 in A52"),
        ],
    )
    def test_reading(self, mangled, expected):
        assert parse_pascal_symbol(mangled).text == expected

    def test_an_operator_is_written_with_a_leading_dollar(self):
        """`defaultmangledname` puts a `$` in front of an operator's internal name so
        that it stays distinct when the symbol is lower-cased for a section name. Reading
        the `$` as a parameter separator turns `+` into a parameter called `plus`."""
        symbol = parse_pascal_symbol("MYUNIT_$$_$plus$TPOINTREC$TPOINTREC$$TPOINTREC")
        assert symbol.name == "$plus"
        assert symbol.parameters == ("TPOINTREC", "TPOINTREC")
        assert symbol.text == "MYUNIT.operator +(TPOINTREC, TPOINTREC): TPOINTREC"

    def test_an_over_long_parameter_list_is_a_checksum_and_is_said_to_be(self):
        """Over about twelve characters the compiler writes `$crc` and a digest instead
        of the types. There is nothing to expand, so this says so rather than reporting
        no parameters."""
        symbol = parse_pascal_symbol("A52_$$_A52_DECODER_INIT$crc3BB10825")
        assert symbol.name == "A52_DECODER_INIT"
        assert symbol.elided
        assert symbol.text == "A52.A52_DECODER_INIT(<parameters elided by the compiler>)"

    def test_a_generic_type_keeps_its_own_dollars(self):
        """`TList$1$crc04FD2F37` is one type, not three parameters."""
        symbol = parse_pascal_symbol(
            "CHMREADER$_$TLIST$1$CRC04FD2F37_$__$$_GETENUMERATOR$$TList$1$crc04FD2F37.TENUMERATOR"
        )
        assert symbol.scope == ("TLIST$1$CRC04FD2F37",)
        assert symbol.parameters == ()
        assert symbol.result == "TList$1$crc04FD2F37.TENUMERATOR"

    def test_a_wrapper_carries_a_whole_symbol_inside_it(self):
        """`WRPR`'s suffix is the class, the interface, the table index and then the
        complete mangled name of the implementing method -- so that last part is parsed
        as a symbol of its own rather than as a signature."""
        symbol = parse_pascal_symbol(
            "WRPR_$CHMREADER_$$_TCOMPARER$1$CRC04FD2F37_$_ICOMPARER$1$CRC04FD2F37_$_0"
            "_$_SYSTEM$_$TINTERFACEDOBJECT_$__$$_QUERYINTERFACE$TGUID$$LONGINT"
        )
        assert symbol.text == (
            "interface wrapper for CHMREADER.TCOMPARER$1$CRC04FD2F37.ICOMPARER$1$CRC04FD2F37 #0: "
            "SYSTEM.TINTERFACEDOBJECT.QUERYINTERFACE(TGUID): LONGINT"
        )

    def test_a_programs_own_symbols_are_marked(self):
        """`P$` keeps a program's symbols from colliding with a unit of the same name."""
        assert parse_pascal_symbol("P$PROG_$$_MAIN").text == "program PROG.MAIN"

    def test_case_does_not_come_back(self):
        """The compiler upper-cases before it mangles, so `Add` and `ADD` are one symbol.
        This is stated rather than papered over."""
        assert parse_pascal_symbol("MYUNIT_$$_ADD$LONGINT$$LONGINT").name == "ADD"


class TestClaimsNothingItShouldNot:
    @pytest.mark.parametrize(
        "name",
        [
            "main",
            "printf",
            "_Z1fv",
            "_ZN4core3fmt9Formatter3padE",
            "area__mymod_u10",
            "_D5mypkg5mymod5Point4normMFZi",
            "FLOAT64_ADD",
            "$",
            "_$",
            "A_$$_",
            # An empty parameter type, an empty result type, and both.
            "MYUNIT_$$_ADD$",
            "MYUNIT_$$_ADD$$",
            "MYUNIT_$$_ADD$LONGINT$$",
            "MYUNIT_$$_ADD$$LONGINT$$LONGINT",
        ],
    )
    def test_it_refuses(self, name):
        assert not detect(name)

    def test_the_compilers_own_sections_keep_their_lone_separator(self):
        """`init`, `finalize` and their `_implicit` forms are written with the parameter
        separator and nothing behind it -- 29 times in the real-world corpus -- so for
        those four names alone the lone separator is the empty list."""
        assert demangle.demangle("AVL_TREE_$$_init$") == "AVL_TREE.init()"
        assert demangle.demangle("MYUNIT_$$_finalize_implicit$") == "MYUNIT.finalize_implicit()"
        assert demangle.demangle("MYUNIT_$$_ADD$LONGINT$$LONGINT") == "MYUNIT.ADD(LONGINT): LONGINT"

    def test_it_claims_nothing_in_the_other_schemes_corpora(self, subtests):
        for path in sorted([*CONFORMANCE.glob("*.txt"), *(CONFORMANCE / "reported").glob("*.txt")]):
            if path.stem.partition("-")[0] == "pascal":
                continue
            with subtests.test(name=path.name):
                claimed = [
                    line.split("\t", 1)[0]
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line and not line.startswith("#") and detect(line.split("\t", 1)[0])
                ]
                assert claimed == []


class TestAgainstTheCompilersOwnRecord:
    """`ppudump` prints a unit's mangled names beside the names it declares."""

    UNITS = "/usr/lib/x86_64-linux-gnu/fpc/3.2.2/units/x86_64-linux"

    def test_every_name_read_is_one_the_unit_declares(self):
        import glob
        import re

        found = sorted(glob.glob(f"{self.UNITS}/*/*.ppu"))[:60]
        if not found:
            pytest.skip("Free Pascal's compiled units are not installed")
        checked = agreed = 0
        disagreed = []
        for ppu in found:
            try:
                dumped = subprocess.run(["ppudump", "-Va", ppu], capture_output=True, text=True)
            except OSError:
                pytest.skip("ppudump is not installed")
            if dumped.returncode != 0:
                pytest.skip("ppudump cannot read these units")
            out = dumped.stdout
            declared = set()
            for kind in (
                "Procedure symbol",
                "Variable symbol",
                "Type symbol",
                "Constant symbol",
                "Static Variable symbol",
                "Property symbol",
                "Field Variable symbol",
                "Absolute Variable symbol",
                "Enumeration symbol",
            ):
                declared.update(m.upper().lstrip("$") for m in re.findall(rf"^\s*{kind} (.+)$", out, re.M))
            aliases = [
                piece.strip()
                for line in re.findall(r"^\s*(?:Alias names|Mangled name)\s*: (.+)$", out, re.M)
                for piece in line.split(",")
            ]
            for alias in aliases:
                try:
                    symbol = parse_pascal_symbol(alias)
                except DemangleFailure:
                    continue
                if symbol.kind in ("label", "section"):
                    continue
                checked += 1
                # `init` and `finalize` are the compiler's own sections; no source
                # declares them, so they cannot be in the declared set.
                if symbol.name.upper().lstrip("$") in declared or symbol.name.lower() in (
                    "init",
                    "finalize",
                    "init_implicit",
                    "finalize_implicit",
                ):
                    agreed += 1
                else:
                    disagreed.append((alias, symbol.name))
        assert checked > 500
        assert disagreed == [], disagreed[:10]


class TestRegisteredAsALanguage:
    def test_demangle_reaches_it_without_being_told(self):
        assert demangle.demangle("MYUNIT_$$_ADD$LONGINT$$LONGINT") == "MYUNIT.ADD(LONGINT): LONGINT"

    def test_naming_the_language_works(self):
        for name in ("pascal", "fpc", "freepascal"):
            assert demangle.demangle("VMT_$MYUNIT_$$_TWIDGET", language=name) == (
                "virtual method table for MYUNIT.TWIDGET"
            )

    def test_it_does_not_take_another_scheme_s_names(self):
        assert demangle.demangle("_Z1fv") == "f()"
        assert demangle.demangle("area__mymod_u10") == "mymod.area"


class TestTree:
    def test_the_tree_spells_what_demangle_spells(self, subtests):
        for mangled, expected in ROWS[:600]:
            with subtests.test(name=mangled):
                assert demangle.parse(mangled).spell() == expected

    def test_the_parts_arrive_separately(self):
        tree = demangle.parse("MYUNIT$_$TWIDGET_$__$$_AREA$$LONGINT")
        assert [(node.kind, node.text) for node in tree.children()] == [
            ("path", "MYUNIT"),
            ("name", "TWIDGET"),
            ("name", "AREA"),
            ("name", "LONGINT"),
        ]

    def test_parameters_are_reachable_as_a_list(self):
        tree = demangle.parse("MYUNIT_$$_ADD$LONGINT$LONGINT$$LONGINT")
        parameters = next(tree.find("parameters"))
        assert [node.text for node in parameters.children()] == ["LONGINT", "LONGINT"]

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # A program's unit carries a `P$` that the spelling drops; the tree must still
            # find the unit under its raw name to keep the lead.
            ("U_$P$XLIB_$$_PX_OPEN_F", "program variable XLIB.PX_OPEN_F"),
            ("TC_$P$PROG_$$_C1", "program typed constant PROG.C1"),
            ("U_$XLIB_$$_PX_OPEN_F", "variable XLIB.PX_OPEN_F"),
            ("P$PROG_$$_MAIN", "program PROG.MAIN"),
        ],
    )
    def test_a_programs_lead_survives_into_the_tree(self, mangled, expected):
        assert demangle.demangle_strict(mangled) == expected
        assert demangle.parse(mangled).spell() == expected


@pytest.mark.parametrize("mangled", ["INIT$_$FOO\n", "_$FOO$_La1\n"])
def test_trailing_newline_is_not_part_of_a_symbol(mangled):
    with pytest.raises(DemangleFailure):
        parse_pascal_symbol(mangled)
    assert demangle.demangle(mangled, language="pascal") == mangled
