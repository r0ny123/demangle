"""`benchmarks/compare.py` counts what its table says it counts.

The timings are not tested -- they depend on the machine. What a reader takes from the
table is the other columns: how many names a tool read, and how many of its answers match
the reference's. A comparison that miscounted those would be worse than none, and
nothing else exercises the script, so these check the arithmetic and the skipping.
"""

import importlib.util
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("compare", ROOT / "benchmarks" / "compare.py")
if spec is None or spec.loader is None:
    pytest.skip("benchmarks/compare.py is not part of this distribution", allow_module_level=True)
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


def corpus_file(tmp_path, text):
    path = tmp_path / "corpus.txt"
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return path


class TestCorpus:
    def test_comments_blank_lines_and_untabbed_lines_are_not_names(self, tmp_path):
        path = corpus_file(tmp_path, "# header\n\n_Z1fv\tf()\nno tab here\n_Z1gv\tg()\n")
        corpus = compare.load_corpus(path, "itanium")
        assert corpus.names == ["_Z1fv", "_Z1gv"]
        assert corpus.recorded == ["f()", "g()"]

    def test_a_repeated_name_is_one_name(self, tmp_path):
        """The cold figure means nothing if the second occurrence is a cache hit."""
        path = corpus_file(tmp_path, "_Z1fv\tf()\n_Z1fv\tf()\n_Z1gv\tg()\n")
        assert compare.load_corpus(path, "itanium").names == ["_Z1fv", "_Z1gv"]

    def test_an_elf_symbol_version_is_removed_from_name_and_spelling(self, tmp_path):
        path = corpus_file(tmp_path, "_Z1fv@@GLIBCXX_3.4\tf()@@GLIBCXX_3.4\n_Z1gv@GLIBCXX_3.4\tg()@GLIBCXX_3.4\n")
        corpus = compare.load_corpus(path, "itanium")
        assert corpus.names == ["_Z1fv", "_Z1gv"]
        assert corpus.recorded == ["f()", "g()"]

    def test_an_msvc_name_keeps_its_at_signs(self, tmp_path):
        path = corpus_file(tmp_path, "?f@@YAXH@Z\tvoid __cdecl f(int)\n")
        corpus = compare.load_corpus(path, "msvc")
        assert corpus.names == ["?f@@YAXH@Z"]
        assert corpus.recorded == ["void __cdecl f(int)"]

    def test_limit_keeps_the_first_names(self, tmp_path):
        path = corpus_file(tmp_path, "_Z1av\ta()\n_Z1bv\tb()\n_Z1cv\tc()\n")
        corpus = compare.load_corpus(path, "itanium", limit=2)
        assert corpus.names == ["_Z1av", "_Z1bv"]
        assert corpus.recorded == ["a()", "b()"]

    def test_every_default_corpus_is_checked_in(self):
        for name, scheme in compare.CORPORA:
            corpus = compare.load_corpus(compare.CONFORMANCE / name, scheme, limit=5)
            assert len(corpus.names) == 5, name


class TestReadingTheOtherToolsOutput:
    def test_one_line_per_name(self):
        assert compare.lines("f()\ng()\n", 2) == ["f()", "g()"]

    def test_a_missing_line_is_no_answer_at_all(self):
        """Pairing the wrong lines with the wrong names would score every later one."""
        assert compare.lines("f()\n", 2) is None

    def test_undname_names_a_refusal_by_the_missing_second_line(self):
        output = "?f@@YAXH@Z\nvoid __cdecl f(int)\n\nmain\n\n?g@@YAXXZ\nvoid __cdecl g(void)\n\n"
        assert compare.undname_groups(output, 3) == ["void __cdecl f(int)", None, "void __cdecl g(void)"]

    def test_undname_with_the_wrong_number_of_groups_is_no_answer(self):
        assert compare.undname_groups("?f@@YAXH@Z\nvoid __cdecl f(int)\n\n", 2) is None


