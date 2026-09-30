"""Ada, as GNAT encodes it, against libiberty's own vectors.

The reference is `ada_demangle` in `libiberty/cplus-dem.c`, reached as
`c++filt --format=gnat`, and the corpus is the 34 cases `demangle-expected` marks
`--format=gnat` at GCC 8.3.0.

The interesting tests here are not the conformance ones -- a scheme with no types in it
is a small grammar and it either transcribes or it does not. They are the detection ones.
An Ada symbol is a lower-case dotted path with no marker saying whose it is, so the
question "is this Ada?" has no syntactic answer, and the tests below pin what was
measured rather than what seems reasonable.
"""

import gzip
import shutil
import subprocess
from pathlib import Path

import pytest

import demangle
from demangle.core.errors import DemanglingError
from demangle.schemes import ada
from demangle.schemes.ada._parser import DemangleFailure, demangle_ada

from .conftest import CONFORMANCE, load_corpus
from .test_conformance import ADA_AUTODETECTED, ADA_EXACT, ADA_TOTAL

CORPUS = "ada-libiberty.txt"


def vectors():
    return load_corpus(CORPUS)


class TestAgainstLibertysOwnVectors:
    def test_the_corpus_is_the_size_it_was(self):
        assert len(vectors()) == ADA_TOTAL

    def test_every_vector_matches_the_reference(self, subtests):
        exact = 0
        for mangled, expected in vectors():
            with subtests.test(mangled=mangled):
                got = demangle.demangle(mangled, language="ada")
                assert got == expected
                exact += 1
        assert exact == ADA_EXACT

    def test_the_one_name_the_reference_declines_is_declined_here(self):
        # `c++filt --format=gnat` answers `<x_E>`, its way of saying "not mine".
        assert demangle.demangle("x_E", language="ada") == "x_E"
        with pytest.raises(DemanglingError):
            demangle.demangle_strict("x_E", language="ada")


class TestTheShapesTheSchemeExistsFor:
    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("yz__qrs", "yz.qrs"),
            ("oper__Oadd", 'oper."+"'),
            ("_ada_x__m3", "x.m3"),
            ("p___elabb", "p'Elab_Body"),
            ("p___elabs", "p'Elab_Spec"),
            ("p__taskobjTKB", "p.taskobj"),
            ("p__taskobjTK__f1.2330", "p.taskobj.f1"),
            ("prot__lock__getP", "prot.lock.get"),
            ("prot__lock__update_B7s", "prot.lock.update"),
            ("system__partition_interface__racw_stub_typeDA", "system.partition_interface.racw_stub_type.Adjust"),
            ("system__finalization_root__root_controlledSI", "system.finalization_root.root_controlled'Input"),
            ("ada__synchronous_task_control___size__2", "ada.synchronous_task_control'Size"),
            ("system__finalization_root___assign__2", 'system.finalization_root.":="'),
            ("x__y__z__rXb", "x.y.z.r"),
        ],
    )
    def test_one_of_each(self, mangled, expected):
        assert demangle.demangle(mangled, language="ada") == expected

    @pytest.mark.parametrize(
        "mangled",
        [
            "Yz__qrs",  # an Ada unit name is lower case
            "yz__Oyikes",  # not an operator in the table
            "yz__qrsSZ",  # not a stream operation
            "yz__qrsDZ",  # not a controlled-type operation
            "yz__qrs_B7",  # an entry body must end in `s`
            "x_E",  # an exception name: the reference declines these
            "yz__qrsS",  # an enumerated type name table: likewise
            "",
        ],
    )
    def test_what_the_grammar_refuses(self, mangled):
        with pytest.raises(DemangleFailure):
            demangle_ada(mangled)


