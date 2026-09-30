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

#: One real name per scheme, so the bound is tested on a name the parser reads deeply.
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


#: Labelled so the label is the test id: `PYTEST_CURRENT_TEST` carries the id in the
#: environment, and Windows refuses values past 32,767 characters.
HOSTILE_NAMES = [
    # A GNU-runtime Objective-C method: readings search over pairs of underscores.
    ("objc method readings", "_i_" + "a_" * 800),
    # Rust v0 bound lifetimes: the count is a base-62 field, so each further
    # character multiplies the printer's work sixty-two-fold.
    ("rust bound lifetimes", "_RMC0FGZZZZZZ_Eu"),
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
]


class TestBoundsAreEnforcedWhileWorking:
    """A bound has to stop the work, not describe it afterwards.

    Each of these was a denial of service. The numbers are wide on purpose -- this is a
    test of asymptotics, not of one machine's speed -- but every case took seconds to
    days before the fix and milliseconds after.
    """

    @pytest.mark.parametrize(("name", "mangled"), HOSTILE_NAMES, ids=[label for label, _ in HOSTILE_NAMES])
    def test_a_hostile_name_is_answered_promptly(self, name, mangled):
        started = time.perf_counter()
        result = demangle.demangle(mangled)
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, f"{name} took {elapsed:.1f}s"
        assert isinstance(result, str)

    def test_a_deeply_nested_msvc_name_reports_the_bound_it_hit(self):
        """It says which bound was hit, not that the name is unreadable."""
        with pytest.raises(demangle.LimitExceeded):
            demangle.demangle_strict("?f@@YAX" + "PA" * 5000 + "H@Z")

    def test_an_msvc_symbol_nested_in_a_template_argument_keeps_the_callers_bound(self):
        """The parser spun up for a nested symbol ran with the default limits, so a
        tight `max_depth` could be dodged by putting the deep part in a template argument."""
        nested = "?g@@YAXPAPAPAPAPAPAH@Z"
        tight = Limits(max_depth=5)
        with pytest.raises(demangle.LimitExceeded):
            demangle.demangle_strict(nested, language="msvc", limits=tight)
        with pytest.raises(demangle.LimitExceeded):
            demangle.demangle_strict(f"??$f@H$1{nested}@@YAXXZ", language="msvc", limits=tight)
        assert demangle.demangle_strict(f"??$f@H$1{nested}@@YAXXZ", language="msvc").startswith("void __cdecl f<")

    def test_an_msvc_md5_name_is_held_to_max_output(self):
        """Every other path went through the length check; the hashed one just returned."""
        with pytest.raises(demangle.LimitExceeded):
            demangle.demangle_strict("??@" + "A" * 1000 + "@", language="msvc", limits=Limits(max_output=10))

    def test_an_msvc_array_with_a_thousand_extents_reports_the_bound_it_hit(self):
        """`DOI@` is a count of 1000, and each extent nested without a depth check: a
        `RecursionError`, swallowed into a `ParseError` that called the name unreadable."""
        with pytest.raises(demangle.LimitExceeded):
            demangle.demangle_strict("?arr@@3QAYDOI@" + "1" * 1000 + "HB", language="msvc")


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
        try:
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
        finally:
            # Taken back out, or `--list-styles` in a later test file sees it.
            from demangle.core.style import _STYLES

            if _STYLES is not None:
                _STYLES.pop("cache-probe", None)
            demangle.cache_clear()

    def test_limits_are_part_of_the_key(self):
        """Tight limits from one caller must not poison the entry for the next."""
        tight = Limits(max_output=8)
        assert demangle.demangle(self.NAME, limits=tight) == self.NAME
        assert demangle.demangle(self.NAME) != self.NAME


def _on_a_deep_stack(work):
    """Run `work` where the interpreter's stack is not the binding bound.

    A level of nesting costs several frames, so at CPython's default recursion limit
    the stack may give out before `max_depth` does, and by how much depends on how deep
    the caller already was. Raising it makes `max_depth` the bound a test observes.
    """
    import sys
    import threading

    seen = []
    previous_limit = sys.getrecursionlimit()
    previous_size = threading.stack_size(128 << 20)
    try:
        sys.setrecursionlimit(100_000)
        thread = threading.Thread(target=lambda: seen.append(work()))
        thread.start()
        thread.join()
    finally:
        threading.stack_size(previous_size)
        sys.setrecursionlimit(previous_limit)
    assert len(seen) == 1, "the work raised"
    return seen[0]


