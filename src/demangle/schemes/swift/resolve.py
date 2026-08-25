"""Resolving a symbolic reference, which needs the image the name came out of.

A symbolic reference is an offset from itself to a descriptor somewhere else in the same
binary, so reading one means reading the binary. That is why it is here rather than in
the demangler: `demangle()` is given a name and nothing else, and no amount of cleverness
recovers what the name deliberately does not carry.

What a resolver has to produce is a *mangled fragment* in Swift's own grammar -- `4demo5PointV`
for `demo.Point` -- which the demangler then reads in place. Handing back a fragment
rather than a finished spelling is what keeps the rest of the mangling working: the
reference may be the base of a bound generic type, or a member the next operator
qualifies, and only a real node can be used that way.

The descriptor layout is Swift's `TargetContextDescriptor` and the two that extend it:

    uint32  Flags        low five bits are the kind
    int32   Parent       a relative *indirectable* pointer: low bit set means
                         "the offset reaches a pointer to the descriptor"
    int32   Name         a relative direct pointer to a NUL-terminated name;
                         present on module and on nominal type descriptors alike

Walking `Parent` to the module and writing each name back out with its length is the
whole of it. The check that it is right is that the fragment demangles to the same name
the type carries in the symbol table.
"""

import struct

from .symbolic import CONTEXT, DIRECT, INDIRECT

__all__ = ["ContextResolver", "Image", "elf_image", "macho_image"]

_U32 = struct.Struct("<I")
_I32 = struct.Struct("<i")
_U64 = struct.Struct("<Q")

#: `ContextDescriptorKind`, from `include/swift/ABI/MetadataValues.h`. Only the kinds
#: that can appear in the parent chain of a nominal type are named; anything else stops
#: the walk, because a fragment this cannot spell is worse than no fragment at all.
_MODULE = 0
#: Stands for "this component is the module" in a built chain. A module carries no letter
#: after its name, and a marker no descriptor kind can produce keeps that distinct.
_MODULE_LETTER = ""
_EXTENSION = 1
_ANONYMOUS = 2
_PROTOCOL = 3

#: Type kinds, and the letter each is written with in a mangled name.
_TYPE_LETTERS = {16: "C", 17: "V", 18: "O"}

#: A type descriptor's kind-specific flags live in the high half of the flags word, and
#: bit 2 of them says the name is followed by extended import information.
_HAS_IMPORT_INFO = 1 << 2

#: The letter a Clang-imported `typedef` is written with. `_buildDemanglingForContext`
#: overrides the kind to `TypeAlias` when the symbol namespace says the descriptor came
#: from a C type definition, and `a` is how a type alias is mangled.
_TYPE_ALIAS = "a"
_C_TYPE_DEFINITION = "t"


class Image:
    """A flat view of a loaded binary: virtual address in, bytes out.

    Deliberately the smallest thing a resolver can be given. A caller with a memory dump,
    a debugger connection or an object format this package does not read supplies its own
    `read`, and everything else here works unchanged.
    """

    __slots__ = ("_segments",)

    def __init__(self, segments):
        #: `(address, bytes)` pairs, in no particular order. Overlaps are the caller's
        #: business; the first that contains an address answers for it.
        self._segments = tuple(segments)

    def read(self, address, length):
        """`length` bytes at virtual `address`, or None if they are not all mapped."""
        for start, blob in self._segments:
            offset = address - start
            if offset >= 0 and offset + length <= len(blob):
                return blob[offset : offset + length]
        return None

    def cstring(self, address, limit=1024):
        """The NUL-terminated string at `address`, or None."""
        for start, blob in self._segments:
            offset = address - start
            if 0 <= offset < len(blob):
                stop = blob.find(b"\0", offset, offset + limit)
                if stop < 0:
                    return None
                return blob[offset:stop]
        return None


def elf_image(data):
    """An `Image` over a 64-bit little-endian ELF file's PT_LOAD segments."""
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError("not a 64-bit little-endian ELF file")
    (program_offset,) = _U64.unpack_from(data, 0x20)
    (entry_size,) = struct.unpack_from("<H", data, 0x36)
    (count,) = struct.unpack_from("<H", data, 0x38)
    segments = []
    for index in range(count):
        at = program_offset + index * entry_size
        (kind,) = _U32.unpack_from(data, at)
        if kind != 1:  # PT_LOAD
            continue
        (offset,) = _U64.unpack_from(data, at + 0x08)
        (vaddr,) = _U64.unpack_from(data, at + 0x10)
        (filesz,) = _U64.unpack_from(data, at + 0x20)
        segments.append((vaddr, data[offset : offset + filesz]))
    return Image(segments)


