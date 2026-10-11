"""MkDocs hook: package src/demangle into assets/demangle.whl and assets/demangle_bundle.js."""

from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path
from typing import Any


def build_assets(root_dir: Path | None = None) -> tuple[Path, Path]:
    """Build pure-Python wheel and base64 JavaScript bundle for Pyodide playground."""
    if root_dir is None:
        root_dir = Path(__file__).resolve().parent.parent

    src = root_dir / "src" / "demangle"
    assets_dir = root_dir / "docs" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Read package version
    init_file = src / "__init__.py"
    version = "0.5.2"
    for line in init_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            version = line.split("=")[1].strip().strip("\"'")
            break

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        # 1. Package source files under 'demangle/'
        for p in sorted(src.rglob("*")):
            if p.is_file() and not p.name.endswith(".pyc") and "__pycache__" not in p.parts:
                arcname = str(p.relative_to(src.parent))
                z.write(p, arcname)

        # 2. Write minimal PEP 427 wheel dist-info
        dist_info = f"demangle-{version}.dist-info"
        z.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: demangle-builder\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        metadata = (
            f"Metadata-Version: 2.1\n"
            f"Name: demangle\n"
            f"Version: {version}\n"
            f"Summary: Read mangled symbol names in pure Python.\n"
        )
        z.writestr(f"{dist_info}/METADATA", metadata)
        z.writestr(
            f"{dist_info}/entry_points.txt",
            "[console_scripts]\ndemangle = demangle.cli:main\n",
        )

    wheel_bytes = buf.getvalue()

    # Write binary wheel
    whl_path = assets_dir / "demangle.whl"
    whl_path.write_bytes(wheel_bytes)

    # Write Base64 JavaScript bundle
    b64_str = base64.b64encode(wheel_bytes).decode("ascii")
    bundle_js_path = assets_dir / "demangle_bundle.js"
    bundle_js_path.write_text(
        "// Auto-generated pure-Python demangle bundle for offline/CORS-free Pyodide WebAssembly\n"
        f'window.DEMANGLE_BUNDLE_BASE64 = "{b64_str}";\n',
        encoding="utf-8",
    )

    return whl_path, bundle_js_path


def on_pre_build(config: Any) -> None:
    """MkDocs pre-build event hook."""
    build_assets()


if __name__ == "__main__":
    w, b = build_assets()
    print(f"Generated wheel: {w} ({w.stat().st_size} bytes)")
    print(f"Generated bundle: {b} ({b.stat().st_size} bytes)")
