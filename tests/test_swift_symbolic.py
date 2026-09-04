"""Symbolic references: the part of the Swift mangling that points into the binary.

A mangled name in Swift metadata can hold a one-byte marker and a four-byte offset in
place of a type it would otherwise spell, so reading one needs the image the name came
out of. What is pinned here is the encoding, the refusal without a resolver, and a corpus
of real typerefs recorded from the Swift 5.10.1 runtime -- the bytes, the fragment each
reference resolved to, and the spelling.

Every fragment in that corpus was checked against the symbol the *linker* put at the same
address, with `swift-demangle` reading both sides: 4,528 of 4,528. Neither side of that
check is this library's opinion.
"""

import pathlib

import pytest

from demangle.schemes.swift import demangle_symbolic
from demangle.schemes.swift.resolve import ContextResolver, Image
from demangle.schemes.swift.symbolic import (
    CONTEXT,
    DIRECT,
    INDIRECT,
    KINDS,
    PADDING,
    RESERVED,
    end_of_name,
    names,
    read,
    scan,
)

CORPUS = pathlib.Path(__file__).parent / "conformance" / "swift-symbolic.txt"


def corpus():
    rows = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        raw, pairs, spelled = line.split("\t")
        fragments = {}
        for pair in pairs.split():
            at, _, fragment = pair.partition("=")
            fragments[int(at)] = fragment
        rows.append((bytes.fromhex(raw), fragments, spelled))
    return rows


ROWS = corpus()


class TestTheEncoding:
    """Transcribed from `Demangler.cpp::demangleSymbolicReference`."""

    def test_the_offset_is_signed_little_endian_and_relative_to_itself(self):
        reference, after = read(b"\x01\x17\x02\x00\x00", 0)
        assert (reference.kind, reference.directness) == (CONTEXT, DIRECT)
        assert reference.offset == 0x217
        assert reference.at == 1
        assert after == 5

    def test_a_negative_offset_reads_as_one(self):
        reference, _ = read(b"\x01\xaa\xf4\xff\xff", 0)
        assert reference.offset == -2902

    def test_the_indirect_kind_is_a_separate_byte(self):
        assert KINDS[0x01] == (CONTEXT, DIRECT)
        assert KINDS[0x02] == (CONTEXT, INDIRECT)

    @pytest.mark.parametrize("raw", sorted(RESERVED))
    def test_a_reserved_introducer_is_read_and_then_refused(self, raw):
        """The reference's switch accepts these bytes and falls through its default, so
        the four offset bytes are still consumed -- which is what stops the name after
        one being read as though the reference were not there."""
        reference, after = read(bytes([raw]) + b"\x01\x00\x00\x00", 0)
        assert reference.reserved
        assert after == 5

    def test_a_truncated_reference_is_a_failure_not_a_short_integer(self):
        with pytest.raises(ValueError):
            read(b"\x01\x17\x02", 0)

    def test_padding_is_not_an_introducer(self):
        """It carries nothing and the demangler skips it, but it does occupy a byte, so
        the reference after it sits one further along."""
        assert PADDING not in KINDS
        padded = scan(b"\xff\x01\x17\x02\x00\x00")
        bare = scan(b"\x01\x17\x02\x00\x00")
        assert [(r.kind, r.offset) for r in padded] == [(r.kind, r.offset) for r in bare]
        assert padded[0].at == bare[0].at + 1


class TestFindingWhereANameEnds:
    """The reason this module exists at all, for anyone reading a metadata section."""

    def test_an_offset_holding_a_zero_does_not_end_the_name(self):
        blob = b"\x01\x17\x02\x00\x00Sg\x00"
        assert blob.index(0) == 3
        assert end_of_name(blob) == 7

    def test_splitting_a_section_gives_whole_names(self):
        blob = b"\x01\x17\x02\x00\x00\x00Si\x00\x011\x02\x00\x00\x00"
        assert names(blob) == (b"\x01\x17\x02\x00\x00", b"Si", b"\x011\x02\x00\x00")

    def test_a_truncated_reference_takes_the_rest_of_the_blob(self):
        assert end_of_name(b"ab\x01\x17") == 4

    def test_a_start_outside_the_blob_is_refused_as_read_refuses_it(self):
        """A negative start indexed from the end, or raised `IndexError`; the end itself
        is a fine place to start and finds nothing."""
        for start in (-1, -5, 4):
            with pytest.raises(ValueError):
                end_of_name(b"abc", start=start)
        assert end_of_name(b"abc", start=3) == 3


