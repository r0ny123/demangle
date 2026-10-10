"""Paired graph hash/equality benchmark against the immutable first audit revision.

The Rust tree stores generics in both ``parts`` and ``base``/``arguments``. This
workload uses those public node constructors to create a small-output shared graph.
Run on an idle machine; the corpus benchmark audit_core.py covers ordinary trees.
"""

import argparse
import cProfile
import gc
import statistics
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from demangle.schemes.rust import nodes as current  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--before", default="0f56a94")
parser.add_argument("--trials", type=int, default=9)
parser.add_argument("--depth", type=int, default=12)
parser.add_argument("--repeats", type=int, default=10)
args = parser.parse_args()
if args.trials < 1 or args.repeats < 1 or not 1 <= args.depth <= 18:
    parser.error("positive trials/repeats and depth between 1 and 18 required")
source = subprocess.check_output(["git", "show", f"{args.before}:src/demangle/core/ast.py"], cwd=ROOT, text=True)
baseline_ast = types.ModuleType("demangle.core._audit_before")
exec(compile(source, "baseline_ast.py", "exec"), baseline_ast.__dict__)
baseline = types.ModuleType("demangle.schemes.rust._audit_before")
baseline.__dict__.update(Node=baseline_ast.Node, rendered=baseline_ast.rendered)
source = (ROOT / "src/demangle/schemes/rust/nodes.py").read_text()
source = source.replace("from ...core.ast import Node, rendered", "")
exec(compile(source, "baseline_rust_nodes.py", "exec"), baseline.__dict__)


def chain(module):
    node = module.RustName(("Base",))
    for _ in range(args.depth):
        node = module.Generics((node, "<>"), node, ())
    return node


pairs = [(chain(module), chain(module)) for module in (baseline, current)]
assert hash(pairs[0][0]) == hash(pairs[1][0])
for left, right in pairs:
    assert left == right
    assert left.render() == right.render()
print("depth", args.depth, "distinct_nodes", args.depth + 1, "rendered_characters", pairs[0][0].size)
results = {mode: [[], []] for mode in ("hash", "equal")}
for index, (left, right) in enumerate(pairs):
    profile = cProfile.Profile()
    profile.enable()
    hash(left)
    assert left == right
    profile.disable()
    print("before" if index == 0 else "after", "python_calls", sum(entry.callcount for entry in profile.getstats()))
for trial in range(args.trials):
    for index in (0, 1) if trial % 2 == 0 else (1, 0):
        left, right = pairs[index]
        for mode in results:
            gc.collect()
            gc.disable()
            start = time.perf_counter()
            for _ in range(args.repeats):
                if mode == "hash":
                    hash(left)
                else:
                    assert left == right
            elapsed = (time.perf_counter() - start) / args.repeats
            gc.enable()
            results[mode][index].append(elapsed)
for mode, samples in results.items():
    print(mode)
    for label, values in zip(("before", "after"), samples, strict=True):
        print(label, "median", statistics.median(values), "range", (min(values), max(values)), "samples", values)
    print("change_percent", (statistics.median(samples[1]) / statistics.median(samples[0]) - 1) * 100)
