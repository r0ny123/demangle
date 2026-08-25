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
)

#: The entry-point group third-party packages advertise plugins under.
ENTRY_POINT_GROUP = "demangle.languages"

_lock = threading.RLock()
_plugins = {}
_aliases = {}
_ordered = None
_loaded = False


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
    global _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        # Set before importing: a plugin module calls register(), which must not
        # recurse back into loading.
        _loaded = True
        for _name, module_path in _BUILTIN_MODULES:
            __import__(module_path)
        _load_entry_points()


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
    resolved = _aliases.get(name, name)
    return _plugins[resolved]


def available():
    """Every registered plugin, in detection order."""
    global _ordered
    _load()
    if _ordered is None:
        with _lock:
            _ordered = tuple(sorted(_plugins.values(), key=lambda p: (p.priority, p.name)))
    return _ordered


def names():
    """Registered language names, sorted."""
    _load()
    return sorted(_plugins)


def detect(name):
    """The first plugin claiming `name`, or None.

    Order is by plugin priority, which is how overlapping schemes are resolved: Rust's
    legacy mangling is Itanium mangling, so Rust is offered the name first.
    """
    if not name:
        return None
    for plugin in available():
        if plugin.detect(name):
            return plugin
    return None
