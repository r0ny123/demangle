"""Unit tests for the shared machinery.

`core` is what every scheme is built on, so its pieces are tested directly rather than
only through whole-name demangling, where a bug would show up as a puzzling spelling
several layers away.
"""

import pytest

from demangle.core.cache import MISSING, BoundedCache
from demangle.core.errors import ParseError, TruncatedError
from demangle.core.reader import Reader
from demangle.core.spelling import SPELLING_BUILDER as B


class TestReader:
    def test_peek_past_the_end_is_empty_not_an_error(self):
        reader = Reader("ab")
        reader.pos = 2
        assert reader.peek() == ""
        assert reader.peek(10) == ""

    def test_take_past_the_end_raises_truncated(self):
        reader = Reader("")
        with pytest.raises(TruncatedError):
            reader.take()

    def test_eat_reports_without_consuming_on_failure(self):
        reader = Reader("abc")
        assert reader.eat("x") is False
        assert reader.pos == 0
        assert reader.eat("ab") is True
        assert reader.pos == 2

    @pytest.mark.parametrize("text,expected", [("_", 0), ("0_", 1), ("1_", 2), ("Z_", 36), ("10_", 37)])
    def test_seq_id_is_base_36_offset_by_one(self, text, expected):
        """`S_` is entry 0 and `S0_` is entry 1: the empty sequence has to mean something."""
        assert Reader(text).seq_id() == expected

    def test_number_handles_the_negative_marker(self):
        assert Reader("n42").number() == "-42"
        assert Reader("42").number() == "42"

    def test_expect_reports_the_offset(self):
        reader = Reader("abc")
        with pytest.raises(ParseError) as info:
            reader.expect("z")
        assert info.value.position == 0


class TestDeclaratorPlacement:
    """The part of a demangler most often subtly wrong."""

    def test_plain_type(self):
        assert B.spell(B.builtin("int"), "x") == "int x"

    def test_pointer_binds_tightly(self):
        assert B.spell(B.pointer(B.builtin("int"))) == "int*"

    def test_function_pointer_needs_grouping(self):
        # Without the parentheses this reads as a function returning a pointer.
        function = B.function(B.builtin("int"), [B.builtin("char")])
        assert B.spell(B.pointer(function)) == "int (*)(char)"

    def test_named_function_pointer_puts_the_name_in_the_hole(self):
        function = B.function(B.builtin("int"), [B.builtin("char")])
        assert B.spell(B.pointer(function), "f") == "int (*f)(char)"

    def test_pointer_to_array(self):
        assert B.spell(B.pointer(B.array(B.builtin("int"), "10"))) == "int (*) [10]"

    def test_array_of_pointers(self):
        assert B.spell(B.array(B.pointer(B.builtin("int")), "10")) == "int* [10]"

    def test_qualifiers_are_postfix(self):
        assert B.spell(B.qualify(B.builtin("int"), ["const"])) == "int const"
        assert B.spell(B.pointer(B.qualify(B.builtin("int"), ["const"]))) == "int const*"
        assert B.spell(B.qualify(B.pointer(B.builtin("int")), ["const"])) == "int* const"

    def test_member_pointer_spacing(self):
        assert B.spell(B.member_pointer(B.name("Foo"), B.builtin("int"))) == "int Foo::*"

    def test_member_function_pointer(self):
        function = B.function(B.builtin("int"), [])
        assert B.spell(B.member_pointer(B.name("Foo"), function)) == "int (Foo::*)()"

    @pytest.mark.parametrize(
        "outer,inner,expected",
        [
            ("reference", "reference", "int&"),
            ("reference", "rvalue_reference", "int&"),
            ("rvalue_reference", "reference", "int&"),
            ("rvalue_reference", "rvalue_reference", "int&&"),
        ],
    )
    def test_reference_collapsing(self, outer, inner, expected):
        """[dcl.ref]: only rvalue-to-rvalue stays an rvalue reference."""
        built = getattr(B, outer)(getattr(B, inner)(B.builtin("int")))
        assert B.spell(built) == expected


class TestBoundedCache:
    def test_reports_a_miss_distinctly_from_a_stored_none(self):
        cache = BoundedCache()
        cache.put("key", None)
        assert cache.get("key") is None
        assert cache.get("absent") is MISSING

    def test_clears_wholesale_at_the_high_water_mark(self):
        cache = BoundedCache(max_size=4)
        for index in range(5):
            cache.put(index, index)
        assert len(cache) == 1

    def test_tracks_hit_rate(self):
        cache = BoundedCache()
        cache.put("k", 1)
        cache.get("k")
        cache.get("absent")
        assert cache.stats["hits"] == 1
        assert cache.stats["misses"] == 1


class TestSubstitutionTable:
    def test_rejects_a_production_the_abi_does_not_call_a_candidate(self):
        """A parser recording the wrong thing shifts every later index; fail loudly."""
        from demangle.schemes.itanium.substitutions import SubstitutionTable

        table = SubstitutionTable("_Z1fv")
        with pytest.raises(AssertionError, match="not a substitution candidate"):
            table.remember(object(), "operator-name")

    def test_reports_an_out_of_range_reference_clearly(self):
        from demangle.schemes.itanium.substitutions import SubstitutionTable

        table = SubstitutionTable("_Z1fv")
        with pytest.raises(ParseError, match="refers past"):
            table.lookup(3)
