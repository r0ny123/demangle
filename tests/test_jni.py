"""JNI native method names.

The one scheme here whose encoding is written down normatively rather than having to be
transcribed from a reference implementation: the JNI specification's Design Overview,
"Resolving Native Method Names". So what these tests assert is the specification, and
the cases are the ones it names.

There is no reference *demangler* to score against -- neither binutils nor LLVM reads
these, and Ghidra and IDA do not either -- so the property that stands in for one is
re-mangling: a reading is right if encoding it again gives back the symbol.
"""

import pytest

import demangle
from demangle.core.errors import DemanglingError
from demangle.schemes.jni import descriptor_types, parse_jni_symbol
from demangle.schemes.jni._parser import DemangleFailure, unescape


def mangle(path, signature=None):
    """The specification's encoding, written out so the tests can go back the other way."""
    out = []
    for char in path:
        if char.isascii() and char.isalnum():
            out.append(char)
        elif char == "/":
            out.append("_")
        elif char == "_":
            out.append("_1")
        elif char == ";":
            out.append("_2")
        elif char == "[":
            out.append("_3")
        else:
            out.append(f"_0{ord(char):04x}")
    encoded = "Java_" + "".join(out)
    return encoded if signature is None else encoded + "__" + mangle(signature)[len("Java_") :]


class TestTheSpecificationsOwnExamples:
    @pytest.mark.parametrize(
        ("mangled", "expected"),
        [
            ("Java_com_example_Foo_bar", "com.example.Foo.bar"),
            # An overloaded native carries the parameter part of its descriptor.
            ("Java_com_example_Foo_bar__Ljava_lang_String_2", "com.example.Foo.bar(java.lang.String)"),
            ("Java_pkg_C_m__I", "pkg.C.m(int)"),
            # An overloaded method taking nothing: the long name promises an argument
            # list and the list is empty.
            ("Java_pkg_C_m__", "pkg.C.m()"),
            # `/` before an escape also makes a `__`, so the separator is the one whose
            # tail is a descriptor -- here there is none, and the whole run is the name.
            ("Java_com_example_Foo__003c0", "com.example.Foo.\u03c0"),
            ("Java_com_example_Foo__003c0__D", "com.example.Foo.\u03c0(double)"),
            # `_3` is `[`, so an array parameter reads as one.
            ("Java_pkg_C_m___3I", "pkg.C.m(int[])"),
            ("Java_pkg_C_m___3_3Ljava_lang_Object_2", "pkg.C.m(java.lang.Object[][])"),
            # Every primitive descriptor.
            ("Java_p_C_m__BCDFIJSZ", "p.C.m(byte, char, double, float, int, long, short, boolean)"),
            # `_1` is an underscore that was written in the name.
            ("Java_pkg_C_my_1method", "pkg.C.my_method"),
            # `_0XXXX` is any other character: `$` is `_00024`, so an inner class decodes.
            ("Java_Foo_00024Bar_baz", "Foo$Bar.baz"),
        ],
    )
    def test_reading(self, mangled, expected):
        assert demangle.demangle(mangled) == expected

    def test_the_escapes_round_trip(self, subtests):
        """Every character the encoding treats specially, through both directions."""
        for name in ("a_b", "a;b", "a[b", "a$b", "aéb", "a/b/C/m", "_", "C/m"):
            with subtests.test(name=name):
                assert unescape(mangle(name)[len("Java_") :]) == name


class TestWhatIsRefused:
    """The prefix is not proof, so a name that does not decode is left alone."""

    @pytest.mark.parametrize(
        "name",
        [
            # No declaring class: a native method always has one, and without this rule
            # any C function whose name merely starts `Java_` would be rewritten.
            "Java_helper",
            "Java_",
            # `Q` is not a type descriptor, and `L` needs its terminator.
            "Java_pkg_C_m__Q",
            "Java_pkg_C_m__Ljava_lang_String",
            # An array with no element type.
            "Java_pkg_C_m___3",
            # `V` is a return type (JVMS 4.3.2) and the overload signature carries only
            # parameters, so it never stands here -- bare or as an array's element.
            "Java_pkg_C_m__V",
            "Java_pkg_C_m___3V",
            # An overload head that unescapes to `a//b`: the fallback path refused the
            # empty component, the overload loop did not, and `a..b(int)` was read.
            "Java_a__b__I",
            # Not this scheme at all.
            "JNI_OnLoad",
            "JavaScript_thing",
        ],
    )
    def test_a_name_that_does_not_decode_is_passed_through(self, name):
        assert demangle.demangle(name) == name
        assert demangle.detect(name) != "jni"

    def test_another_scheme_s_name_is_left_to_it(self):
        """Not passed through -- read by the scheme it belongs to, and not by this one."""
        assert demangle.detect("_ZN3foo3barEv") == "itanium"
        assert demangle.demangle("_ZN3foo3barEv") == "foo::bar()"

    def test_strict_says_why(self):
        with pytest.raises(DemanglingError):
            demangle.demangle_strict("Java_pkg_C_m__Q", language="jni")


