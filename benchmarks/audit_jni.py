"""Paired JNI parser timings against the immutable audit baseline.

Run: python benchmarks/audit_jni.py --output /tmp/audit-jni.json
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
PARSER = "src/demangle/schemes/jni/_parser.py"


def load_parser(source, label):
    module = types.ModuleType(label)
    module.__package__ = "demangle.schemes.jni"
    exec(compile(source, label, "exec"), module.__dict__)
    return module


def verdict(module, name):
    try:
        symbol = module.parse_jni_symbol(name)
    except module.DemangleFailure:
        return None
    return symbol.text, symbol.declaring, symbol.method, symbol.parameters


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
    old = load_parser(baseline, "baseline_jni")
    new = load_parser((ROOT / PARSER).read_text(), "current_jni")
    workloads = {
        "ordinary_names": (
            ["Java_pkg_Class_method", "Java_java_lang_String_indexOf__I"],
            3000,
        ),
        "unicode_path_1000_candidates": (["Java_pkg" + "__003c0" * 1000 + "_method"], 3),
        "invalid_1000_empty_components": (["Java_pkg_Class" + "_" * 1000 + "method"], 3),
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
