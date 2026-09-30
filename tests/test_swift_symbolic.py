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
from demangle.schemes.swift.resolve import ContextResolver, Image, _fragment_from_symbol, elf_image
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
    resolved in turn; one that names another without end must not recurse until the
    interpreter gives up and `demangle_symbolic` lets the `RecursionError` out."""

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


class TestAnAnonymousContext:
    """A type declared inside a function has for its context a descriptor that names
    nothing. The runtime's `_buildDemanglingForContext` spells it by its pointer
    identity, `(unknown context at $<hex>)`, and gives it no generic arguments of its
    own; the same here, with the descriptor's virtual address.
    """

    def build(self):
        import struct

        blob = bytearray(0x100)
        strings = 0x80

        def put(at, values):
            blob[at : at + 4 * len(values)] = b"".join(struct.pack("<i", v) for v in values)

        blob[strings : strings + 5] = b"demo\0"
        blob[strings + 5 : strings + 11] = b"Point\0"
        put(0x10, [0x00000000, 0, strings - (0x10 + 8)])
        # anonymous: flags kind 2, parent the module, no name
        put(0x30, [0x00000002, 0x10 - (0x30 + 4)])
        put(0x20, [0x00000011, 0x30 - (0x20 + 4), (strings + 5) - (0x20 + 8)])
        return ContextResolver(Image([(0x1000, bytes(blob))]))

    def test_the_fragment_is_the_anonymous_context_production(self):
        assert self.build().fragment(0x1020) == "4demo5$1030yXZ5PointV"

    def test_it_spells_as_the_runtime_spells_it(self):
        """`$s4demo5$1030yXZ5PointVD` is what `swift-demangle` prints for the fragment."""
        resolver = self.build()
        spelled = demangle_symbolic(b"\x01\x00\x00\x00\x00", lambda ref, at: resolver.fragment(0x1020))
        assert spelled == "demo.(unknown context at $1030).Point"

    def test_generic_arguments_pass_through_it_to_the_enclosing_type(self):
        """A real typeref from the 6.1.2 runtime: `_Buffer`, declared inside a method of
        the generic `LockedState<State>`, bound with `State = ()`. The mangling carries
        two lists, one per declaration, and an anonymous context is not one: the
        reference text demangler takes its identifier for its parent and refuses, while
        the runtime's builder hands the arguments on to `LockedState`.
        """
        fragment = "20FoundationEssentials11LockedStateV7$5084e8yXZ7_BufferC"
        spelled = demangle_symbolic(bytes.fromhex("02494a0a007979745f47"), lambda ref, at: fragment)
        assert spelled == "FoundationEssentials.LockedState<()>.(unknown context at $5084e8)._Buffer"

    def test_a_module_takes_no_arguments(self):
        """Nothing outside the anonymous context can take them, so the name is refused
        as the reference refuses it."""
        assert (
            demangle_symbolic(bytes.fromhex("02494a0a007979745f47"), lambda ref, at: "4demo5$1018yXZ7_BufferC") is None
        )


class TestAnImportedDescriptor:
    """A shared object's indirect references point at pointer slots the loader fills, and
    a slot filled from *another* image holds nothing in the file. The relocation names
    the symbol, and a descriptor's symbol is its own mangling with a suffix."""

    @pytest.mark.parametrize(
        ("symbol", "fragment"),
        [
            ("$s4demo5PointVMn", "4demo5PointV"),
            ("_$s4demo5PointVMn", "4demo5PointV"),  # Mach-O's leading underscore
            ("$s4demo5PointVMn@@SWIFT_6", "4demo5PointV"),  # a symbol version
            ("$sSSMn", "SS"),
            ("$ss5ErrorMp", "s5ErrorP"),  # a protocol needs its letter put back
            ("$s20FoundationEssentials11FormatStyleMp", "20FoundationEssentials11FormatStyleP"),
            ("$sSHMp", "SH"),  # already a type: `SHP` is nothing
            ("$sScAMp", "ScA"),
            ("$s5SwiftMXM", "5Swift"),
            ("$s4main1fQryFQOMQ", "4main1fQryFQO"),  # an opaque type descriptor
            ("$sSiN", None),  # a metadata symbol, not a descriptor
            ("$sMn", None),
            ("_ZN4demo5PointE", None),
        ],
    )
    def test_the_fragment_a_symbol_carries(self, symbol, fragment):
        assert _fragment_from_symbol(symbol) == fragment

    def test_an_empty_slot_is_answered_by_its_import(self):
        # An indirect reference whose target slot holds zero; the import names it.
        blob = bytes(0x20)
        image = Image([(0x1000, blob)], imports={0x1010: "$ss5ErrorMp"})
        resolver = ContextResolver(image)
        # kind 2: indirect context; the offset field sits at 1, the slot 0x1010 is
        # 0x0F past it.
        raw = b"\x02\x0f\x00\x00\x00_p"
        assert demangle_symbolic(raw, lambda ref, at: resolver(ref, 0x1001 + ref.at - 1)) == "Swift.Error"

    def test_an_empty_slot_with_no_import_declines(self):
        resolver = ContextResolver(Image([(0x1000, bytes(0x20))]))
        assert demangle_symbolic(b"\x02\x0f\x00\x00\x00_p", lambda ref, at: resolver(ref, 0x1001)) is None

    def test_an_imported_parent_stands_where_the_walk_would_have_gone_on(self):
        """A nested type whose enclosing type lives in another image: the chain stops
        at a parent slot the loader would fill, and the import's mangling is the parent's.
        """
        import struct

        blob = bytearray(0x100)
        blob[0x80:0x86] = b"Inner\0"
        # struct: kind 17, parent an *indirect* relative pointer (low bit) to the slot at
        # 0x40, which holds zero; name at 0x80
        blob[0x20:0x2C] = b"".join(struct.pack("<i", v) for v in [0x11, (0x40 - 0x24) | 1, 0x80 - 0x28])
        image = Image([(0x1000, bytes(blob))], imports={0x1040: "$s4demo5OuterVMn"})
        assert ContextResolver(image).fragment(0x1020) == "4demo5OuterV5InnerV"


