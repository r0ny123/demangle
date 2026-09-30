"""Pre-Itanium C++: g++ 2.x, cfront/ARM, Lucid, HP aCC and EDG.

The reference is GNU libiberty's `cplus-dem.c` at GCC 8.3.0, the last release that
carried it, and the corpus is that same tree's own `demangle-expected` -- every case it
marks `--format=gnu`, `--format=lucid`, `--format=arm` or `--format=hp`. Both settings
of `DMGL_PARAMS` are scored, because the file records both and they exercise different
paths.

The second thing measured here is the one that decides whether this scheme is safe to
ship: how often it claims a name that is not one of these. A GNU v2 symbol is an ordinary
C identifier with a `__` in it, so the question cannot be settled by looking at a prefix
and has to be answered with a number.
"""

import pytest

import demangle
from demangle.core.errors import DemanglingError
from demangle.schemes import gnuv2
from demangle.schemes.gnuv2 import GnuV2Options, nodes
from demangle.schemes.gnuv2._parser import DemangleFailure, demangle_gnuv2

from .conftest import CONFORMANCE
from .test_conformance import GNUV2_EXACT as LIBIBERTY_EXACT
from .test_conformance import GNUV2_TOTAL as LIBIBERTY_TOTAL

#: How many of the 662 the *default* style claims. The rest are ARM/Lucid/HP-only shapes
#: that GNU's reading refuses rather than guesses; `GnuV2Options(style=...)` reads them.
DETECTED_UNDER_GNU = 499

#: The rest have no argument list to separate: vtables, `type_info`, static data, thunks.
STRUCTURED = 624


def vectors():
    """The corpus, as `(mangled, style, with_params, without_params)`."""
    rows = []
    for line in (CONFORMANCE / "gnuv2-libiberty.txt").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            rows.append(tuple(line.split("\t")))
    return rows


def read(mangled, style, params=True):
    """What this reads, or the name unchanged -- which is what the reference prints."""
    try:
        return demangle_gnuv2(mangled, style=style, params=params).text
    except DemangleFailure:
        return mangled


class TestAgainstLibibertysOwnVectors:
    """The reference's test file, replayed."""

    def test_the_corpus_is_the_size_it_was(self):
        assert len(vectors()) * 2 == LIBIBERTY_TOTAL

    def test_every_vector_matches_the_reference(self):
        rows = vectors()
        exact = 0
        wrong = []
        for mangled, style, full, no_params in rows:
            for want, params in ((full, True), (no_params, False)):
                got = read(mangled, style, params)
                if got == want:
                    exact += 1
                else:
                    wrong.append((style, mangled, params, want, got))
        assert exact == LIBIBERTY_EXACT, wrong[:5]

    @pytest.mark.parametrize(
        ("mangled", "style", "expected"),
        [
            # The shape the whole thing exists for: a class prepended to a name that was
            # written first.
            ("AtEnd__13ivRubberGroup", "gnu", "ivRubberGroup::AtEnd(void)"),
            # A GNU destructor, whose marker is `_$_`.
            ("_$_3foo", "gnu", "foo::~foo(void)"),
            # An operator, spelled from the table.
            ("__eq__3fooRT0", "gnu", "foo::operator==(foo &)"),
            # A virtual table, which consumes the whole name.
            ("__vt_3foo", "gnu", "foo virtual table"),
            # ARM's constructor marker, and its explicit `F` before the arguments.
            ("__ct__1cFi", "arm", "c::c(int)"),
            # Lucid and ARM number back-references from one, over arguments only.
            ("__ct__11fstreambaseFiPcT1", "lucid", "fstreambase::fstreambase(int, char *, int)"),
            # HP aCC writes a template class's arguments as `X<type>` after the name.
            ("__dt__2T5XTi__Fv", "hp", "T5<int>::~T5(void)"),
            # A `_GLOBAL_` key, which is not a name at all.
            ("_GLOBAL_$I$set", "gnu", "global constructors keyed to set"),
        ],
    )
    def test_the_shapes_the_scheme_exists_for(self, mangled, style, expected):
        assert demangle_gnuv2(mangled, style=style).text == expected


