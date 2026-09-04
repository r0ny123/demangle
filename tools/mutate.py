#!/usr/bin/env python3
"""Mutate real symbols and put the results to a reference demangler.

`tools/enumerate.py` covers the *shape* space: every string a small alphabet spells,
up to five or six characters. That is exactly the wrong length for the constructs
that only appear once a name is long -- a substitution referring back to a component
built earlier, a template argument list nested three deep, a return type that is
itself a function pointer. Those need a name a compiler actually emitted, and there
is no alphabet short enough to reach them by counting.

So this starts from the checked-in corpora -- 640,892 real symbols -- and damages
them: truncate, delete, duplicate, transpose, substitute a character from the
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

Exit status is non-zero unless the divergence count is exactly `--expect`, which is 0 by
default -- so this gates a commit in both directions, like the conformance corpora do: a
new divergence fails, and so does a stale pin after one is fixed. The number is a
property of `--seed` and `--count` together; the pin CI uses is for the defaults.

What the pin currently stands at
--------------------------------
Zero, at `--seed 0 --count 20000`. Every divergence this draw reports is either a defect
that was fixed or an `ACCEPTED` rule in `tools/enumerate.py` naming the reason a
reference's answer is not evidence. `--expect` defaults to 0, so the gate is now "no
divergence at all", and a name this library reads differently from the reference for a
reason nobody has written down fails it.

That is not a claim that nothing is left: a divergence *not in this draw* is not a
divergence that does not exist. Three such are named at the end of this docstring, and
`--seed` and `--count` are there to go looking.

What this draw has found and what became of it
----------------------------------------------
Five divergences this draw reported are gone because the defect was this library's and
was fixed: a constructor whose class is named by an operator (`_ZNssC1Ev` read as
`operator<=>::operator()`, the class name cut at the first `<`), the `F` friend marker
read and then dropped from a constructor, and a fold expression printed with llvm's
spacing and bracketing under the GNU style. Two more are now `ACCEPTED` rules in
`tools/enumerate.py`, each with the reason a reference's answer is not evidence: a
cv-qualified function type reached through a substitution, where each reference
contradicts its own answer for the same type written out, and the D one below.

The D divergence was carried here for several sittings as "a deep chain of `Q` back
references round a `___dgliteral1`", which was wrong -- that was the shape of the
*mutant*, not of the disagreement. Diffing the mutant against the seed it came from
named the edit: one duplicated `_` inside `13__dgliteral10`, which leaves the length
prefix covering `___dgliteral1` and hands the `0` after it to the grammar as the
anonymous `<SymbolName>`. `_D1a0MFZv` is the whole of it in nine characters -- `a` here,
refused by `c++filt --format=dlang` -- and libiberty reads both neighbours, `_D1a0i` and
`_D1a0FZv`, so the refusal is an inconsistency inside the reference rather than a rule.
Shrinking the mutant by deletion had found a *different* shape with the same symptom, an
`S` template argument opening on a template instance; that one is real too and is pinned
in `tests/test_d.py`, but it is not what this draw reaches. A reproducer that reproduces
the symptom is not yet the cause.

The draw is a property of the seed *and the corpora*, so growing a seed corpus changes
which mutants are drawn. Two divergences earlier draws reported are simply not in this
one and are still open, pinned by nothing but this note: a `<template-param>` naming an
argument pack outside a `Dp` expansion -- `_Z1fIJfdEEvT_` is `void f<float,
double>(float)` to both references and `(float, double)` here, and making the default
pack index 0 breaks eleven of libcxxabi's own vectors -- and a destructor whose class is
named by a vendor extended operator, `v1 <source-name>`, where `llvm-cxxfilt` writes
`~()`, `c++filt` writes the name without `operator`, and this writes the name the
encoding gives.
"""

import argparse
import contextlib
import gzip
import random
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from enumerate import ACCEPTED, JOBS, reference_answers

import demangle

CONFORMANCE = Path(__file__).resolve().parent.parent / "tests" / "conformance"

#: Where each scheme's seeds come from, and the prefix a mutant has to keep to stay in
#: that scheme. Mutating the prefix would mostly produce names no scheme claims, which
#: tests the detector rather than the parser -- and the detector has its own tests.
SEEDS = {
    "itanium": (
        [
            "itanium-libstdcxx.txt",
            "itanium-real-world.txt",
            "itanium-real-world-gnu.txt",
            "itanium-types.txt",
            "itanium-types-llvm.txt",
            "itanium-regressions.txt",
            "itanium-libcxxabi.txt.gz",
        ],
        "_Z",
    ),
    "rust": (["rust-real-world.txt", "rust-toolchain.txt"], "_R"),
    "d": (["d-real-world.txt", "d-libiberty.txt"], "_D"),
    "ada": (["ada-libiberty.txt"], ""),
    "msvc": (["msvc-llvm-corpus.txt", "msvc-arm64ec.txt", "msvc-clang.txt"], "?"),
    # Only the `$s` corpus rows: `swift-real-world.txt` carries both manglings and the
    # prefix a mutant has to keep can only be one of them. `swift-symbolic.txt` is hex
    # and `swift-simplified.txt` is scored under other options, so neither seeds this.
    "swift": (["swift-real-world.txt", "swift-upstream.txt", "swift-refusals.txt"], "$s"),
    # No prefix to keep: a pre-Itanium name is an ordinary identifier with a `__` in it.
    # All four styles' rows seed this, read under `gnu` on both sides, which is what the
    # scheme's default does with them too.
    "gnuv2": (["gnuv2-libiberty.txt"], ""),
}

