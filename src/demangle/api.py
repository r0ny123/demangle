"""The public API.

Deliberately small. A handful of functions cover what callers actually do, and every
one of them is stable ground that later versions can build on without breaking:

    demangle(name)          the readable spelling, or the name unchanged   -- never raises
    demangle_strict(name)   the readable spelling                          -- raises
    parse(name)             a tree to inspect                              -- raises
    detect(name)            which scheme, if any
    demangle_all(names)     the batch form, cached

    demangle_type(enc, language=...)  a bare type encoding, spelled       -- raises
    parse_type(enc, language=...)     a bare type encoding, as a tree     -- raises

Each of those has a `...b` form taking and returning bytes, because a symbol table holds
bytes rather than text.

The split between `demangle` and `demangle_strict` is the important one. A tool
labelling every symbol in a binary meets far more non-mangled names than mangled ones,
and treating that as an error would mean an exception per symbol; it wants the name
back. A tool demangling one name a user typed wants to be told what was wrong with it.
Serving both from one function -- with a sentinel, or a flag -- makes the common case
carry the uncommon one's error handling, so they are two functions.
"""

from collections.abc import Iterable, Iterator
from typing import Any, NoReturn

from .core import registry as _registry
from .core import style as _style_module
from .core.ast import Node, builder_for
from .core.cache import MISSING, BoundedCache
from .core.decorations import split_decorations
from .core.errors import (
    DemanglingError,
    LimitExceeded,
    NotMangledError,
    ParseError,
    reraise_if_operational,
)
from .core.limits import DEFAULT_LIMITS, Limits
from .core.registry import candidates, get, names
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
    "node_kinds",
    "parse",
    "parse_type",
    "parseb",
    "parseb_type",
    "style",
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


def _refuse_non_string(mangled):
    """Report a non-`str` argument as the caller's mistake it is.

    Bytes used to be handed straight back, unchanged and unremarked, so a tool reading
    an ELF string table -- where names *are* bytes -- saw every symbol come back exactly
    as it went in and concluded the library did not work. The strict entry points were
    worse: they reached the registry's first-character screen and raised
    `TypeError: 'in <string>' requires string as left operand, not int`, which names
    neither the problem nor the fix, and which the documented "raises only
    `DemanglingError`" contract said could not happen.
    """
    if isinstance(mangled, _BYTES_LIKE):
        raise TypeError(f"expected str, got {type(mangled).__name__}; symbol tables hold bytes, so use demangleb()")
    raise TypeError(f"expected str, got {type(mangled).__name__}")


def _resolve(language):
    """Pick the plugin for `language`, or None to mean "detect"."""
    if language is None:
        return None
    try:
        return get(language)
    except (KeyError, TypeError):
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


def _refuse_unhashable(language, limits) -> NoReturn:
    """Name the argument that could not go into the cache key, as a `ValueError`.

    Only reached once a lookup has raised `TypeError`, so one of the two is unhashable;
    the name is a `str` and a style's name is one too.
    """
    try:
        hash(language)
    except TypeError:
        raise ValueError(f"unknown language {language!r}; known languages are {names()}") from None
    try:
        hash(limits)
    except TypeError:
        raise ValueError(f"unhashable limits {limits!r}; pass a Limits instance") from None
    raise TypeError("arguments to demangle() must be hashable")


