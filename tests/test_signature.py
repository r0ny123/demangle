"""`signature()`: the parts of a name rather than its spelling.

The property that makes the three name fields safe to recombine -- `namespace`, the
scheme's separator, and `base_name` spell `qualified_name` exactly -- is checked here
against every corpus rather than against a handful of examples. It is the one thing a
caller can rely on across every scheme, and the ways to break it (a `::` inside a
template argument, a `.` inside a Nim operator, a Rust closure whose own name starts
with the separator) are all things a corpus holds and a hand-written case does not.
"""

import pytest

import demangle
from demangle import Signature, signature, signatureb
from demangle._signature import _SEPARATORS, _split_last
from demangle.core.errors import DemanglingError

from .conftest import corpus_files, load_corpus, requires_gnu_cxxfilt
from .test_conformance import NO_PARAMS_AGREE, NO_PARAMS_TOTAL

#: The refusal corpora hold names with no expected column, so they load as nothing.
CORPORA = [name for name in corpus_files() if load_corpus(name)]
DELPHI_CORPORA = ["delphi-constructs.txt", "delphi-real-world.txt", "delphi-tdump.txt"]
PASCAL_CORPORA = ["pascal-real-world.txt"]
MSVC_SPECIAL_CORPORA = ["msvc-llvm-corpus.txt", "msvc-boost.txt", "msvc-clang.txt", "msvc-type-descriptors.txt"]

#: What the Delphi unmangler writes before a name, none of which is part of it.
DELPHI_PREFIXES = frozenset(
    {"__cdecl", "__pascal", "__fastcall", "__stdcall", "__saveregs", "__linkproc__", "__tpdsc__"}
)


@pytest.mark.sweep
class TestTheNameFields:
    """`namespace` + separator + `base_name` == `qualified_name`, everywhere."""

    @pytest.mark.parametrize("corpus", CORPORA)
    def test_the_parts_recombine(self, corpus, subtests):
        for mangled, _ in load_corpus(corpus):
            try:
                parts = signature(mangled)
            except DemanglingError:
                continue
            separator = _SEPARATORS.get(parts.language, "::")
            joined = f"{parts.namespace}{separator}{parts.base_name}" if parts.namespace else parts.base_name
            with subtests.test(mangled=mangled):
                assert joined == parts.qualified_name

    @pytest.mark.parametrize("corpus", CORPORA)
    def test_the_qualified_name_is_text_from_the_spelling(self, corpus, subtests):
        """Nothing in the name fields is invented: every one of them was spelled.

        Objective-C is the exception, and a deliberate one -- a method's category sits
        between its class and its selector in the spelling and is not part of its name.
        Delphi's `__linkproc__` and `__vdflg__` are the other: they sit between a unit or
        class and the name, and are a label, not a component. So is the type an MSVC
        `RTTI Type Descriptor` describes where it is a pointer to a function or an array:
        the label sat inside the declarator, and taking it out joins what was on either
        side.
        """
        for mangled, _ in load_corpus(corpus):
            try:
                parts = signature(mangled)
            except DemanglingError:
                continue
            if parts.language == "objc" or parts.special in (
                "__linkproc__",
                "__vdflg__",
                "RTTI Type Descriptor",
                "RTTI Type Descriptor Name",
            ):
                continue
            with subtests.test(mangled=mangled):
                assert parts.qualified_name in parts.demangled

    @pytest.mark.parametrize("corpus", CORPORA)
    def test_a_base_name_is_never_empty(self, corpus, subtests):
        """A name that parsed has a last component; nothing here answers with a blank."""
        for mangled, _ in load_corpus(corpus):
            try:
                parts = signature(mangled)
            except DemanglingError:
                continue
            with subtests.test(mangled=mangled):
                assert parts.base_name


