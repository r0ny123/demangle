"""The public API.

Deliberately small. A handful of functions cover what callers actually do, and every
one of them is stable ground that later versions can build on without breaking:

    demangle(name)          the readable spelling, or the name unchanged   -- never raises
    demangle_strict(name)   the readable spelling                          -- raises
    parse(name)             a tree to inspect                              -- raises
    detect(name)            which scheme, if any
    demangle_all(names)     the batch form, cached
    preload(*languages)     import schemes now, not on first use

    demangle_type(enc, language=...)  a bare type encoding, spelled       -- raises
    parse_type(enc, language=...)     a bare type encoding, as a tree     -- raises

"Raises" means over an unreadable name. Each also raises `TypeError` or `ValueError` for
an argument that is wrong rather than unreadable -- an unknown `language`, say; `detect`
only the `ValueError`, since it answers None to any name, a `str` or not.

`language` is one scheme's name, which forces that scheme, or None, which detects among
all of them -- or, everywhere but the two type readers, a sequence of names, which
detects among those alone.

Each single-name entry point has a `...b` form taking and returning bytes, because a
symbol table holds bytes rather than text.

The split between `demangle` and `demangle_strict` is the important one. A tool
labelling every symbol in a binary meets far more non-mangled names than mangled ones,
and treating that as an error would mean an exception per symbol; it wants the name
back. A tool demangling one name a user typed wants to be told what was wrong with it.
Serving both from one function -- with a sentinel, or a flag -- makes the common case
carry the uncommon one's error handling, so they are two functions.
"""

from collections.abc import Iterable, Iterator, Sequence
from typing import Any, NoReturn, TypeGuard

from .core import registry as _registry
from .core import style as _style_module
from .core.ast import Node, builder_for
from .core.cache import MISSING, BoundedCache
from .core.decorations import VERSION_SEPARATOR, split_decorations
from .core.errors import (
    DemanglingError,
    LimitExceeded,
    NotMangledError,
    ParseError,
    reraise_if_operational,
)
from .core.limits import DEFAULT_LIMITS, Limits
from .core.registry import candidates, canonical, get, load_plugins, names
from .core.style import DEFAULT_STYLE, Style, available_styles, get_style

__all__ = [
    "cache_clear",
    "cache_stats",
    "demangle",
    "demangle_all",
    "demangle_strict",
    "demangle_type",
    "demangleb",
    "demangleb_strict",
    "demangleb_type",
    "detect",
    "detectb",
    "languages",
    "load_plugins",
    "node_kinds",
    "parse",
    "parse_type",
    "parseb",
    "parseb_type",
    "preload",
    "style",
    "styles",
]

#: Keyed by everything that changes the answer, `limits` included: otherwise one caller's
#: tight limits would poison the entry for every other caller of that name.
#:
#: Sized so one generation holds a large library's whole symbol table (libLLVM exports 56k
#: names), which a second pass then finds in full. Measured at about 440 bytes per C++
#: entry, so full it holds under 60 MB.
#:
#: Also weighed in bytes, name plus result, because `Limits` lets 64K characters through in
#: each direction and a count alone would not bound hostile input: 48 MB in a generation
#: holds libLLVM's table several times over and caps the whole near 100 MB. A non-ASCII
#: `str` is counted at four bytes a character, its most; `isascii` answers without a scan.
_CACHE = BoundedCache(
    max_size=131072,
    max_weight=96 << 20,
    weigh=lambda key, value: (len(key[0]) + len(value)) << (0 if key[0].isascii() and value.isascii() else 2),
)


#: Emptied whenever a style or language is registered. By notification rather than a
#: generation counter in the key, which would slow the hottest path; a miss reads the
#: cache's `epoch` before it parses, so an answer from before the change is not stored.
_style_module.notify_on_change(_CACHE.clear)
_registry.notify_on_change(_CACHE.clear)

#: The default limits' slot in a cache key. Private, so nothing a caller passes can
#: share it.
_DEFAULT_LIMITS_KEY = object()


