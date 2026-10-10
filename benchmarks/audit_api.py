#!/usr/bin/env python3
"""Compare warm API calls and uncached ordinary names with a fixed revision.

Run: python benchmarks/audit_api.py --before 3728106 --trials 9
Both APIs share the current parsers but use their revision's cache implementation.
This isolates lookup overhead; it does not measure parser improvements.
"""

import argparse
import gc
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from demangle import api  # noqa: E402


def baseline(revision):
    modules = {}
    for label, filename in (("api", "api.py"), ("cache", "core/cache.py")):
        module = ModuleType(f"demangle._audit_{label}")
        module.__package__ = "demangle"
        source = subprocess.check_output(["git", "show", f"{revision}:src/demangle/{filename}"], cwd=ROOT, text=True)
        exec(compile(source, f"<{revision} {filename}>", "exec"), module.__dict__)
        modules[label] = module
    old = modules["api"]
    # The separate source module has its own sentinel; the API must see the same one.
    modules["cache"].__dict__["_MISSING"] = old.MISSING
    old.__dict__["_CACHE"] = modules["cache"].BoundedCache(
        max_size=api._CACHE.max_size, max_weight=api._CACHE.max_weight, weigh=api._CACHE._weigh
    )
    return old


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", default="3728106")
    parser.add_argument("--trials", type=int, default=9)
    parser.add_argument("--repeats", type=int, default=150000)
    args = parser.parse_args()
    if args.trials < 1 or args.repeats < 1:
        parser.error("--trials and --repeats must be positive")
    old = baseline(args.before)
    api.preload()
    old.preload()
    results = {
        "baseline_commit": subprocess.check_output(["git", "rev-parse", args.before], cwd=ROOT, text=True).strip(),
        "python": sys.version,
        "trials": args.trials,
        "repeats": args.repeats,
        "units": "seconds per call",
        "workloads": {},
    }
    name = "_ZNSt6vectorIiSaIiEE9push_backERKi"
    cases = (
        ("warm_cpp", name, None),
        ("uncached_ordinary_name", "ordinary_function", None),
        ("warm_refusal", "_Znotmangled", None),
        ("warm_custom_limits", name, api.Limits(max_depth=100)),
    )
    for label, name, limits in cases:
        if limits is None:
            assert old.demangle(name) == api.demangle(name)
        else:
            assert old.demangle(name, limits=limits) == api.demangle(name, limits=limits)
        samples = [[], []]
        for trial in range(args.trials):
            for index in (0, 1) if trial % 2 == 0 else (1, 0):
                fn = (old, api)[index].demangle
                gc.collect()
                enabled = gc.isenabled()
                gc.disable()
                try:
                    start = time.perf_counter()
                    if limits is None:
                        for _ in range(args.repeats):
                            fn(name)
                    else:
                        for _ in range(args.repeats):
                            fn(name, limits=limits)
                    samples[index].append((time.perf_counter() - start) / args.repeats)
                finally:
                    if enabled:
                        gc.enable()
        before, after = map(statistics.median, samples)
        results["workloads"][label] = {
            "name": name,
            "before_samples": samples[0],
            "after_samples": samples[1],
            "before_median": before,
            "after_median": after,
            "before_range": [min(samples[0]), max(samples[0])],
            "after_range": [min(samples[1]), max(samples[1])],
            "reduction_percent": 100 * (1 - after / before),
        }
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