class TestItanium:
    def test_a_member_function(self):
        parts = signature("_ZNSt6vectorIiSaIiEE9push_backERKi")
        assert parts.language == "itanium"
        assert parts.namespace == "std::vector<int, std::allocator<int>>"
        assert parts.base_name == "push_back"
        assert parts.parameters == ("int const&",)
        assert parts.return_type is None
        assert (parts.is_function, parts.is_data) == (True, False)

    def test_a_separator_inside_a_template_argument_is_not_one(self):
        """The `::` in `std::allocator<int>` must not end up splitting the name."""
        parts = signature("_ZN3FooINSt3mapIiiEEE3barEv")
        assert parts.namespace == "Foo<std::map<int, int>>"
        assert parts.base_name == "bar"

    def test_an_operator_name_holding_the_separators_characters(self):
        parts = signature("_ZN3FoolsERKS_")
        assert (parts.namespace, parts.base_name) == ("Foo", "operator<<")

    def test_a_free_operator_is_not_split_at_its_space(self):
        parts = signature("_Znwm")
        assert (parts.namespace, parts.base_name) == ("", "operator new")

    def test_a_template_specialisation_carries_its_return_type(self):
        parts = signature("_ZSt4sortIPiEvT_S1_")
        assert parts.return_type == "void"
        assert parts.base_name == "sort<int*>"
        assert parts.parameters == ("int*", "int*")

    def test_a_function_taking_nothing_has_an_empty_parameter_list(self):
        assert signature("_Z1fv").parameters == ()

    def test_trailing_qualifiers(self):
        parts = signature("_ZNKO3Foo3barEv")
        assert parts.qualifiers == ("const", "&&")

    @pytest.mark.parametrize(
        ("mangled", "special", "base"),
        [
            ("_ZTVN3FooE", "vtable for", "Foo"),
            ("_ZTIN3FooE", "typeinfo for", "Foo"),
            ("_ZGVZN3fooEvE1x", "guard variable for", "x"),
        ],
    )
    def test_a_symbol_about_an_entity_describes_the_entity(self, mangled, special, base):
        parts = signature(mangled)
        assert (parts.special, parts.base_name, parts.is_special) == (special, base, True)

    def test_a_version_decoration_is_kept_apart_from_the_name(self):
        parts = signature("_ZN3Foo3barEv@@GLIBCXX_3.4")
        assert (parts.qualified_name, parts.decoration) == ("Foo::bar", "@@GLIBCXX_3.4")

    @pytest.mark.parametrize("mangled", ["_ZN3FooC1Ei", "_ZN3FooD2Ev", "_ZN3FooIiEC1Ev"])
    def test_constructors_and_destructors(self, mangled):
        assert signature(mangled).is_ctor_or_dtor

    @pytest.mark.parametrize("mangled", ["_ZN3Foo3barEv", "_ZN3Foo4FoooEv"])
    def test_what_is_not_a_constructor(self, mangled):
        assert not signature(mangled).is_ctor_or_dtor


class TestMsvc:
    def test_a_member_function_carries_its_access_and_convention(self):
        parts = signature("?f@Foo@@AEBAXH@Z")
        assert parts.language == "msvc"
        assert (parts.namespace, parts.base_name) == ("Foo", "f")
        assert parts.parameters == ("int",)
        assert parts.return_type == "void"
        assert parts.calling_convention == "__cdecl"
        assert parts.qualifiers == ("private", "const")

    def test_data_is_not_a_function(self):
        parts = signature("?x@@3HA")
        assert (parts.is_data, parts.is_function) == (True, False)
        assert parts.return_type == "int"
        assert parts.parameters is None

    def test_a_pointer_to_a_function_is_data(self):
        """The declared type is a pointer; only a declared function type is a function."""
        parts = signature("?x@@3P6AHH@ZA")
        assert (parts.is_data, parts.is_function) == (True, False)
        assert parts.parameters is None


