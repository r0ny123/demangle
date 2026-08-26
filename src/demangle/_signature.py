"""The parts of a symbol, in one shape, for every scheme.

`demangle()` answers "what does this name say"; this answers "what are its pieces". A
disassembler labelling a call site wants the base name without its namespace; a
cross-reference wants the namespace without the base name; a signature matcher wants the
parameter types and nothing else. All three can be had by splitting the spelling, and
all three get it wrong the same way, because `::` and `,` and `(` occur inside template
arguments and inside function types as well as between the parts a caller means.

So the split is done on the tree the parser already built. Two shapes of tree, because
the schemes come in two kinds:

* C++, both manglings, build a declaration -- a function node with its name inside its
  own type, or a declarator standing beside one. The fields are read off it directly.
* Swift, D, Go, Nim, Free Pascal and Delphi build a node that *is* its own fragments, in
  output order: `parts` interleaves the literal text the printer wrote with the subtrees
  it wrote between them. That interleaving is the structure -- a `.` in `parts`
  separates two components and a `.` inside a name does not -- so reading it gives the
  same answer the printer gave, which splitting the printer's output could not.

Text is the last resort, for the places where a tree has already flattened the answer,
and even there with a reader that counts brackets and that declines a spelling which is
a phrase rather than a name.

What each scheme can say still differs, and the difference is real rather than a gap to
be filled in later:

* C++ encodes parameter types, the return type where the ABI writes one, cv- and
  ref-qualifiers, and for MSVC the calling convention and the declared access.
* Swift encodes the whole function type, so parameters, result and `throws` all come
  back. D, Free Pascal and Delphi encode their parameter types, and Free Pascal a
  function's result.
* Rust, Go, Nim and Objective-C encode a path, or a class and a selector, and no
  signature at all. `parameters` is `None` for them, which is not the same as `()`: one
  says "the name does not carry this", the other says "it carries an empty list".

The `None`s are the point. A field that guesses is worse than a field that declines.
"""

from dataclasses import dataclass
from typing import NamedTuple

from .api import _decode
from .api import detect as _detect
from .api import parse as _parse
from .core.limits import DEFAULT_LIMITS, Limits
from .core.style import DEFAULT_STYLE, Style

__all__ = ["Signature", "signature", "signatureb"]

#: What separates the components of a qualified name, by scheme. Objective-C's is a
#: space, because a method is a class and a selector; a category -- the `(Store247)` in
#: `+[A_B209(Store247) andThen:do:]` -- says which chunk of source declared the method
#: and is not part of its name, so it stays in `demangled` and out of `qualified_name`.
_SEPARATORS = {
    "itanium": "::",
    "msvc": "::",
    "rust": "::",
    "delphi": "::",
    "swift": ".",
    "d": ".",
    "go": ".",
    "nim": ".",
    "pascal": ".",
    "objc": " ",
}

#: Words that trail a declaration and qualify it rather than being part of its type.
_TRAILING_QUALIFIERS = frozenset({"const", "volatile", "restrict", "__restrict", "&", "&&", "noexcept"})

#: What MSVC writes before a declaration: the access it was declared with, and whether it
#: is static or virtual. Kept because a tool reading a binary wants to know, and nothing
#: else in the structured view records it.
_LEADING_QUALIFIERS = frozenset({"private", "protected", "public", "static", "virtual"})


