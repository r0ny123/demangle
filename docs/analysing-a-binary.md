# Working with the tree

`demangle()` gives you a string. `parse()` gives you the tree that string is rendered
from, which is what you want once the question is about structure instead of text.
This page takes one such question against a library you already have. The numbers are
measured, and a test reruns the example.

## The task

Find every function `libstdc++.so.6` exports that takes a `std::basic_string` by const
reference.

It's easy to get nearly right. `basic_string` shows up inside other types, template
arguments and return types. A `const&` in the spelling may belong to a different
parameter. And the element types have `<`, `>` and `,` of their own, so counting
brackets won't find you the argument list.

## Reading the symbols

```python
import subprocess


def symbols(library):
    output = subprocess.run(["nm", "-D", "--defined-only", library], capture_output=True, text=True).stdout
    return [line.split()[-1] for line in output.splitlines() if line.strip()]
```

## Asking the question

`parse()` returns a `Node`. Every node has `walk()`, `find(kind)`, `children()` and
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

Each test asks about a node, not about characters. The `qualified` step is the kind of
thing you only find by printing a real tree.

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

On libstdc++ 6.0.33, `element_types` comes out as 170 `char` and
112 `wchar_t`: 282 parameters, in 278 functions. The name is compared whole on purpose.
`"basic_string" in ...` would also match the four
`basic_stringbuf<...>::__xfer_bufptrs` constructors.

## What a regular expression gets wrong

The obvious shortcut is to search the demangled string:

```python
import re

PATTERN = re.compile(r"basic_string<[^)]*const&")
```

On the same library, for the same question:

| | Tree | Regular expression |
|---|---|---|
| Functions found | 278 | 384 |
| False positives | — | 106 |

Most of the false positives are `basic_string`'s own constructors, where the `const&`
belongs to another parameter:
`basic_string(char const*, unsigned long, std::allocator<char> const&)` matches because
the pattern has no idea where one parameter ends and the next begins. Teaching it that
means matching nested brackets, which a regular expression can't do.

## Other things the tree answers

```python
tree = demangle.parse("_ZNK3Foo3barIiEEvPKc")

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

The Go one is a good example of why. You can't split
`example.com/m/v2%2e5.(*T).Method` into a package and a name by looking for a `.`,
because the package path has one of its own, written `%2e` so it isn't ambiguous. The
tree hands it over already decoded.

## How the schemes differ

Every scheme returns a tree, but the kinds depend on what the language has to say.

- C++ trees carry declarator shape: pointers, references, parameter lists, return
  types. `int (*)(char)` is a pointer to a function, and the tree says so.
- Rust and Go trees carry path structure: `symbol`, `path`, `impl`, `namespace`,
  `receiver`. Neither language has declarators, and what you usually want from their
  symbols is the crate or package, and whether it's a method and on what.
- `name`, `template` and `literal` mean the same thing everywhere, so a tool walking a
  mixed binary can ask for identifiers without knowing which language made the tree.

[Architecture](ARCHITECTURE.md) explains why the trees are built this way.
