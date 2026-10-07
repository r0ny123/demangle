# The Swift reference

`src/demangle/schemes/swift/` is a port of `lib/Demangling/` from swiftlang/swift, and
without this it has no oracle: nothing a distribution ships demangles Swift.
`llvm-cxxfilt` and `c++filt` both decline a `$s` name outright, so the corpora are the
only evidence unless there is a way to re-derive them and to put a *new* name to the
reference.

This builds that reference out of Swift's own demangler -- the eleven files in
`lib/Demangling/`, unmodified -- behind a line-per-name front end, so the differential
tools can ask it the way they ask `llvm-cxxfilt`: one name in, one spelling out, a name
it cannot read handed back unchanged.

    tools/swift-demangle-reference/build.sh
    tools/swift-demangle-reference/build/swift-demangle-reference [--simplified] < names

Needs a C++17 compiler, LLVM's headers (`llvm-dev`) and one fetch from github.com -- no
Swift toolchain, no CMake and no LLVM libraries. It is not built by default and is not a
dependency of the test suite; `tools/enumerate.py` and `tools/mutate.py` run their Swift
job when it is there and skip it when it is not. `swift/` and `build/` are ignored, and
the checkout is a blobless cone-mode sparse one over six directories -- about 16 MB,
against a full clone of swiftlang/swift.

## Why a commit and not a release tag

The pinned revision is `8305a5023a3174493ab8cae462452e2d6baa6a9a`, swiftlang/swift
`main` as of 2026-10-07. That is deliberate, and the alternative was measured rather
than guessed. `tests/conformance/swift-upstream.txt` is a transcription of
`test/Demangle/Inputs/manglings.txt`, and every shipped release refuses part of it:

| revision built | swift-upstream | swift-simplified | swift-refusals | swift-real-world |
|---|---|---|---|---|
| `swift-5.10.1-RELEASE` | 456 / 531 | 215 / 217 | 70 / 71 | 8493 / 8494 |
| `swift-6.4.0-RELEASE` | 513 / 531 | 217 / 217 | 70 / 71 | 8493 / 8494 |
| `main` @ `8305a502` | **531 / 531** | **217 / 217** | **71 / 71** | 8493 / 8494 |

One miss in each release is not a miss but an abort: `$sTJSdSSSpSrSUSP` takes the
printer of both 5.10.1 and 6.4.0 down with `std::bad_alloc`, so scoring them needs one
process per name. That name is a malformed autodiff subset-parameters thunk, and the fix
upstream made for it -- a `getNumChildren() < 5` guard in `NodePrinter` -- is the reason
the row exists in `manglings.txt` at all. This library refuses it, as `main` does.

5.10.1's other 74 misses and 6.4.0's other 17 are not defects in them. They are names
for constructs that did not exist yet -- `sending`, typed `throws`, `@isolated(any)`,
`~Copyable`, `Builtin.ImplicitActor`, `nonisolated(nonsending)`,
`yielding_borrow`/`yielding_mutate`, `@called(exactlyOnce)` and `@called(atMostOnce)`,
`@convention(com_method)`, the `async_Main` entry point -- which upstream added to
`manglings.txt` afterwards. Counting the rows in that file at each tag says the same
thing: 446 at 5.10.1, 470 at 6.0.3, 481 at 6.1.3, 495 at 6.2.4, 500 at 6.3.3, 512 at
6.4.0, and 531 on `main`. The corpus needs all 531, so no tag reaches it.

The corpora were recorded with a `main` build, not with `swift-demangle` 5.10.1, which
spells `$s4main3fooyySiFyyXEfU_TA.1` as

    closure #1 () -> () in main.foo(Swift.Int) -> ()partial apply forwarder with unmangled suffix ".1"

-- exactly what its own `manglings.txt` at that tag expects. The corpus carries the
later spelling, `partial apply forwarder for closure #1 ...`.

## The one row this reference does not match

`tests/conformance/swift-real-world.txt` has one name `main` refuses and this library
reads:

    $sSUss17FixedWidthIntegerRzrlEyxqd__cSzRd__lufCSu_SiTgm5

That is a real symbol out of the 5.10.1 runtime. `Tg` is a generic specialisation and
the `m` after it is `MetatypeParamsRemoved`, which 5.10.1's `demangleSpecAttributes`
reads and current `main` does not -- upstream deleted the flag. Reading it is the right
answer for a demangler pointed at binaries in the wild, which still hold names 5.10.1
emitted, so the row stays and this is version skew rather than a defect on either side.

## What the front end does, and does not

`main.cpp` mirrors `tools/swift-demangle/swift-demangle.cpp` for one name: its options
(`SynthesizeSugarOnTypes` on, unless `--simplified` replaces the whole set), its
one-underscore strip for a name beginning `__`, `Context::demangleSymbolAsNode`,
`nodeToString`, and its fallback of printing the name back when the spelling is empty.

Getting the sugar right was not cosmetic: with `SynthesizeSugarOnTypes` off, `_TtGSaSS_`
comes out as `Swift.Array<Swift.String>` rather than `[Swift.String]`, and the reference
scores 5504 of 8494 against a corpus that is otherwise a match.

What it does *not* mirror is the tool's stdin mode, which scans each line for
maybe-mangled *substrings* and substitutes them in place. That is a filter over text,
not a demangler over names, and here one line is one name -- which is how the corpora
are scored, and the only shape the differential tools can compare.

## Build flags

The three `-D`s in `build.sh` are `swift_demangling_compile_flags` from
`lib/Demangling/CMakeLists.txt`, verbatim, and each one is load-bearing:

- `LLVM_DISABLE_ABI_BREAKING_CHECKS_ENFORCING=1` -- without it the link wants
  `llvm::DisableABIBreakingChecks`, which lives in libLLVMSupport.
- `SWIFT_SUPPORT_OLD_MANGLING=1` -- without it `OldDemangler.cpp` compiles to nothing
  and every `_Tt`/`_T` name comes back unread. 203 of the 8,494 names in
  `swift-real-world.txt` are in the old mangling (`_T` followed by anything but `0`);
  `_T0` is the Swift 4 mangling, which the current demangler reads.
- `SWIFT_STDLIB_HAS_TYPE_PRINTING=1` -- without it `NodePrinter.cpp` compiles to
  nothing and there is no `nodeToString` to link against.

LLVM's headers are needed (`Punycode.h` includes `llvm/ADT/StringRef.h`, `LLVM.h`
includes `llvm/Support/Casting.h`) but none of its libraries are. Built against
LLVM 18's headers here; anything from 16 up should do, since `main` uses
`std::optional` rather than the `llvm::Optional` that LLVM 16 removed. The 5.10.1 row
was measured against LLVM 15's headers for that reason; 6.4.0 builds against 18's.