class TestMsvcSpecials:
    """A label goes to `special` and the entity it is about stays in the name fields."""

    @pytest.mark.parametrize(
        ("mangled", "special", "namespace", "base"),
        [
            ("??_7Base@@6B@", "vftable", "", "Base"),
            ("??_7A@B@@6BC@D@@@", "vftable", "B", "A"),
            ("??_7A@@6BB@@C@@@", "vftable", "", "A"),
            ("??_8Middle2@@7B@", "vbtable", "", "Middle2"),
            ("??_SBase@@6B@", "local vftable", "", "Base"),
            ("??_R0?AUBase@@@8", "RTTI Type Descriptor", "", "Base"),
            ("??_R1A@?0A@EA@Base@@8", "RTTI Base Class Descriptor", "", "Base"),
        ],
    )
    def test_a_table_or_descriptor_is_data_about_its_class(self, mangled, special, namespace, base):
        parts = signature(mangled)
        assert (parts.special, parts.namespace, parts.base_name) == (special, namespace, base)
        assert (parts.is_data, parts.is_function) == (True, False)
        assert parts.qualified_name == (f"{namespace}::{base}" if namespace else base)

    @pytest.mark.parametrize(
        ("mangled", "special"),
        [
            ("??_GBase@@UEAAPEAXI@Z", "scalar deleting dtor"),
            ("??_EBase@@UEAAPEAXI@Z", "vector deleting dtor"),
            ("??_DDiamond@@QEAAXXZ", "vbase dtor"),
            ("??_F?$SomeTemplate@H@@QAEXXZ", "default ctor closure"),
            ("??_O?$SomeTemplate@H@@QAEXXZ", "copy ctor closure"),
            ("??_KBase@@UEAAPEAXI@Z", "virtual displacement map"),
            ("??_LBase@@UEAAPEAXI@Z", "eh vector ctor iterator"),
        ],
    )
    def test_a_compiler_made_member_is_a_function_of_its_class(self, mangled, special):
        parts = signature(mangled)
        assert parts.special == special
        assert (parts.is_function, parts.is_data) == (True, False)
        assert parts.parameters is not None
        assert "`" not in parts.qualified_name

    def test_an_adjustor_thunk_is_labelled_adjustor_and_names_what_it_adjusts(self):
        parts = signature("?f@C@@WBA@EAAHXZ")
        assert (parts.special, parts.namespace, parts.base_name) == ("adjustor", "C", "f")
        assert parts.parameters == ("void",)

    def test_an_adjustor_over_a_compiler_made_member_names_the_class(self):
        parts = signature("??_EBase@@G3AEPAXI@Z")
        assert (parts.special, parts.qualified_name) == ("adjustor", "Base")

    def test_a_vcall_thunk_keeps_its_convention_and_loses_its_payload(self):
        parts = signature("??_9Base@@$B7AA")
        assert (parts.special, parts.qualified_name) == ("vcall", "Base")
        assert parts.calling_convention == "__cdecl"
        assert parts.is_function

    def test_an_iterator_with_no_class_is_named_by_its_label(self):
        parts = signature("??_H@YAXPEAX_K1P6APEAX0@Z@Z")
        assert parts.special == "vector ctor iterator"
        assert parts.qualified_name == parts.base_name == "vector ctor iterator"
        assert parts.is_function

    def test_a_dynamic_initialiser_is_about_its_variable(self):
        parts = signature("??__EFoo@@YAXXZ")
        assert (parts.special, parts.qualified_name) == ("dynamic initializer for", "Foo")
        assert (parts.is_function, parts.parameters) == (True, ("void",))

    def test_a_dynamic_initialiser_of_a_static_member_is_about_the_member(self):
        parts = signature("??__E?i@C@@0HA@@YAXXZ")
        assert (parts.special, parts.namespace, parts.base_name) == ("dynamic initializer for", "C", "i")

    def test_a_dynamic_atexit_destructor_is_about_a_local_static(self):
        parts = signature("??__Fvalue@?1??getenv@nowide@boost@@YAPEADPEBD@Z@YAXXZ")
        assert parts.special == "dynamic atexit destructor for"
        assert parts.base_name == "value"
        assert parts.namespace.endswith("getenv(char const *)'::`2'")

    def test_an_anonymous_namespace_is_a_component_not_a_phrase(self):
        parts = signature("?anonymous@?A@N@@3HA")
        assert parts.special is None
        assert (parts.namespace, parts.base_name) == ("N::`anonymous namespace'", "anonymous")

    def test_a_local_static_splits_after_its_function(self):
        parts = signature("?e2@?1???$get_escape_R_string@D@re_detail_500@boost@@YAPEBDXZ@4PEBDB")
        assert parts.base_name == "e2"
        assert parts.namespace.startswith("`char const * __cdecl boost::re_detail_500::")

    @pytest.mark.sweep
    @pytest.mark.parametrize("corpus", MSVC_SPECIAL_CORPORA)
    def test_every_label_in_the_corpora_is_taken_out_of_the_name(self, corpus, subtests):
        for mangled, _ in load_corpus(corpus):
            try:
                parts = signature(mangled)
            except DemanglingError:
                continue
            if "`" not in parts.demangled:
                continue
            with subtests.test(mangled=mangled):
                joined = f"{parts.namespace}::{parts.base_name}" if parts.namespace else parts.base_name
                assert joined == parts.qualified_name
                if parts.special is not None:
                    for field in (parts.qualified_name, parts.namespace, parts.base_name):
                        assert f"`{parts.special}" not in field


