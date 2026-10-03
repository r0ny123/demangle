#!/usr/bin/env python3
"""Build the Go conformance corpus from real Go binaries.

Go has no reference demangler to compare against -- `go tool nm` prints symbol names as
they stand, escapes and all, and nothing in the toolchain turns one back into a package
path. So the expected column here cannot be "what the reference said", and pretending
otherwise would make the corpus a record of this library agreeing with itself.

What it is instead is a regression pin, and the correctness argument lives in a property
that does not need a reference: `escape_path(decode(symbol)) == symbol`. `escape_path` is
a transcription of Go's own `objabi.PathToPrefix`, the function that produced these names,
so a decoding that survives the round trip is a decoding the Go linker would have written.
`tests/test_go.py` checks that over every symbol, not over the sample recorded here.

The sample is chosen to cover the shapes rather than to be large: every symbol that
carries an escape (the part that is actually encoded), plus a spread of receivers,
generic instantiations, closures, itabs and type descriptors.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from demangle.schemes.go._parser import escape_path, parse_go_symbol  # noqa: E402

DEFAULT_BINARIES = [
    "/usr/local/go/bin/go",
    "/usr/local/go/pkg/tool/linux_amd64/compile",
    "/usr/local/go/pkg/tool/linux_amd64/link",
]


#: The shipped toolchain contains no escaped symbol at all, so this module supplies them:
#: package directories `v2.5` and `weird.pkg.name`, and non-ASCII identifiers.
CORPUS_SOURCE = ROOT / "tools" / "corpus_sources" / "go"


def build_corpus_module(destination):
    """Compile the checked-in module, returning the binary or None if Go is absent."""
    if not shutil.which("go"):
        return None
    destination.mkdir(parents=True, exist_ok=True)
    binary = destination / "gocorpus"
    result = subprocess.run(
        # `-l` disables inlining, so a small module still emits the symbols it declares
        # rather than having them folded into main.
        ["go", "build", "-gcflags=all=-l", "-o", str(binary), "."],
        cwd=CORPUS_SOURCE,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  building the corpus module failed: {result.stderr.strip().splitlines()[:2]}")
        return None
    return binary


def symbols(binary):
    """Every symbol name in `binary`, as `nm` prints it: `[address] type name`.

    The name is everything after the type letter, spaces included -- a generic
    instantiation over a struct shape is written `go.shape.struct { X int }`, and taking
    the last whitespace-separated field of that line would keep `}`.
    """
    result = subprocess.run(["nm", binary], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    names = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3:
            names.append(fields[2])
        elif len(fields) == 2:  # an undefined symbol has no address
            names.append(fields[1])
    return names


def shape(name):
    """A coarse bucket, so the sample spans the forms rather than one of them."""
    if "%" in name:
        return "escaped"
    if name.startswith(("go:itab", "go.itab")):
        return "itab"
    if name.startswith(("type:", "type.")):
        return "type"
    if name.startswith(("go:", "go.")):
        return "generated"
    if "(*" in name:
        return "pointer-receiver"
    if name.endswith("]"):
        return "generic"
    if ".func" in name:
        return "closure"
    return "plain"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binaries", nargs="*", default=DEFAULT_BINARIES)
    parser.add_argument("--per-shape", type=int, default=250, help="how many of each shape to keep")
    parser.add_argument("--out", type=Path, default=ROOT / "tests" / "conformance" / "go-real-world.txt")
    parser.add_argument("--build-dir", default="/tmp/go-corpus-build")
    arguments = parser.parse_args()

    seen = set()
    for binary in arguments.binaries:
        if Path(binary).exists():
            seen.update(symbols(binary))

    built = build_corpus_module(Path(arguments.build_dir))
    if built is not None:
        # Its own symbols, and the linker's generated ones over its types: a struct tag
        # reaches a symbol name quoted, and every symbol carrying one is kept.
        contributed = [name for name in symbols(built) if "example.com/corpus" in name or '"' in name]
        print(f"  corpus module contributed {len(contributed)} symbols")
        seen.update(contributed)
    else:
        print("  corpus module not built; escaped forms will be missing")

    if not seen:
        sys.exit("no symbols read; is a Go toolchain installed?")

    buckets = {}
    for name in sorted(seen):
        buckets.setdefault(shape(name), []).append(name)

    rows = []
    for kind, names in sorted(buckets.items()):
        # Every escaped symbol is kept: it is the only part of a Go name that is encoded,
        # so sampling it would be sampling away the thing under test.
        keep = names if kind == "escaped" else names[: arguments.per_shape]
        for name in keep:
            try:
                symbol = parse_go_symbol(name)
            except Exception:
                continue
            if escape_path(symbol.package) != name[len(symbol.generated) :][: len(escape_path(symbol.package))]:
                sys.exit(f"round trip failed for {name!r}; refusing to record it")
            rows.append((name, symbol.text))

    header = (
        "# Conformance corpus: Go symbol names.\n"
        "#\n"
        "# Read out of the shipped Go toolchain binaries by tools/generate_go_corpus.py.\n"
        "#\n"
        "# Go has no reference demangler: `go tool nm` prints these names as they stand,\n"
        "# escapes included, and nothing in the toolchain decodes one. So the expected\n"
        "# column is not a reference's answer, and this file is a regression pin rather\n"
        "# than a conformance check against another implementation.\n"
        "#\n"
        "# The correctness argument is a property instead, and it needs no reference:\n"
        "# re-escaping a decoded package path must reproduce the bytes the Go linker\n"
        "# wrote, where the escaping is a transcription of Go's own PathToPrefix. Every\n"
        "# row here passed that before being written, and tests/test_go.py checks it over\n"
        "# every symbol in the binaries rather than over this sample.\n"
        "#\n"
    )
    arguments.out.write_text(header + "".join(f"{name}\t{text}\n" for name, text in rows), encoding="utf-8")
    counts = ", ".join(f"{kind} {len([r for r in rows if shape(r[0]) == kind])}" for kind in sorted(buckets))
    print(f"wrote {len(rows)} rows to {arguments.out}")
    print(f"  {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
