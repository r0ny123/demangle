"""Shared fixtures and corpus loading."""

import shutil
import subprocess
from pathlib import Path

import pytest

CONFORMANCE = Path(__file__).parent / "conformance"


def load_corpus(name):
    """Read (mangled, expected) pairs from a conformance file."""
    path = CONFORMANCE / name
    if not path.exists():
        return []
    pairs = []
    for line in path.read_text(encoding="utf-8").splitlines():
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
