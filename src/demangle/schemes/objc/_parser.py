"""Objective-C symbol names, as the compiler writes them.

Objective-C is barely mangled, and what mangling there is comes from clang rather than
from a language specification. So the rules here are transcribed from clang's own
sources -- `lib/AST/Mangle.cpp` for method and block names, `lib/CodeGen/CGObjCMac.cpp`
for the Apple runtime's data symbols and `lib/CodeGen/CGObjCGNU.cpp` for the GNUstep
runtime's -- and correctness rests on re-assembly, as it does for Go, Nim and Free
Pascal: the parts this splits out, rejoined with the compiler's own separators, must
reproduce the symbol exactly.

Three runtimes emit three different sets of names and this reads all three:

    -[NSString stringWithFormat:]              Apple, and the readable form everywhere
    _i_NSString__stringWithFormat_             GNUstep and the GCC runtime
    _OBJC_CLASS_$_NSString                     Apple, non-fragile ABI
    _OBJC_CLASS_NSString                       Apple fragile ABI, and GNUstep
    .objc_class_name_NSString                  Apple fragile ABI

**The GNU-family method mangling is not injective**, and clang says so where it writes
it: "This is the mangling we've always used on the GNU runtimes, but it has obvious
collisions in the face of underscores within class names, category names, and
selectors." `_i_A_B_c` is `-[A(B) c]` if `A` is the class, and `-[A_B c]`... is
impossible to tell apart from it. This reads the leftmost split that re-mangles, and
`Method.ambiguous` says when another reading exists.
"""

import re

__all__ = [
    "DemangleFailure",
    "ObjcSymbol",
    "detect",
    "gnu_method_readings",
    "mangle_gnu_method",
    "parse_objc_symbol",
    "spell_method",
]


class DemangleFailure(Exception):
    """Raised when a name is not one this reads."""


class ObjcSymbol:
    """One Objective-C symbol.

    `text` is the readable spelling. The remaining fields are what the mangling said,
    and `raw` is the symbol exactly as it came in, so a caller can rejoin. `label` is
    the words a runtime data symbol's spelling opens with, `Objective-C class ` say,
    and empty for a method or a block.
    """

    __slots__ = (
        "ambiguous",
        "block",
        "category",
        "class_name",
        "ivar",
        "kind",
        "label",
        "raw",
        "runtime",
        "selector",
        "text",
    )

    def __init__(
        self,
        raw,
        text,
        kind,
        *,
        runtime="",
        class_name=None,
        category=None,
        selector=None,
        ivar=None,
        block=None,
        ambiguous=False,
        label="",
    ):
        self.raw = raw
        self.text = text
        self.kind = kind
        self.runtime = runtime
        self.class_name = class_name
        self.category = category
        self.selector = selector
        self.ivar = ivar
        #: `(index, parent)` for a block invocation function, else None.
        self.block = block
        #: Whether another reading of a GNU-family method name re-mangles to the same
        #: symbol. Never true for the Apple form.
        self.ambiguous = ambiguous
        self.label = label

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"ObjcSymbol({self.raw!r}, {self.text!r}, {self.kind!r})"


#: `$` is deliberately excluded although clang accepts it: it is the separator these
#: symbols are built from, and allowing it reads `_OBJC_CLASS_$_` as a class `$_`.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_SELECTOR = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*)?(?::(?:[A-Za-z_][A-Za-z0-9_]*)?)*$")

#: Selectors clang synthesises for C++ ivars; the only ones that open on a `.`.
_CXX_SPECIAL_SELECTORS = frozenset({".cxx_construct", ".cxx_destruct"})

#: clang's `mangleObjCMethodName` for the Apple runtimes.
_APPLE_METHOD = re.compile(r"^([-+])\[([A-Za-z_][A-Za-z0-9_]*)(?:\(([A-Za-z_][A-Za-z0-9_]*)\))? ([^]]*)\]$")

#: `mangleFunctionBlock`. For a method, `<outer>` is length-prefixed
#: (`mangleObjCMethodNameAsSourceName`), which no C identifier can be.
_BLOCK = re.compile(r"^__(.+)_block_invoke(?:_([0-9]+))?$")

