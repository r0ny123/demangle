"""The `demangle` command.

Reads names from arguments or standard input and writes their expansions, so it drops
into a pipeline the way `c++filt` does:

    nm -a libfoo.so | demangle
    demangle _ZNSt6vectorIiSaIiEE9push_backERKi

Names that cannot be read pass through unchanged, which is what makes it safe to run
over a whole symbol table.

On reading a stream
-------------------
With no arguments this is a *filter*, not a line reader: every symbol-shaped word in the
input is a candidate and everything around it is copied through untouched. That is what
`c++filt`, `demumble` and `rustfilt` all do, and it is the only behaviour under which
the first example above works. `nm` writes an address and a type letter before the name,
so treating the whole line as one symbol would demangle nothing at all:

    $ printf '0000000000001139 T _ZN3foo3barEv\\n' | demangle
    0000000000001139 T _ZN3foo3barEv        # every line, unchanged

Encoding
--------
A symbol table holds bytes, and they are not reliably UTF-8. Both streams are read and
written with `surrogateescape`, so a name this package cannot read comes back byte for
byte -- and so a non-UTF-8 symbol does not end the run with a `UnicodeDecodeError`
traceback, which is what the `strict` error handler does.
"""

import argparse
import base64
import codecs
import contextlib
import dataclasses
import difflib
import errno
import functools
import io
import itertools
import json
import os
import signal
import sys
import textwrap

from . import __version__
from ._signature import Signature, signature
from .api import _read, demangle, demangle_strict, demangle_type, detect, languages, parse, parse_type, style, styles
from .core.ast import Function
from .core.errors import DemanglingError, LimitExceeded
from .core.limits import DEFAULT_LIMITS, RELAXED_LIMITS, Limits
from .core.style import get_style
from .filter import TOKEN, TOKEN_MUST_HOLD

_TOKEN = TOKEN
_TOKEN_MUST_HOLD = TOKEN_MUST_HOLD


_DESCRIPTION = """\
Demangle C++, Rust, Swift, MSVC and other symbol names.

examples:
  nm -a libfoo.so | demangle                each symbol in a listing, in place
  demangle _ZN3foo3barEv '?f@@YAXH@Z'       the names given
  demangle --json-lines --signature _Z1fPi  a JSON object per name

A name that cannot be read is printed unchanged. With no NAME, or where a NAME is -, \
standard input is read, and answered as it arrives: every symbol-shaped word in it is \
demangled and the text around it copied through. Under -0 each NUL-ended record is one \
name instead, and under --types each line one type. A terminal is read only for -."""

_EPILOG = """\
exit status:
  0    every name was read, or printed unchanged
  1    a name was refused: under --strict, or with no tree to print for --tree or --json
  2    the command line was wrong
  130  interrupted

documentation: https://r0ny123.github.io/demangle/
issues: https://github.com/r0ny123/demangle/issues"""


class _Formatter(argparse.HelpFormatter):
    """Wraps a paragraph of prose to the terminal, and leaves one written as several
    lines -- the examples, the exit statuses -- as it is written.

    Never at a hyphen: `--no-leading-underscores` is one flag, and split across two lines
    it is two words that are neither.
    """

    def __init__(self, prog):
        super().__init__(prog, max_help_position=32)

    def _fill_text(self, text, width, indent):
        return "\n\n".join(
            "\n".join(indent + line for line in paragraph.splitlines())
            if "\n" in paragraph
            else textwrap.fill(
                " ".join(paragraph.split()),
                width,
                initial_indent=indent,
                subsequent_indent=indent,
                break_on_hyphens=False,
            )
            for paragraph in text.split("\n\n")
        )

    def _split_lines(self, text, width):
        return textwrap.wrap(" ".join(text.split()), width, break_on_hyphens=False)


