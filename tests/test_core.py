"""Unit tests for the shared machinery.

`core` is what every scheme is built on, so its pieces are tested directly rather than
only through whole-name demangling, where a bug would show up as a puzzling spelling
several layers away.
"""

import pathlib
import string
import subprocess
import sys

import pytest

import demangle
from demangle.core import registry
from demangle.core.ast import AST_BUILDER as A
from demangle.core.cache import MISSING, BoundedCache
from demangle.core.errors import DemanglingError, ParseError, TruncatedError
from demangle.core.plugin import LanguagePlugin
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

    Keeping `ParameterPack((ParameterPack(()),))` as a pack of one member would give it
    a non-zero `size` while it renders to nothing, so the two questions the parser asks
    about a pack could get different answers from the two builders: "did this
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

    def test_a_full_generation_ages_out_rather_than_everything(self):
        """Clearing wholesale threw the working set away with the rest."""
        cache = BoundedCache(max_size=4)
        for index in range(5):
            cache.put(index, index)
        assert [index for index in range(5) if index in cache] == [2, 3, 4]

    def test_never_holds_more_than_max_size(self):
        cache = BoundedCache(max_size=6)
        for index in range(100):
            cache.put(index, index)
            assert len(cache) <= 6

    def test_an_entry_in_use_survives_every_turnover(self):
        cache = BoundedCache(max_size=4)
        cache.put("hot", 1)
        for index in range(50):
            assert cache.get("hot") == 1
            cache.put(index, index)
        assert "hot" in cache
        assert 0 not in cache

    def test_a_hit_in_either_generation_is_a_hit(self):
        cache = BoundedCache(max_size=4)
        cache.put("old", 1)
        cache.put("x", 2)
        cache.put("young", 3)
        assert cache.get("young") == 3
        assert cache.get("old") == 1
        assert cache.get("absent") is MISSING
        assert (cache.stats["hits"], cache.stats["misses"]) == (2, 1)

    def test_clear_empties_both_generations_and_the_counts(self):
        cache = BoundedCache(max_size=4)
        for index in range(3):
            cache.put(index, index)
        cache.get(0)
        cache.clear()
        assert len(cache) == 0
        assert cache.get(0) is MISSING
        assert cache.stats["hits"] == 0

    def test_a_value_computed_before_a_clear_is_not_stored_after_it(self):
        cache = BoundedCache()
        epoch = cache.epoch
        cache.clear()
        assert cache.put("stale", 1, epoch) == 1
        assert "stale" not in cache
        cache.put("fresh", 2, cache.epoch)
        assert cache.get("fresh") == 2

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

    def test_a_scheme_declaring_nothing_is_always_offered(self, monkeypatch):
        plain = LanguagePlugin(name="plain", detect=lambda name: False, parse=lambda *args: None)
        monkeypatch.setattr(registry, "_plugins", {**registry._plugins, "plain": plain})
        monkeypatch.setattr(registry, "_ordered", None)
        monkeypatch.setattr(registry, "_by_first", None)
        for name in ("anything at all", "_Z1fv", "?f@@YAXH@Z", "x"):
            assert plain in registry.candidates(name)


