"""Malformed, truncated and adversarial input.

A mangled name is attacker-controlled in any tool that opens files it did not produce.
These tests assert the properties that make the library safe to point at a hostile
binary.

Every property here asserts the *real* invariant, not merely that a `str` came back. An
earlier version of this file checked only `isinstance(result, str)`, and a demangler
stubbed to return `""` for every input -- one that had silently lost every symbol it was
given -- passed fourteen of its sixteen tests. `assert isinstance(x, str)` is not a test.
"""

import contextlib

import pytest

import demangle
from demangle.core.ast import rendered
from demangle.core.errors import DemanglingError, LimitExceeded
from demangle.core.limits import Limits

from .conftest import load_corpus

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

MANGLING_ALPHABET = "_ZNSKPRIEJLTUvbcahstijlmxynofdeg0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ?@$."

deadline = settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def answered(value):
    """The library's actual contract, as one predicate.

    `demangle()` either produced something it can stand behind -- in which case
    `demangle_strict()` agrees, character for character -- or it handed the name back
    untouched. There is no third outcome, and in particular it never returns a *different*
    string while quietly failing.
    """
    result = demangle.demangle(value)
    if result == value:
        return True
    try:
        return demangle.demangle_strict(value) == result
    except DemanglingError:
        return False


# -- the strategies ----------------------------------------------------------------
#
# Random text almost never reaches a parser: measured over 20,000 samples from the
# mangling alphabet, 1.5% got past the one-character prefix test in `detect` and *none*
# parsed successfully. A defect living behind the grammar -- which is where they live --
# would never be found that way. So the strategies below start from names that really do
# parse and damage them.

CORPUS = [name for name, _ in load_corpus("itanium-real-world.txt")][:400]
CORPUS += [name for name, _ in load_corpus("msvc-llvm-corpus.txt")][:200]
CORPUS += [name for name, _ in load_corpus("rust-real-world.txt")][:200]
# Every scheme, so a grammar added later is fuzzed as soon as it has a corpus. A
# hostile binary is not obliged to hold only the schemes that existed first.
CORPUS += [name for name, _ in load_corpus("swift-real-world.txt")][:200]
CORPUS += [name for name, _ in load_corpus("d-real-world.txt")][:200]
CORPUS += [name for name, _ in load_corpus("go-real-world.txt")][:200]
CORPUS += [name for name, _ in load_corpus("nim-real-world.txt")][:200]
CORPUS += [name for name, _ in load_corpus("pascal-real-world.txt")][:200]
CORPUS += [name for name, _ in load_corpus("objc-real-world.txt")][:200]
CORPUS += [name for name, _ in load_corpus("delphi-real-world.txt")]


@st.composite
def mutated_symbol(draw):
    """A real mangled name with one thing wrong with it."""
    name = draw(st.sampled_from(CORPUS))
    if not name:
        return name
    how = draw(st.sampled_from(["truncate", "flip", "delete", "insert", "splice", "repeat"]))
    index = draw(st.integers(min_value=0, max_value=max(len(name) - 1, 0)))
    if how == "truncate":
        return name[:index]
    if how == "flip":
        return name[:index] + draw(st.sampled_from(MANGLING_ALPHABET)) + name[index + 1 :]
    if how == "delete":
        return name[:index] + name[index + 1 :]
    if how == "insert":
        return name[:index] + draw(st.sampled_from(MANGLING_ALPHABET)) + name[index:]
    if how == "splice":
        return name[:index] + draw(st.sampled_from(CORPUS))
    return name[:index] + name[index:] * 2


class TestNeverRaises:
    """`demangle()` answers for any input at all."""

    @deadline
    @given(st.text(max_size=200))
    def test_arbitrary_text(self, value):
        assert answered(value)

    @deadline
    @given(st.text(alphabet=MANGLING_ALPHABET, max_size=200))
    def test_mangling_shaped_text(self, value):
        assert answered(value)

    @deadline
    @given(st.binary(max_size=200))
    def test_decoded_bytes(self, payload):
        assert answered(payload.decode("latin-1"))

    @deadline
    @given(mutated_symbol())
    def test_damaged_real_symbols(self, value):
        """The strategy that actually reaches the grammars."""
        assert answered(value)

    def test_non_string_input_is_returned_not_raised(self):
        """Symbol tables are read as bytes; this is the likeliest caller mistake.

        The annotations say `str`, and these calls deliberately violate them -- which is
        the point. A type checker protects callers who use one; this protects the rest.
        """
        for value in (b"_Z1fv", 42, None, ["_Z1fv"]):
            assert demangle.demangle(value) is value  # ty: ignore[invalid-argument-type]
            assert demangle.detect(value) is None


