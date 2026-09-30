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
allocates an object per grammar node. In Python that cost is measurable: building the
tree takes about a fifth longer than building the string, and most callers only ever
wanted the string.

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

The parser is written once and stays honest about the grammar; the cost model is
chosen by the caller. Adding a third backend -- emitting JSON, or a token stream for a
syntax highlighter -- means writing one class and touching no parser.

This is the single most important thing to understand about the codebase. A change that
makes a parser build strings directly, however locally convenient, breaks it. A test
enforces the rule as far as a test can reach -- `tests/test_architecture.py` fails if a
scheme imports `core.spelling` -- but a parser that concatenates strings itself and
hands the result to `builder.name()` imports nothing, and only review catches that.

### Expressions

An expression inside a type goes through `Builder.expression(form, parts)`, so it
reaches the tree as structure rather than one opaque node. `parts` interleaves the
production's fixed text with its operands' handles and `form` names the shape --
`binary`, `conditional`, `call`, `sizeof`.

One method rather than one per operator. Fifteen methods would be fifteen things every
future builder must implement, and would still not cover the next operator someone
mangles. The parser already owns operator *spelling* -- it comes from `tables.py`, which
exists to be checked against the ABI -- so what is left for the builder to decide is
structure, and `form` plus operands is that.

Brackets are parts like any other. Whether an operand needs them is a precedence
question only the parser can answer, so it arrives settled; a consumer walking the tree
sees a `paren` expression wrapping the operand rather than punctuation glued into a
string.

Rust reaches the same place by a different route, and the difference is worth knowing.
Its printer emits one linear stream of fragments and a *sink* decides what to do with
them: `TextSink` concatenates, `TreeSink` remembers where each production began and
ended. So the tree is not a second traversal that has to be kept in step with the
text -- it is the same traversal with its boundaries kept, and a tree renders to what
`demangle()` returns by construction rather than by test. Rust can do this because its
spelling is strictly left to right; a C-family declarator, which wraps the name it
declares, cannot be recovered from a linear stream and needs the builder proper.

### When a scheme needs its own spelling

`core/spelling.py` implements *C-family* declarator placement. A scheme whose output
looks like a C declaration uses it and gets the hard part for free. A scheme whose
output does not -- MSVC, where the calling convention sits inside the parentheses and
the spacing rules differ -- supplies its own node kinds and renderer instead,
subclassing `core.ast.Node` so `walk()`, `find()` and `spell()` keep working.
`AstBuilder` is shared regardless.

That is the extension point, not a workaround: forcing every scheme through one
renderer would mean MSVC-shaped branches inside `core`.

## Layout

```
demangle/
  api.py              demangle(), parse(), detect() -- the public surface
  _signature.py       signature(): the parts of a name rather than its spelling
  filter.py           demangling the symbols out of text that is not only symbols
  cli.py              the `demangle` command
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

C++ spells a type *around* the name it declares -- `int (*f)(char)` -- so a type under
construction is a pair of strings with a hole between them, not a string. Keeping that
hole in the right place through nested declarators is the part of a demangler most often
subtly wrong, so `core/spelling.py` does it once, with its own tests, for every scheme
whose output looks like a C declaration. Its
[reference](https://r0ny123.github.io/demangle/reference/core/#spelling) has the
mechanics.

## Substitutions are load-bearing, not an optimisation

The Itanium scheme compresses repeated components into back-references: `S_`, `S0_`,
`S1_`. The numbering is implicit -- it depends entirely on which components the
*encoder* considered substitutable, and in what order. Add one entry the specification
does not, or miss one it does, and every later back-reference in the name resolves to
the wrong component.

The rules are not folklore; they are ABI section 5.1.10, and
`schemes/itanium/substitutions.py` implements them as a separate, directly testable
component with the specification quoted at each decision. Rust v0 has its own
back-reference scheme with different rules, kept in its own module for the same reason.

## Adding a scheme

A scheme is a directory under `schemes/` exposing a `LanguagePlugin` (`core/plugin.py`).
Nothing in `core` knows the list of schemes at import time: `core/registry.py` finds the
built-ins lazily and third-party plugins through the `demangle.languages` entry-point
group, so a separate distribution can add a scheme without a patch to this one.

Detection is the part with a cost model. It runs on every non-mangled symbol in a
binary -- in a typical binary, most of them -- so `detect` is expected to be a prefix
test, and the registry narrows what a name is offered to before any `detect` runs. A
scheme with a fixed first character declares it (`first_characters`), and a name
starting with anything else never reaches it. A scheme whose names are ordinary C
identifiers has none, so it declares a `DETECT_SCREEN`: the markers and openings that a
name must hold for its `detect` to say yes, and a name with none of them is not offered
to it. The schemes whose names carry no marker at all -- the pre-Itanium C++
manglings, whose `detect` has to parse -- are offered last, behind everything a prefix
settles.

[Adding a scheme](https://github.com/r0ny123/demangle/blob/main/docs/adding-a-scheme.md)
is the walk-through.

## Performance

Design rules, in the order they matter:

1. **The common call must not allocate an AST.** Hence the builder protocol.
2. **Detection precedes parsing.** See [Adding a scheme](#adding-a-scheme).
3. **Results are memoised.** Symbol tables repeat names heavily -- the same
   `std::allocator<char>` appears thousands of times in one binary. Caches are bounded
   so a long-running process cannot grow without limit.
4. **`__slots__` on every hot class.** Node and Spelling instances are created in the
   millions.
5. **No regular expressions in the hot parse loops.** The Itanium, MSVC, Rust and Swift
   parsers advance by character dispatch; a regular expression appears there only to
   test a whole token or a suffix. The smaller schemes (JNI, Objective-C, Pascal,
   Delphi, CodeWarrior, Ada) use them over short names.

`benchmarks/` measures all of this against real symbol corpora, and
`benchmarks/bench.py --check` runs in CI on every pull request, so a regression fails
the build rather than waiting for someone to complain.

## Correctness

Correctness is defined against the reference implementations, not against our own
reading of the specifications. Most schemes have one: `llvm-cxxfilt` and GNU `c++filt`
for Itanium, `llvm-undname` for MSVC, the `rustc-demangle` crate for Rust,
`c++filt --format=dlang` for D and `--format=gnat` for Ada, Embarcadero's own unmangler
for Delphi, and the third-party `cwdemangle` for CodeWarrior. Where no distribution
ships one, it is built here -- Swift's and pre-Itanium C++'s from the compiler's own
sources, and `rustc-demangle` behind a small front end, since the Rust readers inside
the C++ demanglers are not it. Nim, Free Pascal and Objective-C are checked against what
the compiler itself recorded for each symbol. Go and JNI have no reference anywhere, and
are held to a property instead: re-mangling what was read has to reproduce the symbol.
[CONFORMANCE.md](CONFORMANCE.md) records what each is measured against and what the
measurement says.

`tests/conformance/` holds frozen corpora with the reference output recorded next to
each name; [CONTRIBUTING.md](CONTRIBUTING.md#conformance-numbers) says how their pass
counts are pinned. The harness in `tools/` regenerates those corpora and can run a live
differential against the reference binaries when they are installed.

The contract at the boundary is deliberately narrow: `demangle()` never raises and
returns its input unchanged when it cannot do better, because a wrong expansion is
worse than a mangled name -- it matches neither spelling. Callers who need to know the
difference use `parse()`, which raises.
