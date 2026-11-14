"""A compiled wheel really runs its compiled modules.

Python imports an extension in preference to the `.py` beside it, so a wheel whose
extensions failed to load, or were never built, would pass every other test on the pure
code. The jobs that test a compiled build set `DEMANGLE_EXPECT_COMPILED`.
"""

import ast
import importlib
import importlib.machinery
import os
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "tools" / "hatch_build.py"

pytestmark = pytest.mark.skipif(not os.environ.get("DEMANGLE_EXPECT_COMPILED"), reason="not a compiled build")


def compiled_modules():
    tree = ast.parse(HOOK.read_text(encoding="utf-8"))
    paths = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "COMPILED"
    )
    return [path.removeprefix("src/").removesuffix(".py").replace("/", ".") for path in paths]


@pytest.mark.parametrize("name", compiled_modules())
def test_the_module_is_an_extension(name):
    path = importlib.import_module(name).__file__
    assert path is not None
    assert path.endswith(tuple(importlib.machinery.EXTENSION_SUFFIXES)), path
