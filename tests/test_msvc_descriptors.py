"""RTTI type descriptors, and the names inside them.

A `type_info` points at a string, and the linker spells that string as a `.` followed by
a bare type encoding: `.PEAX`, `.?AVFoo@@`. It is not a decorated name -- there is no `?`
and nothing is declared -- so this scheme used to refuse every one of them, and a PE
symbol dump full of them said nothing.

The marker goes where a *declarator* goes rather than after the type, which for anything
that wraps its name is a different place: `int (*`RTTI Type Descriptor Name')[2]`, not
`int (*)[2] `RTTI Type Descriptor Name''. The descriptor *object*, `??_R0<type>@8`, was
already read and had the same rule and the same bug.

Claiming a leading `.` in a symbol table full of `.text`, `.rodata`, `.L1234` and
`.constprop.0` is the risk here, and it is answered by measurement rather than by
argument: what follows the dot has to parse as a *whole* type before anything is said,
and none of those does.
"""

import pathlib

import demangle

from .conftest import load_corpus
from .test_conformance import MSVC_DESCRIPTORS_EXACT, MSVC_DESCRIPTORS_TOTAL

#: Section and label names a real object file carries. None may be claimed.
NOT_SYMBOLS = [
    ".text",
    ".data",
    ".rodata",
    ".bss",
    ".init",
    ".fini",
    ".eh_frame",
    ".comment",
    ".note",
    ".debug_info",
    ".debug_abbrev",
    ".debug_line",
    ".debug_str",
    ".symtab",
    ".strtab",
    ".L1",
    ".L1234",
    ".LC0",
    ".Lfunc_end0",
    ".Ltmp3",
    ".constprop.0",
    ".isra.0",
    ".part.1",
    ".cold",
    ".local",
    ".plt",
    ".plt.sec",
    ".got",
    ".got.plt",
    ".idata",
    ".rsrc",
    ".pdata",
    ".xdata",
    ".CRT",
    ".tls",
    ".gcc_except_table",
    ".tdata",
    ".tbss",
    ".init_array",
    ".fini_array",
    ".dynamic",
    ".dynsym",
    ".dynstr",
    ".rela.dyn",
    ".rela.plt",
    ".interp",
    ".gnu.hash",
    ".gnu.version",
    ".note.ABI-tag",
    ".hash",
    ".shstrtab",
]


class TestAgainstLlvmUndname:
    def test_the_corpus_is_the_whole_of_it(self):
        assert len(load_corpus("msvc-type-descriptors.txt")) == MSVC_DESCRIPTORS_TOTAL

    def test_every_vector_matches(self):
        for mangled, expected in load_corpus("msvc-type-descriptors.txt"):
            assert demangle.demangle(mangled) == expected, mangled

    def test_the_pinned_number_is_still_accurate(self):
        score = sum(
            1
            for mangled, expected in load_corpus("msvc-type-descriptors.txt")
            if demangle.demangle(mangled) == expected
        )
        assert score == MSVC_DESCRIPTORS_EXACT

    def test_the_marker_sits_where_a_declarator_sits(self):
        """The one thing that is not "append some words"."""
        assert demangle.demangle(".PEAY01H") == "int (*`RTTI Type Descriptor Name')[2]"
        assert demangle.demangle("??_R0P6AHH@Z@8") == "int (__cdecl *`RTTI Type Descriptor')(int)"

    def test_the_name_and_the_object_say_the_same_thing_bar_one_word(self):
        for mangled, expected in load_corpus("msvc-type-descriptors.txt"):
            if not mangled.startswith("."):
                continue
            assert demangle.demangle(f"??_R0{mangled[1:]}@8") == expected.replace(" Name'", "'")


class TestWhatIsNotADescriptor:
    def test_a_section_or_label_name_is_left_alone(self):
        for name in NOT_SYMBOLS:
            assert demangle.demangle(name) == name, name

    def test_no_dot_prefixed_name_in_any_corpus_is_read_as_msvc(self):
        """731 of them, and every one belongs to Objective-C, which is offered first."""
        directory = pathlib.Path(__file__).parent / "conformance"
        seen = 0
        for path in sorted(directory.glob("*.txt")):
            if path.name == "msvc-type-descriptors.txt":
                continue
            for mangled, _ in load_corpus(path.name):
                if not mangled.startswith("."):
                    continue
                seen += 1
                assert demangle.detect(mangled) != "msvc", mangled
        assert seen > 700, "the dot-prefixed names went missing"

    def test_the_whole_encoding_has_to_be_a_type(self):
        for name in (".", "..PEAX", ".?AVFoo@@extra", ".PEAXjunk", ".QQQ"):
            assert demangle.demangle(name) == name, name

    def test_a_type_the_reference_cannot_read_is_still_read_here(self):
        """`llvm-undname` 18.1.3 knows neither `_M` nor `_L`, in any position.

        Not a divergence in the descriptor rule: it refuses `?x@@3_MA` too. They are the
        128-bit integer codes, this reads them wherever a type may stand, and the two
        rows are left out of the corpus because the reference has no answer to record.
        """
        assert demangle.demangle("._M") == "unsigned __int128 `RTTI Type Descriptor Name'"
        assert demangle.demangle("._L") == "__int128 `RTTI Type Descriptor Name'"
        assert demangle.demangle("?x@@3_MA") == "unsigned __int128 x"
