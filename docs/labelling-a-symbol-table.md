# Labelling a symbol table

A disassembler, a debugger or a binary-similarity index meets names a table at a time:
the exports of a PE, the defined symbols of an ELF, a Mach-O's string table. Most of
those names aren't mangled, a few belong to each of several schemes, and the tool
wants one readable label per address, the same one whichever compiler built the
binary. This page is that job, and the calls that fit it.

## One call per name

`demangle()` hands back what it can't read, so the loop needs no guard:

```python
import demangle

labels = {address: demangle.demangle(name) for address, name in table}
```

`demangle_all()` does the same over an iterable, and `demangleb()` takes the bytes a
string table really holds. [Using the library](usage.md#bytes) covers both.

## Say which schemes the table can hold

A PE built by MSVC holds MSVC names and, through MinGW or Rust, Itanium and Rust ones.
A Mach-O holds Itanium, Swift and Objective-C. Detecting among every scheme is right
for a name you know nothing about and wrong for a table you do: a Go-looking path
shouldn't be rewritten in a Windows binary, and Objective-C metadata should stay as
the linker wrote it if your tool labels that some other way.

Pass `language=` a sequence and detection runs among those schemes only, in the usual
order:

```python
>>> demangle.demangle("_OBJC_CLASS_$_NSData", language=("itanium", "swift"))
'_OBJC_CLASS_$_NSData'
>>> list(demangle.demangle_all(["_Z3foov", "CreateFileW", "?bar@@YAXXZ"], language=("itanium", "msvc")))
['foo()', 'CreateFileW', 'void __cdecl bar(void)']
```

A single string still forces one scheme. Use that when the format itself says which
it is, a PDB record for instance.

## Which language is this binary?

A tool that scores a binary's language wants to know which scheme read a name, not
which one it looked like. `detect()` is the cheap answer, made on a prefix so it can
run over every symbol. `strict=True` is the answer `demangle()` would act on, and
costs a parse:

```python
>>> demangle.detect("_ZN3Foo"), demangle.detect("_ZN3Foo", strict=True)
('itanium', None)
>>> demangle.detect("?bar@@YAXXZ", language=("itanium", "swift")) is None
True
```

A legacy Rust name such as `_ZN3std2rt10lang_start17h0123456789abcdefE` is claimed by
`rust` and not `itanium`, because of the hash on the end. So counting Rust symbols is
counting `detect(name) == "rust"`.

## A label that survives the compiler

Compilers spell the same function differently. MSVC writes `int const &`, the access
specifier and the calling convention. GCC writes `int const&` and neither. The
qualified name doesn't change, which makes it the label to match on across builds:

```python
>>> plain = demangle.style("llvm", msvc={"tag_kind": False})
>>> msvc = demangle.signature("?push_back@?$vector@HV?$allocator@H@std@@@std@@QEAAXAEBH@Z", style=plain)
>>> gcc = demangle.signature("_ZNSt6vectorIiSaIiEE9push_backERKi")
>>> msvc.qualified_name == gcc.qualified_name
True
>>> gcc.qualified_name, gcc.namespace, gcc.base_name
('std::vector<int, std::allocator<int>>::push_back', 'std::vector<int, std::allocator<int>>', 'push_back')
```

Same for Rust's two manglings of one function, legacy and v0: `qualified_name` is
`std::rt::lang_start` either way. In Swift, `HLoader.main` is the name and `() -> ()`
the signature. A full declaration of a templated C++ function can run to thousands of
characters, while `qualified_name` stays a name and `base_name` is the shortest label
that's still the function's own.

`special` is set when a symbol isn't a function or a variable: `vtable for`,
`typeinfo name for`, `import thunk for`, `Objective-C class`. The thing it belongs to
is in the name fields, so you can label `_ZTV3Foo` as the vtable of `Foo` and not as a
function called `vtable for Foo`.

## Services and hostile tables

Two things from [Using the library](usage.md) matter more here than in a script. A
service should call [`preload()`](usage.md#start-up) at start-up so its first request
doesn't pay for importing a scheme. And a symbol table is untrusted input, so keep the
default [`limits`](usage.md#errors-and-limits) for any table from a binary you don't
know.
