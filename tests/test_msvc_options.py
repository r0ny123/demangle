"""Printing less of a decorated name.

A decorated name expands to a great deal more than the name -- `public: virtual int
__cdecl C::f(int) const` is one identifier and five pieces of declaration around it --
and every peer tool lets a caller ask for less of it. `UnDecorateSymbolName` takes a
mask; `llvm-undname` takes flags. `MsvcOptions` is those flags, and this is them replayed
against the reference's own answers.

`tests/conformance/msvc-suppressions.txt` records what each flag *changes* over the same
609 names `msvc-llvm-corpus.txt` pins unchanged, so nothing here is this library's own
output.
"""

from typing import ClassVar

import demangle

from .conftest import load_corpus
from .test_conformance import MSVC_SUPPRESSIONS_EXACT, MSVC_SUPPRESSIONS_TOTAL


class TestPrintingLessOfADecoratedName:
    """`llvm-undname`'s five suppression flags, replayed as `MsvcOptions`.

    A decorated name expands to a great deal more than the name, and every peer tool lets
    a caller ask for less of it -- `UnDecorateSymbolName` takes a mask, `llvm-undname`
    takes flags. `tests/conformance/msvc-suppressions.txt` records what each flag changes
    over the same 609 names the corpus next door pins unchanged, so what is checked here
    is the reference's own answer under the reference's own flag.

    The rules are not "drop a word", and three of them are worth stating because they
    look like bugs until you see the reference do them:

    * A function reached as a *pointer's* pointee keeps its calling convention, because
      the pointer prints it rather than the signature. So `--no-calling-convention` over
      `int (__cdecl * __cdecl fn(void))(int)` drops one of the two and keeps the other,
      and a parameter of function-pointer type keeps its own throughout.
    * A symbol naming a *scope* keeps its full spelling: the flags apply to the symbol
      being named, not to the ones saying where it lives.
    * `extern "C" ` goes with `static` and `virtual` rather than with the access
      specifier, so `--no-member-type` drops all three together.
    """

    FLAGS: ClassVar = {
        "no-calling-convention": "calling_convention",
        "no-access-specifier": "access_specifier",
        "no-member-type": "member_type",
        "no-return-type": "return_type",
        "no-variable-type": "variable_type",
    }

    #: The reference truncates these three rather than spelling them, so they are pinned
    #: as divergences instead of copied. See `test_the_shortfall_is_the_references_own`.
    TRUNCATED: ClassVar = frozenset(
        {
            "?memptrtofun7@@3R8B@@EAAP6AHXZXZEQ1@",
            "?memptrtofun8@@3P8B@@EAAR6AHXZXZEQ1@",
            "?memptrtofun9@@3P8B@@EAAQ6AHXZXZEQ1@",
        }
    )

    def _rows(self):
        for mangled, rest in load_corpus("msvc-suppressions.txt"):
            flag, expected = rest.split("\t", 1)
            yield mangled, flag, expected

    def _score(self):
        return sum(
            1
            for mangled, flag, expected in self._rows()
            if demangle.demangle(mangled, style=demangle.style("llvm", msvc={self.FLAGS[flag]: False})) == expected
        )

    def test_the_score_has_not_gone_backwards(self):
        assert len(list(self._rows())) == MSVC_SUPPRESSIONS_TOTAL, "corpus did not load"
        assert self._score() >= MSVC_SUPPRESSIONS_EXACT

    def test_the_pinned_number_is_still_accurate(self):
        assert self._score() == MSVC_SUPPRESSIONS_EXACT

    def test_the_shortfall_is_the_references_own_truncation(self):
        """Three names where `--no-return-type` leaves `llvm-undname` with an open bracket.

        `int (__cdecl * (__cdecl B::*volatile memptrtofun7)(void)` is not a declaration of
        anything -- the reference suppressed the outer return type and lost the `)(void)`
        that closed it. These keep the balanced spelling, which is also what the reference
        itself prints with no flag.
        """
        missed = {
            mangled
            for mangled, flag, expected in self._rows()
            if demangle.demangle(mangled, style=demangle.style("llvm", msvc={self.FLAGS[flag]: False})) != expected
        }
        assert missed == self.TRUNCATED

    def test_every_flag_is_exercised(self):
        """A flag that changed nothing would score a silent 0/0."""
        seen = {flag for _, flag, _ in self._rows()}
        assert seen == set(self.FLAGS)

    def test_the_default_is_to_print_everything(self):
        """Every field defaults True, so the corpus next door still pins the same text."""
        assert demangle.demangle("?g@C@@UEAAXXZ") == "public: virtual void __cdecl C::g(void)"
        assert demangle.demangle("?g@C@@UEAAXXZ", style=demangle.style("llvm")) == (
            "public: virtual void __cdecl C::g(void)"
        )

    def test_the_flags_compose(self):
        narrow = demangle.style("llvm", msvc={"calling_convention": False, "access_specifier": False})
        assert demangle.demangle("?g@C@@UEAAXXZ", style=narrow) == "virtual void C::g(void)"

    def test_the_tree_spells_what_the_text_spells_under_a_flag(self):
        narrow = demangle.style("llvm", msvc={"calling_convention": False})
        for mangled, flag, _ in self._rows():
            if flag != "no-calling-convention":
                continue
            assert demangle.parse(mangled, style=narrow).spell(style=narrow) == demangle.demangle(
                mangled, style=narrow
            ), mangled
