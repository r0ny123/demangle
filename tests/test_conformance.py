"""Replay the checked-in corpora.

Pass counts are pinned as exact numbers rather than floors. A floor lets a fix that
silently breaks something else slip through as long as the net stays positive; an exact
number means every change in either direction has to be an explicit edit to this file,
with a reason in the commit that makes it.

These tests need neither a compiler nor the reference demanglers: the expected output is
recorded in the corpus files. Regenerate them with tools/generate_corpus.py.
"""

import pytest

import demangle
from demangle.core.errors import DemanglingError

from .conftest import CONFORMANCE, load_corpus

# Measured against llvm-cxxfilt 18.1.3 and GNU c++filt 2.42 on the checked-in corpora.
ITANIUM_LLVM_TOTAL, ITANIUM_LLVM_EXACT = 280, 280
ITANIUM_GNU_TOTAL, ITANIUM_GNU_EXACT = 300, 295
MSVC_TOTAL, MSVC_EXACT = 609, 609
LIBSTDCXX_TOTAL, LIBSTDCXX_EXACT = 5913, 5913
REGRESSIONS_TOTAL, REGRESSIONS_EXACT = 31, 31
RUST_TOTAL, RUST_EXACT = 5316, 5316
RUST_TOOLCHAIN_TOTAL, RUST_TOOLCHAIN_EXACT = 394, 394

#: Go is pinned like the rest, but what it is pinned *against* is different: there is no
#: reference demangler for Go, so the expected column is this library's own decoding and
#: the count alone would be a record of agreeing with itself. What makes it mean
#: something is the round-trip property in tests/test_go.py -- re-escaping a decoded path
#: must reproduce the bytes Go's own PathToPrefix wrote. The count pins the corpus; the
#: property is the correctness argument.
GO_TOTAL, GO_EXACT = 1498, 1498

#: D, against GNU binutils' D demangler over the shipped libgphobos and libgdruntime.
D_TOTAL, D_EXACT = 1257, 1257

#: Free Pascal. Like Go and Nim there is no reference demangler, so the expected column
#: is this library's own reading and the count alone would be a record of agreeing with
#: itself. What makes it mean something is the re-assembly property in
#: tests/test_pascal.py -- checked over all 236,570 readable symbols in the shipped
#: runtime, not just this sample -- and the independent check against `ppudump`.
PASCAL_TOTAL, PASCAL_EXACT = 3899, 3899

#: Nim, against the name the compiler itself recorded in its `.ndi` debug-mapping files.
#: Unlike the others this is not a reference demangler -- Nim has none -- so the count is
#: agreement with the compiler's own record, and the correctness *property* lives in
#: tests/test_nim.py: what this reads must re-mangle to the symbol.
NIM_TOTAL, NIM_EXACT = 2115, 2115

#: Swift, against `swift-demangle` 5.10.1. The corpus is a stratified sample of the
#: shipped runtime and Foundation -- up to four symbols per distinct set of
#: demangling-tree node kinds -- plus every current-mangling case from the compiler's own
#: test/Demangle/Inputs/manglings.txt that the reference itself can read, in both the
#: current mangling and Swift 3's.
SWIFT_TOTAL, SWIFT_EXACT = 8494, 8494

#: Objective-C. No reference demangler exists, so the expected column is what the
#: *declaration* said: every symbol here was emitted by clang 18.1.3 for Objective-C this
#: package wrote, or read out of the shipped GCC runtime, libobjc.a. Three ABIs are
#: represented. The mangling's own losses are named rather than counted, in
#: tests/conformance/objc-lossy.txt.
OBJC_TOTAL, OBJC_EXACT = 2665, 2665

#: Delphi/C++Builder. No compiler on this platform, so the reference is a recorded one:
#: `tests/conformance/delphi-tdump.txt` is a dump of the real `tdump.exe -q -um` over the
#: export tables of real BPLs and C++Builder DLLs, and its expected column is what
#: Embarcadero's own unmangler printed. Both files are replayed here -- the sample
#: because it is the one a reader will open, the whole table because the sample is not
#: the measurement. The 10 MSVC `@name@N` decorations sitting in the same tables are
#: refused, and are pinned in `tests/conformance/delphi-refusals.txt`.
DELPHI_TOTAL, DELPHI_EXACT = 68, 68
DELPHI_TABLE_TOTAL, DELPHI_TABLE_EXACT = 11363, 11363
#: The constructs those export tables never produce -- a quarter of the parser, which the
#: whole dump leaves unexercised. Hand-built, and corroborated against an independent
#: implementation rather than against TDUMP, which is weaker evidence and kept in its own
#: file so it cannot be mistaken for the row above. See the file's header.
DELPHI_CONSTRUCT_TOTAL, DELPHI_CONSTRUCT_EXACT = 53, 53

