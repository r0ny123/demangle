"""D mangled names.

100% against GNU binutils' D demangler (`c++filt --format=dlang`) over every symbol it
can read in the shipped `libgphobos` and `libgdruntime` -- 16,333 of 19,315 -- with
nothing mis-spelled and nothing refused among them, and no name anywhere raising anything
but `DemangleFailure`.

The grammar rules that had to be *measured* rather than read off the specification are
pinned below, each with the name that settled it. Every one of them was wrong on the
first reading.
"""

import contextlib
import pathlib

import pytest

import demangle
from demangle.schemes.d._parser import DemangleFailure, parse_d_symbol

from .conftest import load_corpus

CORPUS = pathlib.Path(__file__).parent / "conformance" / "d-real-world.txt"


def corpus():
    rows = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "\t" in line:
            rows.append(tuple(line.split("\t", 1)))
    return rows


ROWS = corpus()


class TestConformance:
    def test_the_corpus_is_not_empty(self):
        assert len(ROWS) > 1000

    def test_every_recorded_name_still_reads_exactly(self, subtests):
        for mangled, expected in ROWS:
            with subtests.test(name=mangled):
                assert parse_d_symbol(mangled).text == expected


class TestGrammar:
    """Constructs settled against the reference or the ABI, each with the name that proved it."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # Compiled with gdc 13.3 for this test; checked against c++filt --format=dlang.
            ("_D5mypkg5mymod5Point4normMFZi", "mypkg.mymod.Point.norm()"),
            ("_D5mypkg5mymod6Widget3getMxFZi", "mypkg.mymod.Widget.get() const"),
            ("_D5mypkg5mymod10takesArrayFAiZv", "mypkg.mymod.takesArray(int[])"),
            ("_D5mypkg5mymod12takesPointerFPiZv", "mypkg.mymod.takesPointer(int*)"),
            # Generated symbols name a thing *about* the rest of the path.
            ("_D5mypkg5mymod6Widget6__vtblZ", "vtable for mypkg.mymod.Widget"),
            ("_D5mypkg5mymod6Widget7__ClassZ", "ClassInfo for mypkg.mymod.Widget"),
            ("_D5mypkg5mymod12__ModuleInfoZ", "ModuleInfo for mypkg.mymod"),
            ("_D5mypkg5mymod5Iface11__InterfaceZ", "Interface for mypkg.mymod.Iface"),
            # A template instance, and the back reference that follows it.
            (
                "_D2rt4util7utility__T8_ComplexTdZQm6__initZ",
                "initializer for rt.util.utility._Complex!(double)._Complex",
            ),
        ],
    )
    def test_spelling(self, mangled, expected):
        assert parse_d_symbol(mangled).text == expected

    def test_back_reference_digits_are_zero_based(self):
        """`Qm` is a distance of 12, not 13.

        Measured rather than read off the spec's wording: in the name below the `Q` sits
        at offset 33 and must resolve to the `8_Complex` at offset 21.
        """
        name = "_D2rt4util7utility__T8_ComplexTdZQm6__initZ"
        assert name.index("Qm") - name.index("8_Complex") == 12
        assert "_Complex!(double)" in parse_d_symbol(name).text


class TestSafety:
    """True regardless of how much of the grammar is modelled."""

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "_D",
            "_D0",
            "_D999999999999x",
            "_DQ",
            "_DQa",
            "_D1a" + "Q" * 200,
            "_D" + "1a" * 500,
            "not a d name",
            "\x00\x01",
            "_D1aQzzzzzzzzz",
        ],
    )
    def test_a_malformed_name_is_refused_rather_than_answered(self, value):
        with pytest.raises(DemangleFailure):
            parse_d_symbol(value)

    def test_detect_takes_only_an_ascii_digit_like_the_parser(self):
        """`str.isdigit` is Unicode-aware, so `_D²foo` was claimed and then refused."""
        from demangle.schemes.d import detect

        assert detect("_D1a") is True
        assert detect("_D²foo") is False
        with pytest.raises(DemangleFailure):
            parse_d_symbol("_D²foo")

    @pytest.mark.parametrize(
        "value",
        [
            "_D3fooC",  # a class type with no qualified name after it
            "_D3fooS",  # a struct
            "_D3fooE",  # an enum
            "_D3fooT",  # a typedef
            "_D3fooPC",  # and behind a pointer
            "_D3fooFCZv",  # and as a parameter, where it took the parameter with it
            "_D3C33C",
        ],
    )
    def test_a_named_type_with_no_name_is_refused(self, value):
        """`C <QualifiedName>` and its three siblings, where the name is not optional.

        `dlang_parse_qualified` reads at least one symbol name and fails otherwise. This
        joined an empty list and returned `""`, so `_D3fooC` -- a variable whose type is
        a class with no name -- came back as `foo`, and `_D3fooFCZv` as `foo()` with the
        parameter simply gone. `c++filt --format=dlang` (binutils 2.42) hands back every
        one of these.

        Found by enumerating every `_D` name up to eight characters over a
        grammar-shaped alphabet: 52,052 of the 260,260 this read were names the
        reference refuses, and they were all this.
        """
        with pytest.raises(DemangleFailure):
            parse_d_symbol(value)

    @pytest.mark.parametrize(
        "value",
        [
            "_D4testFMMfZv",  # `scope` twice
            "_D4testFIIfZv",  # `in` twice
            "_D4testFKKfZv",
            "_D4testFLLfZv",
            "_D4testFNkNkfZv",
            "_D4testFIJfZv",  # two of the four that are mutually exclusive
            "_D4testFKIfZv",  # and out of order
            "_D4testFNkMMfZv",  # `return scope` and then `scope` again
            "_D4testFNkMNkfZv",  # `return scope` and then `return` again
            "_D4testFIKKfZv",  # `in ref` with a second `ref`
        ],
    )
    def test_a_parameter_reads_its_storage_classes_in_order_and_once_each(self, value):
        """`[NkM | [M] [Nk]] [I[K] | J | K | L] <Type>` is a sequence, not a set.

        `dlang_function_args` reads each of these once and in this order and then reads
        the type. Written as a loop here, it took any order and any number: `FMMfZv`
        came back as `(scope scope float)` and `FIJfZv` as `(in out float)`, neither of
        which is a parameter anything can declare. `c++filt --format=dlang` (binutils
        2.42) hands every one of these back.
        """
        with pytest.raises(DemangleFailure):
            parse_d_symbol(value)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("_D4testFNkMfZv", "test(return scope float)"),
            ("_D4testFNkMJfZv", "test(return scope out float)"),
            ("_D4testFNkMKfZv", "test(return scope ref float)"),
            # A real one: `rt.lifetime.__arrayStart` in the LDC 1.40 runtime.
            (
                "_D2rt8lifetime12__arrayStartFNaNbNkMS4core6memory8BlkInfo_ZPv",
                "rt.lifetime.__arrayStart(return scope core.memory.BlkInfo_)",
            ),
        ],
    )
    def test_return_scope_written_return_first(self, value, expected):
        """`NkM`: `return scope`, in that order. DMD 2.104 began writing `Nk` ahead of
        the `M`, libiberty reads `M` then `Nk` and refuses this, and 766 of the LDC 1.40
        runtime's 16,197 symbols carry it. D's own `core.demangle` reads both orders and
        spells this one `return scope`; every one of those 766 reads here the way it
        spells them."""
        assert parse_d_symbol(value).text == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("_D4testFMfZv", "test(scope float)"),
            ("_D4testFIKfZv", "test(in ref float)"),  # the one pair the reference spells
            ("_D4testFMKfZv", "test(scope ref float)"),
            ("_D4testFMNkIfZv", "test(scope return in float)"),
            ("_D4testFMNkKfZv", "test(scope return ref float)"),
            ("_D4testFJfZv", "test(out float)"),
            ("_D4testFLfZv", "test(lazy float)"),
            ("_D4testFZv", "test()"),
        ],
    )
    def test_the_orders_a_parameter_may_be_written_in(self, value, expected):
        assert parse_d_symbol(value).text == expected

    @pytest.mark.parametrize(
        "value",
        ["_D4test3fooMf", "_D4test3fooMxf", "_D4test3fooMC3bar", "_D4test3C33Mf"],
    )
    def test_a_this_parameter_needs_a_function_after_it(self, value):
        """`M` is a member function's `this`, so a function type has to follow it.

        `dlang_parse_mangle` sets `is_function` on seeing `M` and then calls
        `dlang_function_type`, which fails without a calling convention. This read a
        plain type instead and dropped the `M`, the modifiers and the type with it, so
        `_D4test3fooMf` came back as `test.foo` -- a variable, out of a symbol that says
        it is a member function. The reference hands all of these back.
        """
        with pytest.raises(DemangleFailure):
            parse_d_symbol(value)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            # `M` with a function after it, which is the shape it exists for.
            ("_D4test3fooMFiZv", "test.foo(int)"),
            ("_D4test3fooMxFiZv", "test.foo(int) const"),
            # The check is on how far the cursor moved, not on what came out: a
            # zero-length component is anonymous and spells nothing, and the reference
            # reads this one.
            ("_D3fooC0", "foo"),
            ("_D3fooC3bar", "foo"),
            ("_D3fooFC3barZv", "foo(bar)"),
            # A complete type that is not a class still spells only the path.
            ("_D3fooi", "foo"),
        ],
    )
    def test_what_it_still_reads(self, value, expected):
        assert parse_d_symbol(value).text == expected

    def test_nothing_escapes_as_another_exception(self):
        """Only `DemangleFailure` may come out; anything else would escape `demangle()`."""
        for mangled, _ in ROWS[:400]:
            for cut in (len(mangled) // 2, len(mangled) - 1):
                with contextlib.suppress(DemangleFailure):
                    parse_d_symbol(mangled[:cut])


class TestRulesThatHadToBeMeasured:
    """Each of these was wrong on the first reading of the specification."""

    def test_a_scope_function_has_no_return_type(self):
        """The grammar gives a scope `TypeFunctionNoReturn`.

        Reading a return type anyway swallowed the following path component, so a symbol
        declared inside a function lost its own name.
        """
        name = "_D2rt3aaA11fakeEntryTIFNbKSQzQy4ImplxC8TypeInfoxQlZ13tiMangledNameyAa"
        assert parse_d_symbol(name).text.endswith(".tiMangledName")

    def test_a_scope_spells_its_parameters(self):
        name = "_D2rt3aaA11fakeEntryTIFNbKSQzQy4ImplxC8TypeInfoxQlZ13tiMangledNameyAa"
        assert "fakeEntryTI(ref rt.aaA.Impl, const(TypeInfo), const(TypeInfo))" in parse_d_symbol(name).text

    def test_q_is_ambiguous_between_an_identifier_and_a_type(self):
        """The trailing `Qq` here is the *return type*, not another path component.

        Testing only whether the byte could open a name made it look like one and took
        the rest of the symbol with it.
        """
        name = "_D2rt6config13rt_linkOptionFNbNiAyaMDFNbNiQkZQnZQq"
        assert parse_d_symbol(name).text.startswith("rt.config.rt_linkOption(")

    def test_a_value_may_have_more_digits_than_a_length_prefix_may(self):
        """`Vmi3988292384` is a perfectly ordinary `ulong`; a 10-digit *length* is not."""
        name = "_D3std6digest3crc__T3CRCVki32Vmi3988292384ZQx3putMFNaNbNiNeAxhXv"
        assert "CRC!(32u, 3988292384uL)" in parse_d_symbol(name).text

    def test_a_symbol_argument_is_a_whole_mangled_name(self):
        """`S_D...` carries a path *and* its type, and the type has to be consumed."""
        name = "_D3std11concurrency__T8initOnceS_DQBg3net4curl7CurlAPI7_handlePvZQBrFNcLQkZQn"
        assert (
            parse_d_symbol(name).text == "std.concurrency.initOnce!(std.net.curl.CurlAPI._handle).initOnce(lazy void*)"
        )

    def test_variadic_x_takes_no_separator(self):
        """`f(T t...)` for `X`, `f(T t, ...)` for `Y` -- the difference is the comma."""
        name = "_D3std6digest3crc__T3CRCVki32Vmi3988292384ZQx3putMFNaNbNiNeAxhXv"
        assert parse_d_symbol(name).text.endswith("(const(ubyte)[]...)")

    def test_an_array_literal_drops_the_element_suffix(self):
        """`[104, 1281]`, not `[104uL, 1281uL]`, though each element is a `ulong`."""
        name = "_D6object__T10RTInfoImplVAmA2i104i1281ZQBbyG2m"
        assert parse_d_symbol(name).text == "object.RTInfoImpl!([104, 1281]).RTInfoImpl"

    def test_postblit_is_renamed_only_when_the_function_has_no_attributes(self):
        """A reference quirk, not a distinction the language makes.

        Followed exactly rather than approximated in either direction, because the
        corpus is what this library is measured against.
        """
        assert parse_d_symbol("_D3foo3Bar10__postblitMFZv").text == "foo.Bar.this(this)"
        assert parse_d_symbol("_D3foo3Bar10__postblitMFNaNbNiNfZv").text == "foo.Bar.__postblit()"


class TestSpellingsOnlyTheReferenceCouldSettle:
    """Rules with no counterpart in the D ABI: each was derived from the reference.

    `c++filt --format=dlang` was run over the input space rather than read from -- every
    byte value through a string literal, every character type through a literal, the
    boundaries of what it will accept -- because these are the demangler's own choices
    and the specification says nothing about any of them.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # Five named escapes and no more: `\a` and `\b` are *not* among them.
            ("_D8demangle32__T4testVAyaa8_20090a0d0c0b00ffZv", 'demangle.test!(" \\t\\n\\r\\f\\v\\x00\\xff")'),
            ("_D8demangle18__T4testVAyaa1_07Zv", 'demangle.test!("\\x07")'),
            # Neither a quote nor a backslash is escaped inside a string.
            ("_D8demangle18__T4testVAyaa1_22Zv", 'demangle.test!(""")'),
            ("_D8demangle18__T4testVAyaa1_5cZv", 'demangle.test!("\\")'),
            # Printable ASCII stands as itself; 0x7f and everything above does not.
            ("_D8demangle18__T4testVAyaa1_7eZv", 'demangle.test!("~")'),
            ("_D8demangle18__T4testVAyaa1_7fZv", 'demangle.test!("\\x7f")'),
        ],
    )
    def test_string_escapes(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # A character literal names none of the escapes a string names.
            ("_D8demangle14__T4testVai10Zv", "demangle.test!('\\x0a')"),
            ("_D8demangle14__T4testVai32Zv", "demangle.test!(' ')"),
            # Not even a quote or a backslash, which is the reference's own oddity.
            ("_D8demangle14__T4testVai39Zv", "demangle.test!(''')"),
            ("_D8demangle14__T4testVai92Zv", "demangle.test!('\\')"),
            # Only a `char` is ever written as itself; a printable `wchar` is not.
            ("_D8demangle14__T4testVui10Zv", "demangle.test!('\\u000a')"),
            ("_D8demangle16__T4testVui1000Zv", "demangle.test!('\\u03e8')"),
            ("_D8demangle18__T4testVwi100000Zv", "demangle.test!('\\U000186a0')"),
            # The width is a minimum, not a cap.
            ("_D8demangle15__T4testVai256Zv", "demangle.test!('\\x100')"),
        ],
    )
    def test_character_literals(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    def test_a_character_wider_than_thirty_two_bits_is_refused(self):
        """The reference refuses it, and answers the same value as an *integer*."""
        name = "_D4test21__T3funVwi4294967296Z3funFNaNbNiNfZv"
        assert demangle.demangle(name, language="d") == name
        wide = "_D8demangle32__T4testVmi18446744073709551616Zv"
        assert "18446744073709551616uL" in demangle.demangle(wide, language="d")

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # The point goes after the first digit, wherever the digits start.
            ("_D8demangle17__T4testVde0A8P6Zv", "demangle.test!(0x0.A8p6)"),
            ("_D8demangle16__T4testVdeA8P2Zv", "demangle.test!(0xA.8p2)"),
            ("_D8demangle18__T4testVdeN0A8P6Zv", "demangle.test!(-0x0.A8p6)"),
            ("_D8demangle19__T4testVfe08PN125Zv", "demangle.test!(0x0.8p-125)"),
            ("_D8demangle15__T4testVdeNANZv", "demangle.test!(NaN)"),
            ("_D8demangle15__T4testVdeINFZv", "demangle.test!(Inf)"),
            ("_D8demangle16__T4testVdeNINFZv", "demangle.test!(-Inf)"),
        ],
    )
    def test_real_literals(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    def test_a_complex_literal_is_two_reals(self):
        name = "_D8demangle51__T4testVrc0C4CCCCCCCCCCCCCDP4c0B666666666666666P6Zv"
        expected = "demangle.test!(0x0.C4CCCCCCCCCCCCCDp4+0x0.B666666666666666p6i)"
        assert demangle.demangle(name, language="d") == expected

    def test_a_struct_literal_names_its_type(self):
        name = "_D8demangle28__T4testVS8demangle1SS2i1i2Zv"
        assert demangle.demangle(name, language="d") == "demangle.test!(demangle.S(1, 2))"

    def test_a_struct_field_carries_no_type_of_its_own(self):
        """So a field mangled as an integer is never spelled as a character or a bool."""
        name = "_D8demangle35__T4testVS8demangle1SS2i1a3_616263Zv"
        assert demangle.demangle(name, language="d") == 'demangle.test!(demangle.S(1, "abc"))'

    def test_an_associative_array_value_is_written_as_pairs(self):
        name = "_D8demangle23__T4testVHiiA2i1i2i3i4Zv"
        assert demangle.demangle(name, language="d") == "demangle.test!([1:2, 3:4])"

    def test_a_back_referenced_type_is_followed_to_find_its_shape(self):
        """`[0:"c", 2:"a"]` and `[0, "c", 2, "a"]` differ, and only the type says which.

        Written `QFh`, the type says nothing until it is followed -- and the whole name
        was refused, because the pairs were read as a flat list and the count ran out.
        """
        name = (
            "_D3std9algorithm9iteration__T12FilterResultSQBq8typecons__T5TupleTiVAyaa1_61TiVQla1_62TiVQva1_63ZQBm"
            "__T6renameVHiQBtA2i0a1_63i2a1_61ZQBeMFNcZ9__lambda1TAiZQEw9__xtoHashFNbNeKxSQGsQGrQGk__TQGdSQHiQFs"
            "__TQFmTiVQFja1_61TiVQFua1_62TiVQGfa1_63ZQGx__TQFlVQFhA2i0a1_63i2a1_61ZQGjMFNcZQFfTQEyZQJvZm"
        )
        assert 'rename!([0:"c", 2:"a"])' in demangle.demangle(name, language="d")

    def test_a_function_literal_reaches_a_struct_field_as_a_whole_symbol(self):
        name = "_D6mangle__T8fun21753VSQv6S21753S1f_DQBj10__lambda71MFNaNbNiNfZvZQCbQp"
        expected = "mangle.fun21753!(mangle.S21753(mangle.__lambda71())).fun21753"
        assert demangle.demangle(name, language="d") == expected

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # The marker is the literal's *suffix*, not its encoding: all three read one
            # byte a character.
            ("_D8demangle22__T4testVAyaa3_616263Zv", 'demangle.test!("abc")'),
            ("_D8demangle22__T4testVAyaw3_616263Zv", 'demangle.test!("abc"w)'),
            ("_D8demangle22__T4testVAyad3_616263Zv", 'demangle.test!("abc"d)'),
        ],
    )
    def test_string_literal_markers(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    def test_u_is_not_a_string_literal_marker(self):
        name = "_D8demangle22__T4testVAyau3_616263Zv"
        assert demangle.demangle(name, language="d") == name

    def test_extern_pascal(self):
        name = "_D8demangle4testFVZaZv"
        assert demangle.demangle(name, language="d") == "demangle.test(extern(Pascal) char() function)"

    def test_a_delegates_own_qualifier_goes_after_the_word(self):
        """Its *attributes* belong to the function it wraps and go before."""
        assert demangle.demangle("_D8demangle4testFDxFZaZv", language="d") == "demangle.test(char() delegate const)"
        assert (
            demangle.demangle("_D8demangle4testFDxFNaZaZv", language="d") == "demangle.test(char() pure delegate const)"
        )

    def test_an_array_bound_is_a_value_and_not_a_length_prefix(self):
        """Ten digits reach no further into the name than two do."""
        name = "_D8demangle4testFG1234567890aZv"
        assert demangle.demangle(name, language="d") == "demangle.test(char[1234567890])"

    def test_a_numeric_literal_is_still_bounded(self):
        """The interpreter refuses to convert a digit string this long, and its refusal
        is not this parser saying the name is malformed."""
        assert demangle.demangle("_D8demangle4testFG" + "9" * 5000 + "aZv", language="d").startswith("_D")

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_D8demangle004testFaZv", "demangle.test(char)"),
            ("_D8demangle9anonymous0Z", "demangle.anonymous"),
            ("_D8demangle4mainFZ4__S11xi", "demangle.main().x"),
            ("_D3mod4funcFZ__T6nestedTiZ4__S1QpMFNaNbNiNfZi", "mod.func().nested!(int).nested()"),
        ],
    )
    def test_anonymous_and_compiler_scope_components_are_left_out(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    def test_a_name_that_only_looks_like_a_compiler_scope_is_kept(self):
        """`__S` alone and `__S1a` are ordinary names; only `__S<digits>` is dropped."""
        assert demangle.demangle("_D8demangle4mainFZ5__S1a1xi", language="d") == "demangle.main().__S1a.x"
        assert demangle.demangle("_D8demangle4mainFZ3__S1xi", language="d") == "demangle.main().__S.x"

    def test_a_malformed_template_instance_is_refused_not_printed_back(self):
        """Printing the mangling back inside a path answers with something the symbol
        does not say, and the reference refuses the whole name."""
        for name in ("_D10__T4testYZv", "_D12__T4testViiZv", "_D15__T4testVfe0p1Zv"):
            assert demangle.demangle(name, language="d") == name


class TestWhatMutatingRealSymbolsFound:
    """Five readings that only a name *near* a real one reaches.

    `tools/enumerate.py` counts short strings, which is the wrong length for anything
    that needs a symbol a compiler actually emitted -- a pointer stacked on a function
    pointer, a template argument list several deep. `tools/mutate.py` damages the
    checked-in corpora instead: one character deleted, duplicated, transposed or
    swapped, or the head of one symbol spliced onto the tail of another. Each of these
    came out of that, and each is checked against `c++filt --format=dlang`.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # The `P` that *is* the word `function` absorbs; the ones above it do not.
            # `dlang_type` decides that from the character after the `P` -- a calling
            # convention, and nothing else -- and deciding it from the pointee's
            # *spelling* swallowed every level: `PPUZi` and `PPPUZi` both came back as
            # `PUZi`, so a pointer to a function pointer was spelled as the pointer.
            ("_D3foo3barFUZiZv", "foo.bar(extern(C) int() function)"),
            ("_D3foo3barFPUZiZv", "foo.bar(extern(C) int() function)"),
            ("_D3foo3barFPPUZiZv", "foo.bar(extern(C) int() function*)"),
            ("_D3foo3barFPPPUZiZv", "foo.bar(extern(C) int() function**)"),
            ("_D3foo3barFPPiZv", "foo.bar(int**)"),
        ],
    )
    def test_only_the_first_pointer_is_the_word_function(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # `dlang_parse_integer` appends the characters it read, so a leading zero is
            # part of the literal. Formatting the value instead spelled `24u` for `024u`.
            ("_D3foo__T3barVki024Z3bazFZv", "foo.bar!(024u).baz()"),
            ("_D3foo__T3barVmi007Z3bazFZv", "foo.bar!(007uL).baz()"),
            ("_D3foo__T3barVii00Z3bazFZv", "foo.bar!(00).baz()"),
            ("_D3foo__T3barVAmA2i01i02Z3bazFZv", "foo.bar!([01, 02]).baz()"),
            # The same rule for the two hex digits of an unprintable character in a
            # string: the reference copies them out of the name, so their case survives.
            ("_D3foo__T3barVAyaa1_B2Z3bazFZv", 'foo.bar!("\\xB2").baz()'),
            ("_D3foo__T3barVAyaa1_b2Z3bazFZv", 'foo.bar!("\\xb2").baz()'),
        ],
    )
    def test_a_literal_is_written_with_the_digits_the_name_carried(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_D3foo__T3barViN13Z3bazFZv", "foo.bar!(-13).baz()"),
            # The two kinds that spell their *value* rather than their digits used to
            # drop the sign with the digits, and `-'\x11'` came back as `'\x11'` -- the
            # positive literal, not an unspellable one.
            ("_D3foo__T3barVaN17Z3bazFZv", "foo.bar!(-'\\x11').baz()"),
            ("_D3foo__T3barVuN1000Z3bazFZv", "foo.bar!(-'\\u03e8').baz()"),
            ("_D3foo__T3barVbN1Z3bazFZv", "foo.bar!(-true).baz()"),
        ],
    )
    def test_a_negative_value_keeps_its_sign_whatever_the_kind(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    @pytest.mark.parametrize(
        "mangled",
        [
            # A bare `0` is the anonymous *scope* inside a path, where the reference
            # writes nothing for it. An argument list has no such thing: the empty
            # string took a slot and was spelled as one, so these came back
            # `foo.bar!(null, ).baz()` and `foo.bar!(, ).qux()`.
            "_D3foo__T3barVln0Z3bazFZv",
            "_D3foo__T3bar00Z3quxFZv",
        ],
    )
    def test_a_template_argument_with_no_name_is_refused(self, mangled):
        assert demangle.demangle(mangled, language="d") == mangled


class TestMoreOfWhatMutatingRealSymbolsFound:
    """Four more, from the second sitting with `tools/mutate.py`."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # `dlang_parse_mangle`: an artificial symbol ends with `Z` and has no type.
            # The `Z` is what makes it one, so a truncated `_D10TypeInfo_c6__vtbl` is not
            # `_D10TypeInfo_c6__vtblZ` with the end missing -- it is not a symbol.
            ("_D3foo6__vtblZ", "vtable for foo"),
            ("_D3foo6__vtbl", "_D3foo6__vtbl"),
            ("_D3foo7__Class", "_D3foo7__Class"),
            ("_D10TypeInfo_c6__vtbl", "_D10TypeInfo_c6__vtbl"),
            # And a component that is *called* `__vtbl` with a function type after it is
            # an ordinary function, which refusing to fall through had made unreadable.
            ("_D3foo6__vtblFZv", "foo.__vtbl()"),
        ],
    )
    def test_a_generated_symbol_is_the_one_that_ends_in_z(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # `dlang_type_modifiers`, which is not the rule a *type* follows: `O` and
            # `Ng` recurse, `x` and `y` return. So at most one of the last two appears,
            # and it comes last.
            ("_D3foo3barMxFZv", "foo.bar() const"),
            ("_D3foo3barMOxFZv", "foo.bar() shared const"),
            ("_D3foo3barMNgNgFZv", "foo.bar() inout inout"),
            ("_D3foo3barMxxFZv", "_D3foo3barMxxFZv"),
            ("_D3foo3barMxyFZv", "_D3foo3barMxyFZv"),
            ("_D3foo3barMxOFZv", "_D3foo3barMxOFZv"),
            # The same run, and the same rule, on a delegate.
            ("_D3foo3barFDOxFZvZv", "foo.bar(void() delegate shared const)"),
            ("_D3foo3barFDxxFZvZv", "_D3foo3barFDxxFZvZv"),
            ("_D3foo3barFDxOFZvZv", "_D3foo3barFDxOFZvZv"),
            # A *type's* modifiers do nest, and that is a different production: each one
            # wraps the next, so `xx` is a const const and reads.
            ("_D3foo3barFxxiZv", "foo.bar(const(const(int)))"),
            ("_D3foo3barFxyiZv", "foo.bar(const(immutable(int)))"),
        ],
    )
    def test_a_this_parameters_qualifiers_end_at_the_first_const_or_immutable(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # `dlang_parse_qualified` skips a literal `0` with a `continue`, which steps
            # over the "consume the encoded arguments" every other component goes
            # through -- so the type belongs to a component the reference left out, and
            # the reference does not spell it. Writing it after the path said the
            # component before was that function: `Mutex.unlock()` for a name whose `()`
            # is somewhere else.
            ("_D3foo3bar0FZv", "foo.bar"),
            ("_D3foo3bar0FiZv", "foo.bar"),
            ("_D4core4sync5mutex5Mutex6unlock0FNeZv", "core.sync.mutex.Mutex.unlock"),
            # Only the *last* one, and only a literal `0`: an anonymous component in the
            # middle leaves the type belonging to the component after it.
            ("_D3foo003barFZv", "foo.bar()"),
            ("_D8demangle004testFaZv", "demangle.test(char)"),
        ],
    )
    def test_an_anonymous_last_component_does_not_lend_its_type_to_the_one_before(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    @pytest.mark.parametrize(
        "mangled",
        [
            # `TemplateArgX` is `T Type`, `V Type Value`, `S Number_opt QualifiedName` or
            # `X`, and nothing else. A bare symbol name was read here as well, on the
            # grounds that a compiler emits one where the kind is unambiguous -- it does
            # not, `dlang_template_args` refuses one, and neither corpus has a name that
            # needs it.
            "_D3foo__T3bar3bazZ3quxFZv",
            "_D3foo__T3barQeZ3quxFZv",
            # An `S` argument whose qualified name spells nothing is not an argument
            # either: it took a slot and was spelled as one.
            "_D3foo__T3barS0Z3quxFZv",
        ],
    )
    def test_a_template_argument_is_one_of_the_four_the_grammar_names(self, mangled):
        assert demangle.demangle(mangled, language="d") == mangled


class TestWhatTheThirdSittingFound:
    """Four more from `tools/mutate.py`, each measured against `c++filt --format=dlang`."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # `dlang_type`'s `G` case remembers where the digit run began and appends it
            # verbatim, so a leading zero is part of the bound. Same rule as an integer
            # literal; re-formatting it wrote a different bound.
            ("_D3foo3barFG012aZv", "foo.bar(char[012])"),
            ("_D3foo3barFG12aZv", "foo.bar(char[12])"),
            ("_D8demangle4testFG02G42G42aZv", "demangle.test(char[42][42][02])"),
        ],
    )
    def test_an_array_bound_is_written_with_the_digits_the_name_carried(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # Nothing is left to name once the marker is taken off, and the reference
            # still writes the prefix. Requiring a component before it read the marker as
            # an ordinary name.
            ("_D6__initZ", "initializer for"),
            ("_D6__vtblZ", "vtable for"),
        ],
    )
    def test_a_generated_symbol_with_no_path_is_still_one(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # The reference renames `__postblit` only where the type is exactly a `this`
            # parameter and an empty D-convention signature. "No attributes" was the
            # first reading of the rule and renamed six shapes it does not.
            ("_D8demangle4test10__postblitMFZv", "demangle.test.this(this)"),
            ("_D8demangle4test10__postblitMFZi", "demangle.test.this(this)"),
            ("_D8demangle4test10__postblitFZv", "demangle.test.__postblit()"),
            ("_D8demangle4test10__postblitUZv", "demangle.test.__postblit()"),
            ("_D8demangle4test10__postblitMUZv", "demangle.test.__postblit()"),
            ("_D8demangle4test10__postblitMFiZv", "demangle.test.__postblit(int)"),
            ("_D8demangle4test10__postblitMFNaZv", "demangle.test.__postblit()"),
            ("_D8demangle4test10__postblitMxFZv", "demangle.test.__postblit() const"),
            ("_D8demangle4test10__postblitMOFZv", "demangle.test.__postblit() shared"),
        ],
    )
    def test_postblit_is_renamed_only_on_a_bare_member_signature(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    def test_a_back_reference_points_at_a_length_prefixed_identifier(self):
        """`dlang_symbol_backref` reads a number and then that many characters.

        So what a `Q` points at is an identifier and nothing else -- not a `__T` template
        instance. Reading whatever stood there resolved a mutated index onto a whole
        instance and spelled it as a path component, naming it twice: this came back
        `std.range.Chunks!(ubyte[]).Chunks!(ubyte[]).empty()`. 56 of the 119 shapes the
        mutation fuzzer had this scheme reading and the reference refusing were this one.
        """
        name = "_D3std5range__T6ChunksTAhZQo5emptyMFNaNbNdNiNfZb"
        assert demangle.demangle(name, language="d") == name
        # The index the real symbol carries points at `6Chunks`, and still reads.
        good = "_D3std5range__T6ChunksTAhZQl5emptyMFNaNbNdNiNfZb"
        assert demangle.demangle(good, language="d") == "std.range.Chunks!(ubyte[]).Chunks.empty()"


class TestAgainstLibibertysOwnCorpus:
    """The reference's vectors, not this project's.

    `d-real-world.txt` is a corpus this project assembled, and the ROADMAP's claim of
    100% against `c++filt --format=dlang` was true of it before libiberty's own
    `d-demangle-expected` had been adopted -- which is larger, and which found 149 of its
    366 vectors failing when it was.

    All 366 pass now. What closed the last of them was worth recording, because each was
    a rule that could only be *derived* from the reference rather than read out of the D
    ABI: the five characters it names inside a string (`\a` and `\b` are not among them,
    and neither `"` nor a backslash is escaped at all), the different rule for a
    character *literal*, hex float values written with the point after the first digit,
    associative-array values written as pairs where the type says so -- through a back
    reference, if that is how the type was written -- struct and function-literal values,
    `extern(Pascal)`, the anonymous and `__S<n>` path components it leaves out, and the
    malformed template instances it refuses outright rather than printing back.

    Pinned exactly, in both directions.
    """

    #: Every vector. A drop means a regression whatever the total, and this cannot rise.
    EXPECTED_EXACT = 366

    def _score(self):
        exact = 0
        for mangled, expected in load_corpus("d-libiberty.txt"):
            if demangle.demangle(mangled, language="d") == expected:
                exact += 1
        return exact

    def test_the_score_has_not_gone_backwards(self):
        exact = self._score()
        total = len(load_corpus("d-libiberty.txt"))
        assert exact >= self.EXPECTED_EXACT, f"{exact}/{total}, was {self.EXPECTED_EXACT}"

    def test_the_pinned_number_is_still_accurate(self):
        """So the pin is a fact rather than a floor nobody has looked at."""
        assert self._score() == self.EXPECTED_EXACT

    def test_every_vector_is_answered_promptly(self):
        """The corpus holds a real symbol that took over 35 seconds.

        `std.format.formattedWrite`, 2,695 characters with 441 `Q` back references in
        it. The parser was not looping -- three hundred thousand calls in all -- it was
        assembling a string far larger than any caller would accept, and `max_output`
        was checked on the finished string. A bound observed after the work is a report.
        """
        import time

        for mangled, _ in load_corpus("d-libiberty.txt"):
            started = time.perf_counter()
            demangle.demangle(mangled, language="d")
            elapsed = time.perf_counter() - started
            assert elapsed < 1.0, f"{elapsed:.1f}s for {mangled[:60]}"


class TestWhereLibibertyIsNarrowerThanTheGrammar:
    """Two shapes the D grammar admits, `c++filt --format=dlang` refuses, and this reads.

    The grammar this scheme follows, recorded at the head of `_parser.py`:

        SymbolFunctionName ::= SymbolName
                             | SymbolName TypeFunctionNoReturn
                             | SymbolName "M" TypeModifiers? TypeFunctionNoReturn
        SymbolName         ::= LName | TemplateInstanceName | IdentifierBackRef | "0"
        TemplateArgX       ::= T Type | V Type Value | S Number_opt QualifiedName | X ...

    Neither shape is one a compiler writes -- both corpora, 1,257 real symbols and
    libiberty's 366 vectors, have neither -- so both are reachable only by mutation, and
    both are recorded here rather than followed. `tools/mutate.py` carried the first for
    several sittings as "a deep chain of `Q` back references"; that was the shape of the
    mutant, not of the disagreement.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # What libiberty reads, and this reads the same way: the anonymous
            # `<SymbolName>` alone, and carrying a plain function type.
            ("_D1a0i", "a"),
            ("_D1a0FZv", "a"),
            ("_D1a1b0i", "a.b"),
            # What it refuses: the same `0`, carrying the `M` member-function form. Nine
            # characters, and the two neighbours above are what make the refusal an
            # inconsistency inside the reference rather than a rule.
            ("_D1a0MFZv", "a"),
            ("_D1a0MxFZv", "a"),
            ("_D1a1b0MFZv", "a.b"),
        ],
    )
    def test_the_anonymous_symbol_name_carries_a_member_function_type(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_D1w__T1bS__T1cZZ1xi", "w.b!(c!()).x"),
            ("_D1w__T1bS__T1cTaZZ1xi", "w.b!(c!(char)).x"),
            ("_D1w__T1bS__T1cZ1yZ1xi", "w.b!(c!().y).x"),
        ],
    )
    def test_a_symbol_argument_opening_on_a_template_instance(self, mangled, expected):
        """`S <QualifiedName>`, and a `QualifiedName` may open with a template instance.

        `dlang_template_args` has no arm for it and refuses the whole name. Kept out of
        `tools/enumerate.py`'s accept rules on purpose: the pinned draw never reaches
        this shape, and a rule that never fires is one nobody would notice going wrong.
        """
        assert demangle.demangle(mangled, language="d") == expected

    def test_the_shape_the_mutation_actually_produced(self):
        """The seed, the one-character edit, and what it turns the name into.

        `13__dgliteral10` becomes `13___dgliteral10`: the length still says 13, so it now
        covers `___dgliteral1` and the `0` that was part of the identifier is handed to
        the grammar as the anonymous `<SymbolName>` -- with the `MFNaNbNiNfZ` after it,
        which is the shape above.
        """
        mutant = "_D1a13___dgliteral10MFNaNbNiNfZAxa"
        assert demangle.demangle(mutant, language="d") == "a.___dgliteral1"
        seed = "_D1a13__dgliteral10MFNaNbNiNfZAxa"
        assert demangle.demangle(seed, language="d") == "a.__dgliteral10()"


