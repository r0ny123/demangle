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

`--calls` is a second instrument for the same corpora, and it exists because the clock
here cannot resolve everything worth doing. This machine's spread between runs of the
same code is 15-25% (see `PIVOT` below), so a change that removes a tenth of the work is
invisible to `--check`. The number of Python-level
calls a corpus costs is exactly reproducible on a given interpreter, and fewer calls for
the same output is strictly less work, so it reports what the clock cannot. It is a
report and not a gate: the count moves with the interpreter version as well as with this
package, and a gate that fails on someone else's Python would be worse than none.
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

#: Tolerance on a machine-independent ratio (each phase relative to `PIVOT`, the pivot
#: relative to `calibrate`), so it means a real 25% slowdown in the demangler.
TOLERANCE = 1.25

#: The phase every other phase is measured against. The calibration loop's own spread
#: (28% over six idle runs) made calibration-normalised figures noisier than raw seconds:
#:
#:     phase        /calibration   /warm    raw seconds
#:     cold             26.6%       7.1%       23.1%
#:     negative         25.7%       7.2%       25.6%
#:     structured       42.5%      24.2%       23.5%
#:
#: `warm` is the same kind of work in the same process, so boost, allocator and heap state
#: move both numbers together. `warm` itself is still judged against calibration.
PIVOT = "warm"

CALIBRATION_ROUNDS = 200_000


