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

The seeds, the mutation operators and the per-scheme alphabets are `tools/mutate.py`'s;
only the oracle differs. Exit status is non-zero when any invariant fails.

Usage
-----
    tools/invariants.py                  the default draw, which is what CI runs
    tools/invariants.py --count 200000   more mutants per scheme
    tools/invariants.py --seed 7         a different draw; the default draw is fixed
"""

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mutate import SEEDS, alphabet_for, load_seeds, mutate

import demangle
from demangle.core.errors import DemanglingError

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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=20000, help="mutants per scheme")
    parser.add_argument("--seed", type=int, default=0, help="the draw; fixed by default")
    parser.add_argument("--scheme", action="append", help="only these schemes")
    parser.add_argument("--quiet", action="store_true", help="one line per scheme")
    arguments = parser.parse_args()

    schemes = arguments.scheme or sorted(SEEDS)
    total = 0
    for scheme in schemes:
        seeds = load_seeds(scheme)
        if not seeds:
            print(f"{scheme:8} no seed corpus in this checkout")
            continue
        alphabet, prefix = alphabet_for(scheme), SEEDS[scheme][1]
        rng = random.Random(arguments.seed)
        read = 0
        broken = []
        for _ in range(arguments.count):
            name = mutate(rng, seeds, alphabet, prefix)
            if not name:
                continue
            found = problems_with(name)
            if found:
                broken.append((name, found))
            else:
                read += 1
        total += len(broken)
        print(f"{scheme:8} {len(seeds):6} seeds, {arguments.count:7} mutants, {len(broken):4} broken")
        if not arguments.quiet:
            for name, reasons in broken[:10]:
                print(f"   {name}")
                for reason in reasons:
                    print(f"     {reason}")
    if total:
        print(f"\n{total} name(s) broke an invariant")
        return 1
    print("\nno invariant broken")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