class TestWhatTheNameDoesNotSay:
    """The package/class boundary is not in the symbol, and this does not invent one."""

    def test_the_path_is_one_run(self):
        """`Java_a_b_c` is `a.b.c`; which of the dots is the class boundary is not said."""
        assert demangle.demangle("Java_a_b_c") == "a.b.c"

    def test_a_method_with_no_signature_reports_no_parameters(self):
        """`None`, not `()`: only an *overloaded* native carries a signature at all."""
        assert parse_jni_symbol("Java_pkg_C_m").parameters is None
        assert parse_jni_symbol("Java_pkg_C_m__I").parameters == ("int",)


class TestTheTree:
    def test_it_separates_what_text_cannot(self):
        """A parameter type contains dots of its own, so the types come back apart."""
        tree = demangle.parse("Java_pkg_C_m__Ljava_lang_String_2I")
        assert tree.spell() == "pkg.C.m(java.lang.String, int)"
        parameters = next(node for node in tree.walk() if node.kind == "parameters")
        assert [child.spell() for child in parameters.children()] == ["java.lang.String", "int"]

    def test_the_tree_spells_what_the_fast_path_spells(self):
        for name in ("Java_pkg_C_m", "Java_pkg_C_m__I", "Java_Foo_00024Bar_baz___3Ljava_lang_Object_2"):
            assert demangle.parse(name).spell() == demangle.demangle(name)


class TestDescriptors:
    @pytest.mark.parametrize(
        ("descriptor", "expected"),
        [
            ("", ()),
            ("I", ("int",)),
            ("[[J", ("long[][]",)),
            ("Ljava/lang/String;", ("java.lang.String",)),
            ("ILjava/lang/String;[B", ("int", "java.lang.String", "byte[]")),
        ],
    )
    def test_reading_an_argument_list(self, descriptor, expected):
        assert descriptor_types(descriptor) == expected

    @pytest.mark.parametrize("descriptor", ["Q", "L", "Ljava/lang/String", "[", "L;"])
    def test_what_is_not_a_descriptor(self, descriptor):
        with pytest.raises(DemangleFailure):
            descriptor_types(descriptor)


class TestReMangling:
    """The property that stands in for a reference demangler.

    Nothing else reads these, so agreeing with the corpus is agreeing with ourselves.
    What is checkable is that a reading re-encodes to the symbol it came from: the
    specification gives the encoder, `mangle` above implements it, and every name in the
    corpus goes back through it.
    """

    def test_every_corpus_name_re_encodes_to_itself(self, subtests):
        from .conftest import load_corpus

        pairs = load_corpus("jni-real-world.txt")
        assert len(pairs) == 50
        for mangled, _ in pairs:
            parsed = parse_jni_symbol(mangled)
            path = parsed.declaring.replace(".", "/") + "/" + parsed.method if parsed.declaring else parsed.method
            signature = None
            if parsed.parameters is not None:
                signature = "".join(_descriptor_of(one) for one in parsed.parameters)
            with subtests.test(mangled=mangled):
                assert mangle(path, signature) == mangled


#: Back the other way, for the round trip: a spelled type to its JVM descriptor.
_DESCRIPTORS = {
    "byte": "B",
    "char": "C",
    "double": "D",
    "float": "F",
    "int": "I",
    "long": "J",
    "short": "S",
    "void": "V",
    "boolean": "Z",
}


def _descriptor_of(spelled):
    arrays = 0
    while spelled.endswith("[]"):
        arrays += 1
        spelled = spelled[:-2]
    body = _DESCRIPTORS.get(spelled) or f"L{spelled.replace('.', '/')};"
    return "[" * arrays + body


class TestItClaimsNothingItShouldNot:
    """The screen that matters: a scheme offered every symbol in a binary."""

    def test_no_other_scheme_s_corpus_name_is_claimed(self, subtests):
        from .conftest import CONFORMANCE, load_corpus

        names = [
            path.name
            for path in sorted(CONFORMANCE.iterdir())
            if not path.name.endswith(".gz") and path.name != "jni-real-world.txt"
        ]
        for corpus in [*names, "itanium-libcxxabi.txt"]:
            for mangled, _ in load_corpus(corpus):
                if mangled.startswith("Java_"):
                    with subtests.test(mangled=mangled):
                        assert demangle.detect(mangled) != "jni"
