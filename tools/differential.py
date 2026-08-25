#!/usr/bin/env python3
"""Compare this library against the reference demanglers.

Three modes:

  --corpus     replay a checked-in corpus file (mangled name, tab, expected spelling).
               Needs no reference binary, so it runs in CI and offline. With no argument
               every checked-in corpus is replayed, each with the style and language it
               was recorded under -- see CORPUS_SETTINGS.
  --live       demangle names on stdin with both this library and a reference binary,
               reporting every disagreement. Used when hunting new failures.
  --cross      run several reference *versions* over the same names and report where the
               references disagree with each other, and which side we are on.

Exit status is non-zero when anything disagrees, so any mode can gate a commit.

Why --cross exists
------------------
Agreeing with one build of one reference is not the same as being right, and the
difference is not hypothetical. llvm-cxxfilt 16 drops a constructor's name after an ABI
tag -- `failure[abi:cxx11]::(...)` -- which 18 fixed. Between 18 and 20, LLVM changed
its substitution numbering for a template template parameter application; on that name
18 was wrong, and a corpus recorded against 18 had baked the wrong answer in.

So --cross reports reference-versus-reference disagreement as the finding it is. Where
the versions differ, being on one side is not evidence of anything, and the question has
to be settled somewhere else: from the ABI, or from what the *manglers* emit, which is
the only ground truth a demangler actually has. `tools/probe_substitutions.py` asks a
reference to print its own dictionary; compiling a source and reading the symbol asks
the compiler.
"""

import argparse
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import demangle
from demangle.core.errors import DemanglingError

REFERENCES = {"itanium": "llvm-cxxfilt", "msvc": "llvm-undname", "gnu": "c++filt"}

#: How each checked-in corpus must be replayed. A corpus records the output of one
#: reference under one style, so replaying it under another is guaranteed to disagree --
#: a false failure, not a real one. Anything not listed uses the defaults.
CORPUS_SETTINGS = {
    "itanium-real-world-gnu.txt": {"style": "gnu"},
    "msvc-llvm-corpus.txt": {"language": "msvc"},
    # Go on purpose. Most Go symbols carry nothing that distinguishes them from any other
    # dotted name -- `bytes.Compare` could be anything -- so the scheme declines to claim
    # them and a caller names the language instead, which is how a tool that read the
    # binary's build info would do it. Replaying this corpus on auto-detection would be
    # measuring the detector's caution rather than the demangler.
    "go-real-world.txt": {"language": "go"},
}

#: Names in these corpora that the reference and this library legitimately disagree on,
#: because the two reference implementations disagree with *each other* about what goes
#: in the substitution table. Recorded here so the tool reports a clean run rather than a
#: failure someone has to remember the reason for. tests/test_conformance.py pins the
#: same list.
#: Names where a reference discards part of the symbol, so following it would mean
#: spelling distinct symbols identically. `llvm-undname` reads the first element of a
#: vftable's base path and drops the rest, mapping three different vtables onto one
#: spelling; versions 16, 18 and 20 all lose it identically. Listed here so `--cross`
#: reports a genuine finding rather than these, and so removing one is a deliberate act.
#: `tests/test_conformance.py` pins the spellings.
REFERENCE_LOSES_INFORMATION = {
    "??_7A@B@@6BC@D@@E@F@@@",
    "??_7A@B@@6BC@D@@E@F@@G@H@@@",
    "??_7A@@6BB@@C@@@",
    "??_7A@@6BB@@C@@D@@@",
}

KNOWN_DIVERGENCES = {
    "_ZN6modern8measuredINSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEEQ5SizedIT_EEEmRKS7_",
    "_ZZN6modern13genericLambdaEvENKUlTyT_E_clIiEEDaS0_",
}


def load_corpus(path):
    """Read (mangled, expected) pairs, skipping comments and blank lines."""
    pairs = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "\t" not in line:
            continue
        mangled, expected = line.split("\t", 1)
        pairs.append((mangled, expected))
    return pairs


def our_output(mangled, style, language):
    try:
        return demangle.demangle_strict(mangled, style=style, language=language)
    except DemanglingError as exc:
        return f"<{type(exc).__name__}>"
    except RecursionError:
        return "<RecursionError>"


def replay(paths, style, language, show, quiet, overrides=True):
    total = matched = divergent = 0
    failures = []
    reasons = Counter()
    for path in paths:
        settings = CORPUS_SETTINGS.get(path.name, {}) if overrides else {}
        corpus_style = settings.get("style", style)
        corpus_language = settings.get("language", language)
        for mangled, expected in load_corpus(path):
            total += 1
            got = our_output(mangled, corpus_style, corpus_language)
            if got == expected:
                matched += 1
            elif mangled in KNOWN_DIVERGENCES:
                divergent += 1
            else:
                failures.append((mangled, expected, got))
                reasons[got if got.startswith("<") else "wrong spelling"] += 1

    if not quiet:
        for mangled, expected, got in failures[:show]:
            print(f"\n  name     {mangled}")
            print(f"  expected {expected}")
            print(f"  got      {got}")
        if len(failures) > show:
            print(f"\n  ... and {len(failures) - show} more")
        if reasons:
            print("\n  failure kinds:")
            for reason, count in reasons.most_common():
                print(f"    {count:5}  {reason}")

    accounted = matched + divergent
    rate = accounted / total * 100 if total else 0.0
    note = f", {divergent} known reference divergence(s)" if divergent else ""
    print(f"\n{matched}/{total} exact ({rate:.2f}%){note}")
    return 0 if accounted == total else 1