class TestTheSchemesThatCarryASignature:
    def test_swift_separates_parameters_from_the_result(self):
        parts = signature("$s4main3FooV3baryS2i_SStF")
        assert parts.language == "swift"
        assert (parts.namespace, parts.base_name) == ("main.Foo", "bar")
        assert parts.parameters == ("Swift.Int", "Swift.String")
        assert parts.return_type == "Swift.Int"

    def test_swift_records_the_effects_a_function_declares(self):
        parts = signature("$s10Foundation10FileHandleC9readToEndAA4DataVSgyKF")
        assert parts.qualifiers == ("throws",)
        assert parts.parameters == ()
        assert parts.return_type == "Foundation.Data?"

    def test_a_swift_property_is_data_and_its_type_is_the_return_type(self):
        parts = signature("$s10Foundation10CocoaErrorV10underlyings0C0_pSgvp")
        assert (parts.is_data, parts.is_function) == (True, False)
        assert parts.return_type == "Swift.Error?"

    @pytest.mark.parametrize("mangled", ["$s4main3FooVACycfc", "$s4main3FooCfd"])
    def test_swift_init_and_deinit(self, mangled):
        assert signature(mangled).is_ctor_or_dtor

    def test_a_swift_file_private_name_is_not_a_parameter_list(self):
        """`Foundation.FileHandle.(_check in _2DF8)()` opens two brackets, not one."""
        parts = signature("$s10Foundation10FileHandleC06_checkbC033_2DF8E09821905D55FA342549A73ACFB7LLyyF")
        assert parts.namespace == "Foundation.FileHandle"
        assert parts.base_name.startswith("(_checkFileHandle in ")
        assert parts.parameters == ()

    def test_free_pascal_carries_parameter_types_and_a_result(self):
        parts = signature("A52_$$_A52_DECODER_READ$PA52_DECODER$POINTER$LONGINT$$LONGINT")
        assert parts.language == "pascal"
        assert (parts.namespace, parts.base_name) == ("A52", "A52_DECODER_READ")
        assert parts.parameters == ("PA52_DECODER", "POINTER", "LONGINT")
        assert parts.return_type == "LONGINT"
        assert parts.is_function

    def test_free_pascal_writes_no_brackets_for_an_empty_list(self):
        parts = signature("AVL_TREE_$$_init$")
        assert parts.parameters == ()

    def test_d_writes_its_parameter_list_as_one_fragment(self):
        parts = signature("_D4test3fooFiZi")
        assert (parts.language, parts.namespace, parts.base_name) == ("d", "test", "foo")
        assert parts.parameters == ("int",)
        assert parts.is_function

    def test_delphi_carries_parameter_types(self):
        parts = signature("@f$qM8TMyClassi")
        assert parts.language == "delphi"
        assert parts.parameters == ("int TMyClass::*",)


