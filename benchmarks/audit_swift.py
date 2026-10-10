"""Paired Swift parser timings against the immutable audit baseline.

Run: python benchmarks/audit_swift.py --output /tmp/audit-swift.json
Each sample times the same uncached parser workload, alternating old/new order.
"""

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARSER = "src/demangle/schemes/swift/_demangler.py"


def load_parser(source, label):
    module = types.ModuleType(label)
    module.__package__ = "demangle.schemes.swift"
    exec(compile(source, label, "exec"), module.__dict__)
    return module


def verdict(module, name):
    from demangle.schemes.swift._printer import print_root

    root = module.demangle_symbol(name)
    return print_root(root) if root is not None else None


def timed(module, names, iterations):
    start = time.perf_counter()
    for _ in range(iterations):
        for name in names:
            verdict(module, name)
    return (time.perf_counter() - start) / (iterations * len(names))


def allocation_footprint(before_ref, old, new):
    legacy_path = "src/demangle/schemes/swift/_old_demangler.py"
    old_legacy = load_parser(
        subprocess.check_output(["git", "show", f"{before_ref}:{legacy_path}"], cwd=ROOT, text=True),
        "baseline_old_swift",
    )
    new_legacy = load_parser((ROOT / legacy_path).read_text(), "current_old_swift")
    results = {}
    for label, before, after, name in (
        ("modern", old.Demangler, new.Demangler, "$s4demo3fooyyF"),
        ("legacy", old_legacy.OldDemangler, new_legacy.OldDemangler, "_TtC4demo3Foo"),
    ):
        # Warm the shared-key dictionaries to their steady instance allocation size.
        for _ in range(100):
            before(name)
            after(name)
        baseline, current = before(name), after(name)
        before_size = sys.getsizeof(baseline) + sys.getsizeof(vars(baseline))
        after_size = sys.getsizeof(current)
        results[label] = {
            "before_instance_and_dict_bytes": before_size,
            "after_slotted_instance_bytes": after_size,
            "reduction_percent": (1 - after_size / before_size) * 100,
            "excludes": "referenced strings, lists and nodes",
        }
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", "--baseline-ref", dest="baseline_ref", default="3728106")
    parser.add_argument("--samples", type=int, default=9)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    baseline = subprocess.check_output(["git", "show", f"{args.baseline_ref}:{PARSER}"], cwd=ROOT, text=True)
    old = load_parser(baseline, "baseline_swift")
    new = load_parser((ROOT / PARSER).read_text(), "current_swift")
    names = []
    for line in (ROOT / "tests/conformance/swift-real-world.txt").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name = line.split("\t", 1)[0]
        if name.startswith("$s"):
            names.append(name)
        if len(names) == 128:
            break
    workloads = {
        "ordinary_symbols": (["$s4demo3fooyyF", "$s4demo3FooV"], 3000),
        "runtime_symbols": (names, 3),
    }
    results = {
        "baseline_ref": args.baseline_ref,
        "baseline_commit": subprocess.check_output(
            ["git", "rev-parse", args.baseline_ref], cwd=ROOT, text=True
        ).strip(),
        "samples": args.samples,
        "python": platform.python_version(),
        "executable": sys.executable,
        "platform": platform.platform(),
        "allocation_footprint": allocation_footprint(args.baseline_ref, old, new),
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
