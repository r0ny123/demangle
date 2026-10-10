#!/usr/bin/env python3
"""Mutate real symbols and put the results to a reference demangler.

`tools/enumerate.py` covers the *shape* space: every string a small alphabet spells,
up to five or six characters. That is exactly the wrong length for the constructs
that only appear once a name is long -- a substitution referring back to a component
built earlier, a template argument list nested three deep, a return type that is
itself a function pointer. Those need a name a compiler actually emitted, and there
is no alphabet short enough to reach them by counting.

So this starts from the checked-in corpora and damages them:
truncate, delete, duplicate, transpose, substitute a character from the
scheme's own alphabet, or splice the head of one name onto the tail of another. A
mutant keeps almost all of its parent's structure, so it lands *near* the emitted
space rather than in the grammar's cheap corners, which is where a substitution
table gets corrupted rather than merely emptied.

Every mutant this library reads is then offered to the reference, and every
disagreement that `tools/enumerate.py`'s `ACCEPTED` rules do not explain is
reported. The rules are shared on purpose: they are statements about *why* a
reference answer is not evidence, and that reason does not change with how the name
was found.

Usage
-----
    tools/mutate.py                        every scheme with a reference on this box
    tools/mutate.py --scheme itanium       one scheme
    tools/mutate.py --count 200000         more mutants per scheme (default 50000)
    tools/mutate.py --seed 7               a different draw; the default draw is fixed

`--expect` is the divergence count the run must produce, 0 by default. The exit status
is non-zero unless the count is exactly that, so the gate holds in both directions, like
the conformance corpora: a new divergence fails, and so does a stale pin after one is
fixed. The count is a property of `--seed`, `--count` and the seed corpora together;
the pin CI uses is for `--seed 0` with `--count 20000`, not for the default `--count`.
Growing a seed corpus changes which mutants are drawn.

A divergence is either fixed in the library or covered by an `ACCEPTED` rule in
`tools/enumerate.py`, which names the reason the reference's answer is not evidence.
Two rules cover shapes only mutation reaches: a cv-qualified function type reached
through a substitution, where each reference contradicts its own answer for the same
type written out, and a D symbol whose length prefix ends inside an identifier
(`_D1a0MFZv`), which libiberty refuses although it reads both neighbours, `_D1a0i` and
`_D1a0FZv`.

A draw with no divergence does not show that none exist. Two are open and pinned by
nothing but this note:

- A `<template-param>` naming an argument pack outside a `Dp` expansion. `_Z1fIJfdEEvT_`
  is `void f<float, double>(float)` to both references and `(float, double)` here;
  defaulting the pack index to 0 breaks eleven of libcxxabi's own vectors.
- A destructor whose class is named by a vendor extended operator, `v1 <source-name>`.
  `llvm-cxxfilt` writes `~()`, `c++filt` writes the name without `operator`, and this
  follows LLVM's spelling. The references disagree on this unusual encoding.

`--refusals` looks the other way. The comparison above only sees a name this library
*reads*: a name it refuses and the reference reads never enters it. This mode puts the
refused mutants to the reference and prints what it says about them. It is a triage
list and not a gate -- everything on it is either a gap here or the reference reading
junk it should have refused, and telling the two apart is a person's job with the
reference's source open. What it reports is pinned in the schemes' tests as refusals,
each with the reference's reason.

    tools/mutate.py --refusals --scheme msvc --show 100
"""

import argparse
import contextlib
import gzip
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from enumerate import ACCEPTED, JOBS, library_reading, reference_answers

CONFORMANCE = Path(__file__).resolve().parent.parent / "tests" / "conformance"

