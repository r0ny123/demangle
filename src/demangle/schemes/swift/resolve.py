"""Resolving a symbolic reference, which needs the image the name came out of.

A symbolic reference is an offset from itself to a descriptor somewhere else in the same
binary, so reading one means reading the binary. That is why it is here rather than in
the demangler: `demangle()` is given a name and nothing else, and no amount of cleverness
recovers what the name deliberately does not carry.

What a resolver has to produce is a *mangled fragment* in Swift's own grammar --
`4demo5PointV` for `demo.Point` -- which the demangler then reads in place. Handing back
a fragment rather than a finished spelling is what keeps the rest of the mangling
working: the reference may be the base of a bound generic type, or a member the next
operator qualifies, and only a real node can be used that way.

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

import re
import struct

from .symbolic import CONTEXT, DIRECT, INDIRECT

__all__ = ["ContextResolver", "Image", "MalformedImage", "elf_image", "macho_image"]

_U32 = struct.Struct("<I")
_I32 = struct.Struct("<i")
_U64 = struct.Struct("<Q")
_U16 = struct.Struct("<H")

#: `ContextDescriptorKind`, `include/swift/ABI/MetadataValues.h`. Only kinds that can
#: parent a nominal type; anything else stops the walk.
_MODULE = 0
#: "This component is the module": a module has no letter, and no kind produces "".
_MODULE_LETTER = ""
_EXTENSION = 1
_ANONYMOUS = 2
#: Stands for "this component is an anonymous context" in a built chain; see `fragment`.
_ANONYMOUS_MARK = "XZ"
_PROTOCOL = 3

_TYPE_LETTERS = {16: "C", 17: "V", 18: "O"}

#: Bit 2 of a type descriptor's kind-specific flags (high half): extended import info.
_HAS_IMPORT_INFO = 1 << 2

#: `_buildDemanglingForContext` treats a C `typedef` as a `TypeAlias`, mangled `a`.
_TYPE_ALIAS = "a"
_C_TYPE_DEFINITION = "t"


class Image:
    """A flat view of a loaded binary: virtual address in, bytes out.

    Deliberately the smallest thing a resolver can be given. A caller with a memory dump,
    a debugger connection or an object format this package does not read supplies its own
    `read`, and everything else here works unchanged.
    """

    __slots__ = ("_segments", "imports", "symbols")

    def __init__(self, segments, imports=None, symbols=None):
        #: `(address, bytes)` pairs; the first that contains an address answers for it.
        self._segments = tuple(segments)
        #: Symbols the loader would fill slots from, by slot address: the file lacks the
        #: address, but a descriptor symbol's mangled name is all a resolver needs.
        self.imports = dict(imports or {})
        #: Descriptor symbols the image defines, by address: the fallback for a
        #: descriptor the layout alone cannot spell (in an extension, or opaque).
        self.symbols = dict(symbols or {})

    def read(self, address, length):
        """`length` bytes at virtual `address`, or None if they are not all mapped."""
        if length < 0:
            return None
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


class MalformedImage(ValueError):
    """The file is not a well-formed image of the kind it claims to be.

    A `ValueError` rather than whatever the arithmetic happened to raise. These loaders
    read a file the calling tool did not produce -- the same threat model as a mangled
    name -- so a truncated or hostile header has to arrive as a refusal a caller can
    catch, not as an `IndexError` from a header field or a `struct.error` from an offset
    that pointed past the end.
    """


def _field(unpacker, data, at, what):
    """Read one header field, or say which one was not there."""
    try:
        return unpacker.unpack_from(data, at)[0]
    except struct.error:
        raise MalformedImage(f"truncated before {what} at offset {at}") from None


def elf_image(data):
    """An `Image` over a 64-bit little-endian ELF file's PT_LOAD segments.

    Raises `MalformedImage` -- a `ValueError` -- for anything that is not one, including
    a file too short to hold the header it claims.
    """
    if len(data) < 0x40 or data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise MalformedImage("not a 64-bit little-endian ELF file")
    program_offset = _field(_U64, data, 0x20, "the program header offset")
    entry_size = _field(_U16, data, 0x36, "the program header entry size")
    count = _field(_U16, data, 0x38, "the program header count")
    if count and entry_size < 0x38:
        raise MalformedImage(f"ELF program header entry claims {entry_size} bytes; at least 56 required")
    segments = []
    dynamic = None
    for index in range(count):
        at = program_offset + index * entry_size
        # A truncated image (core dump, partial download) is common: answer from what
        # is there.
        if at + 0x38 > len(data):
            break
        kind = _U32.unpack_from(data, at)[0]
        offset = _U64.unpack_from(data, at + 0x08)[0]
        filesz = _U64.unpack_from(data, at + 0x20)[0]
        if kind == 2:  # PT_DYNAMIC
            dynamic = data[offset : offset + filesz]
        if kind != 1:  # PT_LOAD
            continue
        vaddr = _U64.unpack_from(data, at + 0x10)[0]
        segments.append((vaddr, bytearray(data[offset : offset + filesz])))
    imports = _apply_dynamic_relocations(data, segments, dynamic) if dynamic else {}
    return Image([(vaddr, bytes(blob)) for vaddr, blob in segments], imports, _defined_descriptors(data))


#: `(RELATIVE, GLOB_DAT, ABS64)` by `e_machine`. The last two fill a slot with a symbol's
#: address, which the file lacks for an imported symbol.
_RELOCATIONS_BY_MACHINE = {62: (8, 6, 1), 183: (1027, 1025, 257)}  # x86-64, AArch64


def _segment_slice(segments, address, length):
    """The segment and offset holding `length` bytes at `address`, or None."""
    for vaddr, blob in segments:
        offset = address - vaddr
        if offset >= 0 and offset + length <= len(blob):
            return blob, offset
    return None


def _apply_dynamic_relocations(data, segments, dynamic):
    """Fill in the pointer slots the loader would fill, and name the ones it cannot.

    An *indirect* symbolic reference points at a pointer to its descriptor, and in a
    shared object that pointer is not in the file: the slot holds zero and an entry in
    `.rela.dyn` says what the loader writes there. Read off the disk, every such
    reference is unresolved -- 3,526 of the Swift 6.1.2 runtime's typerefs, a
    third of those holding a reference at all. This walks `DT_RELA` and does what the
    loader does for the three kinds that need no other image: a `RELATIVE` slot takes
    its addend, and a slot relocated by a symbol the file defines takes that symbol's
    address. A slot relocated by a symbol the file *imports* is returned by address with
    the symbol's name, because the name is a mangled fragment in its own right.
    """
    machine = _U16.unpack_from(data, 0x12)[0]
    kinds = _RELOCATIONS_BY_MACHINE.get(machine)
    if kinds is None:
        return {}
    relative, glob_dat, abs64 = kinds
    tags: dict[int, int] = {}
    for at in range(0, len(dynamic) - 15, 16):
        tag = _U64.unpack_from(dynamic, at)[0]
        if tag == 0:  # DT_NULL
            break
        tags.setdefault(tag, _U64.unpack_from(dynamic, at + 8)[0])
    # DT_RELA 7, DT_RELASZ 8, DT_RELAENT 9; DT_SYMTAB 6, DT_SYMENT 11; DT_STRTAB 5
    if 7 not in tags or 8 not in tags:
        return {}
    entry = tags.get(9, 24)
    if entry < 24:
        raise MalformedImage(f"ELF relocation entry claims {entry} bytes; at least 24 required")
    table = _segment_slice(segments, tags[7], tags[8])
    if table is None:
        return {}
    symbols = _segment_slice(segments, tags[6], 0) if 6 in tags else None
    strings = _segment_slice(segments, tags[5], 0) if 5 in tags else None
    symbol_size = tags.get(11, 24)
    if symbol_size < 24:
        raise MalformedImage(f"ELF symbol entry claims {symbol_size} bytes; at least 24 required")
    imports = {}
    blob, start = table
    for at in range(start, start + tags[8] - entry + 1, entry):
        r_offset = _U64.unpack_from(blob, at)[0]
        r_info = _U64.unpack_from(blob, at + 8)[0]
        addend = _U64.unpack_from(blob, at + 16)[0]
        kind = r_info & 0xFFFFFFFF
        if kind == relative:
            value = addend
        elif kind in (glob_dat, abs64) and symbols is not None and strings is not None:
            index = r_info >> 32
            symbol = symbols[1] + index * symbol_size
            if symbol + symbol_size > len(symbols[0]):
                continue
            st_name = _U32.unpack_from(symbols[0], symbol)[0]
            st_shndx = _U16.unpack_from(symbols[0], symbol + 6)[0]
            st_value = _U64.unpack_from(symbols[0], symbol + 8)[0]
            if st_shndx != 0 and st_value:
                value = (st_value + addend) & 0xFFFFFFFFFFFFFFFF
            else:
                if addend == 0:
                    stop = strings[0].find(b"\0", strings[1] + st_name)
                    if stop > 0:
                        imports[r_offset] = strings[0][strings[1] + st_name : stop].decode("latin-1")
                continue
        else:
            continue
        slot = _segment_slice(segments, r_offset, 8)
        if slot is not None:
            slot[0][slot[1] : slot[1] + 8] = _U64.pack(value)
    return imports


def _defined_descriptors(data):
    """The descriptor symbols an ELF file defines, by address.

    Read from the section headers rather than the dynamic section, because `.symtab`
    is not loaded and holds what `.dynsym` does not: the descriptors of `internal`
    types, which are not exported. A file with no section headers, or one stripped of
    both tables, gives an empty map, and the walk is on its own.
    """
    if len(data) < 0x40:
        return {}
    table_offset = _U64.unpack_from(data, 0x28)[0]
    entry_size = _U16.unpack_from(data, 0x3A)[0]
    count = _U16.unpack_from(data, 0x3C)[0]
    if not table_offset or not count:
        return {}
    if entry_size < 0x40:
        raise MalformedImage(f"ELF section header entry claims {entry_size} bytes; at least 64 required")
    headers = []
    for index in range(count):
        at = table_offset + index * entry_size
        if at + 0x40 > len(data):
            break
        kind = _U32.unpack_from(data, at + 0x04)[0]
        offset = _U64.unpack_from(data, at + 0x18)[0]
        size = _U64.unpack_from(data, at + 0x20)[0]
        link = _U32.unpack_from(data, at + 0x28)[0]
        symbol_size = _U64.unpack_from(data, at + 0x38)[0]
        headers.append((kind, offset, size, link, symbol_size))
    symbols = {}
    for kind, offset, size, link, symbol_size in headers:
        if kind not in (11, 2) or link >= len(headers):  # SHT_DYNSYM, SHT_SYMTAB
            continue
        if symbol_size < 24:
            raise MalformedImage(f"ELF symbol entry claims {symbol_size} bytes; at least 24 required")
        _, strings_offset, strings_size, _, _ = headers[link]
        strings = data[strings_offset : strings_offset + strings_size]
        table = data[offset : offset + size]
        for at in range(0, len(table) - symbol_size + 1, symbol_size):
            st_shndx = _U16.unpack_from(table, at + 6)[0]
            st_value = _U64.unpack_from(table, at + 8)[0]
            if not st_shndx or not st_value or st_value in symbols:
                continue
            st_name = _U32.unpack_from(table, at)[0]
            stop = strings.find(b"\0", st_name)
            if stop <= st_name or strings[st_name : st_name + 2] not in (b"$s", b"$S", b"_$"):
                continue
            name = strings[st_name:stop].decode("latin-1")
            if _fragment_from_symbol(name) is not None:
                symbols[st_value] = name
    return symbols


#: Descriptor symbols named as a context's own mangling plus a suffix: nominal type,
#: protocol, module, opaque type (`$s4main1fQryFQOMQ` is `4main1fQryFQO` + `MQ`).
_DESCRIPTOR_SUFFIXES = ("Mn", "Mp", "MXM", "MQ")

#: A standard-library protocol abbreviation (`SH`, `ScA`), already a type on its own.
_STANDARD_PROTOCOL = re.compile(r"Sc?[A-Za-z]")


def _fragment_from_symbol(symbol):
    """The mangled fragment a descriptor's symbol name carries, or None.

    `$s4demo5PointVMn` names the nominal type descriptor of `demo.Point`, and
    `4demo5PointV` is what a reference to it stands for. Only the three descriptor
    kinds a context can be are read; a symbol version and Mach-O's leading underscore
    are stripped first.

    A protocol descriptor's name has no type letter of its own -- `$ss5ErrorMp` is
    `s5Error`, a module and an identifier -- so the letter is put back: `s5ErrorP` is
    the protocol as a type, which is what the `_p` or `Qz` that follows a reference to
    one expects to find. Not for a protocol the standard library abbreviates, `$sSHMp`:
    `SH` is a type already, and `SHP` is nothing.
    """
    name = symbol.split("@", 1)[0]
    if name.startswith("_$"):
        name = name[1:]
    if not name.startswith(("$s", "$S")):
        return None
    body = name[2:]
    for suffix in _DESCRIPTOR_SUFFIXES:
        if body.endswith(suffix) and len(body) > len(suffix):
            fragment = body[: -len(suffix)]
            if suffix == "Mp" and not _STANDARD_PROTOCOL.fullmatch(fragment):
                fragment += "P"
            return fragment
    return None


#: Smallest a load command can be. A command claiming less would not advance the walk,
#: and a header claiming four billion of them would spin forever.
_MIN_LOAD_COMMAND = 8


def macho_image(data):
    """An `Image` over a 64-bit Mach-O file's LC_SEGMENT_64 commands.

    A fat binary is not unwrapped: pick the slice first. Only the little-endian 64-bit
    magic is accepted, which is every Mach-O anyone demangles Swift out of.

    Raises `MalformedImage` for anything else, including a header whose command count or
    command sizes do not fit the file.
    """
    if len(data) < 0x20 or data[:4] != b"\xcf\xfa\xed\xfe":
        raise MalformedImage("not a 64-bit little-endian Mach-O file")
    commands = _field(_U32, data, 0x10, "the load command count")
    command_bytes = _field(_U32, data, 0x14, "the load command region size")
    if commands > command_bytes // _MIN_LOAD_COMMAND:
        raise MalformedImage("load command count does not fit the declared command region")
    command_end = 0x20 + command_bytes
    at = 0x20
    segments = []
    for _ in range(commands):
        if at + _MIN_LOAD_COMMAND > len(data):
            break
        command, size = struct.unpack_from("<II", data, at)
        if size < _MIN_LOAD_COMMAND:
            raise MalformedImage(f"load command at offset {at} claims {size} bytes")
        if at + size > command_end:
            raise MalformedImage(f"load command at offset {at} extends beyond the declared command region")
        if command == 0x19:  # LC_SEGMENT_64
            if size < 0x48:
                raise MalformedImage(f"segment command at offset {at} claims {size} bytes; at least 72 required")
            if at + 0x48 > len(data):
                break  # Preserve the complete segments of a partially downloaded image.
            sections = _U32.unpack_from(data, at + 0x40)[0]
            if size < 0x48 + sections * 0x50:
                raise MalformedImage(f"segment command at offset {at} cannot hold its {sections} sections")
            vmaddr = _U64.unpack_from(data, at + 0x18)[0]
            fileoff = _U64.unpack_from(data, at + 0x28)[0]
            filesize = _U64.unpack_from(data, at + 0x30)[0]
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
            slot = target
            target = int.from_bytes(raw, "little")
            if not target:
                # An empty slot is filled from another image; that symbol names the fragment.
                imported = self._imported(slot)
                return imported
        elif reference.directness != DIRECT:
            return None
        return self.fragment(target)

    def _imported(self, slot):
        """The fragment for a pointer slot an import would fill, or None."""
        imports = getattr(self._image, "imports", None)
        if not imports:
            return None
        symbol = imports.get(slot)
        return _fragment_from_symbol(symbol) if symbol else None

    def fragment(self, descriptor):
        """The mangled fragment naming the context descriptor at `descriptor`.

        The walk over the descriptors comes first, because it needs nothing but the
        layout and is what a stripped image still has. Where it declines -- a type
        declared in an extension, whose parent is an extension descriptor; an opaque
        type descriptor, which has no name of its own -- the symbol the image defines
        at that address is the fragment, when the image kept its symbols.
        """
        found = self._walk(descriptor)
        if found is not None:
            return found
        symbols = getattr(self._image, "symbols", None)
        symbol = symbols.get(descriptor) if symbols else None
        return _fragment_from_symbol(symbol) if symbol else None

    def _walk(self, descriptor):
        chain = []
        seen = set()
        at = descriptor
        prefix = None
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
            if kind == _ANONYMOUS:
                # An anonymous context: `_buildDemanglingForContext` spells it
                # `(unknown context at $<address>)`; the descriptor's address stands in.
                chain.append((_ANONYMOUS_MARK, f"${at:x}".encode("ascii")))
                at, prefix = self._parent(at + 4)
                if prefix is not None:
                    break
                continue
            identity = self._identity(at + 8, header[8:12], flags)
            if identity is None:
                return None
            name, namespace = identity
            if kind == _MODULE:
                chain.append((_MODULE_LETTER, name))
                break
            if kind == _PROTOCOL:
                chain.append(("P", name))
                at, prefix = self._parent(at + 4)
                if prefix is not None:
                    break
                continue
            letter = _TYPE_ALIAS if namespace == _C_TYPE_DEFINITION else _TYPE_LETTERS.get(kind)
            if letter is None:
                # An extension (its spelling is a mangled name inside the descriptor) or
                # an unknown kind: declining beats writing the wrong one.
                return None
            chain.append((letter, name))
            at, prefix = self._parent(at + 4)
            if prefix is not None:
                break
        else:
            return None

        if prefix is None and (not chain or chain[-1][0] != _MODULE_LETTER):
            return None
        pieces = [prefix] if prefix else []
        for letter, name in reversed(chain):
            try:
                identifier = name.decode("utf-8")
            except UnicodeDecodeError:
                return None
            # The descriptor stores UTF8 bytes. The demangler accepts their plain
            # byte-counted spelling as well as the compiler's punycoded spelling.
            pieces.append(f"{len(name)}{identifier}")
            if letter == _ANONYMOUS_MARK:
                # The anonymous-context production, with no generic arguments to collect.
                pieces.append("yXZ")
            elif letter != _MODULE_LETTER:
                pieces.append(letter)
        return "".join(pieces)

    def _identity(self, address, raw, flags):
        """`(name, symbol namespace)` for a descriptor, as `ParsedTypeIdentity` reads it.

        A Clang-imported type carries more than its name. After the name's NUL come
        further NUL-terminated components, ended by an empty one, each tagged by its
        first character; `N` gives the *ABI* name, which is the one the runtime mangles,
        and `S` gives the symbol namespace, whose value `t` means the descriptor came
        from a C `typedef`. Without this, `__C.CFArrayRef` would read as `__C.CFArray`
        -- the user-facing name, which the compiler does not use in a symbol.
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
        """Follow a relative *indirectable* pointer: the low bit says which it is.

        Returns `(address, None)` for a parent in this image, `(None, fragment)` for one
        the loader would import -- the symbol's mangling is the parent's -- and
        `(None, None)` for one that cannot be followed.
        """
        raw = self._image.read(address, 4)
        if raw is None:
            return None, None
        (offset,) = _I32.unpack_from(raw, 0)
        if offset == 0:
            return None, None
        if offset & 1:
            target = address + (offset & ~1)
            pointer = self._image.read(target, self._pointer_size)
            if pointer is None:
                return None, None
            parent = int.from_bytes(pointer, "little")
            if parent:
                return parent, None
            return None, self._imported(target)
        return address + offset, None
