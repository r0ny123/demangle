"""Discovery of mangling schemes.

Built-in languages are imported lazily -- naming a language should not cost the import
of every other one -- and third-party languages are found through the
`demangle.languages` entry-point group, so a separate distribution can add a language
without a patch here.

The registry is process-global and populated once. Registration is idempotent, so
importing a plugin module twice is harmless.
"""

import threading

from .plugin import LanguagePlugin

#: Built-in schemes, as (name, import path) pairs. Imported on first use, so naming one
#: language does not cost the import of every other.
_BUILTIN_MODULES = (
    ("rust", "demangle.schemes.rust"),
    ("itanium", "demangle.schemes.itanium"),
    ("msvc", "demangle.schemes.msvc"),
    ("swift", "demangle.schemes.swift"),
    ("d", "demangle.schemes.d"),
    ("go", "demangle.schemes.go"),
    ("pascal", "demangle.schemes.pascal"),
    ("delphi", "demangle.schemes.delphi"),
    ("nim", "demangle.schemes.nim"),
    ("objc", "demangle.schemes.objc"),
)

#: The entry-point group third-party packages advertise plugins under.
ENTRY_POINT_GROUP = "demangle.languages"

_lock = threading.RLock()
_plugins = {}
_aliases = {}
_ordered = None
#: First character -> the plugins that could claim a name starting with it, built on
#: demand and thrown away whenever the registry changes.
_by_first = None
#: Set only once loading has *finished*. A separate "currently loading on this thread"
#: marker handles re-entrancy, because a plugin module calls `register()` while being
#: imported and must not recurse back into loading. Using one flag for both meant a
#: second thread arriving mid-import saw a half-populated registry and got
#: `unknown language 'msvc'` from a library that supports it.
_loaded = False
_loading_thread = None


def register(plugin):
    """Add a plugin to the registry, replacing any earlier one of the same name."""
    global _by_first, _ordered
    if not isinstance(plugin, LanguagePlugin):
        raise TypeError(f"expected a LanguagePlugin, got {type(plugin).__name__}")
    with _lock:
        _plugins[plugin.name] = plugin
        for alias in plugin.aliases:
            _aliases[alias] = plugin.name
        _ordered = None
        _by_first = None
    return plugin


def _load():
    global _loaded, _loading_thread
    if _loaded:
        return
    if _loading_thread == threading.get_ident():
        # Re-entered from a plugin module's own import. Returning lets that module
        # finish registering; the outer call completes the rest.
        return
    with _lock:
        if _loaded:
            return
        _loading_thread = threading.get_ident()
        try:
            for _name, module_path in _BUILTIN_MODULES:
                __import__(module_path)
            _load_entry_points()
            _loaded = True
        finally:
            _loading_thread = None


def _load_entry_points():
    """Discover third-party plugins.

    A broken plugin from another distribution must not take down the whole library, so
    a failing entry point is skipped with a warning rather than raised.
    """
    import warnings
    from importlib.metadata import entry_points

    try:
        found = entry_points(group=ENTRY_POINT_GROUP)
    except Exception:  # pragma: no cover - depends on installed metadata
        return
    for entry in found:
        try:
            plugin = entry.load()
            register(plugin() if callable(plugin) and not isinstance(plugin, LanguagePlugin) else plugin)
        except Exception as exc:  # pragma: no cover - defensive
            warnings.warn(f"failed to load demangle plugin {entry.name!r}: {exc}", RuntimeWarning, stacklevel=2)


def get(name):
    """Look up a plugin by name or alias. Raises KeyError if unknown."""
    _load()
    with _lock:
        resolved = _aliases.get(name, name)
        return _plugins[resolved]


def available():
    """Every registered plugin, in detection order."""
    global _ordered
    _load()
    with _lock:
        if _ordered is None:
            _ordered = tuple(sorted(_plugins.values(), key=lambda p: (p.priority, p.name)))
        return _ordered


def candidates(mangled):
    """The plugins worth offering `mangled` to, in detection order.

    Most schemes start their names with one of a handful of characters, and a caller
    labelling a symbol table offers this every symbol in it -- the great majority of
    which are not mangled at all. Screening on the first character skips the schemes
    that could not match without calling into them.

    The order is `available()`'s, filtered; a scheme that declares no first characters
    is always offered, so adding one changes nothing for a scheme that does not opt in.

    The cache is read without taking the lock, because this runs once for every symbol a
    caller offers the library and the locked path was most of what detection cost: two
    acquisitions -- one here and one inside `available` -- and two more interpreter
    frames, to reach a single dictionary lookup. It is safe to read unlocked because the
    values are finished tuples that are never edited afterwards, keys are only ever
    added, and `register` discards the whole dictionary rather than changing it. A
    reader therefore sees a complete answer or none at all, and none at all falls
    through to the locked path.

    A reader can still return a tuple built before a concurrent `register` -- it read the
    dictionary before that call replaced it -- which is a valid answer for a call that
    began first. Every call starting after `register` returns finds `_by_first` empty and
    rebuilds.
    """
    if not mangled:
        return available()
    cached = _by_first
    if cached is not None:
        found = cached.get(mangled[0])
        if found is not None:
            return found
    return _screen(mangled[0])


def _screen(first):
    """Build and record the candidate list for names starting with `first`.

    `available()` is called inside the lock, not before it. Read outside, a `register`
    landing in the window between the two would be invisible: the screen would be built
    from the older order and then cached, and the `register` that should have thrown the
    cache away has already run. The entry would never be invalidated again.
    """
    global _by_first
    _load()
    with _lock:
        ordered = available()
        cache = _by_first
        if cache is None:
            cache = _by_first = {}
        found = cache.get(first)
        if found is None:
            found = cache[first] = tuple(
                plugin for plugin in ordered if not plugin.first_characters or first in plugin.first_characters
            )
        return found


def names():
    """Registered language names, sorted."""
    _load()
    with _lock:
        return sorted(_plugins)


def aliases():
    """Every alias, mapped to the plugin name it resolves to."""
    _load()
    with _lock:
        return dict(_aliases)