def _refuse_non_string(mangled):
    """Report a non-`str` argument as the caller's mistake it is; bytes are pointed
    at `demangleb()`, since a symbol table holds bytes."""
    if isinstance(mangled, _BYTES_LIKE):
        raise TypeError(f"expected str, got {type(mangled).__name__}; symbol tables hold bytes, so use demangleb()")
    raise TypeError(f"expected str, got {type(mangled).__name__}")


def _refuse_language(language) -> NoReturn:
    raise ValueError(f"unknown language {language!r}; known languages are {names()}") from None


def _resolve(language):
    """Pick the plugin for `language`, or None to mean "detect"."""
    if language is None:
        return None
    try:
        return get(language)
    except (KeyError, TypeError):
        _refuse_language(language)


_BYTES_LIKE = (bytes, bytearray, memoryview)


def _is_allow_list(language: object) -> TypeGuard[Sequence[str]]:
    """A sequence of names, as opposed to one name or something that is neither."""
    return isinstance(language, Sequence) and not isinstance(language, (str, *_BYTES_LIKE))


def _allowed(language):
    """The schemes a sequence of names allows, by the name each is registered under.

    By name rather than by plugin, so allowing a scheme does not import it: `candidates`
    hands over stand-ins and imported plugins alike, and both answer to the name.
    """
    if not _is_allow_list(language):
        _refuse_language(language)
    if not language:
        raise ValueError("language names no scheme; pass None to detect among all of them")
    allowed = set()
    for name in language:
        try:
            allowed.add(canonical(name))
        except KeyError:
            _refuse_language(name)
    return allowed


def _among(mangled, allowed):
    """`candidates(mangled)`, keeping the schemes in `allowed`, in the same order."""
    return tuple(plugin for plugin in candidates(mangled) if plugin.name in allowed)


def _check_language(language):
    """`language` refused if it names nothing, and otherwise as a cache key holds it.

    For the entry points that validate before they loop: a list names what a tuple does,
    and only a tuple can be hashed.
    """
    if language is None or isinstance(language, str):
        _resolve(language)
        return language
    _allowed(language)
    return tuple(language)


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


def _refuse_limits(limits) -> NoReturn:
    raise ValueError(f"limits must be a Limits instance, got {limits!r}")


def _refuse_unhashable_limits(limits) -> NoReturn:
    raise ValueError(f"limits must be hashable, got an unhashable {type(limits).__name__}") from None


def _check_limits(limits):
    """Refuse a `limits` that is not a hashable `Limits`, before it can reach a parser
    or a key.

    The default is tested by identity first, so the common call pays for nothing else.
    """
    if limits is not DEFAULT_LIMITS:
        if not isinstance(limits, Limits):
            _refuse_limits(limits)
        try:
            hash(limits)
        except TypeError:
            _refuse_unhashable_limits(limits)


def _refuse_unhashable(language, limits) -> NoReturn:
    """Name the argument that could not go into the cache key, as a `ValueError`.

    Only reached once a lookup has raised `TypeError`, so one of the two is unhashable;
    the name is a `str` and a style's name is one too.
    """
    try:
        hash(language)
    except TypeError:
        _refuse_language(language)
    if not isinstance(limits, Limits):
        _refuse_limits(limits)
    try:
        hash(limits)
    except TypeError:
        _refuse_unhashable_limits(limits)
    raise TypeError("arguments to demangle() must be hashable")


