"""What to leave out of an MSVC spelling.

A decorated name expands to a great deal more than the name: `public: virtual int
__cdecl C::f(int) const` is one identifier and five pieces of declaration around it.
An analyst grepping a binary usually wants fewer of them, which is why
`UnDecorateSymbolName` takes a mask and `llvm-undname` takes flags.

Both references are represented here, and which one defines a field decides how far the
field reaches -- because the two disagree about that, measurably, and neither is wrong.

The five `llvm-undname` flags are *declaration-level*. They apply to the declaration and
to the types written inside it, and stop at the edge of the symbol: a symbol naming a
scope keeps its full spelling, and a function reached as a pointer's pointee keeps the
convention the pointer prints for it. So `--no-calling-convention` over `int (__cdecl *
__cdecl fn(void))(int)` drops the convention `fn` itself carries and keeps the one
belonging to the function pointer it returns.

The four `UnDecorateSymbolName` flags are *lexical*. They reach every occurrence of what
they name, wherever it stands -- inside a template argument, inside a parameter of
function-pointer type, and inside the enclosing symbol a local name is scoped by.
`ms_keywords` over the same name drops both conventions.

True keeps the piece, which is what either reference prints by default.

`tests/conformance/msvc-suppressions.txt` scores the first five against `llvm-undname`,
over the 609 names of `msvc-llvm-corpus.txt`. `tests/conformance/msvc-dbghelp.txt` scores
the last four against `dbghelp.dll`, over the 478 of those names the two references
already spell alike unflagged -- the rest are left out rather than reconciled, because
the two spell a *name* differently and a corpus cannot ask for both houses at once.
"""

from dataclasses import dataclass

__all__ = ["DEFAULT_OPTIONS", "MsvcOptions"]


@dataclass(frozen=True, slots=True)
class MsvcOptions:
    """Which parts of a decorated name's expansion to print."""

    calling_convention: bool = True
    """`__cdecl`, `__stdcall`, `__thiscall`. `llvm-undname --no-calling-convention`."""

    access_specifier: bool = True
    """`public: `, `protected: `, `private: `. `llvm-undname --no-access-specifier`."""

    member_type: bool = True
    """`static ` and `virtual `. `llvm-undname --no-member-type`."""

    return_type: bool = True
    """The type before the name. `llvm-undname --no-return-type`.

    Not the same as dropping a word: a return type wraps *around* the declarator in C,
    so suppressing it means spelling the declaration the way a constructor is spelled --
    convention, name, parameters -- rather than cutting a prefix off the text.
    """

    variable_type: bool = True
    """The type of a data symbol. `llvm-undname --no-variable-type`.

    Only a variable's: a function keeps its return type, and so do the vtable and RTTI
    names, which are not variables however much they look like one.
    """

    ms_keywords: bool = True
    """Every Microsoft keyword. `UnDecorateSymbolName`'s `UNDNAME_NO_MS_KEYWORDS`.

    The calling convention, `__restrict` and `__unaligned` -- and `__ptr64`, which this
    library never prints because `llvm-undname` does not. Not the same cut as
    `calling_convention`: that one is the declaration's own convention, this one is every
    convention in the spelling, so a function-pointer parameter loses its own too.

    `__int64` stays. It names a type rather than qualifying one, and the reference keeps
    it.
    """

    leading_underscores: bool = True
    """The underscores on those keywords. `UNDNAME_NO_LEADING_UNDERSCORES`.

    `__cdecl` becomes `cdecl` and `__restrict` becomes `restrict`, over the same words
    `ms_keywords` drops entirely. What comes out is not C++ -- it is what a linker map
    from the 16-bit era spelled -- and it is here because the mask has it.

    LLVM's `__attribute__((__swiftcall__))` is left alone. The reference spells that
    convention `__swift_1` and strips it to `swift_1`; it has no answer for LLVM's
    spelling, and stripping the underscores off `__attribute__` would invent one.
    """

    this_type: bool = True
    """What a member function writes after its parameter list. `UNDNAME_NO_THISTYPE`.

    The whole group, not just the cv: `const`, `volatile`, `__restrict`, `__unaligned`
    and the `&`/`&&` ref-qualifier all describe the implicit `this`, and the reference
    drops them together.
    """

    tag_kind: bool = True
    """`class`, `struct`, `union`, `enum` before a tag name. `UNDNAME_NO_ECSU`.

    What C++ calls an elaborated type specifier. MSVC writes one on every user-defined
    type it mangles, so a spelling full of them reads as `struct S const *` where the
    source said `S const *`.
    """

    def for_a_scope(self):
        """These options as they apply to a symbol that names a *scope*.

        `?1??f@@YAXXZ@` says "inside the second scope of `void __cdecl f(void)`", and the
        two references disagree about whether a flag reaches that inner symbol.
        `llvm-undname` applies its five to the symbol being named and not to the ones
        saying where it lives; `UnDecorateSymbolName` applies its four to the text
        throughout. Each flag follows the reference that defines it, so what a scope
        keeps is the five and what it loses is the four.
        """
        if self.ms_keywords and self.leading_underscores and self.this_type and self.tag_kind:
            return DEFAULT_OPTIONS
        return MsvcOptions(
            ms_keywords=self.ms_keywords,
            leading_underscores=self.leading_underscores,
            this_type=self.this_type,
            tag_kind=self.tag_kind,
        )

    def keyword(self, word):
        """`word` as this spelling wants it: as written, without underscores, or not at all.

        The one place a Microsoft keyword becomes text, so that `ms_keywords` and
        `leading_underscores` are applied in a single spot rather than at each of the
        places the parser reads one.
        """
        if not word:
            return word
        if not self.ms_keywords:
            return ""
        if self.leading_underscores or not word.startswith("__") or word.startswith("__attribute__"):
            return word
        return word.lstrip("_")


DEFAULT_OPTIONS = MsvcOptions()