class TestDetectionDeclinesWhatItCannotTell:
    """The measured part, and the reason this scheme is written the way it is.

    A GNAT symbol carries no types and no prefix. `yz__qrs` is a package and a
    subprogram, and it is also exactly what a C program writes. So detection asks for
    something GNAT wrote that a C compiler would not, and the numbers below are why:
    without that test, parsing-and-claiming reads 6,764 of this machine's own symbols as
    Ada.
    """

    def test_a_name_with_no_gnat_encoding_in_it_is_not_claimed(self):
        for name in ("yz__qrs", "x__m1", "x__y__j", "not_mangled__at_all", "std__vector"):
            assert not ada.detect(name), name
            assert demangle.demangle(name) == name

    def test_but_it_still_reads_when_a_caller_names_the_language(self):
        assert demangle.demangle("yz__qrs", language="ada") == "yz.qrs"
        assert demangle.demangle("x__y__j", language="gnat") == "x.y.j"

    def test_a_name_carrying_a_gnat_encoding_is_claimed(self):
        for name in ("_ada_x__m3", "oper__Oadd", "p__taskobjTKB", "prot__lock__getP"):
            assert ada.detect(name), name

    def test_how_many_vectors_detection_claims_on_its_own(self):
        readable = [(m, e) for m, e in vectors() if e != m]
        auto = sum(1 for m, e in readable if demangle.demangle(m) == e)
        assert auto == ADA_AUTODETECTED
        assert len(readable) - auto == 4  # the four with no GNAT encoding in them

    def test_no_name_from_any_other_corpus_is_read_as_ada(self):
        """Over every checked-in corpus, the type ones included: none taken.

        The type corpora are in scope rather than excused. `rDF16_` -- the Itanium
        encoding of `_Float16 restrict` -- really does parse as `r.Finalize` with `16_`
        left over, and it is the name that made the "fully accounted for" half of
        `detect` exist. It is reached only through `demangle_type()`, where the caller
        has already said which scheme it belongs to, but a detection rule that would
        claim it if asked is a rule with a hole in it.
        """
        claimed = []
        for path in sorted(
            [*CONFORMANCE.glob("*.txt"), *CONFORMANCE.glob("*.txt.gz"), *(CONFORMANCE / "reported").glob("*.txt")]
        ):
            if path.stem.partition("-")[0] == "ada":
                continue
            text = (
                gzip.decompress(path.read_bytes()).decode("utf-8")
                if path.suffix == ".gz"
                else path.read_text(encoding="utf-8")
            )
            for line in text.splitlines():
                if not line or line.startswith("#"):
                    continue
                name = line.split("\t")[0]
                if ada.detect(name):
                    claimed.append(name)
        assert claimed == []

    @pytest.mark.sweep
    def test_no_real_symbol_on_this_machine_is_read_as_ada(self):
        """The measurement the docstring quotes, run rather than remembered.

        Skipped where `nm` is not available, which is every platform that is not the one
        this number was taken on. It is the check that would catch a widening of the
        evidence test, so it is worth running where it can be.

        The listing is a glob rather than a shell, and that is not a style preference.
        `bash` on a Windows runner is `C:\\Windows\\System32\\bash.exe` -- the WSL
        launcher -- which, with no distribution installed, writes its complaint to
        *stdout* in UTF-16. Decoded as text that is `'W\\x00i\\x00n\\x00...'`, which
        passed the `if not listing` guard and reached `subprocess` as a filename with
        NUL bytes in it. A directory that does not exist globs to nothing on every
        platform, which is the answer this wanted in the first place.
        """
        listing = []
        for directory in (Path("/usr/lib/x86_64-linux-gnu"), Path("/lib/x86_64-linux-gnu")):
            if directory.is_dir():
                listing.extend(str(path) for path in sorted(directory.glob("*.so*")))
        listing = listing[:120]
        if not listing:
            pytest.skip("no shared libraries to read symbols from")
        if shutil.which("nm") is None:
            pytest.skip("nm is not available")
        seen = 0
        claimed = []
        for library in listing:
            try:
                out = subprocess.run(
                    ["nm", "-D", "--defined-only", library], capture_output=True, text=True, timeout=30
                ).stdout
            except (OSError, subprocess.SubprocessError):
                pytest.skip("nm is not available")
            for line in out.splitlines():
                bits = line.split()
                if len(bits) < 3:
                    continue
                seen += 1
                if ada.detect(bits[-1]):
                    claimed.append(bits[-1])
        if seen < 1000:
            pytest.skip("too few symbols read to make the measurement mean anything")
        assert claimed == []

    def test_parsing_without_the_evidence_test_is_what_it_would_cost(self):
        """The counterfactual, so the number in the docstring is checked and not asserted.

        Every one of these parses. None is Ada. This is the policy the scheme rejects.
        """
        would_claim = []
        for name in ("rDF16_", "sil", "main", "d__d", "gnu"):
            try:
                demangle_ada(name)
            except DemangleFailure:
                continue
            would_claim.append(name)
        assert would_claim, "the counterfactual has stopped demonstrating anything"
        for name in would_claim:
            assert not ada.detect(name), name


