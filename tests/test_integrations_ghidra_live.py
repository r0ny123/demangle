"""Opt-in: run `integrations/demangle_ghidra.py` in a real headless Ghidra.

`test_integrations_hosts.py` runs the script against stand-ins written from Ghidra's
documentation. This runs it in Ghidra itself, through PyGhidra: it compiles a small C++
program, imports it with analysis off so that every symbol is as the ELF loader left it,
runs the script twice, and reads the symbol table back.

It needs, and is skipped without, all of:

- `DEMANGLE_GHIDRA_TESTS=1`
- `GHIDRA_INSTALL_DIR` naming a Ghidra 11.3 or later, and a JDK that Ghidra runs on
- the `pyghidra` package, in the Python that runs pytest, with `demangle` importable there
- `g++`, `c++` or `clang++` on PATH

The symbol names assume an ELF toolchain, which is to say Linux. The run takes several
seconds, nearly all of it starting the JVM and importing the program.

PyGhidra's `run_script` and `open_program` are used because they exist from PyGhidra 2 on
and take a script and a binary in one call. PyGhidra 3 marks both deprecated; if a later
release drops them, this is the file to move to `open_project` and `ghidra_script`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple

import pytest

if os.environ.get("DEMANGLE_GHIDRA_TESTS") != "1":
    pytest.skip("set DEMANGLE_GHIDRA_TESTS=1 to run the Ghidra test", allow_module_level=True)
if not os.environ.get("GHIDRA_INSTALL_DIR"):
    pytest.skip("set GHIDRA_INSTALL_DIR to a Ghidra 11.3 or later installation", allow_module_level=True)
_compiler = shutil.which("g++") or shutil.which("c++") or shutil.which("clang++")
if _compiler is None:
    pytest.skip("no C++ compiler on PATH", allow_module_level=True)
COMPILER: str = _compiler
pyghidra = pytest.importorskip("pyghidra")

pytestmark = [
    pytest.mark.ghidra,
    # PyGhidra 3 deprecates the two calls this file makes: see the docstring.
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
]

SCRIPT = Path(__file__).resolve().parents[1] / "integrations" / "demangle_ghidra.py"
PROJECT = "demangle_live"

SOURCE = """\
namespace shapes {
struct Circle {
    int radius;
    int area() const;
};
int Circle::area() const { return 3 * radius * radius; }
}

int main(int argc, char **) {
    shapes::Circle circle{argc};
    return circle.area();
}
"""

#: `shapes::Circle::area() const`, as g++ mangles it, and the name the script gives it.
MANGLED = "_ZNK6shapes6Circle4areaEv"
READABLE = "shapes::Circle::area"


class Symbol(NamedTuple):
    offset: int
    name: str
    primary: bool
    source: str


def symbols(binary, project_dir):
    """Every symbol of the program as saved in the project, sorted."""
    with pyghidra.open_program(binary, project_location=project_dir, project_name=PROJECT, analyze=False) as api:
        table = api.getCurrentProgram().getSymbolTable()
        return sorted(
            Symbol(int(s.getAddress().getOffset()), str(s.getName()), bool(s.isPrimary()), str(s.getSource()))
            for s in table.getAllSymbols(False)
        )


def run_script(binary, project_dir):
    pyghidra.run_script(binary, str(SCRIPT), project_location=project_dir, project_name=PROJECT, analyze=False)


def named(table, name):
    return [symbol for symbol in table if symbol.name == name]


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """The program's symbols as imported, after the script ran, and after it ran again."""
    work = tmp_path_factory.mktemp("ghidra")
    source = work / "circle.cpp"
    source.write_text(SOURCE, encoding="utf-8")
    binary = work / "circle"
    subprocess.run([COMPILER, "-O0", "-o", str(binary), str(source)], check=True, capture_output=True, text=True)
    project_dir = work / "project"
    project_dir.mkdir()

    pyghidra.start()
    before = symbols(binary, project_dir)
    run_script(binary, project_dir)
    first = symbols(binary, project_dir)
    run_script(binary, project_dir)
    second = symbols(binary, project_dir)
    return SimpleNamespace(before=before, first=first, second=second)


class TestGhidraLive:
    def test_the_function_starts_out_mangled(self, runs):
        # Without this the other tests would pass on a program Ghidra had already read.
        [function] = named(runs.before, MANGLED)
        assert function.primary
        assert not named(runs.before, READABLE)

    def test_the_function_gets_its_readable_name(self, runs):
        [original] = named(runs.first, MANGLED)
        [renamed] = named(runs.first, READABLE)
        assert renamed.primary
        assert renamed.offset == original.offset

    def test_the_mangled_name_survives_as_a_second_label(self, runs):
        [original] = named(runs.first, MANGLED)
        assert not original.primary

    def test_a_second_run_changes_nothing(self, runs):
        assert runs.second == runs.first
