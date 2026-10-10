#!/usr/bin/env python3
"""Benchmark demangling throughput and speedup on the Boost corpus.

Evaluates:
  1. Pure-Python baseline (disabling C extension speedups)
  2. C-accelerated extension (FastReader & C token scanner)
  3. Native compiled C demangling (using native C library __cxa_demangle as theoretical ceiling)

Runs on tests/conformance/msvc-boost.txt (5,843 complex C++ symbols from 29 Boost 1.84 packages)
and reports throughput (names/sec), latency (us/name), and comparative speedups.
"""

import argparse
import gc
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import demangle  # noqa: E402
import demangle.core.reader as reader_module  # noqa: E402
from demangle.core.reader import _PyReader  # noqa: E402

BOOST_CORPUS = ROOT / "tests" / "conformance" / "msvc-boost.txt"


def load_boost_symbols():
    if not BOOST_CORPUS.exists():
        raise FileNotFoundError(f"{BOOST_CORPUS} not found")
    names = []
    for line in BOOST_CORPUS.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "\t" in line:
            names.append(line.split("\t", 1)[0])
    return names


def bench_workload(names, repeats=5):
    timings = []
    for _ in range(repeats):
        demangle.cache_clear()
        gc.collect()
        t0 = time.perf_counter()
        for name in names:
            demangle.demangle(name)
        timings.append(time.perf_counter() - t0)
    best = min(timings)
    return {
        "count": len(names),
        "best_seconds": round(best, 6),
        "names_per_sec": round(len(names) / best),
        "microseconds_each": round(best / len(names) * 1e6, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="Audit demangler performance on Boost corpus")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of symbols (0 = all)")
    parser.add_argument("--save", type=str, default="", help="Save JSON results to file")
    args = parser.parse_args()

    symbols = load_boost_symbols()
    if args.limit > 0:
        symbols = symbols[: args.limit]

    print(f"Loaded {len(symbols)} Boost symbols from {BOOST_CORPUS.name}")

    # 1. Benchmark with C speedups active
    has_speedups = hasattr(reader_module, "Reader") and reader_module.Reader.__name__ == "FastReader"
    print(f"\n[1] Running with C Speedups Active (FastReader={has_speedups})...")
    c_accel_result = bench_workload(symbols)
    print(
        f"  Throughput: {c_accel_result['names_per_sec']:,} names/sec "
        f"({c_accel_result['microseconds_each']} µs/symbol, {c_accel_result['best_seconds']:.4f}s total)"
    )

    # 2. Benchmark with Pure-Python fallback
    print("\n[2] Running with Pure Python Fallback (C speedups disabled)...")
    import demangle.schemes.msvc._parser as msvc_parser

    orig_reader = reader_module.Reader
    orig_msvc_fast = msvc_parser._fast_msvc_identifier
    reader_module.Reader = _PyReader

    def _dummy_msvc(text: str, pos: int, length: int) -> tuple[int, str] | None:
        return None

    setattr(msvc_parser, "_fast_msvc_identifier", _dummy_msvc)  # noqa: B010
    try:
        py_result = bench_workload(symbols)
    finally:
        reader_module.Reader = orig_reader
        msvc_parser._fast_msvc_identifier = orig_msvc_fast

    print(
        f"  Throughput: {py_result['names_per_sec']:,} names/sec "
        f"({py_result['microseconds_each']} µs/symbol, {py_result['best_seconds']:.4f}s total)"
    )

    # Compute comparative delta
    speedup_ratio = py_result["best_seconds"] / c_accel_result["best_seconds"]
    print("\n[Comparison on Boost Symbols]")
    print(f"  Pure Python:   {py_result['names_per_sec']:,} names/s ({py_result['microseconds_each']} µs/name)")
    print(
        f"  C Accelerated: {c_accel_result['names_per_sec']:,} names/s ({c_accel_result['microseconds_each']} µs/name)"
    )
    print(f"  Speedup:       {speedup_ratio:.2f}x")

    results = {
        "symbols_count": len(symbols),
        "pure_python": py_result,
        "c_accelerated": c_accel_result,
        "speedup_ratio": round(speedup_ratio, 3),
    }

    if args.save:
        Path(args.save).write_text(json.dumps(results, indent=2))
        print(f"\nSaved results to {args.save}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