class TestWhatAskingTheReferenceAboutRefusalsFound:
    """`tools/mutate.py --refusals`: the mutants this scheme refuses that c++filt reads.

    The gate only ever puts names this scheme *reads* to the reference, so a name it
    refused and the reference read was invisible to it. Asked the other way round over
    20,000 mutants, seventeen came back read. Two were this scheme's, and are fixed; the
    rest are libiberty reading past the grammar, and are pinned here as refusals so a
    change to either side shows up.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # `dlang_identifier` tries the template grammar only on a length-prefixed
            # identifier of five or more characters. Under that `__T` is a name.
            ("_D4main3__TFZv", "main.__T()"),
            ("_D4main4__TaFZv", "main.__Ta()"),
            # The mutant: `8demanle1`, then `3__T`, then `test` with a Pascal-convention
            # parameter list.
            ("_D8demanle13__T4testVPinZv", "demanle1.__T.test(int*, typeof(null))"),
        ],
    )
    def test_a_short_identifier_opening_on___T_is_a_name_not_a_template(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    def test_a_five_character_template_body_still_has_to_close(self):
        """Five is where the template grammar starts, and `__T1aZ` is six: with the `Z`
        outside the counted body the length mismatches, and both sides refuse."""
        name = "_D4main5__T1aZFZv"
        assert demangle.demangle(name, language="d") == name
        assert demangle.demangle("_D4main6__T1aZFZv", language="d") == "main.a!()()"

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # `_D QualifiedName Z`, where the path's last component carries its own
            # parameter list: the function's `Z` and then the artificial symbol's.
            ("_D4main3fooFZZ", "main.foo()"),
            ("_D4main3fooMFZZ", "main.foo()"),
            ("_D4main3fooFZ3barFZZ", "main.foo().bar()"),
            ("_D3std4math12trigonometry4asinFNaNbNiNfdZZ", "std.math.trigonometry.asin(double)"),
        ],
    )
    def test_a_parameter_list_may_be_followed_by_the_artificial_z(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    def test_a_parameter_list_at_the_end_of_the_name_is_still_refused(self):
        """No return type and no `Z` either: `dlang_parse_qualified` backtracks on the
        end of the name and the reference refuses it, as before."""
        name = "_D4main3fooFZ"
        assert demangle.demangle(name, language="d") == name

    @pytest.mark.parametrize(
        ("mangled", "reference"),
        [
            # `dlang_type`'s `G` arm counts digits and is content with none, so a static
            # array with no bound prints as a dynamic one.
            ("_D8demangle4testFGiZv", "demangle.test(int[])"),
            ("_D8demangle4testFNhGfZv", "demangle.test(__vector(float[]))"),
            # `dlang_parse_real` reads the exponent's digits the same way.
            ("_D8demangle17__T4testVde0A8PZv", "demangle.test!(0x0.A8p)"),
            ("_D8demangle18__T4testVde0A8PNZv", "demangle.test!(0x0.A8p-)"),
            # `dlang_template_args` returns at the end of the name as readily as at `Z`,
            # so a template instance cut off mid-argument is whole to it.
            ("_D4main1xS4main__T3barTi", "main.x"),
        ],
    )
    def test_what_the_reference_reads_past_the_grammar_stays_refused(self, mangled, reference):
        """`reference` is what `c++filt --format=dlang` 2.42 answers; the grammar gives
        each of these a number it does not have."""
        assert demangle.demangle(mangled, language="d") == mangled
        with pytest.raises(DemangleFailure):
            parse_d_symbol(mangled)


class TestWhatTheSecondAndThirdDrawsFound:
    """`tools/mutate.py --seed 2` and `--seed 3`, 20,000 mutants each, over the parser as
    it stood after the pinned draw stood at zero. Three shapes this read and the
    reference refuses, each settled against `d-demangle.c`."""

    @pytest.mark.parametrize(
        "mangled", ["_D4main3fooMxxFZ3barFZv", "_D4main3fooMxOFZ3barFZv", "_D4main3fooMxyFZ3barFZv"]
    )
    def test_a_scope_this_parameter_follows_the_this_rule(self, mangled):
        """`dlang_parse_qualified` reads a scope's `M` modifiers with `dlang_type_modifiers`,
        the rule under which `x` and `y` come last and once. The scope path read them
        as a type's run and spelled `foo() const const.bar()`."""
        assert demangle.demangle(mangled, language="d") == mangled

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_D4main3fooMOxFZ3barFZv", "main.foo() shared const.bar()"),
            ("_D4main3fooMNgNgFZ3barFZv", "main.foo() inout inout.bar()"),
            ("_D4main3fooMOOFZ3barFZv", "main.foo() shared shared.bar()"),
        ],
    )
    def test_what_the_this_rule_admits_on_a_scope(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    def test_a_digit_after_a_path_is_the_next_component_and_nothing_else(self):
        """`dlang_symbol_name_p` says a digit opens a component, and a component that
        does not parse fails the name. Backing out and reading the digits as an old-style
        bare integer value spelled `test!(42)` for a name the reference refuses; the value
        a compiler writes carries its type, `i42`."""
        assert (
            demangle.demangle("_D8demangle__T4testVE3foo3bar42Zv", language="d") == "_D8demangle__T4testVE3foo3bar42Zv"
        )
        assert demangle.demangle("_D8demangle__T4testVE3foo3bari42Zv", language="d") == "demangle.test!(42)"
        # The bounded form of the same: `2Zv` opens a component that runs past the
        # template's length, and the whole instance is malformed.
        name = "_D8demangle28__T4testVS8demangle1S2Si1i2Zv"
        assert demangle.demangle(name, language="d") == name

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_D8demangle__T4testS1iZv", "demangle.test!(i)"),
            ("_D8demangle__T4testS1aZv", "demangle.test!(a)"),
            ("_D8demangle__T4testS6symbolZv", "demangle.test!(symbol)"),
            ("_D8demangle__T4testS116symbol3fooZv", "demangle.test!(symbol.foo)"),
        ],
    )
    def test_a_one_character_symbol_argument_in_the_older_form(self, mangled, expected):
        """`dlang_template_symbol_param` tries a length only where the region after it
        opens on a digit or `_D`. `S1i` had its `1` taken for a length and its `i` for a
        type, and the argument the reference spells `i` was refused."""
        assert demangle.demangle(mangled, language="d") == expected

    @pytest.mark.parametrize("mangled", ["_D8demangle4mainFZ4__S1xi", "_D8demangle4mainFZ4__S1FZ1xi", "_D4main4__S1Z"])
    def test_a_compiler_scope_is_followed_by_the_next_component_at_once(self, mangled):
        """`dlang_identifier` steps over a `__S<n>` and reads the next identifier there
        and then -- not a scope type, not the symbol's type and not the end of the name.
        `_D8demangle4mainFZ4__S1xi` read `demangle.main()` with the `xi` as its type,
        where the reference refuses."""
        assert demangle.demangle(mangled, language="d") == mangled
        assert demangle.demangle("_D8demangle4mainFZ4__S11xi", language="d") == "demangle.main().x"

    def test_a_delegate_back_reference_points_at_a_function_type(self):
        """`dlang_type_backref` with `is_function` set reads a function type at the
        target and nothing else. `PDQg` in a mutant of a real `std.regex` symbol points
        at a struct type, which this resolved and spelled `real delegate*` where the
        reference refuses."""
        assert demangle.demangle("_D4main3fooFFZvDQeZv", language="d") == "main.foo(void() function, void() delegate)"
        assert demangle.demangle("_D4main3fooFFZvPDQfZv", language="d") == "main.foo(void() function, void() delegate*)"
        for mangled in ("_D4main3fooFDQbZv", "_D4main3fooFFZvDQdZv"):
            assert demangle.demangle(mangled, language="d") == mangled
        seed = (
            "_D3std5regex8internal8thompson__T11ThompsonOpsTCQBuQBtQBqQBk__T15ThompsonMatcherTaTSQDeQDdQDa2ir"
            "__T14BackLooperImplTSQElQEkQEhQBh__T5InputTaZQjZQBtZQDhTSQFvQFuQFrQFl__TQEbTaTQDnZQEl5StateHVbi0Z"
            "__T2opVEQHrQHqQHnQEn2IRi162ZQzFNaNbNiNeQHdPQDgZb"
        )
        assert demangle.demangle(seed, language="d").endswith(
            ".op!(162).op(std.regex.internal.thompson.ThompsonMatcher!(char, std.regex.internal.ir.BackLooperImpl!(std.regex.internal.ir.Input!(char).Input).BackLooperImpl).ThompsonMatcher, std.regex.internal.thompson.ThompsonMatcher!(char, std.regex.internal.ir.BackLooperImpl!(std.regex.internal.ir.Input!(char).Input).BackLooperImpl).ThompsonMatcher.State*)"
        )
        mutant = seed.replace("PQDgZb", "PDQgZb")
        assert demangle.demangle(mutant, language="d") == mutant


