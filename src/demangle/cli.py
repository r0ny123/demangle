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
so treating the whole line as one symbol demangled nothing at all:

    $ printf '0000000000001139 T _ZN3foo3barEv\\n' | demangle
    0000000000001139 T _ZN3foo3barEv        # every line, unchanged

Encoding
--------
A symbol table holds bytes, and they are not reliably UTF-8. Both streams are read and
written with `surrogateescape`, so a name this package cannot read comes back byte for
byte -- and so a non-UTF-8 symbol does not end the run with a `UnicodeDecodeError`
traceback, which is what happened wherever the interpreter's error handler was `strict`.
"""

import argparse
import contextlib
import os
import re
import sys

from . import __version__
from ._signature import signature
from .api import demangle, demangle_strict, demangle_type, detect, languages, parse, parse_type, styles
from .core.errors import DemanglingError
from .core.limits import DEFAULT_LIMITS, RELAXED_LIMITS, Limits

#: A candidate symbol in a stream of mixed text. Deliberately wider than any one scheme:
#: offering a word that turns out not to be mangled costs one failed prefix test, and
#: not offering one loses a symbol silently.
#:
#: `?` and `@` are here for MSVC, `$` for Swift and Free Pascal, `.` for clone suffixes
#: and Go package paths, `-`, `+`, `[` and `]` for Objective-C method names, `/` for Go
#: import paths.
_TOKEN = re.compile(r"[A-Za-z0-9_$@?.\-+\[\]/:]+")

#: A word is worth offering only if it holds one of these. Without it every ordinary
#: word in a disassembly listing walks the whole detection chain, and `demumble`'s
#: warning applies -- `I like Pi` should not become `I like int*`.
_TOKEN_MUST_HOLD = re.compile(r"[_$?@\[]")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="demangle",
        description="Demangle C++, Rust, Swift, MSVC and other symbol names.",
        epilog=(
            "With no NAME arguments, names are read from standard input: every "
            "symbol-shaped word is demangled and the text around it is copied through."
        ),
    )
    parser.add_argument("names", nargs="*", metavar="NAME", help="symbol names to demangle")
    parser.add_argument("-l", "--language", help="force a scheme instead of detecting (aliases accepted)")
    parser.add_argument("-s", "--style", default="llvm", help="output style (default: llvm)")
    parser.add_argument("-d", "--detect", action="store_true", help="print the detected scheme, not the expansion")
    parser.add_argument("-t", "--tree", action="store_true", help="print the parse tree")
    parser.add_argument(
        "--types",
        action="store_true",
        help="read each NAME as a bare type encoding, not a symbol (needs --language)",
    )
    parser.add_argument("--strict", action="store_true", help="report failures instead of echoing the input")
    parser.add_argument(
        "-b", "--both", action="store_true", help="print the mangled name and its expansion, as `mangled ==> demangled`"
    )
    parser.add_argument(
        "-m", "--only-demangled", action="store_true", help="print only what demangled, skipping the rest"
    )
    # One name, one part: asking for two of these would print one of them and drop the
    # other, and a flag that is silently ignored is worse than an error.
    parts = parser.add_mutually_exclusive_group()
    parts.add_argument(
        "-p",
        "--no-params",
        action="store_true",
        help="print the name without its parameter list or return type, as `c++filt -p` does",
    )
    parts.add_argument(
        "--base-name", action="store_true", help="print only the last component of the name, without its scope"
    )
    parts.add_argument(
        "--no-return-type", action="store_true", help="print the whole declaration except the return type"
    )
    parser.add_argument("--relaxed", action="store_true", help="raise the resource bounds, for input you trust")
    parser.add_argument("--max-input", type=int, metavar="N", help="characters of input to consider")
    parser.add_argument("--max-output", type=int, metavar="N", help="characters of output to allow")
    parser.add_argument("--max-depth", type=int, metavar="N", help="nesting depth to allow")
    parser.add_argument("--list-languages", action="store_true", help="list supported schemes and exit")
    parser.add_argument("--list-styles", action="store_true", help="list output styles and exit")
    parser.add_argument("--version", action="version", version=f"demangle {__version__}")
    return parser


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
    # Every slot in the hierarchy, not just the most-derived class's: a node whose text
    # lives on a shared base declares none of its own and would print no detail at all.
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


def main(argv=None):
    parser = build_parser()
    arguments = parser.parse_args(argv)

    if arguments.list_languages:
        from .core.registry import available

        for plugin in available():
            aliases = f" (aliases: {', '.join(plugin.aliases)})" if plugin.aliases else ""
            print(f"{plugin.name:10} {plugin.description}{aliases}")
        return 0

    if arguments.list_styles:
        for name in styles():
            print(name)
        return 0

    # Validated through the registry rather than against `languages()`, so the aliases
    # that `--list-languages` advertises are actually accepted.
    if arguments.language:
        from .core.registry import aliases, get

        try:
            get(arguments.language)
        except KeyError:
            known = ", ".join(sorted(set(languages()) | set(aliases())))
            parser.error(f"unknown language {arguments.language!r}; choose from {known}")
    if arguments.types:
        if not arguments.language:
            parser.error("--types needs --language: a type encoding carries no marker to detect on")
        # Checked here rather than per name: a scheme with no type grammar fails on every
        # line, and one message about the run beats one message per symbol.
        from .core.registry import get as _get_plugin

        if _get_plugin(arguments.language).parse_type is None:
            readable = ", ".join(sorted(name for name in languages() if _get_plugin(name).parse_type is not None))
            parser.error(f"{arguments.language} has no type grammar of its own; {readable} do")
        if arguments.detect:
            parser.error("--types and --detect ask different questions; --types already names the scheme")
        if arguments.no_params or arguments.base_name or arguments.no_return_type:
            parser.error("--types reads a type, which has no name, parameters or return type to select")
    if arguments.style not in styles():
        parser.error(f"unknown style {arguments.style!r}; choose from {', '.join(styles())}")
    for flag in ("max_input", "max_output", "max_depth"):
        value = getattr(arguments, flag)
        if value is not None and value < 1:
            parser.error(f"--{flag.replace('_', '-')} must be positive")

    _reconfigure(sys.stdin, errors="surrogateescape")
    _reconfigure(sys.stdout, errors="surrogateescape")

    try:
        if arguments.types:
            return _run_types(arguments.names or sys.stdin, arguments)
        if arguments.names:
            return _run_names(arguments.names, arguments)
        return _run_stream(sys.stdin, arguments)
    except BrokenPipeError:
        # `demangle | head` closes the pipe on us. This has to be caught outside the
        # loop: caught per name it prints one error for every remaining symbol, thousands
        # of them. Point stdout at the null device so the interpreter's shutdown flush
        # does not raise it again, and exit cleanly.
        _silence_stdout()
        return 0


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
        # `dup2` duplicated the descriptor, so this one has done its job either way.
        # Left open it leaked -- which mattered not at all for one exit, and would have
        # mattered for a library caller invoking `main()` in a loop.
        os.close(null)


def _expand(name, arguments, limits):
    """What this run has to say about one name."""
    if arguments.detect:
        return arguments.language or detect(name) or "-"
    if arguments.types:
        if arguments.tree:
            node = parse_type(name, language=arguments.language, style=arguments.style, limits=limits)
            return "\n".join(_dump(node))
        return demangle_type(name, language=arguments.language, style=arguments.style, limits=limits)
    if arguments.tree:
        return "\n".join(_dump(parse(name, language=arguments.language, style=arguments.style, limits=limits)))
    if arguments.no_params or arguments.base_name or arguments.no_return_type:
        return _part_of(name, arguments, limits)
    if arguments.strict:
        return demangle_strict(name, language=arguments.language, style=arguments.style, limits=limits)
    return demangle(name, language=arguments.language, style=arguments.style, limits=limits)


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
    if arguments.base_name:
        return parts.base_name
    if arguments.no_params:
        # What `c++filt -p` prints: the name with its scope, and neither the signature
        # around it nor the qualifiers after it. A `vtable for` still says so.
        lead = f"{parts.special} " if parts.special else ""
        return f"{lead}{parts.qualified_name}{parts.decoration}"
    # `--no-return-type`: everything else, with the return type cut off the front. Cut
    # by what it is rather than at the first space, so a return type with spaces in it
    # goes whole.
    spelling = parts.demangled
    prefix = f"{parts.return_type} " if parts.return_type else ""
    return spelling[len(prefix) :] if prefix and spelling.startswith(prefix) else spelling


def _run_names(names, arguments):
    """One name per argument: the whole argument is the name, whatever it holds.

    No word-splitting here. A caller who typed a name meant that name, and an
    Objective-C method or a Go symbol has spaces and slashes in it.
    """
    limits = _limits_from(arguments)
    status = 0
    out = sys.stdout
    for name in names:
        if not name:
            out.write("\n")
            continue
        try:
            expanded = _expand(name, arguments, limits)
        except BrokenPipeError:
            raise
        except Exception as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            status = 1
            continue
        if arguments.only_demangled and expanded == name:
            continue
        out.write(f"{name} ==> {expanded}\n" if arguments.both else f"{expanded}\n")
    return status


def _run_types(names, arguments):
    """`--types`: one *type* encoding per argument, or per line of standard input.

    Not the filter the default path is, and it cannot be. A type encoding is not
    symbol-shaped -- `Pi`, `H`, `Si` are ordinary words -- so picking them out of mixed
    text would mean turning `I like Pi` into `I like int*`, which is the very thing
    `_TOKEN_MUST_HOLD` exists to prevent. Each input is one encoding, whole.

    An encoding that does not parse comes back unchanged, the bargain `demangle()`
    makes, unless `--strict` asks to hear about it instead.
    """
    limits = _limits_from(arguments)
    status = 0
    out = sys.stdout
    for raw in names:
        # A line off stdin carries its newline, and an argument does not. No type
        # encoding in any of these grammars holds a `\r` either, so a file with CRLF
        # endings reads the same as one without.
        name = raw.rstrip("\r\n")
        if not name:
            out.write("\n")
            continue
        try:
            expanded = _expand(name, arguments, limits)
        except BrokenPipeError:
            raise
        except DemanglingError as exc:
            if arguments.strict:
                print(f"{name}: {exc}", file=sys.stderr)
                status = 1
                continue
            expanded = name
        except Exception as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            status = 1
            continue
        if arguments.only_demangled and expanded == name:
            continue
        out.write(f"{name} ==> {expanded}\n" if arguments.both else f"{expanded}\n")
    return status


def _run_stream(stream, arguments):
    """A filter: substitute every symbol-shaped word, copy everything else through.

    Line by line rather than all at once, so `demangle` in a pipe stays a pipe: someone
    watching `nm ... | demangle` should not have to wait for the input to end.
    """
    limits = _limits_from(arguments)
    status = 0
    out = sys.stdout

    for line in stream:
        pieces = []
        demangled = []
        end = 0
        for match in _TOKEN.finditer(line):
            word = match.group()
            if not _TOKEN_MUST_HOLD.search(word):
                continue
            try:
                expanded = _expand(word, arguments, limits)
            except BrokenPipeError:
                raise
            except Exception as exc:
                print(f"{word}: {exc}", file=sys.stderr)
                status = 1
                continue
            if expanded == word and not arguments.detect:
                continue
            replacement = f"{word} ==> {expanded}" if arguments.both else expanded
            demangled.append(replacement)
            pieces.append(line[end : match.start()])
            pieces.append(replacement)
            end = match.end()
        if arguments.only_demangled:
            for replacement in demangled:
                out.write(replacement + "\n")
            continue
        pieces.append(line[end:])
        out.write("".join(pieces))
    return status


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
