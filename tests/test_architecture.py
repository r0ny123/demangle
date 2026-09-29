"""Enforce the boundaries the design depends on.

Every rule here is one that erodes quietly under ordinary maintenance -- a convenient
import, a parser that builds a string because it is right there. Written down as tests,
they fail at the moment they are broken rather than a year later.
"""

import ast
import pkgutil
import sys
from pathlib import Path

import pytest

import demangle
from demangle.core.builder import Builder
from demangle.core.errors import DemanglingError
from demangle.core.registry import available

SOURCE = Path(demangle.__file__).parent


def imports_of_at(path, root):
    """`imports_of` against an arbitrary package root, for testing the rule itself."""
    global SOURCE
    original, SOURCE = SOURCE, root
    try:
        return imports_of(path)
    finally:
        SOURCE = original


def imports_of(path):
    """Every module named by an import in `path`, resolved to a dotted string.

    Relative imports are resolved against the file's own package, so
    `from ..msvc import x` inside `schemes/rust/` comes back as
    `demangle.schemes.msvc` and can be compared on path segments. Comparing the raw
    `"..msvc"` on substrings let the most obvious cross-scheme import -- a top-level
    one -- walk straight through the rule meant to forbid it.

    A `from` import also records the name behind the `import`, so
    `from demangle.core import spelling` comes back as both `demangle.core` and
    `demangle.core.spelling`. Without the second entry a rule watching for
    `core.spelling` never sees that spelling of the import.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = ["demangle", *path.relative_to(SOURCE).parts[:-1]]
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            found.append(module)
            if module:
                found.extend(f"{module}.{alias.name}" for alias in node.names if alias.name != "*")
    return found


def corpus_names():
    """Every mangled name in every conformance corpus, whatever scheme wrote it."""
    import gzip

    # Anchored on this file, not `demangle.__file__`, which is in site-packages when the
    # sdist's tests run against an installed package.
    conformance = Path(__file__).parent / "conformance"
    names = []
    for path in sorted(conformance.iterdir()):
        if path.suffix == ".gz":
            text = gzip.decompress(path.read_bytes()).decode("utf-8", "surrogateescape")
        elif path.suffix == ".txt":
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
        else:
            continue
        names.extend(line.split("\t")[0] for line in text.splitlines() if line and not line.startswith("#"))
    return names


def python_files(subdirectory):
    return sorted((SOURCE / subdirectory).rglob("*.py"))


def _is_top_level_import(path, name):
    """Whether `name` is imported at the top level of `path`.

    `core/style.py` may name scheme option objects inside a function body, where
    the import stays lazy and cycle-free. A top-level import there would be a real
    layering inversion, so the rule checks those and excuses only the lazy ones.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = ["demangle", *path.relative_to(SOURCE).parts[:-1]]
    for node in tree.body:
        if isinstance(node, ast.Import):
            if any(alias.name == name or name.startswith(alias.name + ".") for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]
                module = ".".join([*base, node.module] if node.module else base)
            else:
                module = node.module or ""
            candidates = [module, *(f"{module}.{a.name}" for a in node.names if a.name != "*")]
            if name in candidates:
                return True
    return False