#: Each scheme's seed corpora and the prefix a mutant keeps, so mutants test the parser
#: rather than the detector.
SEEDS = {
    "itanium": (
        [
            "itanium-libstdcxx.txt",
            "itanium-real-world.txt",
            "itanium-real-world-gnu.txt",
            "itanium-types.txt",
            "itanium-types-llvm.txt",
            "reported/itanium.txt",
            "itanium-libcxxabi.txt.gz",
        ],
        "_Z",
    ),
    "types": (["itanium-types.txt", "itanium-types-llvm.txt"], ""),
    "rust": (["rust-real-world.txt", "rust-toolchain.txt"], "_R"),
    "d": (["d-real-world.txt", "d-libiberty.txt"], "_D"),
    "ada": (["ada-libiberty.txt", "ada-real-world.txt"], ""),
    "msvc": (["msvc-llvm-corpus.txt", "msvc-arm64ec.txt", "msvc-clang.txt"], "?"),
    # `swift-symbolic.txt` is hex and `swift-simplified.txt` uses other options.
    "swift": (["swift-real-world.txt", "swift-upstream.txt", "swift-refusals.txt"], "$s"),
    # All four styles' rows, read under `gnu` on both sides as the scheme's default does.
    "gnuv2": (["gnuv2-libiberty.txt"], ""),
}

#: `(tool, normalise)` asked where the first reference and this library disagree; if its
#: normalised answer matches ours, the first reference is the odd one out.
#:
#: `llvm-cxxfilt` caps a Rust `<base-62-number>` at 64 bits and refuses e.g.
#: `_RNvCsAAAAAAAAAAA_1a1f`; binutils wraps instead and annotates disambiguators `[hex]`.
SECOND_OPINION = {
    "rust": ("c++filt", lambda answer: re.sub(r"\[[0-9a-f]+\]", "", answer)),
}

#: A name to ask the reference about instead, where it cannot read the original for a
#: known reason unrelated to the reading. `llvm-undname` 18 does not know the ARM64EC
#: marker `$$h`, which changes no part of the declaration, so the substitute drops it.
RESCUE = {
    "msvc": lambda name: name.replace("$$h", "", 1) if "$$h" in name else None,
}

#: Added to the scheme's own markers from `JOBS` for substitutions and insertions.
_EXTRA = "0123456789_abcxyzABCXYZ$."


def alphabet_for(scheme):
    letters = "".join(alphabet for _, alphabet in JOBS[scheme][2])
    return "".join(dict.fromkeys(letters + _EXTRA))


def load_seeds(scheme):
    """The mangled column of every corpus listed for `scheme`, deduplicated."""
    files, prefix = SEEDS[scheme]
    names = {}
    for filename in files:
        path = CONFORMANCE / filename
        if not path.exists():
            continue
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as source:
                text = source.read()
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            mangled = line.split("\t", 1)[0]
            if mangled.startswith(prefix) and len(mangled) > len(prefix) + 1:
                names[mangled] = None
    return list(names)


def mutate(rng, seeds, alphabet, prefix):
    """One damaged name, or None where the damage left nothing to read."""
    name = rng.choice(seeds)
    head = len(prefix)
    if len(name) <= head + 1:
        return None
    operator = rng.randrange(6)
    at = rng.randrange(head, len(name))
    if operator == 0:  # truncate
        return name[:at] or None
    if operator == 1:  # delete
        return name[:at] + name[at + 1 :]
    if operator == 2:  # duplicate
        return name[:at] + name[at] + name[at:]
    if operator == 3:  # transpose
        if at + 1 >= len(name):
            return None
        return name[:at] + name[at + 1] + name[at] + name[at + 2 :]
    if operator == 4:  # substitute
        return name[:at] + rng.choice(alphabet) + name[at + 1 :]
    other = rng.choice(seeds)  # splice
    cut = rng.randrange(head, len(other))
    return name[:at] + other[cut:]


def draw(scheme, count, seed):
    """`count` distinct mutants, and the ones this library reads as `{mangled: spelling}`."""
    rng = random.Random(seed)
    seeds = load_seeds(scheme)
    if not seeds:
        return {}, 0
    alphabet = alphabet_for(scheme)
    _, prefix = SEEDS[scheme]
    generated = set()
    while len(generated) < count:
        mutant = mutate(rng, seeds, alphabet, prefix)
        if mutant and mutant not in generated:
            generated.add(mutant)
    return generated, len(seeds)


def readings(scheme, names, style=None):
    found = {}
    for name in names:
        with contextlib.suppress(Exception):
            found[name] = library_reading(scheme, name, style)
    return found