def macho_image(data):
    """An `Image` over a 64-bit Mach-O file's LC_SEGMENT_64 commands.

    A fat binary is not unwrapped: pick the slice first. Only the little-endian 64-bit
    magic is accepted, which is every Mach-O anyone demangles Swift out of.
    """
    if data[:4] not in (b"\xcf\xfa\xed\xfe",):
        raise ValueError("not a 64-bit little-endian Mach-O file")
    (commands,) = _U32.unpack_from(data, 0x10)
    at = 0x20
    segments = []
    for _ in range(commands):
        (command, size) = struct.unpack_from("<II", data, at)
        if command == 0x19:  # LC_SEGMENT_64
            (vmaddr,) = _U64.unpack_from(data, at + 0x18)
            (fileoff,) = _U64.unpack_from(data, at + 0x28)
            (filesize,) = _U64.unpack_from(data, at + 0x30)
            segments.append((vmaddr, data[fileoff : fileoff + filesize]))
        at += size
    return Image(segments)


class ContextResolver:
    """Resolves `context` references by walking the descriptor they point at.

    The other kinds are not resolved and are not guessed at. An accessor-function
    reference names a function that has to be *run* to say what it yields, which is not
    something a file can be asked; the two existential-shape kinds describe a shape
    rather than name a context, and spelling one would mean inventing a name the mangling
    never had.
    """

    __slots__ = ("_image", "_pointer_size")

    def __init__(self, image, pointer_size=8):
        self._image = image
        self._pointer_size = pointer_size

    def __call__(self, reference, address):
        """The mangled fragment for `reference`, whose offset field is at `address`.

        Returns None when the reference cannot be followed, which the demangler treats
        exactly as the reference treats a resolver returning null: the name is refused
        rather than half-read.
        """
        if reference.kind != CONTEXT:
            return None
        target = address + reference.offset
        if reference.directness == INDIRECT:
            raw = self._image.read(target, self._pointer_size)
            if raw is None:
                return None
            target = int.from_bytes(raw, "little")
            if not target:
                return None
        elif reference.directness != DIRECT:
            return None
        return self.fragment(target)

    def fragment(self, descriptor):
        """The mangled fragment naming the context descriptor at `descriptor`."""
        chain = []
        seen = set()
        at = descriptor
        while at:
            if at in seen:
                # A parent chain that loops is a corrupt image, not a name.
                return None
            seen.add(at)
            header = self._image.read(at, 12)
            if header is None:
                return None
            (flags,) = _U32.unpack_from(header, 0)
            kind = flags & 0x1F
            identity = self._identity(at + 8, header[8:12], flags)
            if identity is None:
                return None
            name, namespace = identity
            if kind == _MODULE:
                chain.append((_MODULE_LETTER, name))
                break
            if kind == _PROTOCOL:
                # A protocol descriptor's name is never followed by import info, and its
                # own letter is `P`.
                chain.append(("P", name))
                at = self._parent(at + 4)
                continue
            letter = _TYPE_ALIAS if namespace == _C_TYPE_DEFINITION else _TYPE_LETTERS.get(kind)
            if letter is None:
                # An extension or an anonymous context. Each has a spelling of its own
                # that this does not write, and writing the wrong one would be worse
                # than declining: an extension's is its extended type, which is itself a
                # mangled name that would have to be read out of the descriptor.
                return None
            chain.append((letter, name))
            at = self._parent(at + 4)
        else:
            return None

        if not chain or chain[-1][0] != _MODULE_LETTER:
            return None
        pieces = []
        for letter, name in reversed(chain):
            if not name.isascii():
                # Swift spells a non-ASCII identifier in punycode, and the descriptor
                # holds it as raw UTF-8. Writing the bytes out with a length in front
                # would be a mangling nothing can read back, so decline instead.
                return None
            pieces.append(f"{len(name)}{name.decode('ascii')}")
            if letter != _MODULE_LETTER:
                pieces.append(letter)
        return "".join(pieces)

    def _identity(self, address, raw, flags):
        """`(name, symbol namespace)` for a descriptor, as `ParsedTypeIdentity` reads it.

        A Clang-imported type carries more than its name. After the name's NUL come
        further NUL-terminated components, ended by an empty one, each tagged by its
        first character; `N` gives the *ABI* name, which is the one the runtime mangles,
        and `S` gives the symbol namespace, whose value `t` means the descriptor came
        from a C `typedef`. Without this, `__C.CFArrayRef` reads as `__C.CFArray` -- the
        user-facing name, which the compiler does not use in a symbol.
        """
        (offset,) = _I32.unpack_from(raw, 0)
        if offset == 0:
            return None
        at = address + offset
        name = self._image.cstring(at)
        if name is None:
            return None
        if not ((flags >> 16) & _HAS_IMPORT_INFO):
            return name, ""

        namespace = ""
        at += len(name) + 1
        while True:
            component = self._image.cstring(at)
            if component is None:
                return None
            if not component:
                break
            at += len(component) + 1
            tag, rest = component[:1], component[1:]
            if tag == b"N":
                name = rest
            elif tag == b"S":
                namespace = rest.decode("ascii", "replace")
        return name, namespace

    def _parent(self, address):
        """Follow a relative *indirectable* pointer: the low bit says which it is."""
        raw = self._image.read(address, 4)
        if raw is None:
            return None
        (offset,) = _I32.unpack_from(raw, 0)
        if offset == 0:
            return None
        if offset & 1:
            target = address + (offset & ~1)
            pointer = self._image.read(target, self._pointer_size)
            if pointer is None:
                return None
            return int.from_bytes(pointer, "little") or None
        return address + offset
