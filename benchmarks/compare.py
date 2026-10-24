#!/usr/bin/env python3
"""Compare `demangle` with other demanglers, on the same names, on this machine.

`bench.py` measures this package against its own recorded baseline and gates CI on it.
This answers a different question -- how it stands against the alternatives -- and gates
nothing: the figures depend on the machine, the interpreter and the versions installed,
so the report opens with all three, and the ratios between rows are the finding, not the
absolute rates.

None of the competitors is a dependency of the package or of any dependency group; a
tool that is not installed is skipped with a note. To measure all of them:

    pip install llvm-demangle-fxti cpp-demangle itanium-demangler

and, for the command-line tools, have `llvm-cxxfilt`, GNU `c++filt` and `llvm-undname`
on PATH (`apt-get install llvm binutils`). Then:

    python benchmarks/compare.py
    python benchmarks/compare.py --nm /usr/lib/x86_64-linux-gnu/libstdc++.so.6
    python benchmarks/compare.py --llvm 18    # the LLVM release of llvm-cxxfilt on Ubuntu 24.04

Inputs are the checked-in corpora under `tests/conformance/` -- real compiler output,
every name distinct -- and, with `--nm`, the `_Z` symbols a shared library exports
(`nm -D --defined-only`, symbol versions removed). Per tool and corpus the report gives:

  us/name   cold. `demangle` has a result cache, so its cache is emptied before every
            pass and no name repeats within one: the true parse cost. The other
            in-process tools have nothing to empty. Best of `--repeats` passes, as
            `bench.py` does; the minimum is the least noisy estimator.
  names/s   the same figure as a rate.
  Read      names the tool changed. A demangler hands back what it cannot read, so this
            is output differing from input; an exception counts as not read. It says
            nothing about whether the spelling is right.
  = X       names whose output is identical, byte for byte, to X's. A tool that declines
            and a reference that declines agree, as they would by diffing the two
            outputs. It counts a difference of style -- `> >` against `>>`, a Rust hash
            kept or dropped -- exactly as it counts a wrong answer.

The reference columns are the tools that ran and are references for the scheme (see
`REFERENCES`); where none did, the spelling recorded in the corpus stands in, which is
what the reference named in the corpus header printed. `ref` marks a tool's own column.

A command-line tool is run once over the whole list, so its figure includes starting the
process and piping the names: that is what using it costs, and it is not comparable with
a per-call figure for a few names. `demangle` also appears warm -- the same names again
with the cache full, which is what a real symbol table looks like.
"""

import argparse
import datetime
import functools
import gc
import importlib
import platform
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import demangle  # noqa: E402

CONFORMANCE = ROOT / "tests" / "conformance"

#: (file, scheme). The Itanium pair is small and large: `itanium-real-world.txt` holds
#: 318 names compiled by two compilers at five standards, `itanium-libstdcxx.txt` every
#: symbol an older libstdc++ exports, which is the same shape of input as `--nm`.
CORPORA = (
    ("itanium-real-world.txt", "itanium"),
    ("itanium-libstdcxx.txt", "itanium"),
    ("msvc-boost.txt", "msvc"),
    ("rust-real-world.txt", "rust"),
    ("swift-real-world.txt", "swift"),
)

#: Which tools are references for a scheme, in column order, by `Tool.key`. `llvm-cxxfilt`
#: is the reference the Itanium conformance corpora are recorded from, and GNU `c++filt`
#: the other one: they disagree on spacing and on a few spellings, so both are shown.
#: The LLVM binding and `llvm-undname` read MSVC names with the same code.
REFERENCES = {
    "itanium": ("llvm-cxxfilt", "c++filt"),
    "msvc": ("llvm-demangle-fxti", "llvm-undname"),
}


@dataclass
class Corpus:
    title: str
    scheme: str
    names: list[str]
    #: The spelling the corpus records for each name, where it records one.
    recorded: list[str] | None = None


@dataclass
class Tool:
    """A demangler driven one name at a time, in this process."""

    key: str
    label: str
    schemes: tuple[str, ...]
    #: The name's spelling, or `None` where the tool declines.
    call: Callable[[str], str | None]
    #: Run before every timed pass. `demangle` empties its cache here.
    prepare: Callable[[], None] | None = None


