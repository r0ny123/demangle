"""Check that the files about to be published hold this checkout and nothing else.

A release is approved by hand, and the approver reads the source, not the archives. This
closes the gap between the two: every file in the sdist and in each wheel must be a tracked
file with the same bytes, the metadata the build backend writes, or one of the extensions
`tools/hatch_build.py` compiles. A `.pth` file, a data directory, an extra module, an
undeclared dependency or a second console script all fail here.

What it cannot see is inside a compiled extension: those are trusted to come from the `.py`
beside them because the wheel job built them from this commit.

Exit status: 0 clean, 1 something needs a person.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

PACKAGE = "demangle"
EXTENSION = re.compile(r"\.(?:so|pyd)$")


class Source:
    """The tracked files of a checkout, and what its build configuration promises."""

    def __init__(self, root: Path) -> None:
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True).stdout
        self.root = root
        self.tracked = {name for name in listed.decode().split("\0") if name}
        self.package = {name for name in self.tracked if name.startswith(f"src/{PACKAGE}/")}
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.dependencies = sorted(project["dependencies"])
        scripts = "".join(f"{name} = {target}\n" for name, target in project["scripts"].items())
        self.entry_points = f"[console_scripts]\n{scripts}".encode()
        self.licenses = set(project["license-files"])
        init = (root / "src" / PACKAGE / "__init__.py").read_text(encoding="utf-8")
        self.version = re.findall(r'^__version__ = "(.*)"$', init, re.MULTILINE)[0]
        hook = ast.parse((root / "tools" / "hatch_build.py").read_text(encoding="utf-8"))
        compiled = next(
            ast.literal_eval(node.value)
            for node in hook.body
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "COMPILED"
        )
        self.compiled = {name.removeprefix("src/").removesuffix(".py") for name in compiled}

    def read(self, name: str) -> bytes:
        return (self.root / name).read_bytes()


def _same(built: bytes, tracked: bytes) -> bool:
    # A wheel built on Windows holds the checkout's CRLF line endings.
    return built == tracked or built.replace(b"\r\n", b"\n") == tracked.replace(b"\r\n", b"\n")


def sdist_problems(path: Path, source: Source) -> tuple[list[str], bytes]:
    found, metadata, seen = [], b"", set()
    prefix = f"{PACKAGE}-{source.version}/"
    with tarfile.open(path) as archive:
        for member in archive:
            name = member.name.removeprefix(prefix)
            if not member.isfile() or not member.name.startswith(prefix):
                found.append(f"`{member.name}` is not a plain file under `{prefix}`")
                continue
            held = archive.extractfile(member)
            assert held is not None
            data = held.read()
            if name == "PKG-INFO":
                metadata = data
            elif name not in source.tracked:
                found.append(f"`{name}` is not a tracked file")
            elif data != source.read(name):
                found.append(f"`{name}` differs from the checkout")
            seen.add(name)
    found += [f"`{name}` is missing" for name in sorted(source.package - seen)]
    return found, metadata


def wheel_problems(path: Path, source: Source, metadata: bytes) -> list[str]:
    found, seen, extensions = [], set(), set()
    pure = path.name.endswith("-py3-none-any.whl")
    info = f"{PACKAGE}-{source.version}.dist-info/"
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            name = entry.filename
            if entry.is_dir():
                continue
            data = archive.read(entry)
            module = name.split(".", 1)[0]
            if EXTENSION.search(name) and (module in source.compiled or module == f"{PACKAGE}__mypyc"):
                extensions.add(module)
            elif f"src/{name}" in source.package:
                seen.add(f"src/{name}")
                if not _same(data, source.read(f"src/{name}")):
                    found.append(f"`{name}` differs from the checkout")
            elif name == f"{info}METADATA":
                if data != metadata:
                    found.append("`METADATA` differs from the sdist's `PKG-INFO`")
            elif name == f"{info}entry_points.txt":
                if data != source.entry_points:
                    found.append("`entry_points.txt` is not what `pyproject.toml` declares")
            elif name.removeprefix(f"{info}licenses/") in source.licenses:
                if not _same(data, source.read(name.removeprefix(f"{info}licenses/"))):
                    found.append(f"`{name}` differs from the checkout")
            elif name not in (f"{info}WHEEL", f"{info}RECORD"):
                found.append(f"`{name}` is not a tracked file or an expected extension")
    found += [f"`{name}` is missing" for name in sorted(source.package - seen)]
    expected = set() if pure else source.compiled | {f"{PACKAGE}__mypyc"}
    if extensions != expected:
        found.append(f"compiled extensions are {sorted(extensions)}, expected {sorted(expected)}")
    return found


def problems(directory: Path, source: Source) -> list[str]:
    # uv leaves a `.gitignore` beside what it builds; an upload globs past hidden files.
    files = sorted(path for path in directory.iterdir() if not path.name.startswith("."))
    sdist = directory / f"{PACKAGE}-{source.version}.tar.gz"
    wheels = [path for path in files if path.name.startswith(f"{PACKAGE}-{source.version}-") and path.suffix == ".whl"]
    strays = [path for path in files if path not in [sdist, *wheels]]
    found = [f"`{path.name}` is not a distribution of {source.version}" for path in strays]
    if not sdist.is_file():
        return [*found, "there is no sdist"]
    if sum(path.name.endswith("-py3-none-any.whl") for path in wheels) != 1:
        found.append("there should be exactly one pure wheel")
    sdist_found, metadata = sdist_problems(sdist, source)
    found += [f"{sdist.name}: {problem}" for problem in sdist_found]
    requires = sorted(re.findall(r"^Requires-Dist: (.*)$", metadata.decode(), re.MULTILINE))
    if requires != source.dependencies:
        found.append(f"the metadata requires {requires}, but `pyproject.toml` declares {source.dependencies}")
    for wheel in wheels:
        found += [f"{wheel.name}: {problem}" for problem in wheel_problems(wheel, source, metadata)]
    return found


def main(argv: list[str]) -> int:
    directory = Path(argv[1] if len(argv) > 1 else "dist")
    found = problems(directory, Source(Path(__file__).resolve().parents[1]))
    for problem in found:
        print(problem)
    if not found:
        print(f"Every file in {directory} matches the checkout")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
