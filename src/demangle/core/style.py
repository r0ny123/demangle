"""Output styles.

Some spelling questions have more than one correct answer, and the reference
implementations answer them differently: llvm-cxxfilt prints `std::string` and
`Foo<Bar<int>>`, GNU c++filt prints the full `basic_string` specialisation and
`Foo<Bar<int> >`. Both describe the same types.

A `Style` bundles those choices so a caller picks one name -- `"llvm"`, `"gnu"` --
instead of threading half a dozen flags through every call. New languages add their own
options to the mapping without changing this type, which is why the per-language policy
is an opaque dict rather than a fixed set of fields.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
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


def _build_styles():
    from ..schemes.itanium.options import DEFAULT_OPTIONS, GNU_OPTIONS

    llvm = Style(
        name="llvm",
        spelling_builder=SPELLING_BUILDER,
        language_options={"itanium": DEFAULT_OPTIONS},
    )
    gnu = Style(
        name="gnu",
        spelling_builder=LEGACY_SPELLING_BUILDER,
        language_options={"itanium": GNU_OPTIONS},
    )
    return {"llvm": llvm, "gnu": gnu}


_STYLES = None

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


def get_style(name):
    """Look up a style by name. `None` gives the default."""
    global _STYLES
    if _STYLES is None:
        _STYLES = _build_styles()
    if name is None:
        return _STYLES["llvm"]
    if isinstance(name, Style):
        return name
    try:
        return _STYLES[name]
    except KeyError:
        raise ValueError(f"unknown style {name!r}; known styles are {sorted(_STYLES)}") from None


def register_style(style):
    """Add a style, so a downstream project can define its own house spelling."""
    global _STYLES
    if _STYLES is None:
        _STYLES = _build_styles()
    _STYLES[style.name] = style
    for callback in _on_change:
        callback()
    return style


def available_styles():
    global _STYLES
    if _STYLES is None:
        _STYLES = _build_styles()
    return sorted(_STYLES)


#: The default style. LLVM's spelling: what modern debuggers and disassemblers show.
DEFAULT_STYLE = "llvm"
