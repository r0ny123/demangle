"""Shared fixtures and corpus loading."""

import shutil
import subprocess
from pathlib import Path

import pytest

CONFORMANCE = Path(__file__).parent / "conformance"


def load_corpus(name):
    """Read (mangled, expected) pairs from a conformance file.

    A corpus may be stored gzipped: libcxxabi's is 5MB of text and 580KB compressed, so
    it is kept compressed. The format inside is the same.
    """
    path = CONFORMANCE / name
    if path.exists():
        text = path.read_text(encoding="utf-8")
    elif path.with_suffix(path.suffix + ".gz").exists():
        import gzip

        text = gzip.decompress(path.with_suffix(path.suffix + ".gz").read_bytes()).decode("utf-8")
    else:
        return []
    pairs = []
    for line in text.splitlines():
        if not line or line.startswith("#") or "\t" not in line:
            continue
        mangled, expected = line.split("\t", 1)
        pairs.append((mangled, expected))
    return pairs


def corpus_files():
    """Every corpus, named as `load_corpus` takes it: the checked-in ones and `reported/`."""
    names = {path.name.removesuffix(".gz") for path in CONFORMANCE.glob("*.txt*")}
    names.update(f"reported/{path.name}" for path in (CONFORMANCE / "reported").glob("*.txt"))
    return sorted(names)


def reference_available(tool):
    return shutil.which(tool) is not None


def reference_demangle(tool, name):
    result = subprocess.run([tool, name], capture_output=True, text=True)
    return result.stdout.strip()


def gnu_cxxfilt_available():
    """Whether the `c++filt` on PATH is *GNU's*, rather than something else of that name.

    A name on PATH is not an identity. macOS ships LLVM's demangler as `c++filt`, and it
    is a different reference: it closes `>>` up where GNU writes `> >`, so scoring the
    gnu style against it compares two spelling conventions and reports thousands of
    differences that are not disagreements about anything. The version banner is the
    only thing that tells them apart -- GNU's opens `GNU c++filt`, LLVM's names LLVM --
    so this asks.
    """
    if not reference_available("c++filt"):
        return False
    try:
        banner = subprocess.run(["c++filt", "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return "GNU c++filt" in f"{banner.stdout}{banner.stderr}"


requires_gnu_cxxfilt = pytest.mark.skipif(not gnu_cxxfilt_available(), reason="GNU c++filt not installed")
requires_llvm_cxxfilt = pytest.mark.skipif(not reference_available("llvm-cxxfilt"), reason="llvm-cxxfilt not installed")
requires_llvm_undname = pytest.mark.skipif(not reference_available("llvm-undname"), reason="llvm-undname not installed")