# The GNU shortfalls are not ours to fix: in each, the two references disagree about
# what goes in the substitution table, not about how to spell it. Matching both would
# mean two incompatible parses of the same bytes, so we follow LLVM and pin the
# disagreements by name -- a count alone would let one be traded for a new defect.
#: Names where `llvm-undname` discards part of the symbol, so following it would mean
#: spelling distinct symbols identically. It reads the first element of a vftable's base
#: path and drops the rest, mapping `??_7A@B@@6BC@D@@@`, `??_7A@B@@6BC@D@@E@F@@@` and
#: `??_7A@B@@6BC@D@@E@F@@G@H@@@` -- three different vtables -- onto one spelling. Checked
#: against llvm-undname 16, 18 and 20; all three lose it identically. We keep the whole
#: path, joined the way Microsoft spells one.
UNDNAME_DIVERGENCES = [
    "??_7A@B@@6BC@D@@E@F@@@",
    "??_7A@B@@6BC@D@@E@F@@G@H@@@",
    "??_7A@@6BB@@C@@@",
    "??_7A@@6BB@@C@@D@@@",
]

GNU_DIVERGENCES = [
    # Inside a requires-clause, llvm records the template parameter symbolically (`T`)
    # and GNU records the argument bound to it.
    "_ZN6modern8measuredINSt7__cxx1112basic_stringIcSt11char_traitsIcESaIcEEEQ5SizedIT_EEEmRKS7_",
    # A generic lambda's `operator()`: llvm resolves the back-reference to the lambda's
    # declared parameter, GNU to the argument `operator()` was instantiated with.
    "_ZZN6modern13genericLambdaEvENKUlTyT_E_clIiEEDaS0_",
    # An argument list whose last argument is an empty pack. GNU omits the space it
    # otherwise puts between two closing angle brackets, and it is a bookkeeping slip
    # rather than a rule: libiberty decides on `d_last_char`, a field it updates on
    # every append and does *not* restore when it rewinds the buffer to drop the
    # separator in front of an argument that printed nothing -- so the character it
    # tests is that separator's space. The same output shows both spellings in one
    # name: `f<A<B<C>>, JE>` comes out `void f<A<B<C> >>(A<B<C> >)`. We follow LLVM,
    # which spaces neither. 5,633 of the 264,008 readable symbols in the shipped
    # libLLVM, libclang-cpp and libstdc++ differ from GNU on this alone.
    "_Z1fI1AI1BEJEEvT_",
    "_Z1fI1AI1BI1CEEJEEvT_",
    "_ZSt5asyncISt8functionIFvvEEJEESt6futureINSt15__invoke_resultINSt5decayIT_E4typeEJDpNS5_IT0_E4typeEEE4typeEESt6launchOS6_DpOS9_",
]


def _score(corpus, style, language=None):
    pairs = load_corpus(corpus)
    exact = 0
    for mangled, expected in pairs:
        try:
            got = demangle.demangle_strict(mangled, style=style, language=language)
        except (DemanglingError, RecursionError):
            continue
        exact += got == expected
    return len(pairs), exact


def test_itanium_matches_llvm_cxxfilt():
    total, exact = _score("itanium-real-world.txt", "llvm")
    assert (total, exact) == (ITANIUM_LLVM_TOTAL, ITANIUM_LLVM_EXACT)


def test_itanium_matches_gnu_cxxfilt():
    total, exact = _score("itanium-real-world-gnu.txt", "gnu")
    assert (total, exact) == (ITANIUM_GNU_TOTAL, ITANIUM_GNU_EXACT)


def test_go_corpus():
    """See `GO_TOTAL` for what this does and does not establish."""
    total, exact = _score("go-real-world.txt", "llvm", language="go")
    assert (total, exact) == (GO_TOTAL, GO_EXACT)


def test_d_matches_gnu_dlang_demangler():
    total, exact = _score("d-real-world.txt", "llvm", language="d")
    assert (total, exact) == (D_TOTAL, D_EXACT)


