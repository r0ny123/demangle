"""The artifact check must pass a faithful build and flag anything the checkout does not hold."""

import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "check_artifacts.py"
if not SCRIPT.is_file():
    pytest.skip("release tooling is not part of this distribution", allow_module_level=True)
spec = importlib.util.spec_from_file_location("check_artifacts", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

FILES = {
    "LICENSE": b"MIT\n",
    "pyproject.toml": b"[project]\n",
    "src/demangle/__init__.py": b'__version__ = "1.0.0"\n',
    "src/demangle/core/reader.py": b"def read():\n    pass\n",
}
METADATA = b"Metadata-Version: 2.4\nName: demangle\nVersion: 1.0.0\n"
INFO = "demangle-1.0.0.dist-info/"
PURE = "demangle-1.0.0-py3-none-any.whl"
COMPILED = "demangle-1.0.0-cp313-cp313-win_amd64.whl"
EXTENSIONS = {"demangle__mypyc.cp313-win_amd64.pyd": b"\0", "demangle/core/reader.cp313-win_amd64.pyd": b"\0"}


class Checkout(module.Source):
    def __init__(self):
        self.tracked = set(FILES)
        self.package = {name for name in FILES if name.startswith("src/demangle/")}
        self.dependencies = []
        self.entry_points = b"[console_scripts]\ndemangle = demangle.cli:main\n"
        self.licenses = {"LICENSE"}
        self.version = "1.0.0"
        self.compiled = {"demangle/core/reader"}

    def read(self, name):
        return FILES[name]


def sdist(directory, files=None, metadata=METADATA):
    with tarfile.open(directory / "demangle-1.0.0.tar.gz", "w:gz") as archive:
        for name, data in {**FILES, "PKG-INFO": metadata, **(files or {})}.items():
            member = tarfile.TarInfo(f"demangle-1.0.0/{name}")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


def wheel(directory, name=PURE, files=None, without=()):
    held = {
        "demangle/__init__.py": FILES["src/demangle/__init__.py"],
        "demangle/core/reader.py": FILES["src/demangle/core/reader.py"],
        f"{INFO}METADATA": METADATA,
        f"{INFO}WHEEL": b"Wheel-Version: 1.0\n",
        f"{INFO}RECORD": b"",
        f"{INFO}entry_points.txt": b"[console_scripts]\ndemangle = demangle.cli:main\n",
        f"{INFO}licenses/LICENSE": FILES["LICENSE"],
        **(files or {}),
    }
    with zipfile.ZipFile(directory / name, "w") as archive:
        for entry, data in held.items():
            if entry not in without:
                archive.writestr(entry, data)


def check(directory):
    return module.problems(directory, Checkout())


def test_faithful_build_passes(tmp_path):
    sdist(tmp_path)
    wheel(tmp_path)
    wheel(tmp_path, COMPILED, {**EXTENSIONS, "demangle/core/reader.py": b"def read():\r\n    pass\r\n"})
    assert check(tmp_path) == []


@pytest.mark.parametrize(
    ("entry", "data"),
    [
        ("demangle_init.pth", b"import demangle\n"),
        ("demangle/_telemetry.py", b"import socket\n"),
        ("demangle/__init__.py", b'__version__ = "1.0.0"\nimport os\n'),
        ("demangle-1.0.0.data/scripts/run", b"#!/bin/sh\n"),
        ("demangle/core/reader.cpython-313-darwin.so", b"\0"),
        ("demangle/core/other.cpython-313-darwin.so", b"\0"),
        (f"{INFO}entry_points.txt", b"[console_scripts]\ndemangle = demangle.cli:main\npip = demangle.cli:main\n"),
        (f"{INFO}METADATA", METADATA + b"Requires-Dist: requests\n"),
    ],
)
def test_wheel_holding_something_else_is_flagged(tmp_path, entry, data):
    sdist(tmp_path)
    wheel(tmp_path, files={entry: data})
    found = check(tmp_path)
    assert len(found) == 1
    assert PURE in found[0]


def test_wheel_missing_a_module_or_an_extension_is_flagged(tmp_path):
    sdist(tmp_path)
    wheel(tmp_path, without=["demangle/core/reader.py"])
    wheel(tmp_path, COMPILED, {"demangle__mypyc.cp313-win_amd64.pyd": b"\0"})
    found = check(tmp_path)
    assert len(found) == 2
    assert "`src/demangle/core/reader.py` is missing" in found[1]
    assert "compiled extensions" in found[0]


def test_sdist_holding_something_else_is_flagged(tmp_path):
    sdist(tmp_path, {"setup.py": b"import os\n", "pyproject.toml": b"[project]\ndependencies = ['requests']\n"})
    wheel(tmp_path)
    found = check(tmp_path)
    assert len(found) == 2
    assert "`pyproject.toml` differs" in found[0]
    assert "`setup.py` is not a tracked file" in found[1]


def test_sdist_link_is_flagged(tmp_path):
    with tarfile.open(tmp_path / "demangle-1.0.0.tar.gz", "w:gz") as archive:
        member = tarfile.TarInfo("demangle-1.0.0/LICENSE")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc/passwd"
        archive.addfile(member)
    assert any("not a plain file" in problem for problem in check(tmp_path))


def test_undeclared_dependency_is_flagged(tmp_path):
    requiring = METADATA + b"Requires-Dist: requests\n"
    sdist(tmp_path, metadata=requiring)
    wheel(tmp_path, files={f"{INFO}METADATA": requiring})
    found = check(tmp_path)
    assert len(found) == 1
    assert "requests" in found[0]


def test_stray_file_and_missing_pure_wheel_are_flagged(tmp_path):
    sdist(tmp_path)
    wheel(tmp_path, "demangle-0.9.9-py3-none-any.whl")
    found = check(tmp_path)
    assert len(found) == 2
    assert "demangle-0.9.9-py3-none-any.whl" in found[0]
    assert "exactly one pure wheel" in found[1]
