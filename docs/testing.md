# Fuzzing and the reference demanglers

The checked-in corpora are real symbols, so they cover the shapes compilers emit. The
tools on this page cover the shapes a grammar permits, which is where the worst bugs
are: an encoding no compiler writes, read as a declaration someone would believe. The
page also covers building the three references no distribution ships, and the two
checks for a reference moving on after a corpus was recorded.

## Enumeration

```console
python tools/enumerate.py            # every scheme with a reference on this machine
python tools/enumerate.py --length 6 # deeper, and much slower
```

`tools/enumerate.py` offers the library every string up to `--length` characters over a
per-scheme alphabet, puts the ones it reads to the reference demangler, and reports
every disagreement, including the one that matters most, where the reference hands the
name back and this library answers. CI runs it at `--length 4`. It finds shapes such as
`_Z1fIiEi` read as `int f<int>()`, `_RNvC_1f` as `::f` and `_D3fooC` as `foo`.

A disagreement that's the reference's fault goes in `ACCEPTED` with the reason, not in
a list of names: the shapes are families, and a list goes stale as soon as an alphabet
changes. Where a scheme has two references the tool asks both, each under its own
style.

## Mutation

```console
python tools/mutate.py --count 20000    # what CI runs, with --quiet
python tools/mutate.py                  # the default draw: 50,000 mutants per scheme
python tools/mutate.py --count 200000   # more mutants per scheme
python tools/mutate.py --seed 7         # a different draw; the default draw is fixed
python tools/mutate.py --scheme types   # bare <type> encodings, through demangle_type
```

Enumeration only reaches short strings, and plenty only shows up once a name is long:
a substitution pointing back at an earlier component, a template argument list three
deep, a return type that's itself a function pointer.

So `tools/mutate.py` starts from the checked-in corpora and damages them: truncate,
delete, duplicate, transpose, substitute a character from the scheme's own alphabet,
or splice the head of one symbol onto the tail of another. A mutant keeps almost all
of its parent's structure, so it lands near what compilers emit, where a substitution
table gets corrupted and not just emptied. The draw is seeded, so a failure reproduces
exactly.

```console
python tools/mutate.py --refusals              # what the reference reads that this refuses
python tools/mutate.py --refusals --scheme d --count 20000 --show 40
```

The gate only compares the mutants this library reads, so a name it refuses and the
reference reads never reaches it. `--refusals` covers that: every refused mutant goes
to the reference, and what it read comes back as a list. It's a list and not a gate on
purpose. Most of what it reports is a reference reading past its grammar (libiberty's
D demangler accepts a static array whose bound has no digits), and the rest is a real
bug: MSVC's ellipsis-only parameter list, Swift's six uncounted context kinds and two
D shapes came out of it. Check what it prints against the reference's source. A
reading the grammar doesn't admit gets pinned as a refusal in the scheme's tests, and
one it does admit is a fix.

A refused name can also take a reference down. Swift's own demangler aborts on some,
and binutils' D demangler expands a chain of back references into gigabytes. So each
batch runs under a memory cap and a timeout, and a batch that dies is split until the
one name that did it is found and recorded as a refusal.

The divergence count is pinned, the way the conformance corpora are: `--expect` fails
on a new divergence and on a stale pin after one is fixed. It stands at zero, because
every divergence the default draw reports is covered by an accept rule that names why
the reference's answer doesn't count. A rule is only written once diffing the mutant
against its seed has named the shape. Zero doesn't mean nothing is left, only that
nothing is left in this draw, which is what `--seed` and `--count` are for.

`ACCEPTED` is shared with `tools/enumerate.py`, since why a reference's answer doesn't
count has nothing to do with how the name was found. Two mechanisms are `mutate.py`'s
own. `RESCUE` asks the reference about a different name when it can't read the one in
hand for a known reason: `llvm-undname` 18 refuses every ARM64EC name, so it's asked
about the name without the `$$h` marker, which is how
`tests/conformance/msvc-arm64ec.txt` was built. `SECOND_OPINION` asks another reference
about the same name, for schemes where disagreeing with the first proves nothing on
its own.

Mutation reaches structural bugs the corpora can't: five of the seven Itanium
`<prefix>` productions are bases and take no prefix on the left, so `_ZN1aSa1bEv` is
not a name, and a D `Q` back reference points at a length-prefixed identifier and
nothing else.

## Invariants

```console
python tools/invariants.py                # the default draw, which is what CI runs
python tools/invariants.py --corpus swift # one corpus
```

