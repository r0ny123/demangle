# Fuzzing and the reference demanglers

The checked-in corpora are real symbols, so they cover the shapes compilers *emit*.
These tools cover the shapes a grammar *permits*, which is where the worst defects
live -- an encoding no compiler writes, read as something that looks like a
declaration a person would believe -- and they build the three references that have to
be built here because no distribution ships one. The last section covers the other way
a corpus falls behind: the reference's own vectors moving on after the transcription.

## Enumeration

```console
python tools/enumerate.py            # every scheme with a reference on this machine
python tools/enumerate.py --length 6 # deeper, and much slower
```

`tools/enumerate.py` offers every string up to `--length` characters over a per-scheme
alphabet to the library, puts the ones it reads to the reference demangler, and reports
every disagreement -- including the direction that matters, where the reference hands
the name back and this library answers. CI runs it at `--length 4`. It finds shapes such
as `_Z1fIiEi` read as `int f<int>()`, `_RNvC_1f` as `::f` and `_D3fooC` as `foo`.

A disagreement that is the reference's own goes in `ACCEPTED` with the reason, not in a
list of names -- the shapes are families, and a list goes stale the moment an alphabet
changes. Where a scheme has two references the tool asks both, each under its own style,
because agreeing with one build of one reference is not the same as being right.

## Mutation

```console
python tools/mutate.py --count 20000    # what CI runs, with --quiet
python tools/mutate.py                  # the default draw: 50,000 mutants per scheme
python tools/mutate.py --count 200000   # more mutants per scheme
python tools/mutate.py --seed 7         # a different draw; the default draw is fixed
python tools/mutate.py --scheme types   # bare <type> encodings, through demangle_type
```

Enumeration counts *short* strings, which is the wrong length for everything that only
appears once a name is long: a substitution referring back to a component built earlier,
a template argument list three deep, a return type that is itself a function pointer.
No alphabet is small enough to reach those by counting.

So `tools/mutate.py` starts from the checked-in corpora instead and damages them --
truncate, delete, duplicate, transpose, substitute a character from the scheme's own
alphabet, or splice the head of one symbol onto the tail of another. A mutant keeps
almost all of its parent's structure, so it lands *near* the emitted space rather than
in the grammar's cheap corners, which is where a substitution table gets corrupted
rather than merely emptied. The draw is seeded, so a failure reproduces exactly.

```console
python tools/mutate.py --refusals              # what the reference reads that this refuses
python tools/mutate.py --refusals --scheme d --count 20000 --show 40
```

The gate compares only the mutants this library *reads*, so a name it refuses and the
reference reads never reaches it. `--refusals` asks that question: every refused mutant
goes to the reference, and what it read comes back as a list. It is a list and not a
gate on purpose. Most of what it reports is a reference reading past its grammar --
libiberty's D demangler is content with a static array whose bound has no digits, and
with a template argument list that ends with the name rather than with `Z` -- and the
rest is a defect: MSVC's ellipsis-only parameter list, Swift's six uncounted context
kinds and two D shapes came out of it. A refused name can also take a reference down --
Swift's own demangler aborts on some, and binutils' D demangler expands a chain of back
references into gigabytes -- so each batch runs under a memory cap and a timeout, and a
batch that dies is split until the one name that did it is found and recorded as a
refusal. Triage what it prints against the reference's source: a reading the grammar
does not admit is pinned as a refusal in the scheme's tests, and one it does is a fix.

The count is pinned in both directions rather than driven to zero: `--expect` fails on a
new divergence *and* on a stale pin after one is fixed, which is how the conformance
corpora are pinned. It stands at zero: every divergence the default draw reports is
covered by an accept rule naming the reason a reference's answer is not evidence. An
accept rule for a reason nobody has established is how a defect gets filed as a
reference's, so a rule is written only once diffing the mutant against its
seed has named the shape. Zero is not a claim that nothing is left, either: a divergence
not in this draw is not one that does not exist, which is what `--seed` and `--count`
are for.

It shares `ACCEPTED` with `tools/enumerate.py` on purpose: those rules are statements
about why a reference's answer is not evidence, and the reason does not change with how
the name was found. Two mechanisms are its own. `RESCUE` asks the reference about a
*different* name where it cannot read the one in hand for a known reason --
`llvm-undname` 18 refuses every ARM64EC name, so it is asked about the name without the
`$$h` marker, which is exactly how `tests/conformance/msvc-arm64ec.txt` was built.
`SECOND_OPINION` asks another reference about the same name, for schemes where a
divergence from the first is not evidence on its own.

It reaches structural defects the corpora cannot: five of the seven Itanium `<prefix>`
productions are bases and take no prefix on the left, so `_ZN1aSa1bEv` is not a name,
and a D `Q` back reference points at a length-prefixed identifier and nothing else.

## Invariants

```console
python tools/invariants.py                # the default draw, which is what CI runs
python tools/invariants.py --corpus swift # one corpus
```

`tools/invariants.py` borrows those mutation operators and asks what no reference can be
asked: that a *style* decides a spelling and never whether a name parses, that
`parse(name).spell()` is what `demangle(name)` returns in every style, and that
`signature`, `demangleb` and `parse().to_dict()` raise nothing but a `DemanglingError`
on a name `demangle` read.

Because none of that needs a reference, it seeds from *every* corpus rather than from
the seven schemes `tools/mutate.py` can ask about, and takes each corpus's own
characters as its alphabet -- so Nim, Free Pascal, Delphi, Go, Objective-C, JNI and
CodeWarrior are fuzzed here and nowhere else. A tool that is quiet proves nothing on its
own: `--seed 1 --count 20000 --corpus itanium-libcxxabi` reports the defect it was
written for on the parser as it stood, and nothing on the parser as it is.

## The MSVC corpus a compiler wrote

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

Its shapes are ones LLVM's vectors lack: a member function's qualifiers written past
what its return type wraps -- `char const (& S::b7(void))[2] const`, a const array
rather than a const member function -- and a dynamic initialiser for a *qualified*
variable, which is every namespace-scope object with a non-trivial constructor.

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

Every corpus above is a transcription made on a day, and the references keep moving: a
C++ draft adds a mangling, rustc revises v0, Swift adds a node kind, and each lands in
the reference's own test file first. `tools/upstream_drift.py` fetches those files from
their `main` branches -- libcxxabi's `DemangleTestCases.inc`, LLVM's `ms-*.test`,
Swift's `manglings.txt`, rustc-demangle's `#[test]` vectors -- and scores this library
on every vector the corpora do not yet hold.

```console
python tools/upstream_drift.py                   # every source
python tools/upstream_drift.py --source swift    # one of them
python tools/upstream_drift.py --cache .drift    # keep the downloads between runs
```

A name the corpus holds with the same expectation is unchanged; one it holds with a
different expectation is reported for a person to read, since the corpora carry
documented deviations; a name the corpus does not hold is new, and a new one this
library misreads is what the exit status says: 1, against 2 for a file that could not
be fetched or read. `--show` sets how many examples each table prints.
`.github/workflows/upstream.yml` runs it weekly and opens an issue, or adds to the open
one, when something new is misread.