class TestErrorContract:
    @deadline
    @given(mutated_symbol())
    def test_strict_only_raises_demangling_errors(self, value):
        try:
            demangle.demangle_strict(value)
        except DemanglingError:
            pass
        except Exception as exc:
            pytest.fail(f"{type(exc).__name__} escaped demangle_strict for {value!r}")

    @deadline
    @given(mutated_symbol())
    def test_parse_only_raises_demangling_errors(self, value):
        try:
            demangle.parse(value)
        except DemanglingError:
            pass
        except Exception as exc:
            pytest.fail(f"{type(exc).__name__} escaped parse for {value!r}")

    def test_a_broken_plugin_is_reported_not_leaked(self):
        """A defect in a plugin must still arrive as this package's error type."""
        from demangle.core import registry
        from demangle.core.plugin import LanguagePlugin

        def detect(name):
            return name.startswith("@@bug@@")

        def parse(mangled, builder, limits=None, options=None):
            raise ZeroDivisionError("a bug in a plugin")

        demangle.register_language(
            LanguagePlugin(name="bug", detect=detect, parse=parse, description="test", priority=1)
        )
        try:
            demangle.cache_clear()
            assert demangle.demangle("@@bug@@x") == "@@bug@@x"
            with pytest.raises(DemanglingError):
                demangle.demangle_strict("@@bug@@x")
        finally:
            registry._plugins.pop("bug", None)
            registry._ordered = None
            demangle.cache_clear()

    def test_a_broken_detect_cannot_take_the_library_down(self):
        from demangle.core import registry
        from demangle.core.plugin import LanguagePlugin

        def detect(name):
            raise RuntimeError("detect exploded")

        demangle.register_language(
            LanguagePlugin(name="boom", detect=detect, parse=detect, description="test", priority=1)
        )
        try:
            demangle.cache_clear()
            assert demangle.demangle("_Z1fv") == "f()"
            assert demangle.detect("memcpy") is None
        finally:
            registry._plugins.pop("boom", None)
            registry._ordered = None
            demangle.cache_clear()


class TestDeterminism:
    @deadline
    @given(mutated_symbol())
    def test_repeated_calls_agree(self, value):
        """Cleared between calls, or this asserts only that the cache works."""
        demangle.cache_clear()
        first = demangle.demangle(value)
        demangle.cache_clear()
        assert demangle.demangle(value) == first


