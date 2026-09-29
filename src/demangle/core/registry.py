"""Discovery of mangling schemes.

Built-in languages are imported lazily, and not only when named: the registry knows
each one's detection order and first characters without importing it, so a scheme's
module is imported only once a name reaches its `detect` -- a name starting `?` never
costs the Swift parser. Third-party languages are found through the
`demangle.languages` entry-point group, so a separate distribution can add a language
without a patch here.

The registry is process-global and populated once. Registration is idempotent, so
importing a plugin module twice is harmless.
"""

import sys
import threading
from importlib import import_module

from .plugin import LanguagePlugin

#: Built-in schemes, imported on first use. A two-field entry, `(name, import path)`, is
#: imported when the registry loads. The longer form also carries what detection needs --
#: priority, first characters, symbol-table decorations and aliases, as the scheme's
#: `PLUGIN` declares them -- so the scheme stays unimported until a name reaches it.
#: `tests/test_core.py` checks those fields against every `PLUGIN`.
_BUILTIN_MODULES = (
    ("rust", "demangle.schemes.rust", 50, "_Z", False, ("rs",)),
    ("itanium", "demangle.schemes.itanium", 200, "_", True, ("gnu", "gcc", "clang", "cxx", "c++")),
    ("msvc", "demangle.schemes.msvc", 100, "?.", False, ("microsoft", "ms", "vc")),
    ("swift", "demangle.schemes.swift", 45, "$_@a", False, ()),
    ("d", "demangle.schemes.d", 40, "_", False, ("dlang",)),
    ("go", "demangle.schemes.go", 10, "", False, ("golang",)),
    ("pascal", "demangle.schemes.pascal", 20, "", False, ("fpc", "freepascal")),
    ("delphi", "demangle.schemes.delphi", 35, "@", False, ("borland", "bcc", "c++builder", "embarcadero")),
    ("nim", "demangle.schemes.nim", 10, "", False, ()),
    ("objc", "demangle.schemes.objc", 30, "-+_.lL", False, ("objective-c", "objectivec")),
    ("jni", "demangle.schemes.jni", 15, "J", False, ("java",)),
    ("codewarrior", "demangle.schemes.codewarrior", 300, "", True, ("cw", "metrowerks", "mwcc")),
    ("gnuv2", "demangle.schemes.gnuv2", 290, "", True, ("gnu-v2", "cfront", "cplus-dem")),
    ("ada", "demangle.schemes.ada", 280, "", True, ("gnat",)),
)

#: The entry-point group third-party packages advertise plugins under.
ENTRY_POINT_GROUP = "demangle.languages"

_lock = threading.RLock()

#: Called when the set of plugins changes; see `style._on_change`.
_on_change = []
_plugins = {}
_aliases = {}
_ordered = None
#: First character -> the plugins that could claim a name starting with it; rebuilt on
#: demand after any change.
_by_first = None
#: Set only once loading has *finished*. Re-entrancy (a plugin module calling `register()`
#: during import) has its own marker, so another thread never sees a half-loaded registry.
_loaded = False
_loading_thread = None
#: Name -> the stand-in holding a lazy built-in's place until its module is imported.
_stand_ins = {}
#: Built-ins whose module has made its own `register(PLUGIN)` call.
_self_registered = set()


def notify_on_change(callback):
    """Call `callback` whenever a plugin is registered."""
    _on_change.append(callback)


def register(plugin):
    """Add a plugin to the registry, replacing any earlier one of the same name."""
    global _by_first, _ordered
    if not isinstance(plugin, LanguagePlugin):
        raise TypeError(f"expected a LanguagePlugin, got {type(plugin).__name__}")
    with _lock:
        stand_in = _stand_ins.get(plugin.name)
        if stand_in is not None and _registers_itself(plugin):
            # A lazy built-in arriving: the languages on offer are unchanged, so no
            # callback. It never displaces a plugin a caller put in its place.
            if _plugins.get(plugin.name) is stand_in:
                _plugins[plugin.name] = plugin
                _ordered = None
                _by_first = None
            return plugin
        # Check every alias before recording any, so a rejected plugin leaves no trace.
        for alias in plugin.aliases:
            if alias in _plugins and alias != plugin.name:
                raise ValueError(f"alias {alias!r} collides with a registered language name")
        _plugins[plugin.name] = plugin
        for alias in plugin.aliases:
            _aliases[alias] = plugin.name
        # A re-registered name wins back over any alias that had shadowed it.
        _aliases.pop(plugin.name, None)
        _ordered = None
        _by_first = None
    for callback in _on_change:
        callback()
    return plugin


def _registers_itself(plugin):
    """Whether this is a lazy built-in's module registering its own `PLUGIN`, once."""
    if plugin.name in _self_registered:
        return False
    for entry in _BUILTIN_MODULES:
        if entry[0] == plugin.name:
            module = sys.modules.get(entry[1])
            if module is not None and getattr(module, "PLUGIN", None) is plugin:
                _self_registered.add(plugin.name)
                return True
    return False


