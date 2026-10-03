#!/usr/bin/env python3
"""Build the D conformance corpus from the shipped D runtime libraries.

The mangled names come from `libgphobos` and `libgdruntime`, which gdc installs, and the
expected column from GNU binutils' D demangler, `c++filt --format=dlang`. That is a real
second implementation rather than this library's own output, which is what the corpus for
every other C-family scheme rests on too.

Only the names this scheme reads *exactly* are recorded. The corpus is therefore a floor
-- it stops the conformance figure going down -- and not a claim that D is finished; see
CONFORMANCE.md for where it actually stands.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from demangle.schemes.d._parser import parse_d_symbol  # noqa: E402

DEFAULT_LIBRARIES = [
    "/usr/lib/gcc/x86_64-linux-gnu/13/libgphobos.a",
    "/usr/lib/gcc/x86_64-linux-gnu/13/libgdruntime.a",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libraries", nargs="*", default=DEFAULT_LIBRARIES)
    parser.add_argument("--keep", type=int, default=1200, help="how many rows to record")
    parser.add_argument("--out", type=Path, default=ROOT / "tests" / "conformance" / "d-real-world.txt")
    arguments = parser.parse_args()

    if not shutil.which("c++filt"):
        sys.exit("c++filt is not installed; it is the reference for this corpus")

    names = set()
    for library in arguments.libraries:
        if not Path(library).exists():
            continue
        output = subprocess.run(["nm", library], capture_output=True, text=True).stdout
        names.update(line.split()[-1] for line in output.splitlines() if line.strip())
    names = sorted(name for name in names if name.startswith("_D"))
    if not names:
        sys.exit("no D symbols found; is a D runtime installed?")

    reference = subprocess.run(
        ["c++filt", "--format=dlang"], input="\n".join(names) + "\n", capture_output=True, text=True
    ).stdout.splitlines()
    if len(reference) != len(names):
        sys.exit("c++filt returned a different number of lines than it was given")

    exact = []
    for name, expected in zip(names, reference, strict=True):
        if expected == name:
            continue  # the reference could not read it either
        try:
            if parse_d_symbol(name).text == expected:
                exact.append((name, expected))
        except Exception:
            continue

    readable = sum(1 for name, expected in zip(names, reference, strict=True) if expected != name)
    step = max(1, len(exact) // arguments.keep)
    sample = exact[::step]
    header = (
        "# Conformance corpus: D mangled names.\n"
        "#\n"
        "# Read from the shipped libgphobos and libgdruntime by tools/generate_d_corpus.py,\n"
        "# with the expected column produced by GNU binutils' D demangler,\n"
        "# `c++filt --format=dlang`.\n"
        "#\n"
        "# This corpus holds the names the scheme currently reads *exactly*.\n"
        "# Pinned exactly in tests/test_conformance.py.\n"
        "#\n"
    )
    arguments.out.write_text(header + "".join(f"{n}\t{e}\n" for n, e in sample), encoding="utf-8")
    print(f"{len(names)} D symbols, {readable} the reference can read")
    print(f"  exact: {len(exact)} ({len(exact) / readable * 100:.1f}%)")
    print(f"  wrote {len(sample)} rows to {arguments.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
