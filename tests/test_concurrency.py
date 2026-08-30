"""The library under concurrent use.

A demangler is embedded in tools that walk symbol tables, and those tools thread: a
disassembler labels functions from a worker pool, a symbolication service serves one
request per thread. So "the same name always gives the same answer" has to hold when
several threads are asking at once, not only when one is.

This file exists because it did not. The Rust scheme held one parser at module scope
and both of its grammars keep the name they are reading on `self`, so two threads
overwrote each other mid-parse. It did not raise. It returned *another symbol's* name --
160 times out of 5,710 across eight threads -- and `demangle()` then memoised that
answer under the first symbol's key, so the wrong result outlived the threads that
produced it. `SECURITY.md` calls non-deterministic output for identical input a
vulnerability, and it is right to: a tool that renames a function from a symbol table
writes that lie into its database.

The property is deliberately the strong one. Not "no exception escaped" -- the race
never raised. Every thread must agree, character for character, with the answer a
single thread computes for the same name.
"""

import threading

import pytest

import demangle

from .conftest import load_corpus

#: Every scheme's corpus, because the defect this file was written for lived in one
#: scheme and nothing would have found it in another. A scheme added later is covered
#: as soon as it has a corpus.
CORPORA = (
    "itanium-real-world.txt",
    "itanium-libstdcxx.txt",
    "msvc-llvm-corpus.txt",
    "rust-real-world.txt",
    "rust-toolchain.txt",
    "swift-real-world.txt",
    "d-real-world.txt",
    "go-real-world.txt",
    "nim-real-world.txt",
    "pascal-real-world.txt",
    "objc-real-world.txt",
    "delphi-real-world.txt",
)

#: Enough names per corpus to interleave the threads properly, few enough to stay a
#: unit test. The race this catches showed up at 3% of names, so a few hundred per
#: scheme is far past the point where it would be missed.
PER_CORPUS = 300
THREADS = 8
ROUNDS = 3


def _names():
    collected = []
    for corpus in CORPORA:
        collected += [name for name, _ in load_corpus(corpus)[:PER_CORPUS]]
    return [name for name in collected if name]


def _run_threaded(work):
    """Run `work(seed)` on `THREADS` threads for `ROUNDS` rounds, re-raising failures.

    A bare `Thread` swallows whatever its target raises, which would turn a failed
    assertion into a passing test. Every thread's exception is collected and the first
    is re-raised on the main thread.
    """
    escaped = []

    def guarded(seed):
        try:
            work(seed)
        except BaseException as error:
            escaped.append(error)

    for _ in range(ROUNDS):
        threads = [threading.Thread(target=guarded, args=(seed,)) for seed in range(THREADS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    if escaped:
        raise escaped[0]


def _shuffled(names, seed):
    """A deterministic per-thread order, so threads reach different names at once.

    Every thread walking the list in the same order would have them mostly parsing the
    same name at the same time, which is the one arrangement in which a shared parser
    looks like it works.
    """
    import random

    order = list(names)
    random.Random(seed).shuffle(order)
    return order


@pytest.fixture(scope="module")
def corpus():
    names = _names()
    assert len(names) > 1000, "corpora did not load; this test would prove nothing"
    return names


def test_demangle_agrees_with_the_single_threaded_answer(corpus):
    """Eight threads over every corpus produce exactly what one thread produces."""
    expected = {name: demangle.demangle(name) for name in corpus}
    demangle.cache_clear()

    def work(seed):
        for name in _shuffled(corpus, seed):
            assert demangle.demangle(name) == expected[name], name

    _run_threaded(work)


def test_parse_agrees_with_the_single_threaded_answer(corpus):
    """The same for the tree path, which shares an interning table between callers."""
    expected = {}
    for name in corpus:
        try:
            expected[name] = demangle.parse(name).spell()
        except demangle.DemanglingError:
            expected[name] = None

    def work(seed):
        for name in _shuffled(corpus, seed):
            try:
                got = demangle.parse(name).spell()
            except demangle.DemanglingError:
                got = None
            assert got == expected[name], name

    _run_threaded(work)


def test_detect_agrees_with_the_single_threaded_answer(corpus):
    """Detection reads a registry cache without the lock; it must still be right."""
    expected = {name: demangle.detect(name) for name in corpus}

    def work(seed):
        for name in _shuffled(corpus, seed):
            assert demangle.detect(name) == expected[name], name

    _run_threaded(work)


def test_the_registry_survives_being_loaded_from_several_threads_at_once():
    """First use from several threads at once must not expose a half-built registry.

    The failure this guards against is a second thread arriving while the first is
    still importing the built-in schemes and being told the library does not support
    a language it does support.
    """
    from demangle.core import registry

    expected = sorted(registry.names())
    seen = []
    barrier = threading.Barrier(THREADS)

    def work(_seed):
        barrier.wait()
        seen.append(sorted(registry.names()))
        registry.get("msvc")
        registry.get("itanium")

    _run_threaded(work)
    assert all(names == expected for names in seen)


def test_mixed_budgets_do_not_share_an_answer(corpus):
    """Threads asking for different `Limits` must not be served each other's answers.

    A limit now has a path of its own: a name that exceeds one is refused, and the
    refusal is cached like any other answer. The cache is keyed on the limits as well as
    the name, so the entry a tightened budget leaves must not reach a caller who asked
    for a relaxed one -- and a shared key would show up exactly here, where the same
    name is asked about under five budgets at once.
    """
    from dataclasses import replace

    budgets = {
        "default": demangle.DEFAULT_LIMITS,
        "relaxed": demangle.RELAXED_LIMITS,
        "substitutions": replace(demangle.RELAXED_LIMITS, max_substitutions=4),
        "depth": replace(demangle.RELAXED_LIMITS, max_depth=8),
        "output": replace(demangle.RELAXED_LIMITS, max_output=32),
    }
    names = corpus[:1500]
    expected = {
        (label, name): demangle.demangle(name, limits=limits) for label, limits in budgets.items() for name in names
    }
    demangle.cache_clear()

    def work(seed):
        import random

        local = random.Random(seed)
        for name in _shuffled(names, seed):
            label = local.choice(list(budgets))
            assert demangle.demangle(name, limits=budgets[label]) == expected[(label, name)], (name, label)

    _run_threaded(work)