class TestWhatTheFourthToSixthDrawsFound:
    """`tools/mutate.py --seed 4`, `5` and `6`, 20,000 mutants each: four more shapes
    this read and `c++filt --format=dlang` refuses or spells otherwise, each settled
    against `d-demangle.c`."""

    def test_a_back_reference_to_an_anonymous_component_keeps_its_slot(self):
        """`dlang_symbol_backref` reads a zero-length name and appends nothing, and the
        `.` before the next component is written all the same: `a..c`. A literal `0` is
        skipped whole, as before."""
        assert demangle.demangle("_D1a0Qb1ci", language="d") == "a..c"
        assert demangle.demangle("_D1a1b0Qb1ci", language="d") == "a.b..c"
        assert demangle.demangle("_D1a1b0i", language="d") == "a.b"

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_D8demangle__T4testVAbA2i0i1Zv", "demangle.test!([0, 1])"),
            ("_D8demangle__T4testVAaA2i65i66Zv", "demangle.test!([65, 66])"),
            ("_D8demangle__T4testVAmA2i1i2Zv", "demangle.test!([1, 2])"),
        ],
    )
    def test_an_array_literal_spells_its_elements_untyped(self, mangled, expected):
        """`dlang_parse_arrayliteral` reads each value with no type: no `uL` on a
        `ulong`, and no `true` or `'A'` either, where this wrote `[false, true]`."""
        assert demangle.demangle(mangled, language="d") == expected

    @pytest.mark.parametrize("mangled", ["_D5__T0Zv", "_D__T0Zv", "_D8demangle__T0Zv"])
    def test_a_template_instance_named_by_the_anonymous_zero_is_refused(self, mangled):
        """`dlang_parse_template` refuses `__T0`; this spelled `!()`."""
        assert demangle.demangle(mangled, language="d") == mangled

    def test_no_scope_type_follows_a_literal_anonymous_component(self):
        """`dlang_parse_qualified` `continue`s past a `0`, stepping over the parameters
        every other component may carry, so a function type after it is the symbol's
        own and nothing may follow it. `_D1a0FZ1bi` read `a.().b`."""
        for mangled in ("_D1a0FZ1bi", "_D1a1b0FZ1ci"):
            assert demangle.demangle(mangled, language="d") == mangled
        assert demangle.demangle("_D1a0FZv", language="d") == "a"
        assert (
            demangle.demangle("_D4core4sync5mutex5Mutex6unlock0FNeZv", language="d") == "core.sync.mutex.Mutex.unlock"
        )


