"""`integrations/demangle_labels.py`: the part of the disassembler scripts that needs no disassembler.

The scripts for IDA, Binary Ninja and Ghidra cannot run in an ordinary test run, so
everything that can be decided without a host is decided in this module, and this is where
it is held to account: which names get a label, what the label says, and what the options
change. The scripts themselves are exercised against stand-ins in
`test_integrations_hosts.py`, and the Ghidra one against Ghidra in the opt-in
`test_integrations_ghidra_live.py`.
"""

import ast
import dataclasses
import doctest
import tomllib
from pathlib import Path

import pytest

import demangle
import demangle_labels
from demangle_labels import ELLIPSIS, Entry, Label, Options, label_for, plan_labels

ROOT = Path(__file__).parent.parent

ITANIUM = "_ZNSt6vectorIiSaIiEE9push_backERKi"
SWIFT = "$s10Foundation4DataV5countSivg"
RUST = "_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E"
MSVC = "?f@@YAXH@Z"
OBJC = "_OBJC_CLASS_$_NSData"

TABLE = [(0x10, "main"), (0x20, ITANIUM), (0x30, "memcpy"), (0x40, SWIFT), (0x50, ITANIUM)]


class TestWhatGetsALabel:
    def test_a_pair_is_enough(self):
        label = label_for((0x401000, ITANIUM))
        assert label == Label(
            address=0x401000,
            name=ITANIUM,
            language="itanium",
            full="std::vector<int, std::allocator<int>>::push_back(int const&)",
            short="std::vector<int, std::allocator<int>>::push_back",
        )

    @pytest.mark.parametrize(
        ("name", "language", "full", "short"),
        [
            (SWIFT, "swift", "Foundation.Data.count.getter : Swift.Int", "Foundation.Data.count.getter"),
            (RUST, "rust", "core::fmt::Formatter::pad", "core::fmt::Formatter::pad"),
            (MSVC, "msvc", "void __cdecl f(int)", "f"),
            ("Java_com_example_Foo_bar", "jni", "com.example.Foo.bar", "com.example.Foo.bar"),
            ("eqdestroy___systemZassertions_23", "nim", "system/assertions.=destroy", "system/assertions.=destroy"),
        ],
    )
    def test_every_scheme_is_read_and_named(self, name, language, full, short):
        label = label_for((1, name))
        assert label is not None
        assert (label.language, label.full, label.short) == (language, full, short)

    def test_the_label_hands_back_what_it_was_given(self):
        """The script finds the symbol again by these, so they are not tidied."""
        label = label_for((2**64 - 1, ITANIUM))
        assert label is not None
        assert (label.address, label.name) == (2**64 - 1, ITANIUM)

    @pytest.mark.parametrize("name", ["", "main", "memcpy", "sub_401000", "__imp_CreateFileW", "WinMain@16", "a b c"])
    def test_a_name_that_is_not_mangled_gets_nothing(self, name):
        assert label_for((1, name)) is None

    @pytest.mark.parametrize("name", ["_ZN3Foo", "_Z", "?", "$s", "_R"])
    def test_a_name_that_does_not_read_gets_nothing(self, name):
        assert label_for((1, name)) is None

    def test_a_reading_that_is_the_name_gets_nothing(self):
        """Go paths read as themselves; a label that says what the name says adds nothing."""
        assert demangle.demangle("main.(*T).Method") == "main.(*T).Method"
        assert label_for((1, "main.(*T).Method")) is None

    def test_a_name_that_is_a_reading_gets_nothing(self):
        """Running the script twice must not find work the first run did."""
        assert label_for((1, demangle.demangle(ITANIUM))) is None

    @pytest.mark.parametrize(
        ("name", "special"),
        [
            ("_ZTV3Foo", "vtable for Foo"),
            ("_ZTS3Foo", "typeinfo name for Foo"),
            (OBJC, "Objective-C class NSData"),
        ],
    )
    def test_a_symbol_about_a_thing_is_labelled_as_a_phrase(self, name, special):
        """`short` must not call the vtable of `Foo` `Foo`."""
        label = label_for((1, name))
        assert label is not None
        assert label.short == label.full == special

    def test_what_the_symbol_table_appended_stays_on_the_short_name(self):
        """Two clones of one function are two symbols, and so two names."""
        hot, cold = label_for((1, "_ZN3Foo3barEv")), label_for((2, "_ZN3Foo3barEv.cold"))
        assert hot is not None and cold is not None
        assert (hot.short, cold.short) == ("Foo::bar", "Foo::bar.cold")
        versioned = label_for((3, "_ZNSt10moneypunctIcLb0EE2idE@@GLIBCXX_3.4"))
        assert versioned is not None
        assert versioned.short == "std::moneypunct<char, false>::id@@GLIBCXX_3.4"

    def test_bytes_are_refused_rather_than_skipped(self):
        """A table of bytes read as if it were text would label nothing and say nothing."""
        with pytest.raises(TypeError, match="demangleb"):
            label_for((1, b"_ZN3foo3barEv"))  # ty: ignore[invalid-argument-type]