`tools/invariants.py` uses the same mutation operators to check things no reference
can be asked: that a style decides a spelling and never whether a name parses, that
`parse(name).spell()` is what `demangle(name)` returns in every style, and that
`signature`, `demangleb` and `parse().to_dict()` raise nothing but a `DemanglingError`
on a name `demangle` read.

None of that needs a reference, so it seeds from every corpus and not just the seven
schemes `tools/mutate.py` can ask about, with each corpus's own characters as its
alphabet. Nim, Free Pascal, Delphi, Go, Objective-C, JNI and CodeWarrior are fuzzed
here and nowhere else.

## The MSVC corpus a compiler wrote

`msvc-llvm-corpus.txt` is LLVM's own test file, so it carries the shapes its author
thought of. `tools/corpus_sources/msvc/msvc.cpp` is compiled instead:

```console
python tools/generate_corpus.py --sources tools/corpus_sources/msvc \
  --target x86_64-pc-windows-msvc --compiler clang++ --prefix '?' \
  --tool llvm-undname --name msvc-clang.txt \
  --defects tests/conformance/msvc-reference-defects.txt
```

`clang++` can target the MS ABI from Linux, so the object file holds real MSVC-mangled
symbols: vftables, RTTI records, thunks, guards, dynamic initialiser stubs, the
anonymous namespace, local scopes. The source is freestanding because that target has
no headers here.

It has shapes LLVM's vectors lack. One is a member function's qualifiers written past
what its return type wraps, as in `char const (& S::b7(void))[2] const`, which is a
const array and not a const member function. Another is a dynamic initialiser for a
qualified variable, which is every namespace-scope object with a non-trivial
constructor.

`tests/conformance/msvc-reference-defects.txt` holds the names from that run
`llvm-undname` cannot read at all, with the declaration as the expected column. The
generator reads it and excludes them, so a regeneration cannot record the refusal as the
answer.

## The Rust reference

