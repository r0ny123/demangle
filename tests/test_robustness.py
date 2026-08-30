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

from .conftest import CONFORMANCE, load_corpus

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

MANGLING_ALPHABET = "_ZNSKPRIEJLTUvbcahstijlmxynofdeg0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ?@$."

deadline = settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def corpus_sample(step):
    """Every `step`-th name from every conformance corpus, all schemes together.

    Sampled rather than exhaustive because the whole set is 81,000 names and the tests
    that use this run a pass per *prefix* of each one.
    """
    sampled = []
    for path in sorted(CONFORMANCE.iterdir()):
        if path.suffix not in (".txt", ".gz"):
            continue
        names = [name for name, _ in load_corpus(path.name.removesuffix(".gz"))]
        sampled.extend(names[::step])
    return sampled


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
CORPUS += [name for name, _ in load_corpus("delphi-tdump.txt")][:200]


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

    def test_non_string_input_is_reported_rather_than_ignored(self):
        """A wrong argument type is the caller's mistake, and is now said out loud.

        This used to hand the argument straight back. That read as safe and was not: a
        tool reading an ELF string table -- where names *are* bytes -- got every symbol
        back exactly as it went in, with no error and no expansion, and nothing to tell
        it why. The strict entry points were worse, reaching the registry's
        first-character screen and raising `TypeError: 'in <string>' requires string as
        left operand, not int`, which names neither the problem nor the fix, and which
        the documented "raises only DemanglingError" contract said could not happen.

        The "never raises" promise is about the *name* -- any string, mangled or not --
        not about the type of the argument.
        """
        for value in (b"_Z1fv", 42, None, ["_Z1fv"]):
            with pytest.raises(TypeError):
                demangle.demangle(value)  # ty: ignore[invalid-argument-type]
            with pytest.raises(TypeError):
                demangle.demangle_strict(value)  # ty: ignore[invalid-argument-type]
            with pytest.raises(TypeError):
                demangle.parse(value)  # ty: ignore[invalid-argument-type]
            # `detect` still answers rather than raising: it is offered every symbol in a
            # table and its whole vocabulary is "this scheme, or none".
            assert demangle.detect(value) is None  # ty: ignore[invalid-argument-type]

    def test_the_bytes_entry_points_round_trip_what_they_cannot_read(self):
        """`demangleb` makes the same promise `demangle` does, in bytes.

        Including for bytes that are not valid UTF-8, which a truncated symbol table
        produces by cutting a name mid-character.
        """
        assert demangle.demangleb(b"_ZN3foo3barEv") == b"foo::bar()"
        for value in (b"", b"memcpy", b"\xff\xfe_Z1fv", b"_Z1fv\x80\x81"):
            assert demangle.demangleb(value) == value
        assert demangle.detectb(b"?f@@YAXH@Z") == "msvc"
        assert demangle.parseb(b"_ZN3foo3barEv").spell() == "foo::bar()"
        assert demangle.signatureb(b"_ZN3foo3barEv").base_name == "bar"


class TestTheResultIsWritable:
    """Whatever comes back must be a string a caller can actually write out.

    `demangle()` is documented never to raise, and a caller reads that as a promise it
    can print, log, or serialise the answer. A `str` holding a lone surrogate breaks that
    promise one step later: `UnicodeEncodeError` on `.encode()`, on `json.dump`, on
    writing to a file. The Go scheme could produce one -- its escapes decode to bytes,
    and `%89` is not text -- so this is the invariant rather than a note in that module.
    """

    @staticmethod
    def writable(value):
        result = demangle.demangle(value)
        assert isinstance(result, str)
        result.encode("utf-8")
        return True

    @deadline
    @given(st.text(max_size=200))
    def test_arbitrary_text(self, value):
        assert self.writable(value)

    @deadline
    @given(mutated_symbol())
    def test_damaged_real_symbols(self, value):
        assert self.writable(value)

    @deadline
    @given(st.text(alphabet="%0123456789abcdefABCDEF/.", max_size=60))
    def test_percent_escapes(self, value):
        """Aimed straight at the one scheme that decodes bytes out of its input."""
        assert self.writable(value)
        assert self.writable("example.com/x/" + value + ".Foo")

    def test_every_corpus_entry_is_writable(self):
        for name in CORPUS:
            demangle.demangle(name).encode("utf-8")


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
            # `_by_first` too, or the per-first-character screen keeps handing out
            # tuples that still hold this plugin after it has been unregistered --
            # `candidates()` and `available()` then disagree, and whichever test runs
            # next and asks both fails for a reason that has nothing to do with it.
            registry._by_first = None
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


