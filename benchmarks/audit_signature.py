"""Paired signature audit using the same parser and corpus in both versions.

Uses the source-module comparison protocol of audit_api.py/audit_core.py. Alternating
trials reduce process/runner drift; full field parity gates every retained result.
"""

import argparse
import dataclasses
import gc
import json
import statistics
import subprocess
import sys
import time
import tracemalloc
import types
from pathlib import Path

import bench

from demangle import _signature
from demangle.core.ast import Function, Name, Pointer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", default="03bd234e6684a7b42672bd962da3f21cfa3e1adb")
    parser.add_argument("--trials", type=int, default=9)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.trials < 3:
        parser.error("at least three trials are required")
    source = subprocess.check_output(
        ["git", "show", f"{args.before}:src/demangle/_signature.py"], cwd=bench.ROOT, text=True
    )
    old = types.ModuleType("demangle._signature")
    old.__package__ = "demangle"
    exec(compile(source, "baseline_signature.py", "exec"), old.__dict__)
    groups = {
        language: [(language, name) for name in bench.corpus_sample(corpus, 256)]
        for language, corpus in (
            ("itanium", "itanium-real-world.txt"),
            ("msvc", "msvc-llvm-corpus.txt"),
            ("rust", "rust-real-world.txt"),
            ("go", "go-real-world.txt"),
            ("swift", "swift-real-world.txt"),
        )
    }
    groups["mixed"] = [item for group in groups.values() for item in group]
    results = {}
    for group, inputs in groups.items():
        assert [dataclasses.asdict(old.signature(name, language=language)) for language, name in inputs] == [
            dataclasses.asdict(_signature.signature(name, language=language)) for language, name in inputs
        ], group
        samples = {"before": [], "after": []}
        functions = [("before", old.signature), ("after", _signature.signature)]
        for trial in range(args.trials):
            for label, function in functions if trial % 2 else reversed(functions):
                gc.collect()
                start = time.perf_counter()
                for language, name in inputs:
                    function(name, language=language)
                samples[label].append((time.perf_counter() - start) * 1000)
        medians = {label: statistics.median(values) for label, values in samples.items()}
        results[group] = {
            "names": len(inputs),
            "samples_ms": samples,
            "medians_ms": medians,
            "change_percent": 100 * (medians["after"] / medians["before"] - 1),
        }
    graphs = {}
    for depth in (40, 120, 400):
        parameter = Name("item")
        for _ in range(depth):
            parameter = Pointer(parameter)
        tree = Function(None, (parameter,) * 32, name=Name("f"))
        peaks = {}
        fields = []
        for label, module in (("before", old), ("after", _signature)):
            gc.collect()
            tracemalloc.start()
            result = module._extract(module._Reading("itanium", "::", "gnu"), tree)
            peaks[label] = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            fields.append(dataclasses.asdict(result))
        assert fields[0] == fields[1], depth
        graphs[depth] = {"shared_parameters": 32, "peak_bytes": peaks}
    args.output.write_text(
        json.dumps(
            {
                "baseline": args.before,
                "python": sys.version,
                "trials": args.trials,
                "shared_current_parsers": True,
                "groups": results,
                "constructed_graphs": graphs,
            },
            indent=2,
        )
        + "\n"
    )
    print(args.output)


if __name__ == "__main__":
    main()