def ask_tolerantly(tool, names, batch, casualties=None):
    """`reference_answers`, for names that may take the reference down.

    A process that dies mid-batch answers nothing for any name in it, so a batch that
    comes back short is split and asked again, down to the one name that did it. That
    name is recorded as `None` and, where `casualties` is given, named in it.

    Both halves of the gate need this. A refused mutant can kill a reference -- Swift's
    own demangler aborts on some of them -- and so can a name this library *reads*.
    `c++filt --format=gnat` from binutils 2.42 aborts on `aSO__bDF` with a detected
    buffer overflow: an `'Output` attribute, a `__` separator and a `.Finalize` suffix
    in one name, none of which does it alone. This library reads that mutant as
    `a'Output.b.Finalize`, so it goes to the reference through the gate and, unbatched,
    would take the whole Ada run down with it.

    Binutils' D demangler is the other failure mode: a mutant whose back references
    chain takes it into gigabytes of expansion and never returns, while this library
    refuses the same name in milliseconds at its substitution limit. So every batch
    runs under a memory cap and a timeout, and one that trips either is split the
    same way.
    """
    answers = {}
    for start in range(0, len(names), batch):
        chunk = names[start : start + batch]
        try:
            answers.update(reference_answers(tool, chunk, timeout=60 + len(chunk) // 100, memory=1 << 30))
        except (SystemExit, subprocess.TimeoutExpired):
            if len(chunk) == 1:
                answers[chunk[0]] = None
                if casualties is not None:
                    casualties.add(chunk[0])
            else:
                half = len(chunk) // 2
                answers.update(ask_tolerantly(tool, chunk[:half], batch, casualties))
                answers.update(ask_tolerantly(tool, chunk[half:], batch, casualties))
    return answers


def refusals(scheme, count, seed, show, batch):
    """What the reference says about the mutants this library refuses. Never fails."""
    tool, _, _ = JOBS[scheme]
    if shutil.which(tool.split()[0]) is None:
        print(f"{scheme}: {tool.split()[0]} not on PATH, skipped")
        return
    mutants, seed_count = draw(scheme, count, seed)
    if not mutants:
        print(f"{scheme:8} no seeds")
        return
    refused = sorted(mutants - set(readings(scheme, mutants)))
    theirs = ask_tolerantly(tool, refused, batch)
    # `c++filt --format=gnat` does not echo what it cannot read: it writes the name
    # inside `<...>`, which is its refusal and not a reading.
    read = [
        (name, answer)
        for name, answer in theirs.items()
        if answer is not None and not (scheme == "ada" and answer.startswith("<") and answer.endswith(">"))
    ]
    who = Path(tool.split()[0]).name
    print(
        f"{scheme:8} {seed_count:>6} seeds, {count:>7} mutants,"
        f" {len(refused):>7} refused here, {len(read)} read by {who}"
    )
    for name, answer in sorted(read)[:show]:
        print(f"   {name}\n     {who:14} {answer}")
    sys.stdout.flush()


def run(scheme, count, seed, quiet, show, batch):
    tool, second_tool, _ = JOBS[scheme]
    if shutil.which(tool.split()[0]) is None:
        print(f"{scheme}: {tool.split()[0]} not on PATH, skipped")
        return 0
    if second_tool and shutil.which(second_tool.split()[0]) is None:
        second_tool = None
    accepted = ACCEPTED.get(scheme, lambda *_: False)

    mutants, seed_count = draw(scheme, count, seed)
    if not mutants:
        print(f"{scheme:8} no seeds")
        return 0
    ours = readings(scheme, mutants)
    names = sorted(ours)
    if not names:
        print(f"{scheme:8} {seed_count:>6} seeds, {count:>7} mutants, none read")
        return 0
    ours_gnu = readings(scheme, names, style="gnu") if second_tool else {}

    # Batched and tolerant: a name can kill a reference, which must not lose the whole run.
    casualties = set()

    def ask(what, wanted):
        return ask_tolerantly(what, wanted, batch, casualties)

    theirs = ask(tool, names)
    second = ask(second_tool, names) if second_tool else {}

    # Before the comparison, so the substitute's answer also goes through `ACCEPTED`.
    rescue = RESCUE.get(scheme)
    if rescue is not None:
        substitutes = {name: rescue(name) for name in names}
        wanted = sorted({s for s in substitutes.values() if s and s not in theirs})
        stand_in = {**theirs, **ask(tool, wanted)}
        for name, substitute in substitutes.items():
            if substitute and theirs.get(name) != ours[name]:
                # Where both read `$$h` as part of an identifier (`?foo$$hbar@@YAXXZ`) the
                # original answers stand; a refused substitute must not overwrite one
                # where our spelling still carries `$$h`. Otherwise the reference's
                # answer about the marked name is not evidence.
                stand = stand_in.get(substitute)
                if stand is not None or "$$h" not in ours[name]:
                    theirs[name] = stand

    # Neither a refusal nor a reading, so excluded from the comparison but still reported.
    if casualties:
        who = Path(tool.split()[0]).name
        print(f"{scheme:8} {len(casualties):>6} name(s) aborted {who}; not compared")
        for name in sorted(casualties)[:show]:
            print(f"   {name}")
        names = [n for n in names if n not in casualties]

    differ = [
        (n, ours[n], theirs.get(n), (second.get(n), ours_gnu.get(n)))
        for n in names
        if theirs.get(n) != ours[n] and not (theirs.get(n) is None and ours[n] == n)
    ]
    kept = [row for row in differ if accepted(*row)]
    real = [row for row in differ if not accepted(*row)]

    opinion = SECOND_OPINION.get(scheme)
    if opinion is not None and real and shutil.which(opinion[0].split()[0]):
        other, normalise = opinion
        wanted = sorted({row[0] for row in real})
        answers = ask(other, wanted)
        agreed = [row for row in real if answers.get(row[0]) and normalise(answers[row[0]]) == row[1]]
        kept += agreed
        real = [row for row in real if row not in agreed]

    note = f", {len(kept)} accepted" if kept else ""
    print(f"{scheme:8} {seed_count:>6} seeds, {count:>7} mutants, {len(names):>7} read, {len(real)} unexplained{note}")
    if not quiet:
        for name, mine, other, also in real[:show]:
            print(f"   {name}\n     ours {mine}\n     {tool.split()[0]:14} {other}")
            if second_tool:
                print(f"     {second_tool.split()[0]:14} {also[0]}")
                print(f"     ours (gnu)     {also[1]}")
    sys.stdout.flush()
    return len(real)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scheme", choices=sorted(SEEDS), action="append", help="repeatable; default is all")
    parser.add_argument("--count", type=int, default=50000, help="mutants per scheme (default 50000)")
    parser.add_argument("--seed", type=int, default=0, help="RNG seed; the draw is reproducible")
    parser.add_argument("--show", type=int, default=10, help="divergences to print per scheme")
    parser.add_argument("--batch", type=int, default=20000, help="names per reference process")
    parser.add_argument("--quiet", action="store_true", help="counts only")
    parser.add_argument(
        "--refusals",
        action="store_true",
        help="the other direction: what the reference says about the mutants this library refuses; a list, not a gate",
    )
    parser.add_argument(
        "--expect",
        type=int,
        default=0,
        help="the divergence count to pin, for this --seed and --count (default 0)",
    )
    args = parser.parse_args(argv)
    if args.count < 0:
        parser.error("--count must be non-negative")

    if args.refusals:
        for scheme in args.scheme or sorted(SEEDS):
            refusals(scheme, args.count, args.seed, args.show, args.batch)
        return 0

    total = 0
    for scheme in args.scheme or sorted(SEEDS):
        total += run(scheme, args.count, args.seed, args.quiet, args.show, args.batch)
    if total != args.expect:
        print(f"\n{total} unexplained divergence(s), expected {args.expect}")
        return 1
    if total:
        print(f"\n{total} unexplained divergence(s), which is the pinned number")
        return 0
    print("\nno unexplained divergences")
    return 0


if __name__ == "__main__":
    sys.exit(main())
