"""Compile the Itanium hot path with mypyc, for the platform wheels.

Off unless `HATCH_BUILD_HOOK_ENABLE_CUSTOM=true`, so the sdist, the pure wheel and
editable installs are built as before. The `.py` files stay in the wheel beside their
extensions, which Python imports first. What this asks of the compiled files is in
ARCHITECTURE.md, Performance, rule 6.
"""

import os
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

COMPILED = [
    "src/demangle/core/reader.py",
    "src/demangle/core/spelling.py",
    "src/demangle/schemes/itanium/parser.py",
    "src/demangle/schemes/itanium/substitutions.py",
]


class CompiledBuild(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel" or version == "editable":
            return
        from mypyc.build import mypycify
        from setuptools import Distribution

        root = Path(self.root)
        work = Path(tempfile.mkdtemp(prefix="demangle-mypyc-"))
        os.chdir(root)
        extensions = mypycify(
            ["--check-untyped-defs", "--follow-imports=silent", "--cache-dir", str(work / "cache"), *COMPILED],
            group_name="demangle",
            target_dir=str(work / "c"),
        )
        distribution = Distribution({"ext_modules": extensions})
        build_ext = distribution.get_command_obj("build_ext")
        build_ext.build_lib = str(work / "lib")
        build_ext.build_temp = str(work / "temp")
        distribution.run_command("build_ext")
        for built in build_ext.get_outputs():
            build_data["force_include"][built] = Path(built).relative_to(work / "lib").as_posix()
        build_data["pure_python"] = False
        build_data["infer_tag"] = True
