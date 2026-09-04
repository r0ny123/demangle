"""What every scheme must do alike, and the one place where they legitimately differ.

Parity here is *not* parity of output. The schemes decode different grammars, so the
same input cannot produce the same answer from two of them -- `_Z1fv` is Itanium and
`?f@@YAXH@Z` is MSVC, and neither reads the other. What has to match is the contract
around the answer, because that is what a caller writes code against:

* `demangle()` never raises and hands back a name it could not read, unchanged.
* `demangle_strict()` refuses with a `DemanglingError` and nothing else.
* `parse()` and `demangle_strict()` agree about whether a name is readable.
* the bytes entry points answer what the text ones answer.
* every scheme accepts every registered style.

`tools/invariants.py` checks a name against the entry points without naming a language,
which is the autodetected path. This names one, so a scheme that is only ever reached by
detection -- and therefore only ever asked about names detection already liked -- is
still held to the contract.
"""

import gzip
from pathlib import Path

import pytest

import demangle
from demangle.core.errors import DemanglingError

CONFORMANCE = Path(__file__).parent / "conformance"

LANGUAGES = sorted(demangle.languages())

#: Shapes that have caught something before: nothing, one character, a plain word, a NUL,
#: a non-character, a run of punctuation, and the two prefixes that announce a scheme
#: without saying anything after it.
UNREADABLE = ["", "x", "not_a_symbol_at_all", "\x00", "￾￿", "?" * 40, "_Z", "$s", "@@@"]

#: One readable name per scheme, from its own conformance corpus. Used to check that
#: every style is accepted on a name the scheme actually reads rather than on one it
#: hands back untouched.
STYLE_SAMPLES = {
    "ada": "yz__qrs",
    "codewarrior": "__dt__6CActorFv",
    "d": "_D5mypkg5mymod5Point4normMFZi",
    "delphi": "@$beql$qrx5_GUIDt1",
    "gnuv2": "AddAlignment__9ivTSolverUiP12ivInteractorP7ivTGlue",
    "go": "example.com/corpus/v2%2e5.Closure",
    "itanium": "_Z1fv",
    "jni": "Java_java_lang_System_arraycopy__Ljava_lang_Object_2ILjava_lang_Object_2II",
    "msvc": "?foo@@YAXI@Z",
    "nim": "DefaultRandSeed__pureZrandom_13",
    "objc": "._OBJC_CLASS_A_B209",
    "pascal": "A52_$$_A52_DECODER_READ$PA52_DECODER$POINTER$LONGINT$$LONGINT",
    "rust": "_RINvCsdEttCVZFADF_8features10apply_hrtbNCNvB2_8exercise0EB2_",
    "swift": "$S18resilient_protocol21ResilientBaseProtocolTL",
}

#: The one intentional divergence, and the reason it is one.
#:
#: For every other scheme, "the answer equals the input" means the name was refused. Ada
#: is the exception: a GNAT symbol is a lower-case dotted path with no marker, so a bare
#: identifier is a *valid* Ada unit name that spells itself, and libiberty's
#: `ada_demangle` returns it unchanged for the same reason. `detect()` still declines it
#: -- it demands something GNAT wrote that a C compiler would not -- so autodetection
#: never claims one; only a caller who has said `language="ada"` sees this.
IDENTITY_IS_A_READING = {"ada": {"x", "not_a_symbol_at_all"}}


def corpus_names():
    names, seen = [], set()
    for path in sorted(CONFORMANCE.iterdir()):
        if path.suffix == ".gz":
            text = gzip.decompress(path.read_bytes()).decode("utf-8", "surrogateescape")
        elif path.suffix == ".txt":
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
        else:
            continue
        for line in text.splitlines():
            if line and not line.startswith("#"):
                name = line.split("\t")[0]
                if name not in seen:
                    seen.add(name)
                    names.append(name)
    return names


@pytest.mark.parametrize("language", LANGUAGES)
class TestEverySchemeKeepsTheSameContract:
    def test_demangle_hands_back_what_it_cannot_read(self, language, subtests):
        for name in UNREADABLE:
            with subtests.test(name=name):
                assert demangle.demangle(name, language=language) == name

    def test_strict_refuses_with_a_demangling_error(self, language, subtests):
        for name in UNREADABLE:
            with subtests.test(name=name):
                try:
                    spelled = demangle.demangle_strict(name, language=language)
                except DemanglingError:
                    continue
                except Exception as exc:  # the point of the test
                    pytest.fail(f"{language} raised {type(exc).__name__} rather than a DemanglingError: {exc}")
                assert spelled != name or name in IDENTITY_IS_A_READING.get(language, set()), (
                    f"{language} returned {name!r} unchanged from the strict entry point, "
                    f"which is how the non-strict one says 'refused'"
                )

    def test_parse_and_strict_agree_about_what_is_readable(self, language, subtests):
        for name in UNREADABLE:
            with subtests.test(name=name):

                def readable(call, name=name):
                    try:
                        call(name, language=language)
                    except DemanglingError:
                        return False
                    except Exception as exc:  # the point of the test
                        pytest.fail(f"{language}: {call.__name__} raised {type(exc).__name__}: {exc}")
                    return True

                assert readable(demangle.parse) == readable(demangle.demangle_strict)

    def test_the_bytes_entry_point_hands_back_the_bytes(self, language, subtests):
        for name in UNREADABLE:
            payload = name.encode("utf-8", "surrogatepass")
            with subtests.test(name=name):
                assert demangle.demangleb(payload, language=language) == payload

    def test_every_style_is_accepted(self, language, subtests):
        # One name per scheme that the scheme actually reads. Demangling `_Z1fv`
        # under every language only exercises Itanium: the rest hand it back
        # untouched, so a style the scheme rejects would never be noticed.
        sample = STYLE_SAMPLES[language]
        for style in demangle.styles():
            with subtests.test(style=style):
                assert demangle.demangle(sample, language=language, style=style) != sample


def test_the_bytes_path_answers_what_the_text_path_answers():
    """Over every name in every corpus, whichever scheme claims it.

    Two decoders that agree on the corpora a reference wrote can still disagree on the
    boundary between them, and the byte path is where a surrogate or a stray high byte
    would show it. Autodetected on purpose: this is the entry point a caller reaches for
    when they have a symbol table and no idea what wrote it.
    """
    mismatched = []
    for name in corpus_names():
        payload = name.encode("utf-8", "surrogateescape")
        if demangle.demangleb(payload) != demangle.demangle(name).encode("utf-8", "surrogateescape"):
            mismatched.append(name)
    assert not mismatched, f"{len(mismatched)} name(s) read differently as bytes, e.g. {mismatched[:3]}"


def test_the_documented_divergence_is_still_the_only_one():
    """`IDENTITY_IS_A_READING` is an exception list, and an exception list rots.

    If Ada stops reading a bare identifier, or another scheme starts, this says so rather
    than letting the comment above go quietly out of date.
    """
    reading_themselves = {}
    for language in LANGUAGES:
        for name in UNREADABLE:
            try:
                if demangle.demangle_strict(name, language=language) == name:
                    reading_themselves.setdefault(language, set()).add(name)
            except DemanglingError:
                pass
    assert reading_themselves == IDENTITY_IS_A_READING