class TestDetectScreen:
    """`DETECT_SCREEN` lets the registry skip a scheme with no first character of its own
    unless the name carries a marker it asks for. Like the first-character screen, it
    loses symbols silently if it is wrong, so what is checked is the property it claims:
    no name without the markers or an opening is ever claimed by the scheme."""

    def _names(self):
        names = [name for name in TestFirstCharacterScreen()._corpus_names() if name]
        # The forms a symbol table adds, and the leading underscores a Mach-O one does.
        names += [f"{name}@@VERS_1.0" for name in names] + [f"_{name}" for name in names]
        alphabet = string.ascii_letters + string.digits + "_$?@.%&*<>-+~/("
        names += list(alphabet) + [a + b + c for a in alphabet for b in alphabet for c in "_$."]
        return names

    @staticmethod
    def _passes(screen, name):
        markers, openings = screen
        if any(marker in name for marker in markers):
            return True
        for opening in openings:
            prefix, needs = (opening, ()) if isinstance(opening, str) else opening
            if name.startswith(prefix) and (not needs or any(need in name for need in needs)):
                return True
        return False

    def test_no_screened_scheme_claims_a_name_its_screen_turns_away(self, subtests):
        from demangle.api import _undecorated

        names = self._names()
        assert len(names) > 60000
        screened = [(plugin, registry._screened(plugin)) for plugin in registry.available()]
        assert {plugin.name for plugin, screen in screened if screen} >= {"go", "gnuv2", "codewarrior"}
        for plugin, screen in screened:
            if screen is None:
                continue
            with subtests.test(name=plugin.name):
                missed = []
                for name in names:
                    if self._passes(screen, name):
                        continue
                    base = _undecorated(name)
                    if plugin.detect(name) or (base and plugin.symbol_table_decorations and plugin.detect(base)):
                        missed.append(name)
                assert missed == [], missed[:5]

    def test_a_plain_c_name_is_offered_to_no_scheme_without_a_first_character(self):
        assert registry.candidates("memcpy") == ()
        assert registry.candidates("g_object_ref@@GLIB_2.0") == ()
        assert [plugin.name for plugin in registry.candidates("?f@@YAXH@Z")] == ["msvc"]

    def test_a_marker_offers_the_name_to_every_scheme_in_the_list(self):
        """All or nothing: the markers are one pass, and each scheme still decides."""
        offered = [plugin.name for plugin in registry.candidates("f__Fv")]
        assert offered == ["go", "nim", "pascal", "ada", "gnuv2", "codewarrior"]
        assert demangle.detect("f__Fv") == "gnuv2"
        assert demangle.detect("main.(*T).M") == "go"
        assert demangle.detect("fmt.Println") is None
        assert demangle.detect("github.com/x/y.F") == "go"
        assert demangle.detect("go:buildid") == "go"

    def test_an_opening_with_markers_of_its_own_needs_one_of_them(self):
        """g++ 2.x's special forms open `_` and carry a `$` or `.`; an Itanium name does
        neither, so it is not offered to the six schemes that would each turn it away."""
        assert [plugin.name for plugin in registry.candidates("_ZN3foo3barEv")] == ["d", "swift", "rust", "itanium"]
        assert "gnuv2" in [plugin.name for plugin in registry.candidates("_$_3foo")]
        assert demangle.detect("_$_3foo") == "gnuv2"
        assert demangle.detect("_vt.3foo") == "gnuv2"

    def test_an_opening_counts_only_in_its_own_first_characters_list(self):
        assert [plugin.name for plugin in registry.candidates("_ada_main")][-3:] == ["ada", "gnuv2", "codewarrior"]
        assert registry.candidates("type:int") != ()
        assert registry.candidates("typo") != ()  # Nim's `ty` opening
        assert registry.candidates("tapo") == ()

    def test_a_replacement_under_a_built_in_name_is_not_screened(self, monkeypatch):
        import dataclasses

        mine = dataclasses.replace(registry.get("go"), description="mine")
        assert registry._screened(registry.get("go")) is not None
        assert registry._screened(mine) is None
        monkeypatch.setattr(registry, "_plugins", {**registry._plugins, "go": mine})
        monkeypatch.setattr(registry, "_ordered", None)
        monkeypatch.setattr(registry, "_by_first", None)
        assert mine in registry.candidates("memcpy")


