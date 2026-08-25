"""The `demangle` command.

Reads names from arguments or standard input and writes their expansions, one per line,
so it drops into a pipeline the way `c++filt` does:

    nm -a libfoo.so | demangle
    demangle _ZNSt6vectorIiSaIiEE9push_backERKi

Names that cannot be read pass through unchanged, which is what makes it safe to run
over a whole symbol table.
"""

import argparse
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
    parser.add_argument("-l", "--language", choices=None, help="force a scheme instead of detecting")
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
    for slot in getattr(node, "__slots__", ()):
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

    if arguments.language and arguments.language not in languages():
        parser.error(f"unknown language {arguments.language!r}; choose from {', '.join(languages())}")

    names = arguments.names or (line.rstrip("\n") for line in sys.stdin)

    status = 0
    for name in names:
        if not name:
            print()
            continue
        if arguments.detect:
            print(detect(name) or "-")
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
        except Exception as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
