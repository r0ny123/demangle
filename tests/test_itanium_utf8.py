"""UTF-8 source-name byte lengths, checked with c++filt 2.42 arguments."""

import pytest

import demangle
from demangle.core.errors import ParseError, TruncatedError

NAMES = [
    ("_Z2év", "é()"),
    ("_Z3éav", "éa()"),
    ("_Z2é", "é"),
    ("_ZN2é1fEv", "é::f()"),
    ("_Z3€v", "€()"),
    ("_Z4😀v", "😀()"),
    ("_Z1fB2év", "f[abi:é]()"),
    ("_Z2éB3€v", "é[abi:€]()"),
    ("_ZW2é1fv", "f@é()"),
]


@pytest.mark.parametrize(("name", "expected"), NAMES)
@pytest.mark.parametrize("style", ["gnu", "llvm"])
def test_source_name_byte_lengths(name, expected, style):
    assert demangle.demangle_strict(name, style=style, language="itanium") == expected
    assert demangle.parse(name, language="itanium", style=style).spell() == expected


@pytest.mark.parametrize("name", ["_Z1év", "_Z2😀v", "_ZN1é1fEv", "_Z1fB1év", "_Z3\ud800v"])
def test_invalid_utf8_boundaries_are_parse_errors(name):
    with pytest.raises(ParseError):
        demangle.demangle_strict(name, language="itanium")
    assert demangle.demangle(name, language="itanium") == name


def test_truncation_reports_codepoint_position():
    with pytest.raises(TruncatedError) as raised:
        demangle.demangle_strict("_ZN2é4a", language="itanium")
    assert raised.value.position == 6


def test_bytes_api_uses_same_source_name_length():
    assert demangle.demangleb_strict("_Z2év".encode(), language="itanium") == "é()".encode()
    assert demangle.demangleb(b"_Z1\xffv", language="itanium") == b"_Z1\xffv"
