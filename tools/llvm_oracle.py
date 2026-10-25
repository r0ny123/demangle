"""The newest LLVM's demanglers, as a reference for every checked-in corpus.

`tools/upstream_drift.py` scores this library on the references' *test vectors*. This is
the other half: what the newest LLVM *does today*, over the names the corpora already
hold. The corpora were recorded against `llvm-cxxfilt` 18 and `llvm-undname` 18, and
those are what a CI runner ships; LLVM has moved on since, and the PyPI package
`llvm-demangle-fxti` bundles LLVM 11 through 21 behind one import, so the newest is a
`pip install` away.

Every name in `tests/conformance/` is offered to the reader LLVM would pick for it, by
prefix as `llvm::demangle` picks -- `_Z` Itanium, `_R` Rust v0, `_D` D, `?` MSVC -- and
this library's reading is compared with LLVM's. A name falls in one of five places:

    agree        the two spell it alike, or both decline it
    known        both read it, the spellings differ, and something already accounts for it
    unaccounted  both read it, the spellings differ, and nothing accounts for it
    declined     LLVM declines a name this library reads
    refused      LLVM reads a name this library declines

Only *unaccounted* fails the run. *declined* and *refused* are listed and never fail it,
the way `tools/mutate.py --refusals` lists what a reference reads that this library
refuses. A name LLVM declines is mostly a reference that has not caught up: its D reader
reads qualified names and a few data symbols but no function type, and its Rust reader
stops short of some of v0's newer forms. A name this library declines that LLVM reads is
a gap here or LLVM reading junk, which a person settles with the source open.

What counts as *known* is not a new list. It is what the repository already says about
a reference's wrong answers:

- the name is in `itanium-reference-defects.txt` or `msvc-reference-defects.txt`, whose
  expected column is the declaration and not a reference's reading;
- `tools/enumerate.py`'s `ACCEPTED` rule for the scheme explains it, which for Itanium
  includes `c++filt`'s vote, so that is asked for the names that need it;
- `SPELLINGS` below explains it: three ways LLVM's Rust and D readers spell a symbol
  that the ports this library follows do not, none of which `ACCEPTED` was written for.

`llvm-undname` is asked the way `tools/mutate.py` asks it: a name with the ARM64EC
marker `$$h` is asked again without it, because no LLVM through 21 reads the marker.
"""

import importlib
import re
import shutil
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
CONFORMANCE = ROOT / "tests" / "conformance"

sys.path.insert(0, str(ROOT / "src"))
# After everything else: these are siblings of this file, and a script run from `tools/`
# has them already. Only a caller that loaded this file by path needs the entry.
if str(TOOLS) not in sys.path:
    sys.path.append(str(TOOLS))

from differential import load_corpus  # noqa: E402
from enumerate import ACCEPTED, library_reading  # noqa: E402
from mutate import RESCUE, ask_tolerantly  # noqa: E402

import demangle  # noqa: E402

#: The package that carries LLVM, and the version the weekly workflow installs. Its import
#: name is `llvmdemangle`. It is optional: nothing in `pyproject.toml` asks for it, and
#: `.github/workflows/upstream.yml` installs it in the one job that uses it, from
#: `tools/llvm-oracle-requirements.txt`, which holds this pin again with the wheel's hash.
PACKAGE = "llvm-demangle-fxti"
PIN = f"{PACKAGE}==1.0.0"

#: The LLVM releases `PIN` bundles. `run` holds a requested release to these before it looks
#: for the package, so that a release nobody could ask for is the same error whether or not
#: the package is installed. Once it is imported its own `SUPPORTED_VERSIONS` is the
#: authority and `readers` holds the release to that; this is what the pin is known to hold,
#: and moves with it.
BUNDLED = range(11, 22)

#: `isItaniumEncoding`, `isRustEncoding`, `isDLangEncoding` and `isMicrosoft`: how
#: `llvm::demangle` and `llvm-cxxfilt` choose a reader. The Itanium prefix is one to four
#: underscores and a `Z`.
ROUTES = {
    "itanium": re.compile(r"_{1,4}Z"),
    "msvc": re.compile(r"\?"),
    "rust": re.compile(r"_R"),
    "d": re.compile(r"_D"),
}

