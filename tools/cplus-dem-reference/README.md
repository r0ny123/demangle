# The pre-Itanium C++ reference

`src/demangle/schemes/gnuv2/` is a port of libiberty's `cplus-dem.c`: the demangler for
g++ before 3.0, Lucid's `lcc`, the ARM/cfront encoding, HP aCC and EDG, and without this
it has no oracle on any current machine. binutils 2.42's `c++filt` offers
`-s {none,auto,gnu-v3,java,gnat,dlang,rust}` and nothing older, and GCC 9 removed the
pre-Itanium demangler from libiberty altogether -- `cplus-dem.c` is 5,032 lines at 8.3.0
and 490 at 9.1.0 -- so every claim about this scheme rested on a corpus recorded once,
with no way to put a *new* name to the implementation it was recorded from.

This builds that implementation out of GCC 8.3.0's own tree -- `cplus-dem.c` and the
five helpers it calls, unmodified -- behind a line-per-name front end, so the
differential tools can ask it the way they ask `llvm-cxxfilt`: one name in, one spelling
out, a name it cannot read handed back unchanged.

    tools/cplus-dem-reference/build.sh
    tools/cplus-dem-reference/build/cplus-dem-reference [gnu|lucid|arm|hp|edg|auto] \
        [--no-params] [--no-ansi] [--verbose] < names

Needs a C compiler, `curl` and `sha256sum`, and one fetch from github.com. It is not
built by default and is not a dependency of the test suite; `tools/enumerate.py` and
`tools/mutate.py` run their `gnuv2` job when it is there and skip it when it is not.
`gcc/` and `build/` are ignored. Every fetched file is pinned by content as well as by
tag: `SHA256SUMS` was recorded from `releases/gcc-8.3.0` and the build refuses anything
that comes back different.

## Why 8.3.0

`tests/conformance/gnuv2-libiberty.txt` is a transcription of that tree's own
`libiberty/testsuite/demangle-expected`, so this is the implementation those 1,324
expectations came from. Checked rather than assumed: run over the corpus in each of its
four styles, under `DMGL_PARAMS` and without it, the build reproduces all 662 rows in
both columns -- 1,324 of 1,324. GCC 8.5.0, the last release of that series, carries the
same file: over 100,000 mutants of the corpus the two builds answer identically.

## What the front end does

`main.c` is what `c++filt --format=<style>` did to one name before binutils dropped the
styles: `cplus_demangle_set_style` from the name given, then `cplus_demangle` under
`DMGL_PARAMS | DMGL_ANSI`, the defaults `c++filt` applied and the settings the corpus's
third column records. `--no-params` is its fourth column; `--no-ansi` and `--verbose`
are the two flags `c++filt` exposed beside it. `gnu` is the default, as it is the
default of the scheme this is the oracle for.

## What it found, and what it reads that this does not

Over the 168,420 four-character argument lists `tools/enumerate.py --scheme gnuv2`
offers, the constructor, template-function and name-boundary shapes beside them -- 5.5
million strings at five characters, of which 667,000 read -- and 420,000 mutants of the
corpus over five seeds, this library never reads a name libiberty refuses. Every
divergence is in the other direction, and of one kind: libiberty reads what it is given.
A type code it does not know, a template argument list with nothing in it, a scope with
no name, an `operator` with no symbol -- each is spelled as the empty string, with the
punctuation printed round the gap -- and whatever follows a finished argument list is
taken for the start of another and printed straight after it:

    __ct__3Foo2__C          Foo::__ct(__,  const)
    foo__H1Z0__c            char foo<>(void)
    put__Q2___3foo          ::foo::put(void)
    __op__3foo              foo::operator (void)
    foo__Fex                foo(...)(long long)

And in `demangle_signature`'s `case 'H'` it steps over the character after a template's
arguments without checking that it is the `_` the grammar puts there, so
`foo__H1Zit__3iosFP9streambuf` -- a `t` where nothing goes -- is `ios foo<int>(streambuf
*)` to it. This library refuses each of those guesses and, running the same
`iterate_demangle_function`, reads the name at the next `__` instead:
`ios::foo__H1Zit(streambuf *)`. Neither is a name a compiler wrote. `ACCEPTED["gnuv2"]`
in `tools/enumerate.py` recognises the three shapes -- the gap, the second argument
list, and the reference's function name being a proper prefix of ours up to a `__` --
each checked against every recorded spelling in the corpus, which none of them matches,
so that what the tools report is what is left.

What was left was one defect of this library's own, fixed. `gnu_special` advances the
cursor as it reads a virtual table's class, and on a class it cannot read it returned to
the caller with the cursor past it, so `demangle_prefix` read the *tail* of the name as
a function: `_vt$t3Foo1Z_bar__Fi` came back `_bar(int)`. libiberty does the same --
`_vt$t8BDDHookV1__pt__2_cFv` is `_c::_pt(void)` to it -- and a function named after the
end of a virtual table's symbol is not a reading of that symbol. A `_vt`, `__vt_`,
`__thunk_`, `__ti` or `__tf` prefix says what the name is, so a body that does not read
as that now refuses the name. The corpus is untouched by the rule.