class TestTheCursorNeverPassesTheEndOfInput:
    """`eof` is `pos >= length`, so an overshoot *satisfies* the all-input-consumed check.

    That is what made the block-invoke window bug silent rather than loud. The window
    shortens `reader.length` so a nested encoding stops before the literal that bounds
    it; `expect`, `eat`, `startswith`, `peek2` and `remaining` indexed the string instead
    of asking `length`, so a truncated encoding consumed a character past the end and
    then passed `if not reader.eof` because it had gone *further* than the end rather
    than not far enough.

    The invariant is stronger than that one bug and is checked as such: whatever the
    input, `pos` never exceeds `length`. It is asserted by watching every write to `pos`,
    which is the only way an overshoot can happen, so a new production cannot reintroduce
    one anywhere. Over the corpus and the same names truncated inside a window, this
    reported 633 violations before the fix and none after.
    """

    @staticmethod
    def _watching():
        """A `Reader` that records every write leaving the cursor past the end."""
        from demangle.core import reader as reader_module

        violations = []

        class Watched(reader_module.Reader):
            __slots__ = ()

            def __setattr__(self, name, value):
                object.__setattr__(self, name, value)
                if name == "pos" and value > getattr(self, "length", value):
                    violations.append((self.text, value, self.length))

        return Watched, violations

    def test_no_production_leaves_the_cursor_past_the_end(self):
        from demangle.schemes.itanium import parser as itanium_parser

        watched, violations = self._watching()
        names = corpus_sample(37)
        # The shape that had the bug: a real encoding cut short inside the window that
        # `___Z..._block_invoke` installs, so the literal sits where the rest would be.
        for name in list(names):
            if name.startswith("_Z"):
                body = name[2:]
                for cut in (1, len(body) // 2, len(body) - 1):
                    if cut > 0:
                        names.append(f"___Z{body[:cut]}_block_invoke")

        original = itanium_parser.Reader
        itanium_parser.Reader = watched
        try:
            for name in names:
                with contextlib.suppress(Exception):
                    demangle.demangle_strict(name, language="itanium")
        finally:
            itanium_parser.Reader = original

        assert not violations, f"{len(violations)} overshoots, first five: {violations[:5]}"


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

    def test_a_nested_name_cannot_buy_a_second_of_work_with_sixteen_kilobytes(self):
        """Every `<prefix>` is a substitution candidate, so N components record N
        entries -- and each entry is the whole prefix, so their sizes sum to O(N^2).

        No single entry exceeds `max_output`, which is why that bound never fired.
        `_ZN` and 8,190 components of `1a` is 16KB of input that read in a second and
        allocated 98MB. It is refused in twenty milliseconds and about a megabyte now,
        and the ceiling does not move as the input grows.

        Not a spelling change: all 217,730 distinct Itanium symbols in the shared
        libraries of a stock Ubuntu 24.04 demangle to exactly what they did, and the
        worst of them records 10,209 characters against a budget of 1,048,576.
        """
        for components in (1000, 8190, 32000):
            name = "_ZN" + "1a" * components + "E"
            assert demangle.demangle(name) == name
            with pytest.raises(LimitExceeded):
                demangle.demangle_strict(name)
        # Short ones are unaffected, and a caller who trusts the input can raise it.
        assert demangle.demangle_strict("_ZN" + "1a" * 100 + "E") == "::".join(["a"] * 100)
        relaxed = Limits(max_output=1 << 22)
        assert demangle.demangle_strict("_ZN" + "1a" * 1000 + "E", limits=relaxed) == "::".join(["a"] * 1000)

    def test_truncation_at_every_offset_of_every_corpus_is_answered(self, subtests):
        """Every scheme, on real names, cut short at every offset.

        Sampled rather than exhaustive -- one name in every 150 across all the
        conformance corpora, which is a few hundred names and some tens of thousands of
        prefixes. A truncated name is the shape that found the one non-advancing loop
        this package has had (`Demangler.next_char`, see `tests/test_swift.py`): it ends
        in the middle of a production, which is exactly where a parser is most likely to
        put a character back that it never took.
        """
        sampled = corpus_sample(150)
        assert len(sampled) > 200, "corpora did not load; this test would prove nothing"
        for full in sampled:
            with subtests.test(name=full):
                for cut in range(len(full)):
                    assert answered(full[:cut])

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
