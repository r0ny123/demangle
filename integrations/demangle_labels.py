"""The part of the disassembler scripts that does not depend on a disassembler.

A host hands over the symbols it holds as `(address, name)` pairs. This decides which of
them the `demangle` library can read and what to call each one, and returns the labels.
Nothing here imports a host module, so it is tested like the rest of the repository; the
scripts beside it are the thin layer that reads a host's symbol table and writes the
labels back.

The calls are the ones `docs/labelling-a-symbol-table.md` recommends for that job:

* `language=` as an allow-list, so the origin of the table decides what may read a name;
* `demangle()` for the spelling, which never raises over a name, so a name the library
  cannot read is simply not labelled;
* `signature()` for the qualified name, which is the part of the spelling that does not
  change with the compiler.

    >>> import demangle_labels
    >>> options = demangle_labels.Options(languages=("itanium", "swift"))
    >>> [label.short for label in demangle_labels.plan_labels([(0x1000, "_ZN3foo3barEv")], options)]
    ['foo::bar']
"""

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import NamedTuple

try:
    import demangle
except ImportError as error:
    raise ImportError(
        "the `demangle` package is not installed for this Python; "
        "install it with the pip of the interpreter the disassembler runs"
    ) from error

__all__ = [
    "DEFAULT_OPTIONS",
    "ELLIPSIS",
    "READ_WITH_THE_UNDERSCORE",
    "Entry",
    "Label",
    "Options",
    "label_for",
    "plan_labels",
]

#: What a cut label ends in. ASCII, because a host's name and comment fields are not all
#: Unicode-clean.
ELLIPSIS = "..."

#: Control characters and the Unicode line breaks. A newline in a name would split a
#: comment; a NUL would end a string in a host written in C.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")


class Entry(NamedTuple):
    """One symbol a host holds. A plain `(address, name)` pair is an entry without a reading."""

    address: int
    """Where the host finds the symbol again. Returned unchanged in the label."""

    name: str
    """The symbol as the host stores it: the mangled name, if it is one."""

    host_label: str | None = None
    """What the host's own demangler makes of `name`, where it makes something.

    `None`, an empty string and `name` itself all mean the host left the name as it was.
    """


@dataclass(frozen=True, slots=True)
class Label:
    """What to call one symbol."""

    address: int
    """The `address` of the entry this labels."""

    name: str
    """The entry's `name`, unchanged: the key to find the symbol by, and what to keep."""

    language: str
    """The scheme that read the name: `"swift"`, `"rust"` and so on."""

    full: str
    """The whole readable spelling, parameters included. The same string `demangle()` returns."""

    short: str
    """The name without its signature: `signature().qualified_name`, then whatever the
    symbol table appended to it -- `.cold`, `@@GLIBCXX_3.4` -- which is what tells two
    copies of one function apart.

    Where the symbol is not a function or a variable but something about one -- `vtable
    for Foo`, `typeinfo name for Foo` -- the label is a phrase rather than a name, and this
    is `full`.
    """


@dataclass(frozen=True, slots=True)
class Options:
    """How names are read and what is done with the result."""

    languages: Sequence[str] | None = None
    """The schemes allowed to read a name, as `demangle()`'s `language=` takes them.

    `None` lets every scheme try. A sequence, even of one, is an allow-list: detection as
    usual among those schemes alone, which is not the same as forcing one. A bare string
    is refused for that reason.
    """

    only_left_mangled: bool = True
    """Label a name only where the host's own reading of it came to nothing.

    `False` labels every name the library can read, and leaves it to the caller what to do
    where the host has a reading of its own.
    """

    style: str | demangle.Style | None = None
    """The output style, as `demangle()`'s `style=` takes it. `None` is the default, `"llvm"`."""

    max_length: int | None = None
    """The longest `full` or `short` to return, in characters. Longer ones are cut and end in `...`.

    A templated C++ name can run to thousands of characters, which a host may refuse as a
    name or fit badly into a view. `None` returns them whole.
    """

    leading_underscore: bool = False
    """The table puts one underscore in front of the name the compiler wrote, as Mach-O does.

    A Mach-O symbol is `_` and then the name in the object code, so Nim's `area__mymod_u10`
    is `_area__mymod_u10` there and JNI's `Java_...` is `_Java_...`. Set, a name that
    begins with `_` is read without it, unless it reads as given in one of the schemes
    that already take the Mach-O underscore off themselves (`READ_WITH_THE_UNDERSCORE`):
    the Itanium reading of `__Z` names is Apple's, and `___Z..._block_invoke` is a C++
    block only with all three. A name that does not read without the underscore keeps its
    reading as given, if it has one.
    """

    def __post_init__(self) -> None:
        if isinstance(self.languages, str):
            raise TypeError(
                f"languages is a sequence of scheme names, not one name; write ({self.languages!r},) "
                "to allow only that scheme"
            )
        if self.languages is not None:
            object.__setattr__(self, "languages", tuple(self.languages))
        if self.max_length is not None and self.max_length <= len(ELLIPSIS):
            raise ValueError(f"max_length must leave room for {ELLIPSIS!r} and a name, got {self.max_length}")
        # The library refuses an unknown or empty `language` and an unknown `style`, and an
        # empty name is the cheapest call that makes it say so. Here rather than at the
        # first symbol, so a typo in a setting fails before a host has been touched.
        demangle.demangle("", language=self.languages, style=self.style)