`llvm-cxxfilt` and `c++filt` each carry their own Rust reader, and neither is the
`rustc-demangle` crate that `src/demangle/schemes/rust/` ports. The
[README](https://github.com/r0ny123/demangle/blob/main/tools/rustc-demangle-reference/README.md)
has why and what the build needs; without it the fuzzers use `llvm-cxxfilt`.

```console
cargo build --release --manifest-path tools/rustc-demangle-reference/Cargo.toml
```

With it built, `tools/sweep_rust_chars.py` puts every Unicode scalar value to both as a
`char` const. Whether one prints bare or escaped follows the Unicode tables, and
CPython's and the crate's are not always the same version; the sweep counts where they
part, in about a minute.

## The Swift reference

Nothing a distribution ships reads a Swift name, so `tools/swift-demangle-reference/`
builds swiftlang/swift's own demangler. Its
[README](https://github.com/r0ny123/demangle/blob/main/tools/swift-demangle-reference/README.md)
has what the build needs and why the revision is a commit rather than a release tag.

```console
tools/swift-demangle-reference/build.sh
```

## The pre-Itanium C++ reference

Nothing current reads a pre-Itanium name, so `tools/cplus-dem-reference/` builds
libiberty's demangler from GCC 8.3.0. Its
[README](https://github.com/r0ny123/demangle/blob/main/tools/cplus-dem-reference/README.md)
has what the build needs and what it reads that this library refuses.

```console
tools/cplus-dem-reference/build.sh
```

## The references' vectors as they are today

Every corpus above was recorded on some day, and the references keep moving: a C++
draft adds a mangling, rustc revises v0, Swift adds a node kind, and each lands in the
reference's own test file first. `tools/upstream_drift.py` fetches those files from
their `main` branches (libcxxabi's `DemangleTestCases.inc`, LLVM's `ms-*.test`, Swift's
`manglings.txt`, rustc-demangle's `#[test]` vectors) and scores this library on every
vector the corpora don't hold yet.

```console
python tools/upstream_drift.py                   # every source
python tools/upstream_drift.py --source swift    # one of them
python tools/upstream_drift.py --cache .drift    # keep the downloads between runs
```

A name the corpus already holds with the same expectation is skipped. One it holds
with a different expectation is reported for a person to read, since the corpora carry
documented deviations. A name the corpus doesn't hold is new, and a new one this
library misreads sets exit status 1 (2 means a file couldn't be fetched or read).
`--show` sets how many examples each table prints. `.github/workflows/upstream.yml`
runs it weekly and opens an issue, or adds to the open one, when something new is
misread.

## The newest LLVM over the corpora

The vectors above are what a reference asserts. This is what the newest LLVM does, over
every name the checked-in corpora hold. The corpora were recorded against LLVM 18,
which is what a CI runner ships, and the PyPI package `llvm-demangle-fxti` bundles LLVM
11 through 21 behind one import. `tools/upstream_drift.py` offers each name to the
reader LLVM would pick for it (`_Z` Itanium, `_R` Rust v0, `_D` D, `?` MSVC) as four
more sources, `llvm-itanium`, `llvm-msvc`, `llvm-rust` and `llvm-d`.

```console
uv pip install "llvm-demangle-fxti==1.0.0"        # optional: nothing else needs it
python tools/upstream_drift.py --source llvm-itanium llvm-msvc llvm-rust llvm-d
python tools/upstream_drift.py --source llvm-msvc --llvm-version 18   # an older LLVM
python tools/upstream_drift.py --no-llvm                              # the fetched sources only
```

The package isn't a dependency of anything: without it the sources are skipped with a
note on stderr. Only the newest LLVM is reported by default, because the question is
what someone with a current toolchain sees. `tests/conformance/` already records what
18 said, and the `cross-reference` job in `ci.yml` holds LLVM 16 and 18 against each
other and GNU `c++filt` for Itanium, and 16, 18 and 20 for MSVC.

The package is a third party's native wheel, so the weekly workflow keeps it away from
the token that files issues:

- The `llvm` job runs it alone, with `contents: read`, no secret and no credential left
  by checkout. A separate step wouldn't be enough, since steps of one job share
  `GITHUB_ENV` and `GITHUB_PATH`.
- It installs from `tools/llvm-oracle-requirements.txt` with `--require-hashes
  --only-binary :all:`, and scores with `--require-llvm` so a broken install is an
  error and not a clean week. It uploads a report and an exit status.
- The `drift` job, the only one with `issues: write`, never loads the binding. It reads
  that artifact as data and copies the report into the issue body, and it runs whatever
  became of `llvm`, so the fetched vectors still get their issue in a week the other
  half crashed.
- A status of 1 opens or updates the issue and 2 fails the run. Anything else (a
  segfault's 139, an empty file, no artifact because the job was killed) fails the run
  too, so a crash is never mistaken for a clean week.

A wheel that lies can misreport the `llvm-*` half, but it can't reach the token.
`tests/test_upstream_workflow.py` pins all of this.

Dependabot doesn't read `tools/`, so a new release of the package is a change by hand:
the version in the requirements file, `PIN` in `tools/llvm_oracle.py`, and the hash
from `https://pypi.org/pypi/llvm-demangle-fxti/<version>/json`. The hash is for the
one wheel the runner takes, CPython 3.13 on x86-64 Linux.

Each name falls in one of five places:

- **agree**: the two spell it alike, or both decline it.
- **known**: both read it, the spellings differ, and the repository already says why:
  the name is in `itanium-reference-defects.txt` or `msvc-reference-defects.txt`, whose
  expected column is the declaration; or the scheme's `ACCEPTED` rule in
  `tools/enumerate.py` covers it, with `c++filt`'s vote asked for Itanium as `mutate.py`
  does; or `tools/llvm_oracle.py` names it, for the three ways LLVM's Rust and D readers
  spell a symbol where the ports they differ from do not (a suffix set in parentheses,
  `\u{..}` for a non-ASCII `char`, a D template instance left as written).
- **unaccounted**: both read it, the spellings differ, and nothing says why. This is
  the one that sets exit status 1 and opens the issue.
- **LLVM declines** and **this library declines**: one side reads a name the other
  hands back. Listed, and never a failure, the way `tools/mutate.py --refusals` lists
  what a reference reads that this library refuses. LLVM's D reader reads qualified
  names and a few data symbols (`__init`, `__moduleRef`) but no function type, and its
  Rust reader stops short of some of v0's newer forms (aggregate constants, punycode),
  so most of what it declines is a reference that has not caught up; the C++26 pack
  indexing manglings `Dy` and `sy` are another. A name LLVM reads and this library
  declines is a gap here or LLVM reading junk, and a person settles which with LLVM's
  source open.

`ARM64EC` names are asked as `tools/mutate.py` asks them: no LLVM through 21 reads the
`$$h` marker, so a name carrying it is asked again without it.
