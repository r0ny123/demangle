#!/usr/bin/env python3
"""Reproducible benchmarks.

Performance is a design requirement here, not an afterthought, so it is measured on the
same corpora the conformance tests use and the numbers are committed. `--check` compares
against the recorded baseline and fails on a regression, which is what makes it usable
as a CI gate rather than a thing someone runs when a user complains.

Deliberately measures four different situations, because they have very different cost
profiles and only the first is what a naive benchmark reports:

  cold        every name distinct, cache empty -- the true parse cost
  warm        names repeat, as they do in a real symbol table
  negative    names that are not mangled at all -- the majority in most binaries
  structured  building a full AST rather than a string
"""

import argparse
import contextlib
import gc
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import demangle  # noqa: E402

BASELINE = Path(__file__).parent / "baseline.json"
CONFORMANCE = ROOT / "tests" / "conformance"

#: A regression has to be this much worse than the baseline to fail, so ordinary
#: machine-to-machine and run-to-run noise does not redden CI.
TOLERANCE = 1.40


def corpus_names(*files):
    names = []
    for name in files:
        path = CONFORMANCE / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "\t" in line:
                names.append(line.split("\t", 1)[0])
    return names


def time_it(function, repeats=5):
    """Best-of-N wall time. Best, not mean: the minimum is the least noisy estimator."""
    timings = []
    for _ in range(repeats):
        gc.collect()
        start = time.perf_counter()
        function()
        timings.append(time.perf_counter() - start)
    return min(timings)


def benchmarks():
    itanium = corpus_names("itanium-real-world.txt")
    msvc = corpus_names("msvc-llvm-corpus.txt")
    everything = itanium + msvc
    negatives = [f"not_a_mangled_symbol_{index}" for index in range(len(everything))]

    def cold():
        demangle.cache_clear()
        for name in everything:
            demangle.demangle(name)

    def warm():
        for _ in range(10):
            for name in everything:
                demangle.demangle(name)

    def negative():
        demangle.cache_clear()
        for name in negatives:
            demangle.demangle(name)

    parsed = []

    def structured():
        # The successes are counted, and `main` asserts the count. Suppressing failures
        # and timing whatever is left means a change that made `parse()` raise
        # immediately would time at a fraction of the baseline and be reported as an
        # enormous *improvement*.
        count = 0
        for name in itanium:
            with contextlib.suppress(Exception):
                demangle.parse(name)
                count += 1
        parsed.append(count)

    return parsed, [
        ("cold", cold, len(everything)),
        ("warm", warm, len(everything) * 10),
        ("negative", negative, len(negatives)),
        ("structured", structured, len(itanium)),
    ]


def run():
    results = {}
    parsed, cases = benchmarks()
    for name, function, count in cases:
        if not count:
            continue
        demangle.cache_clear()
        elapsed = time_it(function)
        results[name] = {
            "names": count,
            "seconds": round(elapsed, 6),
            "per_second": round(count / elapsed),
            "microseconds_each": round(elapsed / count * 1e6, 3),
        }
    if "structured" in results:
        results["structured"]["parsed"] = parsed[-1] if parsed else 0
    return results


def report(results):
    print(f"{'benchmark':12} {'names':>8} {'sec':>9} {'names/sec':>12} {'us each':>10}")
    for name, data in results.items():
        print(
            f"{name:12} {data['names']:>8} {data['seconds']:>9.4f} "
            f"{data['per_second']:>12,} {data['microseconds_each']:>10.2f}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--save", action="store_true", help="record these numbers as the baseline")
    parser.add_argument("--check", action="store_true", help="fail if slower than the baseline")
    arguments = parser.parse_args()

    results = run()
    report(results)

    if arguments.save:
        BASELINE.write_text(json.dumps(results, indent=2) + "\n")
        print(f"\nbaseline written to {BASELINE}")
        return 0

    if arguments.check:
        if not BASELINE.exists():
            print("\nno baseline recorded; run with --save first")
            return 0
        baseline = json.loads(BASELINE.read_text())
        expected = baseline.get("structured", {}).get("parsed")
        measured = results.get("structured", {}).get("parsed")
        if expected is not None and measured != expected:
            print(f"\nstructured benchmark parsed {measured} names, baseline parsed {expected}")
            print("a timing that improved because the work stopped happening is not an improvement")
            return 1
        regressions = []
        for name, data in results.items():
            if name not in baseline:
                continue
            before = baseline[name]["microseconds_each"]
            after = data["microseconds_each"]
            if after > before * TOLERANCE:
                regressions.append(f"  {name}: {before:.2f}us -> {after:.2f}us ({after / before:.2f}x)")
        if regressions:
            print("\nperformance regression:")
            print("\n".join(regressions))
            return 1
        print("\nno regression against baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
