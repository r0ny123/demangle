"""Enforce the boundaries the design depends on.

Every rule here is one that erodes quietly under ordinary maintenance -- a convenient
import, a parser that builds a string because it is right there. Written down as tests,
they fail at the moment they are broken rather than a year later.
"""

import ast
import pkgutil
import sys
from pathlib import Path

import demangle
from demangle.core.builder import Builder
from demangle.core.registry import available

SOURCE = Path(demangle.__file__).parent


def imports_of(path):
    """Every module named by an import in `path`, resolved to a dotted string."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append("." * node.level + (node.module or ""))
    return found


def python_files(subdirectory):
    return sorted((SOURCE / subdirectory).rglob("*.py"))


class TestLayering:
    def test_core_never_imports_a_scheme(self):
        """`core` is the contract; a dependency on any scheme inverts the layering.

        The one exception is `style`, which names the built-in option objects inside a
        function body so the import stays lazy and cycle-free.
        """
        offenders = []
        for path in python_files("core"):
            if path.name == "style.py":
                continue
            for name in imports_of(path):
                if "schemes" in name:
                    offenders.append(f"{path.name} imports {name}")
        assert offenders == []

    def test_schemes_never_import_each_other(self):
        """A scheme must be replaceable without disturbing its neighbours."""
        offenders = []
        for scheme in ("itanium", "msvc", "rust"):
            others = {"itanium", "msvc", "rust"} - {scheme}
            for path in python_files(f"schemes/{scheme}"):
                for name in imports_of(path):
                    for other in others:
                        if f"schemes.{other}" in name or f".{other}." in name:
                            offenders.append(f"{scheme}/{path.name} imports {name}")
        assert offenders == []

    def test_no_third_party_imports(self):
        """The dependency-free promise, checked rather than asserted in a README.

        Membership is decided by `sys.stdlib_module_names` rather than a list kept by
        hand. A hand-kept list has to be edited every time a parser reaches for another
        standard module, and the edit looks exactly like someone widening the rule to
        let a real dependency through.
        """
        offenders = []
        for path in SOURCE.rglob("*.py"):
            for name in imports_of(path):
                root = name.split(".")[0]
                if not root or name.startswith(".") or root == "demangle":
                    continue
                if root in sys.stdlib_module_names:
                    continue
                offenders.append(f"{path.relative_to(SOURCE)} imports {name}")
        assert offenders == []


class TestPluginContract:
    def test_every_plugin_is_well_formed(self):
        for plugin in available():
            assert plugin.name and plugin.name.islower()
            assert callable(plugin.detect)
            assert callable(plugin.parse)
            assert plugin.description, f"{plugin.name} has no description"

    def test_detection_is_cheap_and_total(self):
        """`detect` runs on every symbol a caller offers, mangled or not."""
        for plugin in available():
            for value in ["", "memcpy", "_Z1fv", "?f@@YAXH@Z", "\x00\xff"]:
                assert isinstance(plugin.detect(value), bool)

    def test_priority_puts_rust_before_itanium(self):
        """Legacy Rust mangling is Itanium mangling; order is what tells them apart."""
        order = [plugin.name for plugin in available()]
        assert order.index("rust") < order.index("itanium")

    def test_a_third_party_plugin_can_be_registered(self):
        """The extension point works without touching this package."""
        from demangle.core.plugin import LanguagePlugin

        def detect(name):
            return name.startswith("@@toy@@")

        def parse(mangled, builder, limits=None, options=None):
            return builder.raw(mangled.removeprefix("@@toy@@"))

        demangle.register_language(
            LanguagePlugin(name="toy", detect=detect, parse=parse, description="test", priority=1)
        )
        try:
            assert demangle.demangle("@@toy@@hello") == "hello"
            assert demangle.detect("@@toy@@hello") == "toy"
        finally:
            from demangle.core import registry

            registry._plugins.pop("toy", None)
            registry._ordered = None
            demangle.cache_clear()


class TestBuilders:
    def test_both_builders_implement_the_protocol(self):
        from demangle.core.ast import AST_BUILDER
        from demangle.core.spelling import SPELLING_BUILDER

        for builder in (SPELLING_BUILDER, AST_BUILDER):
            for method in (
                "builtin",
                "name",
                "raw",
                "literal",
                "qualified",
                "template",
                "qualify",
                "pointer",
                "reference",
                "rvalue_reference",
                "member_pointer",
                "array",
                "function",
                "pack",
                "vendor_qualify",
                "special",
                "spell",
            ):
                assert callable(getattr(builder, method)), f"{builder} lacks {method}"

    def test_protocol_is_structurally_satisfied(self):
        from demangle.core.spelling import SPELLING_BUILDER

        assert isinstance(SPELLING_BUILDER, Builder)


class TestPackaging:
    def test_every_module_imports_cleanly(self):
        for info in pkgutil.walk_packages([str(SOURCE)], prefix="demangle."):
            __import__(info.name)

    def test_public_names_all_exist(self):
        for name in demangle.__all__:
            assert hasattr(demangle, name), f"__all__ names {name}, which does not exist"

    def test_py_typed_marker_ships(self):
        assert (SOURCE / "py.typed").exists()