@dataclass(frozen=True, slots=True)
class Signature:
    """What a mangled name says about the entity it names.

    Every field is either what the name encodes or `None`/empty where it encodes
    nothing. Nothing here is inferred from a spelling that does not say it.

    The three name fields hold to one invariant, which is what makes them safe to
    recombine: where `namespace` is not empty, joining it to `base_name` with the
    scheme's separator gives `qualified_name` exactly. Template arguments therefore stay
    on `base_name` -- `sort<int*>`, not `sort` -- because they are part of the component
    they belong to.
    """

    language: str
    """The scheme that read the name: `"itanium"`, `"msvc"`, `"rust"` and so on."""

    demangled: str
    """The whole readable spelling, the same string `demangle()` returns."""

    qualified_name: str
    """The entity's name with its scope and without its type."""

    base_name: str
    """The last component of that.

    Empty only where the name's own last component is: D writes an anonymous symbol as
    `demangle.anonymous.`, and inventing a name for it would say more than the mangling
    does.
    """

    namespace: str
    """Everything before the last component. Empty at the top level."""

    parameters: tuple[str, ...] | None
    """The parameter types, spelled, or `None` where the name encodes no parameter list.

    `()` and `None` mean different things: a C++ function taking no arguments has `()`,
    and a Rust path has `None` because Rust does not put the signature in the symbol.
    """

    return_type: str | None
    """The return type, spelled, or `None` where the name does not encode one.

    Most C++ functions do not: the ABI omits it, because overloads cannot differ by it.
    A template specialisation does. For a data symbol this is the type of the object.
    """

    calling_convention: str | None
    """`__cdecl`, `__stdcall`, ... . MSVC only; no other scheme writes one."""

    qualifiers: tuple[str, ...] = ()
    """What qualifies the declaration rather than its type.

    The trailing cv- and ref-qualifiers of a member function -- `const`, `&&`,
    `noexcept` -- for MSVC the leading access and storage words `private`, `static`,
    `virtual`, and for Swift the effects a function declares: `throws`, `async`.
    """

    special: str | None = None
    """What the symbol is *about*, where it is about something rather than being it.

    `vtable for`, `typeinfo for`, `guard variable for`, `non-virtual thunk to`; D's
    `initializer for`; Swift's `protocol requirements base descriptor for`. The other
    fields then describe the entity the symbol is about, so `base_name` on a
    `vtable for std::ostream` is `ostream`.
    """

    decoration: str = ""
    """What the symbol table appended, separator included: `"@@GLIBCXX_3.4"`, `".cold"`."""

    is_function: bool = False
    """Whether the name says it encodes a function.

    False where the name does not say. A Rust or Go symbol is a path, and a path to a
    function reads exactly like a path to a static, so neither is claimed to be either.
    """

    is_data: bool = False
    """Whether the name encodes an object rather than a function."""

    is_ctor_or_dtor: bool = False
    """Whether it is a constructor or a destructor of the type it sits in.

    C++ repeats the class's own name, or negates it; Swift writes `init` and `deinit`.
    No other scheme here marks the two, so no other scheme reports them.
    """

    @property
    def is_special(self) -> bool:
        """Whether the symbol is about an entity rather than being one."""
        return self.special is not None

    def __str__(self) -> str:
        return self.demangled


