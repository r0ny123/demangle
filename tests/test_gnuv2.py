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

#: How many of the 662 the *default* style claims. The rest are shapes only ARM, Lucid or
#: HP write -- an ARM `__ct` marker, an HP template specialisation -- and GNU's reading
#: refuses them rather than guessing, which is the correct answer: a caller who knows the
#: compiler passes `GnuV2Options(style=...)`, and many of them are read correctly by the
#: CodeWarrior scheme next door, which shares the ARM family's `__ct`/`__dt` convention.
DETECTED_UNDER_GNU = 507

#: How many of the 662 come back as a structured tree -- a name and a parameter list --
#: rather than as one flat `name` part. The rest are the shapes with no argument list to
#: separate: virtual tables, `type_info` nodes, static data members, thunks.
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


class TestBounds:
    """A name from a symbol table nobody here wrote."""

    def test_a_name_that_nests_past_the_bound_is_refused_rather_than_recursed(self):
        deep = "f__F" + "PF" * 300 + "v"
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(deep, language="gnuv2")

    def test_the_empty_name_is_refused(self):
        with pytest.raises(DemanglingError):
            demangle.demangle_strict("", language="gnuv2")
