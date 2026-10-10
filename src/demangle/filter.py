"""Demangling the symbols out of text that is not only symbols.

`nm` writes an address and a type letter before a name; a crash log writes a frame
number; an objdump listing writes a whole disassembly around one. So the useful operation
over a *file* is not "demangle this name" but "substitute every symbol-shaped word and
copy everything else through" -- which is what `c++filt`, `demumble` and `rustfilt` all
do, and what the `demangle` command does with no arguments.

rustc-demangle ships `demangle_stream` as a crate function rather than only inside
`rustfilt`, and this is the same thing, so a Python caller need neither shell out to the
command nor re-implement its tokeniser:

    >>> import demangle
    >>> demangle.demangle_text("0000000000001139 T _ZN3foo3barEv\\n")
    '0000000000001139 T foo::bar()\\n'

`find_symbols` is the same scan without the substitution, for a caller who needs to know
*where* in a line a symbol was -- which is the question `microsoftDemangle`'s `n_read`
out-parameter and `llvm-undname --warn-trailing` are asked in C.
"""

import re
from collections.abc import Iterator, Sequence
from typing import NamedTuple

from .api import _check_language, _check_limits
from .api import demangle as _demangle
from .core.limits import DEFAULT_LIMITS, Limits
from .core.style import DEFAULT_STYLE, Style, get_style

__all__ = ["Found", "demangle_stream", "demangle_text", "find_symbols"]

#: A candidate symbol in mixed text, deliberately wider than any one scheme: a false
#: candidate costs a failed prefix test, a missed one loses a symbol silently. `%` and `#`
#: are Delphi's (without them `@%TAutoDriver$...%@$bnot$xqv` split into a misleading
#: `@$bnot$xqv`). `<` and `>` are excluded so objdump's `call 1050 <_ZN3foo3barEv>`
#: still yields the symbol. An Objective-C method, the one name with a space, gets its
#: own alternative, anchored on `+[`/`-[` and the first `]`.
# Keep non-ASCII bytes and characters with their word: otherwise `_Z3foo` in an
# unreadable `_Z3foo\udcff` would be expanded on its own, corrupting the symbol.
# Non-ASCII punctuation is kept conservatively too, since guessing a boundary can
# change a name that the whole-name API correctly refuses.
TOKEN = re.compile(r"[+-]\[[^\[\]\r\n]*\]|(?:[A-Za-z0-9_$@?.\-+\[\]/:%#]|[^\x00-\x7f\s])+")
_ASCII_TOKEN = re.compile(r"[+-]\[[^\[\]\r\n]*\]|[A-Za-z0-9_$@?.\-+\[\]/:%#]+")

#: A word is offered only if it holds one of these: `I like Pi` must not become
#: `I like int*` (`demumble`'s warning).
TOKEN_MUST_HOLD = re.compile(r"[_$?@\[]")


#: A marker or two, then one identifier: `@Override`, `@@Base`, `.text`. `@Name` and
#: `@@Name` are Delphi symbols, but also annotations, decorators, attributes and ELF
#: version suffixes; a reading that only drops the marker or adds its name tells a
#: caller nothing, so the filter declines it (`demangle()` still reads them). The
#: identifier may carry dots (`@GLIBCXX_3.4`, `@Swift.MainActor`).
_MARKER_AND_NAME = re.compile(r"[@.]+([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\Z")


def _says_only_what_the_word_says(word, spelled):
    """Whether a reading of `word` adds nothing but the marker's name. See above."""
    match = _MARKER_AND_NAME.fullmatch(word)
    if match is None:
        return False
    identifier = match.group(1)
    return spelled == identifier or spelled.endswith(" " + identifier)


class Found(NamedTuple):
    """One symbol inside a longer string, and what it says."""

    start: int
    """Index of the first character of the mangled name."""

    end: int
    """Index one past its last character, so `text[start:end]` is the name."""

    mangled: str
    """The name as it appeared."""

    demangled: str
    """Its readable spelling."""


def find_symbols(
    text: str,
    *,
    language: str | Sequence[str] | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> Iterator[Found]:
    """Yield every symbol in `text` that this library can read, in order.

    Only the ones it *can* read: a word that looks symbol-shaped and does not demangle is
    not a symbol, and is left out rather than reported as one that spells itself.

    The spans do not overlap and are in increasing order, so a caller can rebuild the
    string around them -- which is all `demangle_text` does.
    """
    # Validated here: a text with no symbols would never reach `demangle`.
    language = _check_language(language)
    get_style(style)
    _check_limits(limits)
    return _find_symbols(text, language=language, style=style, limits=limits)


def _find_symbols(
    text: str,
    *,
    language: str | Sequence[str] | None,
    style: str | Style | None,
    limits: Limits,
) -> Iterator[Found]:
    token = _ASCII_TOKEN if text.isascii() else TOKEN
    for match in token.finditer(text):
        word = match.group()
        if word.isalnum() or not TOKEN_MUST_HOLD.search(word):
            continue
        spelled = _demangle(word, language=language, style=style, limits=limits)
        if spelled == word:
            continue
        if _says_only_what_the_word_says(word, spelled):
            continue
        yield Found(match.start(), match.end(), word, spelled)


def demangle_text(
    text: str,
    *,
    language: str | Sequence[str] | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> str:
    """Substitute every symbol in `text`, copying everything else through unchanged.

    Never raises over the text, for the reason `demangle()` does not: this is run over
    whole files, and one unreadable word must not end the run. Like `demangle()`, it
    raises `ValueError` when `language` or `style` is not a registered name, or `limits`
    is not a `Limits`.
    """
    language = _check_language(language)
    get_style(style)
    _check_limits(limits)
    return _demangle_text(text, language=language, style=style, limits=limits)


def _demangle_text(
    text: str,
    *,
    language: str | Sequence[str] | None,
    style: str | Style | None,
    limits: Limits,
) -> str:
    """`demangle_text` with its arguments already validated, for the callers that loop."""
    if not TOKEN_MUST_HOLD.search(text):
        return text

    def replace(match):
        word = match.group()
        if word.isalnum() or not TOKEN_MUST_HOLD.search(word):
            return word
        spelled = _demangle(word, language=language, style=style, limits=limits)
        return word if _says_only_what_the_word_says(word, spelled) else spelled

    token = _ASCII_TOKEN if text.isascii() else TOKEN
    return token.sub(replace, text)


def demangle_stream(
    fin,
    fout,
    *,
    language: str | Sequence[str] | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> None:
    """`demangle_text` over a file, a line at a time.

    A line at a time rather than all at once, so this stays a pipe: someone watching
    `nm ... | demangle` should not have to wait for the input to end. `fin` is anything
    iterable by lines and `fout` anything with `write`.

    Encoding is the caller's: a symbol table holds bytes and they are not reliably UTF-8,
    so open both ends with `errors="surrogateescape"` if the input is one. The `demangle`
    command does exactly that.
    """
    language = _check_language(language)
    get_style(style)
    _check_limits(limits)
    for line in fin:
        fout.write(_demangle_text(line, language=language, style=style, limits=limits))
