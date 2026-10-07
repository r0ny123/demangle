"""The `demangle` command.

`main(argv)` takes its arguments and returns a status, so it is driven directly rather
than through a subprocess -- which keeps these fast enough to be worth having.
"""

import base64
import errno
import io
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import threading

import pytest

from demangle import __version__, detect
from demangle.cli import build_parser, main

VECTOR = "_ZNSt6vectorIiSaIiEE9push_backERKi"
VECTOR_SPELLED = "std::vector<int, std::allocator<int>>::push_back(int const&)"

SOURCE = pathlib.Path(__file__).resolve().parent.parent / "src"


def command(*argv, **kwargs):
    """`python -m demangle` in a fresh interpreter, for what only a real process shows."""
    return subprocess.Popen(
        [sys.executable, "-m", "demangle", *argv], env={**os.environ, "PYTHONPATH": str(SOURCE)}, **kwargs
    )


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
        """`--detect` must report the forced language, not the one it would have guessed."""
        _, out, _ = run(capsys, ["--detect", "--language", "msvc", "_Z1fv"])
        assert out.strip() == "msvc"

    @pytest.mark.parametrize("strict", [[], ["--strict"]])
    def test_detect_names_a_forced_scheme_by_its_registered_name(self, capsys, strict):
        _, out, _ = run(capsys, ["--detect", *strict, "-l", "c++", "_Z1fv"])
        assert out == "itanium\n"

    def test_detect_strict_names_the_scheme_that_reads_the_name(self, capsys):
        status, out, err = run(capsys, ["--detect", "--strict", "_Z1fv", "_ZN3Foo"])
        assert (status, out) == (1, "itanium\n")
        assert err.startswith("_ZN3Foo: ")

    def test_detect_strict_answers_what_the_api_answers(self, capsys):
        names = ["_Z1fv", "?f@@YAXH@Z", "_RNvC6_123foo3bar", "$s4main3FooV3baryS2i_SStF", "_ZN3Foo"]
        _, out, _ = run(capsys, ["--detect", "--strict", *names])
        assert out.splitlines() == [detect(name, strict=True) for name in names if detect(name, strict=True)]

    def test_detect_strict_reads_under_the_runs_own_limits(self, capsys):
        deep = "_Z1f" + "P" * 400 + "i"
        assert main(["--detect", "--strict", deep]) == 1
        capsys.readouterr()
        assert main(["--relaxed", "--detect", "--strict", deep]) == 0
        assert capsys.readouterr().out == "itanium\n"

    def test_a_comma_separated_language_is_an_allow_list(self, capsys):
        _, out, _ = run(capsys, ["-l", "itanium,swift", "_Z1fv", "_OBJC_CLASS_$_NSData", "?f@@YAXH@Z"])
        assert out.splitlines() == ["f()", "_OBJC_CLASS_$_NSData", "?f@@YAXH@Z"]

    def test_an_allow_list_detects_among_its_schemes(self, capsys):
        _, out, _ = run(capsys, ["--detect", "-l", "itanium, swift", "_Z1fv", "?f@@YAXH@Z"])
        assert out.splitlines() == ["itanium", "-"]

    def test_an_allow_list_takes_aliases(self, capsys):
        _, out, _ = run(capsys, ["-l", "c++,ms", "_Z1fv", "?f@@YAXH@Z"])
        assert out.splitlines() == ["f()", "void __cdecl f(int)"]

    def test_a_trailing_comma_makes_a_list_of_one(self, capsys):
        """`gnuv2` forces the scheme on any name; `gnuv2,` detects, as `("gnuv2",)` does."""
        _, forced, _ = run(capsys, ["--detect", "-l", "gnuv2", "_Z1fv"])
        _, listed, _ = run(capsys, ["--detect", "-l", "gnuv2,", "_Z1fv"])
        assert (forced, listed) == ("gnuv2\n", "-\n")

    @pytest.mark.parametrize(
        ("value", "message"),
        [
            ("itanium,,swift", "has an empty name in it"),
            ("itanium,cobol", "unknown language 'cobol'"),
            (",", "has an empty name in it"),
        ],
    )
    def test_a_bad_allow_list_is_a_usage_error(self, capsys, value, message):
        with pytest.raises(SystemExit) as info:
            main(["-l", value, "_Z1fv"])
        assert info.value.code == 2
        assert message in capsys.readouterr().err

    def test_an_unknown_language_suggests_the_close_one(self, capsys):
        with pytest.raises(SystemExit):
            main(["-l", "itanum", "_Z1fv"])
        err = capsys.readouterr().err
        assert "did you mean 'itanium'?" in err
        assert "--list-languages" in err

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

    def test_flags_may_follow_names(self, capsys):
        """`demangle NAME... -p`, the way a command line grows when it is edited."""
        status, out, _ = run(capsys, ["_Z1fv", "-b", "_Z1gv", "-p"])
        assert (status, out) == (0, "_Z1fv ==> f\n_Z1gv ==> g\n")

    def test_after_the_separator_nothing_is_a_flag(self, capsys):
        _, out, _ = run(capsys, ["-p", "--", "-p", "_Z1fv"])
        assert out == "-p\nf\n"

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            (["--", "-p"], "-p\n"),
            (["--", "-_Z1fv"], "-_Z1fv\n"),
            (["--types", "-l", "itanium", "--", "Pi", "-x"], "int*\n-x\n"),
            (["_Z1fv", "--", "--strict"], "f()\n--strict\n"),
            (["--", "--"], "--\n"),
        ],
    )
    def test_a_name_after_the_separator_that_looks_like_a_flag_is_a_name(self, capsys, argv, expected):
        status, out, _ = run(capsys, argv)
        assert (status, out) == (0, expected)

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as info:
            main(["--version"])
        assert info.value.code == 0
        assert capsys.readouterr().out == f"demangle {__version__}\n"

    def test_a_usage_error_is_two_lines_not_a_screenful(self, capsys):
        with pytest.raises(SystemExit) as info:
            main(["--bogus"])
        assert info.value.code == 2
        assert capsys.readouterr().err.splitlines() == [
            "usage: demangle [options] [NAME ...]",
            "demangle: error: unrecognized arguments: --bogus",
        ]


