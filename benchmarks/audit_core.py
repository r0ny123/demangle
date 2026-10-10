"""Paired AST audit benchmark: run with the repository virtualenv Python.

Compares working-tree AST machinery with an immutable revision while keeping the Itanium parser fixed.
Run on an idle machine. Nine alternating trials report medians and sample ranges;
serialized results must match exactly. Node bytes count unique retained AST objects,
not containers or allocator overhead. Use --before to choose a different baseline.
"""

import argparse
import gc
import statistics
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from demangle.core import ast  # noqa: E402
from demangle.schemes.itanium import parse  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--before", default="ed67b1433db8ae7b91ae323377cd9b437a9149ea")
parser.add_argument("--trials", type=int, default=9)
args = parser.parse_args()
if args.trials < 1:
    parser.error("--trials must be positive")
# Both versions use the same parser; only their AST machinery differs.
source = subprocess.check_output(["git", "show", f"{args.before}:src/demangle/core/ast.py"], cwd=ROOT, text=True)
before = types.ModuleType("demangle.core._audit_before")
exec(compile(source, "baseline_ast.py", "exec"), before.__dict__)
names = [
    line.split("\t", 1)[0].split("@", 1)[0]
    for line in (ROOT / "tests/conformance/itanium-libstdcxx.txt").read_text().splitlines()
    if line and not line.startswith("#") and "\t" in line
]
builders = [before.AstBuilder(), ast.AstBuilder()]
trees = [[parse(name, builder) for name in names] for builder in builders]
assert [tree.to_dict() for tree in trees[0]] == [tree.to_dict() for tree in trees[1]]
for label, group in zip(["before", "after"], trees, strict=True):
    unique = {id(node): node for tree in group for node in tree.walk()}
    print(
        label,
        "names",
        len(names),
        "unique_nodes",
        len(unique),
        "node_bytes",
        sum(sys.getsizeof(node) for node in unique.values()),
    )
comparisons = []
for module in (before, ast):
    builder = module.AstBuilder()
    comparisons.append([parse(name, builder) for name in names])
assert all(hash(left) == hash(right) for left, right in zip(trees[0], trees[1], strict=True))
results = {mode: [[], []] for mode in ["serialize", "parse", "hash", "equal"]}
for iteration in range(args.trials):
    for index in [0, 1] if iteration % 2 == 0 else [1, 0]:
        for mode in results:
            gc.collect()
            gc.disable()
            start = time.perf_counter()
            if mode == "serialize":
                for tree in trees[index]:
                    tree.to_dict()
            elif mode == "parse":
                for name in names:
                    parse(name, builders[index])
            elif mode == "hash":
                for tree in trees[index]:
                    hash(tree)
            else:
                for left, right in zip(trees[index], comparisons[index], strict=True):
                    assert left == right
            elapsed = time.perf_counter() - start
            gc.enable()
            results[mode][index].append(elapsed)
for mode, samples in results.items():
    print(mode)
    for label, values in zip(["before", "after"], samples, strict=True):
        print(label, "median", statistics.median(values), "min", min(values), "max", max(values), "samples", values)
    print("change_percent", (statistics.median(samples[1]) / statistics.median(samples[0]) - 1) * 100)
