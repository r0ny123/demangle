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
from collections.abc import Iterator
from typing import NamedTuple

from .api import _resolve as _resolve_language
from .api import demangle as _demangle
from .core.limits import DEFAULT_LIMITS, Limits
from .core.style import DEFAULT_STYLE, Style, get_style

__all__ = ["Found", "demangle_stream", "demangle_text", "find_symbols"]

#: A candidate symbol in a stream of mixed text. Deliberately wider than any one scheme:
#: offering a word that turns out not to be mangled costs one failed prefix test, and not
#: offering one loses a symbol silently.
#:
#: `?` and `@` are here for MSVC, `$` for Swift and Free Pascal, `.` for clone suffixes
#: and Go package paths, `-`, `+`, `[` and `]` for Objective-C method names, `/` for Go
#: import paths, and `%` and `#` for Delphi -- which writes a template argument list
#: `%...%` and a virtual-method-table flag `#$cf$`. Without those two the tokeniser cut
#: 638 readable names of the corpora in half, and what it handed back was worse than
#: nothing: `@%TAutoDriver$24Shdocvw_tlb@IWebBrowser2%@$bnot$xqv` came apart into
#: `@$bnot$xqv`, which reads as `operator !() const` -- a real declaration, belonging to
#: a class the fragment no longer names.
#:
#: `<` and `>` are deliberately *not* here, though MSVC writes `<unnamed-type-a>` and
#: `<lambda_0>` and CodeWarrior writes a template argument list in them. objdump spells a
#: call target `call 1050 <_ZN3foo3barEv>`, and a token that takes the brackets in is a
#: token no scheme reads -- so admitting them to recover 99 corpus names would lose the
#: symbol in the listing this module exists to filter. Measured both ways round.
#:
#: An Objective-C *method* is the one name here with a space inside it -- `+[Alpha
#: copy_it:]` is a class and a selector -- so it cannot be a run of word characters and
#: gets an alternative of its own, tried first. It is anchored on both sides: a `+` or
#: `-` immediately followed by `[`, then everything up to the first `]` on that line.
#: That cannot swallow prose, because ordinary text does not open with `+[`.
#:
#: What this fixes is the tokenising, not the finding. A method reaches `find_symbols`
#: from a symbol table in its *mangled* form -- `_i_Alpha319_copy_it_`, which holds none
#: of these characters -- and that has always been found. The bracketed form is what the
#: mangled one demangles *to*, so it spells itself and is declined either way; before
#: this it was declined as the two fragments `+[Alpha` and `copy_it:]`, which is the
#: wrong reading of one name rather than the right reading of two.
TOKEN = re.compile(r"[+-]\[[^\[\]\r\n]*\]|[A-Za-z0-9_$@?.\-+\[\]/:%#]+")

#: A word is worth offering only if it holds one of these. Without it every ordinary word
#: in a disassembly listing walks the whole detection chain, and `demumble`'s warning
#: applies -- `I like Pi` should not become `I like int*`.
TOKEN_MUST_HOLD = re.compile(r"[_$?@\[]")


#: A marker or two, then one plain identifier: `@Override`, `@@Base`, `.text`. A reading
#: of one of these is worth reporting only if it says something the word did not already
#: say, and two shapes do not.
#:
#: `@Name` is a Delphi symbol -- a unit-scope routine, and Embarcadero's own `tdump -um`
#: reads it as `Name`, which is why `demangle()` does and why 33 of them are in
#: `tests/conformance/delphi-tdump.txt`. `@@Name` is another, a runtime linker
#: procedure, read as `__linkproc__ Name`. They are also a Java annotation, a Python
#: decorator, a Swift attribute, a D attribute and an ELF version suffix, and this module
#: is run over whole files: `@Override public void f()` came back
#: `Override public void f()`, `use @property here` came back `use property here`, a
#: Swift signature this library had just printed came back with its `@escaping` and
#: `@autoclosure` shaved off, and `typeinfo for X const*@@CXXABI_FLOAT128` came back with
#: `__linkproc__ CXXABI_FLOAT128` where the version had been.
#:
#: What those readings have in common is that the identifier survives them whole: all
#: they add is the marker's name, or nothing at all. The caller can see the identifier
#: already, and cannot see whether it was an annotation -- so the filter declines them.
#: `demangle()` still reads them: there the caller has said the word is a name. A reading
#: that says more is untouched -- `._OBJC_CLASS_Alpha319` is `Objective-C class
#: Alpha319`, which drops `_OBJC_CLASS_` and is not the word back again, and the 475 of
#: those in the corpus are still found.
#:
#: The identifier may carry dots of its own: an ELF version is `@GLIBCXX_3.4` and a
#: Swift attribute is `@Swift.MainActor`, and a rule that stopped at the first dot left
#: both of those being shaved down to what follows the marker.
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
    language: str | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> Iterator[Found]:
    """Yield every symbol in `text` that this library can read, in order.

    Only the ones it *can* read: a word that looks symbol-shaped and does not demangle is
    not a symbol, and is left out rather than reported as one that spells itself.

    The spans do not overlap and are in increasing order, so a caller can rebuild the
    string around them -- which is all `demangle_text` does.
    """
    # Validated here rather than on first match: a text with no symbols would otherwise
    # never reach `demangle`, and a typo'd language/style would pass silently.
    _resolve_language(language)
    get_style(style)
    return _find_symbols(text, language=language, style=style, limits=limits)


def _find_symbols(
    text: str,
    *,
    language: str | None,
    style: str | Style | None,
    limits: Limits,
) -> Iterator[Found]:
    for match in TOKEN.finditer(text):
        word = match.group()
        if not TOKEN_MUST_HOLD.search(word):
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
    language: str | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> str:
    """Substitute every symbol in `text`, copying everything else through unchanged.

    Never raises over the text, for the reason `demangle()` does not: this is run over
    whole files, and one unreadable word must not end the run. Like `demangle()`, it
    raises `ValueError` when `language` or `style` is not a registered name.
    """
    _resolve_language(language)
    get_style(style)
    return _demangle_text(text, language=language, style=style, limits=limits)


def _demangle_text(
    text: str,
    *,
    language: str | None,
    style: str | Style | None,
    limits: Limits,
) -> str:
    """`demangle_text` with its arguments already validated, for the callers that loop."""
    pieces = []
    end = 0
    for found in _find_symbols(text, language=language, style=style, limits=limits):
        pieces.append(text[end : found.start])
        pieces.append(found.demangled)
        end = found.end
    if not pieces:
        return text
    pieces.append(text[end:])
    return "".join(pieces)


def demangle_stream(
    fin,
    fout,
    *,
    language: str | None = None,
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
    # Validated once here, not once per line: `_demangle_text` is the unchecked form.
    _resolve_language(language)
    get_style(style)
    for line in fin:
        fout.write(_demangle_text(line, language=language, style=style, limits=limits))