class TestLayering:
    def test_core_never_imports_a_scheme(self):
        """`core` is the contract; a dependency on any scheme inverts the layering.

        The one exception is `style`, which names the built-in option objects inside a
        function body so the import stays lazy and cycle-free. Only a top-level import
        there counts; anything deeper is the lazy form the layering allows.
        """
        offenders = []
        for path in python_files("core"):
            for name in imports_of(path):
                if "schemes" not in name:
                    continue
                if path.name == "style.py" and not _is_top_level_import(path, name):
                    continue
                offenders.append(f"{path.name} imports {name}")
        assert offenders == []

    def test_schemes_never_import_each_other(self):
        """A scheme must be replaceable without disturbing its neighbours."""
        schemes = {path.name for path in (SOURCE / "schemes").iterdir() if (path / "__init__.py").is_file()}
        assert len(schemes) > 1, "no schemes found; this test would prove nothing"
        offenders = []
        for scheme in schemes:
            for path in python_files(f"schemes/{scheme}"):
                for name in imports_of(path):
                    segments = name.split(".")
                    if "schemes" not in segments:
                        continue
                    named = segments[segments.index("schemes") + 1 :]
                    if named and named[0] in schemes - {scheme}:
                        offenders.append(f"{scheme}/{path.name} imports {name}")
        assert offenders == []

    def test_the_cross_scheme_rule_would_actually_catch_a_violation(self):
        """The rule above is only worth having if it fires. Prove it does."""
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            scheme = Path(directory) / "demangle" / "schemes" / "rust"
            scheme.mkdir(parents=True)
            offender = scheme / "leaky.py"
            offender.write_text("from ..msvc import demangle_msvc_symbol\n")
            resolved = imports_of_at(offender, Path(directory) / "demangle")
        assert "demangle.schemes.msvc" in resolved

    def test_no_scheme_reaches_for_the_shared_spelling_types(self):
        """Types and names go through the builder, never around it.

        A parser importing `Spelling` would be constructing output rather than reporting
        a production, and `parse()` would silently lose that subtree. Importing
        `core.ast` is fine and expected -- that is how a scheme declares node kinds of
        its own -- so only `core.spelling` is forbidden here. This is the enforceable
        half of the rule; ARCHITECTURE.md says what it does not cover.
        """
        offenders = [
            f"{path.relative_to(SOURCE)} imports {name}"
            for path in SOURCE.glob("schemes/**/*.py")
            for name in imports_of(path)
            if name.endswith("core.spelling")
        ]
        assert offenders == []

    def test_the_spelling_rule_would_actually_catch_a_plain_core_import(self):
        """`from demangle.core import spelling` names only `demangle.core` as a module."""
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            scheme = Path(directory) / "demangle" / "schemes" / "rust"
            scheme.mkdir(parents=True)
            offender = scheme / "leaky.py"
            offender.write_text("from demangle.core import spelling\n")
            resolved = imports_of_at(offender, Path(directory) / "demangle")
        assert "demangle.core.spelling" in resolved

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

    def test_a_rejected_plugin_leaves_the_registry_as_it_found_it(self):
        """An alias may not shadow a registered language, and a plugin refused for one
        alias must not leave its other aliases pointing at a plugin that never arrived."""
        from demangle.core import registry
        from demangle.core.plugin import LanguagePlugin

        def detect(name):
            return False

        def parse(mangled, builder, limits=None, options=None):
            return builder.raw(mangled)

        before = dict(registry.aliases())
        hijack = LanguagePlugin(
            name="hijack", aliases=("zzz-unclaimed", "itanium"), detect=detect, parse=parse, description="test"
        )
        with pytest.raises(ValueError, match="collides with a registered language name"):
            demangle.register_language(hijack)
        assert "hijack" not in demangle.languages()
        assert registry.aliases() == before
        with pytest.raises(KeyError):
            registry.get("zzz-unclaimed")
        assert registry.get("itanium").name == "itanium"

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
            registry._by_first = None
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


class TestTheTreeSpellsWhatTheTextPathSpells:
    """The one invariant every scheme in the package shares, checked over all of them.

    Two builders read one parser, which is the whole reason the builder protocol exists:
    the parts a parser reports are the spelling, in order, so a tree cannot render a name
    differently from `demangle()`. Each scheme's own module checks this on its own
    corpus; this checks it on *every* corpus at once, in both styles, because the ways
    the two drift apart are cross-cutting -- a builder that forgets to distribute over a
    pack, a tree assembled from parsed fields rather than from the fragments.

    It has caught three: a pack holding an empty pack (`core/ast.py`), a declarator over
    an empty pack (`core/spelling.py`), and a Free Pascal program's `program variable `
    lead, which the tree looked for under the unit's raw name and a program's unit is
    spelled without its `P$`.
    """

    def test_every_corpus_name_in_both_styles(self, subtests):
        names = corpus_names()
        assert len(names) > 50_000, "corpora did not load; this test would prove nothing"
        for style in ("llvm", "gnu"):
            with subtests.test(style=style):
                differ = []
                for name in names:
                    try:
                        text = demangle.demangle_strict(name, style=style)
                    except DemanglingError:
                        continue
                    if demangle.parse(name, style=style).spell(style=style) != text:
                        differ.append(name)
                assert differ == []