#: Corpora whose expected column is the declaration, not a reference's reading.
DEFECTS = {
    "itanium": "itanium-reference-defects.txt",
    "msvc": "msvc-reference-defects.txt",
}


class Unavailable(Exception):
    """The reference cannot be asked: the package is not installed."""


# ---- LLVM's own spelling, which ACCEPTED was not written for: predicates over (name, ours, theirs)


def _suffix_in_parentheses(ours, theirs):
    """LLVM sets what follows a symbol off in parentheses -- `foo (.llvm.9D1C9369)`,
    `f (.0.0)`. rustc-demangle, which this scheme ports, drops an `.llvm.<hash>` suffix
    and leaves any other attached: `foo`, `f.0.0` (tests/conformance/rustc-upstream.txt)."""
    found = re.search(r" \((\.[^()]*)\)$", theirs)
    if found is None:
        return False
    base = theirs[: found.start()]
    return ours in (base, base + found.group(1))


def _non_ascii_char_escaped(ours, theirs):
    """LLVM writes a `char` constant outside ASCII as `'\\u{df}'`; rustc-demangle prints
    the character, `'ß'`, as `Debug` does."""
    return ours != theirs and "".join(c if ord(c) < 128 else f"\\u{{{ord(c):x}}}" for c in ours) == theirs


def _template_instance_left_as_written(ours, theirs):
    """LLVM's D reader does not expand a template instance: `test.bar!("abc"w).x` comes
    out `test.__T3barVG3uw3_616263Z.x`, the identifier as the mangling spells it."""
    return "__T" in theirs and "__T" not in ours


SPELLINGS = {
    "rust": lambda name, ours, theirs: _suffix_in_parentheses(ours, theirs) or _non_ascii_char_escaped(ours, theirs),
    "d": lambda name, ours, theirs: _template_instance_left_as_written(ours, theirs),
}


# ---- the reference


def binding():
    """The `llvmdemangle` package, or None where it is not installed."""
    try:
        return importlib.import_module("llvmdemangle")
    except ImportError:
        return None


def newest(package):
    return max(package.SUPPORTED_VERSIONS)


def _not_bundled(version, bundled):
    return ValueError(f"LLVM {version} is not bundled; {PACKAGE} has {sorted(bundled)}")


def readers(package, version):
    """`{scheme: read}` for LLVM `version`, each `read(name)` the text or None for a refusal.

    Itanium keeps what follows an `@` out of the reader and puts it back after the
    answer, as `llvm-cxxfilt` does with an ELF symbol version
    (`_ZNSt6vectorIiE4sizeEv@@GLIBCXX_3.4`). `llvm-undname` prints a reading even when
    input is left over, so the count of bytes read is not asked about. LLVM before 13
    has no Rust reader and before 14 no D reader; those schemes are left out.
    """
    if version not in package.SUPPORTED_VERSIONS:
        raise _not_bundled(version, package.SUPPORTED_VERSIONS)
    backend = package.backend(version)

    def itanium(name):
        head, at, tail = name.partition("@")
        text = backend.itanium_demangle(head)
        return None if text is None else text + at + tail

    def msvc(name):
        found = backend.microsoft_demangle(name)
        return None if found is None else found[0]

    found = {"itanium": itanium, "msvc": msvc}
    if version >= 13:
        found["rust"] = backend.rust_demangle
    if version >= 14:
        found["d"] = backend.dlang_demangle
    return found


def corpus_names(directory=CONFORMANCE):
    """`{scheme: [mangled, ...]}`: every distinct name the corpora hold, by LLVM's routing.

    The names only; the expected columns are other references' readings, and what is
    compared here is this library's reading *now*.
    """
    held = set()
    paths = [*directory.glob("*.txt"), *directory.glob("*.txt.gz"), *(directory / "reported").glob("*.txt")]
    for path in sorted(paths):
        held.update(mangled for mangled, _ in load_corpus(path))
    return {scheme: sorted(name for name in held if route.match(name)) for scheme, route in ROUTES.items()}


