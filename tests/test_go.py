"""Go symbol names.

Go has no reference demangler, so most of this file is not "does it match a reference".
The correctness argument is a property that needs no reference: re-escaping a decoded
package path must reproduce the bytes the Go linker wrote. `escape_path` is a
transcription of Go's own `objabi.PathToPrefix` -- the function that produced these
names -- so a decoding that survives that round trip is one the linker would have
written, and one that does not is wrong regardless of what any tool says about it.

`TestRoundTrip` checks it over every symbol in the corpus. `TestAgainstTheCompiler`
checks the escaping itself against values taken from the Go source, and against symbols
a real `go build` emitted.
"""

import pathlib

import pytest

import demangle
from demangle.core.errors import DemanglingError
from demangle.schemes.go import detect
from demangle.schemes.go._parser import escape_path, parse_go_symbol, unescape_path
from demangle.schemes.go.nodes import Symbol as GoTree

CORPUS = pathlib.Path(__file__).parent / "conformance" / "go-real-world.txt"


def corpus():
    rows = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "\t" in line:
            mangled, expected = line.split("\t", 1)
            rows.append((mangled, expected))
    return rows


ROWS = corpus()


class TestRoundTrip:
    """The property the whole scheme rests on."""

    def test_the_corpus_is_not_empty(self):
        assert len(ROWS) > 1000

    def test_the_corpus_actually_exercises_the_escaping(self):
        """Guards against a corpus that covers everything except the encoded part.

        The shipped Go toolchain contains no escaped symbol at all, so a corpus read only
        from it would pass every test here while the decoder is broken.
        """
        assert sum(1 for mangled, _ in ROWS if "%" in mangled) >= 10

    def test_every_decoded_package_re_escapes_to_what_the_linker_wrote(self, subtests):
        for mangled, _ in ROWS:
            symbol = parse_go_symbol(mangled)
            with subtests.test(name=mangled):
                written = mangled[len(symbol.generated) :]
                assert written.startswith(escape_path(symbol.package))

    def test_every_corpus_row_spells_as_recorded(self, subtests):
        for mangled, expected in ROWS:
            with subtests.test(name=mangled):
                assert demangle.demangle(mangled, language="go") == expected

    def test_a_tree_spells_what_the_text_path_spells(self, subtests):
        for mangled, expected in ROWS:
            with subtests.test(name=mangled):
                assert demangle.parse(mangled, language="go").spell() == expected


class TestAgainstTheCompiler:
    """Values taken from Go's own source and from what `go build` emitted."""

    @pytest.mark.parametrize(
        ("path", "escaped"),
        [
            # A `.` after the last `/` is escaped; one before it is not. This is the rule
            # that makes the package boundary findable at all.
            ("example.com/corpus/v2.5", "example.com/corpus/v2%2e5"),
            ("example.com/corpus/weird.pkg.name", "example.com/corpus/weird%2epkg%2ename"),
            ("example.com/corpus/plain", "example.com/corpus/plain"),
            # Escaping is over UTF-8 bytes, not characters: `ü` is two bytes.
            ("pkg-ünï", "pkg-%c3%bcn%c3%af"),
            ('has"quote', "has%22quote"),
            ("has%percent", "has%25percent"),
        ],
    )
    def test_escape_matches_path_to_prefix(self, path, escaped):
        assert escape_path(path) == escaped
        assert unescape_path(escaped) == path

    @pytest.mark.parametrize(
        ("symbol", "expected"),
        [
            # Emitted by go1.24.7 for tools/corpus_sources/go.
            ("example.com/corpus/v2%2e5.Ünïcødé.Método", "example.com/corpus/v2.5.Ünïcødé.Método"),
            ("example.com/corpus/v2%2e5.(*Ünïcødé).Pointeró", "example.com/corpus/v2.5.(*Ünïcødé).Pointeró"),
            ("example.com/corpus/v2%2e5.Frëe[go.shape.int]", "example.com/corpus/v2.5.Frëe[go.shape.int]"),
            (
                "example.com/corpus/weird%2epkg%2ename..dict.G[string,int]",
                "example.com/corpus/weird.pkg.name..dict.G[string,int]",
            ),
        ],
    )
    def test_symbols_a_real_build_emitted(self, symbol, expected):
        assert demangle.demangle(symbol, language="go") == expected

    def test_a_malformed_escape_is_refused_as_go_refuses_it(self):
        """`PrefixToPath` errors rather than passing the `%` through."""
        for bad in ("example.com/x/y%2.T", "example.com/x/y%.T", "example.com/x/y%zz.T"):
            with pytest.raises(DemanglingError):
                demangle.demangle_strict(bad, language="go")


