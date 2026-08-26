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

from collections.abc import Iterable, Iterator
from typing import Any

from .core import registry as _registry
from .core import style as _style_module
from .core.ast import AST_BUILDER, Node
from .core.cache import MISSING, BoundedCache
from .core.decorations import split_decorations
from .core.errors import DemanglingError, NotMangledError, ParseError, reraise_if_operational
from .core.limits import DEFAULT_LIMITS, Limits
from .core.registry import candidates, get, names
from .core.style import DEFAULT_STYLE, Style, available_styles, get_style

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
#:
#: Keyed by everything that changes the answer, `limits` included. Leaving the limits
#: out is not merely a missed bound: one caller passing tight limits would poison the
#: entry for every other caller of that name in the process, and `demangle()` cannot
#: report it because it never raises. `Limits` is a frozen slots dataclass, so it
#: hashes by value and two equal limit sets share a cache entry.
_CACHE = BoundedCache(max_size=16384)


#: Emptied whenever a style or a language is registered. Replacing `llvm`, or replacing
#: a whole scheme, used to leave every name demangled beforehand still answering from
#: cache with the older spelling.
#:
#: Done by notification rather than by folding a generation counter into the key,
#: because the key is built once per `demangle()` call and that is the hottest path in
#: the package. Asking two modules "have you changed" there measured 58% slower on the
#: warm path than clearing a cache on the rare occasion one has.
_style_module.notify_on_change(_CACHE.clear)
_registry.notify_on_change(_CACHE.clear)


def _keep(key, value):
    """Record `value` under `key`, unless this was a call that must not be cached."""
    return value if key is None else _CACHE.put(key, value)


def _resolve(language):
    """Pick the plugin for `language`, or None to mean "detect"."""
    if language is None:
        return None
    try:
        return get(language)
    except KeyError:
        raise ValueError(f"unknown language {language!r}; known languages are {names()}") from None


def _parse_with(plugin, mangled, builder, limits, style):
    """Run one plugin, handling any symbol-table decoration around the name.

    Splitting here rather than in each parser keeps the grammars free of ELF and
    toolchain conventions, and means a new scheme inherits the behaviour by setting one
    flag instead of reimplementing it.
    """
    options = style.options_for(plugin.name)
    decoration = ""
    if plugin.symbol_table_decorations:
        mangled, decoration = split_decorations(mangled)

    if options is None:
        handle = plugin.parse(mangled, builder, limits)
    else:
        handle = plugin.parse(mangled, builder, limits, options)

    return builder.decorated(handle, decoration) if decoration else handle


