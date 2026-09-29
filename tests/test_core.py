"""Unit tests for the shared machinery.

`core` is what every scheme is built on, so its pieces are tested directly rather than
only through whole-name demangling, where a bug would show up as a puzzling spelling
several layers away.
"""

import pathlib
import string

import pytest

import demangle
from demangle.core import registry
from demangle.core.ast import AST_BUILDER as A
from demangle.core.cache import MISSING, BoundedCache
from demangle.core.errors import DemanglingError, ParseError, TruncatedError
from demangle.core.reader import Reader
from demangle.core.spelling import SPELLING_BUILDER as B


class TestReader:
    def test_peek_past_the_end_is_empty_not_an_error(self):
        reader = Reader("ab")
        reader.pos = 2
        assert reader.peek() == ""
        assert reader.ahead(10) == ""

    def test_ahead_reads_without_moving_and_stops_at_the_end(self):
        reader = Reader("abc")
        assert (reader.peek(), reader.ahead(1), reader.ahead(2), reader.ahead(3)) == ("a", "b", "c", "")
        assert reader.pos == 0

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


class TestTheTwoBuildersAgreeAboutPacks:
    """Nested packs splice, in both builders, because two answers depend on it.

    The tree builder used to keep `ParameterPack((ParameterPack(()),))` as a pack of one
    member. That pack has a non-zero `size` and renders to nothing, so the two questions
    the parser asks about a pack got different answers from the two builders: "did this
    parameter drop out entirely" (`size == 0`) and "how many members does an expansion
    over this range across" (`len(members)`). `_Z1fIJEJT_EiEviT0_N2nn2UpE` -- an empty
    pack, then a pack holding a reference to it -- printed `f(int, , nn::Up)` through the
    tree and `f(int, nn::Up)` through the text. A grammar fuzzer found it; llvm-cxxfilt
    18.1.3 prints the latter.
    """

    def test_a_pack_holding_an_empty_pack_is_empty(self):
        for builder in (B, A):
            pack = builder.parameter_pack([builder.parameter_pack([])])
            assert builder.members(pack) == ()
            assert builder.size(pack) == 0
            assert builder.spell(pack) == ""

    def test_a_nested_pack_contributes_its_own_members_and_not_itself(self):
        for builder in (B, A):
            inner = builder.parameter_pack([builder.builtin("char"), builder.builtin("double")])
            pack = builder.parameter_pack([builder.builtin("int"), inner])
            assert len(builder.members(pack)) == 3
            assert builder.spell(pack) == "int, char, double"

    @pytest.mark.parametrize(
        "style, expected",
        [
            ("llvm", "void f<int>(int, nn::Up)"),
            # c++filt refuses the name, so this is what its rule for an empty pack --
            # an empty entry, comma kept -- gives for a pack that holds an empty one:
            # it spells nothing, and is one. See `gnu_empty_pack_spelling`.
            ("gnu", "void f<, , int>(int, , nn::Up)"),
        ],
    )
    def test_the_name_that_found_it(self, style, expected):
        name = "_Z1fIJEJT_EiEviT0_N2nn2UpE"
        assert demangle.demangle_strict(name, style=style) == expected
        assert demangle.parse(name, style=style).spell(style=style) == expected


