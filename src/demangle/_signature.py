"""The parts of a symbol, in one shape, for every scheme.

`demangle()` answers "what does this name say"; this answers "what are its pieces". A
disassembler labelling a call site wants the base name without its namespace; a
cross-reference wants the namespace without the base name; a signature matcher wants the
parameter types and nothing else. All three can be had by splitting the spelling, and
all three get it wrong the same way, because `::` and `,` and `(` occur inside template
arguments and inside function types as well as between the parts a caller means.

So the split is done on the tree the parser already built. Two shapes of tree, because
the schemes come in two kinds:

* Itanium and MSVC build a declaration -- a function node with its name inside its own
  type, or a declarator standing beside one. The fields are read off it directly.
* Swift, D, Go, Nim, Free Pascal, Delphi, Ada, JNI and the pre-Itanium C++ schemes
  (`gnuv2`, `codewarrior`) build a node that *is* its own fragments, in output order:
  `parts` interleaves the literal text the printer wrote with the subtrees it wrote
  between them. That interleaving is the structure -- a `.` in `parts` separates two
  components and a `.` inside a name does not -- so reading it gives the same answer
  the printer gave, which splitting the printer's output could not.

Rust's path and Objective-C's class and selector are read off their nodes as they stand,
and an Objective-C runtime data symbol is split into the label it opens with and the
class, category, instance variable or selector the label is about.

Text is the last resort, for the places where a tree has already flattened the answer,
and even there with a reader that counts brackets and that declines a spelling which is
a phrase rather than a name.

What each scheme can say still differs, and the difference is real rather than a gap to
be filled in later:

* C++, in all four of its manglings here -- Itanium, MSVC, pre-Itanium g++ and its
  relatives, CodeWarrior -- encodes parameter types, the return type where the mangling
  writes one, cv- and ref-qualifiers, and for MSVC the calling convention and the
  declared access.
* Swift encodes the whole function type, so parameters, result and `throws` all come
  back. D, Free Pascal and Delphi encode their parameter types, Free Pascal a function's
  result, and Delphi the result of a template function and its calling convention; JNI
  encodes them for an overloaded method, the one form that needs them.
* Rust, Go, Nim, Ada and Objective-C encode a path, or a class and a selector, and no
  signature at all. `parameters` is `None` for them, which is not the same as `()`: one
  says "the name does not carry this", the other says "it carries an empty list".

The `None`s are the point. A field that guesses is worse than a field that declines.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

from .api import _decode, _read
from .core.ast import builder_for
from .core.limits import DEFAULT_LIMITS, Limits
from .core.style import DEFAULT_STYLE, Style, get_style

__all__ = ["Signature", "signature", "signatureb"]

#: Component separators by scheme. Objective-C's is a space (class, selector); a category
#: is not part of a method's name, so it stays out of `qualified_name`.
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
    "jni": ".",
    # `gnuv2` and `codewarrior` spell C++ and take the `::` default.
}

_CLOSING = {"msvc": "'", "delphi": "`"}

_TRAILING_QUALIFIERS = frozenset({"const", "volatile", "restrict", "__restrict", "&", "&&", "noexcept"})

#: MSVC's access and storage words, which nothing else in the structured view records.
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
    """The last component of that. Never empty for a name that parsed."""

    namespace: str
    """Everything before the last component. Empty at the top level."""

    parameters: tuple[str, ...] | None
    """The parameter types, spelled, or `None` where the name encodes no parameter list.

    `()` and `None` mean different things: an Itanium function taking no arguments has
    `()`, and a Rust path has `None` because Rust does not put the signature in the
    symbol. MSVC and pre-Itanium g++ spell an empty list `(void)`, and it comes back as
    they spell it: `('void',)`.
    """

    return_type: str | None
    """The return type, spelled, or `None` where the name does not encode one.

    Most C++ functions do not: the ABI omits it, because overloads cannot differ by it.
    A template specialisation does. For a data symbol this is the type of the object.
    """

    calling_convention: str | None
    """`__cdecl`, `__stdcall`, ... . MSVC and Delphi; no other scheme writes one.

    Delphi's `__saveregs` is kept with it, after the convention it modifies.
    """

    qualifiers: tuple[str, ...] = ()
    """What qualifies the declaration rather than its type.

    The trailing cv- and ref-qualifiers of a member function -- `const`, `&&`,
    `noexcept` -- for MSVC the leading access and storage words `private`, `static`,
    `virtual`, and for Swift the effects a function declares: `throws`, `async`.
    """

    special: str | None = None
    """What the symbol is *about*, where it is about something rather than being it.

    `vtable for`, `typeinfo for`, `guard variable for`, `non-virtual thunk to`; D's
    `initializer for`; Swift's `protocol requirements base descriptor for`;
    Objective-C's `Objective-C class` and `instance variable offset for`. The other
    fields then describe the entity the symbol is about, so `base_name` on a
    `vtable for std::ostream` is `ostream`, and on `_OBJC_CLASS_$_NSData` is `NSData`.

    Not every scheme writes its label first. MSVC writes most of its labels after the
    name, `` Base::`vftable' ``, `` C::f`adjustor{16}' ``, and Delphi writes
    `__linkproc__` between a unit and the procedure, `System::__linkproc__ Abort`; the
    label is taken from wherever it sits, so `qualified_name` is `Base`, `C::f`,
    `System::Abort`. The label comes without its quotes and without what follows it
    about its own table: `adjustor`, not `adjustor{16}`; `RTTI Base Class Descriptor`,
    without its `at (0, -1, 0, 64)`; `vftable`, without its `` {for `A'} ``. A label with
    nothing to be about stands as the name too: MSVC's `` `vector ctor iterator' `` has
    `special` and `qualified_name` both `vector ctor iterator`. Only the last label is
    taken: a thunk over a compiler-made member,
    `` Base::`vector deleting dtor'`adjustor{4}' ``, is `adjustor`, and the member it
    adjusts, `` Base::`vector deleting dtor' ``, is the name.
    """

    decoration: str = ""
    """What the symbol table appended, separator included: `"@@GLIBCXX_3.4"`, `".cold"`."""

    is_function: bool = False
    """Whether the name says it encodes a function.

    False where the name does not say. A Rust or Go symbol is a path, and a path to a
    function reads exactly like a path to a static, so neither is claimed to be either.
    """

    is_data: bool = False
    """Whether the name encodes an object rather than a function.

    Of the symbols with a `special` label, MSVC's tables, descriptors and guards set
    it. The others -- Itanium's `vtable for` and `typeinfo for`, Delphi's `__tpdsc__`,
    Free Pascal's `run-time type information for` -- leave it False.
    """

    is_ctor_or_dtor: bool = False
    """Whether it is a constructor or a destructor of the type it sits in.

    C++ repeats the class's own name, or negates it; Swift writes `init` and `deinit`.
    No other scheme here marks the two, so no other scheme reports them. MSVC's
    `scalar deleting dtor`, `vector deleting dtor` and `vbase dtor` are destructors as
    Itanium's deleting destructor is, and so is a thunk to one,
    `` Base::`vector deleting dtor'`adjustor{4}' ``; its closures and iterators are not.
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
    language: str | Sequence[str] | None = None,
    style: str | Style | None = DEFAULT_STYLE,
    limits: Limits = DEFAULT_LIMITS,
) -> Signature:
    """The parts of `mangled`.

    Raises the same errors as `demangle_strict`: a name that cannot be read has no
    parts, and a `Signature` full of `None` would say that it did and that they were
    all empty.
    """
    resolved = get_style(style)
    # The plugin that read the name, under the name it is registered by, so an alias
    # (`objective-c`) finds its separator.
    plugin, tree = _read(mangled, builder_for(resolved), language, resolved, limits)
    return _extract(_Reading(plugin.name, _SEPARATORS.get(plugin.name, "::"), style), tree)