class TestStructure:
    def test_a_package_is_reachable_without_splitting_on_a_dot(self):
        """Splitting the raw name on `.` gets this wrong, which is why the tree exists."""
        tree = demangle.parse("example.com/corpus/v2%2e5.Ünïcødé.Método", language="go")
        assert next(tree.find("path")).text == "example.com/corpus/v2.5"

    def test_a_pointer_receiver_is_marked_as_one(self):
        tree = demangle.parse("example.com/corpus/v2%2e5.(*Ünïcødé).Pointeró", language="go")
        receiver = next(tree.find("receiver"))
        assert receiver.pointer is True
        assert next(receiver.find("name")).text == "Ünïcødé"

    def test_a_generic_instantiation_carries_its_arguments(self):
        tree = demangle.parse("example.com/corpus/v2%2e5.Frëe[go.shape.int]", language="go")
        assert next(tree.find("template")).arguments == "go.shape.int"

    def test_a_generated_symbol_says_so(self):
        tree = demangle.parse("go:itab.*errors.errorString,error", language="go")
        assert isinstance(tree, GoTree)
        assert tree.generated == "go:"


class TestDetection:
    """Go is the one scheme with no marker, so detection must decline rather than guess."""

    @pytest.mark.parametrize(
        "name",
        [
            "example.com/corpus/v2%2e5.T.M",
            "example.com/x.(*T).M",
            "go:itab.*errors.errorString,error",
            "type:.eq.example.com/x.T",
        ],
    )
    def test_claims_what_is_unambiguously_go(self, name):
        assert detect(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            # Real Go symbols, deliberately not claimed: nothing distinguishes them from
            # any other dotted name, and guessing would rewrite names we are unsure of.
            "fmt.Println",
            "main.main",
            # Other schemes' names must never be claimed.
            "_ZNSt6vectorIiSaIiEE9push_backERKi",
            "?f@@YAXH@Z",
            "_RNvCsdEttCVZFADF_8features10btree_work",
            "_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E",
            "memcpy",
            "",
        ],
    )
    def test_declines_everything_else(self, name):
        assert detect(name) is False

    def test_the_unclaimed_ones_still_work_when_asked_for(self):
        assert demangle.demangle("fmt.Println", language="go") == "fmt.Println"

    def test_no_corpus_name_from_another_scheme_is_claimed(self):
        """Checked over every corpus rather than the handful above."""
        stolen = []
        conformance = pathlib.Path(__file__).parent / "conformance"
        for path in sorted([*conformance.glob("*.txt"), *conformance.glob("reported/*.txt")]):
            if path.stem.partition("-")[0] == "go":
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line and not line.startswith("#") and "\t" in line:
                    name = line.split("\t", 1)[0]
                    if detect(name):
                        stolen.append(name)
        assert stolen == []


class TestSafety:
    @pytest.mark.parametrize(
        "value",
        ["", "%", "%2", "%zz", "a/b.%", "a/b." + "%" * 100, "/" * 100 + ".x", "\x00/\x01.\x02"],
    )
    def test_demangle_never_raises(self, value):
        assert isinstance(demangle.demangle(value), str)
        assert isinstance(demangle.demangle(value, language="go"), str)

    def test_every_corpus_name_is_answered(self):
        for mangled, _ in ROWS:
            assert isinstance(demangle.demangle(mangled), str)

    @pytest.mark.parametrize("value", [".", "go:", "type:", "a/b.", ".foo"])
    def test_an_empty_package_or_name_is_refused(self, value):
        """`.` is not the empty string: a dot dropped is not a name read. Only a
        generated symbol may go without a package, and it still needs a name."""
        with pytest.raises(DemanglingError):
            parse_go_symbol(value)
        assert demangle.demangle(value, language="go") == value


