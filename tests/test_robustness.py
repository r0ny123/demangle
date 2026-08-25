"""Malformed, truncated and adversarial input.

A mangled name is attacker-controlled in any tool that opens files it did not produce.
These tests assert the properties that make the library safe to point at a hostile
binary: it terminates, it stays inside its limits, and it never raises out of the
best-effort entry point.
"""

import pytest

import demangle
from demangle.core.errors import DemanglingError
from demangle.core.limits import Limits

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

MANGLING_ALPHABET = "_ZNSKPRIEJLTUvbcahstijlmxynofdeg0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ?@$."

deadline = settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@deadline
@given(st.text(max_size=200))
def test_demangle_never_raises_on_arbitrary_text(value):
    assert isinstance(demangle.demangle(value), str)


@deadline
@given(st.text(alphabet=MANGLING_ALPHABET, max_size=200))
def test_demangle_never_raises_on_mangling_shaped_text(value):
    """Text drawn from the alphabet the schemes use reaches far deeper into the parsers."""
    assert isinstance(demangle.demangle(value), str)


@deadline
@given(st.binary(max_size=200))
def test_demangle_never_raises_on_decoded_bytes(payload):
    assert isinstance(demangle.demangle(payload.decode("latin-1")), str)


@deadline
@given(st.text(alphabet=MANGLING_ALPHABET, max_size=120))
def test_strict_only_raises_demangling_errors(value):
    """Every failure is reportable as this library's own error type."""
    try:
        demangle.demangle_strict(value)
    except DemanglingError:
        pass
    except RecursionError:
        pytest.fail(f"RecursionError escaped for {value!r}; limits should have caught it")


@deadline
@given(st.text(alphabet=MANGLING_ALPHABET, max_size=120))
def test_result_is_deterministic(value):
    assert demangle.demangle(value) == demangle.demangle(value)


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
    ],
    ids=["pointers", "nested", "templates", "prefixes", "substitutions", "msvc", "rust"],
)
def test_pathological_input_terminates(value):
    """Each of these is a few kilobytes describing something enormous."""
    assert isinstance(demangle.demangle(value), str)


def test_truncation_at_every_offset_is_safe():
    """Symbol tables really do hold names cut short by fixed-width fields."""
    full = "_ZNSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEE6appendEPKc"
    for cut in range(len(full)):
        assert isinstance(demangle.demangle(full[:cut]), str)


def test_output_limit_is_enforced():
    """Template expansion can amplify a short name into a very long spelling."""
    tiny = Limits(max_output=16)
    result = demangle.demangle("_ZNSt6vectorIiSaIiEE9push_backERKi", limits=tiny)
    assert result == "_ZNSt6vectorIiSaIiEE9push_backERKi"


def test_depth_limit_is_enforced():
    shallow = Limits(max_depth=4)
    deep = "_Z1f" + "P" * 50 + "i"
    assert demangle.demangle(deep, limits=shallow) == deep


def test_relaxed_limits_still_terminate():
    assert isinstance(demangle.demangle("_Z" + "P" * 3000 + "i", limits=demangle.RELAXED_LIMITS), str)