class TestHelp:
    """`-h` for the options most runs use, `--help` for all of them; both to stdout."""

    @staticmethod
    def _help(capsys, flag):
        with pytest.raises(SystemExit) as info:
            main([flag])
        assert info.value.code == 0
        captured = capsys.readouterr()
        assert captured.err == ""
        return captured.out

    def test_both_open_with_the_usage_and_the_examples(self, capsys):
        for flag in ("-h", "--help"):
            text = self._help(capsys, flag)
            assert text.startswith("usage: demangle [options] [NAME ...]\n")
            examples = text.index("examples:")
            assert "nm -a libfoo.so | demangle" in text[examples:]
            assert examples < text.index("--strict")

    def test_short_help_is_short(self, capsys):
        text = self._help(capsys, "-h")
        assert "common options:" in text
        assert "--no-tag-kind" not in text
        assert "demangle --help" in text
        assert len(text.splitlines()) < len(self._help(capsys, "--help").splitlines()) / 2

    def test_full_help_names_every_option(self, capsys):
        text = self._help(capsys, "--help")
        for action in build_parser()._actions:
            for option in action.option_strings:
                assert option in text, option

    def test_short_help_names_only_real_options(self, capsys):
        text = self._help(capsys, "-h")
        options = text[text.index("common options:") :]
        known = {option for action in build_parser()._actions for option in action.option_strings}
        assert set(re.findall(r"(?<![\w-])(--?[A-Za-z0-9_][\w-]*)", options)) <= known

    def test_full_help_states_the_exit_statuses(self, capsys):
        text = self._help(capsys, "--help")
        statuses = text[text.index("exit status:") :]
        for status in ("0", "1", "2", "130"):
            assert f"\n  {status} " in statuses

    def test_a_flag_is_never_split_at_its_hyphen(self, capsys, monkeypatch):
        monkeypatch.setenv("COLUMNS", "60")
        text = self._help(capsys, "--help")
        assert not re.search(r"-\n", text)

    def test_help_wins_over_the_rest_of_the_line(self, capsys):
        """`demangle -p -h` is a question about `-p`, not a request to demangle nothing."""
        assert "common options:" in self._help(capsys, "-h")
        with pytest.raises(SystemExit) as info:
            main(["-p", "--strict", "-h"])
        assert info.value.code == 0