class TestAGeneratedSymbolIsTheLinkerText:
    """What follows `go:` or `type:` is not a package-qualified declaration.

    It is a type string, or two of them, or the name of an object the linker made.
    Read as a declaration, the first `.` after the last `/` would be the package
    separator, so `type:.eq.[2]string` -- no slash, and a leading dot -- would lose its
    dot and read `type:eq.[2]string`, thirteen corpus rows pinning the loss; and with
    a slash the "package" would be whatever stands before the last one,
    `go:itab.*os.File,io` and the like, which the tree would report as the symbol's
    package. Every symbol here is go1.24.7 output.
    """

    @pytest.mark.parametrize(
        ("symbol", "expected"),
        [
            ("type:.eq.[2]string", "type:.eq.[2]string"),
            ("type:.eq.[2]runtime.Frame", "type:.eq.[2]runtime.Frame"),
            ("type:.hash.[2]string", "type:.hash.[2]string"),
            ("type:*", "type:*"),
            ("go:string.*", "go:string.*"),
            ("go:itab.*os.File,io.Reader", "go:itab.*os.File,io.Reader"),
            # Escaped paths inside the linker's text decode where they stand.
            ("type:.eq.example.com/tag/v2%2e5.K", "type:.eq.example.com/tag/v2.5.K"),
            ("type:.eq.main.Box[example.com/tag/v2%2e5.K]", "type:.eq.main.Box[example.com/tag/v2.5.K]"),
            (
                "go:itab.example.com/corpus/v2%2e5.Ünïcødé,example.com/corpus/v2%2e5.Iface",
                "go:itab.example.com/corpus/v2.5.Ünïcødé,example.com/corpus/v2.5.Iface",
            ),
        ],
    )
    def test_it_is_spelled_as_it_stands_with_its_escapes_decoded(self, symbol, expected):
        assert demangle.demangle(symbol, language="go") == expected
        assert demangle.parse(symbol, language="go").spell() == expected

    @pytest.mark.parametrize(
        "symbol",
        ["type:.eq.[2]string", "type:[]sync/atomic.Pointer[net.T]", "go:itab.*os.File,io.Reader"],
    )
    def test_it_has_no_package_receiver_or_instantiation(self, symbol):
        """`type:[]sync/atomic.Pointer[net.T]` ends in `]` and is not an instantiation of
        anything; `go:itab.*os.File,io.Reader` is not in package `itab`."""
        tree = demangle.parse(symbol, language="go")
        assert isinstance(tree, GoTree)
        assert next(tree.find("path"), None) is None
        assert next(tree.find("receiver"), None) is None
        assert next(tree.find("template"), None) is None
        assert tree.generated in ("go:", "type:")

    def test_a_generated_name_that_is_only_a_dot_keeps_it(self):
        assert demangle.demangle("go:.", language="go") == "go:."
        assert demangle.demangle("type:.", language="go") == "type:."


class TestEscapesOutsideTheLeadingPath:
    """A type string writes each named type with its package path escaped, and quotes
    a struct tag verbatim; both reach symbol names. All go1.24.7 output for a package
    directory called `v2.5` and a field tagged `json:"50%"` or `json:"a%2eb"`.
    """

    @pytest.mark.parametrize(
        ("symbol", "expected"),
        [
            ("main..dict.Gen[example.com/tag/v2%2e5.K]", "main..dict.Gen[example.com/tag/v2.5.K]"),
            ("main..dict.Box[example.com/tag/v2%2e5.K]", "main..dict.Box[example.com/tag/v2.5.K]"),
        ],
    )
    def test_a_path_inside_an_instantiation_decodes(self, symbol, expected):
        assert demangle.demangle(symbol, language="go") == expected
        assert (
            next(demangle.parse(symbol, language="go").find("template")).arguments
            == expected[expected.index("[") + 1 : -1]
        )

    @pytest.mark.parametrize(
        "symbol",
        [
            # `50%\"` is not an escape; the `%2e` beside it is.
            'type:.eq.struct { S string "json:\\"50%\\""; K example.com/tag/v2%2e5.K }',
            'type:.hash.struct { S string "json:\\"a%2eb\\" x:\\"q\\\\\\"z\\""; I interface {} }',
            'main.Gen[go.shape.struct { S string "json:\\"a%2eb\\" x:\\"q\\\\\\"z\\""; I interface {} }]',
            'main.(*Box[go.shape.struct { S string "json:\\"a%b\\" x:\\"q\\\\\\"z\\""; I interface {} }]).Get',
            # An escaped quote does not end the tag.
            'main.Gen[struct { S string "a\\"%2e" }]',
        ],
    )
    def test_a_percent_inside_a_struct_tag_is_not_an_escape(self, symbol):
        got = demangle.demangle_strict(symbol, language="go")
        assert got == symbol.replace("%2e5", ".5")

    def test_a_tag_percent_beside_a_real_escape(self):
        symbol = 'type:.eq.struct { S string "json:\\"50%\\""; K example.com/tag/v2%2e5.K }'
        expected = 'type:.eq.struct { S string "json:\\"50%\\""; K example.com/tag/v2.5.K }'
        assert demangle.demangle(symbol, language="go") == expected

    def test_a_malformed_escape_outside_quotes_is_still_refused(self):
        with pytest.raises(DemanglingError):
            demangle.demangle_strict("main.Gen[example.com/x/y%zz.T]", language="go")