class TestTheStyles:
    """One demangler, five readings, and the name does not say which."""

    def test_the_same_bytes_read_differently_under_different_styles(self):
        # `__ct` is ARM's constructor marker and nothing at all to GNU, which reads it as
        # an ordinary function name. Both readings parse; only one of them is right, and
        # the name does not say which -- which is why the style is an option.
        mangled = "__ct__1cFi"
        assert demangle_gnuv2(mangled, style="arm").text == "c::c(int)"
        assert demangle_gnuv2(mangled, style="gnu").text == "c::__ct(int)"

    def test_a_style_this_does_not_know_is_refused_by_name(self):
        with pytest.raises(ValueError, match="unknown style"):
            demangle_gnuv2("AtEnd__13ivRubberGroup", style="borland")
        with pytest.raises(ValueError, match="unknown style"):
            GnuV2Options(style="borland")

    def test_the_style_reaches_the_parser_through_the_style_object(self):
        mangled = "__dt__Q23foo3barFv"
        # The default `gnu` style does not know `__dt`, so this scheme declines it; the
        # CodeWarrior scheme, which shares the ARM family's convention, reads it instead.
        assert demangle.demangle(mangled) == "foo::bar::~bar()"
        as_lucid = demangle.style("llvm", gnuv2={"style": "lucid"})
        assert demangle.demangle(mangled, language="gnuv2", style=as_lucid) == "foo::bar::~bar(void)"

    def test_params_off_is_the_name_without_its_signature(self):
        mangled = "AddAlignment__9ivTSolverUiP12ivInteractorP7ivTGlue"
        bare = demangle.style("llvm", gnuv2={"params": False})
        assert demangle.demangle(mangled) == "ivTSolver::AddAlignment(unsigned int, ivInteractor *, ivTGlue *)"
        assert demangle.demangle(mangled, language="gnuv2", style=bare) == "ivTSolver::AddAlignment"

    def test_ansi_off_drops_the_qualifiers_a_pre_standard_compiler_did_not_print(self):
        mangled = "foo__FPCc"
        plain = demangle.style("llvm", gnuv2={"ansi": False})
        assert demangle.demangle(mangled) == "foo(char const *)"
        assert demangle.demangle(mangled, language="gnuv2", style=plain) == "foo(char *)"


class TestAFunctionCalledOp:
    """`__op<type>` is the conversion-operator marker -- `__opi__3Foo` is
    `Foo::operator int()` -- and libiberty takes the marker before it looks for the
    type, so a function that is merely called `__op` reads as an operator converting to
    nothing: `operator (int)` for `__op__Fi`. The identifier is what the name says.
    `tools/mutate.py --seed 1 --count 200000`."""

    def test_the_marker_with_a_type_is_the_operator(self):
        assert demangle.demangle("__opi__3Foo", language="gnuv2") == "Foo::operator int(void)"

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [("__op__Fi", "__op(int)"), ("__op__3Foo", "Foo::__op(void)")],
    )
    def test_the_marker_alone_is_a_name(self, mangled, expected):
        assert demangle.demangle(mangled, language="gnuv2") == expected


class TestLibibertySpellingAnEmptyFirstArgument:
    """`__dl__17T5__pt____3fooiRT0iT2iT2`: libiberty prints
    `T5__pt____3fooiRT::operator delete(, int, int, int, int)`, an empty first
    argument, which is a gap it spelled rather than a declaration. This reads the
    identifier. `tools/mutate.py --seed 9`."""

    def test_the_identifier_is_what_the_name_says(self):
        name = "__dl__17T5__pt____3fooiRT0iT2iT2"
        assert demangle.demangle(name, language="gnuv2") == "foo::__dl__17T5__pt__(int, foo &, int, foo &, int, foo &)"


class TestAnItaniumPrefixIsNeverClaimed:
    """The Itanium reader is offered every `_Z` and `__Z` name first; one it refuses
    is offered on down the list, and a Mach-O `__Z` name is full of the `__` this
    grammar reads as a separator. `__ZNKSt3__110__function6__funcI...`, refused by the
    Itanium reader under a forced numbering rule, would read as the method `__ZNKSt3` of
    a class named after the rest of it."""

    def test_a_mach_o_itanium_name_the_itanium_reader_refuses_comes_back_as_itself(self):
        from demangle.schemes.gnuv2 import detect

        name = (
            "__ZNKSt3__110__function6__funcIN14duckdb_httplib7Request20is_connection_closedMUlvE_E"
            "NS_9allocatorIS4_EEFbvEE11target_typeEv"
        )
        assert detect(name) is False
        # Under the ABI's closure rule, forced, `S4_` is the member `is_connection_closed`
        # standing as a type, and the Itanium reader refuses the name; see
        # `tests/test_itanium_closure_prefix.py`. Nothing else may then read it.
        forced = demangle.style("llvm", itanium={"closure_prefix_substitution": True})
        assert demangle.demangle(name, style=forced) == name