class TestDelphi:
    """The unmangler writes a result, a convention and a label before the name."""

    def test_a_method_has_its_convention_off_its_name(self):
        parts = signature("@Unit@TForm1@Button1Click$qqrp14System@TObject")
        assert parts.calling_convention == "__fastcall"
        assert parts.qualified_name == "Unit::TForm1::Button1Click"
        assert (parts.namespace, parts.base_name) == ("Unit::TForm1", "Button1Click")
        assert parts.parameters == ("System::TObject *",)

    @pytest.mark.parametrize(
        ("mangled", "convention"),
        [
            ("@Unit@Proc$qqcv", "__cdecl"),
            ("@Unit@Proc$qqsv", "__stdcall"),
            ("@Unit@Proc$qqpv", "__pascal"),
            ("@Unit@Proc$qqrv", "__fastcall"),
        ],
    )
    def test_each_convention_is_the_convention(self, mangled, convention):
        parts = signature(mangled)
        assert parts.calling_convention == convention
        assert (parts.namespace, parts.base_name) == ("Unit", "Proc")

    def test_a_plain_function_has_no_convention(self):
        parts = signature("@f$qM8TMyClassi")
        assert (parts.calling_convention, parts.namespace, parts.base_name) == (None, "", "f")

    def test_saveregs_stays_with_the_convention(self):
        parts = signature("@f$qqgv")
        assert (parts.calling_convention, parts.base_name) == ("__saveregs", "f")

    def test_a_constructor_and_a_destructor(self):
        built = signature("@Forms@TForm@$bctr$qqrp18Classes@TComponent")
        assert (built.namespace, built.base_name, built.is_ctor_or_dtor) == ("Forms::TForm", "TForm", True)
        gone = signature("@Classes@TFileStream@$bdtr$qqrv")
        assert (gone.namespace, gone.base_name, gone.is_ctor_or_dtor) == ("Classes::TFileStream", "~TFileStream", True)
        assert gone.calling_convention == "__fastcall"

    def test_an_operator_keeps_its_spelling_in_the_base_name(self):
        parts = signature("@$beql$qrx5_GUIDt1")
        assert (parts.namespace, parts.base_name) == ("", "operator ==")

    def test_a_template_function_has_its_result_off_its_name(self):
        parts = signature("@Rtti@TValue@%IsType$p17System@TMetaClass%$qqrv$o")
        assert parts.return_type == "bool"
        assert parts.calling_convention == "__fastcall"
        assert (parts.namespace, parts.base_name) == ("Rtti::TValue", "IsType<System::TMetaClass *>")

    def test_a_linker_procedure_is_labelled_and_keeps_its_unit(self):
        parts = signature("@System@@DynArrayAddRef$qqrv")
        assert parts.special == "__linkproc__"
        assert parts.calling_convention == "__fastcall"
        assert (parts.namespace, parts.base_name) == ("System", "DynArrayAddRef")

    def test_a_bare_linker_procedure(self):
        parts = signature("@@AsClass")
        assert (parts.special, parts.qualified_name, parts.is_function) == ("__linkproc__", "AsClass", False)

    def test_a_class_reference_is_a_type_descriptor_of_its_class(self):
        parts = signature("@$xp$11Forms@TForm")
        assert (parts.special, parts.namespace, parts.base_name) == ("__tpdsc__", "Forms", "TForm")

    def test_a_thunk_is_labelled_and_named_by_its_operands(self):
        parts = signature("@$vc1$B0$1$0$")
        assert (parts.special, parts.qualified_name) == ("__thunk__", "[B,0,1,0]")

    def test_a_virtual_definition_flag_is_labelled(self):
        parts = signature("@f@#$cf$@bar")
        assert (parts.special, parts.namespace, parts.base_name) == ("__vdflg__", "f", "bar")

    @pytest.mark.sweep
    @pytest.mark.parametrize("corpus", DELPHI_CORPORA)
    def test_no_prefix_is_left_in_the_name(self, corpus, subtests):
        for mangled, _ in load_corpus(corpus):
            try:
                parts = signature(mangled)
            except DemanglingError:
                continue
            with subtests.test(mangled=mangled):
                if parts.namespace:
                    assert parts.qualified_name == f"{parts.namespace}::{parts.base_name}"
                assert parts.base_name
                for field in (parts.qualified_name, parts.namespace, parts.base_name):
                    assert not set(field.split()) & DELPHI_PREFIXES


class TestPascal:
    def test_a_unit_function_has_its_result_apart(self):
        parts = signature("A52_$$_A52_DECODER_READ$PA52_DECODER$POINTER$LONGINT$$LONGINT")
        assert parts.qualified_name == "A52.A52_DECODER_READ"
        assert (parts.namespace, parts.base_name, parts.calling_convention) == ("A52", "A52_DECODER_READ", None)

    def test_a_method_splits_at_the_last_dot(self):
        parts = signature("AVL_TREE$_$TAVLTREE_$__$$_CREATE$$TAVLTREE")
        assert (parts.namespace, parts.base_name) == ("AVL_TREE.TAVLTREE", "CREATE")
        assert parts.return_type == "TAVLTREE"

    def test_a_nested_routine_keeps_its_enclosing_routine_in_the_namespace(self):
        parts = signature("APP$_$TDESKTOP_$_CASCADE$TRECT_$$_DOCOUNT$PVIEW")
        assert (parts.namespace, parts.base_name) == ("APP.TDESKTOP.CASCADE$TRECT", "DOCOUNT")
        assert parts.parameters == ("PVIEW",)

    def test_a_routine_with_no_result_has_none(self):
        parts = signature("AVL_TREE_$$_init$")
        assert (parts.namespace, parts.base_name, parts.return_type) == ("AVL_TREE", "init", None)

    @pytest.mark.sweep
    @pytest.mark.parametrize("corpus", PASCAL_CORPORA)
    def test_the_name_holds_nothing_but_the_name(self, corpus, subtests):
        for mangled, _ in load_corpus(corpus):
            try:
                parts = signature(mangled)
            except DemanglingError:
                continue
            with subtests.test(mangled=mangled):
                if parts.namespace:
                    assert parts.qualified_name == f"{parts.namespace}.{parts.base_name}"
                assert parts.base_name
                assert parts.calling_convention is None
                for field in (parts.qualified_name, parts.namespace, parts.base_name):
                    assert not set(field.split()) & DELPHI_PREFIXES
                    assert parts.special or "(" not in field


