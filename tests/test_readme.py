"""The README's and the conformance page's factual claims, checked against the code.

A figure a human has to remember to update drifts from the pinned conformance numbers.
These tests make that drift a test failure. The examples on every page are run by
`tests/test_docs.py`.
"""

import pathlib
import re
from pathlib import Path

import pytest

import demangle

from . import test_conformance as pins

README = Path(__file__).parent.parent / "README.md"
CONFORMANCE = Path(__file__).parent.parent / "CONFORMANCE.md"


def _read(path):
    if not path.exists():  # pragma: no cover - only in a wheel-only checkout
        pytest.skip(f"{path.name} is not part of this distribution")
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def readme():
    return _read(README)


@pytest.fixture(scope="module")
def prose():
    """The README and the conformance page together.

    The counts live on the conformance page and the examples in the README, but which
    document holds a given figure is an editorial decision rather than something these
    checks should pin. Both are read, so moving a row between them is not a test failure
    and dropping one still is.
    """
    return _read(README) + "\n" + _read(CONFORMANCE)


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
        (pins.REFERENCE_DEFECTS_EXACT, pins.REFERENCE_DEFECTS_TOTAL),
        (pins.NO_PARAMS_AGREE, pins.NO_PARAMS_TOTAL),
        (pins.GNUV2_EXACT, pins.GNUV2_TOTAL),
        (pins.CODEWARRIOR_EXACT, pins.CODEWARRIOR_TOTAL),
        (pins.JNI_EXACT, pins.JNI_TOTAL),
    ],
)
def test_every_pinned_count_appears_in_the_prose(prose, exact, total):
    assert f"{exact} / {total}" in prose, f"no page states {exact} / {total}; regenerate the conformance table"


def test_the_prose_states_no_stale_conformance_numbers(prose):
    """Any `N / M` in the table must be a pin, not a stale number."""
    pinned = {
        (pins.LIBSTDCXX_EXACT, pins.LIBSTDCXX_TOTAL),
        (pins.RUST_EXACT, pins.RUST_TOTAL),
        (pins.MSVC_EXACT, pins.MSVC_TOTAL),
        (pins.RUST_TOOLCHAIN_EXACT, pins.RUST_TOOLCHAIN_TOTAL),
        (pins.ITANIUM_LLVM_EXACT, pins.ITANIUM_LLVM_TOTAL),
        (pins.ITANIUM_GNU_EXACT, pins.ITANIUM_GNU_TOTAL),
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
        (pins.SWIFT_UPSTREAM_EXACT, pins.SWIFT_UPSTREAM_TOTAL),
        (pins.SWIFT_REFERENCE_DEFECTS_EXACT, pins.SWIFT_REFERENCE_DEFECTS_TOTAL),
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
        (pins.JNI_EXACT, pins.JNI_TOTAL),
    }
    stated = {(int(a), int(b)) for a, b in re.findall(r"\*\*(\d+) / (\d+)\*\*", prose)}
    assert stated <= pinned, f"the documentation states counts that are not pinned anywhere: {sorted(stated - pinned)}"


def test_the_version_is_stated_once(readme):
    """`__version__` is the single source of truth; the metadata is read from it."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        installed = version("demangle")
    except PackageNotFoundError:
        pytest.skip("demangle is not installed; no metadata to compare against")
    assert installed == demangle.__version__


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
