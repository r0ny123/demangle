"""The parsers in tools/upstream_drift.py, on excerpts of the files they read.

The network part is not tested here; what is pinned is that each reference's vector
format is read the way the reference's own test harness reads it.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("upstream_drift", ROOT / "tools" / "upstream_drift.py")
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
    def test_reads_escapes_and_a_vector_split_over_lines(self):
        text = (
            '// clang-format off\n{"_Z1A", "A"},\n{"_Z1fPKc", "f(char const*)"},\n'
            '{"_ZN1a1bE",\n "a::b"},\n{"_Z1gPFvvE", "g(void (*)())"},\n{"_Z1q", "\\"q\\" \\\\ \\x41"},\n'
        )
        pairs = drift.libcxxabi(FakeFetch({"DemangleTestCases.inc": text}))
        assert pairs == [
            ("_Z1A", "A"),
            ("_Z1fPKc", "f(char const*)"),
            ("_ZN1a1bE", "a::b"),
            ("_Z1gPFvvE", "g(void (*)())"),
            ("_Z1q", '"q" \\ A'),
        ]


class TestMsvc:
    def test_pairs_a_name_with_the_check_line_after_it(self):
        text = "; RUN: llvm-undname < %s | FileCheck %s\n\n; CHECK-NOT: Invalid mangled name\n\n?x@@3HA\n; CHECK: int x\n\n?y@@3PEAHEA\n; CHECK: int *y\n"
        pairs = drift.msvc(FakeFetch({"Demangle": '[{"name": "ms-basic.test"}]', "ms-basic.test": text}))
        assert ("?x@@3HA", "int x") in pairs
        assert ("?y@@3PEAHEA", "int *y") in pairs

    def test_check_lines_match_as_filecheck_does(self):
        assert drift._contains("void __cdecl f(int,   int)", "public: void __cdecl f(int, int)")
        assert not drift._contains("void __cdecl f(int)", "void __cdecl g(int)")


class TestSwift:
    def test_strips_the_remangler_annotation(self):
        text = "_TtBf32_ ---> Builtin.FPIEEE32\n$s3fooFTo ---> {T:$s3fooF,C} @objc foo()\nnot a vector\n"
        pairs = drift.swift(FakeFetch({"manglings.txt": text}))
        assert pairs == [("_TtBf32_", "Builtin.FPIEEE32"), ("$s3fooFTo", "@objc foo()")]


class TestRustc:
    def test_reads_every_macro_form(self):
        v0 = (
            't!("_RNvC3foo3bar", "foo::bar");\n'
            't_nohash!("_RNvC6_123foo3bar", "123foo::bar");\n'
            't_nohash_type!("Rc", "&char");\n'
            't_const!("c22_", r#"\'"\'"#);\n'
            't_const_suffixed!("i_", "-1", "i32");\n'
            't_err!("_RB_");\n'
            't_nohash!(\n    "_RIC0Kee1_\\\n        e2_E",\n    "::<{*\\"\\u{41}\\"}>"\n);\n'
        )
        pairs = drift.rustc(FakeFetch({"v0.rs": v0, "legacy.rs": "", "lib.rs": ""}))
        assert pairs == [
            ("_RNvC3foo3bar", "foo::bar", "hash"),
            ("_RNvC6_123foo3bar", "123foo::bar"),
            ("_RMC0Rc", "<&char>"),
            ("_RIC0Kc22_E", "::<'\"'>"),
            ("_RIC0Ki_E", "::<-1>"),
            ("_RIC0Ki_E", "::<-1i32>", "hash"),
            ("_RB_", "_RB_"),
            ("_RIC0Kee1_e2_E", '::<{*"A"}>'),
        ]


class TestScoring:
    def test_a_recorded_name_is_unchanged_or_recorded_differently_and_a_new_one_is_scored(self, monkeypatch):
        monkeypatch.setitem(
            drift.SOURCES,
            "fake",
            (
                lambda fetch: [("_Z1A", "A"), ("_Z1fv", "f(void)"), ("_Z1gv", "g()"), ("_Z1hv", "nope")],
                "x",
                lambda m, _: {"_Z1gv": "g()", "_Z1hv": "h()"}[m],
                drift._exact,
            ),
        )
        monkeypatch.setattr(drift, "recorded", lambda corpus: {"_Z1A": "A", "_Z1fv": "f()"})
        result = drift.score("fake", None)
        assert result["unchanged"] == 1
        assert result["differently"] == [("_Z1fv", "f()", "f(void)")]
        assert result["new_pass"] == 1
        assert result["new_fail"] == [("_Z1hv", "nope", "h()")]