#: A second reading of the *same* name, for schemes where a divergence from the first
#: reference is not evidence on its own. Each entry is `(tool, normalise)`: the tool is
#: asked about every name the first reference and this library disagree on, and where
#: its answer -- put through `normalise` -- is what this library said, the two
#: implementations that read the name agree and the first is the odd one out.
#:
#: `llvm-cxxfilt` caps a Rust `<base-62-number>` at 64 bits, so an eleven-digit crate
#: disambiguator is refused: `_RNvCsAAAAAAAAAAA_1a1f` is `a::f` here and unreadable
#: there. binutils reads it, wrapping instead, and spells the same path -- annotating
#: each disambiguator as `[hex]`, which it does for every name and which `normalise`
#: takes back off. rustc emits a disambiguator that is a truncated 64-bit hash, so no
#: compiler reaches the split; the ceiling is the reference's integer type and not a
#: rule of the scheme, and Python has no such type to impose.
SECOND_OPINION = {
    "rust": ("c++filt", lambda answer: re.sub(r"\[[0-9a-f]+\]", "", answer)),
}

#: A name to ask the reference about *instead*, where the reference cannot read the one
#: in hand for a reason that is known and is not about the reading. Where its answer for
#: the substitute is what this library said for the original, the marker was read as the
#: marker and everything else agrees.
#:
#: `llvm-undname` 18 does not know the ARM64EC marker `$$h`: where one stands in a
#: function's encoding it refuses the name, and where one stands inside an identifier it
#: reads it as three more characters of the identifier. The marker says "this is the
#: hybrid entry for that function" and changes no part of the declaration, which is
#: exactly how `tests/conformance/msvc-arm64ec.txt` was built -- so the substitute is
#: the name without it. Checked rather than skipped: a mutation that put a `$$h`
#: somewhere it does not belong still has to agree. The substitute stands in only where
#: the reference's own answer is not already ours: the library reads a name as it stands
#: before it takes the marker out, and a `$$h` that is part of an identifier is read as
#: one by both sides.
RESCUE = {
    "msvc": lambda name: name.replace("$$h", "", 1) if "$$h" in name else None,
}

#: Characters a substitution or insertion draws from: the scheme's own markers, so a
#: mutant is a name the grammar could nearly have spelled rather than line noise.
#: Drawn from `JOBS`, plus the digits and letters every scheme's lengths and identifiers
#: need.
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
            found[name] = demangle.demangle_strict(name, language=scheme, style=style)
    return found


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

    # In batches: a reference is handed the names on stdin, and one process for half a
    # million of them is a process that can die with nothing to show for it.
    def ask(what, wanted):
        answers = {}
        for start in range(0, len(wanted), batch):
            answers.update(reference_answers(what, wanted[start : start + batch]))
        return answers

    theirs = ask(tool, names)
    second = ask(second_tool, names) if second_tool else {}

    # Where the reference cannot read a name for a reason that is known and is not about
    # the reading, ask it about the substitute instead and let that answer stand in. Done
    # here rather than after the comparison so the substitute's answer goes through
    # `ACCEPTED` as well: an ARM64EC name is *also* subject to every rule about the name
    # underneath the marker.
    rescue = RESCUE.get(scheme)
    if rescue is not None:
        substitutes = {name: rescue(name) for name in names}
        # A substitute is often another name in the draw -- a mutant and the one it was
        # mutated from -- and the reference has already answered about those.
        wanted = sorted({s for s in substitutes.values() if s and s not in theirs})
        stand_in = {**theirs, **ask(tool, wanted)}
        for name, substitute in substitutes.items():
            if substitute and theirs.get(name) != ours[name]:
                # Over an answer the reference did give, unless it is the answer this
                # library gave: the library reads the name as it stands first, and where
                # the two agree on that reading the marker was part of an identifier --
                # `?foo$$hbar@@YAXXZ` is `foo$$hbar` to both. Otherwise what the
                # reference says about a name carrying the marker is not evidence either
                # way, since it has no production for `$$h` at all: it read
                # `?$oo_aad@@$$hYAXAEAD@Z` as `public: char && $oo_aad()`, taking the
                # marker for part of a type.
                theirs[name] = stand_in.get(substitute)

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
        "--expect",
        type=int,
        default=0,
        help="the divergence count to pin, for this --seed and --count (default 0)",
    )
    args = parser.parse_args(argv)

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
