#!/usr/bin/env python3
"""Build a Rust conformance corpus from real rustc output.

Rust has shipped two mangling schemes, and a hand-written test file tends to capture the
tidy half of each: a path, a hash, maybe one generic argument. What rustc actually emits
is far stranger. Monomorphisation stamps out `<alloc::vec::Vec<T> as core::ops::Index<I>>`
paths hundreds of characters long, closures acquire numeric disambiguators, backreferences
compress the repeated halves of a v0 name into two characters, punycode appears wherever
an identifier left ASCII, and `-C opt-level=2` adds internal-linkage suffixes that only
exist after inlining.

Each source is compiled under both mangling schemes at several optimisation levels, both
as a bare object file and as a fully linked binary -- the link step drags in the
precompiled standard library, which is the largest supply of genuinely real-world names
available without a network. Symbols are read out with `nm`, and the reference
demangler's spelling is recorded next to each name. The result is checked in, so the test
suite needs neither a Rust toolchain nor the reference binary to run.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCES = HERE / "corpus_sources" / "rust"

EDITION = "2021"
SCHEMES = ("legacy", "v0")
OPTIMISATIONS = ("0", "2")
#: More than one codegen unit is what makes the compiler internalise symbols and give
#: them an `.llvm.<hash>` suffix, a spelling that exists in every release build and in no
#: hand-written test file.
CODEGEN_UNITS = ("1", "16")
#: Both an object file and a linked executable. The object holds this crate's own
#: monomorphisations; the executable additionally holds everything the standard library
#: contributed, which is where the long names live.
ARTEFACTS = ("obj", "bin")

#: `-C symbol-mangling-version=legacy` is gated behind `-Z unstable-options`, which a
#: release toolchain refuses without the bootstrap escape hatch. Nothing unstable is
#: being asked of the compiler beyond naming a scheme it already implements.
BOOTSTRAP = {**os.environ, "RUSTC_BOOTSTRAP": "1"}

PREFIXES = ("_R", "__R", "_ZN", "__ZN")


def run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, **kwargs)


def compile_source(source, scheme, optimisation, units, artefact, out_dir):
    """Compile one source under one scheme, returning the output path or None."""
    output = out_dir / f"{source.stem}-{scheme}-O{optimisation}-cgu{units}-{artefact}"
    command = [
        "rustc",
        f"--edition={EDITION}",
        "-Z",
        "unstable-options",
        "-C",
        f"symbol-mangling-version={scheme}",
        "-C",
        f"opt-level={optimisation}",
        "-C",
        f"codegen-units={units}",
        "-C",
        "debuginfo=0",
    ]
    if artefact == "obj":
        command.append("--emit=obj")
    command += ["-o", str(output), str(source)]
    result = run(command, env=BOOTSTRAP)
    if result.returncode != 0:
        print(f"  {source.name} {scheme} -O{optimisation} cgu{units} {artefact}: {result.stderr.splitlines()[:1]}")
        return None
    return output


def symbols_of(binary):
    """Every symbol name in an object file or executable, mangled ones included."""
    result = run(["nm", "--no-demangle", "-a", str(binary)])
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

    One process for the whole corpus rather than one per name: with tens of thousands of
    symbols the process spawn dominates everything else. `rustfilt` rewrites mangled
    names in place wherever it finds them, so feeding it one name per line gives back one
    spelling per line in the same order.
    """
    if not shutil.which(tool):
        return {}
    result = run([tool], input="\n".join(names))
    if result.returncode != 0:
        return {}
    lines = result.stdout.splitlines()
    if len(lines) != len(names):
        return {}
    return dict(zip(names, lines, strict=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "tests" / "conformance")
    parser.add_argument("--tool", default="rustfilt", help="reference demangler")
    parser.add_argument("--name", default="rust-real-world.txt")
    arguments = parser.parse_args()

    if not SOURCES.is_dir():
        sys.exit(f"no corpus sources at {SOURCES}")
    if not shutil.which("rustc"):
        sys.exit("rustc is not installed")

    build_dir = ROOT / "build" / "rust-corpus"
    build_dir.mkdir(parents=True, exist_ok=True)

    version = run(["rustc", "--version"]).stdout.strip()
    mangled = set()
    for source in sorted(SOURCES.glob("*.rs")):
        for scheme in SCHEMES:
            for optimisation in OPTIMISATIONS:
                for units in CODEGEN_UNITS:
                    for artefact in ARTEFACTS:
                        built = compile_source(source, scheme, optimisation, units, artefact, build_dir)
                        if built is None:
                            continue
                        mangled |= {s for s in symbols_of(built) if s.startswith(PREFIXES)}

    print(f"collected {len(mangled)} distinct mangled symbols")
    ordered = sorted(mangled)
    expected = reference_output(arguments.tool, ordered)
    if not expected:
        sys.exit(f"reference demangler {arguments.tool!r} unavailable or disagreed on line count")

    tool_version = run([arguments.tool, "--version"]).stdout.strip().replace("\n", " ")
    arguments.out.mkdir(parents=True, exist_ok=True)
    target = arguments.out / arguments.name

    with target.open("w", encoding="utf-8") as handle:
        handle.write("# Conformance corpus: real rustc output.\n#\n")
        handle.write("# Each line is a mangled name, a tab, and the spelling the reference\n")
        handle.write("# demangler produces for it. Regenerate with tools/generate_rust_corpus.py.\n#\n")
        handle.write(f"# reference: {tool_version} (wraps the rustc-demangle crate)\n")
        handle.write(f"# compiled by: {version}\n")
        handle.write(f"# edition: {EDITION}\n")
        handle.write(f"# mangling: {', '.join(SCHEMES)}\n")
        handle.write(f"# optimisation: {', '.join('-C opt-level=' + o for o in OPTIMISATIONS)}\n")
        handle.write(f"# codegen units: {', '.join(CODEGEN_UNITS)}\n")
        handle.write(f"# artefacts: {', '.join(ARTEFACTS)}\n#\n")
        for name in ordered:
            spelled = expected[name]
            if spelled == name:
                continue  # the reference could not read it either; nothing to assert
            handle.write(f"{name}\t{spelled}\n")

    kept = sum(1 for n in ordered if expected[n] != n)
    print(f"wrote {kept} pairs to {target}")


if __name__ == "__main__":
    main()
