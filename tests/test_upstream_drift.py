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