class TestResourceBounds:
    @pytest.mark.parametrize(
        "value",
        [
            "_Z" + "P" * 5000 + "i",
            "_Z" + "N" * 5000,
            "_Z1f" + "I" * 2000 + "E" * 2000,
            "_ZN" + "1a" * 3000 + "E",
            "_Z1f" + "S_" * 5000,
            "?" + "?" * 5000,
            "_R" + "B" * 5000,
            "_Z" + "GV" * 400 + "1fv",
            "_ZN" + "L" * 2000 + "1aE",
            "_Z1f" + "Tp" * 2000 + "TyEvv",
            "_Z" + "1" * 4400 + "x",
            "_Z1fS" + "Z" * 60000 + "_",
        ],
        ids=[
            "pointers",
            "nested",
            "templates",
            "prefixes",
            "substitutions",
            "msvc",
            "rust",
            "special-names",
            "internal-linkage",
            "parameter-decls",
            "huge-length",
            "huge-seq-id",
        ],
    )
    def test_pathological_input_terminates_and_is_answered(self, value):
        assert answered(value)

    def test_substitution_blowup_is_bounded(self):
        """Each entry built from two copies of the last: output doubles every few bytes.

        Enforcing the bound by measuring the finished string meant building it first;
        ~180 bytes of input reached 1.86 GB. The bound is checked as the type is built.
        """
        name = "_Z1fPi" + "".join("MS_S_" if i == 0 else f"MS{i - 1}_S{i - 1}_" for i in range(40))
        assert demangle.demangle(name) == name
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(name)

    def test_truncation_at_every_offset_is_answered(self):
        """Symbol tables really do hold names cut short by fixed-width fields."""
        for full in (
            "_ZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEE6appendEPKc",
            "??$?0N@?$Foo@H@@QEAA@N@Z",
            "_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E",
            "$s10Foundation10CocoaErrorV4CodeVSQAAMc",
            "_TFC3foo3bar3basfT3zimCS_3zim_T_",
            "_D5mypkg5mymod5Point4normMFZi",
            "eqdestroy___systemZassertions_23",
            "MYUNIT$_$TWIDGET_$__$$_AREA$$LONGINT",
        ):
            for cut in range(len(full)):
                assert answered(full[:cut])

    def test_output_limit_is_enforced(self):
        demangle.cache_clear()
        tiny = Limits(max_output=16)
        name = "_ZNSt6vectorIiSaIiEE9push_backERKi"
        assert demangle.demangle(name, limits=tiny) == name
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(name, limits=tiny)

    def test_tight_limits_do_not_poison_the_cache(self):
        """One caller's bounds must not silently degrade every other caller's answer."""
        demangle.cache_clear()
        name = "_ZNSt6vectorIiSaIiEE9push_backERKi"
        assert demangle.demangle(name, limits=Limits(max_output=16)) == name
        assert demangle.demangle(name) == "std::vector<int, std::allocator<int>>::push_back(int const&)"

    def test_depth_limit_is_enforced(self):
        demangle.cache_clear()
        shallow = Limits(max_depth=4)
        deep = "_Z1f" + "P" * 50 + "i"
        assert demangle.demangle(deep, limits=shallow) == deep

    def test_a_stack_overflow_while_rendering_is_reported_as_a_bound(self):
        """`max_depth` bounds the parse; rendering the tree afterwards is a second walk.

        `build`, and every scheme's `render`, are genuinely recursive over the node
        shapes, so a tree well inside the limit can still be deeper than the
        interpreter's stack. What must never happen is a bare `RecursionError` reaching a
        caller: `demangle()` is documented never to raise and `parse()` to raise only
        `DemanglingError`, and the interpreter's own error is neither.

        Asserted on `rendered` directly rather than by building a tree deep enough to
        overflow, because *how* deep that is is a property of the interpreter and the
        platform rather than of this package -- which is exactly what the first version
        of this test got wrong, passing on CPython 3.11 and failing on everything else.
        """

        def overflows():
            raise RecursionError("maximum recursion depth exceeded")

        with pytest.raises(LimitExceeded) as caught:
            rendered(overflows)
        assert caught.value.limit_name == "recursion depth"
        assert isinstance(caught.value, DemanglingError)

    def test_rendering_returns_its_value_when_it_does_not_overflow(self):
        assert rendered(lambda: "std::vector<int>") == "std::vector<int>"

    @pytest.mark.parametrize(
        "mangled",
        [
            "_RINvC1c1f" + "R" * 247 + "lE",
            "_RINvC1c1f" + "P" * 247 + "lE",
            "_RINvC1c1f" + "S" * 247 + "lE",
            "_Z1fIX" + "ng" * 249 + "Li1EEEv",
        ],
    )
    def test_a_deep_tree_never_raises_the_interpreters_own_error(self, mangled):
        """Whether these overflow depends on the interpreter; that they never report it
        as `RecursionError` does not. Anything but a `DemanglingError` fails here."""
        with contextlib.suppress(DemanglingError):
            demangle.parse(mangled).spell()

    def test_walking_a_deep_tree_does_not_need_the_stack(self):
        """`walk` is iterative, so `find` works however deep the tree is -- including on
        a tree too deep for `spell`, where a `yield from` per level would not."""
        tree = demangle.parse("_RINvC1c1f" + "R" * 247 + "lE")
        assert sum(1 for _ in tree.walk()) > 200
        assert [node.text for node in tree.find("name")][:2] == ["c", "f"]

    def test_input_limit_is_enforced(self):
        demangle.cache_clear()
        with pytest.raises(demangle.LimitExceeded) as info:
            demangle.demangle_strict("_Z1fv" + "P" * 100, limits=Limits(max_input=32))
        assert info.value.limit_name == "input length"
        assert info.value.limit_value == 32

    def test_relaxed_limits_actually_relax(self):
        deep = "_Z1f" + "P" * 400 + "i"
        assert demangle.demangle(deep, limits=Limits(max_depth=64)) == deep
        assert demangle.demangle(deep, limits=demangle.RELAXED_LIMITS).endswith("*)")
