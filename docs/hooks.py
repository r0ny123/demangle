"""MkDocs hook: package the pure-Python source for the browser playground."""

from __future__ import annotations

import io
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
    assets_dir = root_dir / "docs" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

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

    zip_path = assets_dir / "demangle.zip"
    zip_path.write_bytes(buf.getvalue())
    # Remove artifacts emitted by the former fake-wheel/base64 packaging path so a
    # build from a working tree cannot publish stale copies alongside the zip.
    (assets_dir / "demangle.whl").unlink(missing_ok=True)
    (assets_dir / "demangle_bundle.js").unlink(missing_ok=True)
    return zip_path


def on_pre_build(config: Any) -> None:
    """MkDocs pre-build event hook."""
    build_assets()


if __name__ == "__main__":
    path = build_assets()
    print(f"Generated archive: {path} ({path.stat().st_size} bytes)")
