# Architecture

`demangle` turns a mangled symbol name back into the declaration a programmer wrote.
It supports several unrelated mangling schemes, is written in pure Python with no
dependencies, and is meant to be embedded in tools that process millions of symbols.

Those three facts pull in different directions, and the architecture exists to resolve
them.

## The central problem

A demangler has two plausible shapes and each is wrong on its own.

**Shape one: parse straight to a string.** Fast, small, and what nearly every existing
demangler does. But the caller gets an opaque string. A tool that wants the namespace,
the template arguments, or the parameter types has to re-parse the output with a
regular expression -- and C++ declaration syntax is not a language you can pull apart
with regular expressions.

**Shape two: parse to an abstract syntax tree.** Structured and inspectable, but it
allocates an object per grammar node. In Python that cost is not theoretical: on a
binary with 400,000 symbols it is the difference between seconds and minutes, and most
callers only ever wanted the string.

## The resolution: parsers write to a builder

No parser in this codebase constructs its own output. Each one is written against the
`Builder` protocol in `core/builder.py` and calls methods like `builder.pointer(inner)`
or `builder.template(name, args)` as it recognises grammar productions. What those
calls *produce* is the builder's business, not the parser's.

Two builders ship:

| Builder | Produces | Use |
|---|---|---|
| `SpellingBuilder` | `Spelling` pairs, immediately concatenable | `demangle()` -- the hot path |
| `AstBuilder` | `Node` trees | `parse()` -- structured access |

The parser is written once and stays honest about the grammar; the cost model is chosen
by the caller. Adding a third backend -- emitting JSON, or a token stream for a
syntax highlighter -- means writing one class and touching no parser.

This is the single most important thing to understand about the codebase. A change that
makes a parser build strings directly, however locally convenient, breaks it.

## Layout

```
demangle/
  api.py              demangle(), parse(), detect() -- the public surface
  cli.py              the `demangle` command
  core/
    reader.py         a bounds-checked cursor; the only input primitive parsers use
    builder.py        the Builder protocol -- the contract between parser and output
    spelling.py       SpellingBuilder: C++ declarator placement, the fast path
    ast.py            AstBuilder and the Node hierarchy
    errors.py         the exception hierarchy
    cache.py          bounded memoisation
    registry.py       language discovery, including third-party plugins
  schemes/
    itanium/          the Itanium C++ ABI (GCC, Clang, and everything that follows them)
    msvc/             Microsoft's scheme, as UnDecorateSymbolName reverses it
    rust/             Rust legacy (_ZN) and v0 (_R)
```

`core` never imports from `schemes`. `schemes/*` never import from each other.
Both rules are enforced by a test, because both are the kind of thing that erodes.

## Why declarator placement is its own module

C++ does not spell a type before the name; it spells it *around* the name. The type
"pointer to function taking char, returning int" is written `int (*)(char)`, and a
variable of that type is `int (*f)(char)` -- the name lands in a hole in the middle.

So a type under construction is not a string but a pair of strings, `left` and `right`,
and rendering is `left + declarator + right`. Every constructor in `core/spelling.py`
exists to keep that hole in the correct place through pointers, arrays, references and
nested function types. It is the part of a demangler that is most often subtly wrong,
so it is isolated, independently testable, and shared by all three languages.

## Substitutions are load-bearing, not an optimisation

The Itanium scheme compresses repeated components into back-references: `S_`, `S0_`,
`S1_`. The numbering is implicit -- it depends entirely on which components the
*encoder* considered substitutable, and in what order. Add one entry the specification
does not, or miss one it does, and every later back-reference in the name resolves to
the wrong component.

The rules are not folklore; they are ABI section 5.1.10, and `schemes/itanium/
substitutions.py` implements them as a separate, directly testable component with the
specification quoted at each decision. Rust v0 has its own back-reference scheme with
different rules, kept in its own module for the same reason.

## Adding a language

A language is a module under `schemes/` exposing a `LanguagePlugin`. Nothing in `core`
knows the list of schemes at import time; `core/registry.py` finds built-ins lazily and third-party
plugins through the `demangle.languages` entry-point group, so a separate distribution
can add Swift or D support without a patch to this one.

A plugin declares:

- `name` -- the identifier used in the API and on the command line
- `detect(name)` -- a cheap, allocation-free test for "is this plausibly mine?"
- `parse(name, builder)` -- the parser, written against the builder protocol

`detect` must be cheap because `demangle()` on an unknown name calls every registered
`detect` before giving up, and that path runs on every non-mangled symbol in a binary --
which, in a typical binary, is most of them.

## Performance

Design rules, in the order they matter:

1. **The common call must not allocate an AST.** Hence the builder protocol.
2. **Detection precedes parsing.** A one-character prefix test rejects the majority of
   real symbols before any parser starts.
3. **Results are memoised.** Symbol tables repeat names heavily -- the same
   `std::allocator<char>` appears thousands of times in one binary. Caches are bounded
   so a long-running process cannot grow without limit.
4. **`__slots__` on every hot class.** Node and Spelling instances are created in the
   millions.
5. **No regular expressions in a parse loop.** The parsers are character dispatch.

`benchmarks/` measures all of this against real symbol corpora, and the numbers are
part of the release checklist rather than a thing to check when someone complains.

## Correctness

Correctness is defined against the reference implementations, not against our own
reading of the specifications:

- Itanium: `llvm-cxxfilt`
- MSVC: `llvm-undname`
- Rust: `rustc-demangle`

`tests/conformance/` holds frozen corpora with the reference output recorded next to
each name, and the pass counts are pinned as exact numbers so that a change in either
direction has to be a deliberate edit rather than something that slips through. The
harness in `tools/` regenerates those corpora and can run a live differential against
the reference binaries when they are installed.

The contract at the boundary is deliberately narrow: `demangle()` never raises and
returns its input unchanged when it cannot do better, because a wrong expansion is
worse than a mangled name -- it matches neither spelling. Callers who need to know the
difference use `parse()`, which raises.