class TestLanguages:
    def test_the_default_lets_every_scheme_read(self):
        assert Options().languages is None
        labels = [label_for((1, name)) for name in (ITANIUM, SWIFT, RUST, OBJC)]
        assert [label.language for label in labels if label] == ["itanium", "swift", "rust", "objc"]

    def test_an_allow_list_leaves_what_it_does_not_name(self):
        """A Mach-O image's Objective-C metadata stays as the linker wrote it."""
        options = Options(languages=("itanium", "swift"))
        assert label_for((1, OBJC), options) is None
        assert label_for((1, MSVC), options) is None
        assert label_for((1, SWIFT), options) is not None
        assert label_for((1, ITANIUM), options) is not None

    def test_a_scheme_not_allowed_does_not_read_a_name_it_claims(self):
        assert label_for((1, SWIFT), Options(languages=("itanium",))) is None

    def test_a_list_is_kept_as_a_tuple(self):
        assert Options(languages=["swift", "itanium"]).languages == ("swift", "itanium")

    def test_a_sequence_of_one_is_an_allow_list_and_not_a_forced_scheme(self):
        """`("gnuv2",)` declines `PyInit__lldb`; `"gnuv2"` would read it as C++. The first is meant."""
        assert demangle.demangle("PyInit__lldb", language="gnuv2") != "PyInit__lldb"
        assert label_for((1, "PyInit__lldb"), Options(languages=("gnuv2",))) is None

    def test_one_bare_name_is_refused_for_that_reason(self):
        with pytest.raises(TypeError, match=r"\('swift',\)"):
            Options(languages="swift")

    def test_an_empty_list_is_refused(self):
        with pytest.raises(ValueError, match="no scheme"):
            Options(languages=())

    def test_an_unknown_scheme_is_refused_when_the_options_are_made(self):
        with pytest.raises(ValueError, match="unknown language 'swfit'"):
            Options(languages=("swfit",))


class TestTheHostsOwnReading:
    def test_a_name_the_host_read_is_left_alone_by_default(self):
        assert label_for(Entry(1, ITANIUM, "std::vector<int>::push_back(int const&)")) is None

    @pytest.mark.parametrize("reading", [None, "", ITANIUM])
    def test_a_reading_that_is_nothing_or_the_name_is_a_name_left_mangled(self, reading):
        assert label_for(Entry(1, ITANIUM, reading)) is not None

    def test_the_option_labels_them_anyway(self):
        options = Options(only_left_mangled=False)
        label = label_for(Entry(1, RUST, "core::fmt::Formatter::pad::h9b2b3a0e5b4d1b31()"), options)
        assert label is not None
        assert label.full == "core::fmt::Formatter::pad"

    def test_but_not_to_say_what_the_host_already_says(self):
        options = Options(only_left_mangled=False)
        assert label_for(Entry(1, RUST, "core::fmt::Formatter::pad"), options) is None

    def test_a_plain_triple_is_an_entry(self):
        assert label_for((1, ITANIUM, "something")) is None
        assert label_for((1, ITANIUM, None)) is not None


class TestStyle:
    def test_the_default_is_llvm(self):
        label = label_for((1, ITANIUM))
        assert label is not None
        assert label.full == demangle.demangle(ITANIUM, style="llvm")

    def test_a_named_style(self):
        label = label_for((1, ITANIUM), Options(style="gnu"))
        assert label is not None
        assert label.full == "std::vector<int, std::allocator<int> >::push_back(int const&)"
        assert label.short == "std::vector<int, std::allocator<int> >::push_back"

    def test_a_composed_style(self):
        label = label_for((1, MSVC), Options(style=demangle.style("llvm", msvc={"calling_convention": False})))
        assert label is not None
        assert (label.full, label.short) == ("void f(int)", "f")

    def test_an_unknown_style_is_refused_when_the_options_are_made(self):
        with pytest.raises(ValueError, match="unknown style 'nope'"):
            Options(style="nope")


