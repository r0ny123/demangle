"""`tools/repro.py`, run with no reference demangler on PATH."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPRO = Path(__file__).parent.parent / "tools" / "repro.py"
NAME = "_ZNSt6vectorIiSaIiEE9push_backERKi"


def run(*arguments, stdin=None):
    if not REPRO.exists():  # pragma: no cover - only in a wheel-only checkout
        pytest.skip("tools/repro.py is not part of this distribution")
    environment = {**os.environ, "PATH": "", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, str(REPRO), *arguments],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=120,
    )


def test_it_prints_both_styles_a_report_and_a_corpus_line():
    result = run(NAME)
    assert result.returncode == 0, result.stderr
    assert "scheme" in result.stdout and "itanium (detected)" in result.stdout
    assert "std::vector<int, std::allocator<int>>::push_back(int const&)" in result.stdout
    assert "std::vector<int, std::allocator<int> >::push_back(int const&)" in result.stdout
    assert "### What this library prints" in result.stdout
    assert "tests/conformance/reported/itanium.txt" in result.stdout
    assert f"{NAME}\t<what the declaration says>" in result.stdout
    assert "skipped llvm-cxxfilt" in result.stderr


def test_it_reads_the_name_from_stdin():
    result = run(stdin=f"\n{NAME}\n")
    assert result.returncode == 0, result.stderr
    assert "itanium (detected)" in result.stdout


def test_a_name_no_scheme_claims_needs_a_language():
    result = run("not_a_symbol")
    assert result.returncode == 2
    assert "--language" in result.stderr