@dataclass
class Filter:
    """A command-line demangler, run once over the whole list."""

    key: str
    label: str
    schemes: tuple[str, ...]
    command: str
    #: Splits the tool's standard output into one answer per name, `None` for a decline;
    #: `None` where the output does not have one answer for each name sent.
    split: Callable[[str, int], list[str | None] | None]


@dataclass
class Row:
    key: str
    label: str
    seconds_per_name: float
    outputs: list[str]
    note: str = ""


def declining(function):
    """`function` as a callable that answers `None` where it raises."""

    def call(name):
        try:
            return function(name)
        except Exception:
            return None

    return call


def package_version(distribution):
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(distribution)
    except PackageNotFoundError:  # pragma: no cover - imported but not installed as a distribution
        return "?"


def command_version(command):
    """The first dotted number in `command --version`, which is how each of them says it."""
    try:
        completed = subprocess.run([command, "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "?"
    found = re.search(r"\d+(?:\.\d+)+", completed.stdout + completed.stderr)
    return found.group(0) if found else "?"


def lines(text, expected):
    """One answer per input line, as `c++filt` and `llvm-cxxfilt` write them."""
    answers = text.split("\n")
    if answers and answers[-1] == "":
        answers.pop()
    return list(answers) if len(answers) == expected else None


def undname_groups(text, expected):
    """`llvm-undname` writes the input, then its reading if it has one, then a blank line.

    The reason it gives for a refusal goes to standard error, so a name it declines is a
    group of one line.
    """
    groups = text.split("\n\n")
    if groups and groups[-1] == "":
        groups.pop()
    if len(groups) != expected:
        return None
    answers: list[str | None] = []
    for group in groups:
        parts = group.split("\n")
        answers.append(parts[1] if len(parts) > 1 else None)
    return answers


def in_process_tools(notes, llvm_version=None):
    """Every tool this interpreter can import. A missing one is skipped, with a note.

    `llvm_version` picks one of the LLVM releases `llvm-demangle-fxti` ships, 11 to 21;
    the default is the newest, which is what `import llvmdemangle` gives.
    """
    version = demangle.__version__
    tools = [
        Tool(
            "demangle",
            f"demangle {version} (pure Python)",
            ("itanium", "msvc", "rust", "swift"),
            demangle.demangle,
            prepare=demangle.cache_clear,
        ),
        Tool(
            "demangle-gnu",
            f'demangle {version}, style="gnu"',
            ("itanium",),
            functools.partial(demangle.demangle, style="gnu"),
            prepare=demangle.cache_clear,
        ),
    ]
    try:
        llvm = importlib.import_module("llvmdemangle")
    except ImportError:
        notes.append("llvm-demangle-fxti: not installed (pip install llvm-demangle-fxti)")
    else:
        if llvm_version is not None:
            try:
                llvm = llvm.backend(llvm_version)
            except ValueError as error:
                sys.exit(f"--llvm: {error}")
        # Its `demangle()` picks the scheme from the name, as ours does, and hands the input
        # back when it declines: the call someone labelling a symbol table would make.
        label = f"llvm-demangle-fxti {package_version('llvm-demangle-fxti')} (LLVM {llvm.LLVM_VERSION}, native)"
        tools.append(Tool("llvm-demangle-fxti", label, ("itanium", "msvc", "rust"), llvm.demangle))
        # Its Rust reader on its own: the v0 mangling only. A legacy Rust name, `_ZN...E`,
        # is an Itanium name, and `demangle` above reads it as one, hash and all.
        tools.append(Tool("llvm-rust", f"{label}, rust_demangle() v0 only", ("rust",), llvm.rust_demangle))
    try:
        cpp = importlib.import_module("cpp_demangle")
    except ImportError:
        notes.append("cpp-demangle: not installed (pip install cpp-demangle)")
    else:
        tools.append(
            Tool(
                "cpp-demangle",
                f"cpp-demangle {package_version('cpp-demangle')} (Rust, native)",
                ("itanium",),
                declining(cpp.demangle),
            )
        )
    try:
        itanium = importlib.import_module("itanium_demangler")
    except ImportError:
        notes.append("itanium_demangler: not installed (pip install itanium-demangler)")
    else:

        def spell(name):
            # The parse is a tree; a string is what the others return, so rendering it counts.
            tree = itanium.parse(name)
            return None if tree is None else str(tree)

        tools.append(
            Tool(
                "itanium_demangler",
                f"itanium_demangler {package_version('itanium-demangler')} (pure Python)",
                ("itanium",),
                declining(spell),
            )
        )
    return tools


def command_line_tools(notes):
    """Every command-line demangler on PATH, each run over the whole list in one process."""
    candidates = (
        ("llvm-cxxfilt", "llvm-cxxfilt", ("itanium",), lines),
        ("c++filt", "GNU c++filt", ("itanium",), lines),
        ("llvm-undname", "llvm-undname", ("msvc",), undname_groups),
    )
    tools = []
    for command, label, schemes, split in candidates:
        if shutil.which(command) is None:
            notes.append(f"{command}: not on PATH")
            continue
        tools.append(Filter(command, f"{label} {command_version(command)} (one process)", schemes, command, split))
    return tools


def strip_version(name, scheme):
    """Split an ELF symbol version, `name@@GLIBCXX_3.4`, from an Itanium name.

    An Itanium name has no `@`; an MSVC name is full of them.
    """
    if scheme != "itanium":
        return name, ""
    base, at, version = name.partition("@")
    return base, at + version


def load_corpus(path, scheme, limit=None):
    """The names in a conformance file, distinct, and the spelling recorded for each."""
    names, recorded, seen = [], [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "\t" not in line:
            continue
        name, expected = line.split("\t", 2)[:2]
        name, version = strip_version(name, scheme)
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
        recorded.append(expected.removesuffix(version) if version else expected)
    if limit is not None:
        names, recorded = names[:limit], recorded[:limit]
    return Corpus(path.name, scheme, names, recorded)


def nm_corpus(library, limit=None):
    """The distinct `_Z` symbols a shared object exports, versions removed."""
    if shutil.which("nm") is None:
        sys.exit("--nm needs `nm` on PATH")
    completed = subprocess.run(
        ["nm", "-D", "--defined-only", library], capture_output=True, text=True, errors="replace", check=False
    )
    if completed.returncode:
        sys.exit(f"nm could not read {library}: {completed.stderr.strip()}")
    names = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if fields:
            name, _ = strip_version(fields[-1], "itanium")
            if name.startswith("_Z"):
                names[name] = None
    listed = list(names)[:limit] if limit is not None else list(names)
    return Corpus(f"nm -D {Path(library).name}", "itanium", listed)


def timed(run, repeats, prepare=None):
    """Best-of-N wall time of one pass and what the last pass returned."""
    best, results = float("inf"), None
    for _ in range(repeats):
        if prepare is not None:
            prepare()
        gc.collect()
        start = time.perf_counter()
        results = run()
        best = min(best, time.perf_counter() - start)
    return best, results


def measure_tool(tool, names, repeats):
    call = tool.call
    seconds, answers = timed(lambda: [call(name) for name in names], repeats, tool.prepare)
    return Row(
        tool.key,
        tool.label,
        seconds / len(names),
        [name if answer is None else answer for name, answer in zip(names, answers, strict=True)],
    )


def measure_warm(tool, names, repeats):
    """The same pass again, with the cache the last one filled."""
    stats = demangle.cache_stats()
    seconds, answers = timed(lambda: [tool.call(name) for name in names], repeats)
    row = Row(
        tool.key,
        f"demangle {demangle.__version__}, warm cache",
        seconds / len(names),
        [name if answer is None else answer for name, answer in zip(names, answers, strict=True)],
    )
    # Every call after the first pass should have been a hit; a corpus larger than the
    # cache holds would make this a cold figure under another name.
    hits = demangle.cache_stats()["hits"] - stats["hits"]
    if hits < len(names) * repeats:
        row.note = f"cache held {hits // repeats} of {len(names)}"
    return row


def measure_filter(tool, names, repeats):
    payload = ("\n".join(names) + "\n").encode("utf-8", "surrogateescape")

    def run():
        completed = subprocess.run([tool.command], input=payload, capture_output=True, check=False)
        return tool.split(completed.stdout.decode("utf-8", "surrogateescape"), len(names))

    seconds, answers = timed(run, repeats)
    if answers is None:
        return Row(tool.key, tool.label, seconds / len(names), [], note="output did not match the input line for line")
    return Row(
        tool.key,
        tool.label,
        seconds / len(names),
        [name if answer is None else answer for name, answer in zip(names, answers, strict=True)],
    )


def measure(corpus, tools, filters, repeats):
    rows = []
    for tool in tools:
        if corpus.scheme not in tool.schemes:
            continue
        rows.append(measure_tool(tool, corpus.names, repeats))
        if tool.key == "demangle":
            rows.append(measure_warm(tool, corpus.names, repeats))
    for tool in filters:
        if corpus.scheme in tool.schemes:
            rows.append(measure_filter(tool, corpus.names, repeats))
    return rows


def references_for(corpus, rows):
    """The columns to score against: (name, outputs) for each."""
    by_key = {row.key: row for row in rows if row.outputs}
    columns = [(key, by_key[key].outputs) for key in REFERENCES.get(corpus.scheme, ()) if key in by_key]
    if not columns and corpus.recorded is not None:
        columns.append(("corpus", corpus.recorded))
    return columns


def number(value):
    return f"{value:,}"


def rate(per_second):
    """Names per second to three significant figures: the clock carries no more."""
    return f"{float(f'{per_second:.3g}'):,.0f}"


def microseconds(seconds_per_name):
    micro = seconds_per_name * 1e6
    return f"{micro:.2f}" if micro < 1 else f"{micro:.1f}"


def table(corpus, rows):
    columns = references_for(corpus, rows)
    out = [
        f"### {corpus.title}: {number(len(corpus.names))} names, {corpus.scheme}",
        "",
        "| Tool | us/name | names/s | Read | " + " | ".join(f"= {key}" for key, _ in columns) + " |",
        "|---|---:|---:|---:|" + "---:|" * len(columns),
    ]
    for row in rows:
        label = row.label + (f" ({row.note})" if row.note else "")
        if not row.outputs:
            out.append(f"| {label} | {microseconds(row.seconds_per_name)} | -- | -- |" + " -- |" * len(columns))
            continue
        read = sum(1 for output, name in zip(row.outputs, corpus.names, strict=True) if output != name)
        cells = [
            "ref"
            if row.key == key
            else number(sum(1 for output, expected in zip(row.outputs, reference, strict=True) if output == expected))
            for key, reference in columns
        ]
        out.append(
            f"| {label} | {microseconds(row.seconds_per_name)} | {rate(1 / row.seconds_per_name)} | "
            f"{number(read)} | " + " | ".join(cells) + " |"
        )
    return "\n".join(out)


def cpu_model():
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                return line.partition(":")[2].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


def header(repeats, notes):
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    passes = f"{repeats} pass" if repeats == 1 else f"{repeats} passes"
    out = [
        "## demangle against other demanglers",
        "",
        f"- {today}; {platform.python_implementation()} {platform.python_version()}; "
        f"{platform.system()} {platform.machine()}; {cpu_model()}",
        f"- cold: every name distinct, `demangle`'s cache emptied before each pass; best of {passes};"
        " command-line tools run once over the whole list, starting the process included",
        "- `Read`: output differs from input. `= X`: output identical to X's. `ref`: X is that row's own tool",
    ]
    out.extend(f"- skipped: {note}" for note in notes)
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nm", metavar="LIBRARY", help="also measure the _Z symbols a shared library exports")
    parser.add_argument("--repeats", type=int, default=5, help="passes per measurement; the fastest counts (default 5)")
    parser.add_argument("--limit", type=int, metavar="N", help="use only the first N names of each corpus")
    parser.add_argument(
        "--llvm", type=int, metavar="N", help="read with the LLVM N backend of llvm-demangle-fxti (default: its newest)"
    )
    arguments = parser.parse_args(argv)
    if arguments.repeats < 1:
        parser.error("--repeats must be at least 1")
    if arguments.limit is not None and arguments.limit < 1:
        parser.error("--limit must be at least 1")

    corpora = [load_corpus(CONFORMANCE / name, scheme, arguments.limit) for name, scheme in CORPORA]
    if arguments.nm:
        corpora.append(nm_corpus(arguments.nm, arguments.limit))
    corpora = [corpus for corpus in corpora if corpus.names]

    notes: list[str] = []
    tools = in_process_tools(notes, arguments.llvm)
    filters = command_line_tools(notes)
    # Imports and per-scheme start-up are not the parse cost.
    demangle.preload()

    print(header(arguments.repeats, notes))
    for corpus in corpora:
        print()
        print(table(corpus, measure(corpus, tools, filters, arguments.repeats)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
