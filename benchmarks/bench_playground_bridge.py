"""Compare the previous and cached-row Python playground batch serializers.

Run from the repository root:

    .venv/bin/python benchmarks/bench_playground_bridge.py --iterations 51

Both paths use the same parser in this checkout. The previous path mirrors the
main03bd234 bridge: it serializes each row to count bytes, then serializes the
outer object. The current path reuses one encoded row per distinct symbol.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import statistics
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SPEC = importlib.util.spec_from_file_location("playground_bridge_bench", ROOT / "playground" / "playground_bridge.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load playground bridge")
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


def previous_batch(symbols: str, language: str | None = None, style: str = "llvm") -> str:
    lines = symbols.split("\n")
    if len(lines) > bridge.MAX_BATCH_LINES or len(symbols.encode("utf-8")) > bridge.MAX_BATCH_BYTES:
        raise ValueError("Batch input is limited to 500 lines and 256 KiB. Split it into smaller batches.")
    cache = {}
    rows = []
    output_bytes = 0
    for line in lines:
        symbol = line.strip()
        if symbol and output_bytes >= bridge.MAX_BATCH_OUTPUT_BYTES:
            row = {
                "symbol": symbol,
                "state": "limit",
                "success": False,
                "empty": False,
                "language": None,
                "error": "Batch output exceeded 4 MiB. Split the input into smaller batches.",
                "output_variants": {
                    "full": symbol,
                    "both": f"{symbol} ==> {symbol}",
                    "base-name": symbol,
                    "no-params": symbol,
                },
            }
        else:
            if symbol not in cache:
                result = bridge._demangle(symbol, language, style, include_ast=False)
                cache[symbol] = {
                    key: result[key]
                    for key in ("symbol", "state", "success", "empty", "language", "error", "output_variants")
                }
            row = cache[symbol]
        output_bytes += len(json.dumps(row).encode("utf-8"))
        rows.append(row)
    return json.dumps({"batch": True, "rows": rows})


def _memory_sample(function, symbols: str) -> dict[str, int]:
    function(symbols)
    tracemalloc.start()
    result = function(symbols)
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "peak_python_alloc_bytes": peak_bytes,
        "json_bytes": len(result.encode("utf-8")),
    }


def _revision() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _working_tree_dirty() -> bool | None:
    try:
        result = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True)
        return bool(result.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=51)
    args = parser.parse_args()
    if args.iterations < 3:
        parser.error("--iterations must be at least 3")

    cases = {
        "duplicate_valid": "_ZN3foo3barEi\n" * 499 + "_ZN3foo3barEi",
        "unique_plain": "\n".join(f"plain_symbol_{index:04}" for index in range(500)),
        "blank_lines": "\n" * 499,
        "mixed_repeated": "\n".join(["_ZN3foo3barEi", "_ZN3foo3bazEv", "plain_symbol", "_Z", "λ"] * 100),
    }
    results = {}
    for name, symbols in cases.items():
        previous = previous_batch(symbols)
        current = bridge.playground_batch(symbols)
        if previous != current:
            raise AssertionError(f"serialized output changed for {name}")

        previous_times = []
        current_times = []
        for iteration in range(args.iterations):
            ordered = ((previous_batch, previous_times), (bridge.playground_batch, current_times))
            if iteration % 2:
                ordered = tuple(reversed(ordered))
            for function, samples in ordered:
                started = time.perf_counter_ns()
                function(symbols)
                samples.append((time.perf_counter_ns() - started) / 1_000_000)

        previous_metrics = _memory_sample(previous_batch, symbols)
        current_metrics = _memory_sample(bridge.playground_batch, symbols)
        previous_ordered = sorted(previous_times)
        current_ordered = sorted(current_times)
        previous_metrics.update(
            {
                "median_ms": round(statistics.median(previous_times), 3),
                "p95_ms": round(
                    previous_ordered[min(len(previous_ordered) - 1, int((len(previous_ordered) - 1) * 0.95))], 3
                ),
            }
        )
        current_metrics.update(
            {
                "median_ms": round(statistics.median(current_times), 3),
                "p95_ms": round(
                    current_ordered[min(len(current_ordered) - 1, int((len(current_ordered) - 1) * 0.95))], 3
                ),
            }
        )
        results[name] = {
            "lines": 500,
            "input_utf8_bytes": len(symbols.encode("utf-8")),
            "previous_main03bd234": previous_metrics,
            "current": current_metrics,
            "median_change_percent": round((current_metrics["median_ms"] / previous_metrics["median_ms"] - 1) * 100, 1),
        }

    print(
        json.dumps(
            {
                "baseline_revision": "03bd234",
                "measured_revision": _revision(),
                "measured_working_tree_dirty": _working_tree_dirty(),
                "python": platform.python_version(),
                "iterations": args.iterations,
                "timing_method": "alternating paired samples; medians and p95 from paired runs",
                "output_byte_identity": True,
                "cases": results,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
