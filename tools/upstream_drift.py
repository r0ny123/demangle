#!/usr/bin/env python3
"""Score this library against the references' test vectors as they are *today*.

The checked-in corpora are transcriptions of what each reference asserted on the day
they were made. The references keep moving: a new C++ draft adds a mangling, rustc
revises v0, Swift adds a node kind, and each lands in the reference's own test file
first. This fetches those files from their `main` branches, finds the vectors the
corpora do not yet hold, and says which of them this library already reads. Run
weekly by `.github/workflows/upstream.yml`, so a mangling that did not exist when a
scheme was written is noticed before a user meets it in a binary.

Three outcomes per vector. A name the corpus holds with the same expectation is
unchanged. One it holds with a different expectation is *recorded differently*: the
corpus carries documented deviations (tests/test_conformance.py names them), so this
is reported for a person to read, and not a failure. A name the corpus does not hold
is *new*, and a new vector this library misreads is what the exit status reports.

Usage
-----
    tools/upstream_drift.py                       every source
    tools/upstream_drift.py --source rustc swift  some of them
    tools/upstream_drift.py --cache DIR           keep the downloads for the next run
    tools/upstream_drift.py --show 40             more examples per table

Exit status: 0 when every new vector reads as the reference says, 1 when one does not,
2 when a source could not be fetched or read.
"""

import argparse
import gzip
import http.client
import json
import os
import re
import sys
import traceback
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import demangle  # noqa: E402

CONFORMANCE = ROOT / "tests" / "conformance"

LLVM_RAW = "https://raw.githubusercontent.com/llvm/llvm-project/main/"
LLVM_DEMANGLE_TESTS = "https://api.github.com/repos/llvm/llvm-project/contents/llvm/test/Demangle"
SWIFT_RAW = "https://raw.githubusercontent.com/swiftlang/swift/main/test/Demangle/Inputs/manglings.txt"
RUSTC_RAW = "https://raw.githubusercontent.com/rust-lang/rustc-demangle/main/src/"

#: The MSVC test files on the day this was written, used when the directory listing
#: cannot be fetched; a file added upstream is then missed until the listing is back.
MS_TESTS = (
    "ms-arg-qualifiers.test",
    "ms-auto-templates.test",
    "ms-back-references.test",
    "ms-basic.test",
    "ms-cxx11.test",
    "ms-cxx14.test",
    "ms-cxx17-noexcept.test",
    "ms-mangle.test",
    "ms-md5.test",
    "ms-nested-scopes.test",
    "ms-operators.test",
    "ms-options.test",
    "ms-return-qualifiers.test",
    "ms-string-literals.test",
    "ms-template-callback.test",
    "ms-templates.test",
    "ms-templates-memptrs.test",
    "ms-templates-memptrs-2.test",
    "ms-thunks.test",
    "ms-windows.test",
)

#: A cell longer than this is cut in the report: a vector is thousands of characters at
#: the long end, and an issue body is capped at 65,536.
CELL_LIMIT = 300

_C_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
    '"': '"',
    "'": "'",
    "\\": "\\",
    "?": "?",
}
_C_STRING = r'"(?:[^"\\]|\\.)*"'
_C_LITERALS = rf"((?:{_C_STRING}\s*)+)"
_C_VECTOR = re.compile(rf"\{{\s*{_C_LITERALS},\s*{_C_LITERALS}\}}", re.DOTALL)
_C_BODY = re.compile(r'"((?:[^"\\]|\\.)*)"', re.DOTALL)
_C_ESCAPE = re.compile(r"\\(x[0-9a-fA-F]+|[0-7]{1,3}|.)", re.DOTALL)
_RUST_ESCAPE = re.compile(r"\\(\n\s*|u\{[0-9a-fA-F]+\}|x[0-9a-fA-F]{2}|.)", re.DOTALL)
_RUST_CONCAT = re.compile(r"concat!\(\s*((?:\"(?:[^\"\\]|\\.)*\"\s*,?\s*)+)\)", re.DOTALL)
_SWIFT_ANNOTATION = re.compile(r"^\{(?:C|T:[^}]*)\}\s*")
_KEEP_HASH = demangle.style("llvm", rust={"keep_hash": True})


def _rust_literal_pattern(hashes_group):
    """A Rust string literal, plain or raw; `hashes_group` numbers the `(#*)` group so
    the closing delimiter can refer back to it."""
    return rf'(?:r(#*)"(.*?)"\{hashes_group}|"((?:[^"\\]|\\.)*)")'


_RUST_MACRO = re.compile(
    r"\b(t|t_nohash|t_nohash_type|t_const|t_const_suffixed|t_err)!\(\s*"
    + _rust_literal_pattern(2)
    + r"(?:\s*,\s*"
    + _rust_literal_pattern(5)
    + r")?(?:\s*,\s*"
    + _rust_literal_pattern(8)
    + r")?\s*,?\s*\)",
    re.DOTALL,
)


