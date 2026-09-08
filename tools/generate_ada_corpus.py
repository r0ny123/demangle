#!/usr/bin/env python3
"""Build the Ada conformance corpus from the shipped GNAT runtime libraries.

The mangled names come from `libgnat` and `libgnarl`, which GNAT installs, and the
expected column from GNU binutils' GNAT demangler, `c++filt --format=gnat`. That is a real
second implementation rather than this library's own output, which is what the corpus for
every other C-family scheme rests on too.

Only the names this scheme reads *exactly* are recorded. The corpus is therefore a floor
-- it stops the conformance figure going down -- and not a claim that Ada is finished; see
ROADMAP.md for where it actually stands.
"""

import argparse
import glob
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from demangle.schemes.ada import detect as detect_ada  # noqa: E402
from demangle.schemes.ada._parser import demangle_ada  # noqa: E402

DEFAULT_LIBRARIES = [
    "/usr/lib/x86_64-linux-gnu/libgnat-*.so",
    "/usr/lib/x86_64-linux-gnu/libgnarl-*.so",
    "/usr/lib/gcc/x86_64-linux-gnu/*/rts-native/adalib/libgnat.a",
    "/usr/lib/gcc/x86_64-linux-gnu/*/rts-native/adalib/libgnarl.a",
    "/usr/lib/gcc/x86_64-linux-gnu/*/adalib/libgnat.a",
    "/usr/lib/gcc/x86_64-linux-gnu/*/adalib/libgnarl.a",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libraries", nargs="*", default=DEFAULT_LIBRARIES)
    parser.add_argument("--keep", type=int, default=1200, help="how many rows to record")
    parser.add_argument(
        "--include-undetected",
        action="store_true",
        help="include symbols that GNAT runtime defines but which carry no distinct Ada marker",
    )
    parser.add_argument("--out", type=Path, default=ROOT / "tests" / "conformance" / "ada-real-world.txt")
    arguments = parser.parse_args()

    if not shutil.which("c++filt"):
        sys.exit("c++filt is not installed; it is the reference for this corpus")

    resolved_libraries = []
    for pattern in arguments.libraries:
        if "*" in pattern:
            resolved_libraries.extend(glob.glob(pattern))
        elif Path(pattern).exists():
            resolved_libraries.append(pattern)

    names = set()
    for library in resolved_libraries:
        output = subprocess.run(["nm", "-D", "--defined-only", library], capture_output=True, text=True).stdout
        if not output:
            output = subprocess.run(["nm", library], capture_output=True, text=True).stdout
        names.update(line.split()[-1] for line in output.splitlines() if line.strip() and "__" in line.split()[-1])
    if not arguments.include_undetected:
        names = {n for n in names if detect_ada(n)}
    names = sorted(names)
    if not names:
        sys.exit("no Ada symbols found; is a GNAT runtime installed?")

    reference = subprocess.run(
        ["c++filt", "--format=gnat"], input="\n".join(names) + "\n", capture_output=True, text=True
    ).stdout.splitlines()
    if len(reference) != len(names):
        sys.exit("c++filt returned a different number of lines than it was given")

    exact = []
    for name, expected in zip(names, reference, strict=True):
        if expected == name or (expected.startswith("<") and expected.endswith(">")):
            continue  # the reference could not read it either
        try:
            if demangle_ada(name).text == expected:
                exact.append((name, expected))
        except Exception:
            continue

    readable = sum(
        1
        for name, expected in zip(names, reference, strict=True)
        if expected != name and not (expected.startswith("<") and expected.endswith(">"))
    )
    step = max(1, len(exact) // arguments.keep)
    sample = exact[::step]
    header = (
        "# Conformance corpus: Ada/GNAT mangled names.\n"
        "#\n"
        "# Read from the shipped libgnat and libgnarl by tools/generate_ada_corpus.py,\n"
        "# with the expected column produced by GNU binutils' GNAT demangler,\n"
        "# `c++filt --format=gnat`.\n"
        "#\n"
        "# This corpus holds the names the scheme currently reads *exactly*. It is a floor,\n"
        "# not a claim of completeness: Ada landed the same way as the C-family schemes -- see\n"
        "# ROADMAP.md for where it stands. What this file does is stop that number going\n"
        "# down.\n"
        "#\n"
    )
    arguments.out.write_text(header + "".join(f"{n}\t{e}\n" for n, e in sample), encoding="utf-8")
    print(f"{len(names)} Ada symbols, {readable} the reference can read")
    print(f"  exact: {len(exact)} ({len(exact) / readable * 100:.1f}%)")
    print(f"  wrote {len(sample)} rows to {arguments.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