#: A block in a C++ function: the block's `_`, then `_Z` (or `__Z` from the symbol table).
_CXX_BLOCK = ("___Z", "____Z")


def _valid_selector(text):
    """Whether `text` is a selector.

    A selector with arguments ends in `:` and has one colon per argument; a selector
    without takes none. An empty slot is legal -- `:` alone is the selector taking one
    argument and naming nothing -- which is why the slots are optional in the pattern.
    `.cxx_construct` and `.cxx_destruct` are the two selectors clang writes for the
    C++ special members it synthesises; they are not identifiers and take no arguments.
    """
    if not text:
        return False
    if text in _CXX_SPECIAL_SELECTORS:
        return True
    return bool(_SELECTOR.match(text)) and (":" in text or bool(_IDENTIFIER.match(text)))


def mangle_gnu_method(is_class_method, class_name, category, selector):
    """The GNU-family symbol for a method, from clang's `mangleObjCMethodName`.

    Written out because it is what makes reading one checkable: a reading is only
    accepted when re-mangling it reproduces the symbol.
    """
    slots = selector.split(":")
    # Each slot's `:` becomes `_`; `split` leaves a trailing empty piece for the last one.
    body = "".join(f"{slot}_" for slot in slots[:-1]) if slots[-1] == "" else selector
    return f"{'_c_' if is_class_method else '_i_'}{class_name}_{category}_{body}"


def _apple_method(name):
    found = _APPLE_METHOD.match(name)
    if found is None:
        return None
    sign, class_name, category, selector = found.groups()
    if not _valid_selector(selector):
        return None
    return ObjcSymbol(
        name,
        name,
        "class method" if sign == "+" else "instance method",
        runtime="apple",
        class_name=class_name,
        category=category,
        selector=selector,
    )


#: The pair search is quadratic in underscores over attacker-controlled input, so it is
#: bounded (`_i_` + `a_` * 800 is enough to make an unbounded search crawl). Real
#: GNU-runtime method symbols carry at most six.
_MAX_SEPARATORS = 64


def _remangle_selector(selector):
    """The mangled body a selector produces, which is `mangle_gnu_method`'s tail.

    Split out so a reading can be checked without building the whole symbol: the class
    and category halves of a candidate are substrings of the input by construction, so
    the only part that can fail to re-mangle is the selector.
    """
    slots = selector.split(":")
    return "".join([f"{slot}_" for slot in slots[:-1]]) if slots[-1] == "" else selector


def gnu_method_readings(name, limit=None):
    """Every `(class, category, selector)` that re-mangles to `name`, best first.

    The mangling writes `_` for both a separator and a `:`, and does nothing to mark
    which is which, so a symbol can have several readings and all of them are equally
    well-formed. Every split is tried and only those that re-mangle to the input
    survive; what orders them is the one structural signal the mangling does carry.

    A method that is not in a category has an *empty* category field, and the two
    separators around it fall together into a doubled underscore. That is visible, and
    it is the common case, so a reading that needs no category is preferred over one
    that does. Within each group the leftmost split wins, which is the shortest class
    name -- the reading a class whose name holds no underscore produces.

    `limit` stops the search once that many readings are in hand. The caller that wants
    a spelling passes 2, because all it needs is the best reading and whether a second
    exists; enumerating the rest is work nobody reads.

    Measured against the compiler, over symbols clang emitted for declarations this
    package generated: 131 of 131 correct when class, category and selector are ordinary
    identifiers without underscores, which is how Objective-C is conventionally written;
    96 of 122 on a corpus built to put underscores in all three, where 65 of the 122 have
    more than one reading at all. `ObjcSymbol.ambiguous` says which those are.
    """
    if not (name.startswith("_i_") or name.startswith("_c_")):
        return []
    body = name[3:]
    if not body:
        return []

    # The selector for a tail is read once per position, not once per (class, category)
    # pair; recomputing it there would make the search cubic.
    marks = [index for index, char in enumerate(body) if char == "_"][:_MAX_SEPARATORS]
    tails = {}

    def selector_after(mark):
        """The selector spelled by everything past `mark`, or None. Memoised."""
        found = tails.get(mark)
        if found is None:
            tail = body[mark + 1 :]
            selector = _unmangle_selector(tail)
            # `mangle_gnu_method(...) == name` reduces to this: a candidate's class and
            # category are slices of `body`, so they re-mangle by construction and only
            # the selector can disagree.
            if selector is not None and _remangle_selector(selector) != tail:
                selector = None
            found = tails[mark] = (selector,)
        return found[0]

    # Once a prefix is not an identifier, no longer prefix is either.
    stop = len(body)
    for mark in marks:
        if mark and not _IDENTIFIER.match(body[:mark]):
            stop = mark
            break

    readings = []

    def take(class_name, category, selector):
        readings.append((class_name, category or None, selector))
        return limit is not None and len(readings) >= limit

    # Pass one: no category, preferred and cheap (no pair to search).
    for mark in marks:
        if mark == 0 or mark >= stop:
            continue
        if mark + 1 >= len(body) or body[mark + 1] != "_":
            continue
        selector = selector_after(mark + 1)
        if selector is not None and take(body[:mark], "", selector):
            return readings

    # Pass two: readings that need a category, over the already-bounded `marks`.
    for class_end in marks:
        if class_end == 0 or class_end >= stop:
            continue
        class_name = body[:class_end]
        for category_end in marks:
            if category_end <= class_end + 1:
                continue
            category = body[class_end + 1 : category_end]
            if not _IDENTIFIER.match(category):
                continue
            selector = selector_after(category_end)
            if selector is not None and take(class_name, category, selector):
                return readings
    return readings


