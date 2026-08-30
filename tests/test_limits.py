"""Resource bounds, and the cache that must not outlive what it was keyed on.

`SECURITY.md` promises that recursion depth, output length, substitution count and
input length are all bounded, and that a caller can tighten any of them per call. It was
a promise about the package that only some of the package kept: MSVC read a
hundred-thousand-character name under `max_input=32` and then rejected it on output
length a fifth of a second later, and Rust read one under `max_input=32` and answered.
A bound checked after the work it was meant to prevent is a report, not a bound.

The tests here are written per *plugin* rather than per scheme name, so a scheme added
later has to keep the promise too rather than quietly not being covered.
"""

import time
from dataclasses import replace

import pytest

import demangle
from demangle.core.limits import Limits
from demangle.core.registry import available
from demangle.core.spelling import LEGACY_SPELLING_BUILDER, SPELLING_BUILDER
from demangle.core.style import Style
from demangle.schemes.itanium.options import GNU_OPTIONS

from .conftest import load_corpus

#: One real name per scheme, so the bound is tested against something the parser will
#: actually get its teeth into rather than against a name it refuses immediately.
CORPUS_FOR = {
    "itanium": "itanium-real-world.txt",
    "msvc": "msvc-llvm-corpus.txt",
    "rust": "rust-real-world.txt",
    "swift": "swift-real-world.txt",
    "d": "d-real-world.txt",
    "go": "go-real-world.txt",
    "nim": "nim-real-world.txt",
    "pascal": "pascal-real-world.txt",
    "objc": "objc-real-world.txt",
    "delphi": "delphi-real-world.txt",
    "jni": "jni-real-world.txt",
    "gnuv2": "gnuv2-libiberty.txt",
    "codewarrior": "codewarrior-cwdemangle.txt",
    "ada": "ada-libiberty.txt",
}


def _longest(name):
    """The longest name in a scheme's corpus, which is the one a tight bound will bite.

    Read by the first column alone, which is the mangled name in every corpus here --
    the pre-Itanium one carries its style and two spellings in the columns after it.
    """
    corpus = load_corpus(CORPUS_FOR[name])
    return max((mangled for mangled, _ in corpus), key=len, default="")


@pytest.mark.parametrize("plugin", [p.name for p in available()])
class TestEveryPluginHonoursTheInputBound:
    def test_a_name_longer_than_max_input_is_refused(self, plugin):
        """And refused as `LimitExceeded`, not as "this is not a name I can read"."""
        longest = _longest(plugin)
        assert longest, f"no corpus for {plugin}"
        tight = Limits(max_input=len(longest) - 1)
        with pytest.raises(demangle.LimitExceeded):
            demangle.demangle_strict(longest, language=plugin, limits=tight)

    def test_the_same_name_is_read_when_the_bound_allows_it(self, plugin):
        """The bound has to be the thing refusing it, not the name being unreadable."""
        longest = _longest(plugin)
        roomy = Limits(max_input=len(longest))
        demangle.demangle_strict(longest, language=plugin, limits=roomy)


class TestBoundsAreEnforcedWhileWorking:
    """A bound has to stop the work, not describe it afterwards.

    Each of these was a denial of service. The numbers are wide on purpose -- this is a
    test of asymptotics, not of one machine's speed -- but every case took seconds to
    days before the fix and milliseconds after.
    """

    @pytest.mark.parametrize(
        ("name", "mangled"),
        [
            # A GNU-runtime Objective-C method: the reading search was over pairs of
            # underscore positions, and re-mangled the whole symbol for each pair.
            ("objc method readings", "_i_" + "a_" * 800),
            # Rust v0 bound lifetimes: the count is a base-62 field, so each further
            # character multiplies the printer's work sixty-two-fold.
            ("rust bound lifetimes", "_RMC0FGZZZZZZ_Eu"),
            # MSVC: `max_input` was not consulted at all.
            ("msvc long name", "?f@@YAX" + "H" * 60000 + "@Z"),
            # Itanium substitution reuse, which can double the output every few bytes.
            ("itanium pointers", "_Z1f" + "P" * 40000 + "i"),
            # A pack expansion's pattern is read once per member of the pack, so nested
            # expansions cost the product of their arities: eight members and a pattern
            # seven expansions deep is eight million readings of sixty bytes of input.
            (
                "itanium nested pack expansions",
                "_Z1fIJ" + "i" * 8 + "EEv" + "Dp1AI" * 12 + "T_" + "E" * 12,
            ),
            # The same shape wide rather than deep.
            ("itanium wide pack expansion", "_Z1fIJ" + "i" * 2000 + "EEvDp1AIDp1AIT_EE"),
        ],
    )
    def test_a_hostile_name_is_answered_promptly(self, name, mangled):
        started = time.perf_counter()
        result = demangle.demangle(mangled)
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, f"{name} took {elapsed:.1f}s"
        assert isinstance(result, str)

    def test_a_deeply_nested_msvc_name_reports_the_bound_it_hit(self):
        """It used to say the name was unreadable, which is a different claim."""
        with pytest.raises(demangle.LimitExceeded):
            demangle.demangle_strict("?f@@YAX" + "PA" * 5000 + "H@Z")


