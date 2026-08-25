#!/usr/bin/env python3
"""Compare this library against the reference demanglers.

Two modes:

  --corpus     replay a checked-in corpus file (mangled name, tab, expected spelling).
               Needs no reference binary, so it runs in CI and offline. With no argument
               every checked-in corpus is replayed, each with the style and language it
               was recorded under -- see CORPUS_SETTINGS.
  --live       demangle names on stdin with both this library and a reference binary,
               reporting every disagreement. Used when hunting new failures.

Exit status is non-zero when anything disagrees, so either mode can gate a commit.
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
}

#: Names in these corpora that the reference and this library legitimately disagree on,
#: because the two reference implementations disagree with *each other* about what goes
#: in the substitution table. Recorded here so the tool reports a clean run rather than a
#: failure someone has to remember the reason for. tests/test_conformance.py pins the
#: same list.
KNOWN_DIVERGENCES = {
    "_Z16templateTemplateIN5outer5inner6HolderEiET_IT0_Li3EES4_",
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", nargs="*", type=Path, help="corpus files to replay")
    parser.add_argument("--live", action="store_true", help="read names from stdin and compare live")
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