class TestTheLeadingUnderscoresScreen:
    """`__` and a letter opens a special form (`__vt_`, `__thunk_`, `__ti`, `__tf`), a
    constructor (`__Q`, `__K`, `__H`, `__t`), a DLL import (`__imp_`) -- or nothing, and
    then g++ 2.x's reading needs a second `__` to split the name at. `detect` turns the
    last kind away without parsing it: `__libc_start_main` and the rest of a C library's
    reserved names, which are most of what a real symbol table offers this scheme."""

    def _turned_away(self, name):
        return (
            name[:2] == "__"
            and name[2:3].isalpha()
            and name[2] not in "tvQKH"
            and name.find("__", 3) < 0
            and not name.startswith("__imp_")
        )

    def test_every_name_it_turns_away_is_one_the_parse_refuses(self):
        names = [row[0] for row in vectors()]
        names += [f"__{name}" for name in names] + [f"__x{name.replace('__', '_')}" for name in names]
        names += ["__libc_start_main", "__cxa_atexit", "__vtbl", "__ti", "__imp", "__i", "__Ab_", "__a__"]
        turned_away = [name for name in names if self._turned_away(name)]
        assert len(turned_away) > 500
        for name in turned_away:
            with pytest.raises(DemangleFailure):
                demangle_gnuv2(name)
            assert not gnuv2.detect(name), name

    @pytest.mark.parametrize(
        "name",
        ["__Q23foo3bar", "__t6vector1Zdi", "__vt_3foo", "__ti3foo", "__tf3foo", "__3fooi", "__imp_foo__Fv"],
    )
    def test_the_forms_that_need_no_second_separator_still_read(self, name):
        assert gnuv2.detect(name)


