"""The documentation's claims about the code, checked mechanically.

`tests/test_readme.py` covers the README: its counts, its version, and the output of
every example it prints. This covers the rest of `docs/`: every public export is
reachable in the API reference, every registered scheme has a section, and every page
renders the members it asks for.

Links are deliberately *not* checked here. Doing it by hand needs a Markdown parser --
these pages carry MSVC names with a literal backtick in them, so code spans cannot be
found by counting delimiters -- and mkdocs already does it properly. `validation:` in
`mkdocs.yml` promotes an unresolved link, an unlisted page and a bad anchor to a
warning, which `--strict` fails on.
"""

import doctest
import importlib
import re
from pathlib import Path

import pytest

import demangle

ROOT = Path(__file__).parent.parent
DOCS = ROOT / "docs"


def docs_pages():
    if not DOCS.is_dir():  # pragma: no cover - only in a wheel-only checkout
        pytest.skip("docs/ is not part of this distribution")
    return sorted(DOCS.rglob("*.md"))


def prose_pages():
    """Every page a reader meets: the ones under `docs/` and the ones at the root.

    The root files are the ones GitHub renders; `docs/` holds one-line includes of them
    plus the pages that exist only on the site. Both carry examples.
    """
    return sorted(set(docs_pages()) | set(ROOT.glob("*.md")))


def directives():
    """Every `::: target` in the reference, with the members it names.

    A directive with a `members:` list renders exactly those names; one without renders
    every public member of the module. The two checks below need opposite halves of that
    -- which names are rendered, and which module each was asked of -- so both read this
    rather than each matching the directive for itself.
    """
    for page in docs_pages():
        for match in re.finditer(r"^::: (\S+)((?:\n[ \t]+.*)*)", page.read_text(encoding="utf-8"), re.M):
            target, body = match.group(1), match.group(2)
            named = re.findall(r"^\s+- ([A-Za-z_][A-Za-z0-9_]*)$", body, re.M) if "members:" in body else None
            yield page, target, named


def rendered_members():
    """Every name the API reference renders, and every module it renders whole.

    Both count as documented, so the check below has to know which kind each directive
    is.
    """
    named, whole = set(), set()
    for _page, target, members in directives():
        if members is None:
            whole.add(target)
        else:
            named.update(members)
    return named, whole


class TestEveryDocumentedNameExists:
    """The converse of the class below: a documented name must exist.

    `mkdocstrings` renders nothing for a `members:` entry naming something the module
    does not have, and does not fail the build over it, so the page quietly shows one
    member fewer than it asks for.
    """

    def test_every_rendered_target_is_a_module(self, subtests):
        for page, target, _members in directives():
            with subtests.test(page=page.name, target=target):
                importlib.import_module(target)

    def test_every_member_a_page_asks_for_is_there(self, subtests):
        for page, target, members in directives():
            if members is None:
                continue
            module = importlib.import_module(target)
            for name in members:
                with subtests.test(page=page.name, target=target, member=name):
                    assert hasattr(module, name), f"{page.name} renders {target}.{name}, which does not exist"


class TestEveryPublicNameIsDocumented:
    def test_every_export_is_reachable_in_the_api_reference(self, subtests):
        """`demangle.__all__` is the promise; the reference is where it is kept.

        A name exported and documented nowhere is worse than one that is neither: a
        caller finds it by autocompletion, has nothing to read, and guesses.
        """
        named, whole = rendered_members()
        prose = "\n".join(page.read_text(encoding="utf-8") for page in docs_pages())
        for name in sorted(set(demangle.__all__) - {"__version__"}):
            with subtests.test(name=name):
                exported = getattr(demangle, name)
                home = getattr(exported, "__module__", None)
                # An export may be an alias -- `register_language` is the registry's own
                # `register` -- and the reference documents the function under the name
                # it is defined with. That counts, so long as the page also tells a
                # reader which exported name reaches it.
                canonical = getattr(exported, "__name__", name)
                documented = name in named or home in whole or (canonical in named and name in prose)
                assert documented, (
                    f"demangle.{name} is exported but no page renders it: "
                    f"add it to a `members:` list, or render {home} whole"
                )

    def test_every_registered_scheme_has_a_section(self, subtests):
        """A scheme nobody can read about is a scheme nobody uses on purpose."""
        _, whole = rendered_members()
        pages = "\n".join(page.read_text(encoding="utf-8") for page in docs_pages())
        rendered = set(re.findall(r"^::: demangle\.schemes\.([a-z0-9_]+)", pages, re.M)) | {
            target.removeprefix("demangle.schemes.") for target in whole
        }
        for language in sorted(demangle.languages()):
            with subtests.test(language=language):
                assert language in rendered, f"the {language} scheme is registered but docs/reference/ never renders it"