def reference_defects(scheme, directory=CONFORMANCE):
    corpus = DEFECTS.get(scheme)
    if corpus is None or not (directory / corpus).exists():
        return frozenset()
    return frozenset(mangled for mangled, _ in load_corpus(directory / corpus))


# ---- the comparison


def _ours(scheme, name, style=None):
    try:
        return library_reading(scheme, name, style)
    except (demangle.DemanglingError, RecursionError):
        return None


def _theirs(read, name):
    try:
        return read(name)
    except ValueError:  # a name the C API cannot be given: a lone surrogate
        return None


def gnu_opinion(names):
    """`{name: (c++filt's answer, this library's reading under the gnu style)}`.

    The second opinion `ACCEPTED["itanium"]` takes, as `tools/mutate.py` asks for it. Where
    `c++filt` is not on PATH there is none, and every name then reads as one GNU declined.
    """
    if not names or shutil.which("c++filt") is None:
        return {}
    try:
        answers = ask_tolerantly("c++filt", names, 20000)
    except (OSError, ValueError):  # no resource limits where the platform has none
        return {}
    return {name: (answers.get(name), _ours("itanium", name, "gnu")) for name in names}


def score(scheme, names, read, defects=frozenset(), opinion=None):
    """Compare this library with `read` on `names`; see the module docstring for the classes.

    `opinion(names)` is asked about the names no cheaper rule has accounted for, and is
    only given for Itanium: it is `gnu_opinion`.
    """
    result = {
        "source": f"llvm-{scheme}",
        "offered": len(names),
        "agree": 0,
        "known": 0,
        "unaccounted": [],
        "declined": [],
        "refused": [],
    }
    rescue = RESCUE.get(scheme)
    spelled = SPELLINGS.get(scheme, lambda *_: False)
    undecided = []
    for name in names:
        ours = _ours(scheme, name)
        theirs = _theirs(read, name)
        if rescue is not None and theirs != ours and (stand_in := rescue(name)):
            # Where the marker is part of an identifier the first answer stands; a refused
            # stand-in does not overwrite one that our own spelling still carries `$$h` in.
            answer = _theirs(read, stand_in)
            if answer is not None or ours is None or "$$h" not in ours:
                theirs = answer
        if ours == theirs:
            result["agree"] += 1
        elif theirs is None:
            result["declined"].append((name, ours))
        elif ours is None:
            result["refused"].append((name, theirs))
        elif name in defects or spelled(name, ours, theirs):
            result["known"] += 1
        else:
            undecided.append((name, ours, theirs))

    accepted = ACCEPTED.get(scheme, lambda *_: False)
    opinions = opinion([name for name, _, _ in undecided]) if opinion is not None else {}
    for name, ours, theirs in undecided:
        if accepted(name, ours, theirs, opinions.get(name, (None, None))):
            result["known"] += 1
        else:
            result["unaccounted"].append((name, theirs, ours))
    return result


def run(schemes, version=None):
    """The results for `schemes`, one per scheme LLVM `version` can read (the newest if None).

    Raises `ValueError` when the package does not bundle `version`, which is settled first
    and without the package, and `Unavailable` when the package is not installed.
    """
    if version is not None and version not in BUNDLED:
        raise _not_bundled(version, BUNDLED)
    package = binding()
    if package is None:
        raise Unavailable(f"the {PACKAGE} package is not installed (pip install {PIN})")
    version = newest(package) if version is None else version
    read = readers(package, version)
    names = corpus_names()
    results = []
    for scheme in schemes:
        if scheme not in read:
            print(f"llvm-{scheme}: LLVM {version} has no {scheme} reader, skipped", file=sys.stderr)
            continue
        result = score(
            scheme,
            names[scheme],
            read[scheme],
            defects=reference_defects(scheme),
            opinion=gnu_opinion if scheme == "itanium" else None,
        )
        result["reference"] = f"LLVM {version}"
        results.append(result)
    return results