class TestEveryDeclaratorDistributesOverAPack:
    """`Builder.parameter_pack` says a declarator applied to a pack applies to each
    member. `_wrap` -- pointers, references, cv-qualifiers -- had always done it; the
    three constructors that do not go through `_wrap` had not.

    The visible failure was the empty pack. `A3_ T_` with `T_` bound to `J E` printed
    ` [3]`, an array of a parameter that is not there, where the tree builder short-
    circuits the same shape to nothing -- so the two builders spelled the same name two
    ways. A grammar fuzzer found all three (`_Z1fIJEEvViT_A3_T_`,
    `_ZN1fIJET_EEvU9enable_ifIyET_`, `_Z1fIJEEvMT_T_DTfp0_EDi`).

    None of it changes a name any compiler emits: a pack reaches a declarator through
    `Dp`, and the parser has always ranged that over the members itself. What this fixes
    is the encoding that names a pack *without* expanding it, which is ill-formed -- and
    which llvm-cxxfilt prints as its first member and GNU c++filt refuses outright.

    Over an *empty* pack the two builders now agree by refusing: a declarator over no
    members spelled nothing, and a name that says `(int, int [3])` over `J E` came back
    `(int)`, one parameter fewer than it has. See `_over_a_pack`.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fIJicEEviA3_T_", "void f<int, char>(int, int [3], char [3])"),
            ("_Z1fIJicEEviU9enable_ifT_", "void f<int, char>(int, int enable_if, char enable_if)"),
            ("_Z1fIJicEEviMT_l", "void f<int, char>(int, long int::*, long char::*)"),
        ],
    )
    def test_a_declarator_over_a_pack_is_one_per_member(self, mangled, expected):
        assert demangle.demangle_strict(mangled) == expected
        assert demangle.parse(mangled).spell() == expected

    @pytest.mark.parametrize(
        "mangled", ["_Z1fIJEEviA3_T_", "_Z1fIJEEviU9enable_ifT_", "_Z1fIJEEviMT_i", "_Z1fIJEEviMiT_"]
    )
    def test_a_declarator_over_an_empty_pack_is_refused(self, mangled):
        """These read `void f<>(int)` once: the parameter vanished with the pack it was
        built over. `c++filt` refuses them; `llvm-cxxfilt` prints ` [3]` and `int ::*`,
        a declarator round nothing. Both builders refuse now, as one."""
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(mangled)
        assert demangle.demangle(mangled) == mangled
        assert demangle.demangle(mangled, style="gnu") == mangled

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_Z1fIJicEEviDpA3_T_", "void f<int, char>(int, int [3], char [3])"),
            ("_Z1fIJicEEviDpU9enable_ifT_", "void f<int, char>(int, int enable_if, char enable_if)"),
            ("_Z1fIJicEEviDpMT_l", "void f<int, char>(int, long int::*, long char::*)"),
        ],
    )
    def test_the_expansion_the_compiler_actually_writes_is_unchanged(self, mangled, expected):
        """Both references agree on these, and did before this too."""
        assert demangle.demangle_strict(mangled) == expected
        assert demangle.demangle_strict(mangled, style="gnu") == expected


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


class TestFirstCharacterScreen:
    """`LanguagePlugin.first_characters` lets the registry skip a scheme without calling
    into it. It is an optimisation that can lose symbols silently if it is wrong, so what
    is checked here is the property it claims: a scheme that declares a set never accepts
    a name outside it."""

    def _corpus_names(self):
        directory = pathlib.Path(__file__).parent / "conformance"
        for path in sorted(directory.glob("*.txt")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line and not line.startswith("#"):
                    yield line.split("\t", 1)[0]

    def test_no_declaring_scheme_claims_a_name_outside_its_set(self, subtests):
        names = list(self._corpus_names())
        # Plus every one- and two-character start, which no corpus holds.
        alphabet = string.ascii_letters + string.digits + "_$?@.%&*<>-+~"
        names += list(alphabet) + [a + b for a in alphabet for b in alphabet]
        assert len(names) > 20000
        for plugin in registry.available():
            if not plugin.first_characters:
                continue
            with subtests.test(name=plugin.name):
                outside = [
                    name for name in names if name and name[0] not in plugin.first_characters and plugin.detect(name)
                ]
                assert outside == [], outside[:5]

    def test_the_screen_returns_the_same_order_as_asking_everyone(self):
        for name in ("_ZN1fv", "?f@@YAXH@Z", "$sSi", "notmangled", ""):
            screened = [p.name for p in registry.candidates(name)]
            everyone = [p.name for p in registry.available() if p in registry.candidates(name)]
            assert screened == everyone

    def test_a_scheme_declaring_nothing_is_always_offered(self):
        for plugin in registry.available():
            if plugin.first_characters:
                continue
            assert plugin in registry.candidates("anything at all")


class TestDetectionOrderIsPinned:
    """The order plugins are offered names in, asserted rather than reasoned about.

    `priority` is ascending -- lower is offered first -- and the comments beside every
    shape-test scheme used to say the opposite of what its number did: `go` was
    documented as "last" and was in fact first. Nothing caught that, because nothing
    stated the order anywhere a test could read it.

    It is not cosmetic. Free Pascal and Swift both claim `_$SDL_MIXER$_Ld1`, and Free
    Pascal is right about it only because it is asked first. Anyone "fixing" the
    priorities to match the old comments would have broken those six names, and this is
    the test that would have told them so.
    """

    EXPECTED = (
        "go",
        "nim",
        "jni",
        "pascal",
        "objc",
        "delphi",
        "d",
        "swift",
        "rust",
        "msvc",
        "itanium",
        # The three that read a name with no marker of its own, offered last and in
        # order of how strong their evidence test is. Ada asks for an encoding GNAT
        # writes and C does not; the two C++ ones parse the whole name.
        "ada",
        "gnuv2",
        "codewarrior",
    )

    def test_the_built_in_order_is_what_it_is(self):
        from demangle.core.registry import available

        assert tuple(plugin.name for plugin in available()) == self.EXPECTED

    def test_lower_priority_really_does_come_first(self):
        from demangle.core.registry import available

        priorities = [plugin.priority for plugin in available()]
        assert priorities == sorted(priorities)

    def test_the_two_pre_itanium_schemes_are_offered_last_of_all(self):
        """Neither has a marker: their names are C identifiers with a `__` in them.

        GNU v2 before CodeWarrior, because a name valid under both -- and there are many,
        the two encodings being that close -- should go to the commoner mangling.
        """
        from demangle.core.registry import available

        order = [plugin.name for plugin in available()]
        assert order[-2:] == ["gnuv2", "codewarrior"]

    def test_rust_is_offered_before_itanium(self):
        """A legacy Rust symbol *is* an Itanium symbol; only the order tells them apart."""
        from demangle.core.registry import available

        order = [plugin.name for plugin in available()]
        assert order.index("rust") < order.index("itanium")

    def test_free_pascal_is_offered_before_swift(self):
        """Both claim `_$S...`; the corpus says Free Pascal is right about these."""
        import demangle as package
        from demangle.core.registry import available

        order = [plugin.name for plugin in available()]
        assert order.index("pascal") < order.index("swift")
        assert package.detect("_$SDL_MIXER$_Ld1") == "pascal"