def test_swift_matches_swift_demangle():
    total, exact = _score("swift-real-world.txt", "llvm", language="swift")
    assert (total, exact) == (SWIFT_TOTAL, SWIFT_EXACT)


def test_pascal_corpus():
    """See `PASCAL_TOTAL` for what this does and does not establish."""
    total, exact = _score("pascal-real-world.txt", "llvm", language="pascal")
    assert (total, exact) == (PASCAL_TOTAL, PASCAL_EXACT)


def test_nim_matches_the_compilers_own_record():
    """See `NIM_TOTAL` for what this does and does not establish."""
    total, exact = _score("nim-real-world.txt", "llvm", language="nim")
    assert (total, exact) == (NIM_TOTAL, NIM_EXACT)


def test_msvc_matches_llvm_undname():
    total, exact = _score("msvc-llvm-corpus.txt", "llvm", language="msvc")
    assert (total, exact) == (MSVC_TOTAL, MSVC_EXACT)


def test_matches_llvm_cxxfilt_on_the_system_libstdcxx():
    """Every mangled symbol the shipped libstdc++ exports.

    Real released C++ rather than something compiled for the test, which is the point:
    it carries what only a real standard library produces.
    """
    total, exact = _score("itanium-libstdcxx.txt", "llvm")
    assert (total, exact) == (LIBSTDCXX_TOTAL, LIBSTDCXX_EXACT)


def test_regression_corpus():
    """Names that each exposed a distinct defect. Every one must stay fixed."""
    total, exact = _score("itanium-regressions.txt", "llvm")
    assert (total, exact) == (REGRESSIONS_TOTAL, REGRESSIONS_EXACT)


def test_rust_matches_rustc_demangle():
    """Auto-detected, not forced.

    Forcing `language="rust"` would skip detection, which is the part most likely to be
    wrong: legacy Rust mangling *is* Itanium mangling, so a detection miss hands the
    name to the C++ parser and yields a plausible but quite wrong spelling rather than
    an error.
    """
    total, exact = _score("rust-real-world.txt", "llvm")
    assert (total, exact) == (RUST_TOTAL, RUST_EXACT)


def test_rust_matches_the_shipped_toolchain():
    """Symbols from rustc's own libraries, including ELF-versioned ones."""
    total, exact = _score("rust-toolchain.txt", "llvm")
    assert (total, exact) == (RUST_TOOLCHAIN_TOTAL, RUST_TOOLCHAIN_EXACT)


def test_objc_matches_what_the_declaration_said():
    """Auto-detected, not forced.

    The forms that matter here are the ones shaped like ordinary C identifiers -- the
    `_i_`/`_c_` method mangling above all -- so what has to hold is that detection
    reaches them without claiming anything else.
    """
    total, exact = _score("objc-real-world.txt", "llvm")
    assert (total, exact) == (OBJC_TOTAL, OBJC_EXACT)


def test_delphi_matches_embarcadero_unmangle():
    """See `DELPHI_TOTAL` for what this does and does not establish."""
    total, exact = _score("delphi-real-world.txt", "llvm")
    assert (total, exact) == (DELPHI_TOTAL, DELPHI_EXACT)


def test_delphi_whole_export_tables_match_the_dump():
    """The sample is not the measurement. The measurement is the whole TDUMP dump.

    This replays it. The assertion it replaced compared a constant to itself, so it
    could not fail and never read the dump at all -- while the parser it was standing
    for diverged from that reference on 692 of these names.
    """
    total, exact = _score("delphi-tdump.txt", "llvm")
    assert (total, exact) == (DELPHI_TABLE_TOTAL, DELPHI_TABLE_EXACT)


def test_delphi_constructs_absent_from_the_export_tables():
    """See `DELPHI_CONSTRUCT_TOTAL` for what this does and does not establish."""
    total, exact = _score("delphi-constructs.txt", "llvm")
    assert (total, exact) == (DELPHI_CONSTRUCT_TOTAL, DELPHI_CONSTRUCT_EXACT)


def test_gnu_shortfalls_are_only_the_known_reference_divergences():
    """Pin *which* names fail under GNU, not merely how many."""
    failing = []
    for mangled, expected in load_corpus("itanium-real-world-gnu.txt"):
        try:
            got = demangle.demangle_strict(mangled, style="gnu")
        except (DemanglingError, RecursionError):
            failing.append(mangled)
            continue
        if got != expected:
            failing.append(mangled)
    assert failing == GNU_DIVERGENCES


