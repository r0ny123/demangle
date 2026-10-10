#!/usr/bin/env python3
"""Compare Rust parsing with a specified original revision.

Run from a checkout containing the proposed changes:
    python benchmarks/audit_rust.py --repeats 10 --trials 7
    python benchmarks/audit_rust.py --grammar v0 --before 0f56a94 --repeats 10 --trials 7

Corpus timings exercise compiler-emitted symbols. Stress cases exercise valid long
paths, escaped identifiers, and refusal of an oversized length within default input
limits; they do not estimate the distribution of symbols in a production workload.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from demangle.schemes.rust._legacy import LegacyDemangler  # noqa: E402
from demangle.schemes.rust._v0 import V0Demangler  # noqa: E402


def baseline(revision, grammar):
    filename = "_legacy" if grammar == "legacy" else "_v0"
    source = subprocess.run(
        ["git", "show", f"{revision}:src/demangle/schemes/rust/{filename}.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    module = ModuleType("demangle.schemes.rust._audit_baseline")
    module.__package__ = "demangle.schemes.rust"
    exec(compile(source, f"<{revision} Rust legacy parser>", "exec"), module.__dict__)
    return module.LegacyDemangler if grammar == "legacy" else module.V0Demangler


def outcome(parser, name):
    try:
        return (True, parser().demangle(name, 65536))
    except Exception as error:
        if type(error).__name__ not in {
            "UnableToLegacyDemangle",
            "UnableTov0Demangle",
            "RecursedTooDeep",
            "OutputTooLong",
        }:
            raise
        return (False, None)


def main():
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "--before", default="ed67b1433db8ae7b91ae323377cd9b437a9149ea", help="baseline revision"
    )
    argument_parser.add_argument("--profile", action="store_true", help="also count calls over real-world names")
    argument_parser.add_argument("--grammar", choices=("legacy", "v0"), default="legacy")
    argument_parser.add_argument("--repeats", type=int, default=10, help="iterations per timing sample")
    argument_parser.add_argument("--trials", type=int, default=7, help="paired samples per workload")
    arguments = argument_parser.parse_args()
    if arguments.repeats < 1 or arguments.trials < 1:
        argument_parser.error("--repeats and --trials must be positive")
    parsers = (
        baseline(arguments.before, arguments.grammar),
        LegacyDemangler if arguments.grammar == "legacy" else V0Demangler,
    )
    names = []
    for path in sorted((ROOT / "tests" / "conformance").glob("*rust*.txt")):
        for line in path.read_text().splitlines():
            if line and not line.startswith("#"):
                name = line.split("\t")[0]
                prefixes = ("_ZN", "__ZN", "ZN") if arguments.grammar == "legacy" else ("_R", "__R", "R")
                if name.startswith(prefixes):
                    names.append(name)
    workloads = {
        "corpus": (names, arguments.repeats),
        "16000_components": (["_ZN" + "1a" * 16000 + "17h0123456789abcdefE"], arguments.repeats),
        "14000_dot_segments": (["_ZN42004" + "a.." * 14000 + "$LT$E"], arguments.repeats),
        "14000_escapes": (["_ZN56000" + "$LT$" * 14000 + "E"], arguments.repeats),
        # A single call already takes hundreds of milliseconds before the fix.
        "60000_digit_invalid_length": (["_ZN" + "9" * 60000 + "E"], 1),
    }
    if arguments.grammar == "v0":
        workloads = {
            "corpus": (names, arguments.repeats),
            "60000_base62_digits": (["_RCs" + "z" * 60000 + "_1a"], 1),
            "60000_decimal_digits": (["_RC" + "9" * 60000 + "a"], 1),
            "60000_padded_base62_digits": (["_RCs" + "0" * 60000 + "_1a"], arguments.repeats),
            "60000_invalid_punycode_digits": (["_RCu60000_" + "9" * 60000], 1),
        }
    for label, (inputs, repeats) in workloads.items():
        for name in inputs:
            before, after = (outcome(parser, name) for parser in parsers)
            if before != after:
                raise AssertionError((name, before, after))
        samples: list[list[float]] = [[], []]
        for trial in range(arguments.trials):
            # Alternate order to reduce consistent warm-up/order bias.
            for index in (trial % 2, 1 - trial % 2):
                started = time.perf_counter()
                for _ in range(repeats):
                    for name in inputs:
                        outcome(parsers[index], name)
                milliseconds = (time.perf_counter() - started) * 1000 / repeats / len(inputs)
                samples[index].append(milliseconds)
        summaries = [
            {"median": statistics.median(sample), "min": min(sample), "max": max(sample), "samples": sample}
            for sample in samples
        ]
        print(
            json.dumps(
                {
                    "baseline_revision": arguments.before,
                    "grammar": arguments.grammar,
                    "workload": label,
                    "names": len(inputs),
                    "repeats": repeats,
                    "parity": True,
                    "milliseconds_per_name": dict(zip(("before", "after"), summaries, strict=True)),
                    "time_reduction_percent": 100 * (1 - statistics.median(samples[1]) / statistics.median(samples[0])),
                }
            ),
            flush=True,
        )

    if arguments.profile:
        import cProfile
        import pstats

        prefixes = ("_ZN", "__ZN", "ZN") if arguments.grammar == "legacy" else ("_R", "__R", "R")
        real_names = [
            line.split("\t")[0]
            for line in (ROOT / "tests" / "conformance" / "rust-real-world.txt").read_text().splitlines()
            if line.startswith(prefixes)
        ]
        counts = {}
        for label, parser in zip(("before", "after"), parsers, strict=True):

            def run(parser=parser):
                for name in real_names:
                    parser().demangle(name, 65536)

            profiler = cProfile.Profile()
            profiler.runcall(run)
            stats = pstats.Stats(profiler)
            # pstats populates these counters at runtime; its stubs omit them.
            total_calls = stats.total_calls  # ty: ignore[unresolved-attribute]
            primitive_calls = stats.prim_calls  # ty: ignore[unresolved-attribute]
            entries = stats.stats  # ty: ignore[unresolved-attribute]
            counts[label] = {
                "total_calls": total_calls,
                "primitive_calls": primitive_calls,
                "constructor_calls": sum(value[1] for key, value in entries.items() if key[2] == "__init__"),
            }
        print(json.dumps({"profile_names": len(real_names), "profile_calls": counts}), flush=True)


if __name__ == "__main__":
    main()
