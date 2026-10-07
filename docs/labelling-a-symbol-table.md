# Labelling a symbol table

A disassembler, a debugger or a binary-similarity index meets symbol names one table
at a time: every export of a PE, every defined symbol of an ELF, every entry of a
Mach-O's string table. Most of those names are not mangled, a few belong to each of
several schemes, and the tool wants one readable label per address, the same label
whichever compiler built the binary. This page is that job end to end, with the calls
chosen for it and why.

## One call per name, and nothing raises

`demangle()` never raises over a name: what it cannot read comes back exactly as it
went in, so a loop over a table needs no guard.

```python
import demangle

labels = {address: demangle.demangle(name) for address, name in table}
```

`demangle_all()` is the same over an iterable, sharing one result cache; `demangleb()`
takes the bytes a string table actually holds, and hands undecodable bytes back
unchanged rather than raising.

## Say which schemes the table can hold

A PE built by MSVC holds MSVC names and, through MinGW or Rust, Itanium and Rust ones.
A Mach-O holds Itanium, Swift and Objective-C. Detection among every scheme is right
for a name of unknown origin, and wrong for a table whose origin is known: Objective-C
metadata should stay as the linker wrote it in a tool that labels it by other means,
and a Go-looking path should not be rewritten in a Windows binary. `language=` with a
sequence of names detects among those schemes alone, in the usual order:

```python
>>> demangle.demangle("_OBJC_CLASS_$_NSData", language=("itanium", "swift"))
'_OBJC_CLASS_$_NSData'
>>> list(demangle.demangle_all(["_Z3foov", "CreateFileW", "?bar@@YAXXZ"], language=("itanium", "msvc")))
['foo()', 'CreateFileW', 'void __cdecl bar(void)']
```

A single string still forces one scheme, which is what to pass when the format itself
says which it is -- a PDB record, say.

## Evidence of a language

A tool that scores a binary's language wants to know which scheme *read* a name, not
which one it resembled. `detect()` is the cheap claim, made on a prefix so that it can
run over every symbol; `strict=True` is the answer `demangle()` would act on, at the
cost of a parse:

```python
>>> demangle.detect("_ZN3Foo"), demangle.detect("_ZN3Foo", strict=True)
('itanium', None)
>>> demangle.detect("?bar@@YAXXZ", language=("itanium", "swift")) is None
True
```

A legacy Rust name, `_ZN3std2rt10lang_start17h0123456789abcdefE`, is claimed by `rust`
and not by `itanium`, because the hash is evidence the C++ scheme does not have; a tool
that counts Rust symbols can count `detect(name) == "rust"`.

## A label that survives the compiler

The spelling differs between compilers for the same function: MSVC writes
`int const &` and the access specifier and calling convention, GCC writes `int const&`
and neither, and a template argument carries `class` on one side only. The
*qualified name* does not differ, and it is the label an index wants to match on across
builds:

```python
>>> plain = demangle.style("llvm", msvc={"tag_kind": False})
>>> msvc = demangle.signature("?push_back@?$vector@HV?$allocator@H@std@@@std@@QEAAXAEBH@Z", style=plain)
>>> gcc = demangle.signature("_ZNSt6vectorIiSaIiEE9push_backERKi")
>>> msvc.qualified_name == gcc.qualified_name
True
>>> gcc.qualified_name, gcc.namespace, gcc.base_name
('std::vector<int, std::allocator<int>>::push_back', 'std::vector<int, std::allocator<int>>', 'push_back')
```

The same holds for Rust's two manglings of one function, legacy and v0, whose
`qualified_name` is `std::rt::lang_start` either way, and for Swift, where
`HLoader.main` is the name and `() -> ()` the signature. A full declaration can run to
thousands of characters for a templated C++ function; `qualified_name` stays a name,
and `base_name` alone is the shortest label that is still the function's own.

`special` is what a symbol is when it is not a function or a variable -- `vtable for`,
`typeinfo name for`, `import thunk for`, `Objective-C class` -- with the entity it
belongs to in the name fields, so a tool can label `_ZTV3Foo` as the vtable of `Foo`
rather than as a function called `vtable for Foo`.

## Start-up cost, in a service

Each scheme is imported the first time a name reaches it, about 7 ms for MSVC, which
a script reading one file never notices and a service answering its first request
does. `preload()` moves the cost to start-up:

```python
demangle.preload()                      # every scheme
demangle.preload("itanium", "msvc")     # the ones the service will meet
```

## Hostile tables

A symbol table is untrusted input: a few dozen bytes can describe a type nested deep
enough to exhaust the stack, or a spelling larger than memory. Every call takes
`limits=`; the defaults (`DEFAULT_LIMITS`) are above anything a compiler emits and
below what would hurt, and are what to keep for a table from an unknown binary.
`RELAXED_LIMITS` is for input you trust. [Security policy](SECURITY.md) has the
measured worst cases.
