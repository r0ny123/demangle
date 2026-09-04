"""Objective-C symbol names.

The corpus in `test_conformance.py` pins the spellings. What is pinned here is each rule
that had to be *found* -- in clang's own sources, or by compiling Objective-C and reading
what came out -- rather than assumed, plus the two places the mangling loses information.
"""

import pathlib

import pytest

import demangle
from demangle.schemes.objc import gnu_method_readings, mangle_gnu_method, parse_objc_symbol
from demangle.schemes.objc._parser import DemangleFailure, decode_type_encoding
from demangle.schemes.objc.nodes import Symbol

CONFORMANCE = pathlib.Path(__file__).parent / "conformance"


def rows(name):
    found = []
    for line in (CONFORMANCE / name).read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            found.append(tuple(line.split("\t")))
    return found


class TestTheFormsClangWrites:
    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            # `mangleObjCMethodName`, the Apple branch: `[-+][Class(Category) selector]`.
            ("-[NSString stringWithFormat:]", "-[NSString stringWithFormat:]"),
            ("+[NSObject alloc]", "+[NSObject alloc]"),
            ("-[NSString(Extra) doThing:with:]", "-[NSString(Extra) doThing:with:]"),
            # The GNU branch of the same function.
            ("_i_NSString__length", "-[NSString length]"),
            ("_c_NSObject__alloc", "+[NSObject alloc]"),
            ("_i_NSString_Extra_doThing_with_", "-[NSString(Extra) doThing:with:]"),
            # `mangleFunctionBlock`, whose outer name is length-prefixed only when it is
            # a method -- `mangleObjCMethodNameAsSourceName` writes the length.
            ("___13-[Root value]_block_invoke", "block #1 in -[Root value]"),
            ("___20-[Root addValue:to:]_block_invoke_2", "block #2 in -[Root addValue:to:]"),
            ("__8_i_K__go_block_invoke", "block #1 in -[K go]"),
            ("___cfunc_block_invoke", "block #1 in cfunc"),
            # `CGObjCMac.cpp`, the non-fragile ABI.
            ("_OBJC_CLASS_$_NSString", "Objective-C class NSString"),
            ("_OBJC_METACLASS_$_NSString", "Objective-C metaclass NSString"),
            ("_OBJC_IVAR_$_NSString._flags", "instance variable offset for NSString._flags"),
            ("_OBJC_EHTYPE_$_NSException", "Objective-C exception type for NSException"),
            ("__OBJC_$_CATEGORY_NSString_$_Extra", "Objective-C category NSString(Extra)"),
            ("__OBJC_$_INSTANCE_METHODS_NSString", "instance method list for NSString"),
            (
                "__OBJC_$_PROTOCOL_INSTANCE_METHODS_OPT_NSCopying",
                "optional instance method list for protocol NSCopying",
            ),
            ("__OBJC_PROTOCOL_$_NSCopying", "Objective-C protocol NSCopying"),
            ("l_OBJC_LABEL_CLASS_$", "Objective-C class list"),
            # The fragile ABI.
            (".objc_class_name_NSString", "Objective-C class NSString"),
            (".objc_category_name_NSString_Extra", "Objective-C category NSString(Extra)"),
            # `CGObjCGNU.cpp`, and GCC's own front end.
            ("__objc_ivar_offset_Root.ivarOne.i", "instance variable offset for Root.ivarOne"),
            ("._OBJC_CLASS_Root", "Objective-C class Root"),
            ("._OBJC_INIT_CLASS_Root", "class initialiser for Root"),
            ("_OBJC_Class_Object", "Objective-C class Object"),
            ("_OBJC_InstanceMethods_Object", "instance method list for Object"),
            ("_OBJC_METH_VAR_NAME_12", "method variable name #12"),
            ("__start___objc_selectors", "start of the Objective-C selectors section"),
            # `CodeGenFunction::EmitBlockLiteral` and `buildBlockDescriptor`.
            ("__block_literal_global", "global block literal"),
            ("__block_literal_global.1", "global block literal #1"),
            ("__block_descriptor", "block descriptor"),
            ("__block_descriptor_32_e5_v8?0l8", "block descriptor"),
        ],
    )
    def test_reading(self, mangled, expected):
        assert parse_objc_symbol(mangled).text == expected

    def test_a_blocks_length_prefix_has_to_match(self):
        """`mangleObjCMethodNameAsSourceName` writes the method's own length in front of
        it. Ignoring the count would read any `__<digits><anything>_block_invoke` as a
        method, and the count is the only thing that says where the method ends."""
        assert parse_objc_symbol("___7-[K go]_block_invoke").text == "block #1 in -[K go]"
        with pytest.raises(DemangleFailure):
            parse_objc_symbol("___8-[K go]_block_invoke")

    def test_the_first_block_has_no_number(self):
        """`mangleFunctionBlock` numbers from the second: discriminator 0 is written
        bare and the rest carry `discriminator + 1`."""
        assert parse_objc_symbol("___7-[K go]_block_invoke").text.startswith("block #1 ")
        assert parse_objc_symbol("___7-[K go]_block_invoke_2").text.startswith("block #2 ")


