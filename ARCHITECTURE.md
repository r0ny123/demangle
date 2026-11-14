# Architecture

`demangle` turns a mangled symbol name back into the declaration a programmer wrote. It
reads fourteen unrelated mangling schemes, is pure Python with no dependencies, and is
meant to sit inside tools that process millions of symbols. Most of the design comes
from wanting all three at once.

## The problem

A demangler can parse straight to a string. That's fast and small, and it's what
nearly every demangler does, but the caller gets an opaque string. A tool that wants
the namespace, the template arguments or the parameter types has to pull the output
apart again, and C++ declarations can't be pulled apart with a regular expression.

Or it can parse to a tree, which is structured but allocates an object per grammar
node. In Python that costs: building the tree takes about a fifth longer than building
the string, and most callers only wanted the string.

## Parsers write to a builder

So no parser here builds its own output. Each is written against the `Builder`
protocol in `core/builder.py` and calls methods like `builder.pointer(inner)` or
`builder.template(name, args)` as it recognises grammar productions. What those calls
produce is up to the builder.

Two builders ship:

| Builder | Produces | Use |
|---|---|---|
| `SpellingBuilder` | `Spelling` pairs, immediately concatenable | `demangle()` -- the hot path |
| `AstBuilder` | `Node` trees | `parse()` -- structured access |

The parser is written once and the caller picks the cost. A third backend, say JSON or
a token stream for a syntax highlighter, is one new class and no parser changes.

This is the rule that matters most. A parser that builds strings itself,
however convenient locally, breaks it. `tests/test_architecture.py` fails if a scheme
imports `core.spelling`, which is as far as a test can reach: a parser that
concatenates strings and hands the result to `builder.name()` imports nothing, and
only review catches that.

### Expressions

An expression inside a type goes through `Builder.expression(form, parts)`, so it
reaches the tree as structure rather than one opaque node. `parts` interleaves the
production's fixed text with its operands' handles and `form` names the shape --
`binary`, `conditional`, `call`, `sizeof`.

It's one method, not one per operator. Fifteen methods would be fifteen things
every future builder has to implement, and still wouldn't cover the next operator
someone mangles. The parser already owns operator spelling (it comes from `tables.py`,
which exists to be checked against the ABI), so all that's left for the builder is
structure.

Brackets are parts like any other. Whether an operand needs them is a precedence
question only the parser can answer, so a consumer walking the tree sees a `paren`
expression wrapping the operand.

Rust gets there differently. Its printer emits one linear stream of fragments and a
sink decides what to do with them: `TextSink` concatenates, `TreeSink` remembers where
each production began and ended. The tree is the same traversal with its boundaries
kept, so it renders to what `demangle()` returns by construction. That works because
Rust's spelling is strictly left to right. A C-family declarator wraps the name it
declares and can't be recovered from a linear stream.

### When a scheme needs its own spelling

`core/spelling.py` implements C-family declarator placement. A scheme whose output
looks like a C declaration uses it and gets the hard part for free. One whose output
doesn't, like MSVC with its calling convention inside the parentheses and its own
spacing, supplies its own node kinds and renderer, subclassing `core.ast.Node` so
`walk()`, `find()` and `spell()` keep working. `AstBuilder` is shared either way.

That's on purpose: one renderer for every scheme would mean MSVC-shaped branches
inside `core`.

## Layout

```
demangle/
  api.py              demangle(), parse(), detect() -- the public surface
  _signature.py       signature(): the parts of a name rather than its spelling
  filter.py           demangling the symbols out of text that is not only symbols
  cli.py              the `demangle` command
  __main__.py         `python -m demangle`, the same command
  core/
    reader.py         a bounds-checked cursor; the input primitive for a new parser
    builder.py        the Builder protocol -- the contract between parser and output
    spelling.py       SpellingBuilder: C++ declarator placement, the fast path
    ast.py            AstBuilder and the Node hierarchy
    errors.py         the exception hierarchy
    limits.py         the bounds a parser reads before it recurses or emits
    style.py          named styles, and composing one for a single call
    decorations.py    what a symbol table adds around a name, which belongs to no scheme
    plugin.py         LanguagePlugin: the contract a scheme implements
    cache.py          bounded memoisation
    registry.py       scheme discovery, including third-party plugins
  schemes/
    itanium/          the Itanium C++ ABI (GCC, Clang, and everything that follows them)
    msvc/             Microsoft's scheme, spelled as `llvm-undname` spells it
    rust/             Rust legacy (_ZN) and v0 (_R)
    swift/            Swift, both the current mangling and Swift 3's
    d/                D, as GNU binutils reverses it
    go/               Go package paths and receivers
    nim/              Nim, whose symbols are ordinary C identifiers
    objc/             Objective-C
    pascal/           Free Pascal
    delphi/           Borland/Embarcadero Delphi and C++Builder
    gnuv2/            pre-Itanium C++: g++ before 3.0, cfront/ARM, Lucid, HP aCC, EDG
    codewarrior/      Metrowerks CodeWarrior, the other pre-Itanium C++ mangling
    ada/              Ada, as GNAT encodes it
    jni/              the C function a Java `native` method is called through
```