class TestTheSchemesThatCarryOnlyAPath:
    @pytest.mark.parametrize(
        ("mangled", "language", "namespace", "base"),
        [
            ("_RNvC6_123foo3bar", "rust", "123foo", "bar"),
            ("archive/zip.init.0.func1", "go", "archive/zip", "init.0.func1"),
            ("-[NSString length]", "objc", "NSString", "length"),
        ],
    )
    def test_a_path_is_split_at_its_own_boundaries(self, mangled, language, namespace, base):
        parts = signature(mangled)
        assert (parts.language, parts.namespace, parts.base_name) == (language, namespace, base)

    def test_none_of_them_claims_a_parameter_list(self):
        for mangled in ("_RNvC6_123foo3bar", "archive/zip.init.0.func1", "-[NSString length]"):
            assert signature(mangled).parameters is None

    def test_none_of_them_is_claimed_to_be_a_function(self):
        """A path to a function reads exactly like a path to a static."""
        for mangled in ("_RNvC6_123foo3bar", "archive/zip.init.0.func1"):
            assert not signature(mangled).is_function

    def test_a_nim_operator_that_is_the_separator(self):
        """`ops..` is the module `ops` and the operator `..`, not `ops.` and `.`."""
        parts = signature("dotdot___ops_65")
        assert (parts.namespace, parts.base_name) == ("ops", "..")

    def test_a_rust_closure_writes_the_separator_into_its_own_name(self):
        parts = signature("_RNCNCNgCs6DXkGYLi8lr_2cc5spawn00B5_")
        assert parts.namespace == "cc::spawn::{closure#0}"
        assert parts.base_name == "{closure#0}"

    def test_an_objc_module_constructor_is_prose_and_is_left_whole(self):
        parts = signature(".objc_ctor")
        assert (parts.namespace, parts.base_name) == ("", "Objective-C module constructor")
        assert parts.special is None


class TestObjectiveCMetadata:
    """A runtime data symbol is *about* a class, as a Swift descriptor is about a type:
    the label is `special` and the name fields hold the entity."""

    @pytest.mark.parametrize(
        ("mangled", "special", "namespace", "base"),
        [
            ("_OBJC_CLASS_$_NSData", "Objective-C class", "", "NSData"),
            ("_OBJC_METACLASS_$_NSData", "Objective-C metaclass", "", "NSData"),
            ("__OBJC_CLASS_RO_$_NSData", "class data for", "", "NSData"),
            ("l_OBJC_CLASS_NSData", "Objective-C class", "", "NSData"),
            ("__objc_class_name_NSData", "Objective-C class", "", "NSData"),
            (".objc_class_name_NSData", "Objective-C class", "", "NSData"),
            ("_OBJC_IVAR_$_NSData._count", "instance variable offset for", "NSData", "_count"),
            ("__objc_ivar_offset_NSData.count.i", "instance variable offset for", "NSData", "count"),
            ("__OBJC_$_CATEGORY_NSString_$_Extra", "Objective-C category", "", "NSString(Extra)"),
            (".objc_category_name_NSString_Extra", "Objective-C category", "", "NSString(Extra)"),
            ("__OBJC_$_CATEGORY_INSTANCE_METHODS_NSString_$_Extra", "instance method list for", "", "NSString(Extra)"),
            (".objc_selector_foo:_v@:", "Objective-C selector", "", "foo:"),
            # The name is also a letter of the label, and is found after it.
            (".objc_sel_name_b", "Objective-C selector", "", "b"),
            ("_OBJC_CLASS_$_C", "Objective-C class", "", "C"),
        ],
    )
    def test_the_label_is_special_and_the_entity_is_the_name(self, mangled, special, namespace, base):
        parts = signature(mangled)
        assert (parts.language, parts.special, parts.namespace, parts.base_name) == ("objc", special, namespace, base)
        assert parts.is_special
        assert not parts.is_function

    def test_a_method_is_not_about_anything(self):
        parts = signature("-[NSString length]")
        assert (parts.special, parts.qualified_name) == (None, "NSString length")

    @pytest.mark.sweep
    def test_every_labelled_symbol_in_the_corpus_is_split(self, subtests):
        """Each runtime data symbol that names a class, a category, an instance variable
        or a selector: the label leads the spelling, and the entity follows it."""
        split = 0
        for mangled, _ in load_corpus("objc-real-world.txt"):
            parts = signature(mangled)
            if parts.is_function or not demangle.parse(mangled).children():
                continue
            split += 1
            with subtests.test(mangled=mangled):
                assert parts.special is not None
                assert parts.demangled.startswith(parts.special + " ")
                assert parts.qualified_name.replace(" ", ".") in parts.demangled
                assert not parts.qualified_name.startswith(parts.special)
        assert split > 1000


