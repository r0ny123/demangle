#!/usr/bin/env python3
"""Compare this library against the reference demanglers.

Three modes:

  --corpus     replay a checked-in corpus file (mangled name, tab, expected spelling).
               Needs no reference binary, so it runs in CI and offline. With no argument
               every checked-in corpus is replayed, each with the style and language it
               was recorded under -- see `corpus_settings` -- and every file in
               `reported/` under the language (and style) its name gives.
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
18 was wrong, and a corpus recorded against 18 would bake the wrong answer in.

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

#: How each checked-in corpus must be replayed: under the style or language it was
#: recorded with. Anything not listed uses the defaults.
CORPUS_SETTINGS = {
    "itanium-real-world-gnu.txt": {"style": "gnu"},
    "msvc-llvm-corpus.txt": {"language": "msvc"},
    "msvc-clang.txt": {"language": "msvc"},
    "msvc-reference-defects.txt": {"language": "msvc"},
    "msvc-boost.txt": {"language": "msvc"},
    "swift-reference-defects.txt": {"language": "swift"},
    # Most Go symbols are indistinguishable from any dotted name, so detection declines them.
    "go-real-world.txt": {"language": "go"},
    "d-libiberty.txt": {"language": "d"},
    # Pre-Itanium detection is deliberately narrow (see tests/test_gnuv2.py).
    "gnuv2-real-world.txt": {"language": "gnuv2"},
}


def corpus_settings(path):
    """How to replay `path`: `CORPUS_SETTINGS`, or for `reported/<scheme>[-<style>].txt`
    the language and style its name gives."""
    if path.parent.name == "reported":
        language, _, style = path.stem.partition("-")
        return {"language": language, "style": style or "llvm"}
    return CORPUS_SETTINGS.get(path.name, {})


#: Files in `tests/conformance/` that are not two-column corpora replayable with
#: `demangle()`; their own test modules read them.
NOT_REPLAYED = frozenset(
    {
        "nim-lossy.txt",
        "objc-lossy.txt",
        "objc-refusals.txt",
        "pascal-refusals.txt",
        "delphi-refusals.txt",
        "swift-refusals.txt",
        # Hex names with symbolic references; tests/test_swift_symbolic.py.
        "swift-symbolic.txt",
        # 15 known mismatches, grouped and named in tests/test_conformance.py.
        "itanium-libcxxabi.txt",
        "itanium-libcxxabi.txt.gz",
        # tests/test_swift.py.
        "swift-upstream.txt",
        # Expected column is rustc-demangle's `{:#}` mode; tests/test_rust.py.
        "rustc-upstream.txt",
        # Five deliberate non-answers under auto-detection; tests/test_ada.py.
        "ada-libiberty.txt",
        # Bare `<type>` encodings for `demangle_type`; tests/test_types.py.
        "itanium-types.txt",
        "itanium-types-llvm.txt",
        # Three columns (name, flag, spelling); tests/test_msvc.py.
        "msvc-suppressions.txt",
        # Same three columns, from `dbghelp.dll`; tests/test_msvc_options.py.
        "msvc-dbghelp.txt",
        # A comparison, not a demangling; tests/test_msvc_options.py.
        "msvc-name-only.txt",
        # Scored under `--simplified`; tests/test_swift_simplified.py.
        "swift-simplified.txt",
        # Four columns (name, style, with and without params); tests/test_gnuv2.py.
        "gnuv2-libiberty.txt",
        # Three columns, and auto-detection prefers `gnuv2`; tests/test_codewarrior.py.
        "codewarrior-cwdemangle.txt",
    }
)

#: Names where a reference discards part of the symbol: `llvm-undname` (16, 18, 20) keeps
#: only the first element of a vftable's base path. tests/test_conformance.py pins them.
REFERENCE_LOSES_INFORMATION = {
    "??_7A@B@@6BC@D@@E@F@@@",
    "??_7A@B@@6BC@D@@E@F@@G@H@@@",
    "??_7A@@6BB@@C@@@",
    "??_7A@@6BB@@C@@D@@@",
}

#: Must equal `GNU_DIVERGENCES` in tests/test_conformance.py (tests/test_architecture.py
#: checks both ways).
KNOWN_DIVERGENCES: set[str] = set()


def load_corpus(path):
    """Read (mangled, expected) pairs, skipping comments and blank lines."""
    raw = Path(path)
    if raw.suffix == ".gz" or raw.suffixes[-2:] == [".txt", ".gz"]:
        import gzip

        text = gzip.decompress(raw.read_bytes()).decode("utf-8", "surrogateescape")
    else:
        text = raw.read_text(encoding="utf-8", errors="surrogateescape")
    pairs = []
    for line in text.splitlines():
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
        settings = corpus_settings(path) if overrides else {}
        corpus_style = settings.get("style", style)
        corpus_language = settings.get("language", language)
        for mangled, expected in load_corpus(path):
            total += 1
            got = our_output(mangled, corpus_style, corpus_language)
            if got == expected:
                matched += 1
            elif mangled in KNOWN_DIVERGENCES:
                divergent += 1
            elif expected == mangled and got.startswith("<"):
                # Both sides refuse it: the corpus records a name the reference
                # hands back, and we raise rather than misread it. `demangle()`
                # answers those with the name unchanged, so this is agreement.
                matched += 1
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
    expected = _lines_from(subprocess.run([tool], input=_as_input(names), capture_output=True).stdout)
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


def _as_input(names):
    return ("\n".join(names) + "\n").encode("utf-8", "surrogateescape")


def _lines_from(stdout):
    """One entry per newline, and *only* per newline.

    Bytes rather than text mode, and `split` rather than `splitlines`, because each of
    those breaks a line on more than `\n`: text mode turns a carriage return into a line
    break, and `splitlines` breaks on a form feed or a vertical tab too. A pre-Itanium
    template argument of type `char` is spelled as the raw byte -- `foo<'\r'>(void)` --
    so either would put every answer after it out of step with its name.
    """
    lines = stdout.decode("utf-8", "surrogateescape").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


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

    A tool may be written `tool:style` -- `c++filt:gnu` -- and is then compared against
    our output in that style, and only against other tools sharing it. Without that, GNU
    in the set produces thousands of "disagreements" that are only the `> >` spacing GNU
    and LLVM legitimately differ on, which buries every real finding. Comparing across
    styles measures the style.

    Having a second *implementation* in the set, rather than several builds of one, is
    the point: two versions of one demangler share its bugs, and a cross-check over only
    those reports us as the outlier when they are both wrong together.
    """
    parsed = []
    for entry in tools:
        tool, _, tool_style = entry.partition(":")
        parsed.append((tool, tool_style or style))
    missing = [tool for tool, _ in parsed if not shutil.which(tool)]
    if missing:
        sys.exit(f"reference tool(s) not installed: {', '.join(missing)}")

    outputs, styles = {}, {}
    for tool, tool_style in parsed:
        result = subprocess.run([tool], input=_as_input(names), capture_output=True)
        outputs[tool] = _one_line_per_name(tool, _lines_from(result.stdout), len(names))
        styles[tool] = tool_style
    order = [tool for tool, _ in parsed]

    #: Our answer in each style a tool uses, so every comparison is like for like.
    ours = {tool: [our_output(name, styles[tool], language) for name in names] for tool in order}

    split, alone, documented = [], [], 0
    for index, name in enumerate(names):
        groups = {}
        for tool in order:
            groups.setdefault(styles[tool], set()).add(outputs[tool][index])
        if any(len(answers) > 1 for answers in groups.values()):
            split.append(index)
            continue
        differs = [tool for tool in order if outputs[tool][index] not in (ours[tool][index], name)]
        if not differs:
            continue
        if len(differs) == len(order):
            if name in REFERENCE_LOSES_INFORMATION or name in KNOWN_DIVERGENCES:
                documented += 1
            else:
                alone.append(index)
        else:
            # Some references agree with us and some do not, which is itself a
            # disagreement between them.
            split.append(index)

    if split:
        print(f"references disagree with each other on {len(split)} name(s):")
        for index in split[:show]:
            print(f"\n  name {names[index]}")
            for tool in order:
                mark = "*" if outputs[tool][index] == ours[tool][index] else " "
                print(f"  {mark} {f'{tool} ({styles[tool]})':28} {outputs[tool][index]}")
            # Our answer once per style in play, because comparing a gnu-style reference
            # against an llvm-style spelling of ours would only be reporting the style.
            for tool_style in dict.fromkeys(styles[tool] for tool in order):
                first = next(tool for tool in order if styles[tool] == tool_style)
                print(f"    {f'ours ({tool_style})':28} {ours[first][index]}")
        if len(split) > show:
            print(f"\n  ... and {len(split) - show} more")
        print("\n  (* marks the versions we match. Being on a side is not evidence;")
        print("   settle these against the ABI or against what a compiler emits.)")

    if alone:
        print(f"\nevery reference agrees and we do not, on {len(alone)} name(s):")
        for index in alone[:show]:
            print(f"\n  name {names[index]}")
            for tool in order:
                print(f"  {f'{tool} ({styles[tool]})':28} {outputs[tool][index]}")
            print(f"  {'ours':28} {ours[order[0]][index]}")

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
        help="reference versions to compare, each optionally `tool:style` (e.g. c++filt:gnu)",
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
        directory = Path(__file__).resolve().parent.parent / "tests" / "conformance"
        paths = arguments.corpus or sorted(
            path
            for path in [*directory.glob("itanium-*.txt"), *directory.glob("reported/itanium*.txt")]
            if path.name not in NOT_REPLAYED or path.parent.name == "reported"
        )
        names = [mangled for path in paths for mangled, _ in load_corpus(path)]
        return cross(names, arguments.cross, arguments.style, arguments.language, arguments.show)

    paths = arguments.corpus
    if not paths:
        directory = Path(__file__).resolve().parent.parent / "tests" / "conformance"
        paths = [path for path in sorted(directory.glob("*.txt")) if path.name not in NOT_REPLAYED]
        paths += sorted(directory.glob("reported/*.txt"))
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