class TestEveryExampleInTheDocsIsWhatTheCodeDoes:
    """The README's examples are run by `tests/test_readme.py`; these are the others.

    `docs/reference/api.md` prints what a call returns, in the same `call` then
    `# result` shape, and this checks those.
    """

    def test_every_documented_result_is_the_result(self, subtests):
        pattern = re.compile(r"^(demangle\.[^\n#]+?)\n# (.+)$", re.M)
        checked = 0
        for page in docs_pages():
            for block in re.findall(r"```python\n(.*?)```", page.read_text(encoding="utf-8"), re.S):
                for call, expected in pattern.findall(block):
                    call, expected = call.strip(), expected.strip()
                    with subtests.test(page=page.name, call=call):
                        assert repr(eval(call, {"demangle": demangle})) == expected
                    checked += 1
        assert checked, "no documented results were found to check; has the shape of the examples changed?"


class TestTheArchitecturesLayoutIsTheLayout:
    """The tree in ARCHITECTURE.md, against the tree on disk.

    Every scheme and core module on disk appears in the diagram, so a reader taking it
    for the map sees all fourteen manglings.
    """

    ARCHITECTURE = ROOT / "ARCHITECTURE.md"
    SOURCE = ROOT / "src" / "demangle"

    def layout(self):
        if not self.ARCHITECTURE.exists() or not self.SOURCE.is_dir():  # pragma: no cover
            pytest.skip("the source tree and ARCHITECTURE.md are not both present")
        match = re.search(r"```\ndemangle/\n(.*?)```", self.ARCHITECTURE.read_text(encoding="utf-8"), re.S)
        assert match, "ARCHITECTURE.md no longer carries a layout diagram"
        return match.group(1)

    def test_every_scheme_is_listed(self, subtests):
        listed = self.layout()
        for scheme in sorted(demangle.languages()):
            with subtests.test(scheme=scheme):
                assert f"{scheme}/" in listed, f"the layout in ARCHITECTURE.md does not list the {scheme} scheme"

    def test_every_module_is_listed(self, subtests):
        listed = self.layout()
        modules = [*sorted(self.SOURCE.glob("*.py")), *sorted((self.SOURCE / "core").glob("*.py"))]
        for module in modules:
            if module.name == "__init__.py":
                continue
            with subtests.test(module=module.name):
                assert module.name in listed, f"the layout in ARCHITECTURE.md does not list {module.name}"


class TestEverySnippetIsValidPython:
    """Every ```python block in the documentation, compiled.

    Not run -- most of them need a binary, a compiler or a file that is not here -- but
    a block that no longer parses is a block nobody has read since it stopped being
    true. Doctest-style blocks are taken apart by `doctest` first, so the expected
    output between the prompts is not fed to the compiler as if it were source.
    """

    def test_every_block_parses(self, subtests):
        parser = doctest.DocTestParser()
        blocks = 0
        for page in prose_pages():
            for index, block in enumerate(re.findall(r"```python\n(.*?)```", page.read_text(encoding="utf-8"), re.S)):
                blocks += 1
                sources = [example.source for example in parser.get_examples(block)] if ">>>" in block else [block]
                for source in sources:
                    with subtests.test(page=page.name, block=index):
                        compile(source, f"<{page.name}:{index}>", "exec")
        assert blocks, "no python blocks were found; has the shape of the examples changed?"


class TestTheReferenceDefectSourcesAreDocumented:
    """Every source under `tools/corpus_sources/reference_defects/`, against its README.

    The expected column of `tests/conformance/itanium-reference-defects.txt` comes from
    the declaration rather than from a demangler, so the table in that README -- which
    compiler wrote each name, and what each reference does with it -- is the only record
    of why a row says what it says. A source added without a row is evidence nobody else
    can check; a row left behind after its source went names a file that is not there.
    """

    SOURCES = ROOT / "tools" / "corpus_sources" / "reference_defects"

    def readme(self):
        if not self.SOURCES.is_dir():  # pragma: no cover - only in a wheel-only checkout
            pytest.skip("the corpus sources are not part of this distribution")
        return (self.SOURCES / "README.md").read_text(encoding="utf-8")

    def test_every_source_has_a_row(self, subtests):
        readme = self.readme()
        for source in sorted(self.SOURCES.glob("*.cpp")):
            with subtests.test(source=source.name):
                assert f"`{source.name}`" in readme, f"{source.name} has no row in the reference-defects README"

    def test_every_row_names_a_source(self, subtests):
        readme = self.readme()
        for name in re.findall(r"^\| `([^`]+\.cpp)`", readme, re.M):
            with subtests.test(source=name):
                assert (self.SOURCES / name).exists(), (
                    f"the reference-defects README has a row for {name}, which is gone"
                )


def test_the_citation_names_this_version():
    """`CITATION.cff` is what GitHub's "Cite this repository" reads."""
    citation = ROOT / "CITATION.cff"
    if not citation.exists():  # pragma: no cover - only in a wheel-only checkout
        pytest.skip("CITATION.cff is not part of this distribution")
    version = re.search(r'^version:\s*"?([^"\s]+)"?\s*$', citation.read_text(encoding="utf-8"), re.M)
    assert version is not None, "CITATION.cff has no version"
    assert version.group(1) == demangle.__version__
