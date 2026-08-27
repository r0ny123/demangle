# Adding a mangling scheme

A scheme is a directory under `src/demangle/schemes/` exposing a `LanguagePlugin`.
Nothing in `core` knows about it until it registers, and no other scheme is affected.

You do not need to read the rest of the codebase. You need `core/builder.py`, which is
the contract, and `core/reader.py`, which is the input cursor.

## The smallest possible scheme

```python
# src/demangle/schemes/toy/__init__.py
from ...core.errors import NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.plugin import LanguagePlugin
from ...core.reader import Reader
from ...core.registry import register


def detect(name):
    """Cheap enough to run on every symbol in a binary. A prefix test, nothing more."""
    return name.startswith("$T$")


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=None):
    if not detect(mangled):
        raise NotMangledError(mangled)
    reader = Reader(mangled)
    reader.expect("$T$")
    if reader.eof:
        raise ParseError(mangled, reader.pos, "nothing after the prefix")
    return builder.name(reader.remaining)


PLUGIN = LanguagePlugin(
    name="toy",
    detect=detect,
    parse=parse,
    description="A toy scheme, for illustration",
)

register(PLUGIN)
```

Add `("toy", "demangle.schemes.toy")` to `_BUILTIN_MODULES` in `core/registry.py`, and
that is a working language.

## Shipping one separately

You do not have to contribute it here. Advertise it from your own distribution:

```toml
[project.entry-points."demangle.languages"]
toy = "my_package.toy:PLUGIN"
```

It is discovered on first use. A plugin that fails to import is skipped with a warning
rather than taking the library down with it.

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

What comes back is an opaque handle. Pass it to other builder methods; do not inspect it.
Where a grammar genuinely needs the text of something already built, `builder.spell(
handle)` is the one legal way to look.

This is what earns your scheme structured output for free: the same parser drives both
the text path and the AST path.

Declarator placement — `int (*)(char)`, `int (*) [10]` — is already handled in
`core/spelling.py`. If your scheme spells C-like declarations, you get it by calling
`pointer()`, `array()` and `function()` rather than reimplementing it.

## Types, if your scheme has them

`parse_type` is optional and most schemes leave it None. Supply it only if your grammar
has a *type* production that stands alone -- what a `typeinfo` name or an RTTI descriptor
carries -- and it becomes `demangle_type(enc, language="yours")`. It is a separate entry
point rather than a fallback inside `parse` because a type encoding carries no marker
saying which scheme it belongs to, so it can only ever be read on request.

## Priority

`priority` decides detection order, and only matters where two schemes share a prefix.
Rust's legacy mangling *is* Itanium mangling, so `rust` has priority 50 and `itanium`
200. If your scheme overlaps with an existing one, say so in the pull request.

## What a scheme must guarantee

- `detect` is total: it returns a bool for any string, including `""` and binary junk.
- `parse` raises `NotMangledError` or `ParseError` and never returns a partial result.
- `parse` terminates on any input, and respects `limits`.
- Output is deterministic.

`tests/test_robustness.py` and `tests/test_architecture.py` check these for every
registered scheme, so a new plugin is covered by them the moment it registers.

## Conformance

If a reference implementation exists, do not treat your reading of the specification as
the last word. Add a corpus under `tests/conformance/` with the reference output recorded
next to each name, and pin the pass count in `tests/test_conformance.py`. See
`tools/generate_corpus.py` for how the Itanium corpus is produced.