class TestMsvcNestingFollowsMaxDepth:
    """MSVC reads as deep as `max_depth` says, as Itanium does.

    It held a private ceiling of 64 under the caller's bound, so `RELAXED_LIMITS` could
    not read a name one level deeper than `DEFAULT_LIMITS`, and a caller who raised the
    bound was told a figure it had not set. When the interpreter's stack gives out first
    the name is refused the same way, as the bound in force.
    """

    DEFAULT = demangle.DEFAULT_LIMITS.max_depth

    @staticmethod
    def pointers(levels):
        return "?f@@YAX" + "PA" * levels + "H@Z"

    def _bound_reported(self, mangled, limits):
        with pytest.raises(demangle.LimitExceeded) as caught:
            demangle.demangle_strict(mangled, language="msvc", limits=limits)
        return caught.value.limit_value

    def test_nesting_just_under_the_default_is_read(self):
        # `f` and the pointee take the level the last pointer would need.
        spelled = _on_a_deep_stack(lambda: demangle.demangle_strict(self.pointers(self.DEFAULT - 1)))
        assert spelled.endswith("int " + "*" * (self.DEFAULT - 1) + ")")

    def test_nesting_just_over_the_default_is_refused_as_the_default(self):
        assert _on_a_deep_stack(lambda: self._bound_reported(self.pointers(self.DEFAULT), demangle.DEFAULT_LIMITS)) == (
            self.DEFAULT
        )
        assert demangle.demangle(self.pointers(self.DEFAULT)) == self.pointers(self.DEFAULT)

    def test_a_name_past_the_default_reads_under_relaxed_limits(self):
        deep = self.pointers(300)

        def both():
            return (
                self._bound_reported(deep, demangle.DEFAULT_LIMITS),
                demangle.demangle_strict(deep, limits=demangle.RELAXED_LIMITS),
            )

        refused, spelled = _on_a_deep_stack(both)
        assert refused == self.DEFAULT
        assert spelled.endswith("int " + "*" * 300 + ")")

    @pytest.mark.parametrize("asked", [8, 16, 64, 2048])
    def test_a_caller_is_told_its_own_figure(self, asked):
        mangled = self.pointers(asked + 10)
        limits = replace(demangle.RELAXED_LIMITS, max_depth=asked)
        assert _on_a_deep_stack(lambda: self._bound_reported(mangled, limits)) == asked

    @pytest.mark.parametrize(
        "mangled",
        [
            "?f@@YAX" + "PA" * 4000 + "H@Z",
            "?f@@YAX" + "P6AX" * 4000 + "H" + "@Z" * 4000 + "@Z",
            "?f@@YAX" + "V?$A@" * 4000 + "H" + "@@" * 4000 + "@Z",
            # nested symbols and local scopes recurse through names, never through a type
            "??$f@$1" * 4000 + "?x@@3HA" + "@@YAXXZ" * 4000,
            "?g@?1?" * 4000 + "?f@@YAXXZ" + "@YAXXZ" * 4000,
        ],
        ids=["pointer", "function-pointer", "template", "address-argument", "local-scope"],
    )
    def test_nesting_past_the_interpreters_stack_is_refused_cleanly(self, mangled):
        """At the default recursion limit these outrun the stack well before 2048 levels."""
        with pytest.raises(demangle.LimitExceeded) as caught:
            demangle.demangle_strict(mangled, language="msvc", limits=demangle.RELAXED_LIMITS)
        assert caught.value.limit_name == "recursion depth"
        with pytest.raises(demangle.LimitExceeded):
            demangle.parse(mangled, language="msvc", limits=demangle.RELAXED_LIMITS)
        assert demangle.demangle(mangled, limits=demangle.RELAXED_LIMITS) == mangled

    def test_a_type_past_the_interpreters_stack_is_refused_cleanly(self):
        encoding = "PA" * 4000 + "H"
        for mangled, call in ((encoding, demangle.demangle_type), ("." + encoding, demangle.demangle_strict)):
            with pytest.raises(demangle.LimitExceeded):
                call(mangled, language="msvc", limits=demangle.RELAXED_LIMITS)