class TestADescriptorTheWalkCannotSpell:
    """A type declared in an extension has the extension descriptor for its parent, and
    an opaque type descriptor has no name: the walk declines both. The symbol the image
    defines at that address is the fragment, when the image kept its symbols."""

    def build(self, symbols):
        import struct

        blob = bytearray(0x100)
        blob[0x80:0x89] = b"Encoding\0"
        # extension: kind 1, no parent read; struct: kind 17, parent the extension
        struct.pack_into("<Ii", blob, 0x10, 0x01, 0)
        struct.pack_into("<Iii", blob, 0x20, 0x11, 0x10 - 0x24, 0x80 - 0x28)
        return ContextResolver(Image([(0x1000, bytes(blob))], symbols=symbols))

    def test_the_symbol_stands_in_for_the_walk(self):
        resolver = self.build({0x1020: "$sSS20FoundationEssentialsE8EncodingVMn"})
        assert resolver.fragment(0x1020) == "SS20FoundationEssentialsE8EncodingV"
        spelled = demangle_symbolic(b"\x01\x00\x00\x00\x00", lambda ref, at: resolver.fragment(0x1020))
        assert spelled == "(extension in FoundationEssentials):Swift.String.Encoding"

    def test_without_a_symbol_the_walk_still_declines(self):
        assert self.build({}).fragment(0x1020) is None

    def test_an_opaque_type_descriptor_is_its_declaration(self):
        """A real typeref shape: the descriptor, then `y_Qo_`, the opaque type's index."""
        resolver = self.build({0x1020: "$s4main1fQryFQOMQ"})
        raw = bytes.fromhex("0200000000795f516f5f")
        spelled = demangle_symbolic(raw, lambda ref, at: resolver.fragment(0x1020))
        assert spelled == "<<opaque return type of main.f() -> some>>.0"