class TestNothingEverAnswersWithNothing:
    """`demangle()` returns the spelling or the name. The empty string is neither.

    `_RCCC` returned `""`, which would have a tool label a function with a blank.
    """

    @pytest.mark.parametrize(
        "mangled",
        ["_RCCC", "_RCCCC", "_RC", "_RCC", "_R", "_Z", "?", "$s", "_D", "@", "-[", "_i_"],
    )
    def test_a_short_ill_formed_name_comes_back_whole(self, mangled):
        assert demangle.demangle(mangled) == mangled

    def test_no_corpus_name_demangles_to_nothing(self):
        for corpus in CORPUS_FOR.values():
            for mangled, _ in load_corpus(corpus)[:500]:
                if mangled:
                    assert demangle.demangle(mangled) != ""


class TestTheCacheIsKeyedOnWhatChangesTheAnswer:
    """A cache that serves one caller another caller's spelling is worse than none."""

    NAME = "_ZNSt6vectorIiSaIiEE9push_backERKi"

    def test_two_styles_with_the_same_name_do_not_collide(self):
        """The key held `style.name`, and a caller may pass a `Style` object instead."""
        llvm_ish = Style(name="house", spelling_builder=SPELLING_BUILDER)
        gnu_ish = Style(
            name="house",
            spelling_builder=LEGACY_SPELLING_BUILDER,
            language_options={"itanium": GNU_OPTIONS},
        )
        assert demangle.demangle(self.NAME, style=llvm_ish) != demangle.demangle(self.NAME, style=gnu_ish)

    def test_registering_a_style_invalidates_what_was_cached_under_it(self):
        """Replacing `llvm` left every name demangled beforehand answering the old way."""
        before = demangle.demangle(self.NAME)
        replacement = Style(
            name="cache-probe",
            spelling_builder=SPELLING_BUILDER,
        )
        demangle.register_style(replacement)
        first = demangle.demangle(self.NAME, style="cache-probe")
        demangle.register_style(
            Style(
                name="cache-probe",
                spelling_builder=LEGACY_SPELLING_BUILDER,
                language_options={"itanium": GNU_OPTIONS},
            )
        )
        assert demangle.demangle(self.NAME, style="cache-probe") != first
        assert demangle.demangle(self.NAME) == before

    def test_limits_are_part_of_the_key(self):
        """Tight limits from one caller must not poison the entry for the next."""
        tight = Limits(max_output=8)
        assert demangle.demangle(self.NAME, limits=tight) == self.NAME
        assert demangle.demangle(self.NAME) != self.NAME


