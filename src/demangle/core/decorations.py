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

#: Introduces an ELF version suffix (`@@` default version, `@` non-default). Plugins opt
#: in, since MSVC names use `@` as their scope separator.
VERSION_SEPARATOR = "@"

#: Introduces a clone suffix. A dot can also stand inside a mangled name, so this is
#: how an already-found suffix begins, not somewhere to cut.
CLONE_SEPARATOR = "."


def split_decorations(name):
    """Split `name` into (mangled, version-suffix).

    Only the ELF version suffix is split here, because only it can be recognised
    *before* parsing: `@` appears in no scheme this applies to, so the first one is
    necessarily the start of the suffix.

    Clone suffixes deliberately are not. A `.` is not reserved: GCC names a coroutine's
    frame type `_ZN6modern9coroutineEi.Frame` and writes that as a length-prefixed
    identifier, so the coroutine's actor is
    `_ZN6modern9coroutineEPZNS_9coroutineEiE28_ZN6modern9coroutineEi.Frame.actor` --
    where the first `.` is inside the identifier and only the second begins the clone
    suffix. Cutting at the first `.` truncates the name mid-production. A clone suffix
    can only be recognised as what is *left over* once the grammar has consumed
    everything it can, which means the parser has to do it.

    The suffix is returned with its separator intact, so joining is concatenation.
    """
    version = name.find(VERSION_SEPARATOR)
    if version <= 0:
        return name, ""
    return name[:version], name[version:]


def describe(decoration, gnu_clone_suffix=False):
    """A readable spelling of a decoration.

    ELF version suffixes are passed through as written by both references. Clone
    suffixes are not: llvm-cxxfilt writes `Foo::bar() (.cold)` where GNU c++filt writes
    `Foo::bar() [clone .cold]`. Neither is more correct, so it is a style choice.
    """
    if not decoration.startswith(CLONE_SEPARATOR):
        return decoration
    if not gnu_clone_suffix:
        return f" ({decoration})"
    # GNU spells each clone separately -- `.actor.cold` is two clones, not one -- while
    # a trailing number belongs to the clone before it, so `.part.0` stays whole.
    spelled = []
    for component in decoration.split(CLONE_SEPARATOR)[1:]:
        if component.isdigit() and spelled:
            spelled[-1] += f".{component}"
        else:
            spelled.append(f".{component}")
    return "".join(f" [clone {clone}]" for clone in spelled)
