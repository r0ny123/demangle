"""Structural const generic arguments in the Rust v0 scheme.

Every `EXPECTED` pair below is taken from the test module of rustc-demangle 0.1.28
(`src/v0.rs`), which is the implementation `rustfilt` wraps and the one rustc's own
tooling uses, or was produced by running `rustfilt` over a symbol `rustc` emitted for
the sources in `tools/corpus_sources/rust/`. The upstream tests wrap each const body as
`_RIC0K<body>E`, so a bare value shows up as `::<value>`; that wrapper is kept here so
the mangled strings can be compared against upstream by eye.

These productions only reach a symbol name through `adt_const_params`, which is why they
are worth pinning separately: the checked-in corpus can only carry what the installed
toolchain happens to emit, while the grammar is fixed by RFC 2603.
"""

import unittest

import demangle
from demangle.core.errors import DemanglingError

#: `<const>` bodies and the spelling the reference produces, grouped by production.
EXPECTED = [
    # -- placeholder and scalar leaves, for contrast with the composite forms below
    ("p", "_"),
    ("h7b_", "123"),
    ("anb_", "-11"),
    ("b0_", "false"),
    ("b1_", "true"),
    ("c76_", "'v'"),
    # a quote is left bare inside the opposite quote, and escaped inside its own
    ("c22_", "'\"'"),
    ("c27_", "'\\''"),
    ("ca_", "'\\n'"),
    ("c2202_", "'∂'"),
    # `escape_debug` escapes a grapheme-extending character so it cannot attach itself
    # to the quote: a combining mark by category, plus the `Other_Grapheme_Extend` list
    # (U+0DDF is `Mc` and U+FF9E is `Lm`).
    ("c300_", "'\\u{300}'"),
    ("cddf_", "'\\u{ddf}'"),
    ("cff9e_", "'\\u{ff9e}'"),
    ("c1d165_", "'\\u{1d165}'"),
    # leading zeroes are stripped before the 64-bit width test, so this is not `0x...`
    ("j00000000000000000001_", "1"),
    # ... but a genuine `u128` is echoed as hex rather than rejected
    ("off00ff00ff00ff00ff_", "0xff00ff00ff00ff00ff"),
    # -- "A": arrays
    ("AE", "{[]}"),
    ("Aj0_E", "{[0]}"),
    ("Ah1_h2_h3_E", "{[1, 2, 3]}"),
    ("AAh1_h2_EAh3_h4_EE", "{[[1, 2], [3, 4]]}"),
    ("ARe61_Re62_Re63_E", '{["a", "b", "c"]}'),
    # -- "T": tuples. A one-element tuple keeps the comma that makes it one
    ("TE", "{()}"),
    ("Tj0_E", "{(0,)}"),
    ("Th1_b0_E", "{(1, false)}"),
    ("TRe616263_c78_RAh1_h2_h3_EE", "{(\"abc\", 'x', &[1, 2, 3])}"),
    # -- "V": struct and enum-variant values, in all three field shapes
    ("VNvINtNtC4core6option6OptionjE4NoneU", "{core::option::Option::<usize>::None}"),
    ("VNvINtNtC4core6option6OptionjE4SomeTj0_E", "{core::option::Option::<usize>::Some(0)}"),
    (
        "VNtC3foo3BarS1sRe616263_2chc78_5sliceRAh1_h2_h3_EE",
        "{foo::Bar { s: \"abc\", ch: 'x', slice: &[1, 2, 3] }}",
    ),
    # -- "R"/"Q": references, and the "Re" spelling that is a string literal instead
    ("Rp", "{&_}"),
    ("Rh7b_", "{&123}"),
    ("Rb0_", "{&false}"),
    ("Rc58_", "{&'X'}"),
    ("RRRh0_", "{&&&0}"),
    ("RRRe_", '{&&""}'),
    ("QAE", "{&mut []}"),
    ("Re_", '""'),
    ("Re616263_", '"abc"'),
    ("Re27_", '"\'"'),
    ("Re090a_", '"\\t\\n"'),
    ("Ree28882c3bc_", '"∂ü"'),
    # -- "e": a bare `str` const has no Rust syntax, so the reference derefs a literal
    ("e616263_", '{*"abc"}'),
    ("e090a_", '{*"\\t\\n"}'),
]

