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
ITANIUM_LLVM_TOTAL, ITANIUM_LLVM_EXACT = 196, 196
ITANIUM_GNU_TOTAL, ITANIUM_GNU_EXACT = 196, 195
MSVC_TOTAL, MSVC_EXACT = 609, 609

# The one GNU shortfall is not ours to fix. For
# `_Z16templateTemplateIN5outer5inner6HolderEiET_IT0_Li3EES4_` the two references
# disagree about the substitution table itself, not about spelling: llvm-cxxfilt
# resolves `S4_` to `outer::inner::Holder<int, 3>` and GNU c++filt to `int`, meaning
# GNU numbers one fewer entry for a template-template-parameter application. Matching
# both would require two incompatible parses of the same bytes, so we follow LLVM.
GNU_SUBSTITUTION_DIVERGENCE = "_Z16templateTemplateIN5outer5inner6HolderEiET_IT0_Li3EES4_"


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


def test_gnu_shortfall_is_only_the_known_reference_divergence():
    """Pin *which* name fails under GNU, not merely how many.

    A count alone would let this failure be traded for a different one silently.
    """
    failing = []
    for mangled, expected in load_corpus("itanium-real-world-gnu.txt"):
        try:
            got = demangle.demangle_strict(mangled, style="gnu")
        except (DemanglingError, RecursionError):
            failing.append(mangled)
            continue
        if got != expected:
            failing.append(mangled)
    assert failing == [GNU_SUBSTITUTION_DIVERGENCE]


@pytest.mark.parametrize("corpus", ["itanium-real-world.txt", "msvc-llvm-corpus.txt"])
def test_best_effort_never_raises_on_any_corpus_name(corpus):
    """Whatever the corpus holds, `demangle()` answers rather than raising."""
    for mangled, _ in load_corpus(corpus):
        assert isinstance(demangle.demangle(mangled), str)