class TestSharingTheOverlapWithTheOtherPreItaniumSchemes:
    def test_ada_is_offered_before_the_two_cpp_ones(self):
        from demangle.core.registry import available

        order = [plugin.name for plugin in available()]
        assert order.index("ada") < order.index("gnuv2") < order.index("codewarrior")

    def test_a_gnat_name_would_otherwise_be_read_as_cpp(self):
        # GNU v2 would read this and get a wrong name, which is worse than none.
        mangled = "p__taskobjTKB"
        assert demangle.demangle(mangled) == "p.taskobj"
        assert demangle.demangle(mangled, language="gnuv2") != "p.taskobj"


class TestTheTree:
    def test_the_tree_renders_to_exactly_what_the_text_path_spells(self):
        for mangled, expected in vectors():
            if expected == mangled:
                continue
            tree = demangle.parse(mangled, language="ada")
            # `str()` rather than `.render()`: the spelling a caller gets is the one on
            # `Node`, and pinning that is what stops the tree and the text path drifting.
            assert str(tree) == expected, mangled

    def test_the_components_come_back_separated(self):
        tree = demangle.parse("gnat__sockets__sockets_library_controllerDF__2", language="ada")
        components = [node.text for node in tree.walk() if node.kind == "component"]
        assert components == ["gnat", "sockets", "sockets_library_controller"]

    def test_an_attribute_is_not_a_path_component(self):
        # `.split(".")` on the spelling would get this wrong twice over: `.Finalize` is
        # not a component, and `.":="` has a `.` inside one.
        tree = demangle.parse("system__finalization_root___assign__2", language="ada")
        attributes = [node.text for node in tree.walk() if node.kind == "attribute"]
        assert attributes == ['.":="']
        tree = demangle.parse("ada__synchronous_task_control___size__2", language="ada")
        assert [node.text for node in tree.walk() if node.kind == "attribute"] == ["'Size"]

    def test_the_published_vocabulary_is_what_the_trees_hold(self):
        kinds = set()
        for mangled, expected in vectors():
            if expected == mangled:
                continue
            kinds |= {node.kind for node in demangle.parse(mangled, language="ada").walk()}
        assert kinds <= set(ada.PLUGIN.node_kinds)


class TestBounds:
    def test_a_name_longer_than_the_input_bound_is_refused(self):
        from dataclasses import replace

        limits = replace(demangle.DEFAULT_LIMITS, max_input=8)
        with pytest.raises(demangle.LimitExceeded):
            demangle.demangle_strict("yz__qrs__tuv", language="ada", limits=limits)

    def test_the_empty_name_is_refused(self):
        with pytest.raises(DemanglingError):
            demangle.demangle_strict("", language="ada")


class TestAdaRealWorld:
    def test_every_vector_matches_the_reference(self, subtests):
        rows = load_corpus("ada-real-world.txt")
        assert len(rows) == 1438
        for mangled, expected in rows:
            with subtests.test(mangled=mangled):
                assert demangle.demangle(mangled) == expected


class TestTheNameThatAbortsTheReference:
    """`c++filt --format=gnat` from binutils 2.42 dies on a shape this reads.

        $ printf 'aSO__bDF\n' | c++filt --format=gnat
        *** buffer overflow detected ***: terminated
        Aborted

    Eight characters, and all three parts are needed: an `SO` attribute marker, a `__`
    separator, and a `DF` suffix. Drop any one -- `aSO__b`, `a__bDF`, `aSObDF` -- and it
    returns normally, so it is the two expansions in one name that do it. Only the GNAT
    format reaches it; `c++filt` left to detect the scheme does not.

    `tools/mutate.py --seed 37` found it as a mutant of a GNAT runtime symbol, and it
    took the whole Ada run down: the reference answered 6,933 of 20,000 names and the
    tool could pair none of them. `ask_tolerantly` splits a batch down to the name
    that did it and leaves that one out of the comparison, which is why the seed
    completes.

    This library reads all of these. The test is here so that stays true, and so the
    shape is written down somewhere other than a fuzzer's output.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("aSO__bDF", "a'Output.b.Finalize"),
            ("tSO__gDF", "t'Output.g.Finalize"),
            (
                "gnat__wide_string_split__slice_setSO__vstring__table_arrayDF",
                "gnat.wide_string_split.slice_set'Output.vstring.table_array.Finalize",
            ),
        ],
    )
    def test_it_reads_here(self, mangled, expected):
        assert demangle.demangle(mangled, language="ada") == expected
