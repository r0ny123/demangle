"""Shared fixtures and corpus loading."""

import shutil
import subprocess
from pathlib import Path

import pytest

CONFORMANCE = Path(__file__).parent / "conformance"


def load_corpus(name):
    """Read (mangled, expected) pairs from a conformance file.

    A corpus may be stored gzipped. libcxxabi's is 5MB of text and 580KB compressed, and
    a source repository is a bad place to keep four and a half megabytes that gzip would
    have removed; nothing else about it changes.
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


def reference_available(tool):
    return shutil.which(tool) is not None


def reference_demangle(tool, name):
    result = subprocess.run([tool, name], capture_output=True, text=True)
    return result.stdout.strip()


requires_llvm_cxxfilt = pytest.mark.skipif(not reference_available("llvm-cxxfilt"), reason="llvm-cxxfilt not installed")
requires_llvm_undname = pytest.mark.skipif(not reference_available("llvm-undname"), reason="llvm-undname not installed")