def _c_unescape(text):
    def one(found):
        escape = found.group(1)
        if escape[0] == "x":
            code = int(escape[1:], 16)
            return chr(code) if code <= 0x10FFFF else found.group(0)
        if escape[0] in "01234567":
            return chr(int(escape, 8))
        return _C_ESCAPES.get(escape, found.group(0))

    return _C_ESCAPE.sub(one, text)


def _c_literals(adjacent):
    """The bodies of a run of adjacent C string literals, joined as the compiler joins
    them: `"a" "b"` is `"ab"`."""
    return _c_unescape("".join(_C_BODY.findall(adjacent)))


def _rust_unescape(text):
    def one(found):
        escape = found.group(1)
        if escape[0] == "\n":
            return ""
        if escape.startswith("u{"):
            return chr(int(escape[2:-1], 16))
        if escape[0] == "x":
            return chr(int(escape[1:], 16))
        return _C_ESCAPES.get(escape, found.group(0))

    return _RUST_ESCAPE.sub(one, text)


def _rust_literal(raw_body, plain_body):
    if raw_body is not None:
        return raw_body
    return None if plain_body is None else _rust_unescape(plain_body)


def _rust_fold_concat(text):
    """`concat!("a", "b")` of plain literals, written as the one literal it means."""
    return _RUST_CONCAT.sub(lambda found: '"' + "".join(_C_BODY.findall(found.group(1))) + '"', text)


class Fetcher:
    def __init__(self, cache):
        self.cache = cache
        self.token = os.environ.get("GITHUB_TOKEN", "")

    def get(self, url):
        if self.cache:
            path = self.cache / re.sub(r"[^A-Za-z0-9._-]", "_", url)
            if path.exists():
                return path.read_text(encoding="utf-8", errors="surrogateescape")
        headers = {"User-Agent": "demangle-upstream-drift"}
        if self.token and url.startswith("https://api.github.com/"):
            headers["Authorization"] = f"Bearer {self.token}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as response:
            text = response.read().decode("utf-8", "surrogateescape")
        if self.cache:
            self.cache.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", errors="surrogateescape")
        return text


# ---- sources: each yields (mangled, expected[, mode]), expected == mangled for a refusal


def libcxxabi(fetch):
    text = fetch.get(LLVM_RAW + "libcxxabi/test/DemangleTestCases.inc")
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    return [(_c_literals(m), _c_literals(e)) for m, e in _C_VECTOR.findall(text)]


def msvc(fetch):
    """FileCheck's pairing: a file's `CHECK:` and `CHECK-NEXT:` lines match its output
    lines in order, each from where the previous one matched, not the name written
    above each. llvm-undname echoes every input line before its answer, and a check
    written for the echo is not an expectation."""
    try:
        listing = json.loads(fetch.get(LLVM_DEMANGLE_TESTS))
        files = [entry["name"] for entry in listing if entry["name"].startswith("ms-")]
    except (urllib.error.URLError, http.client.HTTPException, ValueError, KeyError, TypeError):
        files = list(MS_TESTS)
    pairs = []
    for name in files:
        lines = fetch.get(LLVM_RAW + "llvm/test/Demangle/" + name).splitlines()
        names = [line.strip() for line in lines if line.strip() and not line.startswith(";")]
        checks = [
            found.group(1).strip()
            for line in lines
            if (found := re.match(r"; CHECK(?:-NEXT)?:(.*)", line))
            if found.group(1).strip() not in names
        ]
        outputs = [demangle.demangle(mangled, language="msvc") for mangled in names]
        cursor = 0
        for check in checks:
            matched = next((at for at in range(cursor, len(outputs)) if _contains(check, outputs[at])), None)
            if matched is None:
                pairs.append((names[min(cursor, len(names) - 1)], check))
                continue
            pairs.append((names[matched], check))
            cursor = matched + 1
    return pairs


def swift(fetch):
    pairs = []
    for line in fetch.get(SWIFT_RAW).splitlines():
        mangled, arrow, expected = line.partition(" ---> ")
        if not arrow:
            continue
        pairs.append((mangled.strip(), _SWIFT_ANNOTATION.sub("", expected.strip())))
    return pairs


def rustc(fetch):
    pairs = []
    for name in ("v0.rs", "legacy.rs", "lib.rs"):
        for found in _RUST_MACRO.finditer(_rust_fold_concat(fetch.get(RUSTC_RAW + name))):
            macro = found.group(1)
            first = _rust_literal(found.group(3), found.group(4))
            second = _rust_literal(found.group(6), found.group(7))
            third = _rust_literal(found.group(9), found.group(10))
            if macro == "t_err":
                pairs.append((first, first))
            elif macro == "t":
                pairs.append((first, second, "hash"))
            elif macro == "t_nohash":
                pairs.append((first, second))
            elif macro == "t_nohash_type":
                pairs.append(("_RMC0" + first, "<" + second + ">"))
            elif macro == "t_const":
                pairs.append(("_RIC0K" + first + "E", "::<" + second + ">"))
            elif macro == "t_const_suffixed":
                pairs.append(("_RIC0K" + first + "E", "::<" + second + ">"))
                pairs.append(("_RIC0K" + first + "E", "::<" + second + (third or "") + ">", "hash"))
    return pairs


