"""Build the site: zip the pure-Python source for the playground, fetch the Pyodide runtime it
runs on, build the pages with Zensical, copy the playground in, and fail on text a browser
would read as a tag.

Zensical runs no build hooks, so this does what they did. `python docs/build.py` is the whole
build; `zensical serve` previews the pages without the playground."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import marshal
import re
import shutil
import stat
import subprocess
import sys
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
#: The Python inside the pinned runtime (`info.python` in its `pyodide-lock.json`). A build
#: run on the same CPython version ships bytecode, which spares the browser compiling the
#: package on every visit: about half a second before the first name is read.
PYODIDE_PYTHON = (3, 13)

#: The Pyodide release the playground runs on.
PYODIDE_VERSION = "0.29.5"
PYODIDE_SOURCE = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
#: Everything the worker loads to start Python, with the SHA-256 of each file. The site serves
#: these itself: a visitor's browser fetches nothing from a third party, and what it is served
#: is what was checked here when the site was built. The digests were taken from jsDelivr and
#: match the files in the `pyodide` npm package of the same version. To move to another
#: release, change the version here and take the digests the same way.
PYODIDE_FILES = {
    "pyodide.mjs": "510ca14305230018f50c40fb81be673afd68080e70ce7733fcce6600dd6a1714",
    "pyodide.asm.js": "356c42f69e1695397e9d8670bd3c2e678248cde76e18bdae1731e848537b47d7",
    "pyodide.asm.wasm": "54309a5a2cfd757b1f0fdbc9093c92503f7b341500110329d932487183912718",
    "python_stdlib.zip": "831fd1e535084b972f87c6a35275de3d236efd5166f5e77ce89497710635e73c",
    "pyodide-lock.json": "14d2c2dba101277999e17135e653d8f15389ad1437f53eae213bf0c3cdff723d",
}


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def fetch_pyodide(root_dir: Path, download: Callable[[str], bytes] = _download) -> Path:
    """Put the pinned Pyodide runtime under ``playground/assets/pyodide``.

    A file already there with the right digest is kept, so only the first build needs a
    network, and one the pin does not name, such as a previous release's, is removed so the
    site never serves it. A download whose digest is not the pinned one is never written, and fails the
    build: a site without the runtime is better than a site serving one nobody checked.
    """
    runtime = root_dir / "playground" / "assets" / "pyodide"
    runtime.mkdir(parents=True, exist_ok=True)
    for path in runtime.iterdir():
        if path.name not in PYODIDE_FILES:
            path.unlink()
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


def _bytecode(source: bytes, name: str) -> bytes:
    """An unchecked hash-based ``.pyc`` (PEP 552): the archive never changes once built."""
    code = compile(source, name, "exec", dont_inherit=True)
    flags = (1).to_bytes(4, "little")
    return importlib.util.MAGIC_NUMBER + flags + importlib.util.source_hash(source) + marshal.dumps(code)


def build_assets(root_dir: Path) -> Path:
    """Build a deterministic zipimportable archive of ``src/demangle``.

    Modules go in as bytecode when this Python is the runtime's, and as source otherwise.
    """
    src = root_dir / "src" / "demangle"
    if not (src / "__init__.py").is_file():
        raise FileNotFoundError(f"demangle source package missing: {src}")

    bytecode = sys.implementation.name == "cpython" and sys.version_info[:2] == PYODIDE_PYTHON
    buf = io.BytesIO()
    notices = [root_dir / name for name in ("LICENSE", "NOTICE")]
    with zipfile.ZipFile(buf, "w") as archive:
        for path in [*sorted(src.rglob("*")), *notices]:
            if not path.is_file() or path.suffix == ".pyc" or "__pycache__" in path.parts:
                continue
            name = path.name if path in notices else path.relative_to(src.parent).as_posix()
            content = path.read_bytes()
            if bytecode and path.suffix == ".py":
                content = _bytecode(content, name)
                name += "c"
            info = zipfile.ZipInfo(name, _ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

    playground_assets = root_dir / "playground" / "assets"
    playground_assets.mkdir(parents=True, exist_ok=True)
    playground_zip = playground_assets / "demangle.zip"
    playground_zip.write_bytes(buf.getvalue())

    return playground_zip


#: What Markdown and mkdocstrings write into a page. Any other tag is prose that put a name
#: between angle brackets outside a code span, as a grammar's `<encoding>` does, and a
#: browser shows none of it.
_ELEMENTS = (
    "a abbr blockquote br code dd del details div dl dt em h1 h2 h3 h4 h5 h6 hr img kbd li ol"
    " p pre small span strong sub summary sup table tbody td th thead tr ul"
)


def stray_tags(html: str) -> list[str]:
    """The tags in a rendered page that are really swallowed text."""
    return sorted(set(re.findall(r"<([a-zA-Z][\w-]*)", html)).difference(_ELEMENTS.split()))


def check_pages(site_dir: Path) -> list[str]:
    """Every page whose text holds a swallowed tag, with the tags."""
    problems = []
    for page in sorted(site_dir.rglob("*.html")):
        content = re.search(r"<article\b[^>]*>(.*?)</article>", page.read_text(encoding="utf-8"), re.S)
        if content and (stray := stray_tags(content.group(1))):
            tags = ", ".join(f"<{tag}>" for tag in stray)
            problems.append(f"{page.relative_to(site_dir).as_posix()}: {tags} needs backticks")
    return problems


def build(root_dir: Path) -> None:
    build_assets(root_dir)
    fetch_pyodide(root_dir)
    subprocess.run([sys.executable, "-m", "zensical", "build", "--strict"], cwd=root_dir, check=True)
    site_dir = root_dir / "site"
    if problems := check_pages(site_dir):
        sys.exit("\n".join(problems))
    shutil.copytree(
        root_dir / "playground",
        site_dir / "playground",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


if __name__ == "__main__":
    build(Path(__file__).resolve().parent.parent)