class _Parser(argparse.ArgumentParser):
    """`-h` is the short help, the options most runs use; `--help` is all of it."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.common = []

    def format_short_help(self):
        formatter = self._get_formatter()
        formatter.add_usage(self.usage, self._actions, self._mutually_exclusive_groups)
        formatter.add_text(self.description)
        formatter.start_section("common options")
        formatter.add_arguments(self.common)
        formatter.end_section()
        formatter.add_text("demangle --help lists every option, and the exit statuses.")
        return formatter.format_help()


class _ShortHelp(argparse.Action):
    def __init__(self, option_strings, dest, help=None):
        super().__init__(option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        assert isinstance(parser, _Parser)
        print(parser.format_short_help(), end="")
        parser.exit()


def build_parser():
    parser = _Parser(
        prog="demangle",
        usage="%(prog)s [options] [NAME ...]",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=_Formatter,
        add_help=False,
    )
    common = parser.common

    reading = parser.add_argument_group("reading names")
    common.append(
        reading.add_argument(
            "names", nargs="*", metavar="NAME", help="symbol names to demangle; - reads standard input"
        )
    )
    common.append(
        reading.add_argument(
            "-l",
            "--language",
            metavar="LANGUAGE[,...]",
            help="force one scheme instead of detecting, or detect among a comma-separated list (aliases accepted)",
        )
    )
    common.append(
        reading.add_argument(
            "--strict",
            action="store_true",
            help="report failures instead of echoing the input; with --detect, name the scheme that reads a name",
        )
    )
    reading.add_argument(
        "-_",
        "--strip-underscore",
        action="store_true",
        help="ignore one leading underscore, as `c++filt --strip-underscore` does",
    )
    reading.add_argument(
        "-0",
        "--null",
        action="store_true",
        help="read standard input as whole names each ended by a NUL, as `find -print0` writes "
        "them, and end each answer with a NUL",
    )
    reading.add_argument(
        "--types",
        action="store_true",
        help="read each NAME as a bare type encoding, not a symbol (needs --language)",
    )

    printing = parser.add_argument_group("what to print")
    common.append(
        printing.add_argument(
            "-d", "--detect", action="store_true", help="print the detected scheme, not the expansion"
        )
    )
    common.append(
        printing.add_argument(
            "-b",
            "--both",
            action="store_true",
            help="print the mangled name and its expansion, as `mangled ==> demangled`",
        )
    )
    common.append(
        printing.add_argument(
            "-m", "--only-demangled", action="store_true", help="print only what demangled, skipping the rest"
        )
    )
    printing.add_argument("-t", "--tree", action="store_true", help="print the parse tree")
    printing.add_argument("--json", action="store_true", help="print the parse tree as JSON")
    common.append(
        printing.add_argument(
            "--json-lines",
            action="store_true",
            help="print one JSON object per name: mangled, demangled, language; and mangled_bytes, the "
            "name's bytes in base64, where they are not UTF-8",
        )
    )
    printing.add_argument(
        "--signature",
        action="store_true",
        help="with --json-lines, add the parts of the name: base_name, parameters, return_type...",
    )

    # One name, one part: a silently ignored flag is worse than an error.
    parts = parser.add_argument_group("printing one part of a name").add_mutually_exclusive_group()
    common.append(
        parts.add_argument(
            "-p",
            "--no-params",
            action="store_true",
            help="print the name without its parameter list or return type, as `c++filt -p` does",
        )
    )
    parts.add_argument(
        "--base-name", action="store_true", help="print only the last component of the name, without its scope"
    )
    parts.add_argument(
        "--no-return-type", action="store_true", help="print the whole declaration except the return type"
    )
    parts.add_argument(
        "--ret-postfix",
        action="store_true",
        help="print the return type after the parameter list, as libiberty's `DMGL_RET_POSTFIX` does",
    )

    spelling = parser.add_argument_group("spelling")
    spelling.add_argument("-s", "--style", default="llvm", help="output style (default: llvm)")
    spelling.add_argument(
        "--simplified",
        action="store_true",
        help="Swift names the way Xcode shows them, as `swift-demangle --simplified`",
    )
    spelling.add_argument(
        "--keep-hash",
        action="store_true",
        help="spell the hash Rust writes to keep a symbol unique, as rustc-demangle's `{}` does",
    )

    less = parser.add_argument_group(
        "printing less of an MSVC name",
        "MSVC decorated names expand to a great deal more than the name. "
        "`--no-calling-convention`, `--no-access-specifier`, `--no-member-type` and "
        "`--no-variable-type` are `llvm-undname`'s flags and mean the same, and apply to "
        "the declaration. `--no-ms-keywords`, `--no-leading-underscores`, "
        "`--no-this-type` and `--no-tag-kind` are `UnDecorateSymbolName` mask bits it has "
        "no flag for, and reach every occurrence of what they name -- inside a template "
        "argument, and inside the symbol a local name is scoped by. `--no-return-type`, "
        "above, applies to every scheme.",
    )
    less.add_argument("--no-calling-convention", action="store_true", help="omit `__cdecl` and its siblings")
    less.add_argument("--no-access-specifier", action="store_true", help="omit `public: `, `private: `")
    less.add_argument("--no-member-type", action="store_true", help="omit `static ` and `virtual `")
    less.add_argument("--no-variable-type", action="store_true", help="print a data symbol as its name alone")
    less.add_argument("--no-ms-keywords", action="store_true", help="omit every Microsoft keyword, wherever it stands")
    less.add_argument(
        "--no-leading-underscores", action="store_true", help="spell those keywords as `cdecl`, `restrict`"
    )
    less.add_argument("--no-this-type", action="store_true", help="omit what a member function writes after `()`")
    less.add_argument("--no-tag-kind", action="store_true", help="omit `class`, `struct`, `union`, `enum`")

    bounds = parser.add_argument_group("resource bounds")
    bounds.add_argument("--relaxed", action="store_true", help="raise the resource bounds, for input you trust")
    bounds.add_argument("--max-input", type=int, metavar="N", help="characters of input to consider")
    bounds.add_argument("--max-output", type=int, metavar="N", help="characters of output to allow")
    bounds.add_argument("--max-depth", type=int, metavar="N", help="nesting depth to allow")

    information = parser.add_argument_group("information")
    common.append(information.add_argument("-h", action=_ShortHelp, help="show the common options and exit"))
    common.append(information.add_argument("--help", action="help", help="show every option and exit"))
    information.add_argument(
        "--version", action="version", version=f"demangle {__version__}", help="show the version and exit"
    )
    information.add_argument("--list-languages", action="store_true", help="list supported schemes and exit")
    information.add_argument("--list-styles", action="store_true", help="list output styles and exit")
    return parser


#: `--flag` to the MSVC option it turns off. `--no-return-type` and `--ret-postfix` need
#: an option for MSVC because its return type wraps *around* the declarator
#: (`int (__cdecl * __cdecl fn(void))(int)`); Itanium uses the tree instead.
_MSVC_SUPPRESSIONS = {
    "no_calling_convention": "calling_convention",
    "no_access_specifier": "access_specifier",
    "no_member_type": "member_type",
    "no_variable_type": "variable_type",
    "no_return_type": "return_type",
    "ret_postfix": "return_type",
    "no_ms_keywords": "ms_keywords",
    "no_leading_underscores": "leading_underscores",
    "no_this_type": "this_type",
    "no_tag_kind": "tag_kind",
}


def _style_from(arguments):
    """The style this run spells with: the named one, plus whatever it is to leave out."""
    changes = {}
    off = {option: False for flag, option in _MSVC_SUPPRESSIONS.items() if getattr(arguments, flag)}
    if off:
        changes["msvc"] = off
    if arguments.simplified:
        from .schemes.swift.options import SIMPLIFIED_OPTIONS

        changes["swift"] = SIMPLIFIED_OPTIONS
    if arguments.keep_hash:
        changes["rust"] = {"keep_hash": True}
    if not changes:
        return arguments.style
    return style(arguments.style, **changes)


def _limits_from(arguments):
    """The bounds this run should use.

    `--relaxed` moves the floor; the three explicit flags override whatever is under
    them, so `--relaxed --max-output 4096` means what it reads as.
    """
    base = RELAXED_LIMITS if arguments.relaxed else DEFAULT_LIMITS
    if arguments.max_input is arguments.max_output is arguments.max_depth is None:
        return base
    return Limits(
        max_depth=base.max_depth if arguments.max_depth is None else arguments.max_depth,
        max_output=base.max_output if arguments.max_output is None else arguments.max_output,
        max_substitutions=base.max_substitutions,
        max_input=base.max_input if arguments.max_input is None else arguments.max_input,
    )


def _dump(node, indent=0):
    """Render a parse tree, one node per line."""
    pad = "  " * indent
    detail = ""
    # Every slot in the hierarchy: a node's text may live on a shared base.
    for slot in node._fields() if hasattr(node, "_fields") else getattr(node, "__slots__", ()):
        value = getattr(node, slot, None)
        if isinstance(value, str) and value:
            detail = f" {value!r}"
            break
    yield f"{pad}{node.kind}{detail}"
    for child in node.children():
        yield from _dump(child, indent + 1)


def _reconfigure(stream, **kwargs):
    """Set an error handler on a stream, where the stream has one to set.

    Under `pytest`'s capture, and behind some redirections, the standard streams are
    substitutes with no `reconfigure`. Nothing here is load-bearing for correctness --
    it decides how undecodable bytes are handled -- so a stream that cannot be
    reconfigured is left alone.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        with contextlib.suppress(ValueError, OSError):
            reconfigure(**kwargs)