def _exact(expected, got):
    return expected == got


def _contains(expected, got):
    """FileCheck's test: a `CHECK:` line is a substring, with a run of spaces matching
    any run. LLVM's MSVC files write theirs without the access specifier."""
    return re.sub(r" +", " ", expected) in re.sub(r" +", " ", got)


SOURCES = {
    "libcxxabi": (libcxxabi, "itanium-libcxxabi.txt.gz", lambda m, _: demangle.demangle(m), _exact),
    "msvc": (msvc, "msvc-llvm-corpus.txt", lambda m, _: demangle.demangle(m, language="msvc"), _contains),
    "swift": (swift, "swift-upstream.txt", lambda m, _: demangle.demangle(m, language="swift"), _exact),
    "rustc": (
        rustc,
        "rustc-upstream.txt",
        lambda m, mode: demangle.demangle(m, language="rust", style=_KEEP_HASH if mode == "hash" else "llvm"),
        _exact,
    ),
}


#: Upstream vectors this library answers differently on purpose, with the test that
#: says so.
EXPECTED_MISREADS = {
    # A `)` after a complete name: llvm-undname stops reading at the name's end, this
    # library refuses leftover input (tests/test_msvc.py, "trailing bytes").
    "??_C@_07LJGFEJEB@D3?$CC?$BB?$AA?$AA?$AA?$AA@)",
}


def recorded(corpus):
    path = CONFORMANCE / corpus
    text = (
        gzip.decompress(path.read_bytes()).decode("utf-8", "surrogateescape")
        if path.suffix == ".gz"
        else path.read_text(encoding="utf-8", errors="surrogateescape")
    )
    rows = {}
    for line in text.splitlines():
        if line and not line.startswith("#") and "\t" in line:
            mangled, expected = line.split("\t", 1)
            rows.setdefault(mangled, expected)
    return rows


def score(name, fetch):
    """A row with a `mode` is a spelling the corpus does not record (rustc-demangle's
    hashed form), so it is scored against the reference's text whether or not the
    corpus holds the name."""
    read, corpus, spell, match = SOURCES[name]
    held = recorded(corpus)
    result = {"source": name, "fetched": 0, "unchanged": 0, "differently": [], "new_pass": 0, "new_fail": []}
    seen = set()
    for row in read(fetch):
        mangled, expected = row[0], row[1]
        mode = row[2] if len(row) > 2 else None
        key = (mangled, mode)
        if key in seen:
            continue
        seen.add(key)
        result["fetched"] += 1
        if mode is None and mangled in held:
            if match(expected, held[mangled]):
                result["unchanged"] += 1
            else:
                result["differently"].append((mangled, held[mangled], expected))
            continue
        got = spell(mangled, mode)
        if mangled in EXPECTED_MISREADS or match(expected, got):
            if mangled in held:
                result["unchanged"] += 1
            else:
                result["new_pass"] += 1
        else:
            result["new_fail"].append((mangled, expected, got))
    return result


def _cell(text):
    """`text` as a Markdown code span whatever it holds: a fence one backtick longer
    than any run inside, spaced off the content, pipes escaped, and cut at `CELL_LIMIT`."""
    if len(text) > CELL_LIMIT:
        text = text[:CELL_LIMIT] + "…"
    text = text.replace("|", "\\|")
    fence = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    return f"{fence} {text} {fence}"


def _table(rows, show, columns):
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for row in rows[:show]:
        lines.append("| " + " | ".join(_cell(cell) for cell in row) + " |")
    if len(rows) > show:
        lines.append(f"| … {len(rows) - show} more | | |")
    return lines


def report(results, show):
    lines = [
        "| source | distinct upstream vectors | unchanged | recorded differently | new, read | new, misread |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        cells = (r["source"], r["fetched"], r["unchanged"], len(r["differently"]), r["new_pass"], len(r["new_fail"]))
        lines.append("| " + " | ".join(str(cell) for cell in cells) + " |")
    for r in results:
        if r["new_fail"]:
            lines += ["", f"### {r['source']}: new vectors this library misreads", ""]
            lines += _table(r["new_fail"], show, ("mangled", "reference", "this"))
        if r["differently"]:
            lines += ["", f"### {r['source']}: recorded with a different expectation", ""]
            lines += _table(r["differently"], show, ("mangled", "recorded", "upstream now"))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", nargs="+", choices=sorted(SOURCES), default=sorted(SOURCES))
    parser.add_argument("--cache", type=Path, help="directory to keep the fetched files in")
    parser.add_argument("--show", type=int, default=15, help="examples per table")
    args = parser.parse_args(argv)
    fetch = Fetcher(args.cache)
    results = []
    for name in args.source:
        try:
            results.append(score(name, fetch))
        except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
            print(f"{name}: could not fetch: {exc}", file=sys.stderr)
            return 2
        except Exception:
            # A reader that cannot cope with the file is not a misread.
            print(f"{name}: could not read the reference's file", file=sys.stderr)
            traceback.print_exc()
            return 2
    print(report(results, args.show))
    return 1 if any(r["new_fail"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
