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
        """`--detect` must report the forced language, not the one it would have guessed."""
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


class TestLimitFlags:
    def test_a_tight_output_bound_refuses(self, capsys):
        assert main(["--max-output", "4", "--strict", "_ZNSt6vectorIiSaIiEE9push_backERKi"]) == 1
        assert "output length" in capsys.readouterr().err

    def test_relaxed_reads_what_the_default_refuses(self, capsys):
        deep = "_Z1f" + "P" * 400 + "i"
        assert main(["--strict", deep]) == 1
        capsys.readouterr()
        assert main(["--relaxed", "--strict", deep]) == 0

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

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("??_7Base@@6B@", "Base::`vftable'"),
            ("??_7A@B@@6BC@D@@@", "B::A::`vftable'{for `D::C'}"),
            ("??_GBase@@UEAAPEAXI@Z", "Base::`scalar deleting dtor'"),
            ("?f@C@@WBA@EAAHXZ", "C::f`adjustor{16}'"),
            ("??_EBase@@W3AEPAXI@Z", "Base::`vector deleting dtor'`adjustor{4}'"),
            ("??_R1A@?0A@EA@Base@@8", "Base::`RTTI Base Class Descriptor at (0, -1, 0, 64)'"),
            ("??_R0?AUBase@@@8", "Base `RTTI Type Descriptor'"),
            ("??_R0PAD@8", "char *`RTTI Type Descriptor'"),
            ("??_R0AAH@8", "int &`RTTI Type Descriptor'"),
            ("??_R0PAY01H@8", "int (*`RTTI Type Descriptor')[2]"),
            ("??_R0P6AXXZ@8", "void (__cdecl *`RTTI Type Descriptor')(void)"),
            ("??_H@YAXPEAX_K1P6APEAX0@Z@Z", "`vector ctor iterator'"),
            ("??__EFoo@@YAXXZ", "`dynamic initializer for 'Foo''"),
            ("??__FFoo@@YAXXZ", "`dynamic atexit destructor for 'Foo''"),
            ("??__E?i@C@@0HA@@YAXXZ", "`dynamic initializer for 'C::i''"),
        ],
    )
    def test_no_params_writes_an_msvc_label_where_msvc_does(self, capsys, name, expected):
        """After the name, as the reference spells it, but without the `const` that is
        about the label's table."""
        _, out, _ = run(capsys, ["-p", name])
        assert out.strip() == expected

    def test_no_params_tells_a_classs_vftables_apart(self, capsys):
        """`{for ...}` is which base's table it is, so it stays: three tables, three lines."""
        _, out, _ = run(capsys, ["-p", "??_7A@B@@6BC@D@@@", "??_7A@B@@6BC@D@@E@F@@@", "??_7A@B@@6BC@D@@E@F@@G@H@@@"])
        assert len(set(out.split("\n")) - {""}) == 3

    def test_base_name_of_an_msvc_label_is_the_class_it_is_about(self, capsys):
        _, out, _ = run(capsys, ["--base-name", "??_7Base@@6B@"])
        assert out.strip() == "Base"

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

    def test_it_refuses_a_scheme_with_no_type_grammar_once_rather_than_per_name(self, capsys):
        with pytest.raises(SystemExit):
            run(capsys, ["--types", "-l", "rust", "Pi", "Pc", "i"])

    def test_crlf_input_reads_the_same_as_lf(self, capsys, monkeypatch):
        _, out, _ = run(capsys, ["--types", "-l", "msvc"], stdin="PEAX\r\n", monkeypatch=monkeypatch)
        assert out == "void *\n"


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
