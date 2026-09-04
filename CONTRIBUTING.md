# Contributing

The project is meant to be approachable one piece at a time. Adding a scheme, fixing a
spelling, or contributing a corpus should not require understanding the whole codebase.

## Getting set up

The project builds with [Hatch](https://hatch.pypa.io/) and installs with
[uv](https://docs.astral.sh/uv/). You need neither -- plain pip works -- but together
they turn the whole check into one command that finishes in seconds.

```console
git clone https://github.com/r0ny123/demangle
cd demangle

# Creates the environment and runs everything a pull request must pass.
hatch run check
```

The pieces are available on their own: `hatch run test`, `cover`, `lint`, `fmt`,
`bench`, `differential`. `hatch run test:test` runs the suite on Python 3.13 and 3.14,
and `hatch run docs:serve` previews the documentation site.

Without Hatch:

```console
uv pip install -e . --group dev     # or: pip install --group dev  (pip 25.1+)
pytest
```

Development requirements are [dependency groups](https://peps.python.org/pep-0735/),
not extras. An extra is a *published* feature of the distribution -- `pip install
demangle[dev]` would appear on PyPI as something users are invited to install -- and a
linter is not a feature of a demangler. The groups are `test`, `format`, `lint`, `docs`,
and `dev`, which includes the first three.

The toolchain is deliberately the fast one: **ruff** for linting and formatting (it
replaces black, isort, flake8 and pyupgrade), **ty** for type checking (it replaces
mypy), **uv** for installing. Both checkers are written in Rust and cover the whole tree
in well under a second, which is what makes it reasonable to gate every commit on them.
Both are pinned exactly; Dependabot proposes the bumps.

The reference demanglers are optional but useful. On Debian or Ubuntu:

```console
apt-get install llvm clang g++ binutils
```

## Reporting a conformance bug

The most valuable report is small and complete:

1. the mangled name,
2. what the reference demangler prints (`llvm-cxxfilt`, `llvm-undname`, or
   `rustc-demangle`),
3. what this library prints.

Add the pair to the matching file in `tests/conformance/` and bump the pinned count in
`tests/test_conformance.py` in the same commit, so the expected number always matches
what the suite actually achieves.

## The rules that matter

Most of the codebase is ordinary Python. Three rules are not negotiable, because the
design rests on them. Two are fully enforced by a test; the first is enforced as far as
a test can reach, and ARCHITECTURE.md says where it does not:

1. **Parsers never build their own output.** Write against the `Builder` protocol
   (`core/builder.py`). Building a string directly is locally convenient and breaks both
   `parse()` and every future output format.
2. **`core` never imports a scheme, and schemes never import each other.** A scheme has
   to stay replaceable in isolation.
3. **No third-party dependencies.** The dependency-free promise is the reason a lot of
   people can use this at all. CI asserts it against a clean install of the built wheel,
   not just against the source tree.

## Working on the Itanium parser

Substitution numbering is where correctness lives. The rules are ABI section 5.1.10 and
are implemented in `schemes/itanium/substitutions.py`, which rejects any attempt to
record a production the specification does not call a candidate.

The specification's prose is genuinely ambiguous in places. Do not guess — ask the
reference implementation. `tools/probe_substitutions.py` appends `S_`, `S0_`, `S1_` to a
name under construction, which makes the reference print its own substitution table
back at you. Several decisions in the parser were settled that way, and each carries a
comment naming the probe that settled it.

## Adding a scheme

See [docs/adding-a-scheme.md](docs/adding-a-scheme.md). In short: three functions and a
`LanguagePlugin`, in a new directory under `src/demangle/schemes/`.

## Before you open a pull request

```console
hatch run check
```

or, without Hatch:

```console
ruff check . && ruff format --check . && ty check .
pytest
python tools/differential.py
python benchmarks/bench.py --check
```

`tools/differential.py` with no arguments replays every checked-in corpus under the
style and language it was recorded with, and exits non-zero if anything disagrees.

If a change moves a conformance number, say which way and why in the commit message.
If it moves a benchmark, say that too.

### Enumeration

```console
python tools/enumerate.py            # every scheme with a reference on this machine
python tools/enumerate.py --length 6 # deeper, and much slower
```

The corpora are real symbols, so they cover the shapes compilers *emit*. They do not
cover the shapes a grammar *permits*, and that is where the worst defects live: an
encoding no compiler writes, read as something that looks like a declaration a person
would believe. `tools/enumerate.py` offers every string up to `--length` characters over
a per-scheme alphabet to the library, puts the ones it reads to the reference demangler,
and reports every disagreement -- including the direction that matters, where the
reference hands the name back and this library answers.

It is worth running against any change to a parser's shape rules. Seventeen defects came
out of one sitting with it, in five schemes: `_Z1fIiEi` read as `int f<int>()`, `_RNvC_1f`
as `::f`, `_D3fooC` as `foo`, `_D4testFMMfZv` as `test(scope scope float)`.

A disagreement that is the reference's own goes in `ACCEPTED` with the reason, not in a
list of names -- the shapes are families, and a list goes stale the moment an alphabet
changes. Where a scheme has two references the tool asks both, each under its own style,
because agreeing with one build of one reference is not the same as being right.

### Mutation

```console
python tools/mutate.py                  # the pinned draw, which is what CI runs
python tools/mutate.py --count 200000   # more mutants per scheme
python tools/mutate.py --seed 7         # a different draw; the default draw is fixed
```

Enumeration counts *short* strings, which is the wrong length for everything that only
appears once a name is long: a substitution referring back to a component built earlier,
a template argument list three deep, a return type that is itself a function pointer.
No alphabet is small enough to reach those by counting.

So `tools/mutate.py` starts from the checked-in corpora instead and damages them --
truncate, delete, duplicate, transpose, substitute a character from the scheme's own
alphabet, or splice the head of one symbol onto the tail of another. A mutant keeps
almost all of its parent's structure, so it lands *near* the emitted space rather than in
the grammar's cheap corners, which is where a substitution table gets corrupted rather
than merely emptied. The draw is seeded, so a failure reproduces exactly.

```console
python tools/invariants.py                # the same mutants, put to the library itself
python tools/invariants.py --corpus swift # one corpus
```

`tools/invariants.py` borrows those mutation operators and asks what no reference can be
asked: that a *style* decides a spelling and never whether a name parses, that
`parse(name).spell()` is what `demangle(name)` returns in every style, and that
`signature`, `demangleb` and `parse().to_dict()` raise nothing but a `DemanglingError` on
a name `demangle` read. The first of those was broken for twelve corpus names when the
tool was written -- the GNU style refused `std::pair`'s constrained constructor that the
llvm style read.

Because none of that needs a reference, it seeds from *every* corpus rather than from the
five schemes `tools/mutate.py` can ask about, and takes each corpus's own characters as
its alphabet -- so Swift, Nim, Free Pascal, Delphi, Go, Objective-C and JNI are fuzzed
here and nowhere else. A tool that is quiet proves nothing on its own:
`--seed 1 --count 20000 --corpus itanium-libcxxabi` reports the defect it was written for
on the parser as it stood, and nothing on the parser as it is.

The count is pinned in both directions rather than driven to zero: `--expect` fails on a
new divergence *and* on a stale pin after one is fixed, which is how the conformance
corpora are pinned. It stands at zero: every divergence the default draw reports is
either a defect that was fixed or an accept rule naming the reason a reference's answer
is not evidence. An accept rule for a reason nobody has established is how a defect gets
filed as a reference's -- the D divergence that stood here for several sittings became a
rule only once diffing the mutant against its seed named the shape, and the description
it had carried until then turned out to be wrong. Zero is not a claim that nothing is
left, either: a divergence not in this draw is not one that does not exist, which is what
`--seed` and `--count` are for.

It shares `ACCEPTED` with `tools/enumerate.py` on purpose: those rules are statements
about why a reference's answer is not evidence, and the reason does not change with how
the name was found. Two mechanisms are its own. `RESCUE` asks the reference about a
*different* name where it cannot read the one in hand for a known reason --
`llvm-undname` 18 refuses every ARM64EC name, so it is asked about the name without the
`$$h` marker, which is exactly how `tests/conformance/msvc-arm64ec.txt` was built.
`SECOND_OPINION` asks another reference about the same name, for schemes where a
divergence from the first is not evidence on its own.

Twenty-eight defects came out of the first three sittings with it, in four schemes. Two
were structural. Five of the seven Itanium `<prefix>` productions are bases and take no
prefix on the left, so `_ZN1aSa1bEv` is not a name -- it had read as
`a::std::allocator::b()`. And a D `Q` back reference points at a length-prefixed
identifier and nothing else, so a mutated index that lands on a template instance is not
a name either -- it had read as one, with the instance named twice. The rest run from a
D pointer to a function pointer spelled as the function pointer, through a D integer
literal re-formatted rather than echoed and a D negative `char` that lost its sign, to a
Rust `<base-62-number>` read wider than the reference reads one, an MSVC ARM64EC marker
stripped until none was left, and MSVC qualifiers written in the order they were read
rather than the order the reference writes them.

### The MSVC corpus a compiler wrote

`msvc-llvm-corpus.txt` is LLVM's own *test* file: vectors somebody chose, which means it
carries the shapes its author thought of. `tools/corpus_sources/msvc/msvc.cpp` is
compiled instead:

```console
python tools/generate_corpus.py --sources tools/corpus_sources/msvc \
  --target x86_64-pc-windows-msvc --compiler clang++ --prefix '?' \
  --tool llvm-undname --name msvc-clang.txt \
  --defects tests/conformance/msvc-reference-defects.txt
```

`clang++` can target the MS ABI from a Linux box, so the object file it produces holds
real MSVC-mangled symbols -- vftables, RTTI records, thunks, guards, the dynamic
initialiser stubs, the anonymous namespace, local scopes. The source is freestanding
because that target has no headers here.

Two defects came out of the first run of it, and neither shape is in LLVM's vectors: a
member function's qualifiers written past what its return type wraps -- `char const (&
S::b7(void))[2] const`, a const array rather than a const member function -- and a
dynamic initialiser for a *qualified* variable refused outright, which is every
namespace-scope object with a non-trivial constructor.

`tests/conformance/msvc-reference-defects.txt` holds the names from that run
`llvm-undname` cannot read at all, with the declaration as the expected column. The
generator reads it and excludes them, so a regeneration cannot record the refusal as the
answer.

### The Rust reference

`llvm-cxxfilt` and `c++filt` each carry their own Rust reader -- LLVM's is a port of an
older `rustc-demangle`, binutils' is independent of both -- so on Rust neither of the
demanglers already on the machine is the implementation `src/demangle/schemes/rust/` is a
port of. Where the three disagree, neither of those two settles it.

`tools/rustc-demangle-reference/` is a twenty-line front end over the crate itself,
answering one spelling per line the way the C++ demanglers do:

```console
cargo build --release --manifest-path tools/rustc-demangle-reference/Cargo.toml
```

`tools/enumerate.py` and `tools/mutate.py` use it when it has been built and fall back to
`llvm-cxxfilt` when it has not. It is not a dependency of the test suite; building it
needs a Rust toolchain and one fetch from crates.io.

### The Swift reference

Nothing a distribution ships reads a Swift name: `llvm-cxxfilt` and `c++filt` both
decline a `$s` outright. So `tools/swift-demangle-reference/` builds swiftlang/swift's
own `lib/Demangling` -- eleven files, unmodified, at a pinned revision -- behind the same
line-per-name front end the Rust reference uses:

```console
tools/swift-demangle-reference/build.sh
```

It needs a C++17 compiler, LLVM's headers (`llvm-dev`) and one fetch from github.com.
No Swift toolchain, no CMake, no LLVM libraries; the checkout is a blobless sparse one
over five directories, about 16 MB. `tools/enumerate.py` and `tools/mutate.py` use it
when it has been built and skip the Swift job when it has not, since there is nothing to
fall back to.

The revision is a commit on `main` rather than a release tag, and
`tools/swift-demangle-reference/README.md` has the measurements that decide it, along
with the one place the reference is wrong and this library does not follow it.

### The pre-Itanium C++ reference

Nothing current reads a pre-Itanium name either: binutils 2.42's `c++filt` offers no
`--format=gnu`, `lucid`, `arm` or `hp`, and GCC 9 removed the demangler from libiberty.
So `tools/cplus-dem-reference/` builds it from GCC 8.3.0's tree -- `cplus-dem.c` and the
five helpers it calls, unmodified, which is the tree `tests/conformance/gnuv2-libiberty.txt`
was transcribed from -- behind the same line-per-name front end:

```console
tools/cplus-dem-reference/build.sh
```

It needs a C compiler, `curl`, `sha256sum` and one fetch from github.com; every file is
pinned by tag and by checksum. `tools/enumerate.py` and `tools/mutate.py` use it when it
has been built and skip the `gnuv2` job when it has not. Its README records what the
build reproduces (the corpus, 1,324 of 1,324) and the two families where libiberty reads
what this library refuses.

## CI and workflows

Workflows run with `permissions: contents: read` and grant more only where a job needs
it. Checkout uses `persist-credentials: false`, because nothing in CI pushes and a token
left in `.git/config` is readable by every later step. Releases publish through PyPI
Trusted Publishing, so there is no long-lived API token in repository secrets.

The test matrix is deliberately not a cross product. Both supported versions run on
Linux; macOS and Windows get one row each, the ceiling and the floor, so neither end of
the range is only ever exercised on Linux. This is a pure-Python library, and everything
that has ever differed between platforms differed in the harness -- a glob, a subprocess,
a console encoding -- which one row per operating system catches as well as four do. Add
a row when a defect shows up that only that row would have caught, and say so in the
comment next to it.

Two rules for anyone editing `.github/`:

- Never use `pull_request_target`. It runs with a writable token against the fork's
  code, which makes any pull request arbitrary code execution with our secrets.
- Never interpolate untrusted values -- issue titles, branch names, PR bodies -- into a
  `run:` block. Pass them through `env:` and reference the variable.

## Style

- Comments explain *why*, especially where the code looks odd because a specification or
  a reference implementation says so. Quote the section.
- Names are spelled out. This is a codebase people read while holding an ABI document in
  the other hand; `substitution_table` beats `st`.
- New behaviour comes with a test. New *reference-derived* behaviour comes with a corpus
  entry.
