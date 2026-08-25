"""The README's factual claims, checked against the code.

Three separate documents had drifted from the pinned conformance numbers at once, which
is what happens to any figure a human has to remember to update. These tests make the
drift a test failure instead.
"""

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
        (pins.RUST_TOOLCHAIN_EXACT, pins.RUST_TOOLCHAIN_TOTAL),
        (pins.ITANIUM_LLVM_EXACT, pins.ITANIUM_LLVM_TOTAL),
        (pins.ITANIUM_GNU_EXACT, pins.ITANIUM_GNU_TOTAL),
        (pins.REGRESSIONS_EXACT, pins.REGRESSIONS_TOTAL),
    ],
)
def test_every_pinned_count_appears_in_the_readme(readme, exact, total):
    assert f"{exact} / {total}" in readme, (
        f"README does not state {exact} / {total}; regenerate the conformance table"
    )


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
        ],
    )
    def test_example_output_is_what_the_readme_prints(self, readme, call, expected):
        assert call() == expected
        assert expected in readme, f"README no longer shows {expected!r}"

    def test_the_tree_example(self, readme):
        tree = demangle.parse("_ZNK3Foo3barIiEEvPKc")
        assert [node.text for node in tree.find("name")] == ["Foo", "bar"]
        function = next(tree.find("function"))
        assert len(function.parameters) == 1

    def test_rust_still_returns_a_leaf_as_the_readme_says(self, readme):
        assert "Rust does not yet" in readme
        assert demangle.parse("_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E").kind == "raw"