def calibrate():
    """Time a fixed workload that has nothing to do with demangling.

    Absolute microseconds-per-name are not comparable across machines, and a tolerance
    factor does not make them so: it only tolerates a difference up to its own size.
    A shared CI runner is comfortably 1.5-2x slower than a developer laptop, so a gate
    on raw wall time fails on the runner for reasons that have nothing to do with the
    change under test.

    So every figure is divided by this, and the result is recorded as `normalised`: the
    *ratio* between the demangler and the interpreter it is running on, which is a
    property of the code rather than of the hardware. The gate reads it for `PIVOT`
    only; every other phase is judged relative to the pivot, for the reason given at
    `PIVOT` and measured below.

    The workload is deliberately the same *kind* of work the demangler does -- string
    slicing, dictionary lookup, list building, attribute access -- so it tracks the same
    machine characteristics rather than, say, floating-point throughput. It touches no
    part of `demangle`: if it did, a genuine regression would slow the calibration too
    and hide itself.

    Measured: running the suite against three times as many busy processes as cores --
    a machine roughly 2.8x slower -- leaves the normalised figures within 8% where the
    raw ones would fail any tolerance worth having.

    What it does *not* do is track mild variation in machine state. On a quiet machine
    the calibration can drift while `structured` stays put, which shows up as a 20%
    "regression" in a figure that is a ratio. The two workloads track each other under
    load, when contention dominates both; they do not track each other when the
    difference is CPU boost state, because a tight dictionary loop benefits from it more
    than an allocating parser does. Run to run the normalised figures carry about 14%
    spread, which is over half the tolerance.

    That is why `--check` confirms a regression before reporting one, and why the
    tolerance is not tightened further. A real regression reproduces; noise mostly does
    not.

    One further caveat: `negative` runs briefly enough that
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
    # Compiled at four standards by two compilers: the widest range of node shapes per name.
    itanium = corpus_names("itanium-real-world.txt")

    # ~14,000 names, so the working set does not fit in L2 as a small sample would.
    # Changing this set means re-recording the baseline.
    sampled = corpus_names(
        "itanium-real-world.txt",
        "itanium-libstdcxx.txt",
        "reported/itanium.txt",
        "msvc-llvm-corpus.txt",
        "rust-real-world.txt",
        "rust-toolchain.txt",
        "go-real-world.txt",
    )
    negatives = [f"not_a_mangled_symbol_{index}" for index in range(len(sampled))]

    def cold():
        demangle.cache_clear()
        for name in sampled:
            demangle.demangle(name)

    def warm():
        for _ in range(3):
            for name in sampled:
                demangle.demangle(name)

    def negative():
        demangle.cache_clear()
        for name in negatives:
            demangle.demangle(name)

    parsed = []

    # One pass carries 16% run-to-run spread, too close to the tolerance.
    structured_passes = 2

    def structured():
        # Counted so `main` can reject a "speed-up" from `parse()` raising immediately.
        count = 0
        for _ in range(structured_passes):
            for name in itanium:
                with contextlib.suppress(Exception):
                    demangle.parse(name)
                    count += 1
        parsed.append(count)

    return parsed, [
        ("cold", cold, len(sampled)),
        ("warm", warm, len(sampled) * 3),
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

    # What the gate reads; see `PIVOT`.
    pivot = results.get(PIVOT)
    if pivot:
        per_name = pivot["seconds"] / pivot["names"]
        for name, data in results.items():
            if name == PIVOT:
                continue
            data["relative_to_pivot"] = round(data["seconds"] / data["names"] / per_name, 4)

    results["calibration"] = {"seconds": round(reference, 6), "rounds": CALIBRATION_ROUNDS}
    return results


def report(results):
    print(
        f"{'benchmark':12} {'names':>8} {'sec':>9} {'names/sec':>12} "
        f"{'us each':>10} {'relative':>10} {'vs ' + PIVOT:>10}"
    )
    for name, data in results.items():
        if name == "calibration":
            continue
        pivoted = data.get("relative_to_pivot")
        shown = f"{pivoted:>10.3f}" if pivoted is not None else f"{'--':>10}"
        print(
            f"{name:12} {data['names']:>8} {data['seconds']:>9.4f} "
            f"{data['per_second']:>12,} {data['microseconds_each']:>10.2f} "
            f"{data['normalised']:>10.2f} {shown}"
        )
    print(f"\ncalibration: {results['calibration']['seconds'] * 1e3:.2f}ms for {CALIBRATION_ROUNDS:,} rounds")


def calls():
    """Python-level calls per name, per phase. Deterministic; see the module docstring."""
    import cProfile
    import pstats

    counted = {}
    _, cases = benchmarks()
    for name, function, count in cases:
        if not count:
            continue
        demangle.cache_clear()
        # Warm-up pass, cache deliberately not cleared after it: `warm` depends on those
        # entries, matching the state `time_it`'s repeated runs give the timing path.
        function()
        profiler = cProfile.Profile()
        profiler.enable()
        function()
        profiler.disable()
        # `total_calls` is set by the constructor, undeclared in typeshed.
        counted[name] = (pstats.Stats(profiler).total_calls, count)  # ty: ignore[unresolved-attribute]
    return counted


def report_calls(counted):
    print(f"{'benchmark':12} {'names':>8} {'calls':>14} {'per name':>10}")
    for name, (total, count) in counted.items():
        print(f"{name:12} {count:>8} {total:>14,} {total / count:>10.1f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--save", action="store_true", help="record these numbers as the baseline")
    parser.add_argument("--check", action="store_true", help="fail if slower than the baseline")
    parser.add_argument("--calls", action="store_true", help="report calls per name instead of timing")
    arguments = parser.parse_args()

    if arguments.calls:
        report_calls(calls())
        return 0

    results = run()
    report(results)

    if arguments.save:
        BASELINE.write_text(json.dumps(results, indent=2) + "\n")
        print(f"\nbaseline written to {BASELINE}")
        return 0

    if arguments.check:
        if not BASELINE.exists():
            print("\nno baseline recorded; run with --save first")
            return 1
        baseline = json.loads(BASELINE.read_text())
        structured = results.get("structured", {})
        if structured.get("parsed") != structured.get("names"):
            print(f"\nstructured benchmark parsed {structured.get('parsed')} of {structured.get('names')} names")
            print("a timing that improved because the work stopped happening is not an improvement")
            return 1
        # A shrunken corpus would also time as an improvement.
        shrunk = [
            (name, results[name]["names"], baseline[name]["names"])
            for name in results
            if name != "calibration"
            and "names" in baseline.get(name, {})
            and results[name]["names"] != baseline[name]["names"]
        ]
        if shrunk:
            for name, measured, expected in shrunk:
                print(f"\n{name} benchmark ran over {measured} names, baseline recorded {expected}")
            print("if the corpus changed on purpose, re-record the baseline and say why")
            return 1
        if "normalised" not in baseline.get("cold", {}):
            print("\nbaseline predates machine calibration; re-record it with --save")
            return 1

        def figure(data, name):
            """The number this phase is judged on, and the one it is judged against.

            `relative_to_pivot` where there is one -- every phase but the pivot itself.
            `normalised` otherwise, which is what the pivot and any older baseline use.
            """
            if name != PIVOT and "relative_to_pivot" in data.get(name, {}):
                return "relative_to_pivot"
            return "normalised"

        def over_tolerance(measured, name):
            if name not in baseline:
                return True
            key = figure(measured, name)
            if key not in baseline[name]:
                # An older baseline carries only `normalised`.
                key = "normalised"
            return measured[name][key] > baseline[name][key] * TOLERANCE

        unknown = [name for name in results if name != "calibration" and name not in baseline]
        if unknown:
            print(f"\nno baseline for {', '.join(sorted(unknown))}; re-record it with --save")
            return 1
        suspects = [name for name in results if name != "calibration" and over_tolerance(results, name)]
        if suspects:
            # One reading over the line on a shared machine is not evidence; confirm once.
            print(f"\nover tolerance on {', '.join(suspects)}; measuring again to confirm")
            second = run()
            regressions = []
            for name in suspects:
                key = figure(second, name)
                if key not in baseline[name]:
                    key = "normalised"
                before = baseline[name][key]
                after = second[name][key]
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