def spell_method(is_class_method, class_name, category, selector):
    """The readable form of a method, which is the Apple runtimes' own spelling."""
    scope = f"{class_name}({category})" if category else class_name
    return f"{'+' if is_class_method else '-'}[{scope} {selector}]"


def _gnu_method(name):
    # `ambiguous` only asks whether a second reading exists.
    readings = gnu_method_readings(name, limit=2)
    if not readings:
        return None
    is_class_method = name[1] == "c"
    class_name, category, selector = readings[0]
    return ObjcSymbol(
        name,
        spell_method(is_class_method, class_name, category, selector),
        "class method" if is_class_method else "instance method",
        runtime="gnu",
        class_name=class_name,
        category=category,
        selector=selector,
        ambiguous=len(readings) > 1,
    )


def _unmangle_selector(body):
    """Undo `:` -> `_` for a mangled selector, or None if `body` cannot be one.

    A selector with arguments ends in `_` and every `_` in it stands for a `:`; a
    selector without arguments has no `_` at all, because the mangler adds one only when
    the selector takes arguments. That is the whole rule, and it is what makes a
    selector containing a literal underscore unreadable -- `set_x` mangles exactly as
    `set:x` does not, but `a_b_` is both `a:b:` and, if `a_b` were a selector taking one
    argument, that. Only readings that re-mangle survive, so the caller settles it.
    """
    if not body:
        return None
    candidate = body[:-1].replace("_", ":") + ":" if body.endswith("_") else body
    return candidate if _valid_selector(candidate) else None


def _block(name):
    """A block invocation function: `__<outer>_block_invoke[_<n>]`."""
    found = _BLOCK.match(name)
    if found is None:
        return None
    outer, index = found.groups()

    digits = 0
    while digits < len(outer) and outer[digits].isdigit():
        digits += 1
    if digits:
        # The count must match exactly, or a C function called `12foo` reads as a method.
        length = int(outer[:digits])
        if length != len(outer) - digits:
            return None
        parent = parse_objc_symbol(outer[digits:])
        parent_text = parent.text
    else:
        if not _IDENTIFIER.match(outer):
            return None
        parent_text = outer

    number = int(index) if index else 1
    return ObjcSymbol(
        name,
        f"block #{number} in {parent_text}",
        "block",
        runtime="apple" if digits and outer[digits] in "-+" else "",
        block=(number, parent_text),
    )