def demangle(
    mangled: str,
    *,
    language: str | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> str:
    """Return the readable spelling of `mangled`, or `mangled` unchanged.

    Never raises for any *string*: a name this library cannot read comes back exactly as
    it went in, because a wrong expansion is worse than a mangled name -- it matches
    neither the original symbol nor the real declaration, so it corrupts every
    downstream lookup that trusted it. That promise is about the name, not about the
    argument's type: passing something that is not a `str` is a mistake in the calling
    code and is reported as one.

    Args:
        mangled: the symbol name. Any string; need not be mangled.
        language: force a scheme by name, or None to detect.
        style: output spelling policy -- `"llvm"` (default) or `"gnu"`.
        limits: resource bounds for the parse.

    Returns:
        The demangled name, or `mangled` unchanged.

    Raises:
        TypeError: `mangled` is not a `str`. Use `demangleb()` for bytes.
    """
    if not isinstance(mangled, str):
        _refuse_non_string(mangled)
    if not mangled:
        return mangled
    resolved_style = get_style(style)
    # A caller may hand in a `Style` object rather than a name, and two different objects
    # can carry the same name -- so keying on the name alone served one of them the
    # other's spelling. A style holds a builder and a mapping of per-language options,
    # neither of which hashes by value, so it cannot go into the key itself; a call that
    # passes one is simply not cached. That is the rare path. The common one is a name,
    # and it stays a four-element tuple. The test is for the name -- `None` or a `str` --
    # rather than for a `Style`, so a subclass of one carrying a different builder under
    # the same name takes the rare path too, instead of being served whatever was cached
    # under that name first. `__class__ is str` rather than `isinstance`, and the
    # registry's flag rather than its `_load()`, because this is the hottest line in the
    # package: the two calls together cost the warm path a sixth of its time.
    #
    # Warmed before the cache is touched: the first `candidates()`/`get()` loads the
    # registry, and loading registers plugins, which clears the cache -- wiping the miss
    # just recorded if the `get` runs first.
    if not _registry._loaded:
        _registry._load()
    if style is not None and style.__class__ is not str:
        key = None
    else:
        key = (mangled, language, resolved_style.name, limits)
        # The lookup hashes the key once. An argument that cannot be hashed -- a list
        # for `limits`, say -- surfaces there as a `TypeError`, and is reported as the
        # bad argument it is. Reported from the failure rather than checked for in
        # advance, because `Limits` is a frozen dataclass whose hash is computed from
        # its fields every time: checking it first would hash it twice on every warm
        # call, which is the call the cache exists to make cheap.
        try:
            cached = _CACHE.get(key)
        except TypeError:
            _refuse_unhashable(language, limits)
        if cached is not MISSING:
            return cached

    builder = resolved_style.spelling_builder
    plugin = None if language is None else _resolve(language)
    if plugin is not None:
        tried, base = (plugin,), None
    else:
        tried, base = candidates(mangled), _undecorated(mangled)

    for candidate in tried:
        try:
            if plugin is None and not _claims(candidate, mangled, base):
                continue
            handle = _parse_with(candidate, mangled, builder, limits, resolved_style)
        except LimitExceeded:
            # A limit is not a "this name is not mine". The scheme claimed it and then
            # ran out of the budget the caller set, which says the name is expensive --
            # not that some other scheme should be handed the same text. Offering it on
            # is how `_ZN11Expressions2f2ILi1EEEvPApsT__i`, an Itanium name that spends
            # more substitutions than a tightened budget allows, came back as
            # `_ZN11Expressions2f2ILi1EEEvPApsT(int)`: the pre-Itanium scheme reading the
            # mangling itself as an identifier and the trailing `i` as a parameter. A
            # declaration that names nothing is the one answer this library treats as
            # worse than no answer, and a caller who *lowers* a limit is defending
            # against hostile input, which is the last place to start guessing.
            break
        except Exception as exc:
            reraise_if_operational(exc)
            # Try the next scheme. Detection is a cheap prefix test and is allowed to be
            # wrong, and a third-party plugin is allowed to be buggy -- the registry
            # already takes care not to let a broken plugin bring the library down, and
            # keeping the `detect` call inside this `try` is what stops it doing so here.
            continue
        # Recorded here rather than through a helper: `demangle` is the hot entry
        # point, and this is a conditional and a call either way.
        result = builder.spell(handle)
        return result if key is None else _CACHE.put(key, result)

    return mangled if key is None else _CACHE.put(key, mangled)


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
    resolved = get_style(style)
    return _parse_handle(mangled, builder_for(resolved), language, resolved, limits)


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
        style: output spelling policy -- `"llvm"` (default) or `"gnu"`.
        limits: resource bounds for the parse.

    Raises:
        ValueError: `language` is unknown, or names a scheme with no type grammar.
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
        # Same contract as `_parse_handle`: these entry points raise `DemanglingError`
        # and nothing else, so a defect in a plugin is wrapped rather than let out as an
        # `AttributeError` no caller can reasonably catch. The original is chained.
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


def _parse_handle(mangled, builder, language, style, limits):
    if not isinstance(mangled, str):
        _refuse_non_string(mangled)
    if not mangled:
        raise NotMangledError(mangled, "empty name")
    plugin = _resolve(language)
    if plugin is not None:
        try:
            return _parse_with(plugin, mangled, builder, limits, style)
        except RecursionError as exc:
            raise _depth_exceeded(mangled, limits) from exc

    first_error = None
    base = _undecorated(mangled)
    for candidate in candidates(mangled):
        try:
            if not _claims(candidate, mangled, base):
                continue
            return _parse_with(candidate, mangled, builder, limits, style)
        except RecursionError as exc:
            if first_error is None:
                first_error = _depth_exceeded(mangled, limits)
                first_error.__cause__ = exc
        except LimitExceeded as exc:
            # Raised, not remembered: see the note in `demangle`. The next candidate
            # would be reading a name this one has already claimed, and the answer it
            # gives is a reading of the mangling rather than of the name.
            raise exc
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


def _depth_exceeded(mangled, limits):
    """A `RecursionError` from a parser, reported as the bound it is.

    Two bounds govern how deep a name may nest, and `max_depth` is only one of them. The
    other is the interpreter's own stack, and it is the *lower* of the two in practice:
    a production costs several Python frames, so at the default recursion limit of 1000
    an Itanium name gives out around 141 levels of nested template, 164 of `decltype`,
    197 of function type and 493 of pointer -- all of them under the default
    `max_depth` of 256, let alone `RELAXED_LIMITS`' 2048.

    Which of the two binds first therefore depends on the shape of the name and on how
    deep the caller's own stack already was. Both are the same fact -- this name nests
    further than this process will follow -- so both are reported the same way. Before
    this, one arrived as `LimitExceeded` and the other as
    `ParseError: itanium parser failed: RecursionError(...)`, which reads as a defect in
    the parser rather than a bound doing its job, and leaks an implementation detail
    into a message a caller was meant to be able to act on.

    A caller who needs the deeper limits to be reachable can raise
    `sys.setrecursionlimit()`; on the versions this package supports, a Python-to-Python
    call does not consume the C stack, so that is safer than it once was.
    """
    return LimitExceeded(mangled, "recursion depth", limits.max_depth)


def _undecorated(mangled):
    """The name with any ELF version suffix taken off, or None if it carries none.

    Asked once per name rather than once per candidate. The split depends on nothing but
    the name, and a symbol table is mostly names that carry no decoration at all -- so
    the answer for the first plugin that asks is the answer for all of them, and it is
    almost always "there is nothing to take off".
    """
    base, decoration = split_decorations(mangled)
    return base if decoration else None


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


def detect(mangled: str) -> str | None:
    """Name the scheme `mangled` appears to use, or None.

    A prefix test only -- it reports what the name looks like, not that it will parse.
    Never raises: like `demangle()`, it is called on every symbol in a table.
    """
    if not isinstance(mangled, str):
        # `detect` is offered every symbol in a table and answers None for anything it
        # does not recognise, so a wrong type is answered the same way rather than
        # raised: a caller looping over a table wants a verdict, not an exception.
        return None
    if not mangled:
        return None
    base = _undecorated(mangled)
    for plugin in candidates(mangled):
        if _claims(plugin, mangled, base):
            return plugin.name
    return None


#: How a symbol table's bytes become a `str` and back again.
#:
#: A mangled name is read out of an object file, where it is a run of bytes ending at a
#: NUL and nothing else -- not text in any declared encoding. Almost all of them are
#: ASCII, but not all: a raw identifier can carry anything the assembler accepted, and a
#: truncated symbol table can cut a name mid-character.
#:
#: `surrogateescape` is what makes the round trip total. Every byte that is not valid
#: UTF-8 is parked in a lone surrogate, and encoding back with the same handler restores
#: exactly the byte that went in. So a name this package cannot read comes back out of
#: `demangleb` byte for byte, which is the same promise `demangle` makes for a `str`.
_BYTES_ENCODING = "utf-8"
_BYTES_ERRORS = "surrogateescape"

#: The types `demangleb` accepts. `memoryview` is included because that is what a caller
#: slicing a mapped object file has in hand, and copying it to ask a question would be a
#: strange thing to make them do.
_BYTES_LIKE = (bytes, bytearray, memoryview)


def _decode(mangled):
    """Bytes in, `str` out, or a `TypeError` naming what was actually passed."""
    if isinstance(mangled, _BYTES_LIKE):
        return bytes(mangled).decode(_BYTES_ENCODING, _BYTES_ERRORS)
    raise TypeError(f"expected bytes, got {type(mangled).__name__}")


def demangleb(
    mangled: bytes,
    *,
    language: str | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> bytes:
    """`demangle()` over bytes, returning bytes.

    The form to use when the names come from a symbol table, which is where they usually
    do: an ELF or Mach-O string table holds bytes, and a tool that has read one should
    not have to guess an encoding to ask what a name says.

    Like `demangle()`, never raises for a name it cannot read -- it hands the bytes back
    exactly as they arrived, including any that are not valid UTF-8. Raises `TypeError`
    if given something that is not bytes-like, which is a mistake in the calling code
    rather than a property of the symbol.

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
    language: str | None = None,
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
    language: str | None = None,
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


def detectb(mangled: bytes) -> str | None:
    """`detect()` over bytes. The scheme's name is a `str`; it is this package's own."""
    if not isinstance(mangled, _BYTES_LIKE):
        return None
    return detect(bytes(mangled).decode(_BYTES_ENCODING, _BYTES_ERRORS))


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