class TestWhatItRefusesToClaim:
    """The number that decides whether this is safe to have registered at all."""

    def test_an_ordinary_c_symbol_with_a_double_underscore_is_not_claimed(self):
        for name in (
            "__libc_start_main",
            "__cxa_atexit",
            "_IO_new_file_xsputn",
            "__init__",
            "std__vector",
            "__not_mangled",
            "__not_mangled_either__",
            "my__thing",
        ):
            assert not gnuv2.detect(name), name
            assert demangle.demangle(name) == name

    def test_a_spelling_that_cannot_be_a_declaration_is_not_a_reading(self):
        """Two shapes the grammar produces and no C++ declaration contains.

        Both are found by offering every symbol in the 957 shared objects a stock Ubuntu
        24.04 ships -- 345,601 names -- to the whole registry and looking at what comes
        back changed. They are the naming conventions an analyst actually meets:
        `g_cclosure_marshal_<RET>__<ARGS>` is GLib's generated marshaller, in every GTK
        binary, and `PyInit_<module>` covers every CPython extension whose name begins
        with an underscore.

        `int0_t` is what the reference prints when `I` is followed by something that is
        not hex -- `sscanf("%x")` over `NT` leaves the width zero and the bytes are
        swallowed. `f(char, short, void)` is `void` used as one parameter among several,
        which is a parameter list only when it is the whole of it.

        Both rules are detection only. Asked for by name the reading is unchanged, bug
        for bug, because that is what the 1,324-vector libiberty corpus measures.
        """
        for name, forced in (
            ("g_cclosure_marshal_VOID__INT", "g_cclosure_marshal_VOID(int0_t)"),
            ("g_cclosure_marshal_VOID__UINTv", "g_cclosure_marshal_VOID(unsigned int0_t, void)"),
            ("PyInit__csv", "PyInit(char, short, void)"),
            ("f__FI", "f(int0_t)"),
            ("f__Fcsv", "f(char, short, void)"),
        ):
            assert not gnuv2.detect(name), name
            assert demangle.demangle(name) == name
            assert demangle.demangle_strict(name, language="gnuv2") == forced

    def test_a_second_argument_list_after_the_ellipsis_is_not_a_reading(self):
        """`e` ends the list, so only the end or a return type may follow it.

        libiberty itself reads `foo__Fex` as `foo(...)(long long)`: back in
        `demangle_signature` the `x` is taken for the start of another list, and nothing
        checks that one was already read. A function returning a function is not a C++
        declaration, so this is refused -- a deliberate divergence, and one of very few,
        from the reference this scheme otherwise follows bug for bug.
        """
        for name in ("foo__Fex", "foo__Fiex", "foo__FPFe_vex"):
            assert not gnuv2.detect(name), name
            assert demangle.demangle(name) == name
            with pytest.raises(DemanglingError):
                demangle.demangle_strict(name, language="gnuv2")
        assert demangle.demangle("foo__Fe") == "foo(...)"
        assert demangle.demangle("foo__Fie") == "foo(int,...)"
        assert demangle.demangle("foo__FPFe_vi") == "foo(void (*)(...), int)"

    def test_a_special_form_that_does_not_read_is_not_read_as_something_else(self):
        """`_vt$t3Foo1Z_bar__Fi` is refused, not read as `_bar(int)`.

        `gnu_special` reads a virtual table's class and fails on the junk template with
        the cursor past it, and `demangle_prefix` then reads the tail of the name as a
        function. libiberty does the same -- `_vt$t8BDDHookV1__pt__2_cFv` is
        `_c::_pt(void)` to it -- and a function named after the end of a virtual table's
        symbol is not a reading of that symbol. `tools/mutate.py --scheme gnuv2` against the
        libiberty reference reaches it; the same holds for a thunk and a `type_info` name.
        """
        for name in (
            "_vt$t3Foo1Z_bar__Fi",
            "_vt$t8BDDHookV1ZP_new_Fix__FUs",
            "_vt$t8BDDHookV1__pt__2_cFv",
            "__tfPQ25libcwt16option_evet__12T1__pt__3_1tFv",
            "__thunk_8__$_junk__Fi",
            # libiberty tests only the four-character prefix, and misreads this as `k(int)`.
            "__tick__Fi",
        ):
            assert not gnuv2.detect(name), name
            assert demangle.demangle(name, language="gnuv2") == name
            with pytest.raises(DemanglingError):
                demangle.demangle_strict(name, language="gnuv2")
        assert demangle.demangle("_vt$t3Foo1ZPc") == "Foo<char *> virtual table"
        assert (
            demangle.demangle("__thunk_8__$_7ostream")
            == "virtual function thunk (delta:-8) for ostream::~ostream(void)"
        )

    def test_a_thunk_with_a_positive_delta_is_written_with_an_n(self):
        """gcc 2.95's `make_thunk` writes `__thunk_%d_` for a delta of zero or less
        and `__thunk_n%d_` for a positive one, so `__thunk_n8_` is a delta of 8. It is
        the one form, 396 times over, in 56,347 names from KDE 2.2.2, omniORB 3.0.4 and
        the rest of Debian woody's C++ that libiberty does not read: `gnu_special`
        steps past `__thunk_` before finding no digit,
        and `demangle_prefix` then reads the rest as a method,
        `KParts::PartBase::n8_setInstance(KInstance *)`. The compiler's own naming is
        the authority on what the compiler wrote; a name with no digits after the `n`
        is still refused.
        """
        assert (
            demangle.demangle("__thunk_n8_setInstance__Q26KParts8PartBaseP9KInstance", language="gnuv2")
            == "virtual function thunk (delta:8) for KParts::PartBase::setInstance(KInstance *)"
        )
        assert (
            demangle.demangle("__thunk_8_setInstance__Q26KParts8PartBaseP9KInstance", language="gnuv2")
            == "virtual function thunk (delta:-8) for KParts::PartBase::setInstance(KInstance *)"
        )
        for name in ("__thunk_n_foo__1Ai", "__thunk_nn8_foo__1Ai", "__thunk_n8foo__1Ai"):
            with pytest.raises(DemanglingError):
                demangle.demangle_strict(name, language="gnuv2")

    def test_void_alone_is_still_a_parameter_list(self):
        """The rule is `void` *among others*; on its own it is how the grammar says ()."""
        assert gnuv2.detect("f__Fv")
        assert demangle.demangle("f__Fv") == "f(void)"
        assert demangle.demangle("AtEnd__13ivRubberGroup") == "ivRubberGroup::AtEnd(void)"

    def test_a_reading_that_gives_back_its_own_input_is_not_a_reading(self):
        # `demangle_prefix` has a path that appends the rest of the name verbatim. A
        # "successful" demangling that spells the bytes it was given is no evidence.
        assert not gnuv2.detect("_GLOBAL_$I$")

    def test_no_name_from_any_other_scheme_s_corpus_is_read_as_this_one(self):
        """Over every checked-in corpus but the two pre-Itanium ones: none taken.

        End to end rather than through `detect` alone, because a name another scheme
        claims but fails to parse falls through to the rest. CodeWarrior's corpus is left
        out because that overlap is real rather than a defect -- the two manglings share
        shapes, and `tests/test_codewarrior.py` pins how they are divided.
        """
        claimed = []
        for path in sorted(CONFORMANCE.glob("*.txt")):
            if path.name.startswith(("gnuv2-", "codewarrior-")):
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#"):
                    continue
                name = line.split("\t")[0]
                try:
                    tree = demangle.parse(name)
                except DemanglingError:
                    continue
                if isinstance(tree, nodes.Symbol):
                    claimed.append(name)
        assert claimed == []

    def test_the_borland_family_never_even_reaches_the_parser(self):
        # `@Class@method$qqs...` parses as a signature if it is allowed to try, so the
        # `@` is refused before the parse rather than after it.
        assert not gnuv2.detect("@CWnd@ModifyStyle$qqsp6HWND__ulului")


