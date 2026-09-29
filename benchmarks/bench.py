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
invisible to `--check`: six interleaved before/after runs of one such change put both
sides at 75-77us per name with 63-82us of noise around them. The number of Python-level
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

#: A regression has to be this much worse than the baseline to fail, so ordinary
#: run-to-run noise does not redden CI. It is a tolerance on a machine-independent ratio
#: -- each phase relative to `PIVOT`, and the pivot itself relative to `calibrate` -- so
#: it means a real 25% slowdown in the demangler, not 25% of the difference between two
#: machines.
TOLERANCE = 1.25

#: The phase every other phase is measured against, and the reason the gate is worth
#: trusting. Dividing by `calibrate()` -- a tight dictionary loop -- was supposed to
#: cancel machine speed, and does not: measured over six runs on an idle machine, the
#: calibration loop's *own* spread is 28.1% (22.1ms to 29.9ms), which makes it the
#: noisiest single component of the measurement. Dividing by it therefore injects noise
#: rather than removing it, and the normalised figures come out *less* stable than the
#: raw seconds they were derived from:
#:
#:     phase        /calibration   /warm    raw seconds
#:     cold             26.6%       7.1%       23.1%
#:     negative         25.7%       7.2%       25.6%
#:     structured       42.5%      24.2%       23.5%
#:
#: `warm` cancels what calibration cannot because it is the same *kind* of work in the
#: same process -- `demangle()` over cached names -- so CPU boost state, allocator state
#: and heap layout move both numbers together. A tight dictionary loop benefits from boost
#: more than an allocating parser does, which is precisely the failure the docstring on
#: `calibrate` already described without drawing the conclusion.
#:
#: `warm` itself is still judged against calibration; there is no third workload to judge
#: it by, and a regression confined to the cached path alone would have to be read off the
#: raw microseconds. That is the one blind spot, and it is a smaller one than gating four
#: phases on a reference 28% noisier than the thing it normalises.
PIVOT = "warm"

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
    # `structured` stays on the purpose-built Itanium corpus: it is the one compiled at
    # four language standards by two compilers, so it exercises the widest range of node
    # shapes per name.
    itanium = corpus_names("itanium-real-world.txt")

    # A representative subset across four schemes, not a couple of small ones. The
    # earlier selection was 887 names -- about 40KB of text and a few hundred KB of
    # cache entries -- which fits in L2 on any machine this runs on. That flatters
    # the whole measurement: it is the shape of a microbenchmark, not of a tool
    # walking a symbol table, where the working set is tens of thousands of distinct
    # names and nothing stays resident. These seven files give ~14,000 names; the
    # conformance directory holds more, but widening this set means re-recording
    # the baseline, so that is a deliberate change rather than a drive-by.
    sampled = corpus_names(
        "itanium-real-world.txt",
        "itanium-libstdcxx.txt",
        "itanium-regressions.txt",
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

    #: How many times `structured` walks its corpus. One pass is about twenty
    #: milliseconds, short enough that the ratio of two best-of-N measurements carried
    #: 16% run-to-run spread -- more than half the regression tolerance, which would make
    #: the gate flake rather than gate. Several passes make the measurement long enough
    #: to be stable without making the suite slow.
    structured_passes = 2

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

    # And again against `warm`, which is what the gate actually reads. See `PIVOT`.
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
        # One pass first, and the cache is *not* cleared after it. `cold` and `negative`
        # clear their own inside, so they are unaffected; `warm` is the phase whose whole
        # premise is the entries the pass before it left, and clearing here counted a
        # third of it as misses -- 120 calls a name where a cache hit costs eight. The
        # timing path gets the same state from `time_it` running the function five times.
        function()
        profiler = cProfile.Profile()
        profiler.enable()
        function()
        profiler.disable()
        # `total_calls` is set on the instance by `Stats.get_top_level_stats`, which the
        # constructor calls; typeshed declares neither, so a checker cannot see it.
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
        # Two guards, and the first is the one that matters. `parsed` must equal the
        # number of names the pass was given: a change that made `parse()` raise
        # immediately would time at a fraction of the baseline and be reported as an
        # enormous improvement, and this catches that within the run rather than against
        # a record that goes stale every time the corpus is edited.
        if structured.get("parsed") != structured.get("names"):
            print(f"\nstructured benchmark parsed {structured.get('parsed')} of {structured.get('names')} names")
            print("a timing that improved because the work stopped happening is not an improvement")
            return 1
        # The second catches the corpus itself shrinking, which would do the same thing
        # more quietly. A deliberate change to it is a deliberate edit to the baseline.
        #
        # Asked of *every* phase, not only `structured`. It used to be asked of that one
        # alone, and the cold corpus quietly lost four names without anything noticing --
        # which is the whole failure this guard exists for, in the phase the headline
        # figure comes from.
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
                # An older baseline carries only `normalised`. Fall back to it rather
                # than skipping the phase, so a stale file still gates something.
                key = "normalised"
            return measured[name][key] > baseline[name][key] * TOLERANCE

        unknown = [name for name in results if name != "calibration" and name not in baseline]
        if unknown:
            print(f"\nno baseline for {', '.join(sorted(unknown))}; re-record it with --save")
            return 1
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