def signature(
    mangled: str,
    *,
    language: str | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> Signature:
    """The parts of `mangled`.

    Raises the same errors as `demangle_strict`: a name that cannot be read has no
    parts, and a `Signature` full of `None` would say that it did and that they were
    all empty.
    """
    tree = _parse(mangled, language=language, style=style, limits=limits)
    scheme = language or _detect(mangled) or ""
    return _extract(_Reading(scheme, _SEPARATORS.get(scheme, "::"), style), tree)


def signatureb(
    mangled: bytes,
    *,
    language: str | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> Signature:
    """`signature()` over bytes. Its fields are `str`, as a tree's spellings are.

    The form to use when the names come from a symbol table: an ELF or Mach-O string
    table holds bytes, and a tool that has read one should not have to guess an encoding
    to ask what a name's parts are.
    """
    return signature(_decode(mangled), language=language, style=style, limits=limits)


class _Reading(NamedTuple):
    """What every step of the read needs to know, and nothing else.

    `style` travels with the rest because a subtree has to be spelled the way the whole
    name was: a tree parsed under one style and spelled under another is a mixture of
    the two, and `namespace` would then disagree with `demangled` about the same name.
    """

    scheme: str
    separator: str
    style: str | Style | None

    def spell(self, node):
        return node.spell(style=self.style)


def _extract(reading, tree):
    whole = demangled = reading.spell(tree)
    decoration = ""
    special = None

    # `whole` is the current tree's spelling, refreshed each time a wrapper comes off:
    # the `symbol` test below compares a child against it, and rendering the tree twice
    # to ask is rendering it twice per name.
    while True:
        kind = tree.kind
        if kind == "decorated":
            decoration = tree.decoration + decoration
            tree = tree.inner
        elif kind == "special":
            # `vtable for X` describes X, so the rest of the fields describe X. A chain
            # of them -- `guard variable for reference temporary for x` -- keeps the
            # outermost label, which is what the symbol *is*.
            if special is None:
                special = tree.label.strip()
            tree = tree.inner
        elif kind == "symbol" and len(tree.children()) == 1 and reading.spell(tree.children()[0]) == whole:
            # Rust, D and Swift wrap the whole thing in a `symbol` node, which carries
            # the spelling and nothing the parts need. Only where the child *is* the
            # spelling: Go's `go:buildinfo` and D's `initializer for demangle.test` are
            # one node inside a wrapper that adds text of its own, and unwrapping them
            # would drop it.
            tree = tree.children()[0]
        else:
            break
        whole = reading.spell(tree)

    found = _parts_of(reading, tree)
    if special is None:
        special = found["label"]
    namespace, base = found["namespace"], found["base_name"]
    if namespace is None:
        namespace, base = _split_last(found["qualified_name"], reading.separator)

    return Signature(
        language=reading.scheme,
        demangled=demangled,
        qualified_name=found["qualified_name"],
        base_name=base,
        namespace=namespace,
        parameters=found["parameters"],
        return_type=found["return_type"],
        calling_convention=found["calling_convention"],
        qualifiers=found["qualifiers"],
        special=special,
        decoration=decoration,
        is_function=bool(found["is_function"]),
        is_data=found["is_data"],
        is_ctor_or_dtor=_is_structor(reading, namespace, base),
    )


def _blank(reading, tree):
    return {
        "qualified_name": reading.spell(tree),
        "namespace": None,
        "base_name": "",
        "parameters": None,
        "return_type": None,
        "calling_convention": None,
        "qualifiers": (),
        "is_function": None,
        "is_data": False,
        "label": None,
    }


def _parts_of(reading, tree):
    """Read what the tree says, by the shape it has rather than by the scheme that built it."""
    found = _blank(reading, tree)
    kind = tree.kind

    if kind == "function" and getattr(tree, "name", None) is not None:
        # A C++ function declaration: the name sits inside its own type.
        _name_from(reading, found, tree.name)
        found["parameters"] = tuple(reading.spell(parameter) for parameter in tree.parameters)
        found["return_type"] = reading.spell(tree.returns) if tree.returns is not None else None
        found["qualifiers"] = _trailing(getattr(tree, "suffix", ""))
        found["is_function"] = True
        return found

    if kind == "declaration":
        # MSVC: the declarator is the name, and the type stands beside it.
        _name_from(reading, found, tree.declarator)
        found["qualifiers"] = _leading(tree.prefix) + _trailing(tree.suffix)
        declared = tree.type
        # A *pointer* to a function is data; only a declared function type is a
        # function, so this looks through nothing.
        if declared is not None and declared.kind == "function":
            found["parameters"] = tuple(reading.spell(parameter) for parameter in declared.parameters)
            found["return_type"] = reading.spell(declared.returns) if declared.returns is not None else None
            found["calling_convention"] = getattr(declared, "convention", None) or None
            found["qualifiers"] += _trailing(" ".join(getattr(declared, "member_cv", ()) or ()))
            found["is_function"] = True
        else:
            found["is_data"] = True
            found["is_function"] = False
            found["return_type"] = reading.spell(declared) if declared is not None else None
        return found

    if kind == "symbol" and reading.scheme == "objc":
        # A method is a class and a selector, and the selector's colons say how many
        # arguments it takes without saying what any of them is.
        names = [reading.spell(child) for child in tree.children() if child.kind == "name"]
        if len(names) == 2:
            found["namespace"], found["base_name"] = names
            found["qualified_name"] = " ".join(names)
            found["is_function"] = True
        else:
            # A module constructor or a class reference, not a method. There is no class
            # and no selector to hand back, and the spelling is prose -- splitting
            # `Objective-C module constructor` at its last space would invent both.
            found["namespace"], found["base_name"] = "", found["qualified_name"]
        return found

    if kind in ("path", "qualified"):
        _name_from(reading, found, tree)
        return found

    if _from_parts(reading, found, tree):
        return found

    if kind == "variable":
        # For a tree that names its kinds but is not built out of fragments -- which a
        # registered plugin's may not be. Every scheme shipped here reaches the reader
        # above instead.
        found["is_data"] = True
        found["is_function"] = False
    return found


def _name_from(reading, found, node):
    """Fill the three name fields from a name node.

    Splitting a *node* is what makes the answer right: `A::operator<` and
    `map<int, string>::at` both hold the separator's characters in places that are not
    separators, and only the tree knows which is which. Where the components do not
    reproduce the spelling -- Rust writes `::` into a closure's own name, so joining
    them would double it -- the components are the wrong answer and are dropped, leaving
    the bracket-counting reader in `_extract` to do it.
    """
    found["qualified_name"] = reading.spell(node)
    if node.kind not in ("path", "qualified"):
        return
    components = [reading.spell(child) for child in node.children()]
    if not components or not all(components):
        return
    if reading.separator.join(components) != found["qualified_name"]:
        return
    found["namespace"] = reading.separator.join(components[:-1])
    found["base_name"] = components[-1]


#: What a printer writes between a declaration and its result. Swift spells a function's
#: with `->` and a variable's with `:`; Free Pascal writes a routine's with `:`.
_RESULT_MARKERS = frozenset({"->", ":"})

#: Words a printer writes after the parameter list that qualify the declaration.
_DECLARATION_WORDS = frozenset({"async", "mutating", "nonmutating", "rethrows", "throws"})

#: What Free Pascal writes where the parameter list would go when the compiler dropped
#: it. A note is not a parameter, so a list that is only this one is no list at all.
_ELIDED = "<parameters elided by the compiler>"


def _from_parts(reading, found, node):
    """Read a node that is its own fragments, in output order.

    Swift, D, Go, Nim, Free Pascal and Delphi all build their trees this way: `parts`
    interleaves the literal text a printer emitted with the subtrees it emitted between
    them. That interleaving is the structure -- a `.` in `parts` separates two
    components and a `.` inside a name does not -- so reading it gives the same answer
    the printer gave, which splitting its output could not.
    """
    parts = getattr(node, "parts", None)
    if not parts:
        return False

    components = [""]
    parameters = None
    result = None
    qualifiers = []
    label = None
    at = 0

    # A leading literal is what the printer said this symbol is *about*: `type info for`,
    # `default associated conformance accessor for`. It labels the entity the rest of the
    # parts describe, which is what `special` records. The trailing space is what makes it
    # a label rather than a prefix of the name: Go's `go:buildinfo` is one word.
    if isinstance(parts[0], str) and parts[0].strip() and parts[0].endswith(" ") and _holds_a_node(parts[1:]):
        label = parts[0].strip()
        at = 1

    region = "name"
    while at < len(parts):
        part = parts[at]
        at += 1
        if not isinstance(part, str):
            if region == "result":
                result += reading.spell(part)
            elif region == "name":
                components[-1] += reading.spell(part)
            continue
        if region == "name" and part == reading.separator:
            components.append("")
        elif region == "name" and components[-1] and "(" in part:
            # A `(` after a component opens the parameter list; a `(` where a component
            # has not started yet is part of one. Swift writes a file-private routine as
            # `Foundation.FileHandle.(_check in _2DF8)()`, where the first bracket is the
            # name and the second is the call. D writes the whole list as one literal,
            # so what precedes the bracket in this fragment is still name.
            head, _, opening = part.partition("(")
            components[-1] += head
            parameters, at = _parameter_list(reading, parts, at, opening)
            region = "signed"
        elif region != "result" and part.strip() in _RESULT_MARKERS:
            region, result = "result", ""
        elif region == "name":
            components[-1] += part
        elif region == "result":
            result += part
        else:
            qualifiers.extend(word for word in part.split() if word in _DECLARATION_WORDS)

    found["qualified_name"] = reading.separator.join(components)
    if len(components) > 1:
        found["namespace"] = reading.separator.join(components[:-1])
        found["base_name"] = components[-1]
    # One component is not a split. `_TtBf32_` spells `Builtin.FPIEEE32` out of a single
    # literal, and D writes `initializer for demangle.test` as a label and one node --
    # in both the components are all the tree has, so the reader in `_extract` takes
    # over, which at least counts brackets.
    found["parameters"] = None if parameters == (_ELIDED,) else parameters
    found["return_type"] = result or None
    found["qualifiers"] = tuple(qualifiers)
    found["label"] = label
    if node.kind == "variable":
        found["is_data"] = True
        found["is_function"] = False
    elif parameters is not None or node.kind == "function" or (result is not None and label is None):
        # A labelled symbol is *about* an entity rather than being one, and the type
        # after its `:` is what the label names -- an associated type, not a result --
        # so it says nothing about whether anything here is callable.
        found["is_function"] = True
    return True


def _holds_a_node(parts):
    """Whether any of `parts` is a subtree rather than literal text."""
    return any(not isinstance(part, str) for part in parts)


def _parameter_list(reading, parts, at, opening=""):
    """The parameters between a `(` already consumed and its matching `)`.

    Counted rather than split: a parameter can be a function type of its own, so the
    comma that separates two parameters is the one at bracket depth one. Free Pascal and
    Delphi hand the whole list over as a single `parameters` node, which says the arity
    outright and is taken as it stands.
    """
    taken = _Parameters()
    if taken.text(opening):
        return tuple(taken.found), at
    while at < len(parts):
        part = parts[at]
        at += 1
        if not isinstance(part, str):
            if part.kind == "parameters":
                taken.found.extend(spelled for child in part.children() if (spelled := reading.spell(child)))
            else:
                taken.current += reading.spell(part)
            continue
        if taken.text(part):
            return tuple(taken.found), at
    taken.end()
    return tuple(taken.found), at


class _Parameters:
    """A parameter list being read, a fragment at a time.

    The state has to survive between fragments because a parameter's text can span
    several of them -- a subtree, then the literal that follows it -- and the bracket
    depth that decides which comma is a separator spans them too.
    """

    __slots__ = ("current", "depth", "found")

    def __init__(self):
        self.current = ""
        self.found = []
        # One, for the `(` the caller has already read.
        self.depth = 1

    def text(self, fragment):
        """Read literal text. True once the bracket the list opened with has closed."""
        for char in fragment:
            if char in "([{":
                self.depth += 1
            elif char in ")]}":
                self.depth -= 1
                if not self.depth:
                    self.end()
                    return True
            elif char == "," and self.depth == 1:
                self.end()
                continue
            self.current += char
        return False

    def end(self):
        """End the parameter being read, where anything was read."""
        if self.current.strip():
            self.found.append(self.current.strip())
        self.current = ""


def _trailing(text):
    return tuple(word for word in text.split() if word in _TRAILING_QUALIFIERS)


def _leading(text):
    return tuple(word.rstrip(":") for word in text.split() if word.rstrip(":") in _LEADING_QUALIFIERS)


def _split_last(text, separator):
    """Split a qualified name at its last separator, counting brackets.

    `std::map<int, std::string>::at` splits after `>`, not inside the argument list, and
    `A::operator->` does not open a bracket it never closes -- the depth is clamped, so
    an unbalanced `>` from an operator name cannot swallow the rest of the string.

    A space outside every bracket means this is not a qualified name at all but a phrase
    the printer wrote -- `inout Swift.Int`, `operator new`, `__thunk__ [B,0,1,0]` -- and
    the separators in a phrase do not separate components. Those are left whole, which
    is the only answer that is not invented.
    """
    if not separator or (separator.strip() and _phrase(text)):
        return "", text
    depth = 0
    cut = -1
    at = 0
    width = len(separator)
    while at < len(text):
        char = text[at]
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth = depth - 1 if depth else 0
        elif depth == 0 and text.startswith(separator, at):
            cut = at
            at += width
            continue
        at += 1
    if cut < 0:
        return "", text
    return text[:cut], text[cut + width :]


def _phrase(text):
    """Whether `text` holds a space at bracket depth zero."""
    depth = 0
    for char in text:
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth = depth - 1 if depth else 0
        elif char == " " and depth == 0:
            return True
    return False


#: What Swift calls the two, where C++ repeats the class's own name.
_SWIFT_STRUCTORS = frozenset({"__allocating_init", "__deallocating_deinit", "deinit", "init"})


def _is_structor(reading, namespace, base):
    """Whether a name is a constructor or a destructor of the class it sits in."""
    if reading.scheme == "swift":
        return bool(namespace) and base in _SWIFT_STRUCTORS
    if base.startswith("~"):
        return True
    if not namespace:
        return False
    _, enclosing = _split_last(namespace, reading.separator)
    # `Foo<int>::Foo` is a constructor: the class carries its arguments and the
    # constructor does not, so the comparison drops them.
    cut = enclosing.find("<")
    if cut > 0:
        enclosing = enclosing[:cut]
    return bool(enclosing) and enclosing == base
