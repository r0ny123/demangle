#!/usr/bin/env python3
"""Put every Unicode scalar value to this library and to rustc-demangle as a `char` const.

`escape_debug` in `src/demangle/schemes/rust/_v0.py` decides whether a `char` const
prints bare or as `\\u{...}` from `unicodedata.category`, so the answer depends on the
Unicode version CPython was built with. rustc-demangle carries its own table. This
measures the gap rather than remembering it.

    tools/sweep_rust_chars.py

Needs `tools/rustc-demangle-reference` built. About a minute. Prints a count per
bucket and one example each: assigned here and unassigned there, the other way,
category moved, and the rest.

The name is the `features::const_char` vector with the hex swapped: every scalar
value, including the ones a compiler will never write.
"""

from __future__ import annotations

import subprocess
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import demangle

PREFIX = "_RINvCsdEttCVZFADF_8features10const_charKc"
SUFFIX = "_EB2_"
REFERENCE = (
    Path(__file__).resolve().parent / "rustc-demangle-reference" / "target" / "release" / "rustc-demangle-reference"
)
BATCH = 4096


def scalar_values():
    for code in range(0x110000):
        if 0xD800 <= code <= 0xDFFF:
            continue
        yield code


def names_for(codes):
    return [f"{PREFIX}{code:x}{SUFFIX}" for code in codes]


def extract(spelled):
    """The quoted `char` body, or None when the name was refused."""
    start = spelled.find("::<'")
    if start < 0 or not spelled.endswith("'>"):
        return None
    return spelled[start + 4 : -2]


def main():
    if not REFERENCE.exists():
        print("build tools/rustc-demangle-reference first", file=sys.stderr)
        return 1

    codes = list(scalar_values())
    ours = {}
    for name, code in zip(names_for(codes), codes, strict=True):
        spelled = demangle.demangle(name)
        ours[code] = None if spelled == name else extract(spelled)

    theirs = {}
    for start in range(0, len(codes), BATCH):
        chunk = codes[start : start + BATCH]
        names = names_for(chunk)
        proc = subprocess.run(
            [str(REFERENCE)],
            input="\n".join(names) + "\n",
            capture_output=True,
            text=True,
            check=True,
        )
        lines = proc.stdout.splitlines()
        if len(lines) != len(names):
            raise SystemExit(f"reference: {len(lines)} lines for {len(names)} names")
        for code, name, line in zip(chunk, names, lines, strict=True):
            theirs[code] = None if line == name else extract(line)

    buckets = Counter()
    examples = {}
    for code in codes:
        a, b = ours[code], theirs[code]
        if a == b:
            continue
        category = unicodedata.category(chr(code))
        if a is None or b is None:
            kind = "refused"
        elif a.startswith("\\u{") and not (b.startswith("\\") or b.startswith("\\u")):
            kind = f"escaped here, printed there ({category})"
        elif b.startswith("\\u{") and not (a.startswith("\\") or a.startswith("\\u")):
            kind = f"printed here, escaped there ({category})"
        else:
            kind = f"other ({category})"
        buckets[kind] += 1
        examples.setdefault(kind, f"U+{code:04X} ours={a!r} theirs={b!r}")

    print(f"{len(codes)} scalar values, {sum(buckets.values())} differ")
    print(f"CPython Unicode {unicodedata.unidata_version}")
    for kind, count in sorted(buckets.items(), key=lambda item: (-item[1], item[0])):
        print(f"  {count:5}  {kind}")
        print(f"         {examples[kind]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
