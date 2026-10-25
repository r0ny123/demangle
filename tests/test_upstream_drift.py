"""The parsers and the scoring in tools/upstream_drift.py, on excerpts of the files they
read.

The network part is not tested here; what is pinned is that each reference's vector
format is read the way the reference's own test harness reads it.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("upstream_drift", ROOT / "tools" / "upstream_drift.py")
if spec is None or spec.loader is None:  # pragma: no cover - wheel-only checkout
    pytest.skip("tools/upstream_drift.py is not part of this distribution", allow_module_level=True)
drift = importlib.util.module_from_spec(spec)
sys.modules["upstream_drift"] = drift
spec.loader.exec_module(drift)


class FakeFetch:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url):
        for suffix, text in self.pages.items():
            if url.endswith(suffix):
                return text
        raise OSError(url)


class TestLibcxxabi:
    def test_reads_escapes_split_vectors_and_adjacent_literals_and_skips_commented_ones(self):
        text = (
            '// clang-format off\n{"_Z1A", "A"},\n// {"_Z1B", "B"},\n{"_Z1fPKc", "f(char const*)"},\n'
            '{"_ZN1a1bE",\n "a::b"},\n{"_ZNSdC1Ev",\n "std::basic_iostream<char, std::char_traits<char>"\n'
            ' ">::basic_iostream()"},\n{"_Z1q", "\\"q\\" \\\\ \\x41 \\101 \\a"},\n{"_Z1r", "\\x1" "B"},\n'
        )
        pairs = drift.libcxxabi(FakeFetch({"DemangleTestCases.inc": text}))
        assert pairs == [
            ("_Z1A", "A"),
            ("_Z1fPKc", "f(char const*)"),
            ("_ZN1a1bE", "a::b"),
            ("_ZNSdC1Ev", "std::basic_iostream<char, std::char_traits<char>>::basic_iostream()"),
            ("_Z1q", '"q" \\ A A \a'),
            ("_Z1r", "\x01B"),
        ]

    def test_a_hex_escape_past_unicode_stays_literal(self):
        assert drift._c_unescape("\\x110000") == "\\x110000"


class TestMsvc:
    def test_pairs_check_lines_with_names_in_order_as_filecheck_does(self):
        text = (
            "; RUN: llvm-undname < %s | FileCheck %s\n"
            "; RUN: llvm-undname --no-access-specifier < %s | FileCheck %s --check-prefix=CHECK-NO-ACCESS\n\n"
            "; CHECK-NOT: Invalid mangled name\n\n"
            "?x@@3HA\n?unchecked@@3HA\n?y@@3PEAHEA\n?z@@3HA\n\n"
            "; CHECK: int x\n; CHECK-NO-ACCESS: int x\n; CHECK: int *y\n; CHECK: not what z is\n"
        )
        pairs = drift.msvc(FakeFetch({"Demangle": '[{"name": "ms-basic.test"}]', "ms-basic.test": text}))
        assert pairs == [("?x@@3HA", "int x"), ("?y@@3PEAHEA", "int *y"), ("?z@@3HA", "not what z is")]

    def test_a_check_spent_on_the_echo_expects_nothing_and_check_next_takes_the_answer(self):
        md5 = "??@a6a285da2eea70dba6b578022be61d81@"
        text = (
            f"; CHECK-NOT: Invalid mangled name\n\n{md5}\n; CHECK: {md5}\n; CHECK-NEXT: {md5}\n\n"
            f"{md5}asdf\n; CHECK: {md5}asdf\n; CHECK-NEXT: {md5}\n\n?x@@3HA\n; CHECK: int x\n"
        )
        pairs = drift.msvc(FakeFetch({"Demangle": '[{"name": "ms-md5.test"}]', "ms-md5.test": text}))
        assert pairs == [(md5, md5), (md5 + "asdf", md5), ("?x@@3HA", "int x")]

    def test_check_lines_match_as_filecheck_does(self):
        assert drift._contains("void __cdecl f(int,   int)", "public: void __cdecl f(int, int)")
        assert not drift._contains("void __cdecl f(int)", "void __cdecl g(int)")


class TestSwift:
    def test_strips_the_remangler_annotation_and_nothing_else(self):
        text = (
            "_TtBf32_ ---> Builtin.FPIEEE32\n$s3fooFTo ---> {T:$s3fooF,C} @objc foo()\n$s3barFTo ---> {C} bar()\n"
            "$s1xyXO ---> {closure #1} in main\nnot a vector\n"
        )
        pairs = drift.swift(FakeFetch({"manglings.txt": text}))
        assert pairs == [
            ("_TtBf32_", "Builtin.FPIEEE32"),
            ("$s3fooFTo", "@objc foo()"),
            ("$s3barFTo", "bar()"),
            ("$s1xyXO", "{closure #1} in main"),
        ]


class TestRustc:
    def test_reads_every_macro_form(self):
        v0 = (
            't!("_RNvC3foo3bar", "foo::bar");\n'
            't_nohash!("_RNvC6_123foo3bar", "123foo::bar");\n'
            't_nohash_type!("Rc", "&char");\n'
            't_nohash_type!(concat!("TT", "p", "E"), "((*const _,),)");\n'
            't_const!("c22_", r#"\'"\'"#);\n'
            't_const_suffixed!("i_", "-1", "i32");\n'
            't_err!("_RB_");\n'
            't_nohash!(\n    "_RIC0Kee1_\\\n        e2_E",\n    "::<{*\\"\\u{41}\\"}>"\n);\n'
            't_nohash!("_RNvC1a1b", "a\\\\\nb");\n'
            't_nohash!("_RNvC1a1c", "a\\0b");\n'
        )
        pairs = drift.rustc(FakeFetch({"v0.rs": v0, "legacy.rs": "", "lib.rs": ""}))
        assert pairs == [
            ("_RNvC3foo3bar", "foo::bar", "hash"),
            ("_RNvC6_123foo3bar", "123foo::bar"),
            ("_RMC0Rc", "<&char>"),
            ("_RMC0TTpE", "<((*const _,),)>"),
            ("_RIC0Kc22_E", "::<'\"'>"),
            ("_RIC0Ki_E", "::<-1>"),
            ("_RIC0Ki_E", "::<-1i32>", "hash"),
            ("_RB_", "_RB_"),
            ("_RIC0Kee1_e2_E", '::<{*"A"}>'),
            ("_RNvC1a1b", "a\\\nb"),
            ("_RNvC1a1c", "a\0b"),
        ]


class TestScoring:
    def test_each_vector_lands_in_one_of_the_outcomes(self, monkeypatch):
        def read(fetch):
            return [
                ("_Z1A", "A"),
                ("_Z1fv", "f(void)"),
                ("_Z1gv", "g()"),
                ("_Z1hv", "nope"),
                ("_Z1A", "A"),
                ("_Z1A", "A::h", "hash"),
                ("_Z1kv", "k()", "hash"),
                ("_Z1mv", "anything"),
            ]

        def spell(mangled, mode):
            return {"_Z1gv": "g()", "_Z1hv": "h()", "_Z1A": "A::h", "_Z1kv": "k"}.get(mangled, "")

        monkeypatch.setitem(drift.SOURCES, "fake", (read, "x", spell, drift._exact))
        monkeypatch.setattr(drift, "recorded", lambda corpus: {"_Z1A": "A", "_Z1fv": "f()"})
        monkeypatch.setattr(drift, "EXPECTED_MISREADS", {"_Z1mv"})
        result = drift.score("fake", None)
        assert result["fetched"] == 7
        assert result["unchanged"] == 1
        assert result["differently"] == [("_Z1fv", "f()", "f(void)")]
        assert result["new_pass"] == 3
        assert result["new_fail"] == [("_Z1hv", "nope", "h()"), ("_Z1kv", "k()", "k")]


class TestReport:
    def test_cells_survive_backticks_pipes_and_length(self):
        assert drift._cell("`anonymous namespace'::f") == "`` `anonymous namespace'::f ``"
        assert drift._cell("a|b") == "` a\\|b `"
        assert drift._cell("x" * 400) == "` " + "x" * 300 + "… `"
        assert drift._cell("a\nb") == "` a\\nb `"

    def test_the_report_is_cut_on_a_line_to_fit_an_issue(self):
        results = [
            {
                "source": "s",
                "fetched": 1,
                "unchanged": 0,
                "differently": [],
                "new_pass": 0,
                "new_fail": [("n" * 50, "e", "g")] * 5,
            }
        ]
        text = drift.report(results, 15, limit=400)
        assert len(text) < 450
        assert text.endswith("… cut at 400 characters.")
        assert "\n\n…" in text

    def test_the_summary_and_the_tables(self):
        results = [
            {
                "source": "s",
                "fetched": 3,
                "unchanged": 1,
                "differently": [("m", "r", "u")],
                "new_pass": 0,
                "new_fail": [("n", "e", "g")],
            },
        ]
        text = drift.report(results, 15)
        assert "| s | 3 | 1 | 1 | 0 | 1 |" in text
        assert "### s: new vectors this library misreads" in text
        assert "| ` n ` | ` e ` | ` g ` |" in text
        assert "### s: recorded with a different expectation" in text


class TestExitStatus:
    def test_one_two_and_zero(self, monkeypatch, capsys):
        good = (lambda fetch: [("_Z1A", "A")], "x", lambda m, _: "A", drift._exact)
        bad = (lambda fetch: [("_Z1A", "A")], "x", lambda m, _: "B", drift._exact)
        broken = (lambda fetch: 1 / 0, "x", lambda m, _: "A", drift._exact)
        monkeypatch.setattr(drift, "recorded", lambda corpus: {})
        monkeypatch.setattr(drift, "SOURCES", {"good": good, "bad": bad, "broken": broken})
        assert drift.main(["--source", "good"]) == 0
        assert drift.main(["--source", "bad"]) == 1
        assert drift.main(["--source", "broken"]) == 2
        assert "could not read" in capsys.readouterr().err


def _llvm_result(source="llvm-itanium", **fields):
    return {
        "source": source,
        "reference": "LLVM 21",
        "offered": 10,
        "agree": 6,
        "known": 1,
        "unaccounted": [],
        "declined": [],
        "refused": [],
        **fields,
    }


class TestLlvmReport:
    def test_a_summary_row_per_scheme_and_a_table_for_each_kind_of_difference(self):
        result = _llvm_result(
            unaccounted=[("_Z1fv", "g()", "f()")],
            declined=[("_Z1gv", "g()"), ("_Z1hv", "h()")],
            refused=[("_Z1iv", "i()")],
        )
        text = drift.report([result], 15)
        assert "| llvm-itanium | 10 | 6 | 1 | 1 | 2 | 1 |" in text
        assert "### llvm-itanium: read differently by LLVM 21, and nothing accounts for it" in text
        assert "| mangled | LLVM 21 | this |" in text
        assert "| ` _Z1fv ` | ` g() ` | ` f() ` |" in text
        assert "### llvm-itanium: read by LLVM 21, declined by this library" in text
        assert "### llvm-itanium: read by this library, declined by LLVM 21" in text
        # the fetched sources' table is not printed when only LLVM was asked
        assert "distinct upstream vectors" not in text

    def test_it_follows_the_fetched_sources_in_the_same_report(self):
        fetched = {"source": "s", "fetched": 3, "unchanged": 1, "differently": [], "new_pass": 2, "new_fail": []}
        text = drift.report([fetched, _llvm_result()], 15)
        assert text.index("| s | 3 | 1 | 0 | 2 | 0 |") < text.index("| llvm-itanium |")
        assert "###" not in text

    def test_only_what_is_unaccounted_for_is_a_table_when_nothing_is_declined(self):
        assert "###" not in drift.report([_llvm_result()], 15)

    def test_the_tables_are_cut_like_the_others(self):
        text = drift.report([_llvm_result(declined=[(f"_Z{n}", "x") for n in range(30)])], 5)
        assert "| … 25 more | | |" in text


class TestLlvmSources:
    def run(self, monkeypatch, run, *argv):
        monkeypatch.setattr(drift.llvm_oracle, "run", run)
        return drift.main(["--source", *argv])

    def test_each_scheme_is_a_source_and_asks_for_its_own_reader(self, monkeypatch, capsys):
        asked = []

        def run(schemes, version=None):
            asked.append((schemes, version))
            return [_llvm_result("llvm-d")]

        assert self.run(monkeypatch, run, "llvm-d", "llvm-rust", "--llvm-version", "18") == 0
        assert asked == [(["d", "rust"], 18)]
        assert "| llvm-d | 10 | 6 | 1 | 0 | 0 | 0 |" in capsys.readouterr().out

    def test_a_name_nothing_accounts_for_is_exit_status_one(self, monkeypatch):
        def run(schemes, version=None):
            return [_llvm_result(unaccounted=[("_Z1fv", "g()", "f()")])]

        assert self.run(monkeypatch, run, "llvm-itanium") == 1

    def test_declined_names_are_listed_and_never_fail(self, monkeypatch, capsys):
        def run(schemes, version=None):
            return [_llvm_result(declined=[("_Z1gv", "g()")], refused=[("_Z1iv", "i()")])]

        assert self.run(monkeypatch, run, "llvm-itanium") == 0
        assert "_Z1gv" in capsys.readouterr().out

    def test_without_the_package_the_sources_are_skipped_with_a_note(self, monkeypatch, capsys):
        def run(schemes, version=None):
            raise drift.llvm_oracle.Unavailable("the llvm-demangle-fxti package is not installed")

        assert self.run(monkeypatch, run, "llvm-itanium") == 0
        captured = capsys.readouterr()
        assert "llvm-*: skipped: the llvm-demangle-fxti package is not installed" in captured.err
        assert captured.out == ""

    def test_skipping_leaves_the_fetched_sources_alone(self, monkeypatch, capsys):
        def run(schemes, version=None):
            raise drift.llvm_oracle.Unavailable("not installed")

        monkeypatch.setattr(drift, "recorded", lambda corpus: {})
        monkeypatch.setitem(drift.SOURCES, "good", (lambda fetch: [("_Z1A", "A")], "x", lambda m, _: "A", drift._exact))
        monkeypatch.setattr(drift, "LLVM_SOURCES", {"llvm-itanium": "itanium"})
        assert self.run(monkeypatch, run, "good", "llvm-itanium") == 0
        captured = capsys.readouterr()
        assert "| good | 1 | 0 | 0 | 1 | 0 |" in captured.out
        assert "skipped" in captured.err

    def test_require_llvm_turns_the_skip_into_exit_status_two(self, monkeypatch, capsys):
        def run(schemes, version=None):
            raise drift.llvm_oracle.Unavailable("the llvm-demangle-fxti package is not installed")

        assert self.run(monkeypatch, run, "llvm-itanium", "--require-llvm") == 2
        assert "could not be scored: the llvm-demangle-fxti package is not installed" in capsys.readouterr().err

    def test_a_version_the_package_lacks_is_exit_status_two_even_without_require(self, monkeypatch, capsys):
        def run(schemes, version=None):
            raise ValueError("LLVM 7 is not bundled")

        assert self.run(monkeypatch, run, "llvm-itanium", "--llvm-version", "7") == 2
        assert "LLVM 7 is not bundled" in capsys.readouterr().err

    def test_a_crash_in_the_comparison_is_exit_status_two(self, monkeypatch, capsys):
        def run(schemes, version=None):
            return 1 / 0

        assert self.run(monkeypatch, run, "llvm-itanium") == 2
        assert "could not be scored" in capsys.readouterr().err

    def test_the_package_that_is_really_absent_is_skipped_and_required_alike(self, monkeypatch, capsys):
        """No stand-in for `run` here: the import itself fails, as it does where nothing installed the package."""
        monkeypatch.setitem(sys.modules, "llvmdemangle", None)
        assert drift.main(["--source", "llvm-d"]) == 0
        captured = capsys.readouterr()
        assert (
            "skipped: the llvm-demangle-fxti package is not installed (pip install llvm-demangle-fxti==1.0.0)"
            in captured.err
        )
        assert drift.main(["--source", "llvm-d", "--require-llvm"]) == 2
        assert "could not be scored" in capsys.readouterr().err

    def test_a_version_nothing_bundles_is_exit_status_two_where_the_package_is_absent_too(self, monkeypatch, capsys):
        monkeypatch.setitem(sys.modules, "llvmdemangle", None)
        for args in (["--llvm-version", "7"], ["--llvm-version", "7", "--require-llvm"]):
            assert drift.main(["--source", "llvm-d", *args]) == 2
            err = capsys.readouterr().err
            assert "llvm-*: LLVM 7 is not bundled; llvm-demangle-fxti has [11, 12," in err
            assert "skipped" not in err
        assert drift.main(["--source", "llvm-d", "--llvm-version", "18"]) == 0
        assert "skipped: the llvm-demangle-fxti package is not installed" in capsys.readouterr().err

    def test_every_source_is_the_default_and_the_llvm_ones_are_among_them(self):
        assert drift.LLVM_SOURCES == {
            "llvm-itanium": "itanium",
            "llvm-msvc": "msvc",
            "llvm-rust": "rust",
            "llvm-d": "d",
        }


class TestNoLlvm:
    """`--no-llvm` is how the job that holds a token runs: the binding is a third party's
    native code, and nothing in that process may so much as ask for it."""

    @pytest.fixture
    def only_good(self, monkeypatch):
        monkeypatch.setattr(drift, "recorded", lambda corpus: {})
        monkeypatch.setattr(
            drift, "SOURCES", {"good": (lambda fetch: [("_Z1A", "A")], "x", lambda m, _: "A", drift._exact)}
        )

        def forbidden(*args, **kwargs):
            raise AssertionError("the LLVM reference was asked for")

        monkeypatch.setattr(drift.llvm_oracle, "run", forbidden)
        monkeypatch.setattr(drift.llvm_oracle, "binding", forbidden)
        monkeypatch.delitem(sys.modules, "llvmdemangle", raising=False)

    def test_it_scores_the_fetched_sources_and_loads_no_binding(self, only_good, capsys):
        assert drift.main(["--no-llvm"]) == 0
        captured = capsys.readouterr()
        assert "| good | 1 | 0 | 0 | 1 | 0 |" in captured.out
        assert "llvm" not in captured.err
        assert "llvmdemangle" not in sys.modules

    def test_it_is_still_the_fetched_sources_when_they_are_named(self, only_good, capsys):
        assert drift.main(["--no-llvm", "--source", "good"]) == 0
        assert "| good |" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "argv",
        [
            ["--no-llvm", "--source", "good", "llvm-d"],
            ["--no-llvm", "--require-llvm"],
            ["--no-llvm", "--llvm-version", "18"],
        ],
    )
    def test_asking_for_the_llvm_sources_as_well_is_an_argument_error(self, only_good, capsys, argv):
        with pytest.raises(SystemExit) as stop:
            drift.main(argv)
        assert stop.value.code == 2
        assert "--no-llvm leaves the llvm-* sources out" in capsys.readouterr().err

    def test_without_it_the_default_is_every_source_the_llvm_ones_included(self, monkeypatch, capsys):
        asked = []

        def run(schemes, version=None):
            asked.append(schemes)
            return [_llvm_result()]

        monkeypatch.setattr(drift, "SOURCES", {})
        monkeypatch.setattr(drift.llvm_oracle, "run", run)
        assert drift.main([]) == 0
        assert asked == [["d", "itanium", "msvc", "rust"]]
        assert "| llvm-itanium |" in capsys.readouterr().out
