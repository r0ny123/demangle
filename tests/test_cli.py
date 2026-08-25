"""The `demangle` command.

`main(argv)` takes its arguments and returns a status, so it is driven directly rather
than through a subprocess -- which keeps these fast enough to be worth having.
"""

import io

import pytest

from demangle.cli import main

VECTOR = "_ZNSt6vectorIiSaIiEE9push_backERKi"
VECTOR_SPELLED = "std::vector<int, std::allocator<int>>::push_back(int const&)"


def run(capsys, argv, stdin=None, monkeypatch=None):
    if stdin is not None:
        assert monkeypatch is not None, "supplying stdin requires the monkeypatch fixture"
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    status = main(argv)
    captured = capsys.readouterr()
    return status, captured.out, captured.err


class TestArguments:
    def test_names_on_the_command_line(self, capsys):
        status, out, _ = run(capsys, [VECTOR, "?f@@YAXH@Z"])
        assert status == 0
        assert out.splitlines() == [VECTOR_SPELLED, "void __cdecl f(int)"]

    def test_names_on_stdin(self, capsys, monkeypatch):
        status, out, _ = run(capsys, [], stdin=f"{VECTOR}\nmemcpy\n", monkeypatch=monkeypatch)
        assert status == 0
        assert out.splitlines() == [VECTOR_SPELLED, "memcpy"]

    def test_empty_stdin(self, capsys, monkeypatch):
        status, out, _ = run(capsys, [], stdin="", monkeypatch=monkeypatch)
        assert (status, out) == (0, "")

    def test_blank_lines_are_preserved(self, capsys, monkeypatch):
        status, out, _ = run(capsys, [], stdin="\n\n", monkeypatch=monkeypatch)
        assert (status, out) == (0, "\n\n")


class TestOptions:
    def test_detect(self, capsys):
        _, out, _ = run(capsys, ["--detect", VECTOR, "?f@@YAXH@Z", "memcpy"])
        assert out.splitlines() == ["itanium", "msvc", "-"]

    def test_detect_honours_a_forced_language(self, capsys):
        """It used to print the detected scheme regardless, contradicting `--language`."""
        _, out, _ = run(capsys, ["--detect", "--language", "msvc", "_Z1fv"])
        assert out.strip() == "msvc"

    def test_style(self, capsys):
        _, out, _ = run(capsys, ["--style", "gnu", VECTOR])
        assert out.strip().count("> >") == 1

    def test_language_aliases_are_accepted(self, capsys):
        """`--list-languages` advertises them, so they have to work."""
        for alias in ("gnu", "gcc", "clang", "c++", "ms", "rs"):
            status = main(["--language", alias, "--detect", "_Z1fv"])
            assert status == 0, alias

    def test_tree(self, capsys):
        _, out, _ = run(capsys, ["--tree", "_Z1fPKc"])
        lines = out.splitlines()
        assert lines[0] == "function"
        assert any("builtin 'char'" in line for line in lines)

    def test_strict_reports_failure(self, capsys):
        status, out, err = run(capsys, ["--strict", "memcpy"])
        assert status == 1
        assert out == ""
        assert "not a mangled name" in err

    def test_best_effort_passes_unknown_names_through(self, capsys):
        status, out, _ = run(capsys, ["memcpy"])
        assert (status, out.strip()) == (0, "memcpy")

    def test_list_languages(self, capsys):
        status, out, _ = run(capsys, ["--list-languages"])
        assert status == 0
        assert {"itanium", "msvc", "rust"} <= {line.split()[0] for line in out.splitlines()}

    def test_list_styles(self, capsys):
        _, out, _ = run(capsys, ["--list-styles"])
        assert out.split() == ["gnu", "llvm"]


class TestArgumentErrors:
    """Bad arguments must produce a clean message, never a traceback."""

    @pytest.mark.parametrize(
        "argv",
        [["--language", "cobol", "_Z1fv"], ["--style", "bogus", "_Z1fv"]],
        ids=["language", "style"],
    )
    def test_unknown_choice_exits_two(self, capsys, argv):
        with pytest.raises(SystemExit) as info:
            main(argv)
        assert info.value.code == 2
        assert "unknown" in capsys.readouterr().err

    def test_a_name_starting_with_a_dash_needs_the_separator(self, capsys):
        status, out, _ = run(capsys, ["--", "-_Z1fv"])
        assert status == 0
        assert out.strip() == "-_Z1fv"

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as info:
            main(["--version"])
        assert info.value.code == 0


class TestPipeline:
    def test_a_closed_pipe_is_not_an_error_per_symbol(self, capsys, monkeypatch):
        """`demangle | head` closes the pipe; that must not print one error per name.

        It used to catch `BrokenPipeError` inside the per-name loop and keep going,
        producing thousands of identical error lines on a real symbol table.
        """
        written = []

        class ClosingStdout:
            def write(self, text):
                written.append(text)
                if len(written) > 2:
                    raise BrokenPipeError(32, "Broken pipe")
                return len(text)

            def flush(self):
                pass

        monkeypatch.setattr("sys.stdin", io.StringIO("\n".join([VECTOR] * 500)))
        monkeypatch.setattr("sys.stdout", ClosingStdout())
        devnull_calls = []
        monkeypatch.setattr("os.dup2", lambda *a: devnull_calls.append(a))
        monkeypatch.setattr("sys.stdout.fileno", lambda: 1, raising=False)

        assert main([]) == 0
        assert devnull_calls, "stdout should be redirected to devnull after a broken pipe"
        assert capsys.readouterr().err == ""
