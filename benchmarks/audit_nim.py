"""Paired Nim parser timings against the immutable audit baseline.

Run: python benchmarks/audit_nim.py --output /tmp/audit-nim.json
Each sample times the same uncached parser workload, alternating old/new order.
"""

import argparse
import json
import statistics
import subprocess
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARSER = "src/demangle/schemes/nim/_parser.py"


def load_parser(source, label):
    module = types.ModuleType(label)
    exec(compile(source, label, "exec"), module.__dict__)
    return module


def verdict(module, name):
    try:
        symbol = module.parse_nim_symbol(name)
    except module.DemangleFailure:
        return None
    return symbol.text, symbol.kind, symbol.name, symbol.module


def timed(module, names, iterations):
    start = time.perf_counter()
    for _ in range(iterations):
        for name in names:
            verdict(module, name)
    return (time.perf_counter() - start) / (iterations * len(names))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", "--baseline-ref", dest="baseline_ref", default="0f56a94")
    parser.add_argument("--samples", type=int, default=9)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    baseline = subprocess.check_output(["git", "show", f"{args.baseline_ref}:{PARSER}"], cwd=ROOT, text=True)
    old = load_parser(baseline, "baseline_nim")
    new = load_parser((ROOT / PARSER).read_text(), "current_nim")
    workloads = {
        "ordinary_types": (
            ["tyObject_Widget__uq9ciTN8EVx1oU8m5EUfmRA", "tySequence__abcdefghijklmnop"],
            3000,
        ),
        "ordinary_type_info": (["NTIstring__77mFvmsOLKik79ci2hXkHEg_"], 3000),
        "invalid_type_4000_separators": (["tyObject_" + "_" * 4000 + "!"], 3),
        "invalid_type_info_4000_separators": (["NTIfoo" + "_" * 4000 + "!_"], 3),
        "valid_type_info_4000_separators": (["NTIfoo" + "_" * 4000 + "!__hash_"], 3),
    }
    results = {
        "baseline_ref": args.baseline_ref,
        "baseline_commit": subprocess.check_output(
            ["git", "rev-parse", args.baseline_ref], cwd=ROOT, text=True
        ).strip(),
        "samples": args.samples,
        "units": "seconds per uncached parse",
        "workloads": {},
    }
    for label, (names, iterations) in workloads.items():
        for name in names:
            assert verdict(old, name) == verdict(new, name), name
        samples = {"before": [], "after": []}
        for sample in range(args.samples):
            order = [("before", old), ("after", new)]
            if sample % 2:
                order.reverse()
            for key, module in order:
                samples[key].append(timed(module, names, iterations))
        before = statistics.median(samples["before"])
        after = statistics.median(samples["after"])
        results["workloads"][label] = {
            "names": names,
            "iterations": iterations,
            "raw_samples": samples,
            "before_median": before,
            "after_median": after,
            "before_range": [min(samples["before"]), max(samples["before"])],
            "after_range": [min(samples["after"]), max(samples["after"])],
            "reduction_percent": (1 - after / before) * 100,
            "speedup": before / after,
        }
    output = json.dumps(results, indent=2) + "\n"
    if args.output:
        args.output.write_text(output)
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