class TestTypeEncodings:
    """`GetSymbolNameForTypeEncoding` substitutes what an object format will not carry.

    `@` marks a version in an ELF symbol name and `=` breaks lld on Windows, so each is
    written as a control byte "that is not a valid type encoding character (and, being
    non-printable, never will be!)".
    """

    def test_the_object_marker_comes_back(self):
        assert decode_type_encoding("i16\x010:8") == "i16@0:8"

    def test_the_equals_marker_comes_back(self):
        assert decode_type_encoding("{\x02QQ}") == "{=QQ}"

    def test_a_selector_symbol_carries_both(self):
        assert parse_objc_symbol(".objc_sel_types_i16\x010:8").text == "Objective-C type encoding i16@0:8"
        assert parse_objc_symbol(".objc_selector_a:init:_i24\x010:8i16i20").text == (
            "Objective-C selector a:init: with type encoding i24@0:8i16i20"
        )


class TestTheManglingLosesThings:
    """Both losses are clang's, and clang documents the first of them itself."""

    def test_re_mangling_is_what_makes_a_reading_admissible(self):
        assert mangle_gnu_method(False, "NSString", "", "doThing:with:") == "_i_NSString__doThing_with_"
        assert mangle_gnu_method(True, "NSString", "Extra", "length") == "_c_NSString_Extra_length"

    def test_a_method_outside_a_category_leaves_a_doubled_underscore(self):
        """Which is the one structural signal the mangling carries, and why a reading
        needing no category is preferred over one that does."""
        symbol = parse_objc_symbol("_i_Deep_Nested_Name__value")
        assert symbol.text == "-[Deep_Nested_Name value]"
        assert symbol.category is None

    def test_an_ambiguous_name_says_so(self):
        symbol = parse_objc_symbol("_i_A_B_c_d_")
        assert symbol.ambiguous
        assert len(gnu_method_readings("_i_A_B_c_d_")) > 1

    def test_an_unambiguous_name_says_so_too(self):
        assert not parse_objc_symbol("_i_NSString__length").ambiguous

    def test_every_reading_re_mangles(self):
        for name in ("_i_A_B_c_d_", "_i_NSString_Extra_doThing_with_", "_c_A_B662_Store244_z_"):
            for class_name, category, selector in gnu_method_readings(name):
                assert mangle_gnu_method(name[1] == "c", class_name, category or "", selector) == name

    def test_the_recorded_losses_are_exactly_these(self):
        """Named rather than counted. Each row is the declaration clang compiled and the
        reading this library prefers; the point is that the *number* cannot quietly grow.
        """
        recorded = rows("objc-lossy.txt")
        assert len(recorded) == 26
        for mangled, _declared, preferred in recorded:
            assert parse_objc_symbol(mangled).text == preferred
            assert parse_objc_symbol(mangled).ambiguous

    def test_a_category_symbol_with_no_separator_is_refused(self):
        """`.objc_category_` + class + category, with nothing between them."""
        with pytest.raises(DemangleFailure):
            parse_objc_symbol(".objc_category_RootExtra")
        assert demangle.demangle(".objc_category_RootExtra") == ".objc_category_RootExtra"


