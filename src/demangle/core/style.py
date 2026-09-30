"""Output styles.

Some spelling questions have more than one correct answer, and the reference
implementations answer them differently: llvm-cxxfilt prints `std::string` and
`Foo<Bar<int>>`, GNU c++filt prints the full `basic_string` specialisation and
`Foo<Bar<int> >`. Both describe the same types.

A `Style` bundles those choices so a caller picks one name -- `"llvm"`, `"gnu"` --
instead of threading half a dozen flags through every call. New languages add their own
options to the mapping without changing this type, which is why the per-language policy
is an opaque dict rather than a fixed set of fields.

Not every choice is a disagreement between references, though, and the second kind is
what `with_options` is for: *how much of a name to print*. Every peer tool lets a caller
compose those at the call site -- `llvm-undname --no-calling-convention`,
`UnDecorateSymbolName`'s mask, `c++filt -p` -- because "the same names, with less around
them" is what makes a symbol table greppable. A style is immutable, so composing one
returns a new style rather than changing the shared default, and a style object handed to
`demangle()` is deliberately not cached: it is one call's policy, not the process's.
"""

import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from importlib import import_module
from typing import Any

from .spelling import LEGACY_SPELLING_BUILDER, SPELLING_BUILDER


@dataclass(frozen=True, slots=True)
class Style:
    """A named set of output policy choices."""

    name: str
    """What the style is registered and looked up under -- `"llvm"`, `"gnu"`."""

    spelling_builder: Any
    """The builder used when a caller asks for text. Must be a `Builder`."""

    language_options: Mapping[str, Any] = field(default_factory=dict)
    """Per-language option objects, keyed by language name."""

    def options_for(self, language):
        """The option object this style specifies for `language`, or None."""
        return self.language_options.get(language)

    def with_options(self, **languages) -> "Style":
        """This style with some language's options changed, as a new style.

        Each keyword is a language name and each value is either that language's whole
        options object or a mapping of the fields to change in the one already in force:

            >>> import demangle
            >>> from demangle.core.style import get_style
            >>> narrow = get_style("llvm").with_options(msvc={"calling_convention": False})
            >>> demangle.demangle("?f@@YAXH@Z", style=narrow)
            'void f(int)'

        `demangle.style()` is the same thing spelled from the package.

        The result is a `Style` object rather than a registered name, which is what keeps
        it a *per-call* policy: `demangle()` does not cache a call that passes one, so one
        caller's narrower spelling cannot be served to another asking for the default.
        """
        options = self.language_options
        changed = {}
        for language, value in languages.items():
            if isinstance(value, Mapping):
                current = options.get(language)
                if current is None:
                    raise ValueError(
                        f"style {self.name!r} carries no options for {language!r} to change; "
                        f"pass that language's options object instead of a mapping"
                    )
                value = replace(current, **value)
            elif language not in options:
                # The object form adds rather than changes, so a typo must be caught
                # here. Imported lazily: no import-time dependency on any scheme.
                from .registry import names as _known_languages

                known = _known_languages()
                if language not in known:
                    raise ValueError(f"unknown language {language!r}; known languages are {known}")
            changed[language] = value
        if isinstance(options, _LazyOptions):
            # Only the languages changed are imported, not every one the style names.
            return replace(self, language_options=options.with_values(changed))
        return replace(self, language_options={**options, **changed})