def demangle(
    mangled: str,
    *,
    language: str | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> str:
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
    if not isinstance(mangled, str):
        # Symbol tables are read as bytes, so this is the likeliest caller mistake.
        # Returning the argument untouched keeps the "never raises" contract, and a
        # caller who wanted a demangled name gets an obviously unchanged one back.
        return mangled
    resolved_style = get_style(style)
    # A caller may hand in a `Style` object rather than a name, and two different objects
    # can carry the same name -- so keying on the name alone served one of them the
    # other's spelling. A style holds a builder and a mapping of per-language options,
    # neither of which hashes by value, so it cannot go into the key itself; a call that
    # passes one is simply not cached. That is the rare path. The common one is a name,
    # and it stays a four-element tuple.
    #
    # `__class__ is` rather than `isinstance`: this runs once per call on the hottest
    # path in the package, and a subclass of `Style` is not a thing this distinction
    # needs to be right about -- it would only be cached where it could have been left
    # uncached, which is the safe direction.
    key = None if style.__class__ is Style else (mangled, language, resolved_style.name, limits)
    if key is not None:
        cached = _CACHE.get(key)
        if cached is not MISSING:
            return cached

    builder = resolved_style.spelling_builder
    plugin = _resolve(language)
    tried = (plugin,) if plugin is not None else candidates(mangled)

    for candidate in tried:
        try:
            if plugin is None and not _claims(candidate, mangled):
                continue
            handle = _parse_with(candidate, mangled, builder, limits, resolved_style)
        except Exception as exc:
            reraise_if_operational(exc)
            # Try the next scheme. Detection is a cheap prefix test and is allowed to be
            # wrong, and a third-party plugin is allowed to be buggy -- the registry
            # already takes care not to let a broken plugin bring the library down, and
            # keeping the `detect` call inside this `try` is what stops it doing so here.
            continue
        return _keep(key, builder.spell(handle))

    return _keep(key, mangled)


def demangle_strict(
    mangled: str,
    *,
    language: str | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> str:
    """Return the readable spelling of `mangled`, raising when it cannot be read.

    Raises:
        NotMangledError: the name matches no known scheme.
        ParseError: the name has a known prefix but does not follow the grammar.
        LimitExceeded: a resource bound was hit.
    """
    resolved_style = get_style(style)
    builder = resolved_style.spelling_builder
    return builder.spell(_parse_handle(mangled, builder, language, resolved_style, limits))


def parse(
    mangled: str,
    *,
    language: str | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> Node:
    """Parse `mangled` into a tree.

    Use this when the *parts* matter -- the namespace, the template arguments, the
    parameter types -- rather than the spelling. The result is a `core.ast.Node`
    supporting `.walk()`, `.find(kind)` and `.spell()`.

    Raises the same errors as `demangle_strict`.

    Note:
        Every scheme returns a tree, but the kinds differ with what each language has to
        say. C++ trees carry declarator shape -- pointers, references, parameter lists --
        because C++ types wrap the name they declare. Rust has no declarator syntax, so
        its trees carry path structure instead: `symbol`, `path`, `impl`, `namespace`.
        `name`, `template` and `literal` mean the same thing in all three.
    """
    return _parse_handle(mangled, AST_BUILDER, language, get_style(style), limits)


def _parse_handle(mangled, builder, language, style, limits):
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    plugin = _resolve(language)
    if plugin is not None:
        return _parse_with(plugin, mangled, builder, limits, style)

    first_error = None
    for candidate in candidates(mangled):
        try:
            if not _claims(candidate, mangled):
                continue
            return _parse_with(candidate, mangled, builder, limits, style)
        except DemanglingError as exc:
            # Keep the first failure: it came from the highest-priority plugin that
            # claimed the name, so it is the most likely to be the useful diagnostic.
            if first_error is None:
                first_error = exc
        except Exception as exc:
            reraise_if_operational(exc)
            # A plugin raised something that is not a demangling failure -- a defect in
            # it, or in this package. The documented contract is that these entry points
            # raise `DemanglingError` and nothing else, so it is wrapped rather than
            # allowed to escape as an `AttributeError` a caller cannot reasonably catch.
            # The original is chained, so the bug is still diagnosable.
            if first_error is None:
                first_error = ParseError(mangled, None, f"{candidate.name} parser failed: {exc!r}")
                first_error.__cause__ = exc
    if first_error is not None:
        raise first_error
    raise NotMangledError(mangled)


def _claims(plugin, mangled):
    """Whether `plugin` recognises `mangled`, ignoring any symbol-table decoration.

    The raw name is tried first because this runs on every symbol a caller offers, and
    in a real binary most of them are not mangled at all. A decoration is a *suffix*, so
    a prefix test sees straight through it and the split is only worth paying for when
    the cheap test has already failed.

    Never raises. `detect` belongs to a plugin that may be a third party's, and the
    registry already declines to let a broken one take the library down; this is the
    other half of that, since a `detect` that throws would otherwise escape through
    `demangle()`, which is documented never to raise.
    """
    try:
        if plugin.detect(mangled):
            return True
        if not plugin.symbol_table_decorations:
            return False
        base, decoration = split_decorations(mangled)
        return bool(decoration) and plugin.detect(base)
    except Exception as exc:
        reraise_if_operational(exc)
        return False


def detect(mangled: str) -> str | None:
    """Name the scheme `mangled` appears to use, or None.

    A prefix test only -- it reports what the name looks like, not that it will parse.
    Never raises: like `demangle()`, it is called on every symbol in a table.
    """
    if not mangled or not isinstance(mangled, str):
        return None
    for plugin in candidates(mangled):
        if _claims(plugin, mangled):
            return plugin.name
    return None


def demangle_all(
    names_: Iterable[str],
    *,
    language: str | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> Iterator[str]:
    """Demangle an iterable of names, yielding results in order.

    Returns a generator, so a caller streaming a large symbol table never holds more
    than one result at a time beyond what it keeps itself. Shares the module cache,
    which is where the real gain is: symbol tables repeat names heavily.

    Arguments are validated before the generator is created, so a bad `language` or
    `style` is reported at the call rather than at the first `next()`.
    """
    _resolve(language)
    get_style(style)

    def _stream():
        for name in names_:
            yield demangle(name, language=language, style=style, limits=limits)

    return _stream()


def languages() -> list[str]:
    """Registered language names, including any third-party plugins."""
    return names()


def styles() -> list[str]:
    """Registered output style names."""
    return available_styles()


def cache_clear() -> None:
    """Empty the module-level result cache."""
    _CACHE.clear()


def cache_stats() -> dict[str, Any]:
    """Hit rate and occupancy of the result cache."""
    return _CACHE.stats
