#!/usr/bin/env python3
"""Everything a conformance report needs, for one mangled name.

    tools/repro.py _ZNSt6vectorIiSaIiEE9push_backERKi
    echo '_ZNSt6vectorIiSaIiEE9push_backERKi' | tools/repro.py

Detects the scheme (or takes `--language`), prints this library's reading in every
style, and puts the name to each reference demangler `tools/enumerate.py` knows for that
scheme -- including the ones built under `tools/*-reference/` -- skipping any that is not
installed. Then it prints a report body ready to paste into the conformance bug form,
and the line to add to `tests/conformance/reported/<scheme>.txt` once the fix is in.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from enumerate import JOBS, reference_answers

import demangle
from demangle.core.errors import DemanglingError

ISSUE_FORM = "https://github.com/r0ny123/demangle/issues/new?template=conformance-bug.yml"


def references(scheme):
    """`(command, style)` for each reference to ask about `scheme`, first one first.

    The second tool in a `JOBS` entry is GNU's, and is compared in the `gnu` style.
    """
    job = JOBS.get(scheme)
    if job is None:
        return []
    first, second, _ = job
    found = [(first, "llvm")]
    if second:
        found.append((second, "gnu"))
    return found


def installed(command):
    return shutil.which(command.split()[0]) is not None


def ours(name, scheme):
    readings = {}
    for style in demangle.styles():
        try:
            readings[style] = demangle.demangle_strict(name, language=scheme, style=style)
        except (DemanglingError, RecursionError) as error:
            readings[style] = f"<{type(error).__name__}: {error}>"
    return readings


def theirs(command, name):
    """The reference's reading, `None` where it hands the name back, or why it could not run."""
    try:
        return reference_answers(command, [name], timeout=60).get(name)
    except (OSError, subprocess.SubprocessError, SystemExit) as error:
        return f"<{error}>"


def label(command):
    return Path(command.split()[0]).name + "".join(f" {word}" for word in command.split()[1:])


def report(name, scheme, detected):
    readings = ours(name, scheme)
    answers = []
    for command, style in references(scheme):
        if installed(command):
            answers.append((label(command), style, theirs(command, name)))
        else:
            print(f"(skipped {label(command)}: not installed)", file=sys.stderr)

    rows = [(f"ours ({style})", reading) for style, reading in readings.items()]
    rows += [
        (f"{tool} ({style})", answer if answer is not None else "<hands the name back>")
        for tool, style, answer in answers
    ]
    width = max(len(key) for key, _ in rows) + 2
    lines = [f"{'name':{width}}{name}", f"{'scheme':{width}}{scheme}{' (detected)' if detected else ''}"]
    lines += [f"{'version':{width}}demangle {demangle.__version__}", ""]
    lines += [f"{key:{width}}{value}" for key, value in rows]
    if not answers:
        lines.append("(no reference demangler for this scheme is installed or known)")
    reference_rows = [f"{key}: {value}" for key, value in rows[len(readings) :]]

    body = [
        "### The mangled name",
        "",
        f"`{name}`",
        "",
        "### What the reference prints",
        "",
        "```text",
        *(reference_rows or ["<reference, version: spelling>"]),
        "```",
        "",
        "### What this library prints",
        "",
        "```text",
        *(f"{style}: {reading}" for style, reading in readings.items()),
        "```",
        "",
        "### Exact call or flags",
        "",
        f"`demangle --language {scheme} '{name}'`",
        "",
        "### Version",
        "",
        demangle.__version__,
    ]

    # The first reference that read it, in its own style, is what the corpus records.
    expected, style = None, "llvm"
    for _, style_used, answer in answers:
        if answer is not None and not answer.startswith("<"):
            expected, style = answer, style_used
            break
    corpus = f"reported/{scheme}.txt" if style == "llvm" else f"reported/{scheme}-{style}.txt"

    print("\n".join(lines))
    print(f"\n--- report body (fields of {ISSUE_FORM}) ---\n")
    print("\n".join(body))
    print(f"\n--- line for tests/conformance/{corpus} ---\n")
    print(f"{name}\t{expected if expected is not None else '<what the declaration says>'}")
    if expected is not None and expected == readings[style]:
        print("\n(the reference and this library agree on this name)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", nargs="?", help="the mangled name; read from stdin when omitted")
    parser.add_argument("-l", "--language", help="the scheme, when detection does not claim the name")
    arguments = parser.parse_args(argv)

    name = arguments.name
    if name is None:
        name = next((line.strip() for line in sys.stdin if line.strip()), None)
    if not name:
        parser.error("no name given")

    scheme = arguments.language or demangle.detect(name)
    if scheme is None:
        parser.error(f"no scheme claims {name!r}; name one with --language ({', '.join(demangle.languages())})")
    if scheme not in demangle.languages():
        parser.error(f"unknown language {scheme!r}; choose from {', '.join(demangle.languages())}")
    report(name, scheme, detected=arguments.language is None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