def signatureb(
    mangled: bytes,
    *,
    language: str | Sequence[str] | None = None,
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

    @property
    def closing(self):
        """What closes a backtick span: MSVC's `` `anonymous namespace' ``, Delphi's
        `` `class constructor` ``. None where a backtick is not a quote."""
        return _CLOSING.get(self.scheme)

    def spell(self, node):
        return node.spell(style=self.style)


def _extract(reading, tree):
    whole = demangled = reading.spell(tree)
    decoration = ""
    special = None

    # `whole` is refreshed as each wrapper comes off, so the tree is rendered once per step.
    while True:
        kind = tree.kind
        if kind == "decorated":
            decoration = tree.decoration + decoration
            tree = tree.inner
        elif kind == "special":
            # `vtable for X` describes X. A chain keeps the outermost label.
            if special is None:
                special = tree.label.strip()
            tree = tree.inner
        elif kind == "symbol" and len(tree.children()) == 1 and reading.spell(tree.children()[0]) == whole:
            # Rust, D and Swift wrap everything in a `symbol` node. Unwrapped only where
            # the child *is* the spelling: some wrappers add text of their own.
            tree = tree.children()[0]
        else:
            break
        whole = reading.spell(tree)

    found = _parts_of(reading, tree)
    if special is None:
        special = found["label"]
    namespace, base = found["namespace"], found["base_name"]
    if namespace is None:
        namespace, base = _split_last(found["qualified_name"], reading.separator, reading.closing)

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
        is_ctor_or_dtor=special in _MSVC_DESTRUCTORS or _is_structor(reading, namespace, base),
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
    """Read what the tree says, by the shape it has rather than by the scheme that built
    it."""
    found = _blank(reading, tree)
    kind = tree.kind

    if kind == "function" and getattr(tree, "name", None) is not None:
        _name_from(reading, found, tree.name)
        found["parameters"] = tuple(reading.spell(parameter) for parameter in tree.parameters)
        found["return_type"] = reading.spell(tree.returns) if tree.returns is not None else None
        found["qualifiers"] = _trailing(getattr(tree, "suffix", ""))
        found["is_function"] = True
        return found

    if kind == "declaration":
        _name_from(reading, found, tree.declarator)
        _msvc_special(reading, found)
        lead = tree.prefix + getattr(tree, "access", "") + getattr(tree, "member_type", "")
        found["qualifiers"] = _leading(lead) + _trailing(tree.suffix)
        declared = tree.type
        # A *pointer* to a function is data, so this looks through nothing.
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
        names = [reading.spell(child) for child in tree.children() if child.kind == "name"]
        if len(names) == 2:
            found["namespace"], found["base_name"] = names
            found["qualified_name"] = " ".join(names)
            found["is_function"] = True
        elif not _objc_metadata(reading, found, tree):
            # A module constructor or class reference: prose, not a class and selector.
            found["namespace"], found["base_name"] = "", found["qualified_name"]
        return found

    if kind in ("path", "qualified"):
        _name_from(reading, found, tree)
        return found

    if _from_parts(reading, found, tree):
        return found

    if _msvc_special(reading, found):
        # Nothing but the label's own function-ness says what it is: a vcall thunk is
        # code, and a table or a descriptor is an object.
        found["is_function"] = found["label"] == "vcall"
        found["is_data"] = not found["is_function"]
        return found

    if kind == "variable":
        # A plugin tree that names its kinds but is not built from fragments.
        found["is_data"] = True
        found["is_function"] = False
    return found


#: What MSVC writes between `` ` `` and `'` to say what a symbol is about, rather than what it is
#: called. `adjustor` and its kin are a thunk's own label and sit after the one they adjust.
_MSVC_LABELS = frozenset(
    {
        "adjustor",
        "copy ctor closure",
        "default ctor closure",
        "eh vector ctor iterator",
        "eh vector dtor iterator",
        "eh vector vbase ctor iterator",
        "local static guard",
        "local static thread guard",
        "local vftable",
        "local vftable ctor closure",
        "placement delete closure",
        "placement delete[] closure",
        "scalar deleting dtor",
        "vbase dtor",
        "vbtable",
        "vcall",
        "vector ctor iterator",
        "vector deleting dtor",
        "vector dtor iterator",
        "vector vbase ctor iterator",
        "vftable",
        "virtual displacement map",
        "vtordisp",
        "vtordispex",
    }
)

#: The labels that name a destructor the compiler made for the class they are about.
_MSVC_DESTRUCTORS = frozenset({"scalar deleting dtor", "vector deleting dtor", "vbase dtor"})

#: How MSVC joins a label to the name it follows: `` Base::`vftable' ``, `` C::f`adjustor{16}' ``,
#: `` int `RTTI Type Descriptor' ``. Its dynamic initialisers wrap the name instead.
_MSVC_SUFFIXES = (
    dict.fromkeys(_MSVC_LABELS, "::")
    | dict.fromkeys(("adjustor", "vtordisp", "vtordispex"), "")
    | dict.fromkeys(("RTTI Type Descriptor", "RTTI Type Descriptor Name"), " ")
    | dict.fromkeys(
        (
            "RTTI Base Class Array",
            "RTTI Base Class Descriptor",
            "RTTI Class Hierarchy Descriptor",
            "RTTI Complete Object Locator",
        ),
        "::",
    )
)


def _labelled(parts):
    """`qualified_name` with `special` where the scheme writes it.

    MSVC writes most of its labels after the name, `` Base::`vftable' ``, and its dynamic
    initialisers around it, `` `dynamic initializer for 'Foo'' ``; every other scheme's
    go before it: `vtable for Base`. A label that is its own name,
    `` `vector ctor iterator' ``, is written once. Delphi writes a linker procedure and a
    virtual-definition flag or thunk after the scope, `System::__linkproc__ Abort`, and
    after the name it is about where nothing follows, `TStream::__vdthk__`. What MSVC
    writes about the label's own table stays with it -- `` `vftable'{for `A'} ``,
    `` `adjustor{16}' `` -- because it is what tells one table or thunk of a class from
    another.
    """
    special, name = parts.special, parts.qualified_name
    if special is None:
        return name
    if parts.language == "delphi" and special in _DELPHI_SCOPED:
        if f"{name}::{special}" in parts.demangled:
            return f"{name}::{special}"
        if parts.namespace:
            return f"{parts.namespace}::{special} {parts.base_name}"
    if parts.language == "msvc" and special in _MSVC_INITIALISERS:
        return f"`{special} '{name}''"
    joint = _MSVC_SUFFIXES.get(special) if parts.language == "msvc" else None
    if joint is None:
        return f"{special} {name}"
    spelled, after = _spelled_label(parts.demangled, special)
    if name == special:
        return spelled
    if special.startswith("RTTI Type Descriptor"):
        # Where the type's declarator name would be: `` char *`RTTI Type Descriptor' ``,
        # `` int (*`RTTI Type Descriptor')[2] ``.
        if not name.endswith(after):
            after = ""
        head = name[: len(name) - len(after)]
        joint = "" if head.endswith(("*", "&")) else " "
        return f"{head}{joint}{spelled}{after}"
    return f"{name}{joint}{spelled}"


def _spelled_label(demangled, special):
    """The span holding `special` in `demangled` with its payload, and what follows them."""
    for start, end, _ in reversed(_label_spans(demangled)):
        if _msvc_label(demangled[start + 1 : end]) == special:
            rest = demangled[end + 1 :]
            after = _without_payload(rest)
            return demangled[start : end + 1] + rest[: len(rest) - len(after)], after
    return f"`{special}'", ""


#: The Delphi labels the unmangler writes after a scope rather than before the name.
_DELPHI_SCOPED = frozenset({"__linkproc__", "__vdflg__", "__vdthk__"})

_MSVC_INITIALISERS = ("dynamic initializer for", "dynamic atexit destructor for")
_MSVC_INITIALISER = re.compile(rf"`({'|'.join(_MSVC_INITIALISERS)}) (?:'(.*)'|`(.*)')'")
_MSVC_RTTI = re.compile(r"(RTTI [A-Za-z ]+?)(?: at \(.*\))?")
_MSVC_LEADING = frozenset(
    {"[thunk]:", "public:", "private:", "protected:", "static", "virtual", "const", "volatile"}
    | {"class", "struct", "union", "enum"}
)
_MSVC_CONVENTIONS = frozenset({"__cdecl", "__stdcall", "__fastcall", "__thiscall", "__vectorcall", "__clrcall"})


def _label_spans(text):
    """The `` `...' `` spans of `text` that could hold the symbol's own label.

    Each comes with whether it sits inside parentheses. A span inside another, inside
    braces or inside a template argument list belongs to a symbol the name mentions --
    `` X<&const C::`vftable'>::x `` is `x` -- and is not one of them. The `<` and `>` of
    an operator's own name, `` C::operator<`adjustor{4}' ``, open and close nothing.
    """
    spans = []
    angle = paren = brace = quoting = opened = 0
    for at, char in enumerate(text):
        if quoting:
            if char == "'":
                quoting -= 1
                if not quoting and not angle and not brace:
                    spans.append((opened, at, paren > 0))
            elif char == "`":
                quoting += 1
        elif char == "`":
            quoting, opened = 1, at
        elif char in "{}":
            brace = brace + 1 if char == "{" else max(brace - 1, 0)
        elif char in "()":
            paren = paren + 1 if char == "(" else max(paren - 1, 0)
        elif char in "<>" and not text[:at].rstrip("<>=-").endswith("operator"):
            angle = angle + 1 if char == "<" else max(angle - 1, 0)
    return spans


def _msvc_label(inside):
    """The label a span holds, or None where it holds a scope or a number."""
    word = inside.split("{")[0].strip()
    if word in _MSVC_LABELS:
        return word
    rtti = _MSVC_RTTI.fullmatch(word)
    return rtti.group(1) if rtti else None


def _without_payload(rest):
    """`rest` with the `{...}` that follows a label taken off: `{for `B'}`, `{8, {flat}}`."""
    if not rest.startswith("{"):
        return rest
    depth = 0
    for at, char in enumerate(rest):
        depth += (char == "{") - (char == "}")
        if not depth:
            return rest[at + 1 :]
    return rest


def _closes_unopened_angle(rest):
    """Whether `rest` closes a `<` that opened before it."""
    depth = 0
    for char in rest:
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
            if depth < 0:
                return True
    return False


def _declared_name(entity):
    """The name in a spelled declaration.

    `private: static int C::i` is `C::i`, and the declarator around a name is not part
    of it: `int (*x)[3]` is `x`.
    """
    name = entity
    while words := _words(name, "'"):
        name = words[-1].lstrip("*&")
        if not name.startswith("("):
            break
        name = name[1 : _closing_parenthesis(name)]
    return name or entity


def _closing_parenthesis(text):
    """Where the `(` that opens `text` closes, or the end where it does not."""
    depth = 0
    for at, char in enumerate(text):
        depth += (char == "(") - (char == ")")
        if not depth:
            return at
    return len(text)


def _msvc_special(reading, found):
    """Take a label out of an MSVC name, leaving the entity it is about.

    MSVC spells `const Base::`vftable'` and `Base::`scalar deleting dtor'` with the
    label where a name would be, so splitting the spelling makes the label the base
    name. The label goes to `label` and the entity -- the class, the variable, the
    type -- stays, as it does for `vtable for Base`. `const`, the access words and the
    `{for `Base'}` path are about the label's table, not the entity, and are dropped.
    A label is the name's own only where it ends the name, once that path is off, and
    sits outside every template argument list: `` X<&const C::`vftable'>::x `` is a
    static member, not a table. False where the name holds no label of its own.
    """
    if reading.scheme != "msvc":
        return False
    text = found["qualified_name"]
    initialiser = _MSVC_INITIALISER.fullmatch(text)
    label = None
    convention = None
    if initialiser:
        label = initialiser.group(1)
        text = initialiser.group(2) or initialiser.group(3)
        if initialiser.group(3):
            text = _declared_name(text)
    else:
        spans = _label_spans(text)
        if not spans:
            return False
        start, end, enclosed = spans[-1]
        label = _msvc_label(text[start + 1 : end])
        rest = _without_payload(text[end + 1 :])
        # A type descriptor's label stands where a declarator's name would, so it can
        # sit inside the type: `` int (*`RTTI Type Descriptor')[2] ``.
        if label is None or ((rest or enclosed) and not label.startswith("RTTI Type Descriptor")):
            return False
        if _closes_unopened_angle(rest):
            # The span sat in a template argument list whose `<` an operator's own name
            # hid: `` operator<<<&class C `RTTI Type Descriptor'> ``.
            return False
        # The label a thunk adjusts stays in the name it is part of:
        # `` Base::`vector deleting dtor'`adjustor{4}' `` adjusts `` Base::`vector deleting dtor' ``.
        text = text[:start] + rest
        words = text.split(" ")
        while words and words[0] in _MSVC_LEADING | _MSVC_CONVENTIONS:
            if words[0] in _MSVC_CONVENTIONS:
                convention = words[0]
            words.pop(0)
        text = " ".join(words).rstrip(" :")
    found["label"] = label
    found["qualified_name"] = text or label
    if convention:
        found["calling_convention"] = convention
    return True


def _objc_metadata(reading, found, tree):
    """Read an Objective-C runtime data symbol as a label and the entity it is about.

    `_OBJC_CLASS_$_NSData` spells `Objective-C class NSData`, and the split is the one
    Swift's descriptors get: the label is `special`, and the name fields hold the class.
    A category is named as Objective-C names one, `NSString(Extra)`; an instance
    variable is its class and its own name, joined as a method's class and selector
    are. False where the tree is not a label before an entity.
    """
    parts = tree.parts
    if not (isinstance(parts[0], str) and parts[0].endswith(" ") and _holds_a_node(parts[1:])):
        return False
    entity = parts[1:]
    objc_kind = tree.objc_kind
    if objc_kind == "ivar":
        member = entity[-1]
        if len(entity) != 2 or not isinstance(member, str) or not member.startswith("."):
            return False
        found["namespace"], found["base_name"] = reading.spell(entity[0]), member[1:]
        found["qualified_name"] = found["namespace"] + reading.separator + found["base_name"]
    elif objc_kind == "category":
        found["qualified_name"] = "".join(part if isinstance(part, str) else reading.spell(part) for part in entity)
        found["namespace"], found["base_name"] = "", found["qualified_name"]
    elif objc_kind in ("class", "selector") and not isinstance(entity[0], str):
        # A selector's type encoding follows it, and is not part of its name.
        found["qualified_name"] = found["base_name"] = reading.spell(entity[0])
        found["namespace"] = ""
    else:
        return False
    found["label"] = parts[0].strip()
    return True


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


#: Swift's `->` and `:`, Free Pascal's `:`.
_RESULT_MARKERS = frozenset({"->", ":"})

_DECLARATION_WORDS = frozenset({"async", "mutating", "nonmutating", "rethrows", "throws"})

#: Free Pascal's note where a dropped parameter list would go; not a parameter.
_ELIDED = "<parameters elided by the compiler>"


def _from_parts(reading, found, node):
    """Read a node that is its own fragments, in output order.

    Most schemes build their trees this way -- every one but Itanium, MSVC, Rust and
    Objective-C, whose shapes are read above: `parts` interleaves the literal text a
    printer emitted with the subtrees it emitted between them. That interleaving is the
    structure -- a `.` in `parts` separates two components and a `.` inside a name does
    not -- so reading it gives the same answer the printer gave, which splitting its
    output could not.
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

    # A leading literal labels what the symbol is *about* (`type info for`); the trailing
    # space tells a label from a name prefix (Go's `go:buildinfo`).
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
        elif region == "name" and components[-1] and part.startswith("("):
            # Only a fragment that *opens* with `(` is the parameter list: Swift writes
            # `Foundation.FileHandle.(_check in _2DF8)()`.
            parameters, at = _parameter_list(reading, parts, at, part[1:])
            region = "signed"
        elif region == "name" and reading.scheme == "pascal" and part.startswith(" #"):
            # A wrapper's entry number and the method it forwards to follow the
            # interface it is about: `ICOMPARER #0: SYSTEM.TINTERFACEDOBJECT.QUERYINTERF`.
            region = "signed"
        elif region != "result" and part.strip() in _RESULT_MARKERS:
            region, result = "result", ""
        elif region == "name":
            components[-1] += part
        elif region == "result":
            result += part
        else:
            qualifiers.extend(word for word in part.split() if word in _DECLARATION_WORDS)

    head = None
    if reading.scheme == "delphi" and len(components) == 1:
        head = _delphi_head(components[0], parameters is not None)
        components = [head.name]
        label = head.label or label
        result = result or head.result
        if label == "__vdthk__":
            # `(rtti)` after a virtual-definition thunk is its table's flags.
            parameters = None

    found["qualified_name"] = reading.separator.join(components)
    if head is not None and head.base is not None:
        found["namespace"], found["base_name"] = head.scope, head.base
    elif len(components) > 1:
        found["namespace"] = reading.separator.join(components[:-1])
        found["base_name"] = components[-1]
    # One component is not a split (`_TtBf32_`); the reader in `_extract` takes over.
    if head is not None:
        found["calling_convention"] = head.convention
    found["parameters"] = None if parameters == (_ELIDED,) else parameters
    found["return_type"] = result or None
    found["qualifiers"] = tuple(qualifiers)
    found["label"] = label
    if node.kind == "variable":
        found["is_data"] = True
        found["is_function"] = False
    elif parameters is not None or node.kind == "function" or (result is not None and label is None):
        # After a label, the `:` type is what the label names, not a result, so it does
        # not make this a function.
        found["is_function"] = True
    return True


#: The labels whose operand is a type or a thunk's operands, not a name with a result.
_DELPHI_OPERANDS = frozenset({"__tpdsc__", "__thunk__"})


class _DelphiHead(NamedTuple):
    """The spelled head of a Delphi symbol, taken apart.

    `scope` and `base` are set only where the name holds `operator`, whose spelling has
    spaces that the bracket-counting reader takes for a phrase.
    """

    name: str
    convention: str | None
    label: str | None
    result: str | None
    scope: str | None
    base: str | None


def _words(text, closing=None):
    """`text` split at the spaces outside every bracket and every quoted span."""
    words = []
    start = 0
    for at in _top_level(text, closing):
        if text[at] == " ":
            words.append(text[start:at])
            start = at + 1
    words.append(text[start:])
    return [word for word in words if word]


def _top_level(text, closing=None):
    """The positions in `text` outside every bracket and, with `closing`, every quoted span.

    A span opens at a backtick and closes at `closing`: MSVC writes `'` and nests one
    span in another, `` `void f(void)'::`2'::x ``, and Delphi writes a second backtick,
    `` `class constructor` ``. Inside a span only those two characters count, so the `<`
    of the function a local lives in, `` `bool C::operator<(int)' ``, opens nothing.
    Outside one a closing bracket never takes the depth below zero, so the `>` of
    `A::operator->` cannot swallow the rest of the text.
    """
    depth = quoting = 0
    for at, char in enumerate(text):
        if quoting:
            if char == closing:
                quoting -= 1
            elif char == "`":
                quoting += 1
        elif closing and char == "`":
            quoting = 1
        elif char in "<([":
            depth += 1
        elif char in ">)]":
            depth = depth - 1 if depth else 0
        elif not depth:
            yield at


def _delphi_head(head, is_function):
    """Take the spelled prefixes off a Delphi name.

    The unmangler writes the result, the calling convention and what the symbol is
    *before* the qualified name -- `bool __fastcall Rtti::TValue::IsType<int>`,
    `System::__linkproc__ __fastcall Abort`, `__tpdsc__ Forms::TForm` -- so the name is
    what is left once they are read off. Where the convention is absent, the last word
    is the name and any before it the result; `operator` names are the exception, which
    run to the end and are told apart by the word `operator`. A label with nothing after
    it -- `System::__linkproc__`, `TStream::__vdthk__` -- is about its scope, which is
    then the name.
    """
    # Imported here, not above: a scheme is loaded when a name of it is first read.
    from .schemes.delphi._parser import CONVENTIONS, LABELS

    words = _words(head, "`")
    convention = [word for word in words if word in CONVENTIONS]
    words = [word for word in words if word not in CONVENTIONS]
    label = None
    scope = ""
    for at, word in enumerate(words):
        marker = word.rsplit("::", 1)[-1]
        if marker in LABELS:
            label = marker
            scope = word[: len(word) - len(marker)]
            del words[at]
            break
    result = None
    if label in _DELPHI_OPERANDS or not words:
        named = words
    else:
        first = next((at for at, word in enumerate(words) if word.rsplit("::", 1)[-1] == "operator"), None)
        if first is None:
            first = len(words) - 1 if is_function else 0
        named = words[first:]
        result = " ".join(words[:first]) or None
    name = scope + " ".join(named) if named else scope.removesuffix("::") or label or ""
    base = None
    if named and named[0].rsplit("::", 1)[-1] == "operator":
        cut = len(scope) + len(named[0]) - len("operator")
        base = name[cut:]
        scope = name[:cut].removesuffix("::")
    return _DelphiHead(name, " ".join(convention) or None, label, result, scope, base)


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


def _split_last(text, separator, closing=None):
    """Split a qualified name at its last separator, counting brackets.

    `std::map<int, std::string>::at` splits after `>`, not inside the argument list, and
    `A::operator->` does not open a bracket it never closes.

    A space outside every bracket means this is not a qualified name at all but a phrase
    the printer wrote -- `inout Swift.Int`, `operator new`, `__thunk__ [B,0,1,0]` -- and
    the separators in a phrase do not separate components. Those are left whole, which
    is the only answer that is not invented.

    With `closing`, a quoted span is one piece of a component, which is how MSVC writes
    `` `anonymous namespace'::f `` and the function a local static lives in.
    """
    if not separator:
        return "", text
    phrase = separator.strip()
    cut = -1
    after = 0
    for at in _top_level(text, closing):
        if phrase and text[at] == " ":
            return "", text
        if at >= after and text.startswith(separator, at):
            cut = at
            after = at + len(separator)
    if cut < 0:
        return "", text
    return text[:cut], text[cut + len(separator) :]


_SWIFT_STRUCTORS = frozenset({"__allocating_init", "__deallocating_deinit", "deinit", "init"})


def _is_structor(reading, namespace, base):
    """Whether a name is a constructor or a destructor of the class it sits in."""
    if reading.scheme == "swift":
        return bool(namespace) and base in _SWIFT_STRUCTORS
    if reading.scheme == "msvc" and base[1:-1] in _MSVC_DESTRUCTORS:
        # A thunk's own label is `special`, and the destructor it adjusts is the name.
        return True
    if base.startswith("~"):
        return True
    if not namespace:
        return False
    _, enclosing = _split_last(namespace, reading.separator, reading.closing)
    # `Foo<int>::Foo`: the constructor lacks the class's arguments.
    cut = enclosing.find("<")
    if cut > 0:
        enclosing = enclosing[:cut]
    return bool(enclosing) and enclosing == base