class TestLength:
    LONG = "_ZN" + "3foo" * 40 + "Ev"

    def test_a_long_label_is_cut_and_says_so(self):
        label = label_for((1, self.LONG), Options(max_length=50))
        assert label is not None
        assert len(label.full) == len(label.short) == 50
        assert label.full.endswith(ELLIPSIS) and label.short.endswith(ELLIPSIS)
        assert label.full.startswith("foo::foo::foo")

    def test_a_label_that_fits_is_not_touched(self):
        assert label_for((1, ITANIUM), Options(max_length=500)) == label_for((1, ITANIUM))

    def test_the_short_name_is_cut_on_its_own_length(self):
        """The parameters count toward `full` alone: a short name that fits stays whole."""
        limit = len("std::vector<int, std::allocator<int>>::push_back") + 1
        label = label_for((1, ITANIUM), Options(max_length=limit))
        assert label is not None
        assert label.short == "std::vector<int, std::allocator<int>>::push_back"
        assert len(label.full) <= limit and label.full.endswith(ELLIPSIS)

    def test_no_limit_returns_it_whole(self):
        label = label_for((1, self.LONG), Options(max_length=None))
        assert label is not None
        assert len(label.full) > 150

    @pytest.mark.parametrize("limit", [0, 1, len(ELLIPSIS)])
    def test_a_limit_with_no_room_for_a_name_is_refused(self, limit):
        with pytest.raises(ValueError, match="max_length"):
            Options(max_length=limit)

    def test_the_least_that_leaves_room_is_accepted(self):
        label = label_for((1, ITANIUM), Options(max_length=len(ELLIPSIS) + 1))
        assert label is not None
        assert label.full == "s" + ELLIPSIS


class TestWhatTheHostCouldNotTake:
    """Control characters would split a comment or end a name early in a host written in C."""

    def test_control_characters_become_spaces(self, monkeypatch):
        options = Options()
        monkeypatch.setattr(demangle, "demangle", lambda *args, **kwargs: "a\nb\x00c\u2028d")
        label = label_for((1, "_ZN3foo3barEv"), options)
        assert label is not None
        assert label.full == "a b c d"
        assert label.short == "foo::bar"

    def test_a_reading_of_nothing_else_is_no_label(self, monkeypatch):
        options = Options()
        monkeypatch.setattr(demangle, "demangle", lambda *args, **kwargs: "\n\x00 ")
        assert label_for((1, "_ZN3foo3barEv"), options) is None

    def test_a_scheme_that_reads_a_name_twice_differently_is_not_trusted(self, monkeypatch):
        """`signature()` raising for a name `demangle()` read is a defect in a scheme; the table goes on."""

        def refuse(name, **kwargs):
            raise demangle.ParseError(name, None, "defect")

        options = Options()
        monkeypatch.setattr(demangle, "signature", refuse)
        assert label_for((1, ITANIUM), options) is None


class TestPlanLabels:
    def test_the_labels_are_in_the_order_of_the_table(self):
        assert [label.address for label in plan_labels(TABLE)] == [0x20, 0x40, 0x50]

    def test_a_name_at_two_addresses_is_two_labels(self):
        first, last = [label for label in plan_labels(TABLE) if label.name == ITANIUM]
        assert first.full == last.full and first.address != last.address

    def test_it_reads_a_table_as_it_is_given_it(self):
        """A caller counting symbols for a progress bar wraps the table in a generator; this must pull from it."""
        seen = []

        def counted():
            for entry in TABLE:
                seen.append(entry[0])
                yield entry

        labels = plan_labels(counted())
        assert seen == []
        assert next(labels).address == 0x20
        assert seen == [0x10, 0x20]

    def test_options_apply_to_every_entry(self):
        options = Options(languages=("swift",))
        assert [label.address for label in plan_labels(TABLE, options)] == [0x40]

    def test_a_label_is_a_value(self):
        label = label_for((1, ITANIUM))
        assert label is not None
        with pytest.raises(dataclasses.FrozenInstanceError):
            label.full = "x"  # ty: ignore[invalid-assignment]
        assert label == label_for((1, ITANIUM))


class TestTheModule:
    def test_its_example_is_what_it_does(self):
        results = doctest.testmod(demangle_labels)
        assert results.attempted and not results.failed

    def test_it_imports_nothing_a_host_provides(self):
        """The reason the core can be tested here at all."""
        tree = ast.parse(Path(demangle_labels.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert imported == {"re", "collections.abc", "dataclasses", "typing", "demangle"}

    def test_the_scripts_ship_in_the_sdist_and_not_in_the_wheel(self):
        """Packagers run this suite from the sdist, so the scripts it tests go with it."""
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["hatch"]["build"][
            "targets"
        ]
        assert "/integrations" in config["sdist"]["include"]
        assert config["wheel"] == {"packages": ["src/demangle"]}

    def test_the_package_does_not_depend_on_the_scripts(self):
        source = ROOT / "src" / "demangle"
        mentions = [path for path in source.rglob("*.py") if "demangle_labels" in path.read_text(encoding="utf-8")]
        assert mentions == []
