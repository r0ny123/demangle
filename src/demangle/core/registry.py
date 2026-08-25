"""Discovery of mangling schemes.

Built-in languages are imported lazily -- naming a language should not cost the import
of every other one -- and third-party languages are found through the
`demangle.languages` entry-point group, so a separate distribution can add Swift, D or
Delphi support without a patch here.

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
    ("go", "demangle.schemes.go"),
)

#: The entry-point group third-party packages advertise plugins under.
ENTRY_POINT_GROUP = "demangle.languages"

_lock = threading.RLock()
_plugins = {}
_aliases = {}
_ordered = None
#: Set only once loading has *finished*. A separate "currently loading on this thread"
#: marker handles re-entrancy, because a plugin module calls `register()` while being
#: imported and must not recurse back into loading. Using one flag for both meant a
#: second thread arriving mid-import saw a half-populated registry and got
#: `unknown language 'msvc'` from a library that supports it.
_loaded = False
_loading_thread = None


def register(plugin):
    """Add a plugin to the registry, replacing any earlier one of the same name."""
    global _ordered
    if not isinstance(plugin, LanguagePlugin):
        raise TypeError(f"expected a LanguagePlugin, got {type(plugin).__name__}")
    with _lock:
        _plugins[plugin.name] = plugin
        for alias in plugin.aliases:
            _aliases[alias] = plugin.name
        _ordered = None
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
