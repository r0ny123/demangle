#!/usr/bin/env python3
"""Build a conformance corpus from real compiler output.

Hand-written test cases encode what the author already thought of. A corpus compiled
from real source with real toolchains encodes what compilers actually emit, which is a
much larger and stranger set: ABI tags, lambda numbering, cloned functions, thunks,
guard variables, internal-linkage names, and the many spellings that only appear at
certain optimisation levels.

Each source is compiled by every available compiler at several language standards and
optimisation levels, symbols are read out of the object files, and the reference
demangler's output is recorded next to each name. The result is checked in, so the test
suite needs neither a compiler nor the reference binaries to run.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCES = HERE / "corpus_sources"

#: Names on which the reference demangler is wrong; excluded here so they are recorded only
#: in this hand-maintained file.
REFERENCE_DEFECTS = ROOT / "tests" / "conformance" / "itanium-reference-defects.txt"

COMPILERS = ("clang++", "g++")
STANDARDS = ("c++11", "c++14", "c++17", "c++20", "c++23")
OPTIMISATIONS = ("-O0", "-O2")


def run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, **kwargs)


def compile_source(compiler, source, standard, optimisation, out_dir, target=None):
    """Compile one source, returning the object file path or None."""
    tag = f"-{target}" if target else ""
    output = out_dir / f"{source.stem}-{compiler}{tag}-{standard}-{optimisation.lstrip('-')}.o"
    command = [compiler, f"-std={standard}", optimisation, "-c", str(source), "-o", str(output)]
    if target:
        # Only clang can be asked for another ABI, and it is the only compiler that can
        # produce an MSVC-mangled object on this box.
        command.insert(1, f"--target={target}")
    result = run(command)
    return output if result.returncode == 0 else None


def symbols_of(object_file):
    """Every symbol name in an object file, mangled ones included."""
    result = run(["nm", "--no-demangle", "-a", str(object_file)])
    if result.returncode != 0:
        return set()
    found = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if parts:
            found.add(parts[-1])
    return found


def reference_output(tool, names):
    """Ask a reference demangler for its spelling of each name, in one batch.

    One process for the whole corpus rather than one per name: with tens of thousands
    of symbols the process spawn dominates everything else.

    The two references do not agree on output format. `llvm-cxxfilt` and `c++filt`
    write one line per name. `llvm-undname` writes three -- it echoes the input, then
    the result, then a blank line -- so reading its output as one-line-per-name silently
    pairs every name with the wrong answer.
    """
    if not shutil.which(tool):
        return {}
    result = run([tool], input="\n".join(names))
    lines = result.stdout.splitlines()
    if len(lines) == len(names):
        return dict(zip(names, lines, strict=True))
    if tool.endswith("undname"):
        # Blank-line-separated records rather than three lines a name: a record of one
        # line is a refusal -- which `llvm-undname` sends to stderr and this run has to
        # survive, since a corpus is generated exactly where the reference has gaps.
        answers, record = {}, []
        for line in [*lines, ""]:
            if line:
                record.append(line)
            elif record:
                answers[record[0]] = record[1] if len(record) > 1 else record[0]
                record = []
        return answers
    return {}


def reference_defects(path):
    """The mangled names whose expected spelling is not a reference's to give."""
    if not path.exists():  # pragma: no cover - only in a partial checkout
        return frozenset()
    return frozenset(
        line.split("\t", 1)[0]
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "\t" in line
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "tests" / "conformance")
    parser.add_argument("--tool", default="llvm-cxxfilt", help="reference demangler")
    parser.add_argument("--name", default="itanium-real-world.txt")
    parser.add_argument("--sources", type=Path, default=SOURCES, help="directory of .cpp sources")
    parser.add_argument("--target", default=None, help="compiler target triple, for another ABI")
    parser.add_argument("--compiler", action="append", help="repeatable; default is clang++ and g++")
    parser.add_argument("--prefix", action="append", help="mangled-name prefixes to keep")
    parser.add_argument("--defects", type=Path, default=REFERENCE_DEFECTS, help="names the reference reads wrongly")
    parser.add_argument("--standard", action="append", help="repeatable; default is c++11 through c++23")
    arguments = parser.parse_args()

    standards = tuple(arguments.standard) if arguments.standard else STANDARDS

    sources = arguments.sources
    compilers = tuple(arguments.compiler) if arguments.compiler else COMPILERS
    prefixes = tuple(arguments.prefix) if arguments.prefix else ("_Z", "__Z")
    if not sources.is_dir():
        sys.exit(f"no corpus sources at {sources}")

    build_dir = ROOT / "build" / "corpus"
    build_dir.mkdir(parents=True, exist_ok=True)

    mangled = set()
    provenance = []
    for compiler in compilers:
        if not shutil.which(compiler):
            print(f"skipping {compiler}: not installed")
            continue
        version = run([compiler, "--version"]).stdout.splitlines()[0]
        target = f" --target={arguments.target}" if arguments.target else ""
        provenance.append(f"{compiler}{target}: {version}")
        for source in sorted(sources.glob("*.cpp")):
            for standard in standards:
                for optimisation in OPTIMISATIONS:
                    obj = compile_source(compiler, source, standard, optimisation, build_dir, arguments.target)
                    if obj is None:
                        continue
                    mangled |= {s for s in symbols_of(obj) if s.startswith(prefixes)}

    excluded = reference_defects(arguments.defects)
    known_defects = mangled & excluded
    if known_defects:
        print(f"excluding {len(known_defects)} name(s) the reference reads wrongly; see {arguments.defects.name}")
    mangled -= excluded

    print(f"collected {len(mangled)} distinct mangled symbols")
    ordered = sorted(mangled)
    expected = reference_output(arguments.tool, ordered)
    if not expected:
        sys.exit(f"reference demangler {arguments.tool!r} unavailable or disagreed on line count")

    tool_version = run([arguments.tool, "--version"]).stdout.strip().replace("\n", " ")
    arguments.out.mkdir(parents=True, exist_ok=True)
    target = arguments.out / arguments.name

    with target.open("w", encoding="utf-8") as handle:
        handle.write("# Conformance corpus: real compiler output.\n#\n")
        handle.write("# Each line is a mangled name, a tab, and the spelling the reference\n")
        handle.write("# demangler produces for it. Regenerate with tools/generate_corpus.py.\n#\n")
        handle.write("# Names the reference reads wrongly are excluded; their expected spelling comes\n")
        handle.write(f"# from the declaration instead, in {arguments.defects.name}.\n#\n")
        handle.write(f"# reference: {tool_version}\n")
        for line in provenance:
            handle.write(f"# compiled by: {line}\n")
        handle.write(f"# standards: {', '.join(standards)}\n")
        handle.write(f"# optimisation: {', '.join(OPTIMISATIONS)}\n#\n")
        for name in ordered:
            # A name absent from the map is one the reference wrote nothing for, which is
            # the same thing as echoing it back: there is no answer to record.
            spelled = expected.get(name, name)
            if spelled == name:
                continue
            handle.write(f"{name}\t{spelled}\n")

    kept = sum(1 for n in ordered if expected.get(n, n) != n)
    print(f"wrote {kept} pairs to {target}")


if __name__ == "__main__":
    main()