class TestPipeline:
    def test_a_closed_pipe_is_not_an_error_per_symbol(self, capsys, monkeypatch):
        """`demangle | head` closes the pipe; that must not print one error per name.

        Caught inside the per-name loop instead, a closed pipe produces thousands of
        identical error lines on a real symbol table.
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

    def test_a_pipe_closed_before_the_last_flush_is_not_an_error_either(self, monkeypatch):
        """Output short enough to sit in the buffer meets the closed pipe on the way out."""

        class ClosedOnFlush(io.StringIO):
            def flush(self):
                raise BrokenPipeError(32, "Broken pipe")

        monkeypatch.setattr("sys.stdout", ClosedOnFlush())
        monkeypatch.setattr("os.dup2", lambda *a: None)
        monkeypatch.setattr("sys.stdout.fileno", lambda: 1, raising=False)
        assert main(["_Z1fv"]) == 0

    def test_windows_einval_on_a_closed_pipe_is_a_closed_pipe(self, monkeypatch):
        class ClosedOnFlush(io.StringIO):
            def flush(self):
                raise OSError(errno.EINVAL, "Invalid argument")

        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr("sys.stdout", ClosedOnFlush())
        monkeypatch.setattr("os.dup2", lambda *a: None)
        monkeypatch.setattr("sys.stdout.fileno", lambda: 1, raising=False)
        assert main(["_Z1fv"]) == 0

    def test_einval_elsewhere_is_not_swallowed(self, monkeypatch):
        class Failing(io.StringIO):
            def flush(self):
                raise OSError(errno.EINVAL, "Invalid argument")

        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("sys.stdout", Failing())
        with pytest.raises(OSError):
            main(["_Z1fv"])

    def test_head_on_a_long_pipe_ends_quietly(self, tmp_path):
        """`demangle < table | head -1`, through a real pipe, so SIGPIPE's path is the real one."""
        table = tmp_path / "table.txt"
        table.write_bytes(f"0000 T {VECTOR}\n".encode() * 200_000)
        with table.open("rb") as names:
            process = command(stdin=names, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert process.stdout is not None and process.stderr is not None
            first = process.stdout.readline()
            process.stdout.close()
            err = process.stderr.read()
            process.wait(timeout=60)
        assert first == f"0000 T {VECTOR_SPELLED}{os.linesep}".encode()
        assert (process.returncode, err) == (0, b"")

    def test_each_line_is_answered_before_the_next_arrives(self):
        """Nothing waits for the input to end, or for 64K of it: `tail -f log | demangle`."""
        process = command(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdin is not None and process.stdout is not None
        try:
            process.stdin.write(b"at _Z1fv\n")
            process.stdin.flush()
            answer = []
            stdout = process.stdout
            reader = threading.Thread(target=lambda: answer.append(stdout.readline()), daemon=True)
            reader.start()
            reader.join(60)
            assert answer, "the first line was not answered while the input stayed open"
            assert answer == [f"at f(){os.linesep}".encode()]
        finally:
            process.stdin.close()
            process.wait(timeout=60)

    @staticmethod
    def _interrupt(*_, **__):
        raise KeyboardInterrupt

    @pytest.mark.parametrize("where", ["_expand", "_parse"])
    def test_ctrl_c_on_windows_exits_130_without_a_traceback(self, capsys, monkeypatch, where):
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr(f"demangle.cli.{where}", self._interrupt)
        assert main(["_Z1fv"]) == 130
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize("where", ["_expand", "_parse"])
    def test_ctrl_c_elsewhere_dies_of_sigint(self, capsys, monkeypatch, where):
        """So that a shell loop over the command stops, as it does for one Ctrl-C killed."""
        calls = []
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr(f"demangle.cli.{where}", self._interrupt)
        monkeypatch.setattr("signal.signal", lambda *a: calls.append(("signal", *a)))
        monkeypatch.setattr("os.kill", lambda *a: calls.append(("kill", *a)))
        main(["_Z1fv"])
        assert calls == [("signal", signal.SIGINT, signal.SIG_DFL), ("kill", os.getpid(), signal.SIGINT)]
        assert capsys.readouterr().err == ""

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows has no death by signal")
    def test_ctrl_c_on_a_real_process(self):
        process = command(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(b"_Z1fv\n")
        process.stdin.flush()
        assert process.stdout.readline() == b"f()\n"
        process.send_signal(signal.SIGINT)
        _, err = process.communicate(timeout=60)
        assert process.returncode == -signal.SIGINT
        assert err == b""


class TestStandardInput:
    """When standard input is read, and what `-` means."""

    @staticmethod
    def _stdin(monkeypatch, text, terminal=False):
        stdin = io.StringIO(text)
        monkeypatch.setattr(stdin, "isatty", lambda: terminal)
        monkeypatch.setattr("sys.stdin", stdin)

    def test_a_dash_reads_standard_input(self, capsys, monkeypatch):
        self._stdin(monkeypatch, "T _Z1fv\n")
        assert main(["-"]) == 0
        assert capsys.readouterr().out == "T f()\n"

    def test_a_dash_among_names_reads_standard_input_in_its_place(self, capsys, monkeypatch):
        self._stdin(monkeypatch, "_Z1gv\n")
        main(["_Z1fv", "-", "_Z1hv"])
        assert capsys.readouterr().out == "f()\ng()\nh()\n"

    def test_a_dash_after_the_separator_still_reads_standard_input(self, capsys, monkeypatch):
        self._stdin(monkeypatch, "_Z1gv\n")
        main(["--", "-"])
        assert capsys.readouterr().out == "g()\n"

    def test_a_terminal_with_no_names_is_a_usage_error_not_a_wait(self, capsys, monkeypatch):
        self._stdin(monkeypatch, VECTOR, terminal=True)
        with pytest.raises(SystemExit) as info:
            main([])
        assert info.value.code == 2
        err = capsys.readouterr().err
        assert "standard input is a terminal" in err
        assert "give - to type them" in err

    def test_a_dash_reads_a_terminal_because_it_asks_to(self, capsys, monkeypatch):
        self._stdin(monkeypatch, "_Z1fv\n", terminal=True)
        assert main(["-"]) == 0
        assert capsys.readouterr().out == "f()\n"

    def test_names_never_touch_standard_input(self, capsys, monkeypatch):
        self._stdin(monkeypatch, "", terminal=True)
        assert main(["_Z1fv"]) == 0

    def test_a_closed_standard_input_is_a_usage_error(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.stdin", None)
        with pytest.raises(SystemExit) as info:
            main([])
        assert info.value.code == 2
        assert "standard input is closed" in capsys.readouterr().err

    def test_a_closed_standard_input_does_not_matter_to_names(self, capsys, monkeypatch):
        monkeypatch.setattr("sys.stdin", None)
        assert main(["_Z1fv"]) == 0
        assert capsys.readouterr().out == "f()\n"

    def test_bytes_that_are_not_utf8_come_back_as_they_went_in(self, tmp_path):
        table = tmp_path / "table.txt"
        table.write_bytes(b"\xff _Z1fv\r\nno newline at the end")
        with table.open("rb") as names:
            process = command(stdin=names, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = process.communicate(timeout=60)
        assert (out, err) == (b"\xff f()\r\nno newline at the end", b"")

    def test_crlf_comes_back_crlf_where_standard_output_writes_newlines_as_crlf(self, monkeypatch):
        """Windows' streams, in-process: `\\n` is written `\\r\\n`, so `\\r\\n` must not be."""
        stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", newline="\r\n")
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"T _Z1fv\r\nx\r\ny\n"), encoding="utf-8"))
        monkeypatch.setattr("sys.stdout", stdout)
        assert main([]) == 0
        assert stdout.buffer.getvalue() == b"T f()\r\nx\r\ny\r\n"


class TestTheModule:
    def test_python_dash_m_runs_the_command(self):
        process = command(VECTOR, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = process.communicate(timeout=60)
        assert (process.returncode, out, err) == (0, VECTOR_SPELLED + "\n", "")


class TestTheStreamFilter:
    """With no arguments the command is a filter, not a line reader.

    A line reader would make the README's own first example a no-op:

        $ printf '0000000000001139 T _ZN3foo3barEv\n' | demangle
        0000000000001139 T _ZN3foo3barEv

    `nm` writes an address and a type letter before the name, so a whole line is never
    a symbol. `c++filt`, `demumble` and `rustfilt` all substitute symbol-shaped words
    and copy the rest through, and so does this.
    """

    @staticmethod
    def _filter(monkeypatch, capsys, text, argv=()):
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(text))
        status = main(list(argv))
        return status, capsys.readouterr().out

    def test_the_symbol_in_an_nm_line_is_replaced_in_place(self, monkeypatch, capsys):
        _, out = self._filter(monkeypatch, capsys, "0000000000001139 T _ZN3foo3barEv\n")
        assert out == "0000000000001139 T foo::bar()\n"

    def test_text_around_a_symbol_is_copied_through(self, monkeypatch, capsys):
        _, out = self._filter(monkeypatch, capsys, "call\t_ZN3foo3barEv@plt ; comment\n")
        assert out == "call\tfoo::bar()@plt ; comment\n"

    def test_several_schemes_in_one_stream(self, monkeypatch, capsys):
        _, out = self._filter(monkeypatch, capsys, "a _ZN3foo3barEv b ?f@@YAXH@Z c\n")
        assert out == "a foo::bar() b void __cdecl f(int) c\n"

    def test_an_ordinary_word_is_left_alone(self, monkeypatch, capsys):
        """demumble's warning: a bare type mangling looks like an English word."""
        _, out = self._filter(monkeypatch, capsys, "I like Pi and cake\n")
        assert out == "I like Pi and cake\n"

    def test_both_shows_the_mangled_name_too(self, monkeypatch, capsys):
        _, out = self._filter(monkeypatch, capsys, "T _ZN3foo3barEv\n", ["--both"])
        assert out == "T _ZN3foo3barEv ==> foo::bar()\n"

    def test_only_demangled_drops_everything_else(self, monkeypatch, capsys):
        _, out = self._filter(monkeypatch, capsys, "T _ZN3foo3barEv x\nnothing here\n", ["-m"])
        assert out == "foo::bar()\n"

    def test_an_argument_is_one_whole_name(self, capsys):
        """Word-splitting belongs to the stream, not to a name a caller typed."""
        main(["-[NSString length]"])
        assert capsys.readouterr().out == "-[NSString length]\n"


class TestNullFlag:
    """`-0`: whole names ended by NUL in, answers ended by NUL out.

    For a name a newline cannot end -- an Objective-C method with a space in it, one
    with a newline in it -- and for whatever `find -print0` and `xargs -0` hand over.
    """

    def test_records_in_and_out(self, capsys, monkeypatch):
        _, out, _ = run(capsys, ["-0"], stdin=f"{VECTOR}\0memcpy\0", monkeypatch=monkeypatch)
        assert out == f"{VECTOR_SPELLED}\0memcpy\0"

    def test_a_record_is_one_whole_name_not_a_line_to_filter(self, capsys, monkeypatch):
        _, out, _ = run(capsys, ["-0"], stdin="T _Z1fv\0a\n_Z1gv\0", monkeypatch=monkeypatch)
        assert out == "T _Z1fv\0a\n_Z1gv\0"

    def test_a_last_record_without_its_nul_still_reads(self, capsys, monkeypatch):
        _, out, _ = run(capsys, ["-0"], stdin="_Z1fv\0_Z1gv", monkeypatch=monkeypatch)
        assert out == "f()\0g()\0"

    def test_an_empty_record_stays_one(self, capsys, monkeypatch):
        _, out, _ = run(capsys, ["-0"], stdin="_Z1fv\0\0_Z1gv\0", monkeypatch=monkeypatch)
        assert out == "f()\0\0g()\0"

    def test_names_on_the_command_line_end_with_nul_too(self, capsys):
        _, out, _ = run(capsys, ["--null", "-b", "_Z1fv", "_Z1gv"])
        assert out == "_Z1fv ==> f()\0_Z1gv ==> g()\0"

    def test_a_multi_line_tree_is_one_record(self, capsys):
        _, out, _ = run(capsys, ["-0", "--tree", "_Z1fPKc"])
        assert out.count("\0") == 1
        assert out.endswith("\0")
        assert "\n" in out

    def test_types_read_records_too(self, capsys, monkeypatch):
        _, out, _ = run(capsys, ["-0", "--types", "-l", "itanium"], stdin="Pi\0I like Pi\0", monkeypatch=monkeypatch)
        assert out == "int*\0I like Pi\0"

    def test_errors_stay_lines_on_standard_error(self, capsys):
        status, out, err = run(capsys, ["-0", "--strict", "memcpy"])
        assert (status, out) == (1, "")
        assert err.endswith("\n")
        assert "\0" not in err


class TestLimitFlags:
    def test_a_tight_output_bound_refuses(self, capsys):
        assert main(["--max-output", "4", "--strict", "_ZNSt6vectorIiSaIiEE9push_backERKi"]) == 1
        assert "output length" in capsys.readouterr().err

    def test_relaxed_reads_what_the_default_refuses(self, capsys):
        deep = "_Z1f" + "P" * 400 + "i"
        assert main(["--strict", deep]) == 1
        capsys.readouterr()
        assert main(["--relaxed", "--strict", deep]) == 0

    @pytest.mark.parametrize(
        ("argv", "remedy"),
        [
            (["--max-output", "4", VECTOR], "--max-output N or --relaxed raises it"),
            (["--max-input", "3", "_Z1fv"], "--max-input N or --relaxed raises it"),
            (["_Z1f" + "P" * 400 + "i"], "--max-depth N or --relaxed raises it"),
        ],
        ids=["output", "input", "depth"],
    )
    def test_a_bound_that_was_hit_says_how_to_move_it(self, capsys, argv, remedy):
        assert main(["--strict", *argv]) == 1
        assert remedy in capsys.readouterr().err

    def test_a_non_positive_bound_is_rejected(self):
        with pytest.raises(SystemExit):
            main(["--max-depth", "0", "_Z1fv"])


class TestPartFlags:
    """`-p`, `--base-name` and `--no-return-type`: one piece of a name, not the whole.

    Checked against `c++filt -p` where the two agree by design. They differ on one
    thing, deliberately: a version decoration is part of the symbol, not part of the
    signature, so `-p` keeps `@@GLIBCXX_3.4` where `c++filt` drops it.
    """

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            (VECTOR, "std::vector<int, std::allocator<int>>::push_back"),
            ("_ZSt4sortIPiEvT_S1_", "std::sort<int*>"),
            ("_ZTVN3FooE", "vtable for Foo"),
            ("_Znwm", "operator new"),
            ("_ZN3FooC1Ei", "Foo::Foo"),
            ("?f@Foo@@AEBAXH@Z", "Foo::f"),
        ],
    )
    def test_no_params(self, capsys, name, expected):
        _, out, _ = run(capsys, ["-p", name])
        assert out.strip() == expected

    def test_no_params_keeps_the_symbols_decoration(self, capsys):
        _, out, _ = run(capsys, ["-p", "_ZN3Foo3barEv@@GLIBCXX_3.4"])
        assert out.strip() == "Foo::bar@@GLIBCXX_3.4"

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            (VECTOR, "push_back"),
            ("_ZSt4sortIPiEvT_S1_", "sort<int*>"),
            ("?f@Foo@@AEBAXH@Z", "f"),
            ("$s4main3FooV3baryS2i_SStF", "bar"),
        ],
    )
    def test_base_name(self, capsys, name, expected):
        _, out, _ = run(capsys, ["--base-name", name])
        assert out.strip() == expected

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            (VECTOR, VECTOR_SPELLED),
            ("_ZSt4sortIPiEvT_S1_", "std::sort<int*>(int*, int*)"),
            # MSVC uses the scheme's own option (`llvm-undname --no-return-type`):
            # `private: ` comes before the return type, so there is no prefix to strip.
            ("?f@Foo@@AEBAXH@Z", "private: __cdecl Foo::f(int) const"),
        ],
    )
    def test_no_return_type(self, capsys, name, expected):
        """Cut by what the return type *is*, so one with spaces in it goes whole."""
        _, out, _ = run(capsys, ["--no-return-type", name])
        assert out.strip() == expected

    def test_a_return_type_with_spaces_is_cut_whole(self, capsys):
        _, out, _ = run(capsys, ["--no-return-type", "_Z1fIPKcET_v"])
        assert out.strip() == "f<char const*>()"

    @pytest.mark.parametrize("flag", ["-p", "--base-name", "--no-return-type"])
    def test_a_name_that_is_not_mangled_passes_through(self, capsys, flag):
        _, out, _ = run(capsys, [flag, "memcpy"])
        assert out.strip() == "memcpy"

    @pytest.mark.parametrize("flag", ["-p", "--base-name", "--no-return-type"])
    def test_strict_reports_a_name_with_no_parts(self, capsys, flag):
        assert main([flag, "--strict", "memcpy"]) == 1
        assert "memcpy" in capsys.readouterr().err

    def test_they_apply_in_the_stream_filter_too(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", io.StringIO(f"0000000000001139 T {VECTOR}\n"))
        main(["-p"])
        expected = "std::vector<int, std::allocator<int>>::push_back"
        assert capsys.readouterr().out == f"0000000000001139 T {expected}\n"

    @pytest.mark.parametrize("argv", [["-p", "--base-name"], ["-p", "--no-return-type"], ["--base-name", "-p"]])
    def test_two_of_them_at_once_is_an_error(self, argv):
        """A flag that is silently ignored is worse than an error."""
        with pytest.raises(SystemExit):
            main([*argv, "_Z1fv"])

    def test_the_style_reaches_them(self, capsys):
        _, out, _ = run(capsys, ["-p", "--style", "gnu", VECTOR])
        assert out.strip() == "std::vector<int, std::allocator<int> >::push_back"


class TestTypeFlag:
    """`--types`: read a bare type encoding, the way `c++filt -t` does."""

    def test_a_type_on_the_command_line(self, capsys):
        _, out, _ = run(capsys, ["--types", "-l", "itanium", "Pi", "PKFvRiE"])
        assert out == "int*\nvoid (*)(int&) const\n"

    def test_a_type_per_line_of_stdin(self, capsys, monkeypatch):
        _, out, _ = run(capsys, ["--types", "-l", "msvc"], stdin="PEAX\n.PEAX\n", monkeypatch=monkeypatch)
        assert out == "void *\nvoid *\n"

    def test_it_is_not_the_stream_filter(self, capsys, monkeypatch):
        """`I like Pi` stays `I like Pi`, because each line is one encoding or nothing.

        The default path picks symbol-shaped words out of mixed text. A type encoding is
        not symbol-shaped -- `Pi` is an ordinary word -- so `--types` reads whole inputs
        instead of scanning them, and a line that is not an encoding comes back whole.
        """
        _, out, _ = run(capsys, ["--types", "-l", "itanium"], stdin="I like Pi\n", monkeypatch=monkeypatch)
        assert out == "I like Pi\n"

    def test_an_unreadable_encoding_comes_back_unchanged(self, capsys):
        status, out, _ = run(capsys, ["--types", "-l", "itanium", "ZZZ"])
        assert status == 0
        assert out == "ZZZ\n"

    def test_strict_reports_it_instead(self, capsys):
        status, out, err = run(capsys, ["--types", "-l", "itanium", "--strict", "ZZZ"])
        assert status == 1
        assert out == ""
        assert "ZZZ" in err

    def test_the_tree_form(self, capsys):
        _, out, _ = run(capsys, ["--types", "-l", "itanium", "--tree", "Pi"])
        assert out.splitlines() == ["pointer", "  builtin 'int'"]

    def test_the_style_is_honoured(self, capsys):
        _, out, _ = run(capsys, ["--types", "-l", "itanium", "-s", "gnu", "Dv4_i"])
        assert out == "int __vector(4)\n"

    def test_it_needs_a_language(self, capsys):
        with pytest.raises(SystemExit):
            run(capsys, ["--types", "Pi"])

    def test_it_refuses_the_questions_it_cannot_answer(self, capsys):
        for extra in (["--detect"], ["--base-name"], ["-p"], ["--no-return-type"]):
            with pytest.raises(SystemExit):
                run(capsys, ["--types", "-l", "itanium", *extra, "Pi"])

    def test_it_refuses_a_list_of_schemes(self, capsys):
        with pytest.raises(SystemExit):
            run(capsys, ["--types", "-l", "itanium,msvc", "Pi"])

    def test_it_refuses_a_scheme_with_no_type_grammar_once_rather_than_per_name(self, capsys):
        with pytest.raises(SystemExit):
            run(capsys, ["--types", "-l", "rust", "Pi", "Pc", "i"])

    def test_crlf_input_reads_the_same_as_lf(self, capsys, monkeypatch):
        _, out, _ = run(capsys, ["--types", "-l", "msvc"], stdin="PEAX\r\n", monkeypatch=monkeypatch)
        assert out == "void *\n"

    def test_an_argument_ending_in_a_line_end_reads_the_same(self, capsys):
        _, out, _ = run(capsys, ["--types", "-l", "itanium", "Pi\r", "Pc\n", "Pv\r\n"])
        assert out == "int*\nchar*\nvoid*\n"


class TestSimplifiedFlag:
    """`--simplified`: Swift names the way Xcode shows them."""

    def test_a_module_qualification_goes(self, capsys):
        _, out, _ = run(capsys, ["--simplified", "_TtO6Monads6Either"])
        assert out == "Either\n"

    def test_a_signature_becomes_its_labels(self, capsys):
        _, out, _ = run(capsys, ["--simplified", "_TtFSiSu"])
        assert out == "(_:)\n"

    def test_it_leaves_other_schemes_alone(self, capsys):
        _, out, _ = run(capsys, ["--simplified", VECTOR, "?f@@YAXH@Z"])
        assert out.splitlines() == [VECTOR_SPELLED, "void __cdecl f(int)"]

    def test_without_it_the_full_spelling_stands(self, capsys):
        _, out, _ = run(capsys, ["_TtO6Monads6Either"])
        assert out == "Monads.Either\n"


class TestJsonFlag:
    def test_the_tree_as_json(self, capsys):
        import json

        _, out, _ = run(capsys, ["--json", "_Z1fPi"])
        assert json.loads(out)["parameters"][0]["kind"] == "pointer"

    def test_it_works_for_a_bare_type_too(self, capsys):
        import json

        _, out, _ = run(capsys, ["--json", "--types", "-l", "itanium", "Pi"])
        assert json.loads(out) == {"kind": "pointer", "inner": {"kind": "builtin", "spelling": "int"}}

    def test_it_is_one_line_per_name(self, capsys):
        _, out, _ = run(capsys, ["--json", "_Z1fv", "?f@@YAXH@Z"])
        assert len(out.splitlines()) == 2

    def test_asking_for_both_spellings_of_the_tree_is_refused(self, capsys):
        with pytest.raises(SystemExit):
            run(capsys, ["--json", "--tree", "_Z1fv"])


class TestJsonLinesFlag:
    """`--json-lines`: one object per name, for a program to read rather than a person.

    `--json` stays the parse tree, as it was released; this is the other question, what
    the command says about each name, as data.
    """

    @staticmethod
    def _records(out):
        return [json.loads(line) for line in out.splitlines()]

    def test_one_object_per_name(self, capsys):
        status, out, _ = run(capsys, ["--json-lines", "_Z1fv", "memcpy", "?f@@YAXH@Z"])
        assert status == 0
        assert self._records(out) == [
            {"mangled": "_Z1fv", "demangled": "f()", "language": "itanium"},
            {"mangled": "memcpy", "demangled": "memcpy", "language": None},
            {"mangled": "?f@@YAXH@Z", "demangled": "void __cdecl f(int)", "language": "msvc"},
        ]

    def test_language_is_the_scheme_that_read_it_not_the_one_it_looks_like(self, capsys):
        _, out, _ = run(capsys, ["--json-lines", "_ZN3Foo"])
        assert self._records(out) == [{"mangled": "_ZN3Foo", "demangled": "_ZN3Foo", "language": None}]

    def test_language_is_the_canonical_name_when_an_alias_forced_it(self, capsys):
        _, out, _ = run(capsys, ["--json-lines", "-l", "c++", "_Z1fv"])
        assert self._records(out)[0]["language"] == "itanium"

    def test_demangled_is_what_the_line_would_have_said(self, capsys):
        _, out, _ = run(capsys, ["--json-lines", "-p", "-s", "gnu", VECTOR])
        assert self._records(out)[0]["demangled"] == "std::vector<int, std::allocator<int> >::push_back"

    def test_signature_adds_the_parts(self, capsys):
        _, out, _ = run(capsys, ["--json-lines", "--signature", "_ZNK3Foo3barEi"])
        (record,) = self._records(out)
        assert record == {
            "mangled": "_ZNK3Foo3barEi",
            "demangled": "Foo::bar(int) const",
            "language": "itanium",
            "qualified_name": "Foo::bar",
            "base_name": "bar",
            "namespace": "Foo",
            "parameters": ["int"],
            "return_type": None,
            "calling_convention": None,
            "qualifiers": ["const"],
            "special": None,
            "decoration": "",
            "is_function": True,
            "is_data": False,
            "is_ctor_or_dtor": False,
        }

    def test_every_record_has_the_same_keys(self, capsys):
        _, out, _ = run(capsys, ["--json-lines", "--signature", "_Z1fv", "memcpy"])
        read, unread = self._records(out)
        assert list(read) == list(unread)
        assert unread["base_name"] is None

    def test_only_demangled_drops_the_unread(self, capsys):
        _, out, _ = run(capsys, ["--json-lines", "-m", "_Z1fv", "memcpy"])
        assert [record["mangled"] for record in self._records(out)] == ["_Z1fv"]

    def test_strict_reports_an_unread_name_instead(self, capsys):
        status, out, err = run(capsys, ["--json-lines", "--strict", "_Z1fv", "memcpy"])
        assert (status, len(self._records(out))) == (1, 1)
        assert err.startswith("memcpy: ")

    def test_a_repeated_name_is_answered_each_time_it_comes(self, capsys):
        status, out, err = run(capsys, ["--json-lines", "--strict", "_Z1fv", "memcpy", "_Z1fv", "memcpy"])
        assert [record["mangled"] for record in self._records(out)] == ["_Z1fv", "_Z1fv"]
        assert (status, err.count("memcpy: ")) == (1, 2)

    def test_strip_underscore_reaches_the_record(self, capsys):
        _, out, _ = run(capsys, ["--json-lines", "-_", "__Z1fv", "_foo"])
        assert [(r["demangled"], r["language"]) for r in self._records(out)] == [("f()", "itanium"), ("_foo", None)]

    def test_over_a_stream_a_record_per_symbol_and_none_for_the_text(self, capsys, monkeypatch):
        text = f"0000 T {VECTOR}\n0000 T main_loop\nsome words\n0000 t _Z1fv\n"
        _, out, _ = run(capsys, ["--json-lines"], stdin=text, monkeypatch=monkeypatch)
        assert [record["mangled"] for record in self._records(out)] == [VECTOR, "_Z1fv"]

    def test_with_null_each_record_ends_with_nul(self, capsys):
        _, out, _ = run(capsys, ["--json-lines", "-0", "_Z1fv", "x"])
        assert [json.loads(record)["mangled"] for record in out.split("\0")[:-1]] == ["_Z1fv", "x"]

    def test_bytes_that_are_not_utf8_stay_valid_json(self, capsys):
        _, out, _ = run(capsys, ["--json-lines", "_Z1f\udcffv"])
        assert self._records(out)[0]["mangled"] == "_Z1f\udcffv"

    def test_bytes_that_are_not_utf8_are_given_back_in_base64(self, capsys, monkeypatch):
        """A reader that turns `\\udcff` into U+FFFD can still recover the name exactly."""
        stdin = io.TextIOWrapper(io.BytesIO(b"_Z3foo\xff\0_Z1fv\0caf\xc3\xa9\0"), encoding="utf-8")
        monkeypatch.setattr("sys.stdin", stdin)
        main(["--json-lines", "-0"])
        records = [json.loads(record) for record in capsys.readouterr().out.split("\0")[:-1]]
        assert records[0] == {
            "mangled": "_Z3foo\udcff",
            "mangled_bytes": "X1ozZm9v/w==",
            "demangled": "_Z3foo\udcff",
            "language": None,
        }
        assert base64.b64decode(records[0]["mangled_bytes"]) == b"_Z3foo\xff"
        assert ["mangled_bytes" in record for record in records[1:]] == [False, False]

    @pytest.mark.parametrize("other", ["--tree", "--json", "--detect", "--both"])
    def test_it_is_one_answer_among_several(self, capsys, other):
        with pytest.raises(SystemExit) as info:
            main(["--json-lines", other, "_Z1fv"])
        assert info.value.code == 2
        assert "choose one" in capsys.readouterr().err

    def test_signature_alone_says_what_it_needs(self, capsys):
        with pytest.raises(SystemExit) as info:
            main(["--signature", "_Z1fv"])
        assert info.value.code == 2
        assert "add --json-lines" in capsys.readouterr().err


class TestMsvcSuppressionFlags:
    """`--no-calling-convention` and its eight siblings, from the command line.

    Five are `llvm-undname`'s flags and four are `UnDecorateSymbolName` mask bits it has
    no flag for; `tests/test_msvc_options.py` scores each set against its own reference.
    What is checked here is that the command reaches them at all, and that they compose.
    """

    MEMBER = "?bar@Foo@@QEBAHXZ"
    TAGGED = "?f@@YAXPEAU?$C@H@@@Z"

    @pytest.mark.parametrize(
        "flag,name,expected",
        [
            ("--no-calling-convention", MEMBER, "public: int Foo::bar(void) const"),
            ("--no-access-specifier", MEMBER, "int __cdecl Foo::bar(void) const"),
            ("--no-member-type", "?g@C@@UEAAXXZ", "public: void __cdecl C::g(void)"),
            ("--no-variable-type", "?x@@3HA", "x"),
            ("--no-ms-keywords", MEMBER, "public: int Foo::bar(void) const"),
            ("--no-leading-underscores", MEMBER, "public: int cdecl Foo::bar(void) const"),
            ("--no-this-type", MEMBER, "public: int __cdecl Foo::bar(void)"),
            ("--no-tag-kind", TAGGED, "void __cdecl f(C<int> *)"),
        ],
    )
    def test_each_flag_reaches_the_scheme(self, capsys, flag, name, expected):
        _, out, _ = run(capsys, [flag, name])
        assert out.strip() == expected

    def test_they_compose(self, capsys):
        _, out, _ = run(capsys, ["--no-tag-kind", "--no-calling-convention", "--no-access-specifier", self.TAGGED])
        assert out.strip() == "void f(C<int> *)"

    def test_none_of_them_disturbs_another_scheme(self, capsys):
        """They are MSVC's, and a run with one set still spells an Itanium name in full."""
        _, out, _ = run(capsys, ["--no-ms-keywords", "--no-tag-kind", VECTOR])
        assert out.strip() == VECTOR_SPELLED

    def test_they_apply_in_the_stream_filter_too(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", io.StringIO(f"0000000000001139 T {self.TAGGED}\n"))
        main(["--no-tag-kind"])
        assert capsys.readouterr().out == "0000000000001139 T void __cdecl f(C<int> *)\n"


class TestTheReturnTypeFlags:
    """`--no-return-type` and `--ret-postfix`, against libiberty's own two options.

    `DMGL_RET_DROP` and `DMGL_RET_POSTFIX` are what these are, and no shipped tool
    exposes either -- `c++filt` has no flag for them -- so the expectations here were
    read off `cplus_demangle_v3(name, DMGL_PARAMS | DMGL_ANSI | flag)` built from
    libiberty's own `cp-demangle.c`, over every Itanium name in the corpora. 342 of the
    345 the reference reads match exactly; the three that do not differ in a `std::`
    abbreviation under both flags alike, which is a spelling question and not this one.
    """

    @pytest.mark.parametrize(
        ("name", "dropped", "postfix"),
        [
            # A plain template function: the return type is a prefix.
            ("_Z1fIiET_S0_", "f<int>(int)", "f<int>(int)int"),
            ("_Z1fIiEvT_", "f<int>(int)", "f<int>(int)void"),
            # One that *wraps* the declarator: `int (*g<int>(int))(int)` has no prefix
            # to strip.
            ("_Z1gIiEPFT_S0_ES0_", "g<int>(int)", "g<int>(int)int (*)(int)"),
            # A name whose mangling carries no return type at all is untouched by both.
            ("_Z1fi", "f(int)", "f(int)"),
        ],
    )
    def test_against_libiberty(self, capsys, name, dropped, postfix):
        _, out, _ = run(capsys, ["--no-return-type", name])
        assert out.strip() == dropped
        _, out, _ = run(capsys, ["--ret-postfix", name])
        assert out.strip() == postfix

    def test_msvc_answers_through_its_own_option(self, capsys):
        """MSVC writes the return type around the declarator, so it is a scheme option.

        `int (__cdecl * __cdecl g(int))(int)` is the same shape as the Itanium case
        above; this pins that both flags reach it.
        """
        _, out, _ = run(capsys, ["--no-return-type", "?g@@YAP6AHH@ZH@Z"])
        assert out.strip() == "__cdecl g(int)"
        _, out, _ = run(capsys, ["--ret-postfix", "?g@@YAP6AHH@ZH@Z"])
        assert out.strip() == "__cdecl g(int)int (__cdecl *)(int)"

    def test_the_two_are_mutually_exclusive(self, capsys):
        with pytest.raises(SystemExit):
            run(capsys, ["--no-return-type", "--ret-postfix", VECTOR])


class TestStripUnderscore:
    """`-_`, as `c++filt --strip-underscore` and `llvm-cxxfilt --strip-underscore`.

    Every expectation here was taken from both references, which agree on all of them.

    What the flag is *for* here is narrower than it looks, because the Itanium, Swift and
    Rust readers tolerate the extra underscore a Mach-O symbol carries -- `__Z1fv`
    and `_$s...` read with or without it. The schemes that do not are the ones whose
    prefix is not itself an underscore: an MSVC name opens with `?`, and it does not read
    until the underscore is gone.
    """

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("__Z1fv", "f()"),
            ("__ZN3foo3barEv", "foo::bar()"),
            # The one that needs it.
            ("_?f@@YAXH@Z", "void __cdecl f(int)"),
            # When the stripped form does not read, both references print the name as given;
            # `_Z1fv` loses its reading under `c++filt --strip-underscore` too.
            ("_Z1fv", "_Z1fv"),
            ("_foo", "_foo"),
            ("_", "_"),
        ],
    )
    def test_against_both_references(self, capsys, name, expected):
        _, out, _ = run(capsys, ["--strip-underscore", name])
        assert out.strip() == expected

    def test_off_by_default(self, capsys):
        _, out, _ = run(capsys, ["_?f@@YAXH@Z"])
        assert out.strip() == "_?f@@YAXH@Z"

    def test_the_filter_strips_too(self, capsys, monkeypatch):
        _, out, _ = run(capsys, ["-_"], stdin="0000 T __Z1fv\n", monkeypatch=monkeypatch)
        assert out.strip() == "0000 T f()"