class TestReadingAnElfImage:
    """A shared object assembled by hand: one `PT_LOAD`, one `PT_DYNAMIC`, and a
    relocation table holding a `RELATIVE` entry, a `GLOB_DAT` against a symbol the file
    defines, and one against a symbol it imports."""

    def build(self):
        import struct

        # Layout, all in one segment at virtual 0x1000 = file offset 0:
        #   0x000 ELF header, 0x040 two program headers
        #   0x100 dynamic section, 0x180 .rela.dyn (three entries)
        #   0x200 .dynsym (three entries: null, a defined symbol, an import), 0x260 .dynstr
        #   0x300 three pointer slots
        #   0x380 three section headers: null, .dynsym, .dynstr
        data = bytearray(0x480)
        struct.pack_into("<Q", data, 0x28, 0x380)  # e_shoff
        struct.pack_into("<HH", data, 0x3A, 0x40, 3)  # e_shentsize, e_shnum
        struct.pack_into("<IIQQQQIIQQ", data, 0x3C0, 0, 11, 0, 0x1200, 0x200, 3 * 24, 2, 0, 8, 24)
        struct.pack_into("<IIQQQQIIQQ", data, 0x400, 0, 3, 0, 0x1260, 0x260, 0x40, 0, 0, 1, 0)
        data[0:4] = b"\x7fELF"
        data[4] = 2  # 64-bit
        data[5] = 1  # little-endian
        struct.pack_into("<H", data, 0x12, 62)  # x86-64
        struct.pack_into("<Q", data, 0x20, 0x40)  # e_phoff
        struct.pack_into("<H", data, 0x36, 0x38)  # e_phentsize
        struct.pack_into("<H", data, 0x38, 2)  # e_phnum
        # PT_LOAD: offset 0, vaddr 0x1000, filesz 0x400
        struct.pack_into("<IIQQQQQQ", data, 0x40, 1, 5, 0, 0x1000, 0x1000, 0x480, 0x480, 0x1000)
        # PT_DYNAMIC: offset 0x100, vaddr 0x1100, filesz 0x80
        struct.pack_into("<IIQQQQQQ", data, 0x78, 2, 6, 0x100, 0x1100, 0x1100, 0x80, 0x80, 8)
        dynamic = [(7, 0x1180), (8, 3 * 24), (9, 24), (6, 0x1200), (11, 24), (5, 0x1260), (0, 0)]
        for index, (tag, value) in enumerate(dynamic):
            struct.pack_into("<QQ", data, 0x100 + 16 * index, tag, value)
        # symbol 1: `$s4demo5OuterVMn`, defined at 0x1020; symbol 2: imported
        data[0x260 : 0x260 + 1] = b"\0"
        data[0x261 : 0x261 + 17] = b"$s4demo5OuterVMn\0"
        data[0x272 : 0x272 + 12] = b"$ss5ErrorMp\0"
        struct.pack_into("<IBBHQQ", data, 0x200 + 24, 1, 0x12, 0, 7, 0x1020, 0)
        struct.pack_into("<IBBHQQ", data, 0x200 + 48, 0x12, 0x12, 0, 0, 0, 0)
        # relocations: RELATIVE at 0x1300 with addend 0x1234; GLOB_DAT at 0x1308 against
        # symbol 1; GLOB_DAT at 0x1310 against symbol 2
        struct.pack_into("<QQq", data, 0x180, 0x1300, 8, 0x1234)
        struct.pack_into("<QQq", data, 0x198, 0x1308, (1 << 32) | 6, 0)
        struct.pack_into("<QQq", data, 0x1B0, 0x1310, (2 << 32) | 6, 0)
        return elf_image(bytes(data))

    def test_a_relative_slot_takes_its_addend(self):
        assert self.build().read(0x1300, 8) == (0x1234).to_bytes(8, "little")

    def test_a_slot_against_a_defined_symbol_takes_its_address(self):
        assert self.build().read(0x1308, 8) == (0x1020).to_bytes(8, "little")

    def test_a_slot_against_an_import_stays_empty_and_is_named(self):
        image = self.build()
        assert image.read(0x1310, 8) == bytes(8)
        assert image.imports == {0x1310: "$ss5ErrorMp"}

    def test_the_defined_descriptor_symbols_are_read_from_the_section_headers(self):
        assert self.build().symbols == {0x1020: "$s4demo5OuterVMn"}

    def test_an_image_without_a_dynamic_section_has_no_imports(self):
        import struct

        data = bytearray(0x80)
        data[0:4] = b"\x7fELF"
        data[4] = 2
        data[5] = 1
        struct.pack_into("<Q", data, 0x20, 0x40)
        struct.pack_into("<H", data, 0x36, 0x38)
        struct.pack_into("<H", data, 0x38, 1)
        struct.pack_into("<IIQQQQQQ", data, 0x40, 1, 5, 0, 0x1000, 0x1000, 0x80, 0x80, 0x1000)
        image = elf_image(bytes(data))
        assert image.imports == {}
        assert image.symbols == {}
        assert image.read(0x1000, 4) == b"\x7fELF"