def demangle(
    mangled: str,
    *,
    language: str | Sequence[str] | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> str:
    """Return the readable spelling of `mangled`, or `mangled` unchanged.

    Never raises over the *name*: whatever string it is given, a name this library
    cannot read comes back exactly as it went in, because a wrong expansion is worse
    than a mangled name -- it matches neither the original symbol nor the real
    declaration, so it corrupts every downstream lookup that trusted it. That promise is
    about the name, not about the other arguments: a `mangled` that is not a `str`, or a
    `language`, `style` or `limits` that names nothing, is a mistake in the calling code
    and is reported as one.

    A sequence of names is an allow-list, for a caller that knows which schemes a table
    can hold -- a Mach-O image's C++ and Swift, say -- and wants nothing else rewriting
    its Objective-C metadata or its paths:

        >>> import demangle
        >>> demangle.demangle("_OBJC_CLASS_$_NSData", language=("itanium", "swift"))
        '_OBJC_CLASS_$_NSData'
        >>> demangle.demangle("$s10Foundation4DataV5countSivg", language=["itanium", "swift"])
        'Foundation.Data.count.getter : Swift.Int'

    It detects, among those schemes, in the usual order. So `("gnuv2",)` is not
    `"gnuv2"`, which forces the scheme on a name its detection would decline:
    `PyInit__lldb` stays as it is under the first and is read as pre-Itanium C++ under
    the second. One name or several, a sequence is a filter on detection, so an
    allow-list built at run time means the same whatever its length.

    Args:
        mangled: the symbol name. Any string; need not be mangled.
        language: force a scheme by name, detect among a sequence of names, or None to
            detect among all of them.
        style: output spelling policy -- a style name (`"llvm"`, the default, or
            `"gnu"`), a `Style` object such as `style()` returns, or None for the
            default.
        limits: resource bounds for the parse.

    Returns:
        The demangled name, or `mangled` unchanged.

    Raises:
        TypeError: `mangled` is not a `str`. Use `demangleb()` for bytes.
        ValueError: `language` or `style` is not a registered name, `language` is an
            empty sequence or names one that is not, or `limits` is not a `Limits`.
    """
    if not isinstance(mangled, str):
        _refuse_non_string(mangled)
    if not mangled:
        _check_language(language)
        get_style(style)
        _check_limits(limits)
        return mangled
    # Loaded before the cache is touched: loading registers plugins, which clears it.
    # The registry flag rather than `_load()`, and `__class__ is str` rather than
    # `isinstance`, because this is the hottest path in the package.
    if not _registry._loaded:
        _registry._load()
    # A `Style` object (or a `str` subclass) is not cached: two objects can share a name,
    # and a style does not hash by value. A name is looked up before it is resolved, so a
    # hit costs no style lookup; one that is not registered misses, and is refused below.
    if style.__class__ is str or style is None:
        # The default limits keyed by a sentinel: a `Limits` hashes through a Python-level
        # `__hash__`, twice on every miss, and nearly every call passes the default.
        if limits is DEFAULT_LIMITS:
            limits_key = _DEFAULT_LIMITS_KEY
        elif isinstance(limits, Limits):
            limits_key = limits
        else:
            _refuse_limits(limits)
        key = (mangled, language, DEFAULT_STYLE if style is None else style, limits_key)
    else:
        key = None
        # The same checks, in the same order, as a cached call, so caching never decides
        # what is accepted or which argument the refusal names.
        if limits is not DEFAULT_LIMITS and not isinstance(limits, Limits):
            _refuse_limits(limits)
        if _is_allow_list(language):
            language = tuple(language)
        try:
            hash((language, limits))
        except TypeError:
            _refuse_unhashable(language, limits)
    if key is not None:
        # An unhashable argument surfaces here as a `TypeError`; not checked in advance,
        # which would hash `limits` twice on every warm call.
        try:
            cached = _CACHE.get(key)
        except TypeError:
            if _is_allow_list(language) and type(language) is not tuple:
                # The allow-list a tuple would be, keyed as one; a tuple that cannot be
                # keyed holds something that is not a name.
                return demangle(mangled, language=tuple(language), style=style, limits=limits)
            _refuse_unhashable(language, limits)
        if cached is not MISSING:
            return cached
        epoch = _CACHE.epoch

    # `get_style`'s lookup, inline where the key already holds a registered name.
    styles = _style_module._STYLES
    resolved_style = None if key is None or styles is None else styles.get(key[2])
    if resolved_style is None:
        resolved_style = get_style(style)
    builder = resolved_style.spelling_builder
    if language is None:
        tried = candidates(mangled)
        if not tried:
            return mangled
        base = _undecorated(mangled)
        detecting = True
    elif isinstance(language, str):
        tried = (_resolve(language),)
        base = None
        detecting = False
    else:
        tried = _among(mangled, _allowed(language))
        if not tried:
            return mangled
        base = _undecorated(mangled)
        detecting = True

    for candidate in tried:
        try:
            # `_claims` inlined for the hot path; a `detect` that throws is caught by this
            # loop's own handler with the same effect.
            if (
                detecting
                and not candidate.detect(mangled)
                and (base is None or not candidate.symbol_table_decorations or not candidate.detect(base))
            ):
                continue
            handle = _parse_with(candidate, mangled, builder, limits, resolved_style)
        except LimitExceeded:
            # A limit is not "this name is not mine": offering it to the next scheme would
            # read `_ZN11Expressions2f2ILi1EEEvPApsT__i` as a pre-Itanium name.
            break
        except Exception as exc:
            reraise_if_operational(exc)
            # Detection may be wrong and a plugin may be buggy; either way try the next.
            continue
        result = builder.spell(handle)
        return result if key is None else _CACHE.put(key, result, epoch)

    return mangled if key is None else _CACHE.put(key, mangled, epoch)


def demangle_strict(
    mangled: str,
    *,
    language: str | Sequence[str] | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> str:
    """Return the readable spelling of `mangled`, raising when it cannot be read.

    Takes the same arguments as `demangle()`. Every failure to read the name is a
    `DemanglingError`; a plugin that fails some other way is wrapped in a `ParseError`,
    with the original chained.

    Raises:
        NotMangledError: the name matches no known scheme, or none that `language`
            allows.
        ParseError: the name has a known prefix but does not follow the grammar.
        LimitExceeded: a resource bound was hit.
        TypeError: `mangled` is not a `str`. Use `demangleb_strict()` for bytes.
        ValueError: `language` or `style` is not a registered name, `language` is an
            empty sequence or names one that is not, or `limits` is not a `Limits`.
    """
    resolved_style = get_style(style)
    builder = resolved_style.spelling_builder
    return builder.spell(_read(mangled, builder, language, resolved_style, limits)[1])


def parse(
    mangled: str,
    *,
    language: str | Sequence[str] | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> Node:
    """Parse `mangled` into a tree.

    Use this when the *parts* matter -- the namespace, the template arguments, the
    parameter types -- rather than the spelling. The result is a `core.ast.Node`
    supporting `.walk()`, `.find(kind)` and `.spell()`.

    Raises the same errors as `demangle_strict`. Which kinds of node a tree can hold
    differs from scheme to scheme; see `node_kinds()`.
    """
    resolved = get_style(style)
    return _read(mangled, builder_for(resolved), language, resolved, limits)[1]


def demangle_type(
    mangled: str,
    *,
    language: str,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> str:
    """Return the readable spelling of a bare *type* encoding.

    `Pi` is `int*`, `PEAX` is `void *`, `SaySiG` is `[Swift.Int]`. This is what a
    `typeinfo` name, an RTTI type descriptor and a Swift metadata typeref carry: a type
    on its own, with none of the surrounding symbol a name has.

    `language` is required, and cannot be made optional. A whole symbol says which
    scheme it belongs to -- `_Z`, `?`, `$s` -- but a type encoding says nothing at all:
    `i` is a valid Itanium type, a valid Swift type, and a valid C identifier, so there
    is no evidence to detect on. Guessing would mean reading plain C symbols as types,
    which is why every reference puts this behind a flag of its own -- `c++filt -t`,
    libiberty's `DMGL_TYPES`, `UnDecorateSymbolName`'s `UNDNAME_TYPE_ONLY`.

    Raises rather than passing the name through, unlike `demangle()`. A caller who named
    the scheme is asking a question about one encoding they already believe is one, not
    labelling a table of symbols, and wants to be told when it is not.

    Args:
        mangled: the type encoding.
        language: which scheme to read it as -- required. See `languages()`.
        style: output spelling policy -- a style name (`"llvm"`, the default, or
            `"gnu"`), a `Style` object such as `style()` returns, or None for the
            default.
        limits: resource bounds for the parse.

    Raises:
        ValueError: `language` is unknown, or names a scheme with no type grammar,
            `style` is not a registered name, or `limits` is not a `Limits`.
        TypeError: `mangled` is not a `str`. Use `demangleb_type()` for bytes.
        NotMangledError: the encoding is empty.
        ParseError: the encoding does not follow the scheme's type grammar.
        LimitExceeded: a resource bound was hit.
    """
    resolved = get_style(style)
    builder = resolved.spelling_builder
    return builder.spell(_parse_type_handle(mangled, builder, language, resolved, limits))


def parse_type(
    mangled: str,
    *,
    language: str,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> Node:
    """Parse a bare *type* encoding into a tree.

    `demangle_type()` is to `demangle_strict()` as this is to `parse()`: same input,
    same required `language`, same errors -- a `core.ast.Node` instead of a string.
    """
    resolved = get_style(style)
    return _parse_type_handle(mangled, builder_for(resolved), language, resolved, limits)


def _parse_type_handle(mangled, builder, language, style, limits):
    if not isinstance(mangled, str):
        _refuse_non_string(mangled)
    _check_limits(limits)
    plugin = _resolve(language)
    if plugin is None:
        raise ValueError("demangle_type needs a language; a type encoding carries no marker to detect on")
    if plugin.parse_type is None:
        raise ValueError(f"{plugin.name} has no type grammar of its own; {_type_languages()} do")
    options = style.options_for(plugin.name)
    try:
        if options is None:
            return plugin.parse_type(mangled, builder, limits)
        return plugin.parse_type(mangled, builder, limits, options)
    except RecursionError as exc:
        raise _depth_exceeded(mangled, limits) from exc
    except DemanglingError:
        raise
    except Exception as exc:
        reraise_if_operational(exc)
        # These entry points raise only `DemanglingError`; a plugin defect is wrapped.
        raise ParseError(mangled, None, f"{plugin.name} type parser failed: {exc!r}") from exc


def style(base: str | Style | None = DEFAULT_STYLE, /, **languages: Any) -> Style:
    """Compose a style for one call: a named one, with some per-language changes.

    The two named styles say how to spell a name; what a caller usually wants to vary is
    how *much* of it to spell. Every peer tool composes that at the call site --
    `llvm-undname --no-calling-convention`, `UnDecorateSymbolName`'s mask -- and this is
    the same thing without a global registration:

        >>> import demangle
        >>> narrow = demangle.style("llvm", msvc={"calling_convention": False})
        >>> demangle.demangle("?f@@YAXH@Z", style=narrow)
        'void f(int)'
        >>> demangle.demangle("?f@@YAXH@Z")
        'void __cdecl f(int)'

    Each keyword is a language name; each value is that language's options object or a
    mapping of the fields to change. `languages()` lists the names, and each scheme's
    `options` module documents what it accepts.

    The result is an object rather than a registered name, and that is what keeps it to
    one call: `demangle()` does not cache a call that passes a style object, so a
    narrower spelling asked for here is never served to a caller asking for the default.

    Raises:
        ValueError: `base` is not a known style, or a language named has no options.
    """
    return get_style(base).with_options(**languages)


def node_kinds(language: str | None = None) -> tuple[str, ...]:
    """Every `kind` a `parse()` tree can hold, sorted. One scheme's, or all of them.

    The vocabulary to switch on, published rather than left to be read out of the source:

        >>> import demangle
        >>> demangle.node_kinds("d")
        ('name', 'path', 'symbol')

    What each kind *means* differs with what the language has to say. A C++ tree carries
    declarator shape -- `pointer`, `array`, `function` -- because a C++ type wraps the
    name it declares. Rust has no declarator syntax, so its trees carry path structure
    instead: `symbol`, `path`, `impl`, `namespace`. `name`, `template` and `literal` mean
    the same thing wherever they appear.

    Raises:
        ValueError: `language` is not a known scheme.
    """
    if language is not None:
        return tuple(sorted(_resolve(language).node_kinds))
    from .core.registry import available

    return tuple(sorted({kind for plugin in available() for kind in plugin.node_kinds}))


def _type_languages():
    """The schemes that read a bare type, for the error that says one does not."""
    return ", ".join(sorted(name for name in names() if get(name).parse_type is not None))


def _read(mangled, builder, language, style, limits):
    """The plugin that reads `mangled`, and what it built; or the error saying why none
    does. What `demangle()` does, raising where it would hand the name back."""
    if not isinstance(mangled, str):
        _refuse_non_string(mangled)
    _check_limits(limits)
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    if language is None:
        tried = candidates(mangled)
    elif isinstance(language, str):
        plugin = _resolve(language)
        try:
            return plugin, _parse_with(plugin, mangled, builder, limits, style)
        except RecursionError as exc:
            raise _depth_exceeded(mangled, limits) from exc
        except DemanglingError:
            raise
        except Exception as exc:
            reraise_if_operational(exc)
            raise ParseError(mangled, None, f"{plugin.name} parser failed: {exc!r}") from exc
    else:
        tried = _among(mangled, _allowed(language))

    first_error = None
    base = _undecorated(mangled)
    for candidate in tried:
        try:
            if not _claims(candidate, mangled, base):
                continue
            return candidate, _parse_with(candidate, mangled, builder, limits, style)
        except RecursionError as exc:
            if first_error is None:
                first_error = _depth_exceeded(mangled, limits)
                first_error.__cause__ = exc
        except LimitExceeded as exc:
            # Raised, not remembered: see the `LimitExceeded` note in `demangle`.
            raise exc
        except DemanglingError as exc:
            # The first failure comes from the highest-priority plugin, so it is kept.
            if first_error is None:
                first_error = exc
        except Exception as exc:
            reraise_if_operational(exc)
            # A plugin defect: wrapped, since these entry points raise only
            # `DemanglingError`; the original is chained.
            if first_error is None:
                first_error = ParseError(mangled, None, f"{candidate.name} parser failed: {exc!r}")
                first_error.__cause__ = exc
    if first_error is not None:
        raise first_error
    raise NotMangledError(mangled)


def _depth_exceeded(mangled, limits):
    """A `RecursionError` from a parser, reported as the bound it is.

    Two bounds govern how deep a name may nest, and `max_depth` is only one of them. At
    the defaults it is the one that binds, first for every shape measured; see
    `Limits.max_depth` for the depths. The other is the interpreter's own stack. A
    production costs several Python frames, so at the default recursion limit of 1000
    the stack binds first only when `max_depth` has been raised or when the caller's own
    stack is already deep. Both are the same fact -- this name nests
    further than this process will follow -- so both are reported the same way. Wrapped
    as `ParseError: itanium parser failed: RecursionError(...)`, it would read as a
    defect in the parser rather than a bound doing its job, and leak an implementation
    detail into a message a caller was meant to be able to act on.

    A caller who needs the deeper limits to be reachable can raise
    `sys.setrecursionlimit()`; on the versions this package supports, a Python-to-Python
    call does not consume the C stack.
    """
    return LimitExceeded(mangled, "recursion depth", limits.max_depth)


def _undecorated(mangled):
    """The name with any ELF version suffix taken off, or None if it carries none.

    Asked once per name rather than once per candidate. The split depends on nothing but
    the name, and a symbol table is mostly names that carry no decoration at all -- so
    the answer for the first plugin that asks is the answer for all of them, and it is
    almost always "there is nothing to take off".

    `split_decorations` is what defines the split and stays the place it is written; this
    is its answer read directly off the same `find`, because building and unpacking a
    two-tuple through a second interpreter frame is a per-name cost for a question whose
    answer is `None` for nearly every symbol. The separator is imported rather than
    spelled again, so the two cannot drift apart.
    """
    version = mangled.find(VERSION_SEPARATOR)
    return mangled[:version] if version > 0 else None


def _claims(plugin, mangled, base):
    """Whether `plugin` recognises `mangled`, ignoring any symbol-table decoration.

    The raw name is tried first because this runs on every symbol a caller offers, and
    in a real binary most of them are not mangled at all. A decoration is a *suffix*, so
    a prefix test sees straight through it and `base` -- what `_undecorated` made of the
    name, or None -- is only consulted when the cheap test has already failed.

    Never raises. `detect` belongs to a plugin that may be a third party's, and the
    registry already declines to let a broken one take the library down; this is the
    other half of that, since a `detect` that throws would otherwise escape through
    `demangle()`, which is documented never to raise.
    """
    try:
        if plugin.detect(mangled):
            return True
        return base is not None and plugin.symbol_table_decorations and plugin.detect(base)
    except Exception as exc:
        reraise_if_operational(exc)
        return False


def detect(
    mangled: str,
    *,
    language: str | Sequence[str] | None = None,
    strict: bool = False,
) -> str | None:
    """Name the scheme `mangled` appears to use, or None.

    Most schemes are recognised by a prefix, so this reports what the name looks like,
    not that it will parse. The schemes whose names carry no marker -- `gnuv2`,
    `codewarrior` and `ada` -- parse the whole name to claim it, and so does a legacy
    Rust name written without its underscore (a bare `ZN...`), which is read up to
    `max_input` to decide.

    `strict=True` asks the other question: which scheme *reads* the name. The answer is
    the one `demangle()` would use, under the default style and limits -- the first in
    detection order that claims the name and then parses it -- or None where it would
    hand the name back. It costs a parse, which is why it is not the default:

        >>> import demangle
        >>> demangle.detect("_ZN3Foo")
        'itanium'
        >>> demangle.detect("_ZN3Foo", strict=True) is None
        True

    `language` narrows the question to the schemes it names, one or a sequence: the
    answer is the first of them, in the usual order, that claims the name. With
    `strict`, one name forces that scheme as it does for `demangle()`, and the answer
    is whether it reads the name.

    Never raises over the name: like `demangle()`, it is called on every symbol in a
    table. A `language` that names nothing is the calling code's mistake, and a
    `ValueError`.
    """
    if language is not None:
        allowed = _allowed((language,) if isinstance(language, str) else language)
    if not isinstance(mangled, str):
        # `detect` answers a verdict for anything in a symbol table, never an exception.
        return None
    if not mangled:
        return None
    if strict:
        resolved = get_style(DEFAULT_STYLE)
        try:
            return _read(mangled, resolved.spelling_builder, language, resolved, DEFAULT_LIMITS)[0].name
        except DemanglingError:
            return None
    tried = candidates(mangled) if language is None else _among(mangled, allowed)
    if not tried:
        return None
    base = _undecorated(mangled)
    for plugin in tried:
        # `_claims` inlined, as in `demangle`.
        try:
            if plugin.detect(mangled) or (base is not None and plugin.symbol_table_decorations and plugin.detect(base)):
                return plugin.name
        except Exception as exc:
            reraise_if_operational(exc)
    return None


#: Symbol-table bytes are not text in any declared encoding; `surrogateescape` parks each
#: invalid byte in a lone surrogate, so `demangleb` returns an unread name byte for byte.
_BYTES_ENCODING = "utf-8"
_BYTES_ERRORS = "surrogateescape"

#: `memoryview` because that is what a caller slicing a mapped object file has.


def _decode(mangled):
    """Bytes in, `str` out, or a `TypeError` naming what was actually passed."""
    if isinstance(mangled, _BYTES_LIKE):
        return bytes(mangled).decode(_BYTES_ENCODING, _BYTES_ERRORS)
    raise TypeError(f"expected bytes, got {type(mangled).__name__}")


def demangleb(
    mangled: bytes,
    *,
    language: str | Sequence[str] | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> bytes:
    """`demangle()` over bytes, returning bytes.

    The form to use when the names come from a symbol table, which is where they usually
    do: an ELF or Mach-O string table holds bytes, and a tool that has read one should
    not have to guess an encoding to ask what a name says.

    Like `demangle()`, never raises for a name it cannot read -- it hands the bytes back
    exactly as they arrived, including any that are not valid UTF-8. Raises `TypeError`
    if given something that is not bytes-like, and `ValueError` for the arguments
    `demangle()` refuses, which are mistakes in the calling code rather than properties
    of the symbol.

        >>> import demangle
        >>> demangle.demangleb(b"_ZN3foo3barEv")
        b'foo::bar()'
    """
    return demangle(_decode(mangled), language=language, style=style, limits=limits).encode(
        _BYTES_ENCODING, _BYTES_ERRORS
    )


def demangleb_strict(
    mangled: bytes,
    *,
    language: str | Sequence[str] | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> bytes:
    """`demangle_strict()` over bytes, returning bytes. Raises what it raises."""
    return demangle_strict(_decode(mangled), language=language, style=style, limits=limits).encode(
        _BYTES_ENCODING, _BYTES_ERRORS
    )


def parseb(
    mangled: bytes,
    *,
    language: str | Sequence[str] | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> Node:
    """`parse()` over bytes. The tree it returns spells `str`, as every tree does."""
    return parse(_decode(mangled), language=language, style=style, limits=limits)


def demangleb_type(
    mangled: bytes,
    *,
    language: str,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> bytes:
    """`demangle_type()` over bytes, returning bytes.

    A type encoding is read out of a binary as often as a symbol is -- an Itanium
    `typeinfo` name sits in `.rodata` and an MSVC type descriptor's name in `.rdata` --
    so it gets the same bytes form the rest of the package has. Raises what
    `demangle_type()` raises.
    """
    return demangle_type(_decode(mangled), language=language, style=style, limits=limits).encode(
        _BYTES_ENCODING, _BYTES_ERRORS
    )


def parseb_type(
    mangled: bytes,
    *,
    language: str,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> Node:
    """`parse_type()` over bytes. The tree it returns spells `str`, as every tree does."""
    return parse_type(_decode(mangled), language=language, style=style, limits=limits)


def detectb(
    mangled: bytes,
    *,
    language: str | Sequence[str] | None = None,
    strict: bool = False,
) -> str | None:
    """`detect()` over bytes. The scheme's name is a `str`; it is this package's own."""
    if not isinstance(mangled, _BYTES_LIKE):
        # Refused as `detect` refuses it, whatever the name is.
        detect("", language=language)
        return None
    return detect(bytes(mangled).decode(_BYTES_ENCODING, _BYTES_ERRORS), language=language, strict=strict)


def demangle_all(
    names_: Iterable[str],
    *,
    language: str | Sequence[str] | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> Iterator[str]:
    """Demangle an iterable of names, yielding results in order.

    Returns a generator, so a caller streaming a large symbol table never holds more
    than one result at a time beyond what it keeps itself. Shares the module cache,
    which is where the real gain is: symbol tables repeat names heavily.

    Arguments are validated before the generator is created, so a bad `language`,
    `style` or `limits` is reported at the call rather than at the first `next()`.
    """
    language = _check_language(language)
    get_style(style)
    _check_limits(limits)

    def _stream():
        for name in names_:
            yield demangle(name, language=language, style=style, limits=limits)

    return _stream()


def languages() -> list[str]:
    """Registered language names. Third-party plugins appear once `load_plugins()` has run."""
    return names()


def preload(*languages: str) -> None:
    """Import schemes now rather than when a name first reaches them.

    A built-in scheme is imported on first use, so a script reading one name pays for
    the scheme that name needs and no other -- and the first MSVC name a process reads
    pays about 7 ms for the import. A long-running service would rather pay at start-up:

        >>> import demangle
        >>> demangle.preload("itanium", "msvc")
        >>> demangle.preload()

    Names, or aliases, import those schemes; none imports every built-in. The order and
    every answer are the same either way: this moves the cost, nothing else.

    Raises:
        ValueError: a name is not a registered language. Nothing is imported then.
    """
    for language in languages:
        try:
            canonical(language)
        except KeyError:
            _refuse_language(language)
    if not languages:
        _registry.available()
    for language in languages:
        get(language)


def styles() -> list[str]:
    """Registered output style names."""
    return available_styles()


def cache_clear() -> None:
    """Empty the module-level result cache."""
    _CACHE.clear()


def cache_stats() -> dict[str, Any]:
    """Hit rate and occupancy of the result cache.

    A name no scheme is offered is looked up but never stored, so it counts as a miss on
    every call.
    """
    return _CACHE.stats