#: Apple runtime data symbols, from `CGObjCMac.cpp`, longest prefix first so that
#: `OBJC_CLASS_RO_$_` is not read as a class `RO_$_...`.
_APPLE_PREFIXES = (
    ("OBJC_CLASSLIST_REFERENCES_$_", "Objective-C class reference", "plain"),
    ("OBJC_CLASSLIST_SUP_REFS_$_", "Objective-C superclass reference", "plain"),
    ("OBJC_CLASS_REFERENCES_", "Objective-C class reference", "plain"),
    ("OBJC_SELECTOR_REFERENCES_", "Objective-C selector reference", "plain"),
    ("_OBJC_$_CATEGORY_INSTANCE_METHODS_", "instance method list for ", "category"),
    ("_OBJC_$_CATEGORY_CLASS_METHODS_", "class method list for ", "category"),
    ("_OBJC_$_CATEGORY_PROP_LIST_", "property list for ", "category"),
    ("_OBJC_$_CATEGORY_CLASS_PROP_LIST_", "class property list for ", "category"),
    ("_OBJC_CATEGORY_PROTOCOLS_$_", "protocol list for ", "category"),
    ("_OBJC_$_CATEGORY_", "Objective-C category ", "category"),
    ("_OBJC_$_PROTOCOL_INSTANCE_METHODS_OPT_", "optional instance method list for protocol ", "class"),
    ("_OBJC_$_PROTOCOL_CLASS_METHODS_OPT_", "optional class method list for protocol ", "class"),
    ("_OBJC_$_PROTOCOL_INSTANCE_METHODS_", "instance method list for protocol ", "class"),
    ("_OBJC_$_PROTOCOL_CLASS_METHODS_", "class method list for protocol ", "class"),
    ("_OBJC_$_PROTOCOL_METHOD_TYPES_", "method type list for protocol ", "class"),
    ("_OBJC_$_PROTOCOL_REFS_", "protocol list for protocol ", "class"),
    ("OBJC_$_PROP_PROTO_LIST_", "property list for protocol ", "class"),
    ("OBJC_$_CLASS_PROP_PROTO_LIST_", "class property list for protocol ", "class"),
    ("_OBJC_$_INSTANCE_METHODS_", "instance method list for ", "class"),
    ("_OBJC_$_CLASS_METHODS_", "class method list for ", "class"),
    ("_OBJC_$_INSTANCE_VARIABLES_", "instance variable list for ", "class"),
    ("_OBJC_$_PROP_LIST_", "property list for ", "class"),
    ("_OBJC_$_CLASS_PROP_LIST_", "class property list for ", "class"),
    ("_OBJC_CLASS_PROTOCOLS_$_", "protocol list for ", "class"),
    ("_OBJC_CLASS_RO_$_", "class data for ", "class"),
    ("_OBJC_METACLASS_RO_$_", "metaclass data for ", "class"),
    ("_OBJC_LABEL_PROTOCOL_$_", "protocol label for ", "class"),
    ("_OBJC_PROTOCOL_REFERENCE_$_", "protocol reference for ", "class"),
    ("_OBJC_PROTOCOL_$_", "Objective-C protocol ", "class"),
    ("_OBJC_PROTOCOLEXT_", "protocol extension for ", "class"),
    ("OBJC_METACLASS_$_", "Objective-C metaclass ", "class"),
    ("OBJC_CLASS_$_", "Objective-C class ", "class"),
    ("OBJC_EHTYPE_$_", "Objective-C exception type for ", "class"),
    ("OBJC_IVAR_$_", "instance variable offset for ", "ivar"),
    # Fragile-ABI list symbols, which carry no `$`.
    ("OBJC_CATEGORY_INSTANCE_METHODS_", "instance method list for ", "category"),
    ("OBJC_CATEGORY_CLASS_METHODS_", "class method list for ", "category"),
    ("OBJC_CATEGORY_PROTOCOLS_", "protocol list for ", "category"),
    ("OBJC_CATEGORY_", "Objective-C category ", "category"),
    ("OBJC_PROTOCOL_INSTANCE_METHODS_OPT_", "optional instance method list for protocol ", "class"),
    ("OBJC_PROTOCOL_CLASS_METHODS_OPT_", "optional class method list for protocol ", "class"),
    ("OBJC_PROTOCOL_INSTANCE_METHODS_", "instance method list for protocol ", "class"),
    ("OBJC_PROTOCOL_CLASS_METHODS_", "class method list for protocol ", "class"),
    ("OBJC_PROTOCOL_METHOD_TYPES_", "method type list for protocol ", "class"),
    ("OBJC_PROTOCOL_REFS_", "protocol list for protocol ", "class"),
    ("OBJC_INSTANCE_METHODS_", "instance method list for ", "class"),
    ("OBJC_CLASS_METHODS_", "class method list for ", "class"),
    ("OBJC_INSTANCE_VARIABLES_", "instance variable list for ", "class"),
    ("OBJC_CLASS_PROTOCOLS_", "protocol list for ", "class"),
    ("OBJC_CLASSEXT_", "class extension for ", "class"),
    ("OBJC_PROTOCOL_", "Objective-C protocol ", "class"),
    ("OBJC_METACLASS_", "Objective-C metaclass ", "class"),
    ("OBJC_CLASS_", "Objective-C class ", "class"),
    # GCC's own Objective-C front end (mixed case), from the shipped `libobjc.a`.
    ("_OBJC_ClassName_", "Objective-C class name ", "class"),
    ("_OBJC_ClassIvars_", "class variable list for ", "class"),
    ("_OBJC_ClassMethods_", "class method list for ", "class"),
    ("_OBJC_InstanceIvars_", "instance variable list for ", "class"),
    ("_OBJC_InstanceMethods_", "instance method list for ", "class"),
    ("_OBJC_MetaClass_", "Objective-C metaclass ", "class"),
    ("_OBJC_Class_", "Objective-C class ", "class"),
    ("_OBJC_METH_VAR_NAME_", "method variable name ", "counted"),
    ("_OBJC_METH_VAR_TYPE_", "method variable type ", "counted"),
    ("OBJC_METH_VAR_NAME_", "method variable name ", "counted"),
    ("OBJC_METH_VAR_TYPE_", "method variable type ", "counted"),
    ("OBJC_CLASS_NAME_", "Objective-C class name ", "counted"),
    ("OBJC_PROP_NAME_ATTR_", "property name or attribute ", "counted"),
    # GNUstep, from `CGObjCGNU.cpp`. `__objc_ivar_offset_` carries the ivar's type
    # encoding after a second stop in the v2 ABI.
    ("__objc_ivar_offset_value_", "instance variable offset value for ", "gnu-ivar"),
    ("__objc_ivar_offset_", "instance variable offset for ", "gnu-ivar"),
    ("__objc_class_name_", "Objective-C class ", "class"),
    ("__objc_class_ref_", "Objective-C class reference for ", "class"),
    ("__objc_eh_typeinfo_", "Objective-C exception type for ", "class"),
    ("__objc_eh_typename_", "Objective-C exception type name for ", "class"),
    (".objc_class_name_", "Objective-C class ", "class"),
    (".objc_category_name_", "Objective-C category ", "category"),
    (".objc_sel_name_", "Objective-C selector ", "selector"),
    (".objc_sel_types_", "Objective-C type encoding ", "encoding"),
    (".objc_selector_", "Objective-C selector ", "selector-and-types"),
    ("_OBJC_INIT_CLASS_", "class initialiser for ", "class"),
    ("_OBJC_REF_CLASS_", "class reference for ", "class"),
    (".objc_protocol_name_", "Objective-C protocol ", "class"),
)

