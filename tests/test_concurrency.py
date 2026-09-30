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

#: Every scheme's corpus: a race in one scheme would not show in another.
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

#: The race this catches showed up at 3% of names, so a few hundred per scheme suffice.
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


def test_schemes_imported_on_first_use_from_several_threads_at_once():
    """Built-in schemes are imported when a name first reaches them. Threads arriving at
    the same unimported scheme at once must all get its answer, not a declined name."""
    import subprocess
    import sys
    from pathlib import Path

    expected = {
        "Java_java_lang_System_nanoTime": "java.lang.System.nanoTime",
        "_D10TypeInfo_c6__vtblZ": "vtable for TypeInfo_c",
        "@$beql$qrx5_GUIDt1": "operator ==(_GUID&, _GUID&)",
        "._OBJC_CLASS_A_B209": "Objective-C class A_B209",
    }
    script = (
        "import threading, demangle\n"
        f"expected = {expected!r}\n"
        f"barrier = threading.Barrier({THREADS})\n"
        "seen = []\n"
        "def work():\n"
        "    barrier.wait()\n"
        "    seen.append({name: demangle.demangle(name) for name in expected})\n"
        f"threads = [threading.Thread(target=work) for _ in range({THREADS})]\n"
        "[t.start() for t in threads]\n"
        "[t.join() for t in threads]\n"
        f"assert len(seen) == {THREADS} and all(answer == expected for answer in seen), seen\n"
    )
    source = Path(__file__).resolve().parent.parent / "src"
    for _ in range(ROUNDS):
        subprocess.run([sys.executable, "-c", script], check=True, env={"PYTHONPATH": str(source)})


def test_a_cache_turning_over_under_many_threads_serves_only_right_answers(corpus, monkeypatch):
    """A small cache turns a generation over every few dozen names, so threads race the
    turnover itself: one moving the young generation to old while others read or promote
    through it. Turnover is exact, so the bound holds, and an entry is never swapped."""
    from demangle import api
    from demangle.core.cache import BoundedCache

    names = corpus[:1500]
    expected = {name: demangle.demangle(name) for name in names}
    small = BoundedCache(max_size=64)
    monkeypatch.setattr(api, "_CACHE", small)

    def work(seed):
        for name in _shuffled(names, seed) + names[:200]:
            assert demangle.demangle(name) == expected[name], name

    _run_threaded(work)
    assert len(small) <= small.max_size
    assert small.hits > 0
    assert small.misses > 0


def test_styles_resolved_on_first_use_from_several_threads_at_once():
    """Each style's per-language options are imported when a scheme first needs them.
    Threads asking under both styles at once, in a fresh process, must all get the
    answer one thread gets."""
    import subprocess
    import sys
    from pathlib import Path

    names = ["_ZNSs4sizeEv", "?f@@YAXH@Z", "$sSiN", "_RNvCs1234_7mycrate3foo", "f__Fi", "__dt__6CActorFv"]
    expected = {(name, style): demangle.demangle(name, style=style) for name in names for style in ("llvm", "gnu")}
    script = (
        "import threading, demangle\n"
        f"expected = {expected!r}\n"
        f"barrier = threading.Barrier({THREADS})\n"
        "seen = []\n"
        "def work():\n"
        "    barrier.wait()\n"
        "    seen.append({key: demangle.demangle(key[0], style=key[1]) for key in expected})\n"
        f"threads = [threading.Thread(target=work) for _ in range({THREADS})]\n"
        "[t.start() for t in threads]\n"
        "[t.join() for t in threads]\n"
        f"assert len(seen) == {THREADS} and all(answer == expected for answer in seen), seen\n"
    )
    source = Path(__file__).resolve().parent.parent / "src"
    for _ in range(ROUNDS):
        subprocess.run([sys.executable, "-c", script], check=True, env={"PYTHONPATH": str(source)})


def test_a_weighed_cache_stays_within_its_weight_under_many_threads():
    """Threads adding at once must not lose each other's weight; under free threading
    an unguarded `+=` did, and the cache held half as much again as its bound. The
    threads stop together, since one left running alone turns the excess over."""
    from demangle.core.cache import BoundedCache

    entry = 2_000
    value = "v" * (entry // 2)
    for _ in range(ROUNDS):
        cache = BoundedCache(max_size=1 << 30, max_weight=100_000, weigh=lambda key, held: len(key) + len(held))
        stop = threading.Event()

        def work(seed, cache=cache, stop=stop):
            index = 0
            while not stop.is_set():
                cache.put(f"{seed}-{index}".ljust(entry // 2, "k"), value)
                index += 1

        threads = [threading.Thread(target=work, args=(seed,)) for seed in range(THREADS)]
        for thread in threads:
            thread.start()
        stop.wait(0.05)
        stop.set()
        for thread in threads:
            thread.join()
        young = sum(len(key) + len(held) for key, held in cache._young.items())
        assert cache._young_weight == young
        assert young + sum(len(key) + len(held) for key, held in cache._old.items()) <= 100_000 + 2 * entry


def test_a_registration_during_a_parse_leaves_no_stale_answer(monkeypatch):
    """A call that parsed with the plugin from before a registration must not store its
    answer after the registration cleared the cache, where it would be served forever."""
    from demangle.core import registry
    from demangle.core.limits import Limits
    from demangle.core.plugin import LanguagePlugin

    name = "_ZN5outer5inner4funcIiEEvT_"
    replaced = threading.Event()
    used = []

    def work(seed):
        index = 0
        while not replaced.is_set():
            limits = Limits(max_depth=300 + THREADS * index + seed)
            used.append(limits)
            demangle.demangle(name, limits=limits)
            index += 1

    def refuse(*arguments):
        raise ValueError("mine")

    mine = LanguagePlugin(name="itanium", detect=lambda text: text.startswith("_Z"), parse=refuse, priority=1)
    # Registered into copies, which `undo` puts back.
    monkeypatch.setattr(registry, "_plugins", dict(registry._plugins))
    monkeypatch.setattr(registry, "_ordered", None)
    monkeypatch.setattr(registry, "_by_first", None)
    threads = [threading.Thread(target=work, args=(seed,)) for seed in range(THREADS)]
    for thread in threads:
        thread.start()
    try:
        demangle.register_language(mine)
    finally:
        replaced.set()
        for thread in threads:
            thread.join()
    try:
        assert [limits for limits in used if demangle.demangle(name, limits=limits) != name] == []
    finally:
        monkeypatch.undo()
        demangle.cache_clear()