#: Real `rustc` output, spelled by `rustfilt`; see the module docstring.
REAL_WORLD = [
    ("_RINvCsipD1KD37Gle_6consts8with_arrKAh1_h2_h3_EEB2_", "consts::with_arr::<{[1, 2, 3]}>"),
    ("_RINvCsipD1KD37Gle_6consts10with_tupleKTh7_b1_EEB2_", "consts::with_tuple::<{(7, true)}>"),
    (
        "_RINvCsipD1KD37Gle_6consts11with_structKVNtB2_5PointS1xln5_1yhc8_EEB2_",
        "consts::with_struct::<{consts::Point { x: -5, y: 200 }}>",
    ),
    ("_RINvCsipD1KD37Gle_6consts9with_enumKVNtNtB2_4Kind1AUEB2_", "consts::with_enum::<{consts::Kind::A}>"),
    (
        "_RINvCsipD1KD37Gle_6consts9with_enumKVNtNtB2_4Kind1BTh3_EEB2_",
        "consts::with_enum::<{consts::Kind::B(3)}>",
    ),
    (
        "_RINvCsipD1KD37Gle_6consts9with_enumKVNtNtB2_4Kind1CS1zsn2_EEB2_",
        "consts::with_enum::<{consts::Kind::C { z: -2 }}>",
    ),
    ("_RINvCsipD1KD37Gle_6consts8with_strKRe68c3a96c6c6f_EB2_", 'consts::with_str::<"héllo">'),
    ("_RINvCsipD1KD37Gle_6consts8with_strKRe_EB2_", 'consts::with_str::<"">'),
]

#: One truncated or otherwise malformed input per production.
MALFORMED = [
    "_RIC0KA",  # array, no elements and no terminator
    "_RIC0KAh1_",  # array, one element, truncated before "E"
    "_RIC0KT",  # tuple, truncated immediately
    "_RIC0KTh1_",  # tuple, truncated before "E"
    "_RIC0KV",  # struct value, no path
    "_RIC0KVNtC3foo3Bar",  # struct value, path but no field shape
    "_RIC0KVNtC3foo3BarX",  # struct value, unknown field shape
    "_RIC0KVNtC3foo3BarS1a",  # named field with no value
    "_RIC0KR",  # reference to nothing
    "_RIC0KQ",  # mutable reference to nothing
    "_RIC0KRe",  # string literal with no terminator
    "_RIC0KRex_",  # string literal with a non-hex digit
    "_RIC0KRe616_",  # string literal with an odd nibble count
    "_RIC0KReff_",  # string literal that is not valid UTF-8
    "_RIC0KRed800_",  # string literal encoding a lone surrogate
    "_RIC0KAB_E",  # array whose element is a backref pointing before itself
    "_RIC0Kcd800_",  # char in the surrogate range, which is not a Rust char
    "_RIC0Kc110000_",  # char above the maximum scalar value
    "_RIC0Kb2_",  # bool that is neither 0 nor 1
    "_RIC0AhKj3_E",  # array length wrongly marked as a generic argument with "K"
]