@pytest.mark.parametrize(
    "corpus",
    [
        "itanium-real-world.txt",
        "msvc-llvm-corpus.txt",
        "itanium-libstdcxx.txt",
        "rust-real-world.txt",
        "rust-toolchain.txt",
        "objc-real-world.txt",
    ],
)
def test_best_effort_never_raises_on_any_corpus_name(corpus):
    """Whatever the corpus holds, `demangle()` answers rather than raising."""
    for mangled, _ in load_corpus(corpus):
        assert isinstance(demangle.demangle(mangled), str)


def test_llvm_undname_loses_a_vftable_base_path_and_we_do_not():
    """The divergence is information loss on their side, not a spelling preference.

    Pinned as a behaviour rather than a note: if this library ever starts agreeing with
    `llvm-undname` here, it has started throwing the same information away.
    """
    assert demangle.demangle("??_7A@B@@6BC@D@@E@F@@@") == "const B::A::`vftable'{for `D::C's `F::E'}"
    assert demangle.demangle("??_7A@B@@6BC@D@@E@F@@G@H@@@") == "const B::A::`vftable'{for `D::C's `F::E's `H::G'}"
    # The property that matters, independent of spelling: one symbol, one meaning. Adding
    # a base path element must change the answer, or the demangler is losing the element.
    family = ["??_7A@B@@6BC@D@@@", "??_7A@B@@6BC@D@@E@F@@@", "??_7A@B@@6BC@D@@E@F@@G@H@@@"]
    assert len({demangle.demangle(name) for name in family}) == len(family)


def _every_corpus():
    """Every conformance file, so a corpus added later is covered without an edit."""
    return sorted(path.name for path in CONFORMANCE.glob("*.txt"))


@pytest.mark.parametrize("corpus", _every_corpus())
def test_the_tree_spells_what_the_fast_path_spells(corpus):
    """The two builders must never disagree, over every name recorded here.

    `tests/test_api.py` checks this on five hand-picked names, and five names cannot
    find a disagreement that needs a particular shape to appear. One did: a declarator
    applied to a pack with no members renders to nothing, and the tree builder measures
    a subtree rather than rendering it -- so it reported a width for a parameter that
    had dropped out, and the separator was left behind:

        f(std::launch, std::function<void ()>&&, )

    One name, in one corpus, out of 44,556. That is the size of corpus the property
    needs; running it costs a few seconds.
    """
    for mangled, _ in load_corpus(corpus):
        try:
            tree = demangle.parse(mangled)
        except demangle.DemanglingError:
            continue
        assert tree.spell() == demangle.demangle(mangled), mangled


class TestAgainstLibcxxabisOwnCorpus:
    """LLVM's own Itanium vectors -- the reference measuring itself.

    `DemangleTestCases.inc` is what `libcxxabi`'s demangler is tested against, and that
    demangler is the code behind `llvm-cxxfilt`. 29,928 pairs, an order of magnitude more
    than anything this project had assembled, and the flagship scheme's real score
    against it.

    Checked in gzipped and pinned, so the number can only go up and cannot quietly stop
    being accurate. What still fails is grouped in the ROADMAP rather than left as one
    number.
    """

    #: Raised as gaps close; never lowered silently. A drop means a vector that used to
    #: pass has stopped, which is a regression whatever the total.
    EXPECTED_EXACT = 29830

    def _score(self):
        return sum(
            1 for mangled, expected in load_corpus("itanium-libcxxabi.txt") if demangle.demangle(mangled) == expected
        )

    def test_the_score_has_not_gone_backwards(self):
        total = len(load_corpus("itanium-libcxxabi.txt"))
        assert total > 29000, "corpus did not load; this test would prove nothing"
        assert self._score() >= self.EXPECTED_EXACT

    def test_the_pinned_number_is_still_accurate(self):
        assert self._score() == self.EXPECTED_EXACT

    def test_nothing_in_it_is_answered_with_nothing(self):
        """Whatever it cannot read, it hands back -- never a blank."""
        for mangled, _ in load_corpus("itanium-libcxxabi.txt"):
            assert demangle.demangle(mangled) != ""

    def test_the_tree_agrees_with_the_text_throughout(self):
        """29,928 names is the size at which declarator placement disagreements show up."""
        for mangled, _ in load_corpus("itanium-libcxxabi.txt"):
            try:
                tree = demangle.parse(mangled)
            except demangle.DemanglingError:
                continue
            assert tree.spell() == demangle.demangle(mangled), mangled