#: Whole symbols with nothing after them; they name a section, not an entity.
_APPLE_LABELS = {
    "OBJC_LABEL_CLASS_$": "Objective-C class list",
    "OBJC_LABEL_CATEGORY_$": "Objective-C category list",
    "OBJC_LABEL_NONLAZY_CLASS_$": "Objective-C non-lazy class list",
    "OBJC_LABEL_NONLAZY_CATEGORY_$": "Objective-C non-lazy category list",
    "OBJC_LABEL_STUB_CATEGORY_$": "Objective-C stub category list",
    "OBJC_MODULES": "Objective-C module info",
    "OBJC_SYMBOLS": "Objective-C symbol table",
    "OBJC_EHTYPE_id": "Objective-C exception type for id",
    "__objc_classes": "Objective-C class list",
    "__objc_cats": "Objective-C category list",
    "__objc_class_refs": "Objective-C class reference list",
    "__objc_class_aliases": "Objective-C class alias list",
    ".objc_selector_list": "Objective-C selector list",
    ".objc_protocol_list": "Objective-C protocol list",
    ".objc_method_list": "Objective-C method list",
    ".objc_protocol_method_list": "Objective-C protocol method list",
    ".objc_property_list": "Objective-C property list",
    ".objc_ivar_list": "Objective-C instance variable list",
    ".objc_load_function": "Objective-C load function",
    ".objcv2_load_function": "Objective-C load function",
    ".objc_init": "Objective-C module initialiser",
    ".objc_ctor": "Objective-C module constructor",
    ".objc_source_file_name": "Objective-C source file name",
    ".objc_statics": "Objective-C statics",
    "__block_literal_global": "global block literal",
    "__block_descriptor": "block descriptor",
    "__objc_load": "Objective-C load function",
    "_OBJC_Module": "Objective-C module info",
    "_OBJC_ClassName_": "Objective-C class name table",
    "_OBJC_SELECTOR_TABLE": "Objective-C selector table",
    "OBJC_SELECTOR_TABLE": "Objective-C selector table",
    ".objc_null_selector": "null Objective-C selector",
    ".objc_null_protocol": "null Objective-C protocol",
    ".objc_null_protocol_ref": "null Objective-C protocol reference",
    ".objc_null_class_alias": "null Objective-C class alias",
    ".objc_null_class_ref": "null Objective-C class reference",
    ".objc_null_cls_init_ref": "null Objective-C class initialiser reference",
    ".objc_null_category": "null Objective-C category",
    ".objc_null_constant_string": "null Objective-C constant string",
    ".objc_section_sentinel": "Objective-C section sentinel",
    ".objc_early_init": "Objective-C early initialiser",
    ".objc_statics_ptr": "Objective-C statics pointer",
    ".objc_metaclass_ref": "Objective-C metaclass reference",
    ".objc_class_ref": "Objective-C class reference",
    ".objc_class_alias": "Objective-C class alias",
    ".objc_protocol": "Objective-C protocol",
    ".objc_protocol_name": "Objective-C protocol name",
    ".objc_static_class_name": "Objective-C static class name",
    ".objc_constant_string": "Objective-C constant string",
    ".objc_str": "Objective-C string",
    ".objc_string": "Objective-C string",
    "__objc_constant_string": "Objective-C constant string",
}

