"""The README's factual claims, checked against the code.

Three separate documents had drifted from the pinned conformance numbers at once, which
is what happens to any figure a human has to remember to update. These tests make the
drift a test failure instead.
"""

import pathlib
import re
from pathlib import Path

import pytest

import demangle

from . import test_conformance as pins

README = Path(__file__).parent.parent / "README.md"


@pytest.fixture(scope="module")
def readme():
    if not README.exists():  # pragma: no cover - only in a wheel-only checkout
        pytest.skip("README.md is not part of this distribution")
    return README.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "exact,total",
    [
        (pins.LIBSTDCXX_EXACT, pins.LIBSTDCXX_TOTAL),
        (pins.RUST_EXACT, pins.RUST_TOTAL),
        (pins.MSVC_EXACT, pins.MSVC_TOTAL),
        (pins.MSVC_DBGHELP_EXACT, pins.MSVC_DBGHELP_TOTAL),
        (pins.RUST_TOOLCHAIN_EXACT, pins.RUST_TOOLCHAIN_TOTAL),
        (pins.ITANIUM_LLVM_EXACT, pins.ITANIUM_LLVM_TOTAL),
        (pins.ITANIUM_GNU_EXACT, pins.ITANIUM_GNU_TOTAL),
        (pins.REGRESSIONS_EXACT, pins.REGRESSIONS_TOTAL),
        (pins.REFERENCE_DEFECTS_EXACT, pins.REFERENCE_DEFECTS_TOTAL),
        (pins.NO_PARAMS_AGREE, pins.NO_PARAMS_TOTAL),
        (pins.GNUV2_EXACT, pins.GNUV2_TOTAL),
        (pins.CODEWARRIOR_EXACT, pins.CODEWARRIOR_TOTAL),
    ],
)
def test_every_pinned_count_appears_in_the_readme(readme, exact, total):
    assert f"{exact} / {total}" in readme, f"README does not state {exact} / {total}; regenerate the conformance table"


def test_the_readme_states_no_stale_conformance_numbers(readme):
    """Any `N / M` in the table must be a pin, not a number left over from before."""
    pinned = {
        (pins.LIBSTDCXX_EXACT, pins.LIBSTDCXX_TOTAL),
        (pins.RUST_EXACT, pins.RUST_TOTAL),
        (pins.MSVC_EXACT, pins.MSVC_TOTAL),
        (pins.RUST_TOOLCHAIN_EXACT, pins.RUST_TOOLCHAIN_TOTAL),
        (pins.ITANIUM_LLVM_EXACT, pins.ITANIUM_LLVM_TOTAL),
        (pins.ITANIUM_GNU_EXACT, pins.ITANIUM_GNU_TOTAL),
        (pins.REGRESSIONS_EXACT, pins.REGRESSIONS_TOTAL),
        (pins.REFERENCE_DEFECTS_EXACT, pins.REFERENCE_DEFECTS_TOTAL),
        (pins.TYPES_LLVM_EXACT, pins.TYPES_TOTAL),
        (pins.TYPES_GNU_EXACT, pins.TYPES_TOTAL),
        (pins.MSVC_SUPPRESSIONS_EXACT, pins.MSVC_SUPPRESSIONS_TOTAL),
        (pins.MSVC_DBGHELP_EXACT, pins.MSVC_DBGHELP_TOTAL),
        (pins.SWIFT_SIMPLIFIED_EXACT, pins.SWIFT_SIMPLIFIED_TOTAL),
        (pins.MSVC_DESCRIPTORS_EXACT, pins.MSVC_DESCRIPTORS_TOTAL),
        (pins.MSVC_ARM64EC_EXACT, pins.MSVC_ARM64EC_TOTAL),
        (pins.GO_EXACT, pins.GO_TOTAL),
        (pins.D_EXACT, pins.D_TOTAL),
        (pins.SWIFT_EXACT, pins.SWIFT_TOTAL),
        (pins.NIM_EXACT, pins.NIM_TOTAL),
        (pins.PASCAL_EXACT, pins.PASCAL_TOTAL),
        (pins.OBJC_EXACT, pins.OBJC_TOTAL),
        (pins.DELPHI_EXACT, pins.DELPHI_TOTAL),
        (pins.DELPHI_TABLE_EXACT, pins.DELPHI_TABLE_TOTAL),
        (pins.DELPHI_CONSTRUCT_EXACT, pins.DELPHI_CONSTRUCT_TOTAL),
        (pins.NO_PARAMS_AGREE, pins.NO_PARAMS_TOTAL),
        (pins.GNUV2_EXACT, pins.GNUV2_TOTAL),
        (pins.CODEWARRIOR_EXACT, pins.CODEWARRIOR_TOTAL),
        (pins.ADA_EXACT, pins.ADA_TOTAL),
    }
    stated = {(int(a), int(b)) for a, b in re.findall(r"\*\*(\d+) / (\d+)\*\*", readme)}
    assert stated <= pinned, f"README states counts that are not pinned anywhere: {sorted(stated - pinned)}"


def test_the_version_is_stated_once(readme):
    """`__version__` is the single source of truth; the metadata is read from it."""
    from importlib.metadata import version

    assert version("demangle") == demangle.__version__