class TestStructuralConsts(unittest.TestCase):
    """The `A`, `T`, `V`, `R`/`Q` and `e` productions of `<const>`."""

    def test_reference_spellings(self):
        for body, spelling in EXPECTED:
            mangled = f"_RIC0K{body}E"
            with self.subTest(body=body):
                self.assertEqual(demangle.demangle_strict(mangled, language="rust"), f"::<{spelling}>")

    def test_real_rustc_output(self):
        for mangled, spelling in REAL_WORLD:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle.demangle_strict(mangled, language="rust"), spelling)

    def test_nested_consts_are_not_braced_again(self):
        """Braces mark the outermost expression only.

        `::<{[[1, 2], [3, 4]]}>` rather than `::<{[{[1, 2]}, {[3, 4]}]}>`: an element is
        already inside an expression, so it needs no disambiguation of its own.
        """
        got = demangle.demangle_strict("_RIC0KAAh1_h2_EAh3_h4_EEE", language="rust")
        self.assertEqual(got.count("{"), 1)

    def test_array_length_in_type_position_is_never_braced(self):
        """`[T; N]` is already an expression context, however structural `N` is.

        The length follows the element type directly: `K` marks a const in *generic
        argument* position, and there is no generic argument here.
        """
        self.assertEqual(demangle.demangle_strict("_RIC0Ahj3_E", language="rust"), "::<[u8; 3]>")
        self.assertEqual(demangle.demangle_strict("_RIC0AhTj3_j4_EE", language="rust"), "::<[u8; (3, 4)]>")

    def test_backref_to_a_const_decides_its_own_braces(self):
        """A `B` const resolves and prints, passing the brace decision through.

        `B3_` points at the whole array and so is braced like any outermost structural
        const; `B4_` points *into* it, at the leaf `h1_`, and a leaf never braces.
        """
        self.assertEqual(
            demangle.demangle_strict("_RIC0KAh1_h2_EKB3_E", language="rust"),
            "::<{[1, 2]}, {[1, 2]}>",
        )
        self.assertEqual(demangle.demangle_strict("_RIC0KAh1_h2_EKB4_E", language="rust"), "::<{[1, 2]}, 1>")


class TestSkipReferenceWithLifetime(unittest.TestCase):
    """Regression: the skip pass must consume a reference's referent, lifetime or not.

    `<const>` is not the only thing the skipper walks. Impl paths are reached through it,
    so a `R`/`Q` type that stopped after its optional lifetime desynchronised everything
    after it and turned a valid name into a parse failure. Spellings are `rustfilt`'s.
    """

    def test_reference_self_types_survive_the_skip_pass(self):
        cases = [
            ("_RNvXs_C3fooRL_hNtC3foo5Trait6method", "<&u8 as foo::Trait>::method"),
            ("_RNvXs_C3fooQL_hNtC3foo5Trait6method", "<&mut u8 as foo::Trait>::method"),
            ("_RNvXs_C3fooRhNtC3foo5Trait6method", "<&u8 as foo::Trait>::method"),
            ("_RNvMs_C3fooRL_h3baz", "<&u8>::baz"),
        ]
        for mangled, spelling in cases:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle.demangle_strict(mangled, language="rust"), spelling)


class TestDynTraitAssociatedConsts(unittest.TestCase):
    """A trait object may bind an associated const, not only an associated type.

    Both are spelled `p <ident> ...` after the trait; a `K` in front of the value is what
    makes it a const. Spellings are `rustfilt`'s.
    """

    def test_associated_const_bindings(self):
        cases = [
            ("_RIC0DNtC3foo5Traitp5AssocKh1_EL_E", "::<dyn foo::Trait<Assoc = 1>>"),
            ("_RIC0DNtC3foo5Traitp5AssochEL_E", "::<dyn foo::Trait<Assoc = u8>>"),
            (
                "_RIC0DNtC3foo5Traitp3LenKj4_p5AssocRhEL_E",
                "::<dyn foo::Trait<Len = 4, Assoc = &u8>>",
            ),
            ("_RIC0DNtC3foo5Traitp3ArrKAh1_h2_EEL_E", "::<dyn foo::Trait<Arr = {[1, 2]}>>"),
        ]
        for mangled, spelling in cases:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle.demangle_strict(mangled, language="rust"), spelling)


class TestMalformedConsts(unittest.TestCase):
    """Truncated and ill-formed consts degrade rather than escaping the library."""

    def test_best_effort_returns_the_name_unchanged(self):
        for mangled in MALFORMED:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle.demangle(mangled), mangled)

    def test_strict_raises_a_demangling_error(self):
        for mangled in MALFORMED:
            with self.subTest(mangled=mangled), self.assertRaises(DemanglingError):
                demangle.demangle_strict(mangled, language="rust")

    def test_deep_nesting_is_bounded(self):
        """A const may not recurse without limit; the depth guard must fire first."""
        mangled = "_RIC0K" + "R" * 5000 + "h1_" + "E"
        self.assertEqual(demangle.demangle(mangled), mangled)
        with self.assertRaises(DemanglingError):
            demangle.demangle_strict(mangled, language="rust")


if __name__ == "__main__":
    unittest.main()