class TestDepthExhaustionIsReportedAsABound:
    """Two ceilings govern nesting, and both mean the same thing about the name.

    `max_depth` is one. The interpreter's own recursion limit is the other, and it is
    the lower of the two in practice -- a production costs several Python frames, so at
    the default limit an Itanium name gives out around 141 levels of nested template,
    well under the default `max_depth` of 256. Which binds first depends on the shape of
    the name and on how deep the caller's stack already was.

    Both arrive as `LimitExceeded`, not one of them as
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
            # `-(-(-fp))`: nested expressions both references read (unlike nested `DT`).
            "_Z1fDT" + "ng" * DEEPER_THAN_THE_LIMIT + "fp_" + "Ev",
            "_Z1f" + "PF" * DEEPER_THAN_THE_LIMIT + "i" + "E" * DEEPER_THAN_THE_LIMIT,
            "?f@@YAX" + "PA" * DEEPER_THAN_THE_LIMIT + "H@Z",
        ],
    )
    def test_a_name_too_deep_to_follow_says_so(self, mangled):
        with pytest.raises(demangle.LimitExceeded):
            demangle.demangle_strict(mangled, limits=demangle.RELAXED_LIMITS)

    def test_and_demangle_still_answers(self):
        # 400 is past the *default* `max_depth` of 256; the bound must come back as the
        # name unchanged, not as an exception.
        deep = "_Z1f" + "1XI" * 400 + "i" + "E" * 400
        assert demangle.demangle(deep) == deep


class TestALimitRefusesRatherThanTruncates:
    """A bound must end the reading, not hand the name to a laxer scheme.

    `demangle()` tries the schemes that claim a name in priority order and moves on when
    one fails. A `LimitExceeded` was being treated as one of those failures, and it is a
    different statement: the scheme *did* claim the name and then ran out of the budget
    the caller set. Offering the same text on is how an Itanium name that spends more
    substitutions than a tightened budget allows came back as a declaration built by the
    pre-Itanium scheme out of the mangling itself.

    Nine corpus names did this, found by tightening each bound in turn over all 77,749 of
    them and asking which came back *different* rather than refused. A caller who lowers
    a limit is defending against hostile input, which is the last place to start guessing.
    """

    #: An Itanium name whose trailing `__i` the pre-Itanium schemes will read as a
    #: parameter list once Itanium gives up on it.
    OVERSPENT = "_ZN11Expressions2f2ILi1EEEvPApsT__i"

    #: An MSVC RTTI type descriptor, which reaches the scheme by a branch of its own.
    DESCRIPTOR = ".?AV?$vector@HV?$allocator@H@std@@@std@@"

    def test_the_name_comes_back_whole_rather_than_read_by_another_scheme(self):
        tight = replace(demangle.RELAXED_LIMITS, max_substitutions=2)
        assert demangle.demangle(self.OVERSPENT, limits=tight) == self.OVERSPENT
        # The pre-Itanium misreading, which must never become the answer.
        assert demangle.demangle(self.OVERSPENT, language="gnuv2") == "_ZN11Expressions2f2ILi1EEEvPApsT(int)"

    @pytest.mark.parametrize("entry", ["demangle_strict", "parse"])
    def test_the_strict_paths_report_the_bound(self, entry):
        tight = replace(demangle.RELAXED_LIMITS, max_substitutions=2)
        with pytest.raises(demangle.LimitExceeded):
            getattr(demangle, entry)(self.OVERSPENT, limits=tight)

    def test_a_relaxed_budget_still_reads_it(self):
        assert demangle.demangle(self.OVERSPENT, limits=demangle.RELAXED_LIMITS) == (
            "void Expressions::f2<1>(int (*) [+1])"
        )

    @pytest.mark.parametrize(("field", "value"), [("max_depth", 4), ("max_output", 8)])
    def test_a_type_descriptor_reports_the_bound_as_one(self, field, value):
        """The descriptor branch sat in front of the translation and leaked `_LimitHit`.

        `api` wrapped it in the arm meant for a plugin with a defect, so a caller who
        lowered a bound was told `msvc parser failed: _LimitHit('recursion depth')` --
        the wrong type, and a message accusing this library of a bug for doing what was
        asked.
        """
        limits = replace(demangle.RELAXED_LIMITS, **{field: value})
        with pytest.raises(demangle.LimitExceeded):
            demangle.demangle_strict(self.DESCRIPTOR, limits=limits)
        assert demangle.demangle_strict(self.DESCRIPTOR) == (
            "class std::vector<int, class std::allocator<int>> `RTTI Type Descriptor Name'"
        )

    @pytest.mark.sweep
    def test_no_corpus_name_answers_differently_under_a_tighter_bound(self, subtests):
        """The property the nine were found by, over a sample of every corpus."""
        names = [
            mangled for corpus in ("itanium-libstdcxx.txt", "gnuv2-libiberty.txt") for mangled, _ in load_corpus(corpus)
        ]
        for field, value in (("max_depth", 8), ("max_output", 32), ("max_substitutions", 4)):
            with subtests.test(field=field):
                changed = []
                for name in names:
                    try:
                        want = demangle.demangle_strict(name, limits=demangle.RELAXED_LIMITS)
                    except demangle.DemanglingError:
                        continue
                    try:
                        got = demangle.demangle_strict(name, limits=replace(demangle.RELAXED_LIMITS, **{field: value}))
                    except demangle.LimitExceeded:
                        continue
                    except demangle.DemanglingError:
                        continue
                    if got != want:
                        changed.append(name)
                assert changed == []