class TestTheBoundReportedIsTheBoundInForce:
    """A scheme may narrow the caller's limit; the report has to name what stopped it.

    MSVC's parser holds a ceiling of its own -- `min(limits.max_depth, MAX_DEPTH)`, with
    `MAX_DEPTH` 64 -- so that a level costing several interpreter frames can never make
    the answer depend on how deep the caller's own stack already was. The error read the
    *caller's* figure back out of `limits`, so a parse that stopped at 64 announced
    "exceeded recursion depth limit of 200000": a number never in force, pointing at a
    limit already far above the ceiling that would change nothing if raised.
    """

    NAME = "?f@@YAX" + "PA" * 100 + "H@Z"

    def _bound_reported(self, asked):
        limits = replace(demangle.RELAXED_LIMITS, max_depth=asked)
        with pytest.raises(demangle.LimitExceeded) as caught:
            demangle.demangle_strict(self.NAME, limits=limits)
        return caught.value.limit_value

    @pytest.mark.parametrize("asked", [8, 16, 64])
    def test_a_caller_tightening_below_the_ceiling_is_told_its_own_figure(self, asked):
        assert self._bound_reported(asked) == asked

    @pytest.mark.parametrize("asked", [2048, 200_000])
    def test_a_caller_asking_past_the_ceiling_is_told_the_ceiling(self, asked):
        from demangle.schemes.msvc._parser import _Demangler

        assert self._bound_reported(asked) == _Demangler.MAX_DEPTH

    def test_the_depth_counter_is_what_stops_it_rather_than_the_interpreter(self):
        # The point of the ceiling: with a stack far deeper than CPython's default, the
        # bound still fires at the same place, so the answer does not depend on the
        # caller's stack. A converted RecursionError could not hold this.
        import sys
        import threading

        seen = []

        def run():
            sys.setrecursionlimit(200_000)
            seen.append(self._bound_reported(200_000))

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        from demangle.schemes.msvc._parser import _Demangler

        assert seen == [_Demangler.MAX_DEPTH]


class TestDepthExhaustionIsReportedAsABound:
    """Two ceilings govern nesting, and both mean the same thing about the name.

    `max_depth` is one. The interpreter's own recursion limit is the other, and it is
    the lower of the two in practice -- a production costs several Python frames, so at
    the default limit an Itanium name gives out around 141 levels of nested template,
    well under the default `max_depth` of 256. Which binds first depends on the shape of
    the name and on how deep the caller's stack already was.

    One used to arrive as `LimitExceeded` and the other as
    `ParseError: itanium parser failed: RecursionError(...)`, which reads as a defect in
    the parser rather than a bound doing its job.

    The nesting below is deeper than the *limit in force*, and that matters rather than
    being belt and braces. These names were 400 deep, which is under `RELAXED_LIMITS`'
    `max_depth` of 2048, so on CPython the only thing stopping them was the interpreter's
    stack -- and on PyPy, whose stack is far deeper, nothing stopped them at all and the
    names parsed. Deriving the count from `max_depth` tests the bound this asserts the
    existence of, on any interpreter, and cannot drift if the limit is retuned.
    """

    #: Each of these repeats costs more than one level, so repeating a production
    #: `max_depth` times is comfortably past the ceiling on every scheme here.
    DEEPER_THAN_THE_LIMIT = demangle.RELAXED_LIMITS.max_depth

    @pytest.mark.parametrize(
        "mangled",
        [
            "_Z1f" + "1XI" * DEEPER_THAN_THE_LIMIT + "i" + "E" * DEEPER_THAN_THE_LIMIT,
            # An expression nested inside an expression, which is what `ng` is -- not a
            # `decltype` nested inside a `decltype`, which was here before and which
            # neither reference reads: `_Z1fDTDTfp_EEv` is handed back by `c++filt` 2.42
            # and `llvm-cxxfilt` 18.1 alike. It parsed here only through a catch-all in
            # `_expression` that read whatever could open a `<type>` as one, and it went
            # with that. `-(-(-fp))` is the same shape and is read the same way by both.
            "_Z1fDT" + "ng" * DEEPER_THAN_THE_LIMIT + "fp_" + "Ev",
            "_Z1f" + "PF" * DEEPER_THAN_THE_LIMIT + "i" + "E" * DEEPER_THAN_THE_LIMIT,
            "?f@@YAX" + "PA" * DEEPER_THAN_THE_LIMIT + "H@Z",
        ],
    )
    def test_a_name_too_deep_to_follow_says_so(self, mangled):
        with pytest.raises(demangle.LimitExceeded):
            demangle.demangle_strict(mangled, limits=demangle.RELAXED_LIMITS)

    def test_and_demangle_still_answers(self):
        # 400 rather than the count above, and correctly: this one runs under the
        # *default* limits, whose `max_depth` of 256 the counter reaches well before
        # here. What it asserts is that the bound comes back as the name unchanged
        # rather than as an exception, which is `demangle()`'s promise.
        deep = "_Z1f" + "1XI" * 400 + "i" + "E" * 400
        assert demangle.demangle(deep) == deep