class _LazyOptions(Mapping):
    """A style's per-language options, each imported the first time it is asked for.

    An options object lives in its scheme's package, and importing
    `demangle.schemes.swift.options` imports the whole Swift demangler first. Building
    the two built-in styles eagerly therefore imported six schemes on the first call into
    the package, whatever the name -- undoing the registry's care to import only the
    schemes a name reaches. Resolved one language at a time instead, a scheme's options
    are imported when that scheme parses something, which is when its package is loaded
    anyway.

    Reads take no lock. `import_module` serialises the import itself, every thread that
    races on a language resolves the same object, and the only write is storing it.
    """

    __slots__ = ("_resolved", "_sources")

    def __init__(self, sources, resolved=None):
        #: Language -> `(module, attribute)` still to be imported.
        self._sources = sources
        #: Language -> its options object, once imported or given.
        self._resolved = {} if resolved is None else resolved

    def __getitem__(self, language):
        value = self._resolved.get(language, _UNRESOLVED)
        if value is _UNRESOLVED:
            source = self._sources.get(language)
            if source is None:
                raise KeyError(language)
            value = getattr(import_module(source[0]), source[1])
            self._resolved[language] = value
        return value

    def get(self, language, default=None):
        value = self._resolved.get(language, _UNRESOLVED)
        if value is not _UNRESOLVED:
            return value
        return self[language] if language in self._sources else default

    def __contains__(self, language):
        return language in self._sources or language in self._resolved

    def __iter__(self):
        return iter({**self._sources, **self._resolved})

    def __len__(self):
        return len(self._sources.keys() | self._resolved.keys())

    def __repr__(self):
        return repr(dict(self))

    def with_values(self, changed):
        """A copy with `changed` in force, importing nothing it does not have to."""
        return _LazyOptions(self._sources, {**self._resolved, **changed})


_UNRESOLVED = object()


def _options(package, attribute="DEFAULT_OPTIONS"):
    return (f"demangle.schemes.{package}.options", attribute)


#: MSVC's, Swift's and pre-Itanium's options are the same in both styles: the two C++
#: references do not disagree about them. Listed so `with_options(msvc=...)` works.
_SHARED_OPTIONS = {
    "msvc": _options("msvc"),
    "swift": _options("swift"),
    "gnuv2": _options("gnuv2"),
    "codewarrior": _options("codewarrior"),
    "rust": _options("rust"),
}


def _build_styles():
    llvm = Style(
        name="llvm",
        spelling_builder=SPELLING_BUILDER,
        language_options=_LazyOptions({"itanium": _options("itanium"), **_SHARED_OPTIONS}),
    )
    gnu = Style(
        name="gnu",
        spelling_builder=LEGACY_SPELLING_BUILDER,
        language_options=_LazyOptions({"itanium": _options("itanium", "GNU_OPTIONS"), **_SHARED_OPTIONS}),
    )
    return {"llvm": llvm, "gnu": gnu}


_STYLES = None

#: Guards the lazy build and every mutation of `_STYLES`.
_lock = threading.RLock()

#: Called when the set of styles changes; `api` registers its cache's `clear` here. A
#: hook rather than a counter in the cache key, which would slow the hottest path.
_on_change = []


def notify_on_change(callback):
    """Call `callback` whenever a style is registered."""
    _on_change.append(callback)


def _registered_table():
    """The built table, for `is_registered` and for tests. Builds it if it is not yet."""
    return _table()


def _table():
    """The style table, built on first use."""
    global _STYLES
    if _STYLES is not None:
        return _STYLES
    with _lock:
        if _STYLES is None:
            _STYLES = _build_styles()
        return _STYLES


def get_style(name):
    """Look up a style by name. `None` gives the default, and a `Style` is returned as is.

    Raises:
        ValueError: `name` is not a registered style.
    """
    # `_table()`'s fast path inlined: every call into the package resolves a style.
    styles = _STYLES
    if styles is None:
        styles = _table()
    if name is None:
        return styles["llvm"]
    if isinstance(name, Style):
        return name
    try:
        return styles[name]
    except (KeyError, TypeError):
        raise ValueError(f"unknown style {name!r}; known styles are {sorted(styles)}") from None


def register_style(style):
    """Add a style, so a downstream project can define its own house spelling."""
    table = _table()
    with _lock:
        table[style.name] = style
    for callback in _on_change:
        callback()
    return style


def available_styles():
    """The registered style names, sorted. `demangle.styles()` is this."""
    return sorted(_table())


#: LLVM's spelling: what modern debuggers and disassemblers show.
DEFAULT_STYLE = "llvm"