class TestATreeFromElsewhere:
    """A registered plugin's tree need not be built the way the shipped ones are."""

    def test_a_variable_node_with_no_fragments_is_data(self):
        from demangle._signature import _parts_of, _Reading
        from demangle.core.ast import Node

        class Variable(Node):
            __slots__ = ()
            kind = "variable"

            def spell(self, declarator="", style=None):
                return "counter"

        found = _parts_of(_Reading("elsewhere", "::", None), Variable())
        assert (found["is_data"], found["is_function"]) == (True, False)
        assert found["qualified_name"] == "counter"


class TestFailing:
    """A name with no parts raises rather than answering with a row of `None`s."""

    @pytest.mark.parametrize("name", ["notasymbol", "", "_Z"])
    def test_a_name_that_cannot_be_read(self, name):
        with pytest.raises(DemanglingError):
            signature(name)

    def test_a_forced_language_that_does_not_fit(self):
        with pytest.raises(DemanglingError):
            signature("_Z1fv", language="rust")


class TestSplitLast:
    """The text reader, for the places a tree has already flattened the answer."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("std::map<int, std::string>::at", ("std::map<int, std::string>", "at")),
            ("A::operator->", ("A", "operator->")),
            ("A::operator>", ("A", "operator>")),
            ("plain", ("", "plain")),
            ("a::b::c", ("a::b", "c")),
            ("f<a::b>", ("", "f<a::b>")),
        ],
    )
    def test_brackets_are_counted_not_split_on(self, text, expected):
        assert _split_last(text, "::") == expected

    def test_an_unbalanced_bracket_cannot_swallow_the_rest(self):
        """`operator>` closes a bracket it never opened; the depth is clamped at zero."""
        assert _split_last("a::operator>::b", "::") == ("a::operator>", "b")

    def test_a_phrase_is_not_a_qualified_name(self):
        """A space outside every bracket means the printer wrote prose, not a name."""
        assert _split_last("inout Swift.Int", ".") == ("", "inout Swift.Int")

    def test_a_space_inside_brackets_is_not_a_phrase(self):
        assert _split_last("a::b<int, char>::c", "::") == ("a::b<int, char>", "c")


class TestTheObject:
    def test_it_is_frozen(self):
        with pytest.raises(AttributeError):
            signature("_Z1fv").language = "msvc"  # ty: ignore[invalid-assignment]

    def test_it_spells_itself_as_the_demangling(self):
        assert str(signature("_Z1fv")) == demangle.demangle("_Z1fv") == "f()"

    def test_bytes_in_gives_the_same_parts(self):
        """A symbol table holds bytes; the fields are `str`, as a tree's spellings are."""
        assert signatureb(b"_ZN3foo3barEv") == signature("_ZN3foo3barEv")
        with pytest.raises(TypeError):
            signatureb("_ZN3foo3barEv")  # ty: ignore[invalid-argument-type]

    def test_it_is_exported(self):
        assert demangle.Signature is Signature
        assert isinstance(signature("_Z1fv"), Signature)

    def test_the_style_reaches_every_field(self):
        """A tree parsed under one style and spelled under another mixes the two."""
        parts = signature("_ZNSt6vectorIiSaIiEE9push_backERKi", style="gnu")
        assert parts.namespace == "std::vector<int, std::allocator<int> >"
        assert parts.demangled.startswith(parts.namespace)

        for style, closing, other in (("gnu", "> >", ">>"), ("llvm", ">>", "> >")):
            parts = signature("_ZN1A1fISt6vectorIiSaIiEEEES3_T_", style=style)
            assert parts.return_type is not None
            assert parts.parameters is not None
            spelled = [parts.demangled, parts.qualified_name, parts.return_type, *parts.parameters]
            for text in spelled:
                assert closing in text, (style, text)
                assert other not in text, (style, text)