#: The linker's GNUstep section bounds: not compiler output, but in every such binary.
_SECTION_BOUNDS = re.compile(r"^__(start|stop)___objc_([a-z_]+)$")

#: `GetSymbolNameForTypeEncoding` in `CGObjCGNU.cpp`: `@` (ELF versions) and `=` (breaks
#: lld on Windows) are written as control bytes.
_ENCODING_SUBSTITUTIONS = {"\x01": "@", "\x02": "="}


def decode_type_encoding(text):
    """An Objective-C type encoding, with the GNU runtime's substitutions undone."""
    for written, meant in _ENCODING_SUBSTITUTIONS.items():
        text = text.replace(written, meant)
    return text


#: The assembler's `.<n>` / `.<n>.<n>` uniquing suffix; not part of any name.
_UNIQUING_SUFFIX = re.compile(r"\.[0-9]+$")


def _labelled(name):
    bounds = _SECTION_BOUNDS.match(name)
    if bounds is not None:
        edge, section = bounds.groups()
        return ObjcSymbol(name, f"{edge} of the Objective-C {section.replace('_', ' ')} section", "label")

    base = name
    suffix = ""
    found = _UNIQUING_SUFFIX.search(base)
    if found is not None:
        base, suffix = base[: found.start()], base[found.start() :]
    spelled = _APPLE_LABELS.get(base)
    if spelled is None and base.startswith("__block_descriptor_"):
        # A layout key rather than a name, so it is not taken apart.
        spelled = "block descriptor"
        suffix = ""
    if spelled is None:
        return None
    return ObjcSymbol(name, spelled + (f" #{suffix[1:]}" if suffix else ""), "label")


def _prefixed(name):
    for prefix, label, shape in _APPLE_PREFIXES:
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix) :]
        symbol = _from_prefix(name, rest, label, shape)
        if symbol is not None:
            symbol.label = label
            return symbol
    return None


