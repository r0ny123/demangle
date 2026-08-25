"""Symbolic references: the part of the Swift mangling that points into the binary.

A mangled name in a Swift binary's *metadata* is not always self-contained. Where a name
would have to spell a type the image already describes, the compiler writes a symbolic
reference instead: one byte saying what kind of thing is referred to and how, then a
four-byte signed offset relative to itself. The bytes are transcribed from
`Demangler.cpp::demangleSymbolicReference` in the Swift 5.10 sources.

    01  a context descriptor, directly
    02  a context descriptor, through a pointer
    09  an accessor function that yields the entity when run
    0A  a unique extended existential type shape
    0B  a non-unique extended existential type shape
    03-08, 0C  reserved, and refused: the reference itself refuses them
    FF  alignment padding in front of a reference, and skipped

Two consequences fall out of the encoding and both matter to a reader:

*   **A metadata blob cannot be split on NUL.** The four-byte offset is arbitrary bytes
    and very often contains a zero, so finding where a mangled name ends means parsing
    it. `end_of_name` does exactly that and nothing else.
*   **A name holding one is not text.** It is bytes, so the entry points here take
    `bytes` where the rest of the package takes `str`.

Resolving a reference needs the image the name came out of, which is why this is a
separate entry point rather than something `demangle()` could do: see `resolve.py` for a
resolver that reads one, and `demangle_symbolic` for the parse that uses it.
"""

import struct

__all__ = [
    "ACCESSOR_FUNCTION",
    "CONTEXT",
    "DIRECT",
    "INDIRECT",
    "KINDS",
    "NON_UNIQUE_EXTENDED_EXISTENTIAL_TYPE_SHAPE",
    "PADDING",
    "RESERVED",
    "UNIQUE_EXTENDED_EXISTENTIAL_TYPE_SHAPE",
    "SymbolicReference",
    "end_of_name",
    "read",
    "scan",
]

#: What a reference points at.
CONTEXT = "context"
ACCESSOR_FUNCTION = "accessor function"
UNIQUE_EXTENDED_EXISTENTIAL_TYPE_SHAPE = "unique extended existential type shape"
NON_UNIQUE_EXTENDED_EXISTENTIAL_TYPE_SHAPE = "non-unique extended existential type shape"

#: Whether the offset reaches the entity or a pointer to it.
DIRECT = "direct"
INDIRECT = "indirect"

#: Introducer byte -> `(kind, directness)`. Exactly the reference's switch, including
#: which values it leaves out: 3 through 8 are reserved for protocol- and
#: associated-conformance descriptors and are *not* emitted, and 0x0C reaches the
#: switch only to fall through its default.
KINDS = {
    0x01: (CONTEXT, DIRECT),
    0x02: (CONTEXT, INDIRECT),
    0x09: (ACCESSOR_FUNCTION, DIRECT),
    0x0A: (UNIQUE_EXTENDED_EXISTENTIAL_TYPE_SHAPE, DIRECT),
    0x0B: (NON_UNIQUE_EXTENDED_EXISTENTIAL_TYPE_SHAPE, DIRECT),
}

#: Introducer bytes the grammar accepts and the reference then refuses.
RESERVED = frozenset({0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x0C})

#: Every byte that begins a symbolic reference, refused or not.
INTRODUCERS = frozenset(KINDS) | RESERVED

#: Written in front of a reference where the platform's relocations need the offset
#: aligned. It carries nothing and is skipped.
PADDING = 0xFF

#: The offset is a 32-bit signed little-endian integer, and it is relative to its own
#: first byte -- not to the introducer, and not to the end of the field.
_OFFSET = struct.Struct("<i")
OFFSET_WIDTH = _OFFSET.size


class SymbolicReference:
    """One symbolic reference, as the bytes described it.

    `at` is where the *offset field* begins, because that is what the offset is relative
    to: the address a resolver wants is `address_of(at) + offset`. Keeping the position
    rather than the resolved address is what lets this module know nothing about images.
    """

    __slots__ = ("at", "directness", "kind", "offset", "raw_kind")

    def __init__(self, raw_kind, kind, directness, offset, at):
        self.raw_kind = raw_kind
        self.kind = kind
        self.directness = directness
        self.offset = offset
        self.at = at

    @property
    def reserved(self):
        """Whether this is one of the introducers the reference declines to read."""
        return self.kind is None

    def __repr__(self):  # pragma: no cover - debugging aid
        kind = self.kind or f"reserved 0x{self.raw_kind:02x}"
        return f"SymbolicReference({kind}, {self.directness}, {self.offset:+d}, at={self.at})"

    def __eq__(self, other):
        if not isinstance(other, SymbolicReference):
            return NotImplemented
        return (self.raw_kind, self.offset, self.at) == (other.raw_kind, other.offset, other.at)

    def __hash__(self):
        return hash((self.raw_kind, self.offset, self.at))


def read(data, pos):
    """Read the reference whose introducer is at `data[pos]`.

    Returns `(reference, position after it)`. Raises `ValueError` if `pos` does not hold
    an introducer, or if the four offset bytes are not all there -- which the reference
    also treats as failure rather than reading a short integer.
    """
    if pos < 0 or pos >= len(data):
        raise ValueError(f"no byte at {pos}")
    raw = data[pos]
    if raw not in INTRODUCERS:
        raise ValueError(f"0x{raw:02x} does not introduce a symbolic reference")
    start = pos + 1
    if start + OFFSET_WIDTH > len(data):
        raise ValueError("truncated symbolic reference")
    (offset,) = _OFFSET.unpack_from(data, start)
    kind, directness = KINDS.get(raw, (None, None))
    return SymbolicReference(raw, kind, directness, offset, start), start + OFFSET_WIDTH


def scan(data):
    """Every symbolic reference in `data`, in order.

    Reading rather than searching: a byte inside an offset can look like an introducer,
    and a name that has been walked properly is the only way to know it is not one.
    """
    found = []
    pos = 0
    length = len(data)
    while pos < length:
        byte = data[pos]
        if byte in INTRODUCERS:
            reference, pos = read(data, pos)
            found.append(reference)
        else:
            pos += 1
    return tuple(found)


def end_of_name(data, start=0):
    """Where the NUL-terminated mangled name beginning at `start` ends.

    The index returned is of the terminating NUL, or of the end of `data` if the name
    runs to it. This exists because the obvious thing does not work: a reference's
    four-byte offset is arbitrary bytes and very often holds a zero, so `data.index(0)`
    cuts a name in half. Swift's own reflection reader walks the name for the same
    reason.
    """
    pos = start
    length = len(data)
    while pos < length:
        byte = data[pos]
        if byte == 0:
            return pos
        if byte in INTRODUCERS:
            if pos + 1 + OFFSET_WIDTH > length:
                # Truncated. The name cannot be read, and neither can the one after it,
                # so the whole remainder is this name.
                return length
            pos += 1 + OFFSET_WIDTH
        else:
            pos += 1
    return length


def names(data):
    """Split a metadata blob of NUL-terminated mangled names into those names.

    Empty entries are dropped: a section is padded with them.
    """
    found = []
    pos = 0
    length = len(data)
    while pos < length:
        stop = end_of_name(data, pos)
        if stop > pos:
            found.append(data[pos:stop])
        pos = stop + 1
    return tuple(found)
