"""The public API.

Deliberately small. Five functions cover what callers actually do, and every one of
them is stable ground that later versions can build on without breaking:

    demangle(name)          the readable spelling, or the name unchanged   -- never raises
    demangle_strict(name)   the readable spelling                          -- raises
    parse(name)             a tree to inspect                              -- raises
    detect(name)            which scheme, if any
    demangle_all(names)     the batch form, cached

The split between `demangle` and `demangle_strict` is the important one. A tool
labelling every symbol in a binary meets far more non-mangled names than mangled ones,
and treating that as an error would mean an exception per symbol; it wants the name
back. A tool demangling one name a user typed wants to be told what was wrong with it.
Serving both from one function -- with a sentinel, or a flag -- makes the common case
carry the uncommon one's error handling, so they are two functions.
"""

from .core.ast import AST_BUILDER
from .core.cache import MISSING, BoundedCache
from .core.errors import DemanglingError, NotMangledError, reraise_if_operational
from .core.limits import DEFAULT_LIMITS
from .core.registry import available, get, names
from .core.style import DEFAULT_STYLE, available_styles, get_style

__all__ = [
    "cache_clear",
    "cache_stats",
    "demangle",
    "demangle_all",
    "demangle_strict",
    "detect",
    "languages",
    "parse",
    "styles",
]

#: Symbol tables repeat names relentlessly -- one binary can name `std::allocator<char>`
#: thousands of times -- so memoisation is worth more here than any micro-optimisation.
#: Keyed by (name, language, style) because all three change the answer.
_CACHE = BoundedCache(max_size=16384)


def _resolve(language):
    """Pick the plugin for `language`, or None to mean "detect"."""
    if language is None:
        return None
    try:
        return get(language)
    except KeyError:
        raise ValueError(f"unknown language {language!r}; known languages are {names()}") from None


def _parse_with(plugin, mangled, builder, limits, style):
    options = style.options_for(plugin.name)
    if options is None:
        return plugin.parse(mangled, builder, limits)
    return plugin.parse(mangled, builder, limits, options)


def demangle(mangled, *, language=None, style=DEFAULT_STYLE, limits=DEFAULT_LIMITS):
    """Return the readable spelling of `mangled`, or `mangled` unchanged.

    Never raises for any input. A name this library cannot read comes back exactly as it
    went in, because a wrong expansion is worse than a mangled name: it matches neither
    the original symbol nor the real declaration, so it corrupts every downstream lookup
    that trusted it.

    Args:
        mangled: the symbol name. Any string; need not be mangled.
        language: force a scheme by name, or None to detect.
        style: output spelling policy -- `"llvm"` (default) or `"gnu"`.
        limits: resource bounds for the parse.

    Returns:
        The demangled name, or `mangled` unchanged.
    """
    if not mangled:
        return mangled
    resolved_style = get_style(style)
    key = (mangled, language, resolved_style.name)
    cached = _CACHE.get(key)
    if cached is not MISSING:
        return cached

    builder = resolved_style.spelling_builder
    plugin = _resolve(language)
    candidates = (plugin,) if plugin is not None else available()

    for candidate in candidates:
        if plugin is None and not candidate.detect(mangled):
            continue
        try:
            handle = _parse_with(candidate, mangled, builder, limits, resolved_style)
        except Exception as exc:
            reraise_if_operational(exc)
            # Try the next scheme: detection is a cheap prefix test and is allowed to
            # be wrong. Only when every candidate has failed is the name given back.
            continue
        return _CACHE.put(key, builder.spell(handle))

    return _CACHE.put(key, mangled)


def demangle_strict(mangled, *, language=None, style=DEFAULT_STYLE, limits=DEFAULT_LIMITS):
    """Return the readable spelling of `mangled`, raising when it cannot be read.

    Raises:
        NotMangledError: the name matches no known scheme.
        ParseError: the name has a known prefix but does not follow the grammar.
        LimitExceeded: a resource bound was hit.
    """
    resolved_style = get_style(style)
    builder = resolved_style.spelling_builder
    return builder.spell(_parse_handle(mangled, builder, language, resolved_style, limits))


def parse(mangled, *, language=None, style=DEFAULT_STYLE, limits=DEFAULT_LIMITS):
    """Parse `mangled` into a tree.

    Use this when the *parts* matter -- the namespace, the template arguments, the
    parameter types -- rather than the spelling. The result is a `core.ast.Node`
    supporting `.walk()`, `.find(kind)` and `.spell()`.

    Raises the same errors as `demangle_strict`.

    Note:
        The MSVC and Rust parsers do not yet emit structured trees; they return a single
        `Raw` node holding the full spelling. Itanium returns a complete tree. See
        ROADMAP.md.
    """
    return _parse_handle(mangled, AST_BUILDER, language, get_style(style), limits)


def _parse_handle(mangled, builder, language, style, limits):
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    plugin = _resolve(language)
    if plugin is not None:
        return _parse_with(plugin, mangled, builder, limits, style)

    first_error = None
    for candidate in available():
        if not candidate.detect(mangled):
            continue
        try:
            return _parse_with(candidate, mangled, builder, limits, style)
        except DemanglingError as exc:
            # Keep the first failure: it came from the highest-priority plugin that
            # claimed the name, so it is the most likely to be the useful diagnostic.
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error
    raise NotMangledError(mangled)


def detect(mangled):
    """Name the scheme `mangled` appears to use, or None.

    A prefix test only -- it reports what the name looks like, not that it will parse.
    """
    from .core.registry import detect as _detect

    plugin = _detect(mangled)
    return plugin.name if plugin is not None else None


def demangle_all(names_, *, language=None, style=DEFAULT_STYLE, limits=DEFAULT_LIMITS):
    """Demangle an iterable of names, yielding results in order.

    A generator, so a caller streaming a large symbol table never holds more than one
    result at a time beyond what it keeps itself. Shares the module cache, which is
    where the real gain is: symbol tables repeat names heavily.
    """
    for name in names_:
        yield demangle(name, language=language, style=style, limits=limits)


def languages():
    """Registered language names, including any third-party plugins."""
    return names()


def styles():
    """Registered output style names."""
    return available_styles()


def cache_clear():
    """Empty the module-level result cache."""
    _CACHE.clear()


def cache_stats():
    """Hit rate and occupancy of the result cache."""
    return _CACHE.stats
