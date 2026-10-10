"""Corpus replay must not treat an empty workload as passing conformance."""

import os
import pathlib
import subprocess
import sys

import pytest

TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"


@pytest.mark.parametrize("contents", ["", "# comments only\n\n"])
def test_empty_replay_fails(tmp_path, contents):
    corpus = tmp_path / "empty.txt"
    corpus.write_text(contents)
    result = replay(corpus)
    assert result.returncode == 1
    assert "no corpus names were replayed" in result.stderr
    assert "0/0 exact" not in result.stdout


def test_replay_with_names_still_passes(tmp_path):
    corpus = tmp_path / "names.txt"
    corpus.write_text("_Z1fv\tf()\n")
    result = replay(corpus)
    assert result.returncode == 0, result.stderr
    assert "1/1 exact" in result.stdout


def replay(corpus):
    return subprocess.run(
        [sys.executable, str(TOOLS / "differential.py"), "--corpus", str(corpus), "--quiet"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(TOOLS.parent / "src")},
        timeout=30,
    )
