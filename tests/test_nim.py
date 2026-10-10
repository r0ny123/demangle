"""Nim symbol names.

Nim has no reference demangler, so what stands in for one is the compiler's own record.
Two independent things are checked, and it matters which is which:

* **Round trip.** Re-mangling what this reads must reproduce the symbol exactly, using a
  transcription of the compiler's `mangle` and `uniqueModuleName`. This is the property;
  it holds for every name in the corpus and it is what makes a reading *a* correct one.
* **Agreement with the compiler.** Nim writes a `.ndi` file mapping each source name to
  the symbol it generated, for a debugger's benefit. The corpus's name half comes from
  there, so the count below is agreement with the compiler, not with ourselves.

The two are separate because the round trip cannot distinguish readings the mangling
maps together, and `tests/conformance/nim-lossy.txt` is exactly that residue: names where
Nim's own `mangle` discarded the difference.
"""

import pathlib
import subprocess

import pytest

import demangle
from demangle.core.errors import DemanglingError
from demangle.schemes.nim._parser import (
    DemangleFailure,
    detect,
    mangle,
    mangle_module,
    parse_nim_symbol,
    unmangle,
    unmangle_module,
)

CONFORMANCE = pathlib.Path(__file__).parent / "conformance"


def rows(name, columns):
    found = []
    for line in (CONFORMANCE / name).read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            parts = line.split("\t")
            if len(parts) == columns:
                found.append(tuple(parts))
    return found


ROWS = rows("nim-real-world.txt", 2)
LOSSES = rows("nim-lossy.txt", 3)

STDLIB_LOSSY_ROUTINES = {
    # pure/hashes.nim: internal helper procs for FarmHash
    "len0_16": "len016",
    "len17_32": "len1732",
    "len33_64": "len3364",
    # wrappers/mysql.nim: MySQL 3.23 protocol helpers (imported with importc)
    "scramble_323": "scramble323",
    "check_scramble_323": "check_scramble323",
    "get_salt_from_password_323": "get_salt_from_password323",
    "make_password_from_salt_323": "make_password_from_salt323",
    "make_scrambled_password_323": "make_scrambled_password323",
}


class TestConformance:
    def test_the_corpus_is_not_empty(self):
        assert len(ROWS) > 2000

    def test_every_recorded_symbol_still_reads_the_same(self, subtests):
        for mangled, expected in ROWS:
            with subtests.test(name=mangled):
                assert parse_nim_symbol(mangled).text == expected

    def test_everything_read_re_mangles_to_what_was_read(self, subtests):
        """The property. A reading that does not reproduce the symbol is not one."""
        for mangled, _expected in ROWS:
            with subtests.test(name=mangled):
                symbol = parse_nim_symbol(mangled)
                nim2 = "_u" in mangled
                assert mangle(symbol.name) + "__" + mangle_module(symbol.module, nim2) in mangled


class TestWhatTheManglingLoses:
    """Nim reserves `_<digit>` for its own disambiguation and drops a source underscore
    that would collide with it. Two different Nim names become one symbol, and nothing
    can tell them apart afterwards -- so this is counted, not rounded off."""

    def test_the_loss_is_small_and_entirely_this_one_cause(self):
        assert len(LOSSES) < len(ROWS) / 100
        for _mangled, original, read in LOSSES:
            assert original.replace("_", "") == read.replace("_", "")
            assert "_" in original

    def test_it_reads_the_other_pre_image_of_each(self, subtests):
        for mangled, _original, read in LOSSES:
            with subtests.test(name=mangled):
                assert parse_nim_symbol(mangled).name == read

    def test_the_eight_stdlib_routines_whose_underscore_is_dropped(self):
        """All 8 routine names across both standard libraries (5,946 total routines)
        that have an underscore before a digit. Nim's mangle() drops the underscore
        unconditionally, making recovery impossible from the symbol alone.
        The 3 in hashes.nim are emitted in Nim 2 as symbols in nim-lossy.txt; the 5 in
        mysql.nim are declared with importc and emit raw C symbols."""
        assert len(STDLIB_LOSSY_ROUTINES) == 8
        for original, mangled_name in STDLIB_LOSSY_ROUTINES.items():
            assert mangle(original) == mangled_name
            assert unmangle(mangled_name) == mangled_name

    def test_and_that_reading_still_re_mangles_correctly(self, subtests):
        """Both names really do mangle to the same symbol; neither reading is wrong."""
        for _mangled, original, read in LOSSES:
            with subtests.test(name=original):
                assert mangle(original) == mangle(read)