def _from_prefix(name, rest, label, shape):
    if shape == "plain":
        # These name nothing; anything after them is the assembler's uniquing counter.
        if rest and not rest.startswith("."):
            return None
        return ObjcSymbol(name, label + (f" #{rest[1:]}" if rest else ""), "reference")

    if shape == "selector":
        if not _valid_selector(rest):
            return None
        return ObjcSymbol(name, label + rest, "selector", runtime="gnu", selector=rest)

    if shape == "counted":
        # A numbered string-table entry; GCC writes the first without a number.
        if rest and not rest.isdigit():
            return None
        return ObjcSymbol(name, label + (f"#{rest}" if rest else "#0"), "label")

    if shape == "encoding":
        if not rest:
            return None
        return ObjcSymbol(name, label + decode_type_encoding(rest), "encoding", runtime="gnu")

    if shape == "selector-and-types":
        # Split from the right: the encoding holds no underscore unless it names a
        # struct, and the selector often does.
        splits = [
            stop
            for stop in range(len(rest) - 1, 0, -1)
            if rest[stop] == "_" and _valid_selector(rest[:stop]) and rest[stop + 1 :]
        ]
        if not splits:
            return None
        stop = splits[0]
        selector, encoding = rest[:stop], rest[stop + 1 :]
        return ObjcSymbol(
            name,
            f"{label}{selector} with type encoding {decode_type_encoding(encoding)}",
            "selector",
            runtime="gnu",
            selector=selector,
            ambiguous=len(splits) > 1,
        )

    if shape == "ivar":
        # A class name cannot hold a stop, so the first one is the separator.
        stop = rest.find(".")
        if stop <= 0:
            return None
        class_name, ivar = rest[:stop], rest[stop + 1 :]
        if not (_IDENTIFIER.match(class_name) and _IDENTIFIER.match(ivar)):
            return None
        return ObjcSymbol(
            name, f"{label}{class_name}.{ivar}", "ivar", runtime="apple", class_name=class_name, ivar=ivar
        )

    if shape == "gnu-ivar":
        # v2 appends `.<type encoding>`, which is kept out of the ivar.
        pieces = rest.split(".")
        if len(pieces) not in (2, 3):
            return None
        class_name, ivar = pieces[0], pieces[1]
        if not (_IDENTIFIER.match(class_name) and _IDENTIFIER.match(ivar)):
            return None
        return ObjcSymbol(name, f"{label}{class_name}.{ivar}", "ivar", runtime="gnu", class_name=class_name, ivar=ivar)

    if shape == "category":
        # `_$_` (non-fragile ABI) cannot occur in either name and is tried first; the
        # fragile `_` is split at its leftmost and `ambiguous` records alternatives.
        marker = rest.find("_$_")
        if marker > 0:
            class_name, category = rest[:marker], rest[marker + 3 :]
            if not (_IDENTIFIER.match(class_name) and _IDENTIFIER.match(category)):
                return None
            splits = 1
        else:
            splits = sum(
                1
                for stop in range(1, len(rest))
                if rest[stop] == "_" and _IDENTIFIER.match(rest[:stop]) and _IDENTIFIER.match(rest[stop + 1 :])
            )
            if not splits:
                return None
            stop = next(
                stop
                for stop in range(1, len(rest))
                if rest[stop] == "_" and _IDENTIFIER.match(rest[:stop]) and _IDENTIFIER.match(rest[stop + 1 :])
            )
            class_name, category = rest[:stop], rest[stop + 1 :]
        return ObjcSymbol(
            name,
            f"{label}{class_name}({category})",
            "category",
            class_name=class_name,
            category=category,
            ambiguous=splits > 1,
        )

    if not _IDENTIFIER.match(rest):
        return None
    return ObjcSymbol(name, label + rest, "class", class_name=rest)


#: The order the readers are tried in. `_labelled` first: `__block_literal_global` would
#: otherwise be read by `_prefixed` as something it is not.
_READERS = (_labelled, _apple_method, _block, _prefixed, _gnu_method)


