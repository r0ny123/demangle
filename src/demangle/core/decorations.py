"""Decorations a symbol table adds around a mangled name.

A symbol in a real binary is often not just a mangled name. The linker and the compiler
both append things to it, and none of those additions belong to any mangling scheme --
they are conventions of the object format and the toolchain:

    _ZNSt10moneypunctIcLb0EE2idE@@GLIBCXX_3.4     ELF symbol versioning
    _ZN3Foo3barEv.cold                             a cold-path clone
    _ZN3Foo3barEv.part.0                           a partial inlining clone
    _ZN3Foo3barEv.llvm.1234567890                  an LLVM internalisation suffix
    _ZN3Foo3barEv.constprop.0                      a constant-propagation clone

Handling these in `core` rather than in each parser matters because they are orthogonal
to the grammar: a scheme should not have to know about ELF, and a new scheme gets this
for free. The reference demanglers pass them through verbatim, and so do we -- the
suffix identifies *which* copy of a function a symbol is, which is information a caller
usually wants to keep.
"""

#: The character introducing an ELF version suffix. `@@` marks the default version of a
#: symbol and `@` a non-default one; both are appended after the mangled name.
#:
#: Only schemes that never use `@` themselves may split on it. MSVC decorated names are
#: full of `@` -- it is their scope separator -- so this must never be applied to them,
#: which is why the plugin has to opt in rather than this being done for everything.
VERSION_SEPARATOR = "@"

#: Compiler-generated clone suffixes, all introduced by a dot. The dot cannot appear in
#: an Itanium mangled name, so finding one is unambiguous.
CLONE_SEPARATOR = "."


def split_decorations(name):
    """Split `name` into (mangled, decoration).

    The decoration is returned with its separator intact, so joining is concatenation
    and nothing has to remember which separator was found. A name with no decoration
    comes back with an empty second element.
    """
    cut = len(name)
    version = name.find(VERSION_SEPARATOR)
    if 0 < version < cut:
        cut = version
    clone = name.find(CLONE_SEPARATOR)
    if 0 < clone < cut:
        cut = clone
    if cut == len(name):
        return name, ""
    return name[:cut], name[cut:]


def describe(decoration):
    """A readable spelling of a decoration, or the decoration itself.

    GCC's clone suffixes are spelled out by the reference demanglers -- `.cold` becomes
    ` [clone .cold]` -- while ELF version suffixes are passed through as written.
    """
    if decoration.startswith(CLONE_SEPARATOR):
        return f" [clone {decoration}]"
    return decoration