def live(names, tool, style, language, show):
    if not shutil.which(tool):
        sys.exit(f"reference tool {tool!r} not installed")
    result = subprocess.run([tool], input="\n".join(names), capture_output=True, text=True)
    expected = result.stdout.splitlines()
    if len(expected) != len(names):
        sys.exit("reference tool returned a different number of lines than it was given")

    disagreements = 0
    for mangled, reference in zip(names, expected, strict=True):
        if reference == mangled:
            continue  # the reference could not read it either
        got = our_output(mangled, style, language)
        if got != reference:
            disagreements += 1
            if disagreements <= show:
                print(f"\n  name     {mangled}")
                print(f"  {tool:8} {reference}")
                print(f"  ours     {got}")
    readable = sum(1 for m, r in zip(names, expected, strict=True) if r != m)
    print(f"\n{readable - disagreements}/{readable} agree with {tool}")
    return 0 if disagreements == 0 else 1


def _one_line_per_name(tool, lines, count):
    """Normalise a reference's stdout to one spelling per input name.

    `llvm-cxxfilt` and `c++filt` print one line per name. `llvm-undname` prints three --
    the decorated name, its spelling, then a blank -- so reading its output the way the
    others are read pairs every name with the wrong answer, silently and off by two.
    """
    if len(lines) == count:
        return lines
    if len(lines) == count * 3:
        return lines[1::3]
    sys.exit(f"{tool} returned {len(lines)} lines for {count} names; cannot pair them")


def cross(names, tools, style, language, show):
    """Run several reference versions over `names` and report where they disagree.

    Prints reference-versus-reference disagreement first, because that is the finding:
    it says the question is open, and that whichever side we are on proves nothing. A
    name where every reference agrees and we do not is our defect, and is reported
    separately.
    """
    missing = [tool for tool in tools if not shutil.which(tool)]
    if missing:
        sys.exit(f"reference tool(s) not installed: {', '.join(missing)}")

    outputs = {}
    for tool in tools:
        result = subprocess.run([tool], input="\n".join(names) + "\n", capture_output=True, text=True)
        lines = _one_line_per_name(tool, result.stdout.splitlines(), len(names))
        outputs[tool] = lines

    ours = [our_output(name, style, language) for name in names]
    split, alone, documented = [], [], 0
    for index, name in enumerate(names):
        answers = {outputs[tool][index] for tool in tools}
        if len(answers) > 1:
            split.append(index)
        elif ours[index] != outputs[tools[0]][index] != name:
            if name in REFERENCE_LOSES_INFORMATION or name in KNOWN_DIVERGENCES:
                documented += 1
            else:
                alone.append(index)

    if split:
        print(f"references disagree with each other on {len(split)} name(s):")
        for index in split[:show]:
            print(f"\n  name {names[index]}")
            for tool in tools:
                mark = "*" if outputs[tool][index] == ours[index] else " "
                print(f"  {mark} {tool:22} {outputs[tool][index]}")
            print(f"    {'ours':22} {ours[index]}")
        if len(split) > show:
            print(f"\n  ... and {len(split) - show} more")
        print("\n  (* marks the versions we match. Being on a side is not evidence;")
        print("   settle these against the ABI or against what a compiler emits.)")

    if alone:
        print(f"\nevery reference agrees and we do not, on {len(alone)} name(s):")
        for index in alone[:show]:
            print(f"\n  name {names[index]}")
            print(f"  all  {outputs[tools[0]][index]}")
            print(f"  ours {ours[index]}")

    note = f" | {documented} documented" if documented else ""
    print(f"\n{len(names)} names | {len(split)} reference splits | {len(alone)} of ours{note}")
    return 1 if alone else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", nargs="*", type=Path, help="corpus files to replay")
    parser.add_argument("--live", action="store_true", help="read names from stdin and compare live")
    parser.add_argument(
        "--cross",
        nargs="+",
        metavar="TOOL",
        help="compare these reference versions against each other over the corpus names",
    )
    parser.add_argument("--tool", default="llvm-cxxfilt")
    parser.add_argument("--style", default="llvm", help="default style; per-corpus settings win")
    parser.add_argument("--language", default=None, help="default language; per-corpus settings win")
    parser.add_argument(
        "--no-corpus-settings",
        action="store_true",
        help="ignore CORPUS_SETTINGS and apply --style/--language to every corpus",
    )
    parser.add_argument("--show", type=int, default=15, help="how many disagreements to print")
    parser.add_argument("--quiet", action="store_true")
    arguments = parser.parse_args()

    if arguments.live:
        names = [line.strip() for line in sys.stdin if line.strip()]
        return live(names, arguments.tool, arguments.style, arguments.language, arguments.show)

    if arguments.cross:
        paths = arguments.corpus or sorted(
            (Path(__file__).resolve().parent.parent / "tests" / "conformance").glob("itanium-*.txt")
        )
        names = [mangled for path in paths for mangled, _ in load_corpus(path)]
        return cross(names, arguments.cross, arguments.style, arguments.language, arguments.show)

    paths = arguments.corpus
    if not paths:
        paths = sorted((Path(__file__).resolve().parent.parent / "tests" / "conformance").glob("*.txt"))
    if not paths:
        sys.exit("no corpus files found")
    return replay(
        paths,
        arguments.style,
        arguments.language,
        arguments.show,
        arguments.quiet,
        overrides=not arguments.no_corpus_settings,
    )


if __name__ == "__main__":
    sys.exit(main())
