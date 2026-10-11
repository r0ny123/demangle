"""The performance gate must reject work that disappeared, not report a speedup."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("bench", ROOT / "benchmarks" / "bench.py")
if spec is None or spec.loader is None:
    pytest.skip("benchmarks/bench.py is not part of this distribution", allow_module_level=True)
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


@pytest.mark.parametrize("missing", ["cold", "warm", "negative", "structured", "all"])
def test_gate_rejects_missing_workloads(tmp_path, monkeypatch, capsys, missing):
    baseline = json.loads((ROOT / "benchmarks" / "baseline.json").read_text())
    results = {name: data for name, data in baseline.items() if name != missing}
    if missing == "all":
        results = {"calibration": baseline["calibration"]}
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline))
    monkeypatch.setattr(bench, "BASELINE", path)
    monkeypatch.setattr(bench, "run", lambda: results)
    monkeypatch.setattr(bench, "report", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["bench.py", "--check"])

    assert bench.main() == 1
    assert "benchmark phases missing" in capsys.readouterr().out


@pytest.mark.parametrize("phase", ["swift", "swift_parse", "signature"])
def test_gate_rejects_phases_that_did_not_complete_their_work(tmp_path, monkeypatch, capsys, phase):
    baseline = json.loads((ROOT / "benchmarks" / "baseline.json").read_text())
    results = {name: dict(data) for name, data in baseline.items()}
    results[phase]["succeeded"] = results[phase]["names"] - 1
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline))
    monkeypatch.setattr(bench, "BASELINE", path)
    monkeypatch.setattr(bench, "run", lambda: results)
    monkeypatch.setattr(bench, "report", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["bench.py", "--check"])

    assert bench.main() == 1
    assert f"{phase} benchmark succeeded" in capsys.readouterr().out
