# Sources for `tests/conformance/itanium-reference-defects.txt`

A corpus is normally the reference demangler's own output, recorded next to the name
(`tools/generate_corpus.py`). These names cannot be recorded that way, because on each
of them the reference's answer is not the right one. Most are a reference resolving a
`<template-param>` recorded in the substitution table to the argument bound to it where
the entry was made, rather than where the back-reference is read; the two differ
whenever the entry was made inside a local entity's enclosing signature. The rest are
other reference defects -- a mis-printed expression, a miscounted substitution
candidate -- and two places where the compilers themselves disagree with each other or
with the ABI. The table says which each source is.

So the expected column here is derived from the **declaration**, not from a demangler.
Each source below is compiled with the compiler named in the corpus, the emitted symbol
is read out of the object file with `nm`, and the spelling is what the source says that
symbol's type is. That is the only ground truth a demangler has.

This directory is deliberately *not* swept by `tools/generate_corpus.py`, which globs
`corpus_sources/*.cpp` and would record the reference's wrong answer.

| source | compiler | what each reference does |
| --- | --- | --- |
| `insort.cpp` | g++ 13.3.0 | llvm-cxxfilt 18 wrong; GNU c++filt 2.42 correct |
| `insort_composite.cpp` | g++ 13.3.0 | the same, for a composite built over the parameter |
| `generic_lambda.cpp` | g++ 13.3.0 | llvm-cxxfilt 18 wrong; GNU c++filt 2.42 correct |
| `member_template_lambda.cpp` | g++ 13.3.0 and clang++ 18.1.3, identically | the same defect the other way round: the closure's own `auto` written as the enclosing template's `T_` entry; llvm-cxxfilt 18 wrong, GNU c++filt 2.42 correct |
| `prepare_execution.cpp` | g++ 13.3.0 | both references wrong |
| `auto_marshall.cpp` | clang++ 18.1.3 | llvm-cxxfilt 18 wrong; GNU c++filt 2.42 refuses |
| `value_init_new.cpp` | g++ 13.3.0 and clang++ 18.1.3, identically | a different defect: llvm-cxxfilt 18 and 20 print `new T()` as `new T`, the expression that does not value-initialise; GNU c++filt 2.42 correct |
| `division.cpp` | g++ 13.3.0 and clang++ 18.1.3, identically | a printing defect: llvm-cxxfilt 18 and 20 give `/` the precedence of an assignment, so `(sizeof(T) + 1) / 2` prints as `sizeof (int) + 1 / 2`; GNU c++filt 2.42 correct |
| `template_template_param.cpp` | g++ 13.3.0 and clang++ 18.1.3, identically | a different defect, about the *count* of entries: `<template-template-param> <template-args>` is two candidates by 5.1.10 and llvm-cxxfilt 18 records only the second, so it refuses one of these names and answers `char<int>` for the other; GNU c++filt 2.42 correct |
| `inheriting_constructor.cpp` | g++ 13.3.0 and clang++ 18.1.3, each under its own rule | a compiler split rather than a demangler defect: g++ enters an inheriting constructor's `<base class type>` in the substitution table and clang does not, so each writes a numbering the other cannot read. llvm-cxxfilt 18 implements clang's and refuses g++'s; GNU c++filt 2.42 reads both parameter lists and names the constructor `D::C` |
| `closure_prefix.cpp` | g++ 13.3.0 under `-fabi-version=17`, GCC 12's default | a numbering defect in the *compiler*, which every demangler then reads by the ABI's rule: GCC 12 and earlier left the prefix before a lambda's `M` out of the substitution table, so every later `S<n>_` is one lower than the ABI says. llvm-cxxfilt 18, LLVM main and GNU c++filt 2.42 all print `ns::Box`, a template with no arguments, as a parameter type; see `ItaniumOptions.closure_prefix_substitution` |

Rebuild a symbol with, for example:

```console
g++ -std=c++17 -c insort.cpp -o /tmp/insort.o && nm /tmp/insort.o | grep insortI
```
