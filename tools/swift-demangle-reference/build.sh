#!/bin/sh
# Build the Swift reference demangler: swiftlang/swift's own lib/Demangling, at a pinned
# revision, behind the line-per-name front end in main.cpp. See README.md for why the
# revision is what it is.
#
#     tools/swift-demangle-reference/build.sh
#
# Needs a C++17 compiler, LLVM's headers (llvm-config on PATH, or LLVM_INCLUDEDIR set),
# and one fetch from github.com. Nothing else: no Swift toolchain, no CMake, no LLVM
# libraries -- lib/Demangling includes LLVM headers but links against none of it.
set -eu

# swiftlang/swift main as of 2026-08-30. Not a release tag on purpose: every release
# through 6.3.3 refuses names the corpora contain, because upstream added them to
# test/Demangle/Inputs/manglings.txt after 6.3.3 branched. README.md has the evidence.
REVISION=871a239941f3613c178b91c294f8584345f30f73

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source=$here/swift
out=$here/build

if [ "${LLVM_INCLUDEDIR-}" = "" ]; then
    for config in llvm-config llvm-config-20 llvm-config-19 llvm-config-18 llvm-config-17 llvm-config-16; do
        if command -v "$config" >/dev/null 2>&1; then
            LLVM_INCLUDEDIR=$("$config" --includedir)
            break
        fi
    done
fi
if [ "${LLVM_INCLUDEDIR-}" = "" ] || [ ! -f "$LLVM_INCLUDEDIR/llvm/ADT/StringRef.h" ]; then
    echo "no LLVM headers: install llvm-dev (or set LLVM_INCLUDEDIR)" >&2
    exit 1
fi

# A blobless sparse checkout of the five directories the demangler needs: about 16 MB,
# against a full clone of swiftlang/swift.
if [ ! -d "$source/.git" ]; then
    mkdir -p "$source"
    git -C "$source" init -q
    git -C "$source" remote add origin https://github.com/swiftlang/swift
    # Cone mode on purpose: it also brings in the loose files in each parent directory,
    # and include/swift/Strings.h -- which Demangle.h includes -- is one of those.
    git -C "$source" sparse-checkout init --cone
    git -C "$source" sparse-checkout set \
        include/swift/ABI include/swift/AST include/swift/Basic \
        include/swift/Demangling lib/Demangling tools/swift-demangle
fi
if [ "$(git -C "$source" rev-parse HEAD 2>/dev/null || true)" != "$REVISION" ]; then
    git -C "$source" fetch -q --depth 1 --filter=blob:none origin "$REVISION"
    git -C "$source" checkout -q "$REVISION"
fi

# The three definitions are `swift_demangling_compile_flags` from
# lib/Demangling/CMakeLists.txt, verbatim. Without the first, linking wants
# llvm::DisableABIBreakingChecks; without the second, an old `_Tt...` name comes back
# unread; without the third, nodeToString is not compiled in at all.
flags="-std=c++17 -fno-exceptions -O2 -w
    -DLLVM_DISABLE_ABI_BREAKING_CHECKS_ENFORCING=1
    -DSWIFT_SUPPORT_OLD_MANGLING=1
    -DSWIFT_STDLIB_HAS_TYPE_PRINTING=1
    -I$source/include -I$LLVM_INCLUDEDIR"

: "${CXX:=c++}"
mkdir -p "$out"
objects=""
for file in "$source"/lib/Demangling/*.cpp "$here/main.cpp"; do
    object=$out/$(basename "${file%.cpp}").o
    # shellcheck disable=SC2086
    $CXX $flags -c "$file" -o "$object"
    objects="$objects $object"
done
# shellcheck disable=SC2086
$CXX $objects -o "$out/swift-demangle-reference"
echo "$out/swift-demangle-reference"
