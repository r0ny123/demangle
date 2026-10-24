# Comparison with other demanglers

This page sets `demangle` beside the demanglers a Python user is most likely to weigh it
against: what each one covers, what a run of
[`benchmarks/compare.py`](https://github.com/r0ny123/demangle/blob/main/benchmarks/compare.py)
measured, and what that measurement cannot say.

The result is mixed, so it comes first. On names it has not seen, `demangle` is slower
than every native demangler measured here: about 50 times slower than LLVM's on
Itanium names and 14 times on MSVC names, both read through `llvm-demangle-fxti`, and about
15 times slower than the Rust `cpp_demangle` crate on Itanium names. It is level with the
one other pure-Python reader, `itanium_demangler`. What it has instead is breadth, a parse
tree, bounded input, one wheel for every platform, and a result cache that makes a repeated
name cost about half a microsecond.

## What each one covers

| | `demangle` | `llvm-demangle-fxti` | `cpp-demangle` | `itanium_demangler` |
|---|---|---|---|---|
| Version measured | 0.6.0 | 1.0.0 | 0.1.3 | 1.1 |
| Written in | Python | C++: LLVM's Demangle library, vendored; LLVM 11 to 21 selectable | Rust: the [`cpp_demangle`](https://github.com/gimli-rs/cpp_demangle) crate, through PyO3 | Python |
| Schemes | Fourteen: Itanium C++, MSVC, Rust (legacy and v0), Swift, Objective-C, Go, D, Nim, Free Pascal, Delphi, Ada/GNAT, JNI, g++ 2.x with cfront/ARM, Lucid and HP aCC, and CodeWarrior | Itanium, Microsoft, Rust v0 and D; a legacy Rust name is read as the Itanium name it also is | Itanium | Itanium |
| What comes back | A string (`demangle`), a tree (`parse`), the parts of a signature (`signature`) | A string; for an Itanium function, `ItaniumPartialDemangler` answers for its base name, context, parameters and return type | A string | A tree of nodes, and a string rendering of it |
| Bounds on hostile input | Recursion depth, output size, substitution count and input length, each configurable per call; see [SECURITY.md](SECURITY.md) | Not tested here | Not tested here | Not tested here |
| Distribution | One `py3-none-any` wheel, Python 3.11 or newer; CPython and PyPy; runs in [Pyodide](playground.md) | `cp36-abi3` wheels for Linux x86-64 (glibc and musl), macOS x86-64 and arm64, and Windows 32- and 64-bit, and a source distribution; CPython only | `cp38-abi3` wheels for Linux x86-64 (glibc), macOS arm64 and Windows x86-64; 0.1.3 lists no source distribution; CPython only | One `py3-none-any` wheel |
| Latest release | 0.6.0, 2026-10-11 | 1.0.0, 2026-02-13, the only release | 0.1.3, 2026-04-28; the one before is 2023-05-01 | 1.1, 2022-08-20 |

The distribution rows are the file lists on PyPI: [`llvm-demangle-fxti`](https://pypi.org/project/llvm-demangle-fxti/),
[`cpp-demangle`](https://pypi.org/project/cpp-demangle/) and
[`itanium_demangler`](https://pypi.org/project/itanium-demangler). Neither native package
lists a PyPy or Pyodide wheel. On a platform with no listed wheel `llvm-demangle-fxti` has a
source distribution to build from, and `cpp-demangle` 0.1.3 has none.

Three command-line tools were measured as well, each run once over the whole list:

- **GNU `c++filt`** 2.42 reads what its `--format` option names: `gnu-v3` (Itanium), `java`,
  `gnat`, `dlang` and `rust`.
- **`llvm-cxxfilt`** 18.1.3 was measured on Itanium names. Given an MSVC name it handed it
  back unchanged.
- **`llvm-undname`** 18.1.3 reads MSVC names.

Disassemblers carry demanglers of their own. [Ghidra 12.1's change
history](https://ghidradocs.com/12.1_PUBLIC/docs/ChangeHistory.html) lists a GNU, a
Microsoft and a Rust demangler, and [IDA 9.4's demangled-names
documentation](https://docs.hex-rays.com/9.4/ida-actions/setdemanglednames) lists
Microsoft, Borland, Watcom, Visual Age and GNU. Both are from their documentation only.
Neither was run, and this page says nothing about their speed or their spellings.

## Measured

Run on 2026-10-24 under CPython 3.13.16 on Linux x86-64, on an Intel Xeon at 2.80 GHz with
four virtual CPUs. The virtual machine was shared with other jobs: the one-minute load
average read 3.1 when the run began and 3.3 when it ended. That makes every rate lower than
an idle machine would give, so read the ratios between rows rather than the rates. Each
figure is the best of 15 passes.

```console
pip install llvm-demangle-fxti cpp-demangle itanium-demangler
python benchmarks/compare.py --repeats 15 --nm /usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.33
```

The report, as the script printed it:

- 2026-10-24; CPython 3.13.16; Linux x86_64; Intel(R) Xeon(R) Processor @ 2.80GHz
- cold: every name distinct, `demangle`'s cache emptied before each pass; best of 15 passes; command-line tools run once over the whole list, starting the process included
- `Read`: output differs from input. `= X`: output identical to X's. `ref`: X is that row's own tool

### itanium-real-world.txt: 318 names, itanium

| Tool | us/name | names/s | Read | = llvm-cxxfilt | = c++filt |
|---|---:|---:|---:|---:|---:|
| demangle 0.6.0 (pure Python) | 53.8 | 18,600 | 318 | 318 | 176 |
| demangle 0.6.0, warm cache | 0.46 | 2,190,000 | 318 | 318 | 176 |
| demangle 0.6.0, style="gnu" | 56.2 | 17,800 | 318 | 182 | 310 |
| llvm-demangle-fxti 1.0.0 (LLVM 21, native) | 1.2 | 819,000 | 318 | 318 | 176 |
| cpp-demangle 0.1.3 (Rust, native) | 4.0 | 252,000 | 281 | 163 | 264 |
| itanium_demangler 1.1 (pure Python) | 46.5 | 21,500 | 234 | 109 | 92 |
| llvm-cxxfilt 18.1.3 (one process) | 48.8 | 20,500 | 318 | ref | 176 |
| GNU c++filt 2.42 (one process) | 9.7 | 103,000 | 310 | 176 | ref |

### itanium-libstdcxx.txt: 5,886 names, itanium

| Tool | us/name | names/s | Read | = llvm-cxxfilt | = c++filt |
|---|---:|---:|---:|---:|---:|
| demangle 0.6.0 (pure Python) | 46.8 | 21,400 | 5,886 | 5,886 | 2,927 |
| demangle 0.6.0, warm cache | 0.39 | 2,540,000 | 5,886 | 5,886 | 2,927 |
| demangle 0.6.0, style="gnu" | 48.9 | 20,400 | 5,886 | 2,927 | 5,886 |
| llvm-demangle-fxti 1.0.0 (LLVM 21, native) | 0.87 | 1,150,000 | 5,886 | 5,886 | 2,927 |
| cpp-demangle 0.1.3 (Rust, native) | 3.1 | 323,000 | 5,886 | 3,071 | 5,195 |
| itanium_demangler 1.1 (pure Python) | 48.2 | 20,700 | 5,817 | 2,718 | 1,460 |
| llvm-cxxfilt 18.1.3 (one process) | 5.3 | 188,000 | 5,886 | ref | 2,927 |
| GNU c++filt 2.42 (one process) | 2.7 | 376,000 | 5,886 | 2,927 | ref |

### msvc-boost.txt: 5,843 names, msvc

| Tool | us/name | names/s | Read | = llvm-demangle-fxti | = llvm-undname |
|---|---:|---:|---:|---:|---:|
| demangle 0.6.0 (pure Python) | 122.8 | 8,140 | 5,843 | 5,843 | 5,843 |
| demangle 0.6.0, warm cache | 0.54 | 1,840,000 | 5,843 | 5,843 | 5,843 |
| llvm-demangle-fxti 1.0.0 (LLVM 21, native) | 8.7 | 115,000 | 5,843 | ref | 5,843 |
| llvm-undname 18.1.3 (one process) | 26.3 | 38,100 | 5,843 | 5,843 | ref |

### rust-real-world.txt: 5,316 names, rust

| Tool | us/name | names/s | Read | = corpus |
|---|---:|---:|---:|---:|
| demangle 0.6.0 (pure Python) | 41.4 | 24,200 | 5,316 | 5,316 |
| demangle 0.6.0, warm cache | 0.43 | 2,340,000 | 5,316 | 5,316 |
| llvm-demangle-fxti 1.0.0 (LLVM 21, native) | 0.99 | 1,020,000 | 5,308 | 2,265 |
| llvm-demangle-fxti 1.0.0 (LLVM 21, native), rust_demangle() v0 only | 0.65 | 1,540,000 | 2,306 | 2,265 |

### swift-real-world.txt: 8,494 names, swift

| Tool | us/name | names/s | Read | = corpus |
|---|---:|---:|---:|---:|
| demangle 0.6.0 (pure Python) | 99.9 | 10,000 | 8,494 | 8,494 |
| demangle 0.6.0, warm cache | 0.49 | 2,050,000 | 8,494 | 8,494 |

### nm -D libstdc++.so.6.0.33: 5,963 names, itanium

| Tool | us/name | names/s | Read | = llvm-cxxfilt | = c++filt |
|---|---:|---:|---:|---:|---:|
| demangle 0.6.0 (pure Python) | 47.5 | 21,100 | 5,963 | 5,885 | 2,986 |
| demangle 0.6.0, warm cache | 0.41 | 2,460,000 | 5,963 | 5,885 | 2,986 |
| demangle 0.6.0, style="gnu" | 49.4 | 20,300 | 5,963 | 2,926 | 5,963 |
| llvm-demangle-fxti 1.0.0 (LLVM 21, native) | 0.88 | 1,140,000 | 5,888 | 5,960 | 2,929 |
| cpp-demangle 0.1.3 (Rust, native) | 3.1 | 327,000 | 5,963 | 3,070 | 5,272 |
| itanium_demangler 1.1 (pure Python) | 48.0 | 20,800 | 5,885 | 2,726 | 1,459 |
| llvm-cxxfilt 18.1.3 (one process) | 5.9 | 169,000 | 5,885 | ref | 2,926 |
| GNU c++filt 2.42 (one process) | 3.5 | 284,000 | 5,963 | 2,926 | ref |

How to read the tables:

- **us/name** is the cold cost: every name distinct, and `demangle`'s result cache emptied
  before each pass, so it is the parse and not a lookup. The other in-process tools keep no
  cache. The `warm cache` row is the same names again with the cache full.
- **Read** is the number of names whose output differs from the input, because a
  demangler hands back what it cannot read. It does not say the spelling is right.
- **= X** is the number of names whose output is identical, byte for byte, to X's. A tool
  that declines a name agrees with a reference that declines it. Where no tool is a reference
  for the scheme, `= corpus` is the spelling recorded in the corpus file, from the
  reference its header names: the `rustc-demangle` crate for Rust, `swift-demangle` built
  from the Swift compiler's `main` for Swift.
- A command-line row runs the tool once over the whole list, so it includes starting the
  process and piping the names in and out.
- The Itanium rows are scored against both `llvm-cxxfilt` and GNU `c++filt`, because the
  two spell some things differently; see the caveats.
- `llvm-demangle-fxti` appears twice on the Rust corpus: through `demangle()`, which picks
  the scheme from the name, and through `rust_demangle()`, its v0 reader alone.

## Where `demangle` is slower

Cold, on names it has not seen, `demangle` costs more per name than every native tool
measured. Each figure below is its cold `us/name` divided by the other tool's:

| Names | `llvm-demangle-fxti` | `cpp-demangle` | `itanium_demangler` |
|---|---:|---:|---:|
| Itanium, the libstdc++ exports (`--nm`) | 54x | 15x | 1.0x |
| Itanium, `itanium-libstdcxx.txt` | 54x | 15x | 1.0x |
| Itanium, `itanium-real-world.txt` | 45x | 13x | 1.2x |
| MSVC, `msvc-boost.txt` | 14x | | |
| Rust, `rust-real-world.txt` | 42x; 64x against `rust_demangle()` alone | | |

That is the cost of reading names in Python rather than in compiled code. Against the one
other pure-Python reader the two are level, on Itanium names only.

The command-line tools were run once over the whole list, so their figures include starting
the process. Over the libstdc++ exports GNU `c++filt` reads 14 times as many names a
second as `demangle` does cold, and `llvm-cxxfilt` 8.1 times; over the MSVC names
`llvm-undname` reads 4.7 times as many. On the 318-name corpus process start dominates instead:
`llvm-cxxfilt` takes about 16 ms in all, 48.8 us a name against `demangle`'s 53.8.

As a job: at the rates for the libstdc++ exports, 100,000 distinct names take `demangle`
about 4.8 s and `llvm-demangle-fxti` about 0.09 s.

What narrows the gap in practice is repetition. A symbol table repeats its names, and a
name read a second time costs 0.4 to 0.5 us here, a dictionary lookup. That is a property
of the cache and not of the parser: a cache can be put in front of any of the other tools,
and that was not measured.

## Where it leads

- **Breadth.** `demangle` reads fourteen schemes. Of the libraries compared, only it reads
  Swift, Objective-C, Go, Nim, Free Pascal, Delphi, Ada/GNAT, JNI and the pre-Itanium C++
  styles. (GNU `c++filt` has a GNAT style as well, and is not a library.) A separate loop over
  the checked-in corpora, which `compare.py` does not run, found `llvmdemangle.demangle()`
  changing none of the names in the Swift, Go, Nim, Objective-C, Free Pascal, Delphi, Ada,
  JNI, g++ 2.x and CodeWarrior corpora, and 256 of the 1,257 in the D one.
- **Rust, where the schemes overlap.** The readings still differ. In the Rust corpus 3,002 of
  the 5,316 names are legacy `_ZN...E` names and 2,314 are v0. `llvm-demangle-fxti` reads a
  legacy name as the Itanium name it also is, so the `$LT$` escapes and the `::h<hash>` stay
  in the output, and none of the 3,002 comes back as `rustc-demangle` spells it. Its Rust
  reader takes 2,306 of the 2,314 v0 names and spells 2,265 of them as `rustc-demangle`
  does. `demangle` matches `rustc-demangle` on all 5,316.
- **Conformance.** Scored against `llvm-cxxfilt`, `demangle` agrees on all 5,886 names of
  `itanium-libstdcxx.txt` and on 5,885 of the 5,963 the libstdc++ here exports. The
  other 78 are names that LLVM 18 leaves mangled and GNU `c++filt` reads: 69 transaction
  clones (`_ZGTt...`), six `_Float32x`-family types and three `std::bfloat16_t` typeinfo
  names. In GNU style it agrees with `c++filt` on every name of both sets. On the MSVC
  names it agrees with `llvm-undname` and with the LLVM binding on all 5,843. `cpp-demangle`
  agrees with `llvm-cxxfilt` on 3,070 of the libstdc++ names and `itanium_demangler` on
  2,726; the caveats show what sort of difference that is. [Conformance](CONFORMANCE.md) is the whole
  account, over about 2,500,000 live symbols.
- **A tree.** `parse()` returns nodes for all fourteen schemes, `signature()` the parts of a
  name, and `to_dict()` the tree as data. Of the others, `itanium_demangler` returns a tree
  for Itanium names, and LLVM's `ItaniumPartialDemangler` answers for the parts of an
  Itanium function name. The rest return strings.
- **Bounded input.** Recursion depth, output size, substitution count and input length are
  bounded and configurable per call, a bound ends the reading rather than guessing, and
  `demangle()` never raises. [SECURITY.md](SECURITY.md) has the threat model and how the
  bounds are tested. The other tools were not tried on hostile input here, so this page
  makes no claim about them.
- **One wheel.** A single `py3-none-any` wheel serves every platform, CPython and PyPy, and
  runs in a browser under Pyodide, which is how the [playground](playground.md) works. The two
  native packages publish CPython wheels for the platforms in the first table, and neither
  lists a PyPy or Pyodide wheel.
- **A cache that comes with it.** `demangle()` keeps a result cache bounded in bytes as well
  as entries, so a loop over a symbol table needs no wrapper. The warm rows show what it
  buys. It is a convenience and not a faster parser.

## What the numbers do not say

- **Exact-string agreement counts style.** `llvm-cxxfilt` and GNU `c++filt` themselves agree
  on only 2,926 of the 5,963 libstdc++ names, most of the difference being spacing,
  `> >` against `>>`. That is why there are two reference columns, and why `demangle` also
  appears with `style="gnu"`, which matches `c++filt` on every one. The differences are not
  all spacing. Four names, with what each tool prints where they differ:

  ```text
  _ZNSt6vectorIiSaIiEE9push_backERKi
    llvm-cxxfilt, LLVM binding, itanium_demangler, demangle:
      std::vector<int, std::allocator<int>>::push_back(int const&)
    c++filt, cpp-demangle, demangle with style="gnu":
      std::vector<int, std::allocator<int> >::push_back(int const&)

  _ZNSs6insertEmRKSs
    llvm-cxxfilt, LLVM binding, cpp-demangle, itanium_demangler, demangle:
      std::string::insert(unsigned long, std::string const&)
    c++filt, demangle with style="gnu":
      std::basic_string<char, std::char_traits<char>, std::allocator<char> >::insert(unsigned long, std::basic_string<char, std::char_traits<char>, std::allocator<char> > const&)

  _ZTVNSt8ios_base7failureE
    everyone but cpp-demangle:
      vtable for std::ios_base::failure
    cpp-demangle:
      {vtable(std::ios_base::failure)}

  _ZNKSt7codecvtIDsc11__mbstate_tE9do_lengthERS0_PKcS4_m
    everyone but itanium_demangler:
      std::codecvt<char16_t, char, __mbstate_t>::do_length(__mbstate_t&, char const*, char const*, unsigned long) const
    itanium_demangler:
      std::codecvt<char16_t, char, __mbstate_t>::do_length const(__mbstate_t&, char const*, char const*, unsigned long)
  ```

  `itanium_demangler` also writes constructors as `{ctor}` and does not read
  `_ZTIDF32_`, which every other tool reads as `typeinfo for _Float32`. A mismatch in the
  tables is a reason to look at the names, not a verdict.
- **The LLVM binding is a different LLVM from the command-line tools.** It defaults to
  LLVM 21; `llvm-cxxfilt` and `llvm-undname` here are LLVM 18. `llvm-demangle-fxti` ships
  LLVM 11 to 21, and `--llvm 18` reads with the release the command-line tools were built
  from. It then agrees with `llvm-cxxfilt` on all 5,963 libstdc++ names, where LLVM 21
  agrees on 5,960: LLVM 21 reads the three `std::bfloat16_t` names that 18 leaves
  mangled. The MSVC and Rust counts do not move. The speeds in the tables are LLVM 21's.
- **The corpora are this project's own conformance corpora.** `msvc-boost.txt` holds only
  names `llvm-undname` 18 reads, by its header, and every name of `itanium-libstdcxx.txt` is
  read by `llvm-cxxfilt`, so the references' `Read` counts there are not a finding. Another
  set of names would give other counts, and other speeds: MSVC names cost more to read than
  Itanium ones in every tool measured.
- **Read is not right.** It counts names that changed. On the Rust corpus the LLVM binding's
  `demangle()` changes 5,308 of 5,316 names and agrees with `rustc-demangle` on 2,265.
- **One machine, one Python, one run.** CPython 3.13 on a shared virtual machine. PyPy, other
  CPython versions, memory, import time and threads were not measured.
- **Ghidra and IDA were not measured.** Their entries above are from their documentation.
- **The author is one of the competitors.** The project that publishes `demangle` wrote the
  script, the page and the corpora. They are in the repository, and the report prints the
  versions it measured.

## Reproducing it

```console
pip install llvm-demangle-fxti cpp-demangle itanium-demangler
apt-get install llvm binutils        # llvm-cxxfilt, llvm-undname, c++filt, nm
python benchmarks/compare.py --nm /usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.33
python benchmarks/compare.py --llvm 18
```

None of the competitors is a dependency of `demangle` or of any of its dependency groups.
A tool that is not installed is skipped with a note at the top of the report, and the
rest are still measured. `--llvm N` selects one of the LLVM releases `llvm-demangle-fxti`
carries; `--limit N` uses the first N names of each corpus, for a quick look; `--repeats N`
sets the number of passes. The script is in the repository so that anyone can check it, and
rerun it on a machine and a Python of their own.
