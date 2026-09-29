# Working with the tree

`demangle()` gives you a string. `parse()` gives you the thing the string was rendered
from, and the difference starts to matter as soon as the question is about *structure*
rather than about text.

This page works one real task end to end, against a library you already have, and every
number in it was measured rather than estimated.

## The task

Given the symbols `libstdc++.so.6` exports, find every function that takes a
**`std::basic_string` by const reference**.

That is easy to get nearly right and awkward to get right. `basic_string` appears inside
other types, inside template arguments, inside return types. A `const&` in the spelling
may belong to a different parameter. And the element types contain `<`, `>` and `,` of
their own, so counting brackets does not give you the argument list.

## Reading and labelling the symbols

```python
import subprocess


def symbols(library):
    output = subprocess.run(["nm", "-D", "--defined-only", library], capture_output=True, text=True).stdout
    return [line.split()[-1] for line in output.splitlines() if line.strip()]
```

For a report, the string form is what you want, and `demangle()` is built for this shape
of use: most symbols in a real binary are not mangled at all, and one it cannot read
comes back unchanged rather than raising.

```python
import demangle

for name in symbols("/usr/lib/x86_64-linux-gnu/libstdc++.so.6"):
    print(demangle.demangle(name))
```

`demangle_all()` is the same over an iterable, sharing one cache — worth using on a whole
table, because symbol tables repeat themselves relentlessly.

## Asking the structural question

`parse()` returns a `Node`. Every node supports `walk()`, `find(kind)`, `children()` and
`spell()`.

```python
def template_taken_by_const_reference(parameter):
    """The template a parameter is a const reference to, or None."""
    if parameter.kind != "reference":
        return None
    inner = parameter.children()[0]
    if inner.kind != "qualify" or "const" not in inner.qualifiers:
        return None
    referent = inner.children()[0]
    # A scoped name arrives as `qualified`, and the template is its last component:
    # `std::__cxx11::basic_string<...>` is a `qualified` of `std`, `__cxx11` and the
    # template, not a template with a long name.
    if referent.kind == "qualified":
        referent = referent.children()[-1]
    return referent if referent.kind == "template" else None
```

Each test is a question about the node, not about characters — and that `qualified` step
is the sort of thing you only find by looking at a real tree.

```python
import collections
import demangle

element_types = collections.Counter()

for name in symbols("/usr/lib/x86_64-linux-gnu/libstdc++.so.6"):
    try:
        tree = demangle.parse(name)
    except demangle.DemanglingError:
        continue  # not a C++ symbol, or not one we can read

    for function in tree.find("function"):
        for parameter in function.parameters:
            template = template_taken_by_const_reference(parameter)
            if template is not None and template.base.spell() in ("basic_string", "std::basic_string"):
                element_types[template.arguments[0].spell()] += 1
```

On the shipped libstdc++, `element_types` comes out as **170** `char` and **112**
`wchar_t`: 282 parameters, in 278 functions. The name is compared whole rather than
searched for, because `"basic_string" in ...` is a question about characters again, and
would take the four `basic_stringbuf<...>::__xfer_bufptrs` constructors with it.

## What the regular expression gets wrong

The obvious approximation is to search the demangled string:

```python
import re

PATTERN = re.compile(r"basic_string<[^)]*const&")
```

Measured on the same library against the same question:

| | structural | regular expression |
|---|---|---|
| functions found | 278 | 384 |
| false positives | — | **106** |

The false positives are mostly `basic_string`'s own constructors, where the `const&`
belongs to a different parameter entirely:
`basic_string(char const*, unsigned long, std::allocator<char> const&)` matches, because
nothing in the pattern knows where one parameter ends and the next begins. Making it know
means matching brackets, and the element types carry `<`, `>` and `,` of their own.

That is the whole failure: C++ declaration syntax nests, and nesting is what regular
expressions cannot parse. The nesting is not incidental — it is how the type system is
written down.

## Other things the tree answers

```python
tree = demangle.parse("_ZNK3Foo3barIiEEvPKc")

tree.spell()  # 'void Foo::bar<int>(char const*) const'
[node.text for node in tree.find("name")]  # ['Foo', 'bar']
next(tree.find("function")).parameters  # the parameter list, as nodes
next(tree.find("template")).arguments  # the template arguments, as nodes
```

Node kinds are shared across schemes where they mean the same thing, so `find("name")`
works on a Rust or Go tree too:

```python
rust = demangle.parse("_ZN4core3fmt9Formatter3pad17h9b2b3a0e5b4d1b31E")
[node.text for node in rust.find("name")]  # ['core', 'fmt', 'Formatter', 'pad']

go = demangle.parse("example.com/m/v2%2e5.(*T).Method", language="go")
next(go.find("path")).text  # 'example.com/m/v2.5'
next(go.find("receiver")).pointer  # True
```

That last one is worth dwelling on. `example.com/m/v2%2e5.(*T).Method` cannot be split
into a package and a name by looking for a `.`: the package path contains one of its own,
written `%2e` precisely because it would otherwise be ambiguous. Reading it correctly
means decoding it, and the tree hands it over already decoded.

## How the schemes differ

Every scheme returns a tree, but the kinds differ with what each language has to say.

- **C++ trees carry declarator shape** — pointers, references, parameter lists, return
  types — because a C++ type wraps the name it declares. `int (*)(char)` is a pointer to
  a function, and the tree says so rather than leaving you to read it out of the
  brackets.
- **Rust and Go trees carry path structure** — `symbol`, `path`, `impl`, `namespace`,
  `receiver` — because neither language has declarator syntax, and what a caller wants
  from one of their symbols is which crate or package it belongs to, whether it is a
  method, and on what.
- **`name`, `template` and `literal`** mean the same thing everywhere, so a tool walking
  a mixed binary does not need to know which language produced a tree to ask for its
  identifiers.

See [Architecture](ARCHITECTURE.md) for why the trees are built the way they are, and
[Adding a scheme](adding-a-scheme.md) to add one of your own.