class TestExamples:
    """Every output printed in the README, run.

    A README example that has quietly stopped working is the first thing a new reader
    meets, and nothing else in the suite covers them.
    """

    @pytest.mark.parametrize(
        "call,expected",
        [
            (
                lambda: demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi"),
                "std::vector<int, std::allocator<int>>::push_back(int const&)",
            ),
            (lambda: demangle.demangle("?f@@YAXH@Z"), "void __cdecl f(int)"),
            (
                lambda: demangle.demangle("_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E"),
                "core::fmt::Formatter::pad",
            ),
            (lambda: demangle.detect("?f@@YAXH@Z"), "msvc"),
            (lambda: demangle.parse("_ZNK3Foo3barIiEEvPKc").spell(), "void Foo::bar<int>(char const*) const"),
            (
                lambda: demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi", style="gnu"),
                "std::vector<int, std::allocator<int> >::push_back(int const&)",
            ),
            (
                lambda: demangle.demangle("@Unit@Class@Method$qqrv"),
                "__fastcall Unit::Class::Method()",
            ),
        ],
    )
    def test_example_output_is_what_the_readme_prints(self, readme, call, expected):
        assert call() == expected
        assert expected in readme, f"README no longer shows {expected!r}"

    def test_the_bytes_example(self, readme):
        assert demangle.demangleb(b"_ZN3foo3barEv") == b"foo::bar()"
        assert "b'foo::bar()'" in readme
        for name in ("demangleb_strict", "detectb", "parseb", "signatureb"):
            assert hasattr(demangle, name), name
            assert name in readme, name

    def test_the_signature_example(self, readme):
        parts = demangle.signature("_ZNSt6vectorIiSaIiEE9push_backERKi")
        assert (parts.namespace, parts.base_name) == ("std::vector<int, std::allocator<int>>", "push_back")
        assert parts.parameters == ("int const&",)
        assert demangle.signature("$s4main3FooV3baryS2i_SStF").return_type == "Swift.Int"
        for shown in ("('std::vector<int, std::allocator<int>>', 'push_back')", "('int const&',)", "'Swift.Int'"):
            assert shown in readme, f"README no longer shows {shown}"

    def test_the_tree_example(self, readme):
        tree = demangle.parse("_ZNK3Foo3barIiEEvPKc")
        assert [node.text for node in tree.find("name")] == ["Foo", "bar"]
        function = next(tree.find("function"))
        assert len(function.parameters) == 1

    def test_every_scheme_returns_a_tree_as_the_readme_says(self, readme):
        assert "All schemes return full trees" in readme
        # Real names from the conformance corpora; an invented one is as likely to be
        # malformed as to prove anything.
        for name in ("_ZNSt6vectorIiSaIiEE9push_backERKi", "?f@@YAXH@Z", "_RNvCsdEttCVZFADF_8features10btree_work"):
            assert len(list(demangle.parse(name).walk())) > 1, name


class TestWorkedExample:
    """The predicate `docs/analysing-a-binary.md` teaches, run against a real library.

    Documentation that is not executed is documentation that drifts. This runs the
    example's own code and checks the numbers the page states, so a change to the node
    shapes breaks the page rather than quietly making it wrong.
    """

    LIBRARY = "/usr/lib/x86_64-linux-gnu/libstdc++.so.6"

    @staticmethod
    def template_taken_by_const_reference(parameter):
        """Copied from the page. If this needs changing, the page needs changing."""
        if parameter.kind != "reference":
            return None
        inner = parameter.children()[0]
        if inner.kind != "qualify" or "const" not in inner.qualifiers:
            return None
        referent = inner.children()[0]
        if referent.kind == "qualified":
            referent = referent.children()[-1]
        return referent if referent.kind == "template" else None

    def _symbols(self):
        import subprocess

        output = subprocess.run(["nm", "-D", "--defined-only", self.LIBRARY], capture_output=True, text=True).stdout
        return [line.split()[-1] for line in output.splitlines() if line.strip()]

    def test_the_example_finds_what_the_page_says_it_finds(self):
        library = pathlib.Path(self.LIBRARY)
        if not library.exists():
            pytest.skip("no system libstdc++ to read")

        strings = 0
        for name in self._symbols():
            try:
                tree = demangle.parse(name)
            except demangle.DemanglingError:
                continue
            for function in tree.find("function"):
                for parameter in function.parameters:
                    template = self.template_taken_by_const_reference(parameter)
                    if template is not None and "basic_string" in template.base.spell():
                        strings += 1
                        break

        page = (pathlib.Path(__file__).parent.parent / "docs" / "analysing-a-binary.md").read_text()
        assert str(strings) in page, f"the page states a count this library no longer finds ({strings})"

    def test_every_snippet_on_the_page_is_valid_python(self):
        page = (pathlib.Path(__file__).parent.parent / "docs" / "analysing-a-binary.md").read_text()
        blocks = re.findall(r"```python\n(.*?)```", page, re.DOTALL)
        assert blocks, "no python blocks found; has the page been renamed?"
        for block in blocks:
            compile(block, "<page>", "exec")