class TestTheTable:
    def rows(self):
        return [
            compare.Row("one", "tool one", 10e-6, ["A", "b", "C"]),
            compare.Row("two", "tool two", 2e-6, ["a", "b", "c"]),
        ]

    def test_read_counts_names_that_changed_and_agreement_counts_identical_output(self):
        corpus = compare.Corpus("c.txt", "rust", ["a", "b", "c"], recorded=["A", "B", "C"])
        lines = compare.table(corpus, self.rows()).splitlines()
        assert lines[0] == "### c.txt: 3 names, rust"
        assert lines[2].endswith("| = corpus |")
        # Two of three changed; "A" and "C" match the recorded spelling, "b" does not.
        assert lines[4] == "| tool one | 10.0 | 100,000 | 2 | 2 |"
        # Nothing changed, so nothing was read and nothing matches.
        assert lines[5] == "| tool two | 2.0 | 500,000 | 0 | 0 |"

    def test_a_reference_is_marked_in_its_own_row(self):
        corpus = compare.Corpus("c.txt", "itanium", ["a", "b", "c"])
        rows = [compare.Row("llvm-cxxfilt", "llvm-cxxfilt", 1e-6, ["A", "B", "C"]), *self.rows()]
        lines = compare.table(corpus, rows).splitlines()
        assert lines[2] == "| Tool | us/name | names/s | Read | = llvm-cxxfilt |"
        assert lines[4].endswith("| 3 | ref |")
        assert lines[5].endswith("| 2 | 2 |")  # "A" and "C" match; "b" is not "B"

    def test_a_recorded_spelling_stands_in_only_when_no_reference_ran(self):
        corpus = compare.Corpus("c.txt", "itanium", ["a"], recorded=["A"])
        assert [key for key, _ in compare.references_for(corpus, [])] == ["corpus"]
        row = compare.Row("llvm-cxxfilt", "llvm-cxxfilt", 1e-6, ["A"])
        assert [key for key, _ in compare.references_for(corpus, [row])] == ["llvm-cxxfilt"]

    def test_a_tool_whose_output_could_not_be_paired_shows_no_counts(self):
        corpus = compare.Corpus("c.txt", "rust", ["a"], recorded=["A"])
        row = compare.Row("x", "tool x", 1e-6, [], note="output did not match")
        assert compare.table(corpus, [row]).splitlines()[4] == "| tool x (output did not match) | 1.0 | -- | -- | -- |"

    @pytest.mark.parametrize(
        "per_second,shown",
        [(23051, "23,100"), (2_712_345, "2,710,000"), (439, "439"), (1_530_000, "1,530,000")],
    )
    def test_rates_are_three_significant_figures(self, per_second, shown):
        assert compare.rate(per_second) == shown


@pytest.fixture
def without_competitors(monkeypatch):
    """`import llvmdemangle`, `cpp_demangle` and `itanium_demangler` fail, whatever is installed."""
    real = compare.importlib.import_module

    def import_module(name, *args):
        if name in {"llvmdemangle", "cpp_demangle", "itanium_demangler"}:
            raise ImportError(name)
        return real(name, *args)

    monkeypatch.setattr(compare.importlib, "import_module", import_module)


class TestSkipping:
    def test_a_competitor_that_is_not_installed_is_skipped_with_a_note(self, without_competitors):
        notes = []
        tools = compare.in_process_tools(notes)
        assert {tool.key for tool in tools} == {"demangle", "demangle-gnu"}
        assert len(notes) == 3
        assert all("pip install" in note for note in notes)

    def test_a_command_that_is_not_on_path_is_skipped_with_a_note(self, monkeypatch):
        monkeypatch.setattr(compare.shutil, "which", lambda command: None)
        notes = []
        assert compare.command_line_tools(notes) == []
        assert len(notes) == 3

    def test_the_report_still_runs_with_only_this_package(self, without_competitors, monkeypatch, capsys):
        monkeypatch.setattr(compare.shutil, "which", lambda command: None)
        assert compare.main(["--limit", "8", "--repeats", "1"]) == 0
        output = capsys.readouterr().out
        assert ", warm cache" in output
        assert output.count("- skipped:") == 6
        for corpus, _ in compare.CORPORA:
            assert f"### {corpus}: 8 names" in output


def test_the_warm_row_is_all_cache_hits():
    """The row says what a repeated name costs only if the repeats were hits."""
    corpus = compare.load_corpus(compare.CONFORMANCE / "itanium-real-world.txt", "itanium", limit=50)
    tool = compare.in_process_tools([])[0]
    compare.measure_tool(tool, corpus.names, 1)
    row = compare.measure_warm(tool, corpus.names, 2)
    assert row.note == ""
    assert row.outputs == compare.measure_tool(tool, corpus.names, 1).outputs