class TestTheSchemeIsResolvedBeforeItIsUsed:
    """A caller writes whatever name they like; the fields must not depend on which.

    Every alias in `languages()` is documented, so `language="objective-c"` has to read
    the same as `language="objc"`. `_SEPARATORS` is keyed by the canonical name; an
    alias that missed it would skip the scheme-specific extraction and return the whole
    spelling as the base name with `is_function` False.
    """

    @pytest.mark.parametrize(
        ("mangled", "canonical", "aliases"),
        [
            ("+[A_B andThen:do:]", "objc", ("objective-c", "objectivec")),
            ("_ZNSt6vectorIiSaIiEE9push_backERKi", "itanium", ("gnu", "gcc", "clang", "c++")),
            ("?f@@YAXH@Z", "msvc", ("microsoft", "ms", "vc")),
        ],
    )
    def test_an_alias_reads_the_same_as_the_name_it_stands_for(self, mangled, canonical, aliases):
        expected = signature(mangled, language=canonical)
        for alias in aliases:
            assert signature(mangled, language=alias) == expected, alias

    def test_the_language_field_is_the_scheme_rather_than_what_the_caller_typed(self):
        assert signature("+[A_B andThen:do:]", language="objective-c").language == "objc"


class TestEverySchemeHasASeparator:
    """A scheme whose spelling joins components with something other than `::`.

    `jni` joins with `.`: a `::` fallback would return `com.example.Foo.bar` whole as
    the base name with an empty namespace -- the structured fields saying nothing for a
    scheme whose whole shape is a path.
    """

    def test_a_jni_name_splits_at_its_own_separator(self):
        parts = signature("Java_com_example_Foo_bar__I")
        assert parts.base_name == "bar"
        assert parts.namespace == "com.example.Foo"
        assert parts.namespace + "." + parts.base_name == parts.qualified_name

    @pytest.mark.parametrize("mangled", ["AtEnd__13ivRubberGroup", "BuildLight__9CGuiLightCFv"])
    def test_the_pre_itanium_schemes_take_the_cpp_default(self, mangled):
        # Absent from the table on purpose: both spell C++, so `::` is right for them.
        parts = signature(mangled)
        assert parts.namespace and parts.base_name
        assert parts.namespace + "::" + parts.base_name == parts.qualified_name


@requires_gnu_cxxfilt
class TestAgainstCxxfiltMinusP:
    """What `-p` prints, measured against the tool it is named after.

    Skipped where GNU `c++filt` is not installed, which is every Windows runner and most
    macOS ones -- there LLVM's demangler is named `c++filt` and answers differently, so
    the guard asks the banner rather than the name. The decorator belongs to *this* class;
    without it these two shell out unconditionally and fail on the missing binary rather
    than saying what is missing.

    The differences are deliberate and are described in the README. `c++filt` strips the
    parameter list from the outermost declaration only, so a thunk keeps its target's;
    and it drops a `[clone .cold]` suffix while keeping an `@@GLIBCXX_3.4` one. This
    strips throughout and keeps both, because a filter over a symbol table should not
    quietly discard part of the symbol.
    """

    CORPORA = ("itanium-libstdcxx.txt", "itanium-real-world-gnu.txt")

    @staticmethod
    def _ours(name):
        try:
            parts = signature(name, style="gnu")
        except DemanglingError:
            return name
        lead = f"{parts.special} " if parts.special else ""
        return f"{lead}{parts.qualified_name}{parts.decoration}"

    def _measure(self):
        import subprocess

        names = [mangled for corpus in self.CORPORA for mangled, _ in load_corpus(corpus)]
        reference = subprocess.run(
            ["c++filt", "-p", "--no-strip-underscore"],
            input="\n".join(names) + "\n",
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        assert len(reference) == len(names), "c++filt did not answer every name"
        return len(names), sum(
            1 for name, expected in zip(names, reference, strict=True) if self._ours(name) == expected
        )

    def test_the_measured_agreement_is_what_is_pinned(self):
        assert self._measure() == (NO_PARAMS_TOTAL, NO_PARAMS_AGREE)

    def test_every_difference_is_one_of_the_four_described(self, subtests):
        import subprocess

        names = [mangled for corpus in self.CORPORA for mangled, _ in load_corpus(corpus)]
        reference = subprocess.run(
            ["c++filt", "-p", "--no-strip-underscore"],
            input="\n".join(names) + "\n",
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        for name, expected in zip(names, reference, strict=True):
            ours = self._ours(name)
            if ours == expected:
                continue
            with subtests.test(mangled=name):
                assert (
                    expected == name  # a name c++filt refuses and this reads
                    or "thunk" in expected  # the target of a thunk keeps its parameters
                    or "clone for" in expected  # so does the target of a transaction clone
                    or "[clone" in demangle.demangle(name, style="gnu")  # a clone suffix
                ), f"{name}\n  ref: {expected}\n  our: {ours}"