class TestThePackageEndsBeforeAnyTypeString:
    """`example.com/x.F[go.shape.[]internal/sync.node]` is in `example.com/x`: the last
    `/` of the *package* is not the last `/` of the symbol once a receiver or an
    instantiation carries a path of its own. The package is not `example.com/x.F[go.shape.[]internal/sync`.
    """

    @pytest.mark.parametrize(
        ("symbol", "package", "rest"),
        [
            ("example.com/x.F[go.shape.[]internal/sync.node]", "example.com/x", "F[go.shape.[]internal/sync.node]"),
            ("example.com/x.(*T[internal/sync.node]).M", "example.com/x", "(*T[internal/sync.node]).M"),
            ("sync/atomic.(*Pointer[go.shape.struct { internal/sync.isEntry bool }]).Load", "sync/atomic", None),
            ("example.com/x.F", "example.com/x", "F"),
            ("main.F[internal/sync.node]", "main", "F[internal/sync.node]"),
        ],
    )
    def test_the_path_node_is_the_package(self, symbol, package, rest):
        parsed = parse_go_symbol(symbol)
        assert parsed.package == package
        if rest is not None:
            assert parsed.name == rest
        tree = demangle.parse(symbol, language="go")
        assert next(tree.find("path")).text == package
        assert tree.spell() == symbol

    def test_a_path_inside_the_brackets_is_still_evidence_of_go(self):
        assert detect("example.com/x.F[go.shape.[]internal/sync.node]") is True
        assert detect("main.F[internal/sync.node]") is True
        assert demangle.demangle("main.F[internal/sync.node]") == "main.F[internal/sync.node]"


class TestOutputIsAlwaysText:
    """A result `demangle()` returns must be a string a caller can write out.

    `unescape_path` is a faithful port of `PrefixToPath`, which works on bytes; Go
    strings are byte strings and Go's own tooling is content to print whatever the
    escapes decoded to. This package returns `str`, so an escape that decodes to
    something that is not UTF-8 would arrive as a lone surrogate -- a string Python
    refuses to encode. A caller writing that to a file, a socket or JSON would get a
    `UnicodeEncodeError` out of `demangle()`, which is documented never to raise.

    `PathToPrefix` only produces such a name from a path that was not text to begin
    with, which the module system does not permit, so the symbol is refused and comes
    back unchanged.
    """

    @pytest.mark.parametrize(
        "symbol",
        [
            "example.com/x/pkg%89.Foo",  # a continuation byte with no lead byte
            "example.com/x/pkg%ff.Foo",  # never valid UTF-8 anywhere
            "example.com/x/%c3.Foo",  # a lead byte with no continuation
            "example.com/x/pkg%ed%a0%80.Foo",  # an encoded surrogate
        ],
    )
    def test_a_package_path_that_is_not_text_is_refused(self, symbol):
        with pytest.raises(DemanglingError):
            demangle.demangle_strict(symbol, language="go")
        assert demangle.demangle(symbol) == symbol

    def test_every_corpus_result_encodes(self):
        from .conftest import load_corpus

        for mangled, _ in load_corpus("go-real-world.txt"):
            demangle.demangle(mangled, language="go").encode("utf-8")

    def test_a_path_that_is_text_still_decodes(self):
        # The same mechanism, with bytes that do form UTF-8.
        assert demangle.demangle("example.com/x/pkg%c3%bc.Foo") == "example.com/x/pkgü.Foo"
