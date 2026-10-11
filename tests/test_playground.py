"""The browser playground archive and its one-parse Python bridge."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import demangle

ROOT = Path(__file__).parent.parent
PLAYGROUND_HTML = ROOT / "playground" / "index.html"
PLAYGROUND_MD = ROOT / "docs" / "playground.md"
PRESET_SOURCE = PLAYGROUND_HTML if PLAYGROUND_HTML.is_file() else PLAYGROUND_MD
PRESETS = (
    re.findall(
        r'<button\b[^>]*\bdata-symbol="([^"]+)"[^>]*\bdata-scheme="([^"]+)"',
        PRESET_SOURCE.read_text(encoding="utf-8"),
    )
    if PRESET_SOURCE.exists()
    else []
)


def _load_module(name: str, path: Path):
    """Load a docs-only module, or skip cleanly in a wheel-only checkout."""
    if not path.is_file():
        pytest.skip("docs assets are not part of this distribution")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bridge():
    bridge_path = ROOT / "playground" / "playground_bridge.py"
    if not bridge_path.is_file():
        bridge_path = ROOT / "docs" / "assets" / "playground_bridge.py"
    return _load_module("playground_bridge_for_tests", bridge_path)


@pytest.fixture
def build_assets():
    return _load_module("playground_hooks_for_tests", ROOT / "docs/hooks.py").build_assets


@pytest.mark.parametrize(("symbol", "language"), PRESETS or [(None, None)])
def test_every_preset_is_accepted(bridge, symbol, language):
    if symbol is None:
        pytest.skip("playground is not part of this distribution")
    result = json.loads(bridge.playground_demangle(symbol, language))
    assert result["success"], f"{language} preset {symbol!r}: {result['error']}"
    assert result["language"] == language
    assert result["ast"] is not None
    assert result["demangled"] == demangle.demangle(symbol, language=language)
    assert result["output_variants"]["full"] == result["demangled"]
    assert result["output_variants"]["both"] == f"{symbol} ==> {result['demangled']}"
    parts = demangle.signature(symbol, language=language)
    assert result["output_variants"]["base-name"] == parts.base_name


def test_output_variants_match_cli_for_both_styles(bridge):
    """The browser's four display modes match the command for varied name shapes."""
    cases = [
        ("_ZTVN10__cxxabiv117__class_type_infoE", "itanium"),  # special
        ("_ZNSt6vectorIiSaIiEE9push_backERKi@@GLIBCXX_3.4", "itanium"),  # decorated
        ("_ZNSt6vectorIiSaIiEE9push_backERKi", "itanium"),  # function/template spacing
    ]
    script = r"""
import contextlib, io, json, sys
from demangle.cli import main
cases = json.loads(sys.argv[1])
rows = []
for symbol, language in cases:
    for style in ("llvm", "gnu"):
        for label, mode in (("full", []), ("both", ["--both"]),
                            ("base-name", ["--base-name"]), ("no-params", ["--no-params"])):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(["--language", language, "--style", style, *mode, symbol])
            rows.append([symbol, language, style, label, status, output.getvalue().rstrip("\n")])
print(json.dumps(rows))
"""
    cli = subprocess.run(
        [sys.executable, "-c", script, json.dumps(cases)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        check=True,
    )
    actual = json.loads(cli.stdout)
    for symbol, language, style, label, status, output in actual:
        assert status == 0
        expected = json.loads(bridge.playground_demangle(symbol, language, style))["output_variants"][label]
        assert output == expected, (symbol, style, label)


def test_malformed_symbol_returns_error_and_identity_output(bridge):
    result = json.loads(bridge.playground_demangle("not-a-mangled-symbol"))
    assert not result["success"]
    assert result["error"]
    assert result["ast"] is None
    assert result["output_variants"]["full"] == "not-a-mangled-symbol"


def test_empty_input_returns_empty_result(bridge):
    result = json.loads(bridge.playground_demangle("  "))
    assert result["empty"]
    assert result["output_variants"] == {"full": "", "both": "", "base-name": "", "no-params": ""}


def test_success_path_reads_tree_once(bridge, monkeypatch):
    original = bridge._read
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(bridge, "_read", counted)
    result = json.loads(bridge.playground_demangle("_ZN3foo3barEv"))
    assert result["success"]
    assert calls == 1


def test_source_archive_is_deterministic_and_importable(tmp_path, build_assets):
    package = tmp_path / "src" / "demangle"
    package.parent.mkdir()
    shutil.copytree(ROOT / "src" / "demangle", package)
    for name in ("LICENSE", "NOTICE"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    # The builder must exclude caches even when they exist in the source tree.
    cache = package / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / "stale.pyc").write_bytes(b"stale bytecode")

    archive_path = build_assets(tmp_path)
    first_bytes = archive_path.read_bytes()
    (package / "__init__.py").touch()
    assert build_assets(tmp_path).read_bytes() == first_bytes

    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert "demangle/__init__.py" in names
        assert "demangle/_signature.py" in names
        for name in ("LICENSE", "NOTICE"):
            assert archive.read(name) == (ROOT / name).read_bytes()
        assert all("__pycache__" not in name and not name.endswith(".pyc") for name in names)
        assert archive.read("demangle/__init__.py") == (package / "__init__.py").read_bytes()

    script = "import demangle; print(demangle.__version__, demangle.demangle('_ZN3foo3barEi'))"
    env = {**os.environ, "PYTHONPATH": str(archive_path)}
    imported = subprocess.run(
        [sys.executable, "-S", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert imported.stdout.strip() == f"{demangle.__version__} foo::bar(int)"