`core` never imports from `schemes`; `schemes/*` never import from each other. Both are
enforced by a test; the cross-scheme rule's test is itself tested against a constructed
violation.

`core/style.py` imports no scheme: it names each scheme's option module by dotted path
and imports it on first use, so `core` stays free of `schemes` at import time.

## Why declarator placement is its own module

C++ spells a type around the name it declares, `int (*f)(char)`, so a type under
construction is two strings with a hole between them. Keeping that hole in the right
place through nested declarators is where demanglers most often go subtly wrong, so
`core/spelling.py` does it once, with its own tests, for every scheme whose output
looks like a C declaration. Its
[reference](https://r0ny123.github.io/demangle/reference/core/#spelling) has the
mechanics.

## Substitutions

The Itanium scheme compresses repeated components into back-references: `S_`, `S0_`,
`S1_`. The numbering is implicit. It depends on which components the encoder counted
as substitutable, and in what order. Add one entry the specification doesn't, or miss
one it does, and every later back-reference in the name points at the wrong thing.

The rules are ABI section 5.1.10, and `schemes/itanium/substitutions.py` implements
them as a separate, directly testable component with the specification quoted at each
decision. Rust v0 has its own back-reference rules, in its own module for the same
reason.

## Adding a scheme

A scheme is a directory under `schemes/` exposing a `LanguagePlugin` (`core/plugin.py`).
Nothing in `core` knows the list of schemes at import time: `core/registry.py` finds the
built-ins lazily, and third-party plugins through the `demangle.languages` entry-point
group once a caller runs `load_plugins()`.

Detection is the part that has to be cheap. It runs on every symbol in a binary, and
most of them aren't mangled, so `detect` is expected to be a prefix test, and the
registry narrows what a name is offered to before any `detect` runs. A
scheme with a fixed first character declares it (`first_characters`), and a name
starting with anything else never reaches it. A scheme whose names are ordinary C
identifiers has none, so it declares a `DETECT_SCREEN`: the markers and openings that a
name must hold for its `detect` to say yes, and a name with none of them is not offered
to it. Both can apply: Nim and Free Pascal names are identifiers, so they also declare
the letters and `_` as first characters, which keeps them off MSVC's `?` and Swift's
`$s` names even though those hold the `__` and `$` they screen on. The schemes whose
names carry no marker at all (Ada and the two pre-Itanium C++ manglings, whose `detect`
has to parse) are offered last, behind everything a prefix settles.

[Adding a scheme](https://github.com/r0ny123/demangle/blob/main/docs/adding-a-scheme.md)
is the walk-through.

## Performance

Design rules, in the order they matter:

1. **The common call must not allocate an AST.** Hence the builder protocol.
2. **Detection precedes parsing.** See [Adding a scheme](#adding-a-scheme).
3. **Results are memoised.** Symbol tables repeat names heavily: the same
   `std::allocator<char>` appears thousands of times in one binary. Caches are bounded
   so a long-running process can't grow without limit.
4. **`__slots__` on every hot class.** Node and Spelling instances are created in the
   millions.
5. **No regular expressions in the hot parse loops.** The Itanium, MSVC, Rust and Swift
   parsers advance by character dispatch; a regular expression appears there only to
   test a whole token or a suffix. The smaller schemes (JNI, Objective-C, Pascal,
   Delphi, CodeWarrior, Ada, Nim) use them over short names.
6. **The Itanium hot path also ships compiled.** `tools/hatch_build.py` builds the
   reader, the spelling builder and the Itanium parser with mypyc for CPython's
   platform wheels. It is the same source, so the pure modules stay the reference, but
   mypyc checks at run time the types mypy infers: an attribute or local has to keep
   one type, and one that mypy would narrow to `None` across a call that sets it is
   annotated `Any`. The build fails if those files do not pass
   `mypy --check-untyped-defs`.

`benchmarks/bench.py --check` measures this against real symbol corpora in CI on every
pull request, so a regression fails the build.

## Correctness

Correct means agreeing with the reference demangler, not with our reading of the
specification. Where a scheme has no reference it's checked against what the compiler
recorded for each symbol, and Go and JNI, which have neither, must re-mangle to the
symbol they were read from. [CONFORMANCE.md](CONFORMANCE.md) says what each scheme is
measured against and what the measurement says.

`tests/conformance/` holds frozen corpora with the reference output next to each name;
[CONTRIBUTING.md](CONTRIBUTING.md#conformance-numbers) says how their pass counts are
pinned. The harness in `tools/` regenerates the corpora and can run a live differential
against the reference binaries when they're installed.

`demangle()` never raises over a name and returns its input unchanged when it can't do
better, because a wrong expansion is worse than a mangled name: it matches neither the
symbol nor the declaration. Callers who need to know the difference use `parse()`,
which raises.