class TestAScopeInsideATypesName:
    """`dlang_parse_qualified` inside a type: the parameters a component carries are its
    scope whenever they parse and the name goes on, with no trailing type for them to
    be instead, and the `this` modifiers are written only for a symbol -- `suffix_modifiers`
    is 0 for a type's name and a plain symbol argument, 1 for a symbol and a `_D`-prefixed
    argument. `tools/mutate.py --seed 8` reached the first through a `std.utf` mutant
    whose struct name ended `byUTF(...)` and was read here with the parameters handed to
    the enclosing parameter list instead."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_D4main3fooFS4main1S3barFZiZv", "main.foo(main.S.bar(), int)"),
            ("_D4main3fooFS4main1S3barMxFZ1xZv", "main.foo(main.S.bar().x)"),
            ("_D4main3fooFS4main1S3barMxFZZv", "main.foo(main.S.bar())"),
            ("_D8demangle__T4testS4main3fooMxFZ3barZv", "demangle.test!(main.foo().bar)"),
            ("_D8demangle__T4testS_D4main3fooMxFZvZv", "demangle.test!(main.foo() const)"),
            ("_D8demangle__T4testS_D4main3fooMxFZ3barFZvZv", "demangle.test!(main.foo() const.bar())"),
            ("_D4main3fooMxFZ3barFZv", "main.foo() const.bar()"),
        ],
    )
    def test_the_spelling(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected


class TestAPostblitAnywhereInTheName:
    """`dlang_lname` matches the thirteen characters `__postblitMFZ` as one thing,
    wherever in the name they stand, and writes `this(this)` with no parameter list
    after it. Renaming only the last component left an interior one as `__postblit()`,
    which `tools/mutate.py --seed 11` found in a `std.digest` mutant. Any other shape
    -- attributes, modifiers, a parameter -- is left as `__postblit`, as before."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            (
                "_D8demangle4test10__postblitMFZ3std6digest2md3MD53putMFNaNbNeMAxhXv",
                "demangle.test.this(this).std.digest.md.MD5.put(scope const(ubyte)[]...)",
            ),
            ("_D3foo3Bar10__postblitMFZ3bazMFZv", "foo.Bar.this(this).baz()"),
            ("_D3foo3Bar10__postblitMxFZ3bazMFZv", "foo.Bar.__postblit() const.baz()"),
            ("_D3foo3Bar10__postblitMFZv", "foo.Bar.this(this)"),
            ("_D3foo3Bar10__postblitMFZi", "foo.Bar.this(this)"),
            ("_D3foo3Bar10__postblitMFNaNbNiNfZv", "foo.Bar.__postblit()"),
        ],
    )
    def test_the_spelling(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    @pytest.mark.parametrize("mangled", ["_D3foo3Bar10__postblitMFZ", "_D3foo3Bar10__postblitMFZv3bazMFZv"])
    def test_what_the_reference_refuses(self, mangled):
        assert demangle.demangle(mangled, language="d") == mangled


class TestABackReferenceIntoADigitRun:
    """`dlang_symbol_backref` reads the length at the target with `dlang_number`, which
    takes the whole digit run: `06289` is a length of 6289, and `01a` is `a`. Only a
    lone `0` is the empty identifier. Stopping at the first `0` read a target inside a
    mutated name's own digits as an anonymous component and went on, spelling
    `..length` and `.array.Appender` where the reference refuses the name.
    `tools/mutate.py --seed 15` and `--seed 17`."""

    @pytest.mark.parametrize(
        "mangled",
        [
            "_D202TypeInfo_S3std6random__T21MersenneTwisterEngineTmVmi64Vmi312Vmi156Vmi31VmN5403634167711393303"
            "Vmi29Vmi6148914691236517205Vmi17Vmi8202884508482404352Vmi37VmN22706289tiArrayTSQzQx__T9BitPackedTk"
            "Vmi12ZQsTtZQBs__T6lengthVmi1ZQmMxFNaNbNdNiNfZm",
            "_D3std6digest__T13WrapperDigestTSQBfQBe3crc__T3CRCVki64VmN3932672073523589310ZQBgZ__T14formattedWrite"
            "VAyaa15_20253032643a253032643a25303264TSQCy5array__T8AppenderTQCjZQoTxhTxhTxhZQDqFNaNfKQBwxhxhxhZk",
        ],
    )
    def test_a_length_that_overruns_is_refused(self, mangled):
        assert demangle.demangle(mangled, language="d") == mangled

    def test_a_lone_zero_is_still_the_empty_identifier(self):
        assert demangle.demangle("_D1a0Qb1ci", language="d") == "a..c"


class TestABackReferenceReadsAPlainIdentifier:
    """`dlang_symbol_backref` is `dlang_number` and then `dlang_lname`: a length and
    that many characters spelled as they stand, with only the constructor and
    destructor renames. A target whose body happens to be a template instance,
    `13__T4testThTuZ`, is the identifier `__T4testThTuZ` to it, where `symbol_name`
    read the template and spelled `test!(ubyte, wchar)` twice over. No compiler points
    a back reference at one. `tools/mutate.py --seed 21`."""

    def test_a_template_instance_body_is_spelled_as_it_stands(self):
        assert (
            demangle.demangle("_D8demangle13__T4testThTuZQpFNaNbNiNfwZh", language="d")
            == "demangle.test!(ubyte, wchar).__T4testThTuZ(dchar)"
        )

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_D3std3foo__TQhTiZQmv", "std.foo.foo!(int).foo"),
            ("_D3foo6__ctorFSQjZv", "foo.this(this)"),
            ("_D3foo6__dtorFSQjZv", "foo.~this(~this)"),
            ("_D1a0Qb1ci", "a..c"),
        ],
    )
    def test_the_rest_is_unchanged(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected


class TestWhatTheTwentyFourthAndTwentySixthDrawsFound:
    """Three more libiberty rules, each reached by a mutant of a real name."""

    @pytest.mark.parametrize("mangled", ["_D8demangle4mainFZ4__S10xi", "_D8demangle4mainFZ4__S1Qji"])
    def test_a_compiler_scope_is_followed_by_an_identifier_with_a_length(self, mangled):
        """`dlang_identifier` reads the next identifier straight after a `__S<n>`, and a
        `0` there is a length of nothing to it -- refused, not the anonymous component
        the path loop skips. Reading it as anonymous spelled `demangle.main()`."""
        assert demangle.demangle(mangled, language="d") == mangled

    def test_a_compiler_scope_still_reads_before_a_real_identifier(self):
        assert demangle.demangle("_D8demangle4mainFZ4__S11xi", language="d") == "demangle.main().x"

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_D8demangle32__T4testS20_D6symbol3foo3bar2ZvZv", "demangle.test!(_D6symbol3foo3bar2Zv)"),
            ("_D8demangle__T4testS20_D6symbol3foo3bar2ZvZv", "demangle.test!(_D6symbol3foo3bar2Zv)"),
            ("_D8demangle31__T4testS19_D6symbol3foo3barZvZv", "demangle.test!(_D6symbol3foo3barZv)"),
            ("_D8demangle30__T4testS18_D6symbol3foo3bariZv", "demangle.test!(symbol.foo.bar)"),
        ],
    )
    def test_a_prefixed_symbol_argument_needs_its_type(self, mangled, expected):
        """`dlang_parse_mangle` is `_D QualifiedName Type` or `_D QualifiedName Z`, the
        type not optional, so a length-bounded region that is a qualified name and
        nothing more is not a symbol to `dlang_template_symbol_param` and is spelled as
        it stands. Reading it as one spelled `symbol.foo.bar.Zv`."""
        assert demangle.demangle(mangled, language="d") == expected

    @pytest.mark.parametrize(
        "mangled",
        [
            "_D8demangle__T4testS_DaZv",
            "_D8demangle__T4testS_D0Zv",
            "_D4core8internal2gc4impl12conservativeQw3Gcx__T7markAllS_DaZv",
        ],
    )
    def test_a_prefix_with_no_name_after_it_is_refused(self, mangled):
        """`dlang_template_symbol_param` takes the `_D` form only where a symbol name
        follows the prefix; otherwise the `_D` is read as a length, which it is not.
        `S_DaZv` came back as an argument spelling nothing."""
        assert demangle.demangle(mangled, language="d") == mangled

    def test_the_prefixed_form_still_reads(self):
        assert (
            demangle.demangle("_D8demangle__T4testS_D6symbol3foo3bariZv", language="d")
            == "demangle.test!(symbol.foo.bar)"
        )


