#!/bin/sh
# Build the pre-Itanium C++ reference (libiberty's `cplus-dem.c` from GCC 8.3.0) behind
# main.c. See README.md for why that release and what the build was checked against.
set -eu

TAG=releases/gcc-8.3.0
MIRROR=https://raw.githubusercontent.com/gcc-mirror/gcc

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source=$here/gcc
out=$here/build

mkdir -p "$source/include" "$source/libiberty"
while read -r sum file; do
    if [ ! -f "$source/$file" ]; then
        curl -sSfL --retry 3 "$MIRROR/$TAG/$file" -o "$source/$file"
    fi
done < "$here/SHA256SUMS"
(cd "$source" && sha256sum --quiet -c "$here/SHA256SUMS")

# What libiberty's configure would have found on any hosted system; cplus-dem.c and its
# helpers need nothing more than these.
mkdir -p "$out"
cat > "$out/config.h" <<'CONFIG'
#define HAVE_STRING_H 1
#define HAVE_STDLIB_H 1
#define HAVE_LIMITS_H 1
#define HAVE_STRINGS_H 1
#define HAVE_UNISTD_H 1
#define HAVE_SYS_TYPES_H 1
#define HAVE_INTTYPES_H 1
#define HAVE_STDINT_H 1
#define HAVE_ALLOCA_H 1
#define HAVE_DECL_BASENAME 1
CONFIG

: "${CC:=cc}"
# shellcheck disable=SC2086
$CC -O2 -w -DHAVE_CONFIG_H -I"$out" -I"$source/include" -o "$out/cplus-dem-reference" \
    "$here/main.c" \
    "$source/libiberty/cplus-dem.c" "$source/libiberty/cp-demangle.c" "$source/libiberty/cp-demint.c" \
    "$source/libiberty/d-demangle.c" "$source/libiberty/rust-demangle.c" "$source/libiberty/safe-ctype.c" \
    "$source/libiberty/xmalloc.c" "$source/libiberty/xstrdup.c" "$source/libiberty/xmemdup.c" \
    "$source/libiberty/xexit.c" "$source/libiberty/xstrerror.c"
echo "$out/cplus-dem-reference"