def _stand_in(name, module_path, priority, first_characters, decorations, aliases):
    """A plugin that imports its scheme on first use and forwards to it."""

    def detect(mangled):
        return _materialise(name, module_path).detect(mangled)

    def parse(mangled, builder, *rest):
        return _materialise(name, module_path).parse(mangled, builder, *rest)

    return LanguagePlugin(
        name=name,
        detect=detect,
        parse=parse,
        priority=priority,
        first_characters=first_characters,
        symbol_table_decorations=decorations,
        aliases=aliases,
    )


def _materialise(name, module_path):
    """The plugin now registered under a lazy built-in's name, importing it if need be.

    The import runs outside the lock: the module's own `register()` takes it, and a
    thread importing the module directly would otherwise deadlock against one holding
    the lock while waiting for that import.
    """
    global _by_first, _ordered
    module = import_module(module_path)
    with _lock:
        current = _plugins.get(name)
        if current is None or current is _stand_ins.get(name):
            # Its own `register()` did not replace the stand-in (it ran once, earlier).
            current = _plugins[name] = module.PLUGIN
            _self_registered.add(name)
            _ordered = None
            _by_first = None
        return current


def _load():
    global _loaded, _loading_thread
    if _loaded:
        return
    if _loading_thread == threading.get_ident():
        # Re-entered from a plugin module's own import; the outer call completes the rest.
        return
    with _lock:
        if _loaded:
            return
        _loading_thread = threading.get_ident()
        try:
            installed = False
            for entry in _BUILTIN_MODULES:
                if len(entry) == 2:
                    import_module(entry[1])
                elif entry[0] not in _plugins:
                    _install_stand_in(_stand_in(*entry))
                    installed = True
            _load_entry_points()
            _loaded = True
        finally:
            _loading_thread = None
    if installed:
        for callback in _on_change:
            callback()


def _install_stand_in(stand_in):
    global _by_first, _ordered
    _stand_ins[stand_in.name] = stand_in
    _plugins[stand_in.name] = stand_in
    for alias in stand_in.aliases:
        _aliases[alias] = stand_in.name
    _ordered = None
    _by_first = None


def _load_entry_points():
    """Discover third-party plugins.

    A broken plugin from another distribution must not take down the whole library, so
    a failing entry point is skipped with a warning rather than raised.
    """
    if not _may_advertise_plugins():
        return

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


def _may_advertise_plugins():
    """Whether any distribution `importlib.metadata` can see might declare the group.

    Importing `importlib.metadata` costs more than every built-in scheme's detection
    put together, and most environments hold no plugin at all. So the metadata
    directories it would read are searched for the group's name first, the same way it
    finds them; anything this cannot read the same way -- a zip on `sys.path`, an
    import hook that finds distributions of its own -- answers True and is left to it.
    """
    import os
    from importlib.machinery import PathFinder

    for finder in sys.meta_path:
        if finder is not PathFinder and getattr(finder, "find_distributions", None) is not None:
            return True
    try:
        for entry in sys.path:
            root = os.fspath(entry) or "."
            if not os.path.isdir(root):
                if os.path.exists(root):
                    return True
                continue
            if root.lower().endswith(".egg"):
                return True
            with os.scandir(root) as children:
                for child in children:
                    if not child.name.lower().endswith((".dist-info", ".egg-info")):
                        continue
                    try:
                        with open(os.path.join(child.path, "entry_points.txt"), "rb") as file:
                            if ENTRY_POINT_GROUP.encode() in file.read():
                                return True
                    except OSError:
                        continue
    except (OSError, TypeError, ValueError):
        return True
    return False


def get(name):
    """Look up a plugin by name or alias. Raises KeyError if unknown."""
    _load()
    try:
        with _lock:
            plugin = _plugins[name] if name in _plugins else _plugins[_aliases[name]]
    except TypeError:
        raise KeyError(name) from None
    if plugin is _stand_ins.get(plugin.name):
        return _materialise(plugin.name, _module_of(plugin.name))
    return plugin


def _module_of(name):
    return next(entry[1] for entry in _BUILTIN_MODULES if entry[0] == name)


def available():
    """Every registered plugin, in detection order."""
    _load()
    with _lock:
        waiting = [name for name, stand_in in _stand_ins.items() if _plugins.get(name) is stand_in]
    for name in waiting:
        _materialise(name, _module_of(name))
    return _order()


def _order():
    """`available()`'s order, lazy built-ins still standing in where not yet imported."""
    global _ordered
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
        _load()
        return _order()
    cached = _by_first
    if cached is not None:
        found = cached.get(mangled[0])
        if found is not None:
            return found
    return _screen(mangled[0])


def _screen(first):
    """Build and record the candidate list for names starting with `first`.

    The order is read inside the lock, not before it. Read outside, a `register`
    landing in the window between the two would be invisible: the screen would be built
    from the older order and then cached, and the `register` that should have thrown the
    cache away has already run. The entry would never be invalidated again.
    """
    global _by_first
    _load()
    with _lock:
        ordered = _order()
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