class TestTheTree:
    """Parts in order, sliced out of the spelling the reference builds."""

    def test_the_tree_renders_to_exactly_what_the_text_path_spells(self):
        for mangled, style, _full, _bare in vectors():
            try:
                symbol = demangle_gnuv2(mangled, style=style)
            except DemangleFailure:
                continue
            assert nodes.build(symbol).render() == symbol.text, mangled

    def test_the_argument_list_comes_back_separated(self):
        tree = demangle.parse("AddAlignment__9ivTSolverUiP12ivInteractorP7ivTGlue", language="gnuv2")
        types = [node.render() for node in tree.walk() if node.kind == "type"]
        assert types == ["unsigned int", "ivInteractor *", "ivTGlue *"]
        names = [node.render() for node in tree.walk() if node.kind == "name"]
        assert names == ["ivTSolver::AddAlignment"]

    def test_how_many_vectors_get_a_structure_rather_than_one_flat_part(self):
        structured = 0
        for mangled, style, _full, _bare in vectors():
            try:
                symbol = demangle_gnuv2(mangled, style=style)
            except DemangleFailure:
                continue
            tree = nodes.build(symbol)
            if any(node.kind == "parameters" for node in tree.walk()):
                structured += 1
        assert structured == STRUCTURED

    def test_the_signature_view_splits_the_name_the_way_the_parser_did(self):
        parts = demangle.signature("AddAlignment__9ivTSolverUiP12ivInteractorP7ivTGlue", language="gnuv2")
        assert parts.qualified_name == "ivTSolver::AddAlignment"
        assert parts.base_name == "AddAlignment"
        assert parts.namespace == "ivTSolver"


class TestTheDefaultStyleClaims:
    """What arrives without a style named, which is what a symbol-table filter gets."""

    def test_how_many_vectors_the_default_style_claims(self):
        claimed = sum(1 for mangled, _style, _full, _bare in vectors() if gnuv2.detect(mangled))
        assert claimed == DETECTED_UNDER_GNU

    def test_a_name_the_default_style_declines_still_reads_when_the_style_is_named(self):
        mangled = "__ct__Q23foo3barFv"
        assert not gnuv2.detect(mangled)
        assert demangle_gnuv2(mangled, style="lucid").text == "foo::bar::bar(void)"