class TestDetectionOrderIsPinned:
    """The order plugins are offered names in, asserted rather than reasoned about.

    `priority` is ascending -- lower is offered first -- so the comment beside every
    shape-test scheme must agree with its number: `go` is first, not "last". This test
    states the order where it can be read.

    It is not cosmetic. Free Pascal and Swift both claim `_$SDL_MIXER$_Ld1`, and Free
    Pascal is right about it only because it is asked first. Reordering the
    priorities changes what that name detects as, and this test says so.
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


def _fresh(script):
    """Run `script` in a new interpreter, where no scheme has been imported yet."""
    source = pathlib.Path(__file__).resolve().parent.parent / "src"
    run = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True, env={"PYTHONPATH": str(source)}
    )
    return run.stdout.split()


class TestLazyBuiltIns:
    """The registry orders and screens a built-in scheme before importing it, from what
    `_BUILTIN_MODULES` says about it. That copy must say what the scheme itself does."""

    def test_the_registry_describes_each_scheme_as_the_scheme_does(self):
        import importlib

        for name, module, *declared in (entry for entry in registry._BUILTIN_MODULES if len(entry) > 2):
            scheme = importlib.import_module(module)
            plugin = scheme.PLUGIN
            actual = [
                plugin.priority,
                plugin.first_characters,
                plugin.symbol_table_decorations,
                tuple(plugin.aliases),
                getattr(scheme, "DETECT_SCREEN", None),
            ]
            assert declared == actual, f"update {name}'s entry in core/registry.py to {actual}"

    def test_a_name_imports_only_the_schemes_it_reaches(self):
        loaded = _fresh(
            "import sys, demangle\n"
            "assert demangle.demangle('?f@@YAXH@Z') == 'void __cdecl f(int)'\n"
            "assert 'jni' in demangle.languages() and 'gnat' in demangle.core.registry.aliases()\n"
            "print(*sorted(m.split('.')[2] for m in sys.modules if m.count('.') == 2 and '.schemes.' in m))\n"
        )
        # Nothing else starts `?`, and the name has no marker a screened scheme asks for.
        assert loaded == ["msvc"]

    def test_detection_order_is_the_same_before_any_scheme_is_imported(self):
        order = _fresh(
            "from demangle.core import registry\nprint(*(plugin.name for plugin in registry.candidates('x__$/')))\n"
        )
        expected = [name for name in TestDetectionOrderIsPinned.EXPECTED if not registry.get(name).first_characters]
        assert order == expected

    def test_a_replacement_survives_the_built_in_being_imported_later(self):
        replaced = _fresh(
            "import dataclasses, demangle\n"
            "from demangle.core import registry\n"
            "demangle.languages()\n"
            "stand_in = registry._plugins['jni']\n"
            "mine = dataclasses.replace(stand_in, description='mine')\n"
            "demangle.register_language(mine)\n"
            "import demangle.schemes.jni\n"
            "print(registry.get('jni') is mine, registry.get('java') is mine)\n"
        )
        assert replaced == ["True", "True"]

    @pytest.mark.parametrize("loaded_first", [False, True], ids=["before-load", "after-load"])
    @pytest.mark.parametrize("name", ["itanium", "msvc", "swift", "gnuv2", "codewarrior", "rust"])
    def test_a_replacement_survives_the_style_importing_the_built_in(self, name, loaded_first):
        """Registered before the first call there was no stand-in yet, so the built-in's
        own `register`, run when the style imported its options mid-parse, took the name
        back. One scheme per process: importing one can import another."""
        results = _fresh(
            "import demangle\n"
            "from demangle.core import registry\n"
            "from demangle.core.plugin import LanguagePlugin\n"
            + ("demangle.demangle('x')\n" if loaded_first else "")
            + f"mine = LanguagePlugin(name={name!r}, detect=lambda s: True,\n"
            "                      parse=lambda m, b, limits, options=None: b.name('CUSTOM'))\n"
            "registry.register(mine)\n"
            f"print(demangle.demangle('a', language={name!r}), demangle.demangle('b', language={name!r}))\n"
            f"import demangle.schemes.{name}\n"
            f"print(registry.get({name!r}) is mine)\n"
        )
        assert results == ["CUSTOM", "CUSTOM", "True"]

    def test_a_replacement_registered_before_loading_answers_to_the_built_ins_aliases(self):
        results = _fresh(
            "from demangle.core import registry\n"
            "from demangle.core.plugin import LanguagePlugin\n"
            "mine = registry.register(LanguagePlugin(name='itanium', detect=bool, parse=print))\n"
            "print(registry.get('gnu') is mine, registry.get('c++') is mine)\n"
        )
        assert results == ["True", "True"]

    @pytest.mark.parametrize("order", ["before-load", "after-load", "before-import"])
    def test_a_plugins_own_aliases_win_over_a_built_ins_whenever_it_registers(self, order):
        """`gnu` is Itanium's alias until a caller's plugin declares it, before or after
        the registry loads or Itanium is imported."""
        results = _fresh(
            "import demangle\n"
            "from demangle.core import registry\n"
            "from demangle.core.plugin import LanguagePlugin\n"
            + ("demangle.demangle('x')\n" if order == "after-load" else "")
            + "mine = registry.register(LanguagePlugin(name='d', detect=bool, parse=print, aliases=('gnu',)))\n"
            + ("import demangle.schemes.itanium\n" if order == "before-import" else "")
            + "print(registry.get('gnu') is mine, registry.get('c++').name, registry.aliases()['gnu'])\n"
        )
        assert results == ["True", "itanium", "d"]

    @pytest.mark.parametrize("loaded_first", [False, True], ids=["before-load", "after-load"])
    def test_an_alias_naming_a_built_in_is_refused_before_loading_too(self, loaded_first):
        results = _fresh(
            "import demangle\n"
            "from demangle.core import registry\n"
            "from demangle.core.plugin import LanguagePlugin\n"
            + ("demangle.demangle('x')\n" if loaded_first else "")
            + "try:\n"
            "    registry.register(LanguagePlugin(name='mine', detect=bool, parse=print, aliases=('rust',)))\n"
            "except ValueError as error:\n"
            "    print('refused')\n"
            "print(registry.get('rust').name)\n"
        )
        assert results == ["refused", "rust"]

    @pytest.mark.parametrize("loaded_first", [False, True], ids=["before-load", "after-load"])
    def test_the_built_in_can_be_registered_back_over_a_replacement(self, loaded_first):
        results = _fresh(
            "import dataclasses, demangle\n"
            "from demangle.core import registry\n"
            + ("demangle.demangle('x')\n" if loaded_first else "")
            + "import demangle.schemes.itanium as itanium\n"
            "mine = dataclasses.replace(itanium.PLUGIN, description='mine')\n"
            "registry.register(mine)\n"
            "print(demangle.demangle('_Z1fv') == 'f()', registry.get('gnu') is mine)\n"
            "registry.register(itanium.PLUGIN)\n"
            "print(registry.get('itanium') is itanium.PLUGIN, demangle.demangle('_Z1gv'))\n"
        )
        assert results == ["True", "True", "True", "g()"]

    @pytest.mark.parametrize("loaded_first", [False, True], ids=["before-load", "after-load"])
    def test_importing_a_built_in_with_no_replacement_registers_it(self, loaded_first):
        results = _fresh(
            "import demangle\n"
            "from demangle.core import registry\n"
            + ("demangle.languages()\n" if loaded_first else "")
            + "import demangle.schemes.msvc as msvc\n"
            "print(registry.get('msvc') is msvc.PLUGIN, demangle.demangle('?f@@YAXH@Z') == 'void __cdecl f(int)')\n"
        )
        assert results == ["True", "True"]


class TestPluginDiscoveryScreen:
    """`importlib.metadata` is consulted only when a distribution might name the group."""

    def test_an_environment_without_the_group_is_not_searched(self, tmp_path, monkeypatch):
        (tmp_path / "other-1.0.dist-info").mkdir()
        (tmp_path / "other-1.0.dist-info" / "entry_points.txt").write_text("[console_scripts]\nx = y:z\n")
        monkeypatch.setattr("sys.path", [str(tmp_path), str(tmp_path / "missing")])
        assert registry._may_advertise_plugins() is False

    @pytest.mark.parametrize("folder", ["toy-1.0.dist-info", "Toy.egg-info"])
    def test_a_distribution_naming_the_group_is(self, tmp_path, monkeypatch, folder):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "entry_points.txt").write_text(f"[{registry.ENTRY_POINT_GROUP}]\ntoy = toy:PLUGIN\n")
        monkeypatch.setattr("sys.path", [str(tmp_path)])
        assert registry._may_advertise_plugins() is True

    def test_anything_it_cannot_read_is_left_to_importlib_metadata(self, tmp_path, monkeypatch):
        archive = tmp_path / "bundle.zip"
        archive.write_bytes(b"")
        monkeypatch.setattr("sys.path", [str(archive)])
        assert registry._may_advertise_plugins() is True

        class Finder:
            def find_distributions(self, context=None):
                return []

        monkeypatch.setattr("sys.path", [])
        monkeypatch.setattr("sys.meta_path", [Finder(), *sys.meta_path])
        assert registry._may_advertise_plugins() is True
