# Adding a mangling scheme

A scheme is a directory under `src/demangle/schemes/` exposing a `LanguagePlugin`.
No other scheme is affected by it; the registry learns of it from one line.

You do not need to read the rest of the codebase. You need `core/builder.py`, which is
the contract, and `core/reader.py`, which is the input cursor.

## The smallest possible scheme

```python
# src/demangle/schemes/demo/__init__.py
from ...core.errors import LimitExceeded, NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.reader import Reader
from ...core.registry import register


def detect(name):
    """Cheap enough to run on every symbol in a binary. A prefix test, nothing more."""
    return name.startswith("$D$")


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=None):
    if not detect(mangled):
        raise NotMangledError(mangled)
    if len(mangled) > limits.max_input:
        raise LimitExceeded(mangled, "input length", limits.max_input)
    reader = Reader(mangled)
    reader.expect("$D$")
    if reader.eof:
        raise ParseError(mangled, reader.pos, "nothing after the prefix")
    return builder.name(reader.remaining)


PLUGIN = LanguagePlugin(
    name="demo",
    detect=detect,
    parse=parse,
    node_kinds=("name",),
    description="A demonstration scheme",
    priority=60,
    first_characters="$",
)

register(PLUGIN)
```

`node_kinds` is every `kind` a tree from the scheme can hold -- here, only the `name`
that `builder.name()` makes -- and it is the vocabulary `demangle.node_kinds()`
publishes to callers walking a tree. `first_characters` lists every character a name
the scheme detects can start with; the registry never offers the scheme a name that
starts with any other, so it must hold for every name `detect` accepts. Avoid `toy` as
a name: `tests/test_architecture.py` registers and removes one.

## Registering it

The test suite checks each of these for every scheme, so a scheme is finished when all
of them are done:

1. **`core/registry.py`.** Add this entry to `_BUILTIN_MODULES`: name, module,
   priority, first characters, symbol-table decorations, aliases and `DETECT_SCREEN`,
   each as the scheme's `PLUGIN` declares it.
   `("demo", "demangle.schemes.demo", 60, "$", False, (), None),`
   `TestLazyBuiltIns` in `tests/test_core.py` compares the entry with the scheme. The
   two-field form, `("demo", "demangle.schemes.demo")`, also works, but the scheme is
   then imported as soon as the registry loads, and four tests that assert a name
   imports only the schemes it reaches fail: three in `tests/test_api.py`
   (`TestStylesImportOnlyWhatIsUsed`) and one in `TestLazyBuiltIns`.
2. **`DETECT_SCREEN`, only if the scheme has no fixed first character.** Leave
   `first_characters` empty, and declare in the scheme module a pair `(markers,
   openings)` such that `detect` is False for every name containing none of the
   markers and starting with none of the openings, as `schemes/nim/__init__.py` does.
   Repeat it as the last field of the registry entry. Without it, the scheme is
   offered every name in a binary.
3. **`tests/test_core.py`.** Insert the name into `TestDetectionOrderIsPinned.EXPECTED`
   at the position its `priority` gives (lowest first, then by name). Here, `demo` sits
   between `rust` (50) and `msvc` (100).
4. **`tests/conformance/demo-real-world.txt`.** One `mangled<TAB>expected` per line, and
   at least one line, then `"demo": "demo-real-world.txt"` in `CORPUS_FOR` in
   `tests/test_limits.py`. That test lengthens and shortens `max_input` around the
   corpus's longest name, which is why `parse` above checks it.
5. **`tests/test_parity.py`.** Add `"demo": "$D$hello"` to `STYLE_SAMPLES`: a name the
   scheme reads, which every registered style must render differently from the input.
6. **`docs/reference/schemes.md`.** Append a `::: demangle.schemes.demo` section
   (`tests/test_docs.py`), following the others.
7. **`ARCHITECTURE.md`.** Add a `demo/` line to the layout (`tests/test_docs.py`).

`node_kinds` must be non-empty and hold every kind the corpus produces
(`tests/test_api_surface.py`).

## Shipping one separately

You do not have to contribute it here. Advertise it from your own distribution:

```toml
[project.entry-points."demangle.languages"]
demo = "my_package.demo:PLUGIN"
```

It is discovered when a program calls `demangle.load_plugins()`, which the `demangle`
command does and a library does not: a plugin named for a built-in replaces it, so
loading them is the caller's choice. A plugin that fails to import is skipped with a
warning rather than taking the library down with it.

## The builder

Your parser must not build strings. It calls builder methods as it recognises
productions:

```python
builder.builtin("int")
builder.pointer(inner)
builder.function(returns, parameters, suffix, name)
builder.template(base, arguments)
builder.qualified([namespace, class_, method])
```

What comes back is an opaque handle. Pass it to other builder methods; do not inspect
it. Where a grammar genuinely needs the text of something already built,
`builder.spell(handle)` is the one legal way to look.

This is what earns your scheme structured output for free: the same parser drives both
the text path and the AST path.

Declarator placement -- `int (*)(char)`, `int (*) [10]` -- is already handled in
`core/spelling.py`. If your scheme spells C-like declarations, you get it by calling
`pointer()`, `array()` and `function()` rather than reimplementing it.

## Types, if your scheme has them

`parse_type` is optional and most schemes leave it None. Supply it only if your grammar
has a *type* production that stands alone -- what a `typeinfo` name or an RTTI
descriptor carries -- and it becomes `demangle_type(enc, language="yours")`. It is a
separate entry point rather than a fallback inside `parse` because a type encoding
carries no marker saying which scheme it belongs to, so it can only ever be read on
request.

## Priority

`priority` decides detection order, and only matters where two schemes share a prefix.
Rust's legacy mangling *is* Itanium mangling, so `rust` has priority 50 and `itanium`
200. If your scheme overlaps with an existing one, say so in the pull request.

A scheme whose names have **no marker at all** goes last, and owes a number rather than
an argument. `gnuv2` -- pre-Itanium C++ -- is priority 290 for that reason, and
`codewarrior`, the other pre-Itanium mangling, 300 behind it: their names are ordinary C
identifiers with a `__` somewhere in them, so `detect` parses the whole name instead of
testing a prefix, and what makes it safe to register is a measurement. `gnuv2` is
scored over every other scheme's checked-in corpus, which `tests/test_gnuv2.py` asserts,
and over 339,117 symbols from real shared libraries, which
[CONFORMANCE.md](CONFORMANCE.md) records. If your scheme is in that position, do the
same: a claim that "false positives are unlikely" is not a test, and the corpora are
already there to run against.

## What a scheme must guarantee

- `detect` is total: it returns a bool for any string, including `""` and binary junk.
- `parse` raises `NotMangledError` or `ParseError` and never returns a partial result.
- `parse` terminates on any input, and respects `limits`.
- Output is deterministic.

Which tests cover a new scheme depends on what you registered above. `detect` totality
and a non-empty description are checked for every registered scheme
(`tests/test_architecture.py`). The termination, determinism and never-partial checks in
`tests/test_robustness.py` run over every file in `tests/conformance/`, so they reach
the scheme through its corpus, and `tests/test_parity.py` and `tests/test_limits.py`
through `STYLE_SAMPLES` and `CORPUS_FOR`.

## Conformance

If a reference implementation exists, do not treat your reading of the specification as
the last word. Add a corpus under `tests/conformance/` with the reference output
recorded next to each name, and pin its count as
[Contributing](CONTRIBUTING.md#conformance-numbers) describes. See
`tools/generate_corpus.py` for how the Itanium corpus is produced.
