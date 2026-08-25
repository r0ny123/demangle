"""Replay the checked-in corpora.

Pass counts are pinned as exact numbers rather than floors. A floor lets a fix that
silently breaks something else slip through as long as the net stays positive; an exact
number means every change in either direction has to be an explicit edit to this file,
with a reason in the commit that makes it.

These tests need neither a compiler nor the reference demanglers: the expected output is
recorded in the corpus files. Regenerate them with tools/generate_corpus.py.
"""

import pytest

import demangle
from demangle.core.errors import DemanglingError

from .conftest import load_corpus

# Measured against llvm-cxxfilt 18.1.3 and GNU c++filt 2.42 on the checked-in corpora.
ITANIUM_LLVM_TOTAL, ITANIUM_LLVM_EXACT = 278, 278
ITANIUM_GNU_TOTAL, ITANIUM_GNU_EXACT = 275, 272
MSVC_TOTAL, MSVC_EXACT = 609, 609
LIBSTDCXX_TOTAL, LIBSTDCXX_EXACT = 5913, 5913
REGRESSIONS_TOTAL, REGRESSIONS_EXACT = 30, 30
RUST_TOTAL, RUST_EXACT = 5316, 5316
RUST_TOOLCHAIN_TOTAL, RUST_TOOLCHAIN_EXACT = 394, 394

# The GNU shortfalls are not ours to fix: in each, the two references disagree about
# what goes in the substitution table, not about how to spell it. Matching both would
# mean two incompatible parses of the same bytes, so we follow LLVM and pin the
# disagreements by name -- a count alone would let one be traded for a new defect.
GNU_DIVERGENCES = [
    # llvm-cxxfilt resolves `S4_` to `outer::inner::Holder<int, 3>` and c++filt to
    # `int`: GNU records one fewer entry for a template-template-parameter application.
    "_Z16templateTemplateIN5outer5inner6HolderEiET_IT0_Li3EES4_",
    # Inside a requires-clause, llvm records the template parameter symbolically (`T`)
    # and GNU records the argument bound to it.
    "_ZN6modern8measuredINSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEEQ5SizedIT_EEEmRKS7_",
    # A generic lambda's `operator()`: llvm resolves the back-reference to the lambda's
    # declared parameter, GNU to the argument `operator()` was instantiated with.
    "_ZZN6modern13genericLambdaEvENKUlTyT_E_clIiEEDaS0_",
]


def _score(corpus, style, language=None):
    pairs = load_corpus(corpus)
    exact = 0
    for mangled, expected in pairs:
        try:
            got = demangle.demangle_strict(mangled, style=style, language=language)
        except (DemanglingError, RecursionError):
            continue
        exact += got == expected
    return len(pairs), exact


def test_itanium_matches_llvm_cxxfilt():
    total, exact = _score("itanium-real-world.txt", "llvm")
    assert (total, exact) == (ITANIUM_LLVM_TOTAL, ITANIUM_LLVM_EXACT)


def test_itanium_matches_gnu_cxxfilt():
    total, exact = _score("itanium-real-world-gnu.txt", "gnu")
    assert (total, exact) == (ITANIUM_GNU_TOTAL, ITANIUM_GNU_EXACT)


def test_msvc_matches_llvm_undname():
    total, exact = _score("msvc-llvm-corpus.txt", "llvm", language="msvc")
    assert (total, exact) == (MSVC_TOTAL, MSVC_EXACT)


def test_matches_llvm_cxxfilt_on_the_system_libstdcxx():
    """Every mangled symbol the shipped libstdc++ exports.

    Real released C++ rather than something compiled for the test, which is the point:
    it carries what only a real standard library produces.
    """
    total, exact = _score("itanium-libstdcxx.txt", "llvm")
    assert (total, exact) == (LIBSTDCXX_TOTAL, LIBSTDCXX_EXACT)


def test_regression_corpus():
    """Names that each exposed a distinct defect. Every one must stay fixed."""
    total, exact = _score("itanium-regressions.txt", "llvm")
    assert (total, exact) == (REGRESSIONS_TOTAL, REGRESSIONS_EXACT)


def test_rust_matches_rustc_demangle():
    """Auto-detected, not forced.

    Forcing `language="rust"` would skip detection, which is the part most likely to be
    wrong: legacy Rust mangling *is* Itanium mangling, so a detection miss hands the
    name to the C++ parser and yields a plausible but quite wrong spelling rather than
    an error.
    """
    total, exact = _score("rust-real-world.txt", "llvm")
    assert (total, exact) == (RUST_TOTAL, RUST_EXACT)


def test_rust_matches_the_shipped_toolchain():
    """Symbols from rustc's own libraries, including ELF-versioned ones."""
    total, exact = _score("rust-toolchain.txt", "llvm")
    assert (total, exact) == (RUST_TOOLCHAIN_TOTAL, RUST_TOOLCHAIN_EXACT)


def test_gnu_shortfalls_are_only_the_known_reference_divergences():
    """Pin *which* names fail under GNU, not merely how many."""
    failing = []
    for mangled, expected in load_corpus("itanium-real-world-gnu.txt"):
        try:
            got = demangle.demangle_strict(mangled, style="gnu")
        except (DemanglingError, RecursionError):
            failing.append(mangled)
            continue
        if got != expected:
            failing.append(mangled)
    assert failing == GNU_DIVERGENCES


@pytest.mark.parametrize(
    "corpus",
    [
        "itanium-real-world.txt",
        "msvc-llvm-corpus.txt",
        "itanium-libstdcxx.txt",
        "rust-real-world.txt",
        "rust-toolchain.txt",
    ],
)
def test_best_effort_never_raises_on_any_corpus_name(corpus):
    """Whatever the corpus holds, `demangle()` answers rather than raising."""
    for mangled, _ in load_corpus(corpus):
        assert isinstance(demangle.demangle(mangled), str)
