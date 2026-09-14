"""The documentation's claims about the code, checked mechanically.

`tests/test_readme.py` covers the README: its counts, its version, and the output of
every example it prints. This covers the rest of `docs/`, which had no guard at all.

Every rule here was written because the thing it checks had already drifted: every
public export is supposed to be reachable in the API reference, and `Decorated` and
`register_language` were not -- exported, and documented nowhere; every registered
scheme is supposed to have a section, and Ada and JNI had none; and a page is supposed
to render the members it asks for, where the pre-Itanium parser's section asked for a
`detect` that module does not have.

Links are deliberately *not* checked here. Doing it by hand needs a Markdown parser --
these pages carry MSVC names with a literal backtick in them, so code spans cannot be
found by counting delimiters -- and mkdocs already does it properly. What it did not do
was fail: `validation:` in `mkdocs.yml` now promotes an unresolved link, an unlisted
page and a bad anchor from INFO to a warning, which `--strict` turns into a red build.
"""

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
    """The converse of the class below, and it had drifted the same way.

    `mkdocstrings` renders nothing for a `members:` entry naming something the module
    does not have, and does not fail the build over it, so the page quietly shows one
    member fewer than it asks for. The pre-Itanium parser's section listed a `detect`
    that the scheme's package defines and `_parser` does not.
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
    `# result` shape, and nothing checked those until now.
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