class TestAnArgumentListWithoutItsMarker:
    """`name__<type letters>` with no `F` and no class: a C name, not a g++ 2.x one.

    Over the symbols of every shared object a stock Ubuntu 24.04 ships, the default style
    claims none of three C names -- `PyInit__lldb`, `PyInit__sre`, `drm_intel_gem_bo_map__wc`
    -- because the reference reads whatever follows the `__` as an argument list when it
    opens with nothing it recognises. g++ 2.x never writes that: a free function is
    `name__F<args>` and a member `name__<class><args>`. Detection refuses the shape;
    asked for by name, the reading is the reference's.
    """

    @pytest.mark.parametrize(
        ("name", "forced"),
        [
            ("PyInit__lldb", "PyInit(long, long, double, bool)"),
            ("PyInit__sre", "PyInit(short, long double,...)"),
            ("drm_intel_gem_bo_map__wc", "drm_intel_gem_bo_map(wchar_t, char)"),
            ("foo__i", "foo(int)"),
        ],
    )
    def test_it_is_not_claimed_but_still_reads_by_name(self, name, forced):
        assert not gnuv2.detect(name)
        assert demangle.demangle(name) == name
        assert demangle.demangle_strict(name, language="gnuv2") == forced

    def test_the_marked_forms_are_still_claimed(self):
        assert demangle.demangle("foo__Fi") == "foo(int)"
        assert demangle.demangle("polar__Fdd") == "polar(double, double)"
        assert demangle.demangle("foo__3Bari") == "Bar::foo(int)"
        assert demangle.demangle("a__b__Fi") == "a__b(int)"

    def test_no_name_in_either_corpus_has_the_shape(self):
        """The rule costs nothing: every reading of that shape, over both corpora."""
        names = {row[0] for row in vectors()}
        for line in (CONFORMANCE / "gnuv2-real-world.txt").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                names.add(line.split("\t")[0])
        unmarked = []
        for mangled in names:
            try:
                symbol = demangle_gnuv2(mangled, style="gnu")
            except DemangleFailure:
                continue
            if symbol.evidence == {"unmarked"}:
                unmarked.append(mangled)
        assert unmarked == []

    def test_what_a_failed_guess_decoded_is_not_evidence_for_the_one_that_parsed(self):
        """Each `__` is tried in turn, and the evidence is rolled back with the rest.

        HP's `__dl__2T5XTi__SFPv` reads under `gnu` as `__dl__2T5XTi(void *) static`; a
        class that made it look plausible would come from a guess that has already failed.
        """
        for name in ("__dl__2T5XTi__SFPv", "elem__6vectorXTiSM__SCFPPd"):
            assert demangle_gnuv2(name, style="gnu").evidence == frozenset()
            assert not gnuv2.detect(name)
        assert demangle_gnuv2("__dl__2T5XTi__SFPv", style="hp").text == "T5<int>::operator delete(void *) static"


class TestRustV0:
    def test_a_damaged_v0_name_is_not_read_as_this_one(self):
        """A `_R` name the Rust reader refuses falls through, as `_Z` does from Itanium."""
        for name in (
            "_RNvCs1Y7DaGC1cwg_7ustc___r14___rust_realloc",
            "_RINvNtCsicaZO8UCM9y_3std2rt10lang_startuECsipD1KD37Gle__6consts",
            "__RNvCs1Y7DaGC1cwg_7ustc___r14___rust_realloc",
        ):
            assert not gnuv2.detect(name), name
            with pytest.raises(DemanglingError):
                demangle.parse(name, language="rust")
        assert demangle.demangle("_RNvCs1Y7DaGC1cwg_7ustc___r14___rust_realloc") == (
            "_RNvCs1Y7DaGC1cwg_7ustc___r14___rust_realloc"
        )

    def test_codewarrior_s_type_info_marker_is_not_mistaken_for_it(self):
        assert demangle.demangle("__RTTI__40TObjOwnerDerivedFromIObj<12CStringTable>") == (
            "TObjOwnerDerivedFromIObj<CStringTable>::__RTTI"
        )


class TestBounds:
    """A name from a symbol table nobody here wrote."""

    def test_a_name_that_nests_past_the_bound_is_refused_rather_than_recursed(self):
        deep = "f__F" + "PF" * 300 + "v"
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(deep, language="gnuv2")

    def test_the_empty_name_is_refused(self):
        with pytest.raises(DemanglingError):
            demangle.demangle_strict("", language="gnuv2")