class TestKeepHash:
    """`--keep-hash`: rustc-demangle's `{}` rather than its `{:#}`.

    One flag with two manifestations, because that is how the reference has it -- the
    same `alternate` bit suppresses all of this. The score against the reference is
    stated in tests/test_rust.py.
    """

    @pytest.mark.parametrize(
        ("name", "default", "kept"),
        [
            # legacy: the path's trailing `17h<16 hex>` component
            (
                "_ZN4core3fmt5write17h05af221e174051e9E",
                "core::fmt::write",
                "core::fmt::write::h05af221e174051e9",
            ),
            # v0: the crate's disambiguator, wherever a crate root is spelled
            ("_RNvCs1_1a1f", "a::f", "a[3]::f"),
            # and nothing at all where the crate wrote none
            ("_RNvC1a1f", "a::f", "a::f"),
            # v0 again: the same bit spells an integer const's own type after its value
            (
                "_RINvCsdEttCVZFADF_8features12const_signedKln11_EB2_",
                "features::const_signed::<-17>",
                "features[9f05e0465351d495]::const_signed::<-17i32>",
            ),
            # a `bool` const takes no suffix under either, which the reference decides
            (
                "_RINvCsdEttCVZFADF_8features10const_boolKb0_EB2_",
                "features::const_bool::<false>",
                "features[9f05e0465351d495]::const_bool::<false>",
            ),
        ],
    )
    def test_against_rustc_demangle(self, capsys, name, default, kept):
        _, out, _ = run(capsys, [name])
        assert out.strip() == default
        _, out, _ = run(capsys, ["--keep-hash", name])
        assert out.strip() == kept
