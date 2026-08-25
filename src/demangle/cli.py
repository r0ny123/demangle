"""The `demangle` command.

Reads names from arguments or standard input and writes their expansions, one per line,
so it drops into a pipeline the way `c++filt` does:

    nm -a libfoo.so | demangle
    demangle _ZNSt6vectorIiSaIiEE9push_backERKi

Names that cannot be read pass through unchanged, which is what makes it safe to run
over a whole symbol table.
"""

import argparse
import os
import sys

from . import __version__
from .api import demangle, demangle_strict, detect, languages, parse, styles


def build_parser():
    parser = argparse.ArgumentParser(
        prog="demangle",
        description="Demangle C++, Rust and MSVC symbol names.",
        epilog="With no NAME arguments, names are read from standard input, one per line.",
    )
    parser.add_argument("names", nargs="*", metavar="NAME", help="symbol names to demangle")
    parser.add_argument("-l", "--language", help="force a scheme instead of detecting (aliases accepted)")
    parser.add_argument("-s", "--style", default="llvm", help="output style (default: llvm)")
    parser.add_argument("-d", "--detect", action="store_true", help="print the detected scheme, not the expansion")
    parser.add_argument("-t", "--tree", action="store_true", help="print the parse tree")
    parser.add_argument("--strict", action="store_true", help="report failures instead of echoing the input")
    parser.add_argument("--list-languages", action="store_true", help="list supported schemes and exit")
    parser.add_argument("--list-styles", action="store_true", help="list output styles and exit")
    parser.add_argument("--version", action="version", version=f"demangle {__version__}")
    return parser


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
    if arguments.style not in styles():
        parser.error(f"unknown style {arguments.style!r}; choose from {', '.join(styles())}")

    names = arguments.names or (line.rstrip("\n") for line in sys.stdin)

    status = 0
    try:
        status = _run(names, arguments)
    except BrokenPipeError:
        # `demangle | head` closes the pipe on us. Catching this per name -- which the
        # loop's own `except Exception` used to do -- printed one error per remaining
        # symbol, thousands of them. Redirect stdout to devnull so the interpreter's
        # shutdown flush does not raise it again, and exit cleanly.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    return status


def _run(names, arguments):
    status = 0
    for name in names:
        if not name:
            print()
            continue
        if arguments.detect:
            print(arguments.language or detect(name) or "-")
            continue
        try:
            if arguments.tree:
                tree = parse(name, language=arguments.language, style=arguments.style)
                print("\n".join(_dump(tree)))
                continue
            if arguments.strict:
                print(demangle_strict(name, language=arguments.language, style=arguments.style))
                continue
            print(demangle(name, language=arguments.language, style=arguments.style))
        except BrokenPipeError:
            raise
        except Exception as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