class TestRefusesRatherThanGuesses:
    def test_it_refuses_exactly_what_is_recorded(self, subtests):
        for (mangled,) in rows("objc-refusals.txt"):
            with subtests.test(name=mangled):
                assert demangle.demangle(mangled) == mangled

    def test_the_runtimes_own_c_functions_are_not_objective_c_names(self):
        for name in ("__objc_exec_class", "__objc_msg_forward", "__objc_init_class_tables"):
            assert demangle.detect(name) is None

    @pytest.mark.parametrize("mangled", ["", "-[", "+[]", "-[Foo", "_i_", "_i_A", "_OBJC_CLASS_$_"])
    def test_it_refuses_the_malformed(self, mangled):
        assert demangle.demangle(mangled) == mangled


class TestRegisteredAsALanguage:
    def test_demangle_reaches_it_without_being_told(self):
        assert demangle.demangle("_OBJC_CLASS_$_NSString") == "Objective-C class NSString"

    def test_naming_the_language_works(self):
        assert demangle.demangle("_i_NSString__length", language="objc") == "-[NSString length]"
        assert demangle.demangle("_i_NSString__length", language="objective-c") == "-[NSString length]"

    def test_the_block_labels_are_claimed_unasked(self):
        """`parse` read these; the detector's screen never mentioned them, so
        auto-detection handed back unchanged what `language="objc"` read."""
        assert demangle.demangle("__block_literal_global") == "global block literal"
        assert demangle.demangle("__block_descriptor_32_e5_v8?0l8") == "block descriptor"

    def test_it_does_not_claim_another_scheme_s_names(self):
        assert demangle.detect("_ZNSt6vectorIiE9push_backERKi") == "itanium"
        assert demangle.detect("$s10Foundation4DataV5countSivg") == "swift"
        assert demangle.detect("_RNvC1a1b") == "rust"


class TestTree:
    def test_the_tree_spells_what_demangle_spells(self):
        for mangled, expected in rows("objc-real-world.txt")[:400]:
            assert demangle.parse(mangled).spell() == expected

    def test_a_method_carries_its_parts(self):
        tree = demangle.parse("_i_NSString_Extra_doThing_with_")
        assert isinstance(tree, Symbol)
        assert tree.spell() == "-[NSString(Extra) doThing:with:]"
        assert tree.objc_kind == "instance method"
        assert tree.runtime == "gnu"
        assert [(node.kind, node.text) for node in tree.children()] == [
            ("name", "NSString"),
            ("path", "Extra"),
            ("name", "doThing:with:"),
        ]

    def test_a_class_symbol_names_its_class(self):
        tree = demangle.parse("_OBJC_CLASS_$_NSString")
        assert isinstance(tree, Symbol)
        assert [node.text for node in tree.find("name")] == ["NSString"]
        assert tree.objc_kind == "class"

    def test_parse_and_demangle_refuse_together(self):
        with pytest.raises(demangle.DemanglingError):
            demangle.demangle_strict(".objc_category_RootExtra")
        with pytest.raises(demangle.DemanglingError):
            demangle.parse(".objc_category_RootExtra")


class TestTheMethodShapeScreen:
    """`_i_`/`_c_` after every strip `_candidates` makes, computed without the list.

    `detect` is offered every symbol in a binary, and asking the question by building
    the candidate list and running a generator over it was two thirds of what it cost:
    1.13us a name over the shipped libstdc++, against 0.43 for the same predicate
    written out. These pin that it *is* the same predicate -- the strips are one or two
    characters off the front, and each of them is a case here.
    """

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("_i_Object_class", True),
            ("_c_Object_class", True),
            ("__i_Object_class", True),  # the leading underscore a Mach-O table adds
            ("._i_Object_class", True),  # an ELF object built for GNUstep
            ("l__i_Object_class", True),  # an assembler-local label
            ("L__i_Object_class", True),
            ("._i_", True),
            ("x_i_Object_class", False),  # not at the front of any candidate
            ("__x_i_Object", False),
            ("l_x_i_Object", False),
            ("_i", False),
            ("", False),
            ("_", False),
            ("l_", False),
        ],
    )
    def test_the_written_out_screen_agrees_with_the_candidate_list(self, name, expected):
        from demangle.schemes.objc._parser import _candidates, _method_prefixed

        assert _method_prefixed(name) is expected
        listed = any(candidate.startswith(("_i_", "_c_")) for candidate in _candidates(name)) if name else False
        assert listed is expected
