"""Demangling the symbols out of text that is not only symbols.

`nm` writes an address and a type letter before a name; a crash log writes a frame
number; an objdump listing writes a whole disassembly around one. So the useful operation
over a *file* is not "demangle this name" but "substitute every symbol-shaped word and
copy everything else through" -- which is what `c++filt`, `demumble` and `rustfilt` all
do, and what the `demangle` command does with no arguments.

It lived inside the command until now, so a Python caller who wanted it had to shell out
to our own CLI or re-implement the tokenizer. rustc-demangle ships `demangle_stream` as a
crate function rather than only inside `rustfilt`, and this is the same thing:

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

from .api import demangle as _demangle
from .core.limits import DEFAULT_LIMITS, Limits
from .core.style import DEFAULT_STYLE, Style

__all__ = ["Found", "demangle_stream", "demangle_text", "find_symbols"]

#: A candidate symbol in a stream of mixed text. Deliberately wider than any one scheme:
#: offering a word that turns out not to be mangled costs one failed prefix test, and not
#: offering one loses a symbol silently.
#:
#: `?` and `@` are here for MSVC, `$` for Swift and Free Pascal, `.` for clone suffixes
#: and Go package paths, `-`, `+`, `[` and `]` for Objective-C method names, `/` for Go
#: import paths.
TOKEN = re.compile(r"[A-Za-z0-9_$@?.\-+\[\]/:]+")

#: A word is worth offering only if it holds one of these. Without it every ordinary word
#: in a disassembly listing walks the whole detection chain, and `demumble`'s warning
#: applies -- `I like Pi` should not become `I like int*`.
TOKEN_MUST_HOLD = re.compile(r"[_$?@\[]")


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
    for match in TOKEN.finditer(text):
        word = match.group()
        if not TOKEN_MUST_HOLD.search(word):
            continue
        spelled = _demangle(word, language=language, style=style, limits=limits)
        if spelled != word:
            yield Found(match.start(), match.end(), word, spelled)


def demangle_text(
    text: str,
    *,
    language: str | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> str:
    """Substitute every symbol in `text`, copying everything else through unchanged.

    Never raises for any string, for the reason `demangle()` does not: this is run over
    whole files, and one unreadable word must not end the run.
    """
    pieces = []
    end = 0
    for found in find_symbols(text, language=language, style=style, limits=limits):
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
    for line in fin:
        fout.write(demangle_text(line, language=language, style=style, limits=limits))