DEFAULT_OPTIONS = Options()

#: The schemes whose Mach-O names already read as the symbol table holds them: each takes
#: the extra underscore off itself (`__Z`, `_$s`, `__D`, `__R`, `_OBJC_CLASS_$_`). Read
#: without it, `__Z` loses Apple's rules and `___Z3foov_block_invoke` is not a C++ block.
#: Every other scheme reads the underscore as part of the name: Nim's `_area__mymod_u10`
#: as `mymod._area`, an Ada name as GCC 2's C++, or not at all.
READ_WITH_THE_UNDERSCORE = frozenset({"itanium", "swift", "rust", "d", "objc"})

EntryLike = Entry | tuple[int, str] | tuple[int, str, str | None]


def label_for(entry: EntryLike, options: Options = DEFAULT_OPTIONS) -> Label | None:
    """The label for one entry, or `None` where there is nothing to apply.

    That is the answer for a name that is not mangled, for one no allowed scheme reads, for
    one the host has already read when `only_left_mangled` is set, and for one whose
    reading is what the host already shows.

    Raises:
        TypeError: the name is not a `str`. Decode a symbol table's bytes first.
    """
    address, name, host_label = Entry(*entry)
    if not name:
        return None
    host_reading = host_label if host_label and host_label != name else None
    if host_reading is not None and options.only_left_mangled:
        return None

    reading = _read(name, options)
    # On Mach-O, a scheme outside the set read the underscore as part of the name, if it
    # read anything; the name without it is the one the compiler wrote.
    if (
        options.leading_underscore
        and name.startswith("_")
        and (reading is None or reading[1].language not in READ_WITH_THE_UNDERSCORE)
    ):
        reading = _read(name[1:], options) or reading
    if reading is None:
        return None
    full, parts = reading
    if full == host_reading:
        return None

    spelling = _tidy(full, options.max_length)
    # Compared again once cut: with `max_length` set, what the host shows is the cut
    # spelling a run before this one applied, and applying it again changes nothing.
    if not spelling or spelling == host_reading:
        return None
    # A phrase such as `vtable for Foo` is already as short as it gets; cutting it down to
    # `Foo` would label the vtable as the class.
    short = spelling
    if parts.special is None:
        short = _tidy(parts.qualified_name + parts.decoration, options.max_length) or spelling
    return Label(address, name, parts.language, spelling, short)


def plan_labels(entries: Iterable[EntryLike], options: Options = DEFAULT_OPTIONS) -> Iterator[Label]:
    """The labels for a table, in the order of the table, one at a time.

    Lazy, so a caller that wraps `entries` in a generator of its own gets a progress count
    and a place to stop without this function knowing about either.
    """
    for entry in entries:
        label = label_for(entry, options)
        if label is not None:
            yield label


def _read(name: str, options: Options) -> tuple[str, demangle.Signature] | None:
    """The spelling of `name` and its parts, or `None` where no allowed scheme reads it."""
    full = demangle.demangle(name, language=options.languages, style=options.style)
    if full == name:
        return None
    try:
        parts = demangle.signature(name, language=options.languages, style=options.style)
    except demangle.DemanglingError:
        # `demangle()` has just read this name, and `signature()` reads it the same way, so
        # this is a defect in a scheme and not something about the name. The table has
        # other names, and a label that cannot say which scheme made it is not one to apply.
        return None
    return full, parts


def _tidy(text: str, limit: int | None) -> str:
    """`text` with control characters made spaces, and cut to `limit` characters."""
    text = _CONTROL.sub(" ", text).strip()
    if limit is not None and len(text) > limit:
        text = text[: limit - len(ELLIPSIS)].rstrip() + ELLIPSIS
    return text