def parse_objc_symbol(name):
    """Parse `name`, returning an `ObjcSymbol`, or raise `DemangleFailure`.

    A Mach-O symbol table carries decoration the assembler added and the compiler did
    not write: one leading underscore on every symbol, and `l` or `L` before that on a
    private label. Each is stripped in turn and the stripped form is tried *first*.

    Trying it first rather than second is what gets `___cfunc_block_invoke` right. Both
    readings parse: `__` is the block prefix, so the outer name is either `_cfunc` with
    no Mach-O underscore or `cfunc` with one. The second is the answer on Darwin, where
    these symbols come from, and on ELF the stripped form does not begin with `__` at
    all and so does not parse -- which leaves the unstripped reading, correctly. A C
    function genuinely called `_cfunc` in an ELF object is the one case the two are
    indistinguishable, and it reads as `cfunc`.
    """
    if not name:
        raise DemangleFailure("empty name")
    if name.startswith(_CXX_BLOCK):
        # A block in a C++ function: the C++ scheme has the production and spells it
        # (`invocation function for block in foo()`); this one would not.
        raise DemangleFailure("a C++ block invocation function, not an Objective-C one")
    for candidate in _candidates(name):
        for reader in _READERS:
            try:
                found = reader(candidate)
            except DemangleFailure:
                continue
            if found is not None:
                found.raw = name
                return found
    raise DemangleFailure("not an Objective-C symbol")


#: The two method manglings shaped like an ordinary C identifier. Measured, not reasoned:
#: over ~740,000 real symbols they claim five, all real methods in the shipped `libobjc.a`.
_METHOD_PREFIXES = ("_i_", "_c_")


def _method_prefixed(name):
    """Whether any strip `_candidates` makes leaves `_i_` or `_c_` at the front.

    The same question as `any(c.startswith(_METHOD_PREFIXES) for c in _candidates(name))`
    and the same answer, without building the list: `detect` is offered every symbol in
    a binary, so the list is not worth allocating. Every strip that production makes is
    one or two characters off the front, so the offsets are what it does, written out.
    """
    if name.startswith(_METHOD_PREFIXES):
        return True
    opening = name[:2]
    if (opening[:1] == "." or opening[:1] == "_") and name.startswith(_METHOD_PREFIXES, 1):
        return True
    return opening in ("l_", "L_", "._") and name.startswith(_METHOD_PREFIXES, 2)


def _candidates(name):
    """`name` with each layer of assembler decoration stripped, most-stripped first."""
    found = []
    if name[:2] in ("l_", "L_"):
        found.append(name[2:])
    if name.startswith("."):
        # GNUstep ELF objects carry `._OBJC_CLASS_Foo`, but `.objc_class_name_Foo` really
        # begins with a stop, so the name itself is tried second rather than dropped.
        found.append(name[1:])
        if name.startswith("._"):
            found.append(name[2:])
    if name.startswith("_"):
        found.append(name[1:])
    found.append(name)
    return [candidate for candidate in found if candidate]


_SCREEN_MARKERS = ("objc", "OBJC", "_block_invoke", "block_literal", "block_descriptor")


def detect(name):
    """Whether `name` is one this reads, and distinctive enough to claim unasked.

    The screen is only there to keep the parse off every symbol in a binary; what
    actually claims a name is the parse succeeding. A form that says `objc` somewhere in
    it is worth trying, and so are the two that do not: `-[`/`+[`, and the `_i_`/`_c_`
    method mangling.

    The markers are tested one by one rather than through `any(...)` over
    `_SCREEN_MARKERS`. This plugin declares `_` among its first characters, so it is
    offered every underscore-prefixed symbol in a binary -- 85% of the benchmark corpus
    -- and the generator would cost six interpreter frames per name to run five
    membership tests that are each a single C-level scan. Written out it is the same
    five scans in the same short-circuiting order, and no frames at all: 71,778 calls
    fewer over the cold corpus. `_SCREEN_MARKERS` stays as the documentation of what the
    screen is.
    """
    if not name:
        return False
    if name[0] in "-+":
        return _apple_method(name) is not None
    if (
        "objc" not in name
        and "OBJC" not in name
        and "_block_invoke" not in name
        and "block_literal" not in name
        and "block_descriptor" not in name
        and not _method_prefixed(name)
    ):
        return False
    try:
        return parse_objc_symbol(name) is not None
    except DemangleFailure:
        return False
