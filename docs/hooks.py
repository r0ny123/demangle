"""MkDocs hook: zip the pure-Python source for the playground, fetch the Pyodide runtime it
runs on, and copy the playground into the site."""

from __future__ import annotations

import hashlib
import io
import shutil
import stat
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

#: The Pyodide release the playground runs on; `playground/playground-worker.js` names the same
#: one, and `tests/test_playground.py` holds the two together.
PYODIDE_VERSION = "0.26.4"
PYODIDE_SOURCE = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
#: Everything the worker loads to start Python, with the SHA-256 of each file. The site serves
#: these itself: a visitor's browser fetches nothing from a third party, and what it is served
#: is what was checked here when the site was built. The digests were taken from jsDelivr and
#: match the files in the `pyodide` npm package of the same version. To move to another
#: release, change the version here and in the worker, and take the digests the same way.
PYODIDE_FILES = {
    "pyodide.js": "c0069107621d5b942a659e737a12e774cc0451feaa2256f475d72e071d844ec7",
    "pyodide.asm.js": "919560652ed3dad3707cb3a394785da1e046fb13dc0defa162058ff230cb7eed",
    "pyodide.asm.wasm": "b7e66a19427a55010ac3367c1b6c64b893f9826f783412945fdf0c3337f3bc94",
    "python_stdlib.zip": "72894522b791858b9d613ac786b951d8b5094035dcf376313ea24a466810f336",
    "pyodide-lock.json": "cd50b49de944c579045e122fe8628b31f9ce446379f032f36c05e273d38766e0",
}


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def fetch_pyodide(root_dir: Path | None = None, download: Callable[[str], bytes] = _download) -> Path:
    """Put the pinned Pyodide runtime under ``playground/assets/pyodide``.

    A file already there with the right digest is kept, so only the first build needs a
    network. A download whose digest is not the pinned one is never written, and fails the
    build: a site without the runtime is better than a site serving one nobody checked.
    """
    if root_dir is None:
        root_dir = Path(__file__).resolve().parent.parent
    runtime = root_dir / "playground" / "assets" / "pyodide"
    runtime.mkdir(parents=True, exist_ok=True)
    for name, expected in PYODIDE_FILES.items():
        path = runtime / name
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            continue
        content = download(PYODIDE_SOURCE + name)
        found = hashlib.sha256(content).hexdigest()
        if found != expected:
            raise ValueError(f"{PYODIDE_SOURCE}{name}: SHA-256 is {found}, not the pinned {expected}")
        path.write_bytes(content)
    return runtime


def build_assets(root_dir: Path | None = None) -> Path:
    """Build a deterministic zipimportable archive of ``src/demangle``."""
    if root_dir is None:
        root_dir = Path(__file__).resolve().parent.parent

    src = root_dir / "src" / "demangle"
    if not (src / "__init__.py").is_file():
        raise FileNotFoundError(f"demangle source package missing: {src}")

    buf = io.BytesIO()
    notices = [root_dir / name for name in ("LICENSE", "NOTICE")]
    with zipfile.ZipFile(buf, "w") as archive:
        for path in [*sorted(src.rglob("*")), *notices]:
            if not path.is_file() or path.suffix == ".pyc" or "__pycache__" in path.parts:
                continue
            name = path.name if path in notices else path.relative_to(src.parent).as_posix()
            info = zipfile.ZipInfo(name, _ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

    playground_assets = root_dir / "playground" / "assets"
    playground_assets.mkdir(parents=True, exist_ok=True)
    playground_zip = playground_assets / "demangle.zip"
    playground_zip.write_bytes(buf.getvalue())

    return playground_zip


def on_pre_build(config: Any) -> None:
    """Build the archive and fetch the runtime before the site, so the copy below finds them."""
    build_assets()
    fetch_pyodide()


def on_post_build(config: Any) -> None:
    """Copy `playground/` into the built site."""
    site_dir = Path(config["site_dir"])
    playground_src = Path(__file__).resolve().parent.parent / "playground"
    playground_dest = site_dir / "playground"
    if playground_src.exists():
        shutil.copytree(
            playground_src,
            playground_dest,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )


if __name__ == "__main__":
    path = build_assets()
    print(f"Generated archive: {path} ({path.stat().st_size} bytes)")
