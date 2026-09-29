#!/usr/bin/env python3
"""Damaged names put to the library's *other* entry points, with no reference involved.

`tools/mutate.py` asks a reference demangler what a mutant means. Some properties have
no reference to ask, because they are about this package rather than about the scheme:

  * A style is a spelling policy. Whether a name parses at all must not depend on it --
    and it did, for twelve names in the corpora, because the GNU style resolves a
    `<template-param>` inside a requires-clause where the llvm style spells it
    symbolically, and refused the name where nothing was bound. `std::pair`'s
    constrained constructor, which is what GCC 13 emits for the real `std::pair`, read
    under `--style llvm` and came back mangled under `--style gnu`.
  * The tree and the text are one stream. `parse(name).spell()` is what `demangle(name)`
    returns, in every style; `tests/test_conformance.py` checks that over the corpora and
    this checks it over names no compiler wrote.
  * Every public entry point survives what `demangle` reads. `signature`, `demangleb`,
    `parse().to_dict()` and `Signature.base_name` are offered the same names, and
    anything but a `DemanglingError` out of them is a defect: a caller walking a symbol
    table gets a verdict, not a traceback.

The mutation operators are `tools/mutate.py`'s. The seeds are not: that tool can only
damage names it has a reference to ask about, which is seven schemes of the fourteen,
and none of these invariants needs one. So this seeds from *every* conformance corpus --
Nim, Free Pascal, Delphi, Go, Objective-C, JNI and CodeWarrior included, which nothing
else fuzzes -- and takes each corpus's own characters as the alphabet to draw
substitutions from, which is the alphabet its scheme actually writes.

Exit status is non-zero when any invariant fails.

The draw is seeded, so a finding reproduces exactly. The three defects this was written
for were all the first invariant, and `--seed 1 --count 20000 --corpus itanium-libcxxabi`
reports them on the parser as it stood before the fix and nothing on the parser as it is:
that is what says the instrument works, rather than that it is quiet.

Usage
-----
    tools/invariants.py                  the default draw, which is what CI runs
    tools/invariants.py --count 200000   more mutants per corpus
    tools/invariants.py --seed 7         a different draw; the default draw is fixed
    tools/invariants.py --corpus rust    only corpora whose name holds this
"""

import argparse
import gzip
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mutate import mutate

import demangle
from demangle.core.errors import DemanglingError

CONFORMANCE = Path(__file__).resolve().parent.parent / "tests" / "conformance"

#: How many seeds contribute to the alphabet. Every corpus here is homogeneous -- one
#: scheme, one mangler -- so the characters of the first few thousand names are the
#: characters of all of them, and reading every one of 35,000 to build a set of forty
#: is work for nothing.
_ALPHABET_SAMPLE = 2000

#: What a mutant is offered, beyond `demangle` itself. Each is a call and the name it is
#: reported under; none of them may raise anything but a `DemanglingError`.
ENTRY_POINTS = (
    ("signature", demangle.signature),
    ("signature.base_name", lambda name: demangle.signature(name).base_name),
    ("demangleb", lambda name: demangle.demangleb(name.encode("utf-8", "surrogateescape"))),
    ("parse().to_dict()", lambda name: demangle.parse(name).to_dict()),
)


def problems_with(name):
    """Every invariant `name` breaks, as a list of reasons. Empty is the good answer."""
    found = []
    read = {}
    for style in demangle.styles():
        try:
            read[style] = demangle.demangle_strict(name, style=style)
        except DemanglingError:
            read[style] = None
        except Exception as exc:
            found.append(f"{style}: demangle_strict raised {type(exc).__name__}: {exc}")
            read[style] = None
    if len({answer is None for answer in read.values()}) > 1:
        readable = ", ".join(f"{style}={'read' if answer else 'refused'}" for style, answer in read.items())
        found.append(f"a style decided whether it parses: {readable}")
    for style, answer in read.items():
        if answer is None:
            continue
        try:
            spelled = demangle.parse(name, style=style).spell(style=style)
        except DemanglingError:
            found.append(f"{style}: parse refused what demangle read")
            continue
        except Exception as exc:
            found.append(f"{style}: parse raised {type(exc).__name__}: {exc}")
            continue
        if spelled != answer:
            found.append(f"{style}: the tree spells {spelled!r}, the text {answer!r}")
    if any(answer is not None for answer in read.values()):
        for label, call in ENTRY_POINTS:
            try:
                call(name)
            except DemanglingError:
                pass
            except Exception as exc:
                found.append(f"{label} raised {type(exc).__name__}: {exc}")
    return found


def corpora(pattern=None):
    """Every conformance corpus as `(name, seeds, alphabet)`, longest names first."""
    found = []
    for path in sorted(CONFORMANCE.iterdir()):
        if path.suffix == ".gz":
            text = gzip.decompress(path.read_bytes()).decode("utf-8", "surrogateescape")
        elif path.suffix == ".txt":
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
        else:
            continue
        if pattern and pattern not in path.name:
            continue
        seeds = [line.split("\t", 1)[0] for line in text.splitlines() if line and not line.startswith("#")]
        # Two characters is not a name to damage; three leaves nothing after a cut.
        seeds = [seed for seed in seeds if len(seed) > 3]
        if not seeds:
            continue
        alphabet = "".join(sorted({character for seed in seeds[:_ALPHABET_SAMPLE] for character in seed}))
        found.append((path.name.removesuffix(".gz"), seeds, alphabet))
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=4000, help="mutants per corpus")
    parser.add_argument("--seed", type=int, default=0, help="the draw; fixed by default")
    parser.add_argument("--corpus", help="only corpora whose filename holds this")
    parser.add_argument("--quiet", action="store_true", help="one line per corpus")
    arguments = parser.parse_args()

    total = 0
    for name, seeds, alphabet in corpora(arguments.corpus):
        rng = random.Random(arguments.seed)
        broken = []
        for _ in range(arguments.count):
            mutant = mutate(rng, seeds, alphabet, "")
            if not mutant:
                continue
            found = problems_with(mutant)
            if found:
                broken.append((mutant, found))
        total += len(broken)
        if broken or not arguments.quiet:
            print(f"{name:34} {len(seeds):6} seeds, {arguments.count:7} mutants, {len(broken):4} broken")
        for mutant, reasons in broken[:10]:
            print(f"   {mutant}")
            for reason in reasons:
                print(f"     {reason}")
    if total:
        print(f"\n{total} name(s) broke an invariant")
        return 1
    print("\nno invariant broken")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
