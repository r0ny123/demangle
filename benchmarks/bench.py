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
#: run-to-run noise does not redden CI. It is a tolerance on the *normalised* figure --
#: see `calibrate` -- so it means a real 25% slowdown in the demangler, not 25% of the
#: difference between two machines.
TOLERANCE = 1.25

#: Iterations of the calibration loop. Enough to take a few milliseconds on any machine
#: that can run the test suite, small enough not to lengthen the benchmark noticeably.
CALIBRATION_ROUNDS = 200_000


def calibrate():
    """Time a fixed workload that has nothing to do with demangling.

    Absolute microseconds-per-name are not comparable across machines, and a tolerance
    factor does not make them so: it only tolerates a difference up to its own size.
    A shared CI runner is comfortably 1.5-2x slower than a developer laptop, so a gate
    on raw wall time fails on the runner for reasons that have nothing to do with the
    change under test -- which is what happened the first time this ran in CI.

    So every figure is divided by this. What the gate then compares is the *ratio*
    between the demangler and the interpreter it is running on, which is a property of
    the code rather than of the hardware.

    The workload is deliberately the same *kind* of work the demangler does -- string
    slicing, dictionary lookup, list building, attribute access -- so it tracks the same
    machine characteristics rather than, say, floating-point throughput. It touches no
    part of `demangle`: if it did, a genuine regression would slow the calibration too
    and hide itself.

    Measured: running the suite against three times as many busy processes as cores --
    a machine roughly 2.8x slower -- moves `cold` from 20.3us to 57.7us but its
    normalised figure only from 500 to 487, and `structured` from 68.6us to 186.1us but
    1693 to 1569. The raw numbers would fail any tolerance worth having; the normalised
    ones sit within 8%.

    What it does *not* do is track mild variation in machine state. Measured again on a
    quiet machine: the calibration moved 39ms to 33ms across a few days while
    `structured` stayed at 66-67us, which shows up as a 20% "regression" in a figure that
    is a ratio. The two workloads track each other under load, when contention dominates
    both; they do not track each other when the difference is CPU boost state, because a
    tight dictionary loop benefits from it more than an allocating parser does. Run to
    run on this hardware the normalised figures carry about 14% spread, which is over half
    the tolerance.

    That is why `--check` confirms a regression before reporting one, and why the
    tolerance is not tightened further. A real regression reproduces; noise mostly does
    not.

    One further caveat: `negative` runs in about two milliseconds, short enough that
    best-of-N finds a clean scheduling slot even on a loaded machine, so it
    under-inflates and reads as much *faster* under load. That direction never fails the
    gate, and a real regression would show in `cold` and `structured` as well, so it is a
    loss of sensitivity rather than a false alarm.
    """

    def workload():
        counts: dict[str, int] = {}
        distinct: list[str] = []
        text = "_ZNSt6vectorIiSaIiEE9push_backERKi"
        total = 0
        for index in range(CALIBRATION_ROUNDS):
            piece = text[index % 8 : index % 8 + 6]
            seen = counts.get(piece)
            if seen is None:
                counts[piece] = 1
                distinct.append(piece)
            else:
                counts[piece] = seen + 1
            total += len(piece)
        return total, len(distinct)

    return time_it(workload, repeats=9)


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

    #: How many times `structured` walks its corpus. One pass is about twenty
    #: milliseconds, short enough that the ratio of two best-of-N measurements carried
    #: 16% run-to-run spread -- more than half the regression tolerance, which would make
    #: the gate flake rather than gate. Several passes make the measurement long enough
    #: to be stable without making the suite slow.
    structured_passes = 5

    def structured():
        # The successes are counted, and `main` asserts the count. Suppressing failures
        # and timing whatever is left means a change that made `parse()` raise
        # immediately would time at a fraction of the baseline and be reported as an
        # enormous *improvement*.
        count = 0
        for _ in range(structured_passes):
            for name in itanium:
                with contextlib.suppress(Exception):
                    demangle.parse(name)
                    count += 1
        parsed.append(count)

    return parsed, [
        ("cold", cold, len(everything)),
        ("warm", warm, len(everything) * 10),
        ("negative", negative, len(negatives)),
        ("structured", structured, len(itanium) * structured_passes),
    ]


def run():
    results = {}
    parsed, cases = benchmarks()
    reference = calibrate()
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
    for data in results.values():
        data["normalised"] = round(data["seconds"] / data["names"] / reference * 1e6, 3)
    results["calibration"] = {"seconds": round(reference, 6), "rounds": CALIBRATION_ROUNDS}
    return results


def report(results):
    print(f"{'benchmark':12} {'names':>8} {'sec':>9} {'names/sec':>12} {'us each':>10} {'relative':>10}")
    for name, data in results.items():
        if name == "calibration":
            continue
        print(
            f"{name:12} {data['names']:>8} {data['seconds']:>9.4f} "
            f"{data['per_second']:>12,} {data['microseconds_each']:>10.2f} {data['normalised']:>10.2f}"
        )
    print(f"\ncalibration: {results['calibration']['seconds'] * 1e3:.2f}ms for {CALIBRATION_ROUNDS:,} rounds")


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
        if "normalised" not in baseline.get("cold", {}):
            print("\nbaseline predates machine calibration; re-record it with --save")
            return 1

        def over_tolerance(measured, name):
            return name in baseline and measured[name]["normalised"] > baseline[name]["normalised"] * TOLERANCE

        suspects = [name for name in results if name != "calibration" and over_tolerance(results, name)]
        if suspects:
            # Measure again before reporting. The normalised figures carry more spread on
            # a shared machine than the tolerance leaves room for, so one reading over the
            # line is not evidence. A real regression is there on the second reading too;
            # noise usually is not. Same discipline as re-running a CI job once to
            # confirm a failure rather than to wish it away -- once, and a second failure
            # is real.
            print(f"\nover tolerance on {', '.join(suspects)}; measuring again to confirm")
            second = run()
            regressions = []
            for name in suspects:
                before = baseline[name]["normalised"]
                after = second[name]["normalised"]
                if after > before * TOLERANCE:
                    ratio = after / before
                    regressions.append(f"  {name}: {before:.2f} -> {after:.2f} ({ratio:.2f}x, machine-relative)")
                else:
                    print(f"  {name}: {second[name]['normalised']:.2f} on re-measure, within tolerance")
            if regressions:
                print("\nperformance regression, confirmed on a second measurement:")
                print("\n".join(regressions))
                return 1
        print("\nno regression against baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
