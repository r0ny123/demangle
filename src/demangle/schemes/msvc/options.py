"""What to leave out of an MSVC spelling.

A decorated name expands to a great deal more than the name: `public: virtual int
__cdecl C::f(int) const` is one identifier and five pieces of declaration around it.
An analyst grepping a binary usually wants fewer of them, which is why
`UnDecorateSymbolName` takes a mask and `llvm-undname` takes flags.

Every field here is a flag `llvm-undname` has, spelled the same way and meaning the same
thing, and `tests/conformance/msvc-llvm-corpus.txt` is replayed under each one. True
keeps the piece, which is what the reference prints by default.

The suppressions apply to the *declaration*, not to every type inside it. That is what
the reference does and it is worth stating, because the alternative reads as a bug:
`--no-calling-convention` over `int (__cdecl * __cdecl fn(void))(int)` drops the
convention `fn` itself carries and keeps the one belonging to the function pointer it
returns, and a parameter of function-pointer type keeps its own throughout.
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


DEFAULT_OPTIONS = MsvcOptions()
