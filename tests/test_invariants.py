"""The invariant gate must check a workload before reporting success."""

import os
import pathlib
import subprocess
import sys

import pytest

TOOLS = pathlib.Path(__file__).resolve().parent.parent / "tools"


@pytest.mark.parametrize("selector", ["does-not-exist", "__unmatched_audit_selector__"])
def test_unmatched_corpus_selector_is_an_argument_error(selector):
    result = subprocess.run(
        [sys.executable, str(TOOLS / "invariants.py"), "--corpus", selector, "--count", "1", "--quiet"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(TOOLS.parent / "src")},
        timeout=30,
    )
    assert result.returncode == 2
    assert "no usable corpus matches --corpus" in result.stderr
    assert "no invariant broken" not in result.stdout


def test_matching_corpus_selector_runs_a_workload():
    result = subprocess.run(
        [sys.executable, str(TOOLS / "invariants.py"), "--corpus", "jni", "--count", "1"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(TOOLS.parent / "src")},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "jni" in result.stdout
    assert "1 mutants" in result.stdout
    assert "no invariant broken" in result.stdout


@pytest.mark.parametrize("tool,option", [("invariants", "--count"), ("mutate", "--count"), ("enumerate", "--length")])
def test_negative_workload_size_is_an_argument_error(tool, option):
    result = subprocess.run(
        [sys.executable, str(TOOLS / f"{tool}.py"), option, "-1"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(TOOLS.parent / "src")},
        timeout=30,
    )
    assert result.returncode == 2
    assert f"{option} must be non-negative" in result.stderr


@pytest.mark.parametrize("tool,option", [("invariants", "--count"), ("mutate", "--count"), ("enumerate", "--length")])
def test_zero_workload_size_remains_an_allowed_dry_run(tool, option):
    selection = ["--corpus", "jni"] if tool == "invariants" else ["--scheme", "ada"]
    result = subprocess.run(
        [sys.executable, str(TOOLS / f"{tool}.py"), *selection, option, "0", "--quiet"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(TOOLS.parent / "src")},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
