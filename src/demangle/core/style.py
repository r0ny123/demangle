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
from typing import Any

from .spelling import LEGACY_SPELLING_BUILDER, SPELLING_BUILDER


@dataclass(frozen=True, slots=True)
class Style:
    """A named set of output policy choices."""

    name: str
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

            demangle(name, style=styles.get("llvm").with_options(msvc={"calling_convention": False}))

        The result is a `Style` object rather than a registered name, which is what keeps
        it a *per-call* policy: `demangle()` does not cache a call that passes one, so one
        caller's narrower spelling cannot be served to another asking for the default.
        """
        changed = dict(self.language_options)
        for language, value in languages.items():
            if isinstance(value, Mapping):
                current = changed.get(language)
                if current is None:
                    raise ValueError(
                        f"style {self.name!r} carries no options for {language!r} to change; "
                        f"pass that language's options object instead of a mapping"
                    )
                value = replace(current, **value)
            elif language not in changed:
                # The object form adds rather than changes, so the mapping check above
                # cannot see it -- but a typo must still fail rather than add dead
                # options under a name nothing reads. Looked up lazily so this module
                # keeps no import-time dependency on any scheme.
                from .registry import names as _known_languages

                known = _known_languages()
                if language not in known:
                    raise ValueError(f"unknown language {language!r}; known languages are {known}")
            changed[language] = value
        return replace(self, language_options=changed)


def _build_styles():
    from ..schemes.codewarrior.options import DEFAULT_OPTIONS as CODEWARRIOR_OPTIONS
    from ..schemes.gnuv2.options import DEFAULT_OPTIONS as GNUV2_OPTIONS
    from ..schemes.itanium.options import DEFAULT_OPTIONS, GNU_OPTIONS
    from ..schemes.msvc.options import DEFAULT_OPTIONS as MSVC_OPTIONS
    from ..schemes.rust.options import DEFAULT_OPTIONS as RUST_OPTIONS
    from ..schemes.swift.options import DEFAULT_OPTIONS as SWIFT_OPTIONS

    # MSVC's, Swift's and pre-Itanium C++'s options are the same in both styles, and
    # deliberately: they say how much of a name to print -- and, for pre-Itanium, which
    # of the five compilers wrote it -- which is not something the two C++ references
    # disagree about. They are here so `with_options(msvc=...)` has something to change.
    llvm = Style(
        name="llvm",
        spelling_builder=SPELLING_BUILDER,
        language_options={
            "itanium": DEFAULT_OPTIONS,
            "msvc": MSVC_OPTIONS,
            "swift": SWIFT_OPTIONS,
            "gnuv2": GNUV2_OPTIONS,
            "codewarrior": CODEWARRIOR_OPTIONS,
            "rust": RUST_OPTIONS,
        },
    )
    gnu = Style(
        name="gnu",
        spelling_builder=LEGACY_SPELLING_BUILDER,
        language_options={
            "itanium": GNU_OPTIONS,
            "msvc": MSVC_OPTIONS,
            "swift": SWIFT_OPTIONS,
            "gnuv2": GNUV2_OPTIONS,
            "codewarrior": CODEWARRIOR_OPTIONS,
            "rust": RUST_OPTIONS,
        },
    )
    return {"llvm": llvm, "gnu": gnu}


_STYLES = None

#: Guards the lazy build and every mutation of `_STYLES`. Two threads reaching
#: `get_style` first would each have built a table, and whichever finished last would
#: have discarded any style the other had registered into the first. The registry has
#: taken the same care since it was written; this module had not.
_lock = threading.RLock()

#: Called when the set of styles changes. `api` puts its cache's `clear` here, because a
#: style registered after a name was demangled must not be served the older spelling.
#:
#: A hook rather than a counter folded into the cache key: the key is built once per
#: `demangle()` call, which is the hottest path in the package, and asking two modules
#: "have you changed" there costs more than clearing a cache on the rare occasion that
#: one has. Measured -- the counter version cost 58% of the warm path.
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
    """Look up a style by name. `None` gives the default."""
    # `_table()`'s own fast path, written out: every call into this package resolves a
    # style first, so reaching an already-built table through a second interpreter frame
    # is a frame per name demangled. The build, and the lock around it, stay there.
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
    return sorted(_table())


#: The default style. LLVM's spelling: what modern debuggers and disassemblers show.
DEFAULT_STYLE = "llvm"