class TestGrammar:
    """Rules taken from the compiler's own source and settled by measurement."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("area__mymod_u10", "mymod.area"),
            ("describe__mymod_27", "mymod.describe"),
            # An operator is spelled out word by word, and the trailing `_` marks that
            # something was escaped -- which is why there are three underscores here.
            ("dollar___ops_17", "ops.$"),
            ("eqeq___ops_41", "ops.=="),
            ("X5BX5Deq___allmain_26", "allmain.[]="),
            ("emarkeqeq___ops_101", "ops.!=="),
            # `=destroy` and `:tmp` are the compiler's own names.
            ("eqdestroy___systemZassertions_23", "system/assertions.=destroy"),
            # A module path: `Z` is the separator, `O` a dot.
            ("bitincl__pureZcollectionsZintsets_u387", "pure/collections/intsets.bitincl"),
        ],
    )
    def test_reading(self, mangled, expected):
        assert parse_nim_symbol(mangled).text == expected

    def test_nim_1_writes_a_digit_in_a_path_as_a_code_and_nim_2_does_not(self):
        """The `u` before the id is the only thing that says which, so it is read as a
        version marker. Without it `pureZmd53` is `pure/md53` and the module is wrong."""
        assert parse_nim_symbol("FF__pureZmd53_42").module == "pure/md5"
        assert parse_nim_symbol("encode__pureZbase64_u42").module == "pure/base64"

    def test_an_upper_case_or_underscore_in_a_path_becomes_its_ascii_code(self):
        assert parse_nim_symbol("fromUpperNamed__77y9577odule_1").module == "My_Module"
        assert parse_nim_symbol("fromUpperNamed__77y9577odule_u1").module == "My_Module"
        assert parse_nim_symbol("fromNested__subZdeepZnested95mod_1").module == "sub/deep/nested_mod"

    def test_a_word_is_not_decoded_where_the_name_grammar_forbids_it(self):
        """`result` contains `lt`, and `start` contains `star`. Decoding greedily reads
        them as `resu<` and `*t`, and re-mangling cannot tell: both spellings produce the
        same bytes. What rules them out is that neither is a Nim name -- an operator
        character does not appear inside an identifier."""
        assert unmangle("resultX60gensym4_") == "result`gensym4"
        assert unmangle("startX60gensym26_") == "start`gensym26"
        assert mangle("resu<`gensym4") == "resultX60gensym4_"

    def test_a_setter_is_the_one_place_an_operator_follows_a_name(self):
        assert unmangle("sa_sigactioneq_") == "sa_sigaction="

    def test_a_name_that_is_just_an_underscore_is_not_an_escape_marker(self):
        assert unmangle("_") == "_"

    def test_module_paths_round_trip_under_both_compilers(self):
        for path in (
            "system",
            "pure/collections/tables",
            "std/private/digitsutils",
            "My_Module",
            "sub/deep/nested_mod",
            "pure/base64",
            "a_1",
        ):
            for nim2 in (False, True):
                assert unmangle_module(mangle_module(path, nim2), nim2) == path


class TestOtherShapes:
    @pytest.mark.parametrize(
        ("mangled", "expected", "kind"),
        [
            ("NTIstring__77mFvmsOLKik79ci2hXkHEg_", "type information for string", "type-info"),
            ("tyObject_Widget__uq9ciTN8EVx1oU8m5EUfmRA", "Widget", "type"),
            ("tySequence__3paLwDVN07Xmqd9c79a76Ysg", "sequence", "type"),
            ("Marker_tySequence__3paLwDVN07Xmqd9c79a76Ysg", "garbage-collector marker for sequence", "marker"),
            ("TM__Q5wkpxktOdTGvlSRo9bzt9aw_10", "module temporary #10", "temporary"),
        ],
    )
    def test_reading(self, mangled, expected, kind):
        symbol = parse_nim_symbol(mangled)
        assert (symbol.text, symbol.kind) == (expected, kind)


class TestClaimsNothingItShouldNot:
    """A Nim symbol is an ordinary C identifier, so a detector that guesses is a
    liability. This one parses, and only claims a name a reading re-mangles to."""

    @pytest.mark.parametrize(
        "name",
        [
            "main",
            "printf",
            "_ZN4core3fmt9Formatter3padE",
            "__libc_start_main",
            "_D5mypkg5mymod5Point4normMFZi",
            "foo__bar",
            "x__y_",
            "__",
            "a__b_u",
            "SYSTEM_$$_init",
            "_GLOBAL__sub_I_main.cpp",
            # A lone surrogate, which is what a byte that is not UTF-8 arrives as.
            "\ud800__b_1",
            "tyObject_\ud800__abc",
        ],
    )
    def test_it_refuses(self, name):
        assert not detect(name)
        with pytest.raises(DemangleFailure):
            parse_nim_symbol(name)

    def test_a_byte_that_is_not_text_is_refused_rather_than_raised(self):
        """`demangleb` hands the parser a lone surrogate for every byte that is not UTF-8."""
        assert demangle.demangleb(b"\x80__b_1", language="nim") == b"\x80__b_1"
        with pytest.raises(DemanglingError):
            demangle.demangleb_strict(b"\x80__b_1", language="nim")

    def test_it_claims_nothing_in_the_other_schemes_corpora(self, subtests):
        for path in sorted([*CONFORMANCE.glob("*.txt"), *(CONFORMANCE / "reported").glob("*.txt")]):
            if path.stem.partition("-")[0] == "nim":
                continue
            with subtests.test(name=path.name):
                claimed = [
                    line.split("\t", 1)[0]
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line and not line.startswith("#") and detect(line.split("\t", 1)[0])
                ]
                assert claimed == []

    @pytest.mark.parametrize(
        "binary",
        ["/usr/lib/x86_64-linux-gnu/libstdc++.so.6", "/usr/lib/x86_64-linux-gnu/libc.so.6"],
    )
    def test_it_claims_nothing_in_a_real_c_or_cxx_binary(self, binary):
        if not pathlib.Path(binary).exists():
            pytest.skip(f"{binary} is not installed")
        try:
            out = subprocess.run(["nm", "-D", "--defined-only", binary], capture_output=True, text=True)
        except OSError:
            pytest.skip("nm is not installed")
        if out.returncode != 0:
            pytest.skip("nm cannot read this binary")
        names = {line.split()[-1] for line in out.stdout.splitlines() if line.strip()}
        assert [name for name in names if detect(name)] == []


class TestRegisteredAsALanguage:
    def test_demangle_reaches_it_without_being_told(self):
        assert demangle.demangle("area__mymod_u10") == "mymod.area"

    def test_naming_the_language_works(self):
        assert demangle.demangle("dollar___ops_17", language="nim") == "ops.$"

    def test_it_does_not_take_another_scheme_s_names(self):
        assert demangle.demangle("_Z1fv") == "f()"
        assert demangle.demangle("_D5mypkg5mymod5Point4normMFZi") == "mypkg.mymod.Point.norm()"
        assert demangle.demangle("example.com/m.(*T).Method", language="go") == ("example.com/m.(*T).Method")


class TestTree:
    def test_the_tree_spells_what_demangle_spells(self, subtests):
        for mangled, expected in ROWS[:400]:
            with subtests.test(name=mangled):
                assert demangle.parse(mangled).spell() == expected

    def test_the_module_and_the_name_arrive_separately(self):
        """Splitting the spelling on `.` does not work: a module path is not the only
        thing with dots in it, and `ops..` is the operator `.`."""
        tree = demangle.parse("dot___ops_17")
        assert tree.spell() == "ops.."
        assert [(node.kind, node.text) for node in tree.children()] == [
            ("path", "ops"),
            ("name", "."),
        ]

    def test_find_name_works_as_it_does_for_the_other_schemes(self):
        tree = demangle.parse("bitincl__pureZcollectionsZintsets_u387")
        assert [node.text for node in tree.find("name")] == ["bitincl"]
        assert [node.text for node in tree.find("path")] == ["pure/collections/intsets"]


class TestForeignSymbolsAreDeclined:
    """Nim recognises names by shape, and other compilers produce the same shape.

    OCaml's is the collision that matters: `caml<Module>__<name>_<id>` splits at the
    last `__` into a name, an all-lower-case module and a numeric id, and *re-mangles to
    exactly the symbol it came from* -- so the round-trip property this scheme relies on
    cannot rule it out. `camlStdlib__Int__compare_296` is the pinned case.

    Every symbol the OCaml compiler emits carries the prefix and no Nim symbol does, so
    declining it costs nothing a caller wanted.
    """

    @pytest.mark.parametrize(
        "mangled",
        [
            "camlStdlib__Int__compare_296",
            "camlStdlib__List__map_310",
            "camlDune__exe__Main__entry_42",
            "caml_apply2",
        ],
    )
    def test_an_ocaml_symbol_is_not_claimed(self, mangled):
        assert demangle.detect(mangled) is None
        assert demangle.demangle(mangled) == mangled

    def test_a_caller_who_knows_can_still_ask(self):
        """The refusal is in detection, not in the grammar."""
        with pytest.raises(demangle.DemanglingError):
            demangle.demangle_strict("caml_apply2", language="nim")


@pytest.mark.parametrize("mangled", ["foo__bar_1\n", "tyObject__hash\n", "TM__hash_1\n", "NTIint__hash_\n"])
def test_trailing_newline_is_not_part_of_a_symbol(mangled):
    with pytest.raises(DemangleFailure):
        parse_nim_symbol(mangled)
    assert demangle.demangle(mangled, language="nim") == mangled


@pytest.mark.parametrize("prefix", ["NTIfoo", "NTIv2foo", "tyObject_", "Marker_tyObject_"])
@pytest.mark.parametrize("tail", ["!", "!_", "!__hash_"])
def test_many_type_separators_do_not_hide_an_invalid_hash(prefix, tail):
    mangled = prefix + "_" * 16000 + tail
    if tail == "!__hash_" and prefix.startswith("NTI"):
        # A type-info label is kept verbatim; punctuation in it is legitimate.
        assert parse_nim_symbol(mangled).kind == "type-info"
    else:
        with pytest.raises(DemangleFailure):
            parse_nim_symbol(mangled)