class TestATemplateBodyIsReadAgainstTheWholeName:
    """`dlang_parse_template` reads a length-prefixed template body against the whole
    of what remains and compares what it consumed with the length afterwards, refusing
    the name on a mismatch. Bounding the body first read a mutant of
    `demangle.fn!(sym, val("null"))` where the reference refuses it: inside the body,
    `sym` is followed by a `V` that opens a function type whose parameter list happens
    to run to a `Z` far past the body -- `56` and then fifty-six characters -- and the
    reference reads that greedily, as it reads any scope inside a type, then finds the
    `v` after it is no template argument. Found with an instrumented build of
    libiberty's own source; `tools/mutate.py --seed 28`."""

    def test_the_greedy_reading_runs_past_the_body_and_the_name_is_refused(self):
        mangled = "_D8demangle32__T2fnTS3symVS3valS1a4_6e756c6cZ3fun13__T8positionZ13__T8confusesZ8demangle4testMOxFZv"
        assert demangle.demangle(mangled, language="d") == mangled

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # One character more after the body and the digits no longer line up: the
            # greedy reading fails, is put back, and the name reads.
            (
                "_D8demangle32__T2fnTS3symVS3valS1a4_6e756c6cZ3fun13__T8positionZ13__T8confusesZ9demangle24testMOxFZv",
                'demangle.fn!(sym, val("null")).fun.position!().confuses!().demangle2.test() shared const',
            ),
            (
                "_D8demangle32__T2fnTS3symVS3valS1a4_6e756c6cZ3fun13__T8positionZ13__T8confusesZ8demangleFDFxaZvZv",
                'demangle.fn!(sym, val("null")).fun.position!().confuses!().demangle(void(const(char)) delegate)',
            ),
            ("_D8demangle11__T4testTaZv", "demangle.test!(char)"),
            ("_D4main3__TFZv", "main.__T()"),
        ],
    )
    def test_the_rest_is_unchanged(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected

    def test_a_length_that_does_not_match_still_refuses(self):
        assert demangle.demangle("_D8demangle12__T4testTaZv", language="d") == "_D8demangle12__T4testTaZv"


class TestAnAnonymousLastComponentInsideAnArgument:
    """The type after an anonymous last component belongs to that component, which the
    reference does not spell, so neither is the type: `dlang_parse_qualified` steps past
    the `0` and `dlang_parse_mangle` reads the type as the symbol's own and prints
    nothing for it. `parse` knew this for a whole symbol -- `foo.bar` for
    `_D3foo3bar0FNbmZm` -- and `mangled_symbol` did not, so the same symbol as a
    template argument spelled `foo.bar(ulong)`. A mutant of a `core.internal.gc`
    symbol, `tools/mutate.py --scheme d --seed 38`."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_D8demangle__T4testS_D3foo3bar0FNbmZmZv", "demangle.test!(foo.bar)"),
            ("_D8demangle__T4testS_D3foo3barFNbmZmZv", "demangle.test!(foo.bar(ulong))"),
            ("_D3foo3bar0FNbmZm", "foo.bar"),
        ],
    )
    def test_the_spelling(self, mangled, expected):
        assert demangle.demangle(mangled, language="d") == expected


class TestTheMachOUnderscore:
    """LDC on macOS writes the same `_D` names as everywhere else, and the linker puts a
    leading underscore on every symbol, so `nm` shows `__D4test3fooFZv`. GNU `c++filt
    --format=dlang -_` strips one and reads it; `ddemangle` refuses it, never having
    seen a symbol table. One comes off here, as for an Itanium or Swift name."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("__D4test3fooFZv", "test.foo()"),
            ("__Dmain", "D main"),
            ("_D4test3fooFZv", "test.foo()"),
        ],
    )
    def test_one_underscore_comes_off(self, mangled, expected):
        from demangle.schemes.d import detect

        assert detect(mangled)
        assert demangle.demangle(mangled) == expected
        assert demangle.demangle_strict(mangled, language="d") == expected

    def test_only_one_comes_off(self):
        from demangle.schemes.d import detect

        assert not detect("___D4test3fooFZv")
        assert demangle.demangle("___D4test3fooFZv") == "___D4test3fooFZv"
        assert demangle.demangle("__D") == "__D"
