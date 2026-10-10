"""Paired stream throughput and JSON retention audit against a historical revision.

Both stream implementations use current parsers: this isolates filtering/caching.
Run with the repository virtualenv: python benchmarks/audit_stream.py.
"""

import argparse
import contextlib
import io
import json
import statistics
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root / "src"))
import demangle  # noqa: E402
from demangle import cli  # noqa: E402
from demangle import filter as current  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--before", default="0f56a94")
parser.add_argument("--repeats", type=int, default=9)
args = parser.parse_args()
if args.repeats < 1:
    parser.error("--repeats must be positive")


def before(file):
    name = "demangle._audit_" + file
    source = subprocess.check_output(["git", "show", f"{args.before}:src/demangle/{file}.py"], cwd=root, text=True)
    module = type(sys)(name)
    module.__package__ = "demangle"
    sys.modules[name] = module
    exec(compile(source, name, "exec"), module.__dict__)
    return module


old_filter, old_cli = before("filter"), before("cli")
old_cli._TOKEN = old_filter.TOKEN
old_cli._TOKEN_MUST_HOLD = old_filter.TOKEN_MUST_HOLD
name = "_ZNSt6vectorIiSaIiEE9push_backERKi"
demangle.demangle(name)
workloads = {
    "plain_logs": ["2026-10-09 12:34:56 INFO worker completed request from localhost in 12 ms\n"] * 10000,
    "nm_symbols": [f"0000000000001139 T {name}\n"] * 10000,
    "disassembly": [f"    104a: e8 ab cd 00 00 call 1090 <{name}>\n"] * 10000,
    "mixed_logs": (["2026-10-09 INFO completed processing request\n"] * 9 + [f"frame 1 {name}\n"]) * 1000,
}


class Sink:
    def write(self, text):
        return len(text)

    def flush(self):
        pass


def library(module, lines, destination=None):
    module.demangle_stream(iter(lines), Sink() if destination is None else destination)


def command(module, lines, options=(), destination=None):
    args = module.build_parser().parse_args(options)
    with contextlib.redirect_stdout(Sink() if destination is None else destination):
        module._run_stream([lines], args, module._answerer(args, demangle.DEFAULT_LIMITS))


def json_command(module, lines, destination=None):
    return command(module, lines, ["--json-lines"], destination)


result = {}
for path, fn, old, new in [
    ("library", library, old_filter, current),
    ("cli", command, old_cli, cli),
    ("json_cli", json_command, old_cli, cli),
]:
    for label, lines in workloads.items():
        captured = []
        for module in (old, new):
            destination = io.StringIO()
            fn(module, lines, destination=destination)
            captured.append(destination.getvalue())
        assert captured[0] == captured[1], (path, label)
        del captured
        values = {"before": [], "after": []}
        for run in range(args.repeats):
            order = [("before", old), ("after", new)] if run % 2 == 0 else [("after", new), ("before", old)]
            for key, module in order:
                start = time.perf_counter()
                fn(module, lines)
                values[key].append(time.perf_counter() - start)
        med = {key: statistics.median(v) for key, v in values.items()}
        result[path + "/" + label] = {
            **values,
            "median": med,
            "percent_faster": 100 * (1 - med["after"] / med["before"]),
        }
for key, module in [("before", old_cli), ("after", cli)]:
    tracemalloc.start()
    answer = module._answerer(module.build_parser().parse_args(["--json-lines"]), demangle.DEFAULT_LIMITS)
    for index in range(256):
        answer(str(index) + "x" * (1 << 16))
    retained, peak = tracemalloc.get_traced_memory()
    result["json_retention/" + key] = {
        "retained_bytes": retained,
        "peak_bytes": peak,
        "records": 256,
        "name_length": 65536,
    }
    tracemalloc.stop()
    del answer
print(json.dumps({"baseline": args.before, "shared_current_parsers": True, "results": result}, indent=2))