class TestRefusalsTheReferenceReads:
    """Names libiberty reads and this refuses: libiberty spelling a gap round something
    it should have refused, the shapes `ACCEPTED["gnuv2"]` in `tools/enumerate.py` names."""

    @pytest.mark.parametrize("mangled", ["foo__H1Zi_X01i_", "foo__H1Zt2TA2ZiZt4N__A1im9_X01i_"])
    def test_a_return_type_marker_with_nothing_after_it_is_refused(self, mangled):
        """A template function's arguments end in `_` and the return type; here the name
        ends at the `_`. `demangle_signature` steps over it and `do_type` on nothing
        succeeds, so libiberty prints `foo<int>(int, int)` with no return type -- the
        spelling of the name one character shorter. That name reads here; this one does
        not, because the marker promises a type."""
        assert demangle.demangle(mangled, language="gnuv2") == mangled
        assert demangle.demangle(mangled[:-1], language="gnuv2").startswith("foo<")
        assert demangle.demangle(mangled + "v", language="gnuv2").startswith("void foo<")


class TestAVirtualTableWithACountTooLarge:
    """`gnu_special` reads a virtual table's class as a run of counted pieces, and a
    count larger than what remains is a `.<digits>` static-local marker to it: the
    reference's `break` leaves only the `switch`, the count is dropped, and what follows
    is read as the next piece, so `_vt.6i` is `i virtual table`. Leaving the whole loop
    would leave the `i` for the caller, which would read it as a parameter list and
    spell ` virtual table(int)` -- a blank class and a signature a table does not have.
    `tools/mutate.py --seed 35`."""

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("_vt.6i", "i virtual table"),
            ("_vt.7foo", "foo virtual table"),
            ("_vt.1i", "i virtual table"),
            ("_vt.3foo", "foo virtual table"),
            ("_vt.3foo1i", "fooi virtual table"),
            ("__vt_3foo", "foo virtual table"),
        ],
    )
    def test_the_spelling(self, mangled, expected):
        assert demangle.demangle(mangled, language="gnuv2") == expected


class TestATemplateValueArgumentWithNoTypeInFrontOfIt:
    """`_8_` in a template argument list is the value `8`, and nothing spells its type.

    `demangle_fund_type` in the reference ends its switch on `'\\0'` and `'_'` with a
    bare `break`: an empty fundamental type, successful, integral, and consuming nothing,
    so `demangle_template_value_parm` reads the `_8_` after it with
    `consume_count_with_underscores`. `__opi__t2TA2Z5__pt__8_PFcPv_i` is therefore
    `TA<__pt_, 8>::operator int(int (*)(char, void *))` to it.

    That empty type is refused everywhere else here, and on purpose -- it is one of the
    three shapes that made this scheme claim ordinary C symbols, and
    `drm_intel_gem_bo_map__cpu` is a C function rather than a call taking a
    `__restrict *`. The refusal is lifted only in front of a template *value* argument,
    where no C name can reach: the whole shape sits inside a `t <count> <name> <count>`
    production. Left unread, what follows would be resynchronised as a class name and
    `__opi__t2TA2Z5__pt__1_i` would read as `_::operator int(int)`, naming a class
    called `_` -- an answer this package treats as worse than none. `tools/mutate.py --seed 54`.
    """

    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("__opi__t2TA2Z5__pt__8_PFcPv_i", "TA<__pt_, 8>::operator int(int (*)(char, void *))"),
            ("__opi__t2TA2Z5__pt__1_i", "TA<__pt_, 1>::operator int(int)"),
            ("__opi__t2TA2Z1A_8_i", "TA<A, 8>::operator int(int)"),
            ("__opi__t2TA1_8_i", "TA<8>::operator int(int)"),
            ("f__t2TA2Z1A_8_i", "TA<A, 8>::f(int)"),
            # A value argument whose type *is* spelled.
            ("f__t2TA1i8i", "TA<8>::f(int)"),
        ],
    )
    def test_the_value_is_read(self, mangled, expected):
        assert demangle.demangle(mangled, language="gnuv2") == expected

    def test_the_empty_type_is_still_refused_outside_a_template(self):
        """The shape that made this scheme claim C symbols is untouched."""
        assert demangle.demangle("drm_intel_gem_bo_map__cpu") == "drm_intel_gem_bo_map__cpu"
        assert not gnuv2.detect("drm_intel_gem_bo_map__cpu")