class TestReadingAnAnswerAgainChangesNothing:
    """What `demangle()` returns is not a mangled name, so offering it back is a no-op.

    Not an academic property. A tool walking a symbol table demangles every entry and
    prints it, and anything downstream that demangles again -- a log scraper, a second
    pass over a report, a user pasting a line back -- must not get a third spelling. The
    library's promise is that a name it cannot read comes back unchanged, and a
    demangled answer is such a name.

    It failed for 49 of the corpora's names, all of them Swift and all for one reason.
    A Swift type is spelled with `@` markers -- `@convention(block) (Swift.Int) ->
    Swift.UInt`, `@escaping @differentiable @callee_guaranteed (@unowned Swift.Float)` --
    and `@` is the Delphi scheme's first character and its qualifier separator, so that
    scheme claimed the answer and read the markers as scope: `escaping
    ::differentiable ::callee_guaranteed (::unowned Swift.Float)`. The claim was never
    about re-reading output alone -- `@feat.00` and `@comp.id` are in every COFF object
    MSVC and clang-cl emit, and came back with the `@` taken off. Fixed by screening
    `delphi.detect` on the alphabet Borland exports are actually made of; see there.
    """

    def test_every_corpus_name_in_both_styles(self, subtests):
        names = corpus_names()
        assert len(names) > 50_000, "corpora did not load; this test would prove nothing"
        for style in ("llvm", "gnu"):
            with subtests.test(style=style):
                unstable = []
                for name in names:
                    once = demangle.demangle(name, style=style)
                    if once == name:
                        continue
                    if demangle.demangle(once, style=style) != once:
                        unstable.append((name, once))
                assert unstable == []


class TestTheToolsAndTheSuiteAgree:
    """Two lists of excused names have to say the same thing.

    `tools/differential.py` excuses a name from its corpus replay; `test_conformance.py`
    pins the same set for the suite. They are edited in different files for different
    reasons, and a name excused in one and not the other means one of the two has stopped
    watching it -- which is how a deliberate shortfall came to be reported as a clean
    100% by the tool while the suite was failing on it.
    """

    @staticmethod
    def module(name):
        import importlib.util

        root = Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location(f"_{name}", root / "tools" / f"{name}.py")
        if spec is None or spec.loader is None:  # pragma: no cover - wheel-only checkout
            pytest.skip(f"tools/{name}.py is not part of this distribution")
        loaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded)
        return loaded

    def test_the_same_names_are_excused_on_both_sides(self):
        from . import test_conformance as pins

        tool = self.module("differential")
        assert set(pins.GNU_DIVERGENCES) == tool.KNOWN_DIVERGENCES

    def test_no_excused_name_is_also_a_reference_defect(self):
        """A name cannot both be an open disagreement and have a settled answer.

        `itanium-reference-defects.txt` says what a name must spell, established from the
        declaration. Excusing the same name in the tool would mean the corpus asserts an
        answer the tool has agreed not to look at.
        """
        from .conftest import load_corpus

        tool = self.module("differential")
        settled = {mangled for mangled, _ in load_corpus("itanium-reference-defects.txt")}
        assert not (settled & tool.KNOWN_DIVERGENCES)

    def test_the_generator_excludes_every_settled_name(self):
        """Regenerating a corpus must not re-record a reference's wrong answer."""
        from .conftest import CONFORMANCE, load_corpus

        generator = self.module("generate_corpus")
        for corpus in ("itanium-reference-defects.txt", "msvc-reference-defects.txt"):
            settled = {mangled for mangled, _ in load_corpus(corpus)}
            assert settled, corpus
            assert settled <= generator.reference_defects(CONFORMANCE / corpus), corpus


class TestPackaging:
    def test_every_module_imports_cleanly(self):
        for info in pkgutil.walk_packages([str(SOURCE)], prefix="demangle."):
            __import__(info.name)

    def test_public_names_all_exist(self):
        for name in demangle.__all__:
            assert hasattr(demangle, name), f"__all__ names {name}, which does not exist"

    def test_py_typed_marker_ships(self):
        assert (SOURCE / "py.typed").exists()
