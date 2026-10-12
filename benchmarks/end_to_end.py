#!/usr/bin/env python3
"""Profile the existing corpora plus startup, streaming I/O and graph serialization.

Run with the same interpreter and alternate --source checkouts when comparing changes.
Wall-time repeats and traced memory are separate: tracing is not a timing instrument.
This is a diagnostic report, not a replacement for bench.py's portable CI gate.
"""

import argparse
import ast
import gc
import hashlib
import importlib.util
import io
import json
import os
import statistics
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path


def measure(function, repeats):
    timings = []
    for _ in range(repeats):
        gc.collect()
        start = time.perf_counter()
        function()
        timings.append(time.perf_counter() - start)
    return {
        "median_ms": round(statistics.median(timings) * 1000, 3),
        "min_ms": round(min(timings) * 1000, 3),
        "max_ms": round(max(timings) * 1000, 3),
    }


def memory(function):
    gc.collect()
    tracemalloc.start()
    function()
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"retained_bytes": retained, "peak_bytes": peak}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cprofile", type=Path, help="write cumulative/self-time profiles beside the report")
    args = parser.parse_args()
    if args.repeats < 3:
        parser.error("at least three repeats are required")
    source = args.source.resolve()
    spec = importlib.util.spec_from_file_location("bench", source / "benchmarks" / "bench.py")
    assert spec is not None and spec.loader is not None
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    import demangle

    parsed, successes, cases = bench.benchmarks()
    inputs = [
        (language, name)
        for language, corpus in (
            ("itanium", "itanium-real-world.txt"),
            ("msvc", "msvc-llvm-corpus.txt"),
            ("rust", "rust-real-world.txt"),
            ("swift", "swift-real-world.txt"),
            ("go", "go-real-world.txt"),
        )
        for name in bench.corpus_sample(corpus, 256)
    ]
    names = [name for _, name in inputs]
    # Retain trees outside the measurement so serialization and parsing stay distinguishable.
    trees = [demangle.parse(name, language=language) for language, name in inputs]
    text = "".join(f"{index:08x} T {name}\n" for index, name in enumerate(names)) * 4
    encoded = text.encode()
    environment = dict(os.environ, PYTHONPATH=str(source / "src"))

    def serialize():
        return json.dumps([tree.to_dict() for tree in trees], ensure_ascii=True, separators=(",", ":"))

    def batch():
        return list(demangle.demangle_all(names * 4))

    def stream():
        output = io.StringIO()
        demangle.demangle_stream(io.StringIO(text), output)
        return output.getvalue()

    expected_stream = stream().encode()

    def cli():
        result = subprocess.run(
            [sys.executable, "-m", "demangle"],
            input=encoded,
            capture_output=True,
            env=environment,
            cwd=source,
            check=True,
        )
        assert result.stdout == expected_stream, "CLI and library stream output differ"
        return result.stdout

    cases.extend(
        [
            ("batch_warm", batch, len(names) * 4),
            ("serialize", serialize, len(trees)),
            ("stream_warm", stream, len(names) * 4),
            ("cli_pipe", cli, len(names) * 4),
        ]
    )
    results = {}
    for name, function, count in cases:
        demangle.cache_clear()
        function()  # imports, fields and caches warm before repeated steady-state measurements
        results[name] = {"names": count, **measure(function, args.repeats)}
        if args.cprofile:
            import cProfile
            import pstats

            profiler = cProfile.Profile()
            profiler.runcall(function)
            with args.cprofile.open("a") as output:
                output.write(f"\n{name}\n")
                stats = pstats.Stats(profiler, stream=output).strip_dirs()
                stats.sort_stats("cumulative").print_stats(15)
                stats.sort_stats("tottime").print_stats(15)
        demangle.cache_clear()
        results[name].update(memory(function))
    assert parsed and all(count == results["structured"]["names"] for count in parsed)
    for phase, counts in successes.items():
        assert counts and all(count == results[phase]["names"] for count in counts), phase
    # Guard outputs when comparing reports, without putting hashing into the timing.
    results["serialize"]["sha256"] = hashlib.sha256(serialize().encode()).hexdigest()
    results["stream_warm"]["sha256"] = hashlib.sha256(stream().encode()).hexdigest()

    startup = """
import time
started = time.perf_counter()
import demangle
imported = time.perf_counter()
for name in ('_ZNSt6vectorIiSaIiEE9push_backERKi', '?f@@YAXH@Z', '_RNvC6_123foo3bar', '$s10Foundation4DataV5countSivg'):
    assert demangle.demangle(name) != name
first = time.perf_counter()
demangle.preload()
print((imported-started, first-imported, time.perf_counter()-first))
"""
    readings = [
        ast.literal_eval(
            subprocess.check_output(
                [sys.executable, "-c", startup],
                env=environment,
                cwd=source,
                text=True,
            )
        )
        for _ in range(args.repeats)
    ]
    for at, name in enumerate(("import", "first_four_languages", "preload_remaining")):
        values = [reading[at] * 1000 for reading in readings]
        results[name] = {
            "median_ms": round(statistics.median(values), 3),
            "min_ms": round(min(values), 3),
            "max_ms": round(max(values), 3),
        }
    report = {
        "python": sys.version,
        "source": str(source),
        "repeats": args.repeats,
        "startup_cache": "fresh process; filesystem/bytecode caches warm",
        "phases": results,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