def _parse(parser, argv):
    """The command line, flags and names in any order, and nothing after `--` a flag.

    `--` is cut off here rather than left to `parse_intermixed_args`, which on 3.11 and
    3.12 still reads `-p` after it as a flag.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    rest = []
    if "--" in argv:
        cut = argv.index("--")
        argv, rest = argv[:cut], argv[cut + 1 :]
    arguments = parser.parse_intermixed_args(argv)
    arguments.names += rest
    return arguments


def main(argv=None):
    try:
        return _main(argv)
    except KeyboardInterrupt:
        return _interrupted()


def _interrupted():
    """Ctrl-C, ended the way a shell can see it was.

    bash stops a `for` loop over `demangle` only when the command dies of SIGINT; a
    status of 130 looks to it like any other failure, and the loop goes on. So where
    there is a SIGINT to die of, what was written is flushed and the command dies of it.
    Windows has none, and there 130 is the answer.
    """
    if sys.platform != "win32":
        with contextlib.suppress(OSError, ValueError, AttributeError):
            sys.stdout.flush()
        try:
            signal.signal(signal.SIGINT, signal.SIG_DFL)
        except ValueError:  # not the main thread, which alone may set a handler
            return 130
        os.kill(os.getpid(), signal.SIGINT)
    # 128 + SIGINT, what a shell reports for a command Ctrl-C stopped.
    return 130


def _main(argv):
    parser = build_parser()
    arguments = _parse(parser, argv)

    if arguments.list_languages:
        from .core.registry import available

        plugins = available()
        width = max((len(plugin.name) for plugin in plugins), default=0)
        for plugin in plugins:
            aliases = f" (aliases: {', '.join(plugin.aliases)})" if plugin.aliases else ""
            print(f"{plugin.name:{width}} {plugin.description}{aliases}")
        return 0

    if arguments.list_styles:
        for name in styles():
            print(name)
        return 0

    if arguments.language:
        arguments.language = _language_from(parser, arguments.language)
    if arguments.json and arguments.tree:
        parser.error("--tree and --json are two spellings of the same answer; choose one")
    if arguments.signature and not arguments.json_lines:
        parser.error("--signature adds fields to --json-lines records; add --json-lines")
    if arguments.json_lines:
        for flag in ("tree", "json", "detect", "both", "types"):
            if getattr(arguments, flag):
                parser.error(f"--json-lines and --{flag} are different answers; choose one")
    if arguments.types:
        if not arguments.language:
            parser.error("--types needs --language: a type encoding carries no marker to detect on")
        if not isinstance(arguments.language, str):
            parser.error("--types reads with one scheme, so --language names one, not a list")
        # Checked once: a scheme with no type grammar fails on every line.
        from .core.registry import get as _get_plugin

        if _get_plugin(arguments.language).parse_type is None:
            readable = ", ".join(sorted(name for name in languages() if _get_plugin(name).parse_type is not None))
            parser.error(f"{arguments.language} has no type grammar of its own; {readable} do")
        if arguments.detect:
            parser.error("--types and --detect ask different questions; --types already names the scheme")
        if arguments.no_params or arguments.base_name or arguments.no_return_type or arguments.ret_postfix:
            parser.error("--types reads a type, which has no name, parameters or return type to select")
    if arguments.style not in styles():
        parser.error(f"unknown style {arguments.style!r}; choose from {', '.join(styles())}")
    for flag in ("max_input", "max_output", "max_depth"):
        value = getattr(arguments, flag)
        if value is not None and value < 1:
            parser.error(f"--{flag.replace('_', '-')} must be positive")

    arguments.style = _style_from(arguments)

    names = arguments.names or ["-"]
    if "-" in names:
        problem = _stdin_problem(named=bool(arguments.names))
        if problem:
            parser.error(problem)

    _reconfigure(sys.stdout, errors="surrogateescape")

    try:
        status = _run(names, arguments)
        # Inside the `try`: output short enough to sit in the buffer meets a closed pipe here.
        sys.stdout.flush()
        return status
    except OSError as exc:
        # `demangle | head` closes the pipe. Caught outside the loop (one error, not one per
        # name); stdout goes to the null device so the shutdown flush does not raise again.
        if not _closed_pipe(exc):
            raise
        _silence_stdout()
        return 0


#: The keys that end what is typed at a terminal.
_END_OF_INPUT = "Ctrl-Z then Enter" if sys.platform == "win32" else "Ctrl-D"


def _stdin_problem(named):
    """Why standard input cannot be read for names, or None.

    A terminal is refused only when nothing asked for it: `demangle` alone, typed at a
    prompt, would otherwise sit waiting for input its user does not know it wants. `-`
    asks, and then names are read as they are typed.
    """
    if sys.stdin is None:
        return "standard input is closed; give the names as arguments"
    if named:
        return None
    try:
        terminal = sys.stdin.isatty()
    except ValueError:  # closed
        terminal = False
    if terminal:
        return (
            "no NAME given, and standard input is a terminal; give names as arguments, "
            f"pipe them in (nm -a libfoo.so | demangle), or give - to type them, ending with {_END_OF_INPUT}"
        )
    return None


def _language_from(parser, text):
    """`--language` as the API takes it: one name, which forces that scheme, or a list.

    `itanium,swift` is the allow-list `("itanium", "swift")`, which detects among those
    schemes alone. A trailing comma makes a list of one -- `gnuv2,` detects where `gnuv2`
    forces -- as it makes a tuple of one in Python. Checked through the registry, so the
    aliases `--list-languages` advertises are accepted, and each comes back as the name
    its scheme is registered under: `-d -l c++` says `itanium`, as `-d --strict` does.
    """
    from .core.registry import aliases, canonical

    names = [name.strip() for name in text.split(",")]
    listed = len(names) > 1
    if listed and not names[-1]:
        names.pop()
    for index, name in enumerate(names):
        if not name:
            parser.error(f"--language {text!r} has an empty name in it; separate names with one comma")
        try:
            names[index] = canonical(name)
        except KeyError:
            close = difflib.get_close_matches(name.lower(), sorted({*languages(), *aliases()}), n=1)
            guess = f"did you mean {close[0]!r}? " if close else ""
            parser.error(f"unknown language {name!r}; {guess}demangle --list-languages lists them")
    return tuple(names) if listed else names[0]


def _closed_pipe(exc):
    """Whether `exc` is a write to a pipe its reader has closed.

    Windows reports some of those as `EINVAL` rather than `EPIPE`.
    """
    if isinstance(exc, BrokenPipeError):
        return True
    return sys.platform == "win32" and exc.errno == errno.EINVAL


def _silence_stdout():
    """Point stdout at the null device, so the shutdown flush has somewhere to go."""
    try:
        null = os.open(os.devnull, os.O_WRONLY)
    except OSError:  # pragma: no cover - no /dev/null
        return
    try:
        os.dup2(null, sys.stdout.fileno())
    except (OSError, ValueError, AttributeError):  # pragma: no cover - not a real stream
        pass
    finally:
        # `dup2` duplicated the descriptor; this one would leak for a caller of `main()`.
        os.close(null)


def _expand(name, arguments, limits):
    """What this run has to say about one name, after any leading underscore is dealt with.

    `--strip-underscore` is for the targets whose assembler prepends one, where the
    symbol in the table is `__Z1fv` and the name the compiler mangled is `_Z1fv`. Both
    references drop exactly one and, where what is left does not read, print the name
    they were *given* rather than the stripped form -- so a table full of `_foo` comes
    back untouched instead of a character short.
    """
    if arguments.strip_underscore and name.startswith("_"):
        answer = _expand_read(name[1:], arguments, limits)
        return name if answer == name[1:] else answer
    return _expand_read(name, arguments, limits)


def _expand_read(name, arguments, limits):
    """What this run has to say about one name."""
    if arguments.detect:
        if arguments.strict:
            return _scheme_of(name, arguments, limits)
        if isinstance(arguments.language, str):
            return arguments.language
        return detect(name, language=arguments.language) or "-"
    if arguments.types:
        try:
            if arguments.json or arguments.tree:
                node = parse_type(name, language=arguments.language, style=arguments.style, limits=limits)
                return json.dumps(node.to_dict()) if arguments.json else "\n".join(_dump(node))
            return demangle_type(name, language=arguments.language, style=arguments.style, limits=limits)
        except DemanglingError:
            # The bargain `demangle()` makes, which `demangle_type()` leaves to its caller.
            if arguments.strict:
                raise
            return name
    if arguments.json:
        return json.dumps(parse(name, language=arguments.language, style=arguments.style, limits=limits).to_dict())
    if arguments.tree:
        return "\n".join(_dump(parse(name, language=arguments.language, style=arguments.style, limits=limits)))
    if _selects_a_part(arguments):
        return _part_of(name, arguments, limits)
    if arguments.strict:
        return demangle_strict(name, language=arguments.language, style=arguments.style, limits=limits)
    return demangle(name, language=arguments.language, style=arguments.style, limits=limits)


def _scheme_of(name, arguments, limits):
    """The scheme that reads `name`, raising what `demangle_strict()` raises if none does.

    The question `detect(name, strict=True)` answers, asked under this run's style and
    limits rather than the defaults, so that `--relaxed --detect --strict` names the
    scheme `--relaxed` reads a deep name with.
    """
    resolved = get_style(arguments.style)
    return _read(name, resolved.spelling_builder, arguments.language, resolved, limits)[0].name


#: What `--signature` adds to a record, in `Signature`'s order: every field but the two a
#: record carries already.
_SIGNATURE_FIELDS = tuple(
    field.name for field in dataclasses.fields(Signature) if field.name not in ("language", "demangled")
)


def _record(name, arguments, limits):
    """`--json-lines`: what this run has to say about one name, as data.

    `demangled` is what the line would have said, `-p` and the rest applied; `language`
    is the scheme that read the name, or None where it comes back unchanged. Every
    record has the same keys, so a name nothing reads has its `--signature` fields None
    rather than missing.

    A name whose bytes are not UTF-8 also has `mangled_bytes`, those bytes in base64.
    `mangled` carries each such byte as a lone surrogate, which `json` writes as an
    escape and most readers then replace with U+FFFD, so it alone cannot give the name
    back.
    """
    stripped = arguments.strip_underscore and name.startswith("_")
    read = name[1:] if stripped else name
    parts = None
    # One read of the name for all three answers: the scheme, the spelling, the parts.
    try:
        if arguments.signature or _selects_a_part(arguments):
            parts = signature(read, language=arguments.language, style=arguments.style, limits=limits)
            scheme = parts.language
            demangled = _part_from(read, parts, arguments, limits) if _selects_a_part(arguments) else parts.demangled
        else:
            resolved = get_style(arguments.style)
            builder = resolved.spelling_builder
            plugin, handle = _read(read, builder, arguments.language, resolved, limits)
            scheme, demangled = plugin.name, builder.spell(handle)
    except DemanglingError:
        if arguments.strict:
            raise
        scheme = demangled = None
    if demangled is None or (stripped and demangled == read):
        # As `_expand` answers: what the stripped name does not change, the name as given.
        demangled = name
    record = {"mangled": name}
    raw = _undecodable(name)
    if raw is not None:
        record["mangled_bytes"] = base64.b64encode(raw).decode("ascii")
    record["demangled"] = demangled
    record["language"] = scheme
    if arguments.signature:
        if parts is not None:
            record.update((field, getattr(parts, field)) for field in _SIGNATURE_FIELDS)
        else:
            record.update(dict.fromkeys(_SIGNATURE_FIELDS))
    return record


def _selects_a_part(arguments):
    return arguments.no_params or arguments.base_name or arguments.no_return_type or arguments.ret_postfix


def _undecodable(name):
    """The bytes `name` was read from, where some were not UTF-8; otherwise None."""
    if name.isascii():
        return None
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        with contextlib.suppress(UnicodeEncodeError):
            return name.encode("utf-8", "surrogateescape")
    return None


def _part_of(name, arguments, limits):
    """One piece of a name rather than the whole spelling.

    A name that cannot be read has no pieces, so without `--strict` it comes back
    unchanged -- the same bargain `demangle` makes, because a filter over a symbol table
    meets far more names that are not mangled than names that are.
    """
    try:
        parts = signature(name, language=arguments.language, style=arguments.style, limits=limits)
    except Exception:
        if arguments.strict:
            raise
        return name
    return _part_from(name, parts, arguments, limits)


def _part_from(name, parts, arguments, limits):
    """The piece of `name` this run asks for, from the parts already read off it."""
    if arguments.base_name:
        return parts.base_name
    if arguments.no_params:
        # What `c++filt -p` prints: the scoped name without signature or qualifiers.
        lead = f"{parts.special} " if parts.special else ""
        return f"{lead}{parts.qualified_name}{parts.decoration}"
    without = _without_return_type(name, arguments, limits, parts)
    if arguments.ret_postfix and parts.return_type:
        # No space, as `cplus_demangle_v3(name, DMGL_PARAMS | DMGL_RET_POSTFIX)` prints.
        return f"{without}{parts.return_type}"
    return without


def _without_return_type(name, arguments, limits, parts):
    """The whole declaration with the return type taken out of it.

    Cutting it off the front of the spelling is right only where it *is* a prefix. A
    return type that wraps the declarator has none to cut -- `int (*g<int>(int))(int)`,
    a function returning a pointer to a function -- and a cut there would leave the
    return type in place and report success, which is the silently ignored flag this
    file warns about a few lines up. MSVC answers it with a scheme option; an Itanium
    `function` node answers it directly, by spelling the same node with nothing where
    the return type was. `libiberty`'s own `DMGL_RET_DROP` agrees.
    """
    if parts.return_type:
        try:
            tree = parse(name, language=arguments.language, style=arguments.style, limits=limits)
        except Exception:  # the cut below is the answer when there is no tree
            tree = None
        if isinstance(tree, Function) and tree.returns is not None:
            bare = Function(returns=None, parameters=tree.parameters, suffix=tree.suffix, name=tree.name)
            return bare.spell(style=arguments.style)
    # Cut by what the return type is, not at the first space.
    spelling = parts.demangled
    prefix = f"{parts.return_type} " if parts.return_type else ""
    return spelling[len(prefix) :] if prefix and spelling.startswith(prefix) else spelling


def _run(names, arguments):
    """Each NAME in turn, and standard input wherever one is `-`."""
    answer = _answerer(arguments, _limits_from(arguments))
    status = 0
    for from_stdin, group in itertools.groupby(names, key=lambda name: name == "-"):
        if not from_stdin:
            # An argument too: `$(cut -f1 types.txt)` from a CRLF file still ends in `\r`.
            group = [name.rstrip("\r\n") for name in group] if arguments.types else list(group)
            ran = _run_names([group], arguments, answer)
        elif arguments.null:
            records = _batches(sys.stdin, "\0")
            ran = _run_names(([record.removesuffix("\0") for record in batch] for batch in records), arguments, answer)
        elif arguments.types:
            # No grammar here holds a `\r`, so CRLF input reads the same.
            lines = _batches(sys.stdin, "\n")
            ran = _run_names(([line.rstrip("\r\n") for line in batch] for batch in lines), arguments, answer)
        else:
            ran = _run_stream(_batches(sys.stdin, "\n"), arguments, answer)
        status = max(status, ran)
    return status


#: How many `--json-lines` records a run remembers, so that a name a listing repeats --
#: every call to one function in a disassembly -- is read once. At a few hundred bytes a
#: record, a few megabytes.
_REMEMBERED = 1 << 14


def _answerer(arguments, limits):
    """What this run says about a name: its expansion, or under `--json-lines` the scheme
    that read it and its record, as JSON."""
    if not arguments.json_lines:
        return functools.partial(_expand, arguments=arguments, limits=limits)

    @functools.lru_cache(maxsize=_REMEMBERED)
    def record(name):
        fields = _record(name, arguments, limits)
        return fields["language"], json.dumps(fields)

    return record


#: The most one read takes from standard input. Output is flushed once the read's lines
#: are written, so a pipe that trickles (`tail -f`) is answered a line at a time and one
#: that floods (`nm`) pays for a flush per 64K rather than one per line.
_READ_SIZE = 1 << 16


def _batches(stream, separator):
    """`stream` cut after each `separator`, in batches of what one read brought.

    Each piece keeps its separator, and a last piece the input ended without one keeps
    none, so a filter copies the input through exactly.
    """
    open_piece = []
    for text in _chunks(stream, lines=separator == "\n"):
        *ended, rest = text.split(separator)
        if ended:
            ended[0] = "".join(open_piece) + ended[0]
            open_piece = []
            yield [piece + separator for piece in ended]
        if rest:
            open_piece.append(rest)
    if open_piece:
        yield ["".join(open_piece)]


def _chunks(stream, lines):
    """The text of `stream`, as it arrives.

    Read from the bytes beneath the text stream with `read1`, which returns what is
    there rather than waiting to fill a buffer: a line typed at a terminal, or written
    by a slow producer, is answered before the next one comes. Undecodable bytes are
    carried as lone surrogates and written back as the same bytes.

    On Windows a line's `\\r\\n` is read as `\\n`, as the text stream would have read it,
    because standard output writes every `\\n` as `\\r\\n`: kept, it would come out
    `\\r\\r\\n`.
    """
    binary = getattr(stream, "buffer", None)
    if not hasattr(binary, "read1"):
        # A text stream with no bytes beneath it, such as an `io.StringIO`.
        yield from iter(functools.partial(stream.read, _READ_SIZE), "")
        return
    decoder = codecs.getincrementaldecoder(stream.encoding or "utf-8")("surrogateescape")
    if lines and sys.platform == "win32":
        decoder = io.IncrementalNewlineDecoder(decoder, translate=True)
    for chunk in iter(functools.partial(binary.read1, _READ_SIZE), b""):
        yield decoder.decode(chunk)
    yield decoder.decode(b"", final=True)


#: The flag that moves each bound, for the message that reports hitting it.
_BOUND_FLAGS = {"input length": "--max-input", "output length": "--max-output", "recursion depth": "--max-depth"}


def _complaint(name, exc):
    """The line on standard error for a name this run could not answer.

    A bound that was hit says how to move it: the name may be a real one, longer or
    deeper than the defaults allow for input nobody vouched for.
    """
    if isinstance(exc, LimitExceeded):
        flag = _BOUND_FLAGS.get(exc.limit_name)
        remedy = f"{flag} N or --relaxed" if flag else "--relaxed"
        return f"{name}: {exc}; {remedy} raises it, for input you trust"
    return f"{name}: {exc}"


def _run_names(batches, arguments, answer):
    """One whole name per item: an argument, a line of standard input under `--types`,
    or a record of it under `--null`.

    No word-splitting here. A caller who typed a name meant that name, and an
    Objective-C method or a Go symbol has spaces and slashes in it. Under `--types` it
    could not be otherwise: a type encoding is not symbol-shaped -- `Pi`, `H`, `Si` are
    ordinary words -- so picking them out of mixed text would turn `I like Pi` into
    `I like int*`, which is the very thing `_TOKEN_MUST_HOLD` exists to prevent.
    """
    status = 0
    out = sys.stdout
    end = "\0" if arguments.null else "\n"
    for batch in batches:
        for name in batch:
            if not name and not arguments.json_lines:
                out.write(end)
                continue
            try:
                expanded = answer(name)
            except BrokenPipeError:
                raise
            except Exception as exc:
                print(_complaint(name, exc), file=sys.stderr)
                status = 1
                continue
            if arguments.json_lines:
                language, text = expanded
                if not (arguments.only_demangled and language is None):
                    out.write(text + end)
                continue
            if arguments.only_demangled and expanded == name:
                continue
            out.write(f"{name} ==> {expanded}{end}" if arguments.both else f"{expanded}{end}")
        out.flush()
    return status


def _run_stream(batches, arguments, answer):
    """A filter: substitute every symbol-shaped word, copy everything else through.

    Line by line rather than all at once, so `demangle` in a pipe stays a pipe: someone
    watching `nm ... | demangle` should not have to wait for the input to end.
    """
    status = 0
    out = sys.stdout

    for batch in batches:
        for line in batch:
            pieces = []
            demangled = []
            end = 0
            for match in _TOKEN.finditer(line):
                word = match.group()
                if not _TOKEN_MUST_HOLD.search(word):
                    continue
                try:
                    expanded = answer(word)
                except BrokenPipeError:
                    raise
                except Exception as exc:
                    print(_complaint(word, exc), file=sys.stderr)
                    status = 1
                    continue
                if arguments.json_lines:
                    # A record for each word the filter would rewrite, and none for the text.
                    language, text = expanded
                    if language is not None:
                        demangled.append(text)
                    continue
                if expanded == word and not arguments.detect:
                    continue
                replacement = f"{word} ==> {expanded}" if arguments.both else expanded
                demangled.append(replacement)
                pieces.append(line[end : match.start()])
                pieces.append(replacement)
                end = match.end()
            if arguments.only_demangled or arguments.json_lines:
                for replacement in demangled:
                    out.write(replacement + "\n")
                continue
            pieces.append(line[end:])
            out.write("".join(pieces))
        out.flush()
    return status


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