class TestAResolverThatAnswersItself:
    """A fragment a resolver hands back may hold references of its own, and each is
    resolved in turn; one that names another without end used to recurse until the
    interpreter gave up, and `demangle_symbolic` let the `RecursionError` out."""

    REFERENCE = b"\x01\x00\x00\x00\x00"

    def test_it_is_given_up_on_rather_than_recursed_into(self):
        asked = []

        def loop(reference, at):
            asked.append(at)
            return self.REFERENCE.decode("latin-1")

        assert demangle_symbolic(self.REFERENCE, loop) is None
        assert len(asked) < 200, "stopped by the interpreter's recursion limit, not by a bound"

    def test_a_chain_that_ends_is_followed_to_its_end(self):
        answers = iter([self.REFERENCE.decode("latin-1"), "4main3FooV"])
        assert demangle_symbolic(self.REFERENCE, lambda reference, at: next(answers)) == "main.Foo"


class TestWithoutAResolver:
    """The reference refuses a name it cannot resolve rather than reading round it."""

    def test_a_name_holding_a_reference_is_refused(self):
        assert demangle_symbolic(b"\x01\x17\x02\x00\x00") is None

    def test_a_name_holding_none_reads_as_it_always_did(self):
        assert demangle_symbolic(b"Si") == "Swift.Int"
        assert demangle_symbolic(b"$s4main1fyyxlF") == "main.f<A>(A) -> ()"

    def test_bytes_are_required(self):
        with pytest.raises(TypeError):
            demangle_symbolic("Si")


class TestTheRecordedTyperefs:
    def test_the_corpus_covers_the_shapes(self):
        assert len(ROWS) > 200

    def test_every_recorded_typeref_spells_the_same(self, subtests):
        for raw, fragments, spelled in ROWS:
            with subtests.test(name=raw.hex()):
                assert demangle_symbolic(raw, lambda ref, at, f=fragments: f.get(at)) == spelled

    def test_the_references_are_where_the_corpus_says(self, subtests):
        for raw, fragments, _ in ROWS:
            with subtests.test(name=raw.hex()):
                assert {reference.at for reference in scan(raw)} >= set(fragments)

    def test_a_resolver_that_declines_refuses_the_name(self):
        raw, _, _ = ROWS[0]
        assert demangle_symbolic(raw, lambda reference, at: None) is None


class TestTheResolvedNodeIsARealNode:
    """A fragment rather than a spelling is what lets the rest of the mangling use it."""

    def test_a_reference_can_be_the_base_of_a_bound_generic(self):
        """A real typeref: the generic and both its arguments are references."""
        raw = bytes.fromhex("01e10100007901b7010000011e02000047")
        fragments = {1: "4demo4PairV", 7: "4demo5PointV", 12: "4demo6ColourO"}
        assert demangle_symbolic(raw, lambda ref, at: fragments.get(at)) == ("demo.Pair<demo.Point, demo.Colour>")

    def test_a_reference_registers_as_a_substitution(self):
        """ "Types register as substitutions even when symbolically referenced." So `AA`
        after one refers back to it, and treating a reference as opaque text would leave
        every later back-reference in the name pointing at the wrong thing.

        A real typeref again: a two-element tuple whose second element is `AA`, the
        substitution the reference itself created.
        """
        raw = bytes.fromhex("015dcc01005f414174")
        assert demangle_symbolic(raw, lambda ref, at: "17_StringProcessing7DSLTreeV4AtomO") == (
            "(_StringProcessing.DSLTree.Atom, _StringProcessing.DSLTree.Atom)"
        )


class TestTheImageResolver:
    """The descriptor walk, over an image assembled by hand so the test needs no binary."""

    def build(self):
        # A module descriptor named `demo`, and a struct `Point` whose parent is it.
        # Addresses are chosen so every relative pointer is a small positive number.
        blob = bytearray(0x100)
        strings = 0x80

        def put(at, values):
            import struct

            blob[at : at + 4 * len(values)] = b"".join(struct.pack("<i", v) for v in values)

        blob[strings : strings + 5] = b"demo\0"
        blob[strings + 5 : strings + 11] = b"Point\0"
        # module: flags kind 0, no parent, name at 0x80
        put(0x10, [0x00000000, 0, strings - (0x10 + 8)])
        # struct: flags kind 17, parent at 0x10, name at 0x85
        put(0x20, [0x00000011, 0x10 - (0x20 + 4), (strings + 5) - (0x20 + 8)])
        return ContextResolver(Image([(0x1000, bytes(blob))]))

    def test_it_writes_the_fragment_the_mangling_wants(self):
        assert self.build().fragment(0x1020) == "4demo5PointV"

    def test_the_fragment_reads_back(self):
        resolver = self.build()
        assert demangle_symbolic(b"\x01\x00\x00\x00\x00", lambda ref, at: resolver.fragment(0x1020)) == "demo.Point"

    def test_an_unmapped_address_declines(self):
        assert self.build().fragment(0xDEAD) is None
