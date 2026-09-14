# Fuzzing and the reference demanglers

The checked-in corpora are real symbols, so they cover the shapes compilers *emit*.
These tools cover the shapes a grammar *permits*, which is where the worst defects
live, and they build the three references that have to be built here because no
distribution ships one.

Run them against any change to a parser's shape rules. Everything below is optional for
a one-line fix and expected for a parser change; [CONTRIBUTING](https://github.com/r0ny123/demangle/blob/main/CONTRIBUTING.md) has the
checklist that says which.

## Enumeration

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

## Mutation

```console
python tools/mutate.py                  # the pinned draw, which is what CI runs
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
almost all of its parent's structure, so it lands *near* the emitted space rather than in
the grammar's cheap corners, which is where a substitution table gets corrupted rather
than merely emptied. The draw is seeded, so a failure reproduces exactly.

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

Two defects came out of the first run of it, and neither shape is in LLVM's vectors: a
member function's qualifiers written past what its return type wraps -- `char const (&
S::b7(void))[2] const`, a const array rather than a const member function -- and a
dynamic initialiser for a *qualified* variable refused outright, which is every
namespace-scope object with a non-trivial constructor.

`tests/conformance/msvc-reference-defects.txt` holds the names from that run
`llvm-undname` cannot read at all, with the declaration as the expected column. The
generator reads it and excludes them, so a regeneration cannot record the refusal as the
answer.

## The Rust reference

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

## The Swift reference

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

## The pre-Itanium C++ reference

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
