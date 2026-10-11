"""MkDocs hook: package the pure-Python source for the browser playground and merge standalone app."""

from __future__ import annotations

import io
import shutil
import stat
import zipfile
from pathlib import Path
from typing import Any

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


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

    archive_bytes = buf.getvalue()

    # Emit to playground/assets/ (for standalone playground)
    playground_assets = root_dir / "playground" / "assets"
    playground_assets.mkdir(parents=True, exist_ok=True)
    playground_zip = playground_assets / "demangle.zip"
    playground_zip.write_bytes(archive_bytes)

    # Also emit to docs/assets/ for backwards compatibility
    docs_assets = root_dir / "docs" / "assets"
    docs_assets.mkdir(parents=True, exist_ok=True)
    docs_zip = docs_assets / "demangle.zip"
    docs_zip.write_bytes(archive_bytes)

    (docs_assets / "demangle.whl").unlink(missing_ok=True)
    (docs_assets / "demangle_bundle.js").unlink(missing_ok=True)
    (playground_assets / "demangle.whl").unlink(missing_ok=True)
    (playground_assets / "demangle_bundle.js").unlink(missing_ok=True)

    return playground_zip


def on_pre_build(config: Any) -> None:
    """MkDocs pre-build event hook."""
    build_assets()


def on_post_build(config: Any) -> None:
    """MkDocs post-build event hook: merge standalone playground into site output."""
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
