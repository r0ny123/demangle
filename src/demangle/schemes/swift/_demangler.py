"""Reading a Swift mangled name into a tree.

A port of the algorithm in the Swift compiler's `lib/Demangling/Demangler.cpp`, which is
the only complete description of the mangling that exists -- `docs/ABI/Mangling.rst` is a
grammar sketch and is explicitly not normative about the tree that results.

The mangling is **postfix**, and the demangler is a stack machine because of it. The main
loop reads one operator at a time and pushes what it produces; an operator that needs
operands pops them back off. `_ZN`-style prefix manglings can be read by recursive
descent, this cannot: in `$sSiSgD` the `Si` is pushed, `Sg` pops it to make `Int?`, and
`D` pops that. Nothing is known about what a component is *for* until the operator that
consumes it arrives.

Three pieces of state span a whole name and are what make it compress so well:

* **Substitutions.** Every nominal type and identifier is remembered; `A` and a letter
  refer back to one. A single `A` operator can push several.
* **Words.** Each identifier contributes its capitalised words to a table, and a later
  identifier can be spelled out of them -- `0c6ToHostD0` builds `HostToPluginMessage` from
  words of the module name. This is the compression no other mangling here has.
* **Position.** `pushBack` is used as a one-character backtrack in several places, so the
  reader is not purely forward-only.

Failure is `None` throughout, exactly as the reference uses `nullptr`: an operator that
cannot read its operands returns `None`, and every caller checks. The public entry point
turns that into an exception at the boundary.
"""

from ._node import CONTEXT_KINDS, FUNCTION_ATTR_KINDS, Node, is_any_generic, is_decl_name, is_entity, is_requirement
from ._punycode import PunycodeError
from ._punycode import decode as punycode_decode

__all__ = ["demangle_symbol", "demangle_type"]

STDLIB_NAME = "Swift"
MANGLING_MODULE_OBJC = "__C"
MANGLING_MODULE_CLANG_IMPORTER = "__C_Synthesized"
BUILTIN_TYPE_NAME_PREFIX = "Builtin."

_MAX_NUM_WORDS = 26
_MAX_REPEAT_COUNT = 2048

#: `getManglingPrefixLength`: the prefixes a Swift symbol may carry. `_T0` is the Swift 4
#: mangling and still appears in shipped binaries; the leading `_` and `@__swiftmacro_`
#: forms come from platforms whose linkers add one.
MANGLING_PREFIXES = ("_T0", "$S", "_$S", "$s", "_$s", "@__swiftmacro_")

#: Types the standard library mangles as `S` and one letter, from the compiler's
#: `StandardTypesMangling.def`. The kind matters: the printer spells a protocol
#: differently from a structure once it is inside an existential.
STANDARD_TYPES = {
    "A": ("Structure", "AutoreleasingUnsafeMutablePointer"),
    "a": ("Structure", "Array"),
    "b": ("Structure", "Bool"),
    "D": ("Structure", "Dictionary"),
    "d": ("Structure", "Double"),
    "f": ("Structure", "Float"),
    "h": ("Structure", "Set"),
    "I": ("Structure", "DefaultIndices"),
    "i": ("Structure", "Int"),
    "J": ("Structure", "Character"),
    "N": ("Structure", "ClosedRange"),
    "n": ("Structure", "Range"),
    "O": ("Structure", "ObjectIdentifier"),
    "P": ("Structure", "UnsafePointer"),
    "p": ("Structure", "UnsafeMutablePointer"),
    "R": ("Structure", "UnsafeBufferPointer"),
    "r": ("Structure", "UnsafeMutableBufferPointer"),
    "S": ("Structure", "String"),
    "s": ("Structure", "Substring"),
    "u": ("Structure", "UInt"),
    "V": ("Structure", "UnsafeRawPointer"),
    "v": ("Structure", "UnsafeMutableRawPointer"),
    "W": ("Structure", "UnsafeRawBufferPointer"),
    "w": ("Structure", "UnsafeMutableRawBufferPointer"),
    "q": ("Enum", "Optional"),
    "B": ("Protocol", "BinaryFloatingPoint"),
    "E": ("Protocol", "Encodable"),
    "e": ("Protocol", "Decodable"),
    "F": ("Protocol", "FloatingPoint"),
    "G": ("Protocol", "RandomNumberGenerator"),
    "H": ("Protocol", "Hashable"),
    "j": ("Protocol", "Numeric"),
    "K": ("Protocol", "BidirectionalCollection"),
    "k": ("Protocol", "RandomAccessCollection"),
    "L": ("Protocol", "Comparable"),
    "l": ("Protocol", "Collection"),
    "M": ("Protocol", "MutableCollection"),
    "m": ("Protocol", "RangeReplaceableCollection"),
    "Q": ("Protocol", "Equatable"),
    "T": ("Protocol", "Sequence"),
    "t": ("Protocol", "IteratorProtocol"),
    "U": ("Protocol", "UnsignedInteger"),
    "X": ("Protocol", "RangeExpression"),
    "x": ("Protocol", "Strideable"),
    "Y": ("Protocol", "RawRepresentable"),
    "y": ("Protocol", "StringProtocol"),
    "Z": ("Protocol", "SignedInteger"),
    "z": ("Protocol", "BinaryInteger"),
}

#: Written `Sc` and one letter. Same source file, second level.
CONCURRENCY_TYPES = {
    "A": ("Protocol", "Actor"),
    "C": ("Structure", "CheckedContinuation"),
    "c": ("Structure", "UnsafeContinuation"),
    "E": ("Structure", "CancellationError"),
    "e": ("Structure", "UnownedSerialExecutor"),
    "F": ("Protocol", "Executor"),
    "f": ("Protocol", "SerialExecutor"),
    "G": ("Structure", "TaskGroup"),
    "g": ("Structure", "ThrowingTaskGroup"),
    "I": ("Protocol", "AsyncIteratorProtocol"),
    "i": ("Protocol", "AsyncSequence"),
    "J": ("Structure", "UnownedJob"),
    "M": ("Class", "MainActor"),
    "P": ("Structure", "TaskPriority"),
    "S": ("Structure", "AsyncStream"),
    "s": ("Structure", "AsyncThrowingStream"),
    "T": ("Structure", "Task"),
    "t": ("Structure", "UnsafeCurrentTask"),
}

_BUILTIN_SIMPLE = {
    "b": "Builtin.BridgeObject",
    "B": "Builtin.UnsafeValueBuffer",
    "e": "Builtin.Executor",
    "I": "Builtin.IntLiteral",
    "O": "Builtin.UnknownObject",
    "o": "Builtin.NativeObject",
    "p": "Builtin.RawPointer",
    "j": "Builtin.Job",
    "D": "Builtin.DefaultActorStorage",
    "d": "Builtin.NonDefaultDistributedActorStorage",
    "c": "Builtin.RawUnsafeContinuation",
    "t": "Builtin.SILToken",
    "w": "Builtin.Word",
    "P": "Builtin.PackIndex",
}

_ANY_PROTOCOL_CONFORMANCE_KINDS = frozenset(
    [
        "ConcreteProtocolConformance",
        "DependentProtocolConformanceRoot",
        "DependentProtocolConformanceInherited",
        "DependentProtocolConformanceAssociated",
    ]
)


def _is_digit(char):
    return "0" <= char <= "9"


def _is_lower(char):
    return "a" <= char <= "z"


def _is_upper(char):
    return "A" <= char <= "Z"


def _is_letter(char):
    return _is_lower(char) or _is_upper(char)


def _is_word_start(char):
    return char != "" and not _is_digit(char) and char != "_"


def _is_word_end(char, previous):
    if char in ("_", ""):
        return True
    return not _is_upper(previous) and _is_upper(char)


def is_mangled_name(name):
    """Whether `name` carries one of the prefixes the reference accepts."""
    return _prefix_length(name) > 0


def _prefix_length(name):
    """`getManglingPrefixLength`.

    `_T0` and `$S` are the Swift 4 spellings and still occur; `@__swiftmacro_` is the
    prefix a macro expansion's symbol carries.
    """
    for prefix in MANGLING_PREFIXES:
        if name.startswith(prefix):
            return len(prefix)
    return 0


def _is_old_function_type_mangling(name):
    """Swift 4 put parameter labels inside the argument tuple; Swift 4.2 and later do not.

    `popFunctionParamLabels` needs to know which, and the only signal is the prefix. It is
    `_T` alone that says so -- `$S` and `_$S` are Swift 4.2 and carry labels the new way,
    which is not obvious from the fact that they are also "old" prefixes.
    """
    return name.startswith("_T")


def _is_protocol_node(node):
    while node is not None and node.kind == "Type":
        node = node.children[0] if node.children else None
    return node is not None and node.kind in ("Protocol", "ProtocolSymbolicReference")


def _is_any_protocol_conformance(kind):
    return kind in _ANY_PROTOCOL_CONFORMANCE_KINDS


class Demangler:
    """The reference's `Demangler`, one instance per name.

    `None` means failure everywhere, as `nullptr` does in the reference. Keeping that
    convention rather than raising is not a stylistic choice: several productions are
    *speculative*, reading ahead and accepting the failure as an answer, and an exception
    would have to be caught at each of them.
    """

    def __init__(self, text, resolver=None):
        self.text = text
        #: Called with `(SymbolicReference, offset-of-its-offset-field)` and expected to
        #: return a mangled fragment naming what the reference points at, or None. With
        #: no resolver a symbolic reference refuses the name, which is what the reference
        #: demangler does too.
        self.resolver = resolver
        self.pos = 0
        self.end = len(text)
        self.stack = []
        self.substitutions = []
        #: The word table for identifier substitutions, capped as the reference caps it.
        self.words = []
        self.is_old_function_type_mangling = False
        #: Guards against a name whose substitutions refer to each other in a cycle.
        self.depth = 0

    # -- reading ---------------------------------------------------------------

    def peek(self):
        return self.text[self.pos] if self.pos < self.end else ""

    def next_char(self):
        if self.pos >= self.end:
            return ""
        char = self.text[self.pos]
        self.pos += 1
        return char

    def next_if(self, what):
        if self.text.startswith(what, self.pos):
            self.pos += len(what)
            return True
        return False

    def push_back(self):
        self.pos -= 1

    def consume_all(self):
        rest = self.text[self.pos :]
        self.pos = self.end
        return rest

    # -- the node stack --------------------------------------------------------

    def push(self, node):
        self.stack.append(node)

    def pop(self, want=None):
        """Pop, optionally only if the top matches.

        `want` is a kind or a predicate. A mismatch is not an error: several callers pop
        optional decorations, and a `None` there means "there was not one".
        """
        if not self.stack:
            return None
        if want is not None:
            kind = self.stack[-1].kind
            if not (want(kind) if callable(want) else kind == want):
                return None
        return self.stack.pop()

    def add_substitution(self, node):
        if node is not None:
            self.substitutions.append(node)

    # -- building --------------------------------------------------------------

    def add_child(self, parent, child):
        if parent is None or child is None:
            return None
        parent.children.append(child)
        return parent

    def with_child(self, kind, child):
        if child is None:
            return None
        return Node(kind, children=[child])

    def with_children(self, kind, *children):
        if any(child is None for child in children):
            return None
        return Node(kind, children=list(children))

    def make_type(self, child):
        return self.with_child("Type", child)

    def with_popped_type(self, kind):
        return self.with_child(kind, self.pop("Type"))

    def change_kind(self, node, kind):
        if node is None:
            return None
        return Node(kind, text=node.text, index=node.index, children=list(node.children))

    def swift_type(self, kind, name):
        return self.make_type(self.with_children(kind, Node("Module", text=STDLIB_NAME), Node("Identifier", text=name)))

    # -- numbers ---------------------------------------------------------------

    def natural(self):
        """A run of digits, or `None` where the reference returns its -1000 sentinel."""
        if not _is_digit(self.peek()):
            return None
        start = self.pos
        while _is_digit(self.peek()):
            self.pos += 1
        return int(self.text[start : self.pos])

    def index(self):
        """`_` is 0 and `<n>_` is n+1, so that 0 costs one character rather than two."""
        if self.next_if("_"):
            return 0
        number = self.natural()
        if number is not None and self.next_if("_"):
            return number + 1
        return None

    def index_as_node(self, kind="Number"):
        found = self.index()
        return None if found is None else Node(kind, index=found)

    # -- entry points ----------------------------------------------------------

    def demangle_symbol(self):
        """`demangleSymbol`: read a whole name and assemble the `Global` node."""
        length = _prefix_length(self.text)
        if length == 0:
            return None
        self.is_old_function_type_mangling = _is_old_function_type_mangling(self.text)
        self.pos = length

        if not self.parse_and_push():
            return None

        top = Node("Global")
        # IRGen's own `.<n>` disambiguator is pushed last and so sits on top of the
        # stack, where it hid the function attributes underneath it: nothing was popped,
        # the closure was never moved inside the forwarder that applies it, and the
        # name came out as its parts in stack order. Taken off first and put back at the
        # end, which is where it is spelled.
        suffix = self.pop("Suffix")
        parent = top
        while True:
            attribute = self.pop(lambda kind: kind in FUNCTION_ATTR_KINDS)
            if attribute is None:
                break
            parent.add(attribute)
            if attribute.kind in ("PartialApplyForwarder", "PartialApplyObjCForwarder"):
                parent = attribute

        for node in self.stack:
            # A `Type` wrapper is dropped at the top level: `Global` holds what the type
            # *is*, not the fact that it is one.
            parent.add(node.first if node.kind == "Type" and node.children else node)

        if suffix is not None:
            top.add(suffix)

        return top if top.children else None

    def parse_and_push(self):
        while self.pos < self.end:
            node = self.demangle_operator()
            if node is None:
                return False
            self.push(node)
        return True

    def demangle_operator(self):
        """One operator, dispatching on its first character.

        The reference writes this as a `switch` with a `goto recur` for the padding byte;
        the loop here is that `goto`.
        """
        while True:
            char = self.next_char()
            if char == "\xff":
                # Alignment padding in front of a symbolic reference. Skipped.
                continue
            handler = _OPERATORS.get(char)
            if handler is not None:
                return handler(self)
            if char == "":
                return None
            if char in _SYMBOLIC_REFERENCE_BYTES:
                # A symbolic reference points into the binary's own metadata, which a
                # name on its own does not carry. Without a resolver the reference
                # refuses it, and so does this.
                return self.demangle_symbolic_reference(ord(char))
            self.push_back()
            return self.demangle_identifier()

    def demangle_symbolic_reference(self, raw_kind):
        """`demangleSymbolicReference`: one introducer byte and a four-byte offset.

        The offset is signed, little-endian, and relative to its own first byte. What the
        resolver hands back is a *mangled fragment* rather than a spelling, which is what
        lets the rest of the name use it: the reference may be the base of a bound
        generic, or a member the next operator qualifies, and only a real node can be
        either. A resolver that declines refuses the whole name, exactly as a null from
        the reference's own resolver does.
        """
        from .symbolic import CONTEXT, KINDS, OFFSET_WIDTH, SymbolicReference

        at = self.pos
        if at + OFFSET_WIDTH > self.end:
            return None
        raw = self.text[at : at + OFFSET_WIDTH].encode("latin-1")
        offset = int.from_bytes(raw, "little", signed=True)
        self.pos = at + OFFSET_WIDTH

        if self.resolver is None:
            return None
        kind, directness = KINDS.get(raw_kind, (None, None))
        if kind is None:
            # 3 through 8 and 0x0C reach the reference's switch only to fall through it.
            return None

        fragment = self.resolver(SymbolicReference(raw_kind, kind, directness, offset, at), at)
        if not fragment:
            return None
        resolved = Demangler(fragment, self.resolver).demangle_fragment()
        if resolved is None:
            return None
        # "Types register as substitutions even when symbolically referenced" -- except
        # for the two opaque-type kinds, which name a position rather than a type.
        if kind == CONTEXT and resolved.kind not in (
            "OpaqueTypeDescriptorSymbolicReference",
            "OpaqueReturnTypeOf",
        ):
            self.add_substitution(resolved)
        return resolved

    def demangle_fragment(self):
        """Read the whole text as one node, for a resolver's answer."""
        if not self.parse_and_push():
            return None
        found = self.pop()
        return found if found is not None and not self.stack else None

    # -- substitutions ---------------------------------------------------------

    def demangle_multi_substitutions(self):
        """`A`, which may name several substitutions at once.

        A lower-case letter says "this one, and more follow"; an upper-case letter is the
        last. A number before a letter is a *repeat count*, so `A2b` pushes the same node
        twice -- except that a number followed by `_` is a large index instead, which is
        why the count cannot be read until the character after it is known.
        """
        repeat = None
        while True:
            char = self.next_char()
            if char == "":
                return None
            if _is_lower(char):
                node = self.push_multi_substitutions(repeat, ord(char) - ord("a"))
                if node is None:
                    return None
                self.push(node)
                repeat = None
                continue
            if _is_upper(char):
                return self.push_multi_substitutions(repeat, ord(char) - ord("A"))
            if char == "_":
                # The number was an index, not a count. 27 rather than 26 because the
                # single-letter form already covers 0-25.
                if repeat is None:
                    return None
                at = repeat + 27
                if at >= len(self.substitutions):
                    return None
                return self.substitutions[at]
            self.push_back()
            repeat = self.natural()
            if repeat is None:
                return None

    def push_multi_substitutions(self, repeat, at):
        if at >= len(self.substitutions):
            return None
        if repeat is not None and repeat > _MAX_REPEAT_COUNT:
            return None
        node = self.substitutions[at]
        if repeat is not None:
            for _ in range(repeat - 1):
                self.push(node)
        return node

    def demangle_standard_substitution(self):
        """`S` and a letter: one of the standard library's common types."""
        char = self.next_char()
        if char == "o":
            return Node("Module", text=MANGLING_MODULE_OBJC)
        if char == "C":
            return Node("Module", text=MANGLING_MODULE_CLANG_IMPORTER)
        if char == "g":
            optional = self.make_type(
                self.with_children(
                    "BoundGenericEnum",
                    self.swift_type("Enum", "Optional"),
                    self.with_child("TypeList", self.pop("Type")),
                )
            )
            self.add_substitution(optional)
            return optional

        self.push_back()
        repeat = self.natural()
        if repeat is not None and repeat > _MAX_REPEAT_COUNT:
            return None
        second_level = self.next_if("c")
        table = CONCURRENCY_TYPES if second_level else STANDARD_TYPES
        found = table.get(self.next_char())
        if found is None:
            return None
        node = self.swift_type(*found)
        if repeat is not None:
            for _ in range(repeat - 1):
                self.push(node)
        return node

    # -- identifiers -----------------------------------------------------------

    def demangle_identifier(self):
        """A length-prefixed identifier, possibly built out of earlier ones.

        Three forms, distinguished by what follows the first `0`:

        * `<n><n bytes>` -- plain.
        * `00<n>_<n bytes>` -- punycoded, for anything outside `[$_A-Za-z0-9]`.
        * `0<letters and lengths>` -- word substitutions. Each letter names a word of an
          earlier identifier; a lower-case one says more pieces follow, an upper-case one
          is the last. This is what makes a long Swift name short, and it is why an
          identifier cannot be read without the identifiers before it.
        """
        has_word_substitutions = False
        punycoded = False
        char = self.peek()
        if not _is_digit(char):
            return None
        if char == "0":
            self.pos += 1
            if self.peek() == "0":
                self.pos += 1
                punycoded = True
            else:
                has_word_substitutions = True

        pieces = []
        while True:
            while has_word_substitutions and _is_letter(self.peek()):
                char = self.next_char()
                if _is_lower(char):
                    at = ord(char) - ord("a")
                else:
                    at = ord(char) - ord("A")
                    has_word_substitutions = False
                if at >= len(self.words):
                    return None
                pieces.append(self.words[at])
            if self.next_if("0"):
                break
            count = self.natural()
            if count is None or count <= 0:
                return None
            if punycoded:
                self.next_if("_")
            if self.pos + count > self.end:
                return None
            slice_ = self.text[self.pos : self.pos + count]
            if punycoded:
                try:
                    pieces.append(punycode_decode(slice_))
                except PunycodeError:
                    return None
            else:
                pieces.append(slice_)
                self._record_words(slice_)
            self.pos += count
            if not has_word_substitutions:
                break

        identifier = "".join(pieces)
        if not identifier:
            return None
        node = Node("Identifier", text=identifier)
        self.add_substitution(node)
        return node

    def _record_words(self, slice_):
        """Split an identifier into the words a later one may refer back to.

        A word runs from a character that can start one to the next capital or `_`, and
        must be at least two characters. The table is capped at 26 because a reference to
        one is a single letter.
        """
        start = -1
        for at in range(len(slice_) + 1):
            char = slice_[at] if at < len(slice_) else ""
            if start >= 0 and _is_word_end(char, slice_[at - 1]):
                if at - start >= 2 and len(self.words) < _MAX_NUM_WORDS:
                    self.words.append(slice_[start:at])
                start = -1
            if start < 0 and _is_word_start(char):
                start = at

    #: `a` through `z` map to the operator characters an identifier may encode. A space
    #: means the letter is not one, which is a refusal rather than a guess.
    _OPERATOR_CHARS = "& @/= >    <*!|+?%-~   ^ ."

    def demangle_operator_identifier(self):
        """`o` -- an identifier that spells an operator, one letter per character."""
        identifier = self.pop("Identifier")
        if identifier is None:
            return None
        spelled = []
        for char in identifier.text:
            if ord(char) > 127:
                spelled.append(char)  # Unicode operators pass through.
                continue
            if not _is_lower(char):
                return None
            mapped = self._OPERATOR_CHARS[ord(char) - ord("a")]
            if mapped == " ":
                return None
            spelled.append(mapped)
        kind = {"i": "InfixOperator", "p": "PrefixOperator", "P": "PostfixOperator"}.get(self.next_char())
        return None if kind is None else Node(kind, text="".join(spelled))

    def demangle_local_identifier(self):
        """`L` -- a declaration not visible outside its file or function."""
        if self.next_if("L"):
            discriminator = self.pop("Identifier")
            name = self.pop(is_decl_name)
            return self.with_children("PrivateDeclName", discriminator, name)
        if self.next_if("l"):
            return self.with_child("PrivateDeclName", self.pop("Identifier"))
        char = self.peek()
        if ("a" <= char <= "j") or ("A" <= char <= "J"):
            self.pos += 1
            result = Node("RelatedEntityDeclName")
            self.add_child(result, Node("Identifier", text=char))
            return self.add_child(result, self.pop())
        return self.with_children("LocalDeclName", self.index_as_node(), self.pop(is_decl_name))

    # -- contexts --------------------------------------------------------------

    def pop_module(self):
        identifier = self.pop("Identifier")
        if identifier is not None:
            return self.change_kind(identifier, "Module")
        return self.pop("Module")

    def pop_context(self):
        module = self.pop_module()
        if module is not None:
            return module
        found = self.pop("Type")
        if found is not None:
            if len(found.children) != 1:
                return None
            child = found.first
            return child if child.kind in CONTEXT_KINDS else None
        return self.pop(lambda kind: kind in CONTEXT_KINDS)

    def pop_type_and_get_child(self):
        found = self.pop("Type")
        if found is None or len(found.children) != 1:
            return None
        return found.first

    def pop_type_and_get_any_generic(self):
        child = self.pop_type_and_get_child()
        return child if child is not None and is_any_generic(child.kind) else None

    def demangle_builtin_type(self):
        """`B` -- one of the primitives the compiler knows about directly."""
        max_size = 4096
        char = self.next_char()
        simple = _BUILTIN_SIMPLE.get(char)
        if simple is not None:
            return self.make_type(Node("BuiltinTypeName", text=simple))
        if char == "f" or char == "i":
            size = self.index()
            if size is None:
                return None
            size -= 1
            if size <= 0 or size > max_size:
                return None
            stem = "Builtin.FPIEEE" if char == "f" else "Builtin.Int"
            return self.make_type(Node("BuiltinTypeName", text=f"{stem}{size}"))
        if char == "v":
            count = self.index()
            if count is None:
                return None
            count -= 1
            if count <= 0 or count > max_size:
                return None
            element = self.pop_type_and_get_child()
            if (
                element is None
                or element.kind != "BuiltinTypeName"
                or not element.text.startswith(BUILTIN_TYPE_NAME_PREFIX)
            ):
                return None
            stem = element.text[len(BUILTIN_TYPE_NAME_PREFIX) :]
            return self.make_type(Node("BuiltinTypeName", text=f"Builtin.Vec{count}x{stem}"))
        if char == "T":
            return self.make_type(Node("BuiltinTupleType"))
        return None

    def demangle_any_generic_type(self, kind):
        name = self.pop(is_decl_name)
        context = self.pop_context()
        node = self.make_type(self.with_children(kind, context, name))
        self.add_substitution(node)
        return node

    def demangle_extension_context(self):
        signature = self.pop("DependentGenericSignature")
        module = self.pop_module()
        extended = self.pop_type_and_get_any_generic()
        extension = self.with_children("Extension", module, extended)
        if signature is not None:
            extension = self.add_child(extension, signature)
        return extension

    # -- functions -------------------------------------------------------------

    def demangle_plain_function(self):
        signature = self.pop("DependentGenericSignature")
        found = self.pop_function_type("FunctionType")
        labels = self.pop_function_param_labels(found)

        if signature is not None:
            found = self.make_type(self.with_children("DependentGenericType", signature, found))

        name = self.pop(is_decl_name)
        context = self.pop_context()

        if labels is not None:
            result = self.with_children("Function", context, name, labels, found)
        else:
            result = self.with_children("Function", context, name, found)
        return _parent_opaque_return_types(result, found)

    def pop_function_type(self, kind, has_clang_type=False):
        """Assemble a function type from the annotations and the two parameter lists.

        The order the annotations are popped in is the order they were pushed, reversed,
        and `popFunctionParamLabels` walks past them by that same order -- so the two
        lists have to agree. They do here because both are transcribed from the reference.
        """
        found = Node(kind)
        if has_clang_type:
            clang = self.demangle_clang_type()
            if clang is None:
                return None
            self.add_child(found, clang)
        for annotation in _FUNCTION_ANNOTATIONS:
            self.add_child(found, self.pop(annotation.__contains__))
        found = self.add_child(found, self.pop_function_params("ArgumentTuple"))
        found = self.add_child(found, self.pop_function_params("ReturnType"))
        return self.make_type(found)

    def pop_function_params(self, kind):
        # `y` -- the empty list -- means no parameters, which is spelled as an empty tuple.
        empty = self.pop("EmptyList") is not None
        params = self.make_type(Node("Tuple")) if empty else self.pop("Type")
        return self.with_child(kind, params)

    def pop_function_param_labels(self, found):
        """The argument labels, which are mangled *after* the type rather than with it.

        A label list is only produced when at least one parameter is labelled: an unlabelled
        signature carries none, and inventing an empty one would make the printer spell
        `f(_:_:)` where the reference spells `f`.
        """
        if not self.is_old_function_type_mangling and self.pop("EmptyList") is not None:
            return Node("LabelList")

        if found is None or found.kind != "Type":
            return None

        function = found.first
        if function.kind == "DependentGenericType":
            function = function.child(1).first
        if function.kind not in ("FunctionType", "NoEscapeFunctionType"):
            return None

        at = 0
        for annotation in _FUNCTION_ANNOTATIONS:
            if at < len(function.children) and function.child(at).kind in annotation:
                at += 1
        if at >= len(function.children):
            return None
        parameters = function.child(at)
        if parameters.kind != "ArgumentTuple":
            return None

        params = parameters.first.first
        count = len(params.children) if params.kind == "Tuple" else 1
        if count == 0:
            return None

        labels = Node("LabelList")
        tuple_ = parameters.first.first

        if self.is_old_function_type_mangling and (tuple_ is None or tuple_.kind != "Tuple"):
            return labels

        has_labels = False
        for at in range(count):
            label = self._function_param_label(tuple_, at)
            if label is None:
                return None
            if label.kind not in ("Identifier", "FirstElementMarker"):
                return None
            labels.add(label)
            has_labels |= label.kind != "FirstElementMarker"

        if not has_labels:
            return Node("LabelList")
        if not self.is_old_function_type_mangling:
            labels.children.reverse()
        return labels

    def _function_param_label(self, params, at):
        if not self.is_old_function_type_mangling:
            return self.pop()
        # Swift 4 put the label inside the tuple element; move it out.
        param = params.child(at)
        for index, child in enumerate(param.children):
            if child.kind == "TupleElementName":
                param.children.pop(index)
                return Node("Identifier", text=child.text)
        return Node("FirstElementMarker")

    # -- lists -----------------------------------------------------------------

    def _pop_list(self, root, element):
        """The shape every list in the mangling shares.

        Elements are mangled in order but read in reverse, and the *first* is marked
        rather than the last, so the loop runs until it sees the marker and then reverses.
        """
        if self.pop("EmptyList") is None:
            while True:
                first = self.pop("FirstElementMarker") is not None
                if not element(root):
                    return None
                if first:
                    break
            root.children.reverse()
        return root

    def pop_tuple(self):
        def element(root):
            item = Node("TupleElement")
            self.add_child(item, self.pop("VariadicMarker"))
            identifier = self.pop("Identifier")
            if identifier is not None:
                item.add(Node("TupleElementName", text=identifier.text))
            found = self.pop("Type")
            if found is None:
                return False
            item.add(found)
            root.add(item)
            return True

        return self.make_type_or_none(self._pop_list(Node("Tuple"), element))

    def pop_pack(self):
        return self.make_type_or_none(self._pop_list(Node("Pack"), self._type_element))

    def pop_sil_pack(self):
        kind = {"d": "SILPackDirect", "i": "SILPackIndirect"}.get(self.next_char())
        if kind is None:
            return None
        return self.make_type_or_none(self._pop_list(Node(kind), self._type_element))

    def pop_type_list(self):
        return self._pop_list(Node("TypeList"), self._type_element)

    def _type_element(self, root):
        found = self.pop("Type")
        if found is None:
            return False
        root.add(found)
        return True

    def make_type_or_none(self, node):
        return None if node is None else self.make_type(node)

    def pop_protocol(self):
        found = self.pop("Type")
        if found is not None:
            if not found.children or not _is_protocol_node(found):
                return None
            return found
        symbolic = self.pop("ProtocolSymbolicReference")
        if symbolic is not None:
            return symbolic
        name = self.pop(is_decl_name)
        context = self.pop_context()
        return self.make_type_or_none(self.with_children("Protocol", context, name))

    # -- protocol conformances -------------------------------------------------

    def demangle_retroactive_protocol_conformance_ref(self):
        module = self.pop_module()
        protocol = self.pop_protocol()
        return self.with_children("ProtocolConformanceRefInOtherModule", protocol, module)

    def demangle_concrete_protocol_conformance(self):
        conditional = self.pop_any_protocol_conformance_list()
        reference = self.pop("ProtocolConformanceRefInTypeModule")
        if reference is None:
            reference = self.pop("ProtocolConformanceRefInProtocolModule")
        if reference is None:
            reference = self.demangle_retroactive_protocol_conformance_ref()
        found = self.pop("Type")
        return self.with_children("ConcreteProtocolConformance", found, reference, conditional)

    def pop_any_protocol_conformance(self):
        return self.pop(_is_any_protocol_conformance)

    def pop_any_protocol_conformance_list(self):
        def element(root):
            conformance = self.pop_any_protocol_conformance()
            if conformance is None:
                return False
            root.add(conformance)
            return True

        return self._pop_list(Node("AnyProtocolConformanceList"), element)

    def pop_dependent_protocol_conformance(self):
        return self.pop(lambda kind: kind != "ConcreteProtocolConformance" and _is_any_protocol_conformance(kind))

    def demangle_dependent_protocol_conformance_root(self):
        index = self.demangle_dependent_conformance_index()
        protocol = self.pop_protocol()
        dependent = self.pop("Type")
        return self.with_children("DependentProtocolConformanceRoot", dependent, protocol, index)

    def demangle_dependent_protocol_conformance_inherited(self):
        index = self.demangle_dependent_conformance_index()
        protocol = self.pop_protocol()
        nested = self.pop_dependent_protocol_conformance()
        return self.with_children("DependentProtocolConformanceInherited", nested, protocol, index)

    def pop_dependent_associated_conformance(self):
        protocol = self.pop_protocol()
        dependent = self.pop("Type")
        return self.with_children("DependentAssociatedConformance", dependent, protocol)

    def demangle_dependent_protocol_conformance_associated(self):
        index = self.demangle_dependent_conformance_index()
        associated = self.pop_dependent_associated_conformance()
        nested = self.pop_dependent_protocol_conformance()
        return self.with_children("DependentProtocolConformanceAssociated", nested, associated, index)

    def demangle_dependent_conformance_index(self):
        """The index is offset by two: 0 was never valid and 1 means "unknown"."""
        index = self.index()
        if index is None or index <= 0:
            return None
        if index == 1:
            return Node("UnknownIndex")
        return Node("Index", index=index - 2)

    def demangle_retroactive_conformance(self):
        index = self.index_as_node()
        conformance = self.pop_any_protocol_conformance()
        return self.with_children("RetroactiveConformance", index, conformance)

    def pop_retroactive_conformances(self):
        found = None
        while True:
            conformance = self.pop("RetroactiveConformance")
            if conformance is None:
                break
            if found is None:
                found = Node("TypeList")
            found.add(conformance)
        if found is not None:
            found.children.reverse()
        return found

    # -- bound generics --------------------------------------------------------

    def demangle_bound_generics(self):
        """The argument lists, outermost last, and any retroactive conformances.

        A nested generic type mangles one list per level, `_`-separated, so
        `Outer<A>.Inner<B>` carries two. They come back innermost first, which is why
        `demangle_bound_generic_args` walks the list backwards.
        """
        conformances = self.pop_retroactive_conformances()
        lists = []
        while True:
            found = Node("TypeList")
            lists.append(found)
            while True:
                item = self.pop("Type")
                if item is None:
                    break
                found.add(item)
            found.children.reverse()
            if self.pop("EmptyList") is not None:
                break
            if self.pop("FirstElementMarker") is None:
                return None, None
        return lists, conformances

    def demangle_bound_generic_type(self):
        lists, conformances = self.demangle_bound_generics()
        if lists is None:
            return None
        nominal = self.pop_type_and_get_any_generic()
        if nominal is None:
            return None
        bound = self.demangle_bound_generic_args(nominal, lists, 0)
        if bound is None:
            return None
        self.add_child(bound, conformances)
        found = self.make_type(bound)
        self.add_substitution(found)
        return found

    def demangle_bound_generic_args(self, nominal, lists, at):
        """Distribute the argument lists over the nesting levels of `nominal`.

        The mangling is flat and the tree is not: the arguments belong to whichever
        enclosing type declared the parameters, and a context that declares none -- a
        closure, an initialiser -- has to be skipped without consuming a list.
        """
        if nominal is None or at >= len(lists):
            return None

        if nominal.kind in ("TypeSymbolicReference", "ProtocolSymbolicReference"):
            remaining = Node("TypeList")
            for each in reversed(lists[at:]):
                remaining.children.extend(each.children)
            return self.with_children("BoundGenericOtherNominalType", self.make_type(nominal), remaining)

        if not nominal.children:
            return None
        context = nominal.first
        consumes = nominal.kind not in _DOES_NOT_CONSUME_GENERIC_ARGS
        args = lists[at]
        if consumes:
            at += 1

        if at < len(lists):
            if context.kind == "Extension":
                bound_parent = self.demangle_bound_generic_args(context.child(1), lists, at)
                bound_parent = self.with_children("Extension", context.first, bound_parent)
                if len(context.children) == 3:
                    self.add_child(bound_parent, context.child(2))
            else:
                bound_parent = self.demangle_bound_generic_args(context, lists, at)
            rebuilt = self.with_child(nominal.kind, bound_parent)
            if rebuilt is None:
                return None
            for child in nominal.children[1:]:
                self.add_child(rebuilt, child)
            nominal = rebuilt

        if not consumes or not args.children:
            return nominal

        if nominal.kind in ("Function", "Constructor"):
            # Not a nominal type at all, but it can still be generic.
            return self.with_children("BoundGenericFunction", nominal, args)
        kind = _BOUND_GENERIC_KINDS.get(nominal.kind)
        if kind is None:
            return None
        return self.with_children(kind, self.make_type(nominal), args)

    # -- SIL-level function types ----------------------------------------------

    def demangle_impl_param_convention(self, kind):
        attribute = _IMPL_PARAM_CONVENTIONS.get(self.next_char())
        if attribute is None:
            self.push_back()
            return None
        return self.with_child(kind, Node("ImplConvention", text=attribute))

    def demangle_impl_result_convention(self, kind):
        attribute = _IMPL_RESULT_CONVENTIONS.get(self.next_char())
        if attribute is None:
            self.push_back()
            return None
        return self.with_child(kind, Node("ImplConvention", text=attribute))

    def demangle_impl_differentiability(self):
        attribute = "@noDerivative" if self.next_if("w") else ""
        return Node("ImplParameterResultDifferentiability", text=attribute)

    def demangle_clang_type(self):
        count = self.natural()
        if count is None or count <= 0 or self.pos + count > self.end:
            return None
        found = self.text[self.pos : self.pos + count]
        self.pos += count
        return Node("ClangType", text=found)

    def demangle_impl_function_type(self):
        """`I` -- a lowered SIL function type, spelled `@convention(...) (...) -> ...`.

        The conventions come first and their *types* come last, off the stack, so the
        loop at the end walks the children it just added in reverse and fills each one in.
        """
        found = Node("ImplFunctionType")

        if self.next_if("s"):
            lists, conformances = self.demangle_bound_generics()
            if lists is None or len(lists) != 1:
                return None
            signature = self.pop("DependentGenericSignature")
            if signature is None:
                return None
            substitutions = Node("ImplPatternSubstitutions", children=[signature, lists[0]])
            if conformances is not None:
                substitutions.add(conformances)
            found.add(substitutions)

        if self.next_if("I"):
            lists, conformances = self.demangle_bound_generics()
            if lists is None or len(lists) != 1:
                return None
            substitutions = Node("ImplInvocationSubstitutions", children=[lists[0]])
            if conformances is not None:
                substitutions.add(conformances)
            found.add(substitutions)

        signature = self.pop("DependentGenericSignature")
        if signature is not None and self.next_if("P"):
            signature = self.change_kind(signature, "DependentPseudogenericSignature")

        if self.next_if("e"):
            found.add(Node("ImplEscaping"))

        if self.peek() in ("d", "l", "f", "r"):
            found.add(Node("ImplDifferentiabilityKind", index=ord(self.next_char())))

        callee = _IMPL_CALLEE_CONVENTIONS.get(self.next_char())
        if callee is None:
            return None
        found.add(Node("ImplConvention", text=callee))

        convention = None
        has_clang_type = False
        char = self.next_char()
        if char == "z":
            inner = self.next_char()
            if inner in ("B", "C"):
                has_clang_type = True
                convention = "block" if inner == "B" else "c"
            else:
                self.push_back()
                self.push_back()
        else:
            convention = _IMPL_FUNCTION_CONVENTIONS.get(char)
            if convention is None:
                self.push_back()
        if convention is not None:
            attribute = Node("ImplFunctionConvention", children=[Node("ImplFunctionConventionName", text=convention)])
            if has_clang_type:
                self.add_child(attribute, self.demangle_clang_type())
            found.add(attribute)

        coroutine = "@yield_once" if self.next_if("A") else "@yield_many" if self.next_if("G") else None
        if coroutine is not None:
            found.add(Node("ImplFunctionAttribute", text=coroutine))
        if self.next_if("h"):
            found.add(Node("ImplFunctionAttribute", text="@Sendable"))
        if self.next_if("H"):
            found.add(Node("ImplFunctionAttribute", text="@async"))

        self.add_child(found, signature)

        pending = 0
        while True:
            parameter = self.demangle_impl_param_convention("ImplParameter")
            if parameter is None:
                break
            found = self.add_child(found, parameter)
            self.add_child(parameter, self.demangle_impl_differentiability())
            pending += 1
        while True:
            result = self.demangle_impl_result_convention("ImplResult")
            if result is None:
                break
            found = self.add_child(found, result)
            self.add_child(result, self.demangle_impl_differentiability())
            pending += 1
        while self.next_if("Y"):
            yielded = self.demangle_impl_param_convention("ImplYield")
            if yielded is None:
                return None
            found = self.add_child(found, yielded)
            pending += 1
        if self.next_if("z"):
            error = self.demangle_impl_result_convention("ImplErrorResult")
            if error is None:
                return None
            found = self.add_child(found, error)
            pending += 1
        if not self.next_if("_"):
            return None

        for at in range(pending):
            item = self.pop("Type")
            if item is None:
                return None
            found.children[len(found.children) - at - 1].add(item)

        return self.make_type(found)

    # -- metadata --------------------------------------------------------------

    def demangle_metatype(self):
        """`M` -- one of the many runtime records that describe a type."""
        char = self.next_char()
        simple = _METATYPE_POPPED_TYPE.get(char)
        if simple is not None:
            return self.with_popped_type(simple)
        popped_any = _METATYPE_POPPED_NODE.get(char)
        if popped_any is not None:
            return self.with_child(popped_any, self.pop())
        if char == "A":
            return self.with_child("ReflectionMetadataAssocTypeDescriptor", self.pop_protocol_conformance())
        if char == "B":
            return self.with_child("ReflectionMetadataBuiltinDescriptor", self.pop("Type"))
        if char == "c":
            return self.with_child("ProtocolConformanceDescriptor", self.pop_protocol_conformance())
        if char == "C":
            found = self.pop("Type")
            if found is None or not found.children or not is_any_generic(found.first.kind):
                return None
            return self.with_child("ReflectionMetadataSuperclassDescriptor", found.first)
        if char == "F":
            return self.with_child("ReflectionMetadataFieldDescriptor", self.pop("Type"))
        if char == "p":
            return self.with_child("ProtocolDescriptor", self.pop_protocol())
        if char == "S":
            return self.with_child("ProtocolSelfConformanceDescriptor", self.pop_protocol())
        if char == "V":
            return self.with_child("PropertyDescriptor", self.pop(is_entity))
        if char == "X":
            return self.demangle_private_context_descriptor()
        return None

    def demangle_private_context_descriptor(self):
        char = self.next_char()
        if char == "E":
            return self.with_child("ExtensionDescriptor", self.pop_context())
        if char == "M":
            return self.with_child("ModuleDescriptor", self.pop_module())
        if char == "Y":
            discriminator = self.pop()
            if discriminator is None:
                return None
            context = self.pop_context()
            if context is None:
                return None
            return Node("AnonymousDescriptor", children=[context, discriminator])
        if char == "X":
            return self.with_child("AnonymousDescriptor", self.pop_context())
        if char == "A":
            path = self.pop_assoc_type_path()
            if path is None:
                return None
            base = self.pop("Type")
            if base is None:
                return None
            return self.with_children("AssociatedTypeGenericParamRef", base, path)
        return None

    # -- archetypes and dependent types ----------------------------------------

    def demangle_archetype(self):
        """`Q` -- a generic parameter, an associated type, or an opaque return type."""
        char = self.next_char()
        if char == "a":
            identifier = self.pop("Identifier")
            archetype = self.pop_type_and_get_child()
            associated = self.make_type(self.with_children("AssociatedTypeRef", archetype, identifier))
            self.add_substitution(associated)
            return associated
        if char == "O":
            return self.with_child("OpaqueReturnTypeOf", self.pop_context())
        if char == "o":
            index = self.index()
            lists, conformances = self.demangle_bound_generics()
            if lists is None:
                return None
            name = self.pop()
            if name is None or index is None:
                return None
            opaque = self.with_children("OpaqueType", name, Node("Index", index=index))
            if opaque is None:
                return None
            bound = Node("TypeList")
            for each in reversed(lists):
                bound.add(each)
            opaque.add(bound)
            if conformances is not None:
                opaque.add(conformances)
            found = self.make_type(opaque)
            self.add_substitution(found)
            return found
        if char == "r":
            return self.make_type(Node("OpaqueReturnType"))
        if char == "R":
            ordinal = self.index()
            if ordinal is None:
                return None
            return self.make_type(self.with_child("OpaqueReturnType", Node("OpaqueReturnTypeIndex", index=ordinal)))
        if char in ("x", "X", "y", "Y", "z", "Z"):
            base = None
            if char in ("y", "Y"):
                base = self.demangle_generic_param_index()
            elif char in ("z", "Z"):
                base = self.dependent_generic_param_type(0, 0)
            if char in ("y", "Y", "z", "Z") and base is None:
                return None
            if char in ("x", "y", "z"):
                found = self.demangle_associated_type_simple(base)
            else:
                found = self.demangle_associated_type_compound(base)
            self.add_substitution(found)
            return found
        if char == "p":
            count = self.pop_type_and_get_child()
            pattern = self.pop_type_and_get_child()
            return self.make_type_or_none(self.with_children("PackExpansion", pattern, count))
        if char == "e":
            pack = self.pop_type_and_get_child()
            level = self.index()
            if level is None:
                return None
            return self.make_type_or_none(
                self.with_children("PackElement", pack, Node("PackElementLevel", index=level))
            )
        if char == "P":
            return self.pop_pack()
        if char == "S":
            return self.pop_sil_pack()
        return None

    def demangle_associated_type_simple(self, base):
        name = self.pop_assoc_type_name()
        base_type = self.make_type(base) if base is not None else self.pop("Type")
        return self.make_type_or_none(self.with_children("DependentMemberType", base_type, name))

    def demangle_associated_type_compound(self, base):
        names = []
        while True:
            first = self.pop("FirstElementMarker") is not None
            name = self.pop_assoc_type_name()
            if name is None:
                return None
            names.append(name)
            if first:
                break
        base_type = self.make_type(base) if base is not None else self.pop("Type")
        while names:
            member = self.with_children("DependentMemberType", base_type, names.pop())
            base_type = self.make_type_or_none(member)
        return base_type

    def pop_assoc_type_name(self):
        protocol = self.pop("Type")
        if protocol is not None and not _is_protocol_node(protocol):
            return None
        if protocol is None:
            protocol = self.pop("ProtocolSymbolicReference")
        identifier = self.pop("Identifier")
        associated = self.with_child("DependentAssociatedTypeRef", identifier)
        self.add_child(associated, protocol)
        return associated

    def pop_assoc_type_path(self):
        def element(root):
            name = self.pop_assoc_type_name()
            if name is None:
                return False
            root.add(name)
            return True

        path = Node("AssocTypePath")
        while True:
            first = self.pop("FirstElementMarker") is not None
            if not element(path):
                return None
            if first:
                break
        path.children.reverse()
        return path

    def dependent_generic_param_type(self, depth, index):
        if depth is None or index is None or depth < 0 or index < 0:
            return None
        return Node("DependentGenericParamType", children=[Node("Index", index=depth), Node("Index", index=index)])

    def demangle_generic_param_index(self):
        """`x` is `<0, 0>`; `d` gives both coordinates; a bare index is depth 0.

        Depth and index are both biased so that the commonest parameter -- the first of
        the innermost signature -- costs a single character.
        """
        if self.next_if("d"):
            depth = self.index()
            index = self.index()
            return self.dependent_generic_param_type(None if depth is None else depth + 1, index)
        if self.next_if("z"):
            return self.dependent_generic_param_type(0, 0)
        if self.next_if("s"):
            return Node("ConstrainedExistentialSelf")
        index = self.index()
        return self.dependent_generic_param_type(0, None if index is None else index + 1)

    def pop_protocol_conformance(self):
        signature = self.pop("DependentGenericSignature")
        module = self.pop_module()
        protocol = self.pop_protocol()
        found = self.pop("Type")
        identifier = None
        if found is None:
            # A property-behaviour conformance names the property as well.
            identifier = self.pop("Identifier")
            found = self.pop("Type")
        if signature is not None:
            found = self.make_type_or_none(self.with_children("DependentGenericType", signature, found))
        conformance = self.with_children("ProtocolConformance", found, protocol, module)
        self.add_child(conformance, identifier)
        return conformance

    # -- thunks and specialisations --------------------------------------------

    def demangle_thunk_or_specialization(self):
        """`T` -- the largest operator, covering everything the compiler synthesises."""
        char = self.next_char()
        plain = _THUNK_PLAIN.get(char)
        if plain is not None:
            return Node(plain)
        entity = _THUNK_OF_ENTITY.get(char)
        if entity is not None:
            return self.with_child(entity, self.pop(is_entity))
        specialization = _GENERIC_SPECIALIZATIONS.get(char)
        if specialization is not None:
            return self.demangle_generic_specialization(specialization)

        if char in ("Y", "Q"):
            kind = "AsyncAwaitResumePartialFunction" if char == "Q" else "AsyncSuspendResumePartialFunction"
            return self.with_child(kind, self.index_as_node())
        if char == "C":
            return self.with_child("CoroutineContinuationPrototype", self.pop("Type"))
        if char in ("z", "Z"):
            flags = self.index_as_node()
            signature = self.pop("DependentGenericSignature")
            result = self.pop("Type")
            implementation = self.pop("Type")
            kind = "ObjCAsyncCompletionHandlerImpl" if char == "z" else "PredefinedObjCAsyncCompletionHandlerImpl"
            node = self.with_children(kind, implementation, result, flags)
            if signature is not None:
                self.add_child(node, signature)
            return node
        if char == "V":
            base = self.pop(is_entity)
            derived = self.pop(is_entity)
            return self.with_children("VTableThunk", derived, base)
        if char == "W":
            witnessed = self.pop(is_entity)
            conformance = self.pop_protocol_conformance()
            return self.with_children("ProtocolWitness", conformance, witnessed)
        if char in ("R", "r", "y"):
            kind = {
                "R": "ReabstractionThunkHelper",
                "y": "ReabstractionThunkHelperWithSelf",
                "r": "ReabstractionThunk",
            }[char]
            thunk = Node(kind)
            self.add_child(thunk, self.pop("DependentGenericSignature"))
            if kind == "ReabstractionThunkHelperWithSelf":
                self.add_child(thunk, self.pop("Type"))
            self.add_child(thunk, self.pop("Type"))
            self.add_child(thunk, self.pop("Type"))
            return thunk
        if char in ("p", "P"):
            kind = "GenericPartialSpecialization" if char == "p" else "GenericPartialSpecializationNotReAbstracted"
            specialized = self.demangle_spec_attributes(kind)
            parameter = self.with_child("GenericSpecializationParam", self.pop("Type"))
            return self.add_child(specialized, parameter)
        if char == "f":
            return self.demangle_function_specialization()
        if char in ("K", "k"):
            return self._key_path_accessor_thunk(char)
        if char in ("H", "h"):
            return self._key_path_equality_thunk(char)
        if char == "l":
            return self.with_child("AssociatedTypeDescriptor", self.pop_assoc_type_name())
        if char == "L":
            return self.with_child("ProtocolRequirementsBaseDescriptor", self.pop_protocol())
        if char == "M":
            return self.with_child("DefaultAssociatedTypeMetadataAccessor", self.pop_assoc_type_name())
        if char in ("n", "N"):
            requirement = self.pop_protocol()
            path = self.pop_assoc_type_path()
            protocol = self.pop("Type")
            kind = "AssociatedConformanceDescriptor" if char == "n" else "DefaultAssociatedConformanceAccessor"
            return self.with_children(kind, protocol, path, requirement)
        if char == "b":
            requirement = self.pop_protocol()
            protocol = self.pop("Type")
            return self.with_children("BaseConformanceDescriptor", protocol, requirement)
        if char == "v":
            index = self.index()
            if index is None:
                return None
            kind = "OutlinedReadOnlyObject" if self.next_char() == "r" else "OutlinedVariable"
            return Node(kind, index=index)
        if char == "e":
            params = self.demangle_bridged_method_params()
            return None if not params else Node("OutlinedBridgedMethod", text=params)
        if char == "U":
            actor = self.pop("Type")
            if actor is None:
                return None
            reabstraction = self.pop()
            if reabstraction is None:
                return None
            return Node("ReabstractionThunkHelperWithGlobalActor", children=[reabstraction, actor])
        if char == "J":
            following = self.peek()
            if following == "S":
                self.pos += 1
                return self.demangle_auto_diff_subset_parameters_thunk()
            if following == "O":
                self.pos += 1
                return self.demangle_auto_diff_self_reordering_reabstraction_thunk()
            if following == "V":
                self.pos += 1
                return self.demangle_auto_diff_function_or_simple_thunk("AutoDiffDerivativeVTableThunk")
            return self.demangle_auto_diff_function_or_simple_thunk("AutoDiffFunction")
        if char == "w":
            return {
                "b": lambda: Node("BackDeploymentThunk"),
                "B": lambda: Node("BackDeploymentFallback"),
                "S": lambda: Node("HasSymbolQuery"),
            }.get(self.next_char(), lambda: None)()
        return None

    def _key_path_accessor_thunk(self, char):
        kind = "KeyPathGetterThunkHelper" if char == "K" else "KeyPathSetterThunkHelper"
        serialized = self.next_if("q")
        types = []
        node = self.pop()
        if node is None or node.kind != "Type":
            return None
        while node is not None and node.kind == "Type":
            types.append(node)
            node = self.pop()
        if node is None:
            return None
        if node.kind == "DependentGenericSignature":
            declaration = self.pop()
            if declaration is None:
                return None
            result = self.with_children(kind, declaration, node)
        else:
            result = self.with_child(kind, node)
        for found in reversed(types):
            result.add(found)
        if serialized:
            result.add(Node("IsSerialized"))
        return result

    def _key_path_equality_thunk(self, char):
        kind = "KeyPathEqualsThunkHelper" if char == "H" else "KeyPathHashThunkHelper"
        serialized = self.next_if("q")
        signature = None
        types = []
        node = self.pop()
        if node is None:
            return None
        if node.kind == "DependentGenericSignature":
            signature = node
        elif node.kind == "Type":
            types.append(node)
        else:
            return None
        while True:
            node = self.pop()
            if node is None:
                break
            if node.kind != "Type":
                return None
            types.append(node)
        result = Node(kind)
        for found in reversed(types):
            result.add(found)
        if signature is not None:
            result.add(signature)
        if serialized:
            result.add(Node("IsSerialized"))
        return result

    def demangle_auto_diff_function_or_simple_thunk(self, kind):
        result = Node(kind)
        while True:
            node = self.pop()
            if node is None:
                break
            result = self.add_child(result, node)
        result.children.reverse()
        result = self.add_child(result, self.demangle_auto_diff_function_kind())
        result = self.add_child(result, self.demangle_index_subset())
        if not self.next_if("p"):
            return None
        result = self.add_child(result, self.demangle_index_subset())
        if not self.next_if("r"):
            return None
        return result

    def demangle_auto_diff_function_kind(self):
        kind = self.next_char()
        if kind not in ("f", "r", "d", "p"):
            return None
        return Node("AutoDiffFunctionKind", index=ord(kind))

    def demangle_auto_diff_subset_parameters_thunk(self):
        result = Node("AutoDiffSubsetParametersThunk")
        while True:
            node = self.pop()
            if node is None:
                break
            result = self.add_child(result, node)
        result.children.reverse()
        result = self.add_child(result, self.demangle_auto_diff_function_kind())
        result = self.add_child(result, self.demangle_index_subset())
        if not self.next_if("p"):
            return None
        result = self.add_child(result, self.demangle_index_subset())
        if not self.next_if("r"):
            return None
        result = self.add_child(result, self.demangle_index_subset())
        if not self.next_if("P"):
            return None
        return result

    def demangle_auto_diff_self_reordering_reabstraction_thunk(self):
        result = Node("AutoDiffSelfReorderingReabstractionThunk")
        self.add_child(result, self.pop("DependentGenericSignature"))
        result = self.add_child(result, self.pop("Type"))
        result = self.add_child(result, self.pop("Type"))
        if result is not None:
            result.children.reverse()
        return self.add_child(result, self.demangle_auto_diff_function_kind())

    def demangle_differentiability_witness(self):
        result = Node("DifferentiabilityWitness")
        signature = self.pop("DependentGenericSignature")
        while True:
            node = self.pop()
            if node is None:
                break
            result = self.add_child(result, node)
        result.children.reverse()
        kind = self.next_char()
        if kind not in ("f", "r", "d", "l"):
            return None
        result = self.add_child(result, Node("Index", index=ord(kind)))
        result = self.add_child(result, self.demangle_index_subset())
        if not self.next_if("p"):
            return None
        result = self.add_child(result, self.demangle_index_subset())
        if not self.next_if("r"):
            return None
        self.add_child(result, signature)
        return result

    def demangle_index_subset(self):
        start = self.pos
        while self.peek() in ("S", "U"):
            self.pos += 1
        if self.pos == start:
            return None
        return Node("IndexSubset", text=self.text[start : self.pos])

    def demangle_differentiable_function_type(self):
        kind = self.next_char()
        if kind not in ("f", "r", "d", "l"):
            return None
        return Node("DifferentiableFunctionType", index=ord(kind))

    def demangle_bridged_method_params(self):
        if self.next_if("_"):
            return ""
        kind = self.next_char()
        if kind not in ("o", "p", "a", "m"):
            return ""
        found = [kind]
        while not self.next_if("_"):
            char = self.next_char()
            if char not in ("n", "b", "g"):
                return ""
            found.append(char)
        return "".join(found)

    def demangle_generic_specialization(self, kind):
        specialized = self.demangle_spec_attributes(kind)
        if specialized is None:
            return None
        types = self.pop_type_list()
        if types is None:
            return None
        for found in types.children:
            specialized.add(self.with_child("GenericSpecializationParam", found))
        return specialized

    def demangle_function_specialization(self):
        """`Tf` -- a copy of a function specialised for particular argument values.

        The parameter *kinds* are mangled inline but their payloads -- a closure's name,
        a propagated constant -- are on the stack, so a second pass walks the parameters
        backwards and fills each one in from what is left.
        """
        specialized = self.demangle_spec_attributes("FunctionSignatureSpecialization")
        while specialized is not None and not self.next_if("_"):
            specialized = self.add_child(
                specialized, self.demangle_func_spec_param("FunctionSignatureSpecializationParam")
            )
        if not self.next_if("n"):
            specialized = self.add_child(
                specialized, self.demangle_func_spec_param("FunctionSignatureSpecializationReturn")
            )
        if specialized is None:
            return None

        for parameter in reversed(specialized.children):
            if parameter.kind != "FunctionSignatureSpecializationParam" or not parameter.children:
                continue
            kind = parameter.first.index
            if kind not in _PARAM_KINDS_WITH_PAYLOAD:
                continue
            fixed = len(parameter.children)
            while True:
                found = self.pop("Type")
                if found is None:
                    break
                if kind not in (_PARAM_CLOSURE_PROP, _PARAM_CONSTANT_PROP_KEY_PATH):
                    return None
                parameter = self.add_child(parameter, found)
            name = self.pop("Identifier")
            if name is None:
                return None
            text = name.text
            if kind == _PARAM_CONSTANT_PROP_STRING and text.startswith("_"):
                # `_` escapes a string constant that would otherwise start with a digit.
                text = text[1:]
            self.add_child(parameter, Node("FunctionSignatureSpecializationParamPayload", text=text))
            parameter.children[fixed:] = reversed(parameter.children[fixed:])
        return specialized

    def demangle_func_spec_param(self, kind):
        parameter = Node(kind)
        char = self.next_char()
        if char == "n":
            return parameter
        if char == "c":
            return self.add_child(parameter, _param_kind(_PARAM_CLOSURE_PROP))
        if char == "p":
            inner = self.next_char()
            if inner == "f":
                return self.add_child(parameter, _param_kind(_PARAM_CONSTANT_PROP_FUNCTION))
            if inner == "g":
                return self.add_child(parameter, _param_kind(_PARAM_CONSTANT_PROP_GLOBAL))
            if inner == "i":
                return self.add_func_spec_param_number(parameter, _PARAM_CONSTANT_PROP_INTEGER)
            if inner == "d":
                return self.add_func_spec_param_number(parameter, _PARAM_CONSTANT_PROP_FLOAT)
            if inner == "s":
                encoding = {"b": "u8", "w": "u16", "c": "objc"}.get(self.next_char())
                if encoding is None:
                    return None
                self.add_child(parameter, _param_kind(_PARAM_CONSTANT_PROP_STRING))
                return self.add_child(parameter, Node("FunctionSignatureSpecializationParamPayload", text=encoding))
            if inner == "k":
                return self.add_child(parameter, _param_kind(_PARAM_CONSTANT_PROP_KEY_PATH))
            return None
        flags = _PARAM_FLAG_SETS.get(char)
        if flags is None:
            return None
        value, options = flags
        for letter, flag in options:
            if self.next_if(letter):
                value |= flag
        return self.add_child(parameter, _param_kind(value))

    def add_func_spec_param_number(self, parameter, kind):
        parameter.add(_param_kind(kind))
        start = self.pos
        while _is_digit(self.peek()):
            self.pos += 1
        if self.pos == start:
            return None
        return self.add_child(
            parameter,
            Node("FunctionSignatureSpecializationParamPayload", text=self.text[start : self.pos]),
        )

    def demangle_spec_attributes(self, kind):
        metatype_params_removed = self.next_if("m")
        serialized = self.next_if("q")
        pass_id = ord(self.next_char() or "\x00") - ord("0")
        if pass_id < 0 or pass_id > 9:
            return None
        specialized = Node(kind)
        if metatype_params_removed:
            specialized.add(Node("MetatypeParamsRemoved"))
        if serialized:
            specialized.add(Node("IsSerialized"))
        specialized.add(Node("SpecializationPassID", index=pass_id))
        return specialized

    # -- witnesses -------------------------------------------------------------

    def demangle_witness(self):
        """`W` -- the tables and accessors that implement a conformance."""
        char = self.next_char()
        conformance_kind = _WITNESS_OF_CONFORMANCE.get(char)
        if conformance_kind is not None:
            return self.with_child(conformance_kind, self.pop_protocol_conformance())
        if char == "C":
            return self.with_child("EnumCase", self.pop(is_entity))
        if char == "V":
            return self.with_child("ValueWitnessTable", self.pop("Type"))
        if char == "v":
            directness = {"d": 0, "i": 1}.get(self.next_char())
            if directness is None:
                return None
            return self.with_children("FieldOffset", Node("Directness", index=directness), self.pop(is_entity))
        if char == "S":
            return self.with_child("ProtocolSelfConformanceWitnessTable", self.pop_protocol())
        if char in ("l", "L"):
            kind = "LazyProtocolWitnessTableAccessor" if char == "l" else "LazyProtocolWitnessTableCacheVariable"
            conformance = self.pop_protocol_conformance()
            found = self.pop("Type")
            return self.with_children(kind, found, conformance)
        if char == "t":
            name = self.pop(is_decl_name)
            conformance = self.pop_protocol_conformance()
            return self.with_children("AssociatedTypeMetadataAccessor", conformance, name)
        if char == "T":
            protocol = self.pop("Type")
            conforming = self.pop_assoc_type_path()
            conformance = self.pop_protocol_conformance()
            return self.with_children("AssociatedTypeWitnessTableAccessor", conformance, conforming, protocol)
        if char == "b":
            protocol = self.pop("Type")
            conformance = self.pop_protocol_conformance()
            return self.with_children("BaseWitnessTableAccessor", conformance, protocol)
        if char == "O":
            kind = _OUTLINED_VALUE_WITNESSES.get(self.next_char())
            if kind is None:
                return None
            signature = self.pop("DependentGenericSignature")
            if signature is not None:
                return self.with_children(kind, self.pop("Type"), signature)
            return self.with_child(kind, self.pop("Type"))
        if char in ("Z", "z"):
            declarations = Node("GlobalVariableOnceDeclList")
            named = []
            while self.pop("FirstElementMarker") is not None:
                identifier = self.pop(is_decl_name)
                if identifier is None:
                    return None
                named.append(identifier)
            for identifier in reversed(named):
                declarations.add(identifier)
            context = self.pop_context()
            if context is None:
                return None
            kind = "GlobalVariableOnceFunction" if char == "Z" else "GlobalVariableOnceToken"
            return self.with_children(kind, context, declarations)
        if char == "J":
            return self.demangle_differentiability_witness()
        return None

    # -- special types ---------------------------------------------------------

    def demangle_special_type(self):
        """`X` -- types with a spelling of their own rather than a name."""
        char = self.next_char()
        function_kind = _SPECIAL_FUNCTION_TYPES.get(char)
        if function_kind is not None:
            return self.pop_function_type(function_kind)
        wrapper = _SPECIAL_TYPE_WRAPPERS.get(char)
        if wrapper is not None:
            return self.make_type_or_none(self.with_child(wrapper, self.pop("Type")))
        if char in ("g", "G"):
            return self.demangle_extended_existential_shape(char)
        if char == "j":
            return self.demangle_symbolic_extended_existential_type()
        if char == "z":
            inner = self.next_char()
            if inner == "B":
                return self.pop_function_type("ObjCBlock", True)
            if inner == "C":
                return self.pop_function_type("CFunctionPointer", True)
            return None
        if char in ("M", "m"):
            kind = "Metatype" if char == "M" else "ExistentialMetatype"
            representation = self.demangle_metatype_representation()
            found = self.pop("Type")
            return self.make_type_or_none(self.with_children(kind, representation, found))
        if char == "P":
            requirements = self.demangle_constrained_existential_requirement_list()
            base = self.pop("Type")
            return self.make_type_or_none(self.with_children("ConstrainedExistential", base, requirements))
        if char == "c":
            superclass = self.pop("Type")
            protocols = self.demangle_protocol_list()
            return self.make_type_or_none(self.with_children("ProtocolListWithClass", protocols, superclass))
        if char == "l":
            return self.make_type_or_none(self.with_child("ProtocolListWithAnyObject", self.demangle_protocol_list()))
        if char in ("X", "x"):
            return self._sil_box_type(char)
        if char == "Y":
            return self.demangle_any_generic_type("OtherNominalType")
        if char == "Z":
            types = self.pop_type_list()
            name = self.pop("Identifier")
            parent = self.pop_context()
            found = Node("AnonymousContext")
            found = self.add_child(found, name)
            found = self.add_child(found, parent)
            return self.add_child(found, types)
        if char == "e":
            return self.make_type(Node("ErrorType"))
        if char == "S":
            return self._sugared_type()
        return None

    def _sugared_type(self):
        """`XS` -- the spelling the source used, kept for the debugger's benefit."""
        char = self.next_char()
        if char == "D":
            value = self.pop("Type")
            key = self.pop("Type")
            return self.make_type_or_none(self.with_children("SugaredDictionary", key, value))
        kind = {"q": "SugaredOptional", "a": "SugaredArray", "p": "SugaredParen"}.get(char)
        if kind is None:
            return None
        return self.make_type_or_none(self.with_child(kind, self.pop("Type")))

    def _sil_box_type(self, char):
        signature = None
        arguments = None
        if char == "X":
            signature = self.pop("DependentGenericSignature")
            if signature is None:
                return None
            arguments = self.pop_type_list()
            if arguments is None:
                return None
        fields = self.pop_type_list()
        if fields is None:
            return None
        layout = Node("SILBoxLayout")
        for field in fields.children:
            mutable = bool(field.children) and field.first.kind == "InOut"
            if mutable:
                # An `inout` field in the type list is how a mutable field is written.
                field = self.make_type(field.first.first)
            layout.add(Node("SILBoxMutableField" if mutable else "SILBoxImmutableField", children=[field]))
        box = Node("SILBoxTypeWithLayout", children=[layout])
        if signature is not None:
            box.add(signature)
            box.add(arguments)
        return self.make_type(box)

    def demangle_symbolic_extended_existential_type(self):
        conformances = self.pop_retroactive_conformances()
        arguments = Node("TypeList")
        while True:
            found = self.pop("Type")
            if found is None:
                break
            arguments.add(found)
        arguments.children.reverse()
        shape = self.pop()
        if shape is None or shape.kind not in (
            "UniqueExtendedExistentialTypeShapeSymbolicReference",
            "NonUniqueExtendedExistentialTypeShapeSymbolicReference",
        ):
            return None
        if conformances is None:
            existential = self.with_children("SymbolicExtendedExistentialType", shape, arguments)
        else:
            existential = self.with_children("SymbolicExtendedExistentialType", shape, arguments, conformances)
        return self.make_type_or_none(existential)

    def demangle_extended_existential_shape(self, char):
        found = self.pop("Type")
        signature = self.pop("DependentGenericSignature") if char == "G" else None
        if signature is not None:
            return self.with_children("ExtendedExistentialTypeShape", signature, found)
        return self.with_child("ExtendedExistentialTypeShape", found)

    # -- entities --------------------------------------------------------------

    def demangle_metatype_representation(self):
        return {
            "t": lambda: Node("MetatypeRepresentation", text="@thin"),
            "T": lambda: Node("MetatypeRepresentation", text="@thick"),
            "o": lambda: Node("MetatypeRepresentation", text="@objc_metatype"),
        }.get(self.next_char(), lambda: None)()

    def demangle_accessor(self, child):
        """A variable or subscript may be followed by which accessor of it this is."""
        char = self.next_char()
        if char == "p":
            # The pseudo-accessor: this names the variable itself.
            return child
        if char == "a":
            kind = _MUTABLE_ADDRESSORS.get(self.next_char())
        elif char == "l":
            kind = _ADDRESSORS.get(self.next_char())
        else:
            kind = _ACCESSORS.get(char)
        if kind is None:
            return None
        return self.with_child(kind, child)

    def demangle_function_entity(self):
        """`f` -- an initialiser, deinitialiser, closure or similar."""
        char = self.next_char()
        if char == "m":
            return self.demangle_entity("Macro")
        if char == "M":
            return self.demangle_macro_expansion()
        if char == "p":
            return self.demangle_entity("GenericTypeParamDecl")
        found = _FUNCTION_ENTITIES.get(char)
        if found is None:
            return None
        args, kind = found

        name_or_index = None
        parameters = None
        labels = None
        if args == "type-and-maybe-private-name":
            name_or_index = self.pop("PrivateDeclName")
            parameters = self.pop("Type")
            labels = self.pop_function_param_labels(parameters)
        elif args == "type-and-index":
            name_or_index = self.index_as_node()
            parameters = self.pop("Type")
        elif args == "index":
            name_or_index = self.index_as_node()

        entity = self.with_child(kind, self.pop_context())
        if args == "index":
            entity = self.add_child(entity, name_or_index)
        elif args == "type-and-maybe-private-name":
            self.add_child(entity, labels)
            entity = self.add_child(entity, parameters)
            self.add_child(entity, name_or_index)
        elif args == "type-and-index":
            entity = self.add_child(entity, name_or_index)
            entity = self.add_child(entity, parameters)
        return entity

    def demangle_entity(self, kind):
        found = self.pop("Type")
        labels = self.pop_function_param_labels(found)
        name = self.pop(is_decl_name)
        context = self.pop_context()
        if labels is not None:
            result = self.with_children(kind, context, name, labels, found)
        else:
            result = self.with_children(kind, context, name, found)
        return _parent_opaque_return_types(result, found)

    def demangle_variable(self):
        return self.demangle_accessor(self.demangle_entity("Variable"))

    def demangle_subscript(self):
        private_name = self.pop("PrivateDeclName")
        found = self.pop("Type")
        labels = self.pop_function_param_labels(found)
        context = self.pop_context()
        if found is None:
            return None
        subscript_ = Node("Subscript")
        subscript_ = self.add_child(subscript_, context)
        self.add_child(subscript_, labels)
        subscript_ = self.add_child(subscript_, found)
        self.add_child(subscript_, private_name)
        subscript_ = _parent_opaque_return_types(subscript_, found)
        return self.demangle_accessor(subscript_)

    def demangle_protocol_list(self):
        types = Node("TypeList")
        protocols = self.with_child("ProtocolList", types)

        def element(root):
            protocol = self.pop_protocol()
            if protocol is None:
                return False
            root.add(protocol)
            return True

        return protocols if self._pop_list(types, element) is not None else None

    def demangle_protocol_list_type(self):
        return self.make_type_or_none(self.demangle_protocol_list())

    def demangle_constrained_existential_requirement_list(self):
        requirements = Node("ConstrainedExistentialRequirementList")
        while True:
            first = self.pop("FirstElementMarker") is not None
            requirement = self.pop(is_requirement)
            if requirement is None:
                return None
            requirements.add(requirement)
            if first:
                break
        requirements.children.reverse()
        return requirements

    def demangle_generic_signature(self, has_param_counts):
        """`l` or `r` -- the `<T, U where ...>` a generic declaration carries.

        `r` spells out how many parameters each depth has; `l` is the shorthand for one
        parameter at one depth, which is what most generic declarations look like.
        """
        signature = Node("DependentGenericSignature")
        if has_param_counts:
            while not self.next_if("l"):
                if self.next_if("z"):
                    count = 0
                else:
                    count = self.index()
                    if count is None:
                        return None
                    count += 1
                signature.add(Node("DependentGenericParamCount", index=count))
        else:
            signature.add(Node("DependentGenericParamCount", index=1))
        counts = len(signature.children)
        while True:
            requirement = self.pop(is_requirement)
            if requirement is None:
                break
            signature.add(requirement)
        signature.children[counts:] = reversed(signature.children[counts:])
        return signature

    def demangle_generic_requirement(self):
        """`R` -- one `where` clause.

        The operator says two things at once: what kind of constraint it is, and how the
        constrained type is written. The default -- no letter at all -- is the commonest
        pair, a generic parameter conforming to a protocol.
        """
        found = _GENERIC_REQUIREMENTS.get(self.peek())
        if found is None:
            constraint, type_kind = "protocol", "generic"
        else:
            constraint, type_kind = found
            self.pos += 1

        if type_kind == "generic":
            constrained = self.make_type_or_none(self.demangle_generic_param_index())
        elif type_kind == "assoc":
            constrained = self.demangle_associated_type_simple(self.demangle_generic_param_index())
            self.add_substitution(constrained)
        elif type_kind == "compound-assoc":
            constrained = self.demangle_associated_type_compound(self.demangle_generic_param_index())
            self.add_substitution(constrained)
        else:
            constrained = self.pop("Type")

        if constraint == "pack-marker":
            return self.with_child("DependentGenericParamPackMarker", constrained)
        if constraint == "protocol":
            return self.with_children("DependentGenericConformanceRequirement", constrained, self.pop_protocol())
        if constraint == "base-class":
            return self.with_children("DependentGenericConformanceRequirement", constrained, self.pop("Type"))
        if constraint == "same-type":
            return self.with_children("DependentGenericSameTypeRequirement", constrained, self.pop("Type"))
        if constraint == "same-shape":
            return self.with_children("DependentGenericSameShapeRequirement", constrained, self.pop("Type"))
        return self._layout_requirement(constrained)

    def _layout_requirement(self, constrained):
        char = self.next_char()
        size = alignment = None
        if char in ("U", "R", "N", "C", "D", "T"):
            pass
        elif char in ("E", "e", "M", "m"):
            size = self.index_as_node()
            if size is None:
                return None
            if char in ("E", "M"):
                alignment = self.index_as_node()
        else:
            return None
        requirement = self.with_children(
            "DependentGenericLayoutRequirement", constrained, Node("Identifier", text=char)
        )
        if size is not None:
            self.add_child(requirement, size)
        if alignment is not None:
            self.add_child(requirement, alignment)
        return requirement

    def demangle_generic_type(self):
        signature = self.pop("DependentGenericSignature")
        found = self.pop("Type")
        return self.make_type_or_none(self.with_children("DependentGenericType", signature, found))

    def demangle_value_witness(self):
        code = self.next_char() + self.next_char()
        kind = _VALUE_WITNESSES.get(code)
        if kind is None:
            return None
        witness = Node("ValueWitness")
        self.add_child(witness, Node("Index", index=kind))
        return self.add_child(witness, self.pop("Type"))

    def demangle_macro_expansion(self):
        found = _MACRO_EXPANSIONS.get(self.next_char())
        if found is None:
            return None
        kind, attached, freestanding = found
        name = self.pop("Identifier")
        private_discriminator = self.pop("PrivateDeclName") if freestanding else None
        attached_name = self.pop(is_decl_name) if attached else None
        context = self.pop(lambda kind_: kind_ in _MACRO_EXPANSION_KINDS)
        if context is None:
            context = self.pop_context()
        discriminator = self.index_as_node()
        if attached:
            result = self.with_children(kind, context, attached_name, name, discriminator)
        else:
            result = self.with_children(kind, context, name, discriminator)
        if private_discriminator is not None and result is not None:
            result.add(private_discriminator)
        return result

    def demangle_type_mangling(self):
        found = self.pop("Type")
        labels = self.pop_function_param_labels(found)
        mangling = Node("TypeMangling")
        self.add_child(mangling, labels)
        return self.add_child(mangling, found)

    def demangle_type_annotation(self):
        char = self.next_char()
        if char == "a":
            return Node("AsyncAnnotation")
        if char == "A":
            return Node("IsolatedAnyFunctionType")
        if char == "b":
            return Node("ConcurrentFunctionType")
        if char == "c":
            return self.with_child("GlobalActorFunctionType", self.pop_type_and_get_child())
        if char == "C":
            return Node("NonIsolatedCallerFunctionType")
        if char == "i":
            return self.make_type_or_none(self.with_child("Isolated", self.pop_type_and_get_child()))
        if char == "j":
            return self.demangle_differentiable_function_type()
        if char == "k":
            return self.make_type_or_none(self.with_child("NoDerivative", self.pop_type_and_get_child()))
        if char == "K":
            # Swift 6 typed throws: the error type is named rather than implied.
            return self.with_child("TypedThrowsAnnotation", self.pop_type_and_get_child())
        if char == "t":
            return self.make_type_or_none(self.with_child("CompileTimeConst", self.pop_type_and_get_child()))
        if char == "g":
            return self.make_type_or_none(self.with_child("ConstValue", self.pop_type_and_get_child()))
        if char == "T":
            return Node("SendingResultFunctionType")
        if char == "u":
            return self.make_type_or_none(self.with_child("Sending", self.pop_type_and_get_child()))
        return None


#: Bytes 1-0xC introduce a symbolic reference: a four-byte offset into the binary's own
#: metadata. A name on its own does not carry what they point at, so they are refused.
_SYMBOLIC_REFERENCE_BYTES = frozenset(chr(byte) for byte in range(1, 0xD))

#: Popped in this order by `pop_function_type`, and skipped in this order by
#: `pop_function_param_labels`. The two must agree; both are the reference's order.
#:
#: Each slot is a set because two of them accept more than one node: a function's
#: isolation is written as a global actor, as `@isolated(any)`, or as
#: `nonisolated(nonsending)`, and never as more than one; and a `throws` is either bare
#: or names the error type it throws.
_FUNCTION_ANNOTATIONS = (
    frozenset({"SendingResultFunctionType"}),
    frozenset({"GlobalActorFunctionType", "IsolatedAnyFunctionType", "NonIsolatedCallerFunctionType"}),
    frozenset({"DifferentiableFunctionType"}),
    frozenset({"ThrowsAnnotation", "TypedThrowsAnnotation"}),
    frozenset({"ConcurrentFunctionType"}),
    frozenset({"AsyncAnnotation"}),
)

#: Contexts that declare no generic parameters of their own, so a nested generic type's
#: argument list belongs to something further out.
_DOES_NOT_CONSUME_GENERIC_ARGS = frozenset(
    [
        "Variable",
        "Subscript",
        "ImplicitClosure",
        "ExplicitClosure",
        "DefaultArgumentInitializer",
        "Initializer",
        "PropertyWrapperBackingInitializer",
        "PropertyWrapperInitFromProjectedValue",
        "Static",
    ]
)

_BOUND_GENERIC_KINDS = {
    "Class": "BoundGenericClass",
    "Structure": "BoundGenericStructure",
    "Enum": "BoundGenericEnum",
    "Protocol": "BoundGenericProtocol",
    "OtherNominalType": "BoundGenericOtherNominalType",
    "TypeAlias": "BoundGenericTypeAlias",
}

_IMPL_PARAM_CONVENTIONS = {
    "i": "@in",
    "c": "@in_constant",
    "l": "@inout",
    "b": "@inout_aliasable",
    "n": "@in_guaranteed",
    "x": "@owned",
    "g": "@guaranteed",
    "e": "@deallocating",
    "y": "@unowned",
    "v": "@pack_owned",
    "p": "@pack_guaranteed",
    "m": "@pack_inout",
}

_IMPL_RESULT_CONVENTIONS = {
    "r": "@out",
    "o": "@owned",
    "d": "@unowned",
    "u": "@unowned_inner_pointer",
    "a": "@autoreleased",
    "k": "@pack_out",
}

_IMPL_CALLEE_CONVENTIONS = {
    "y": "@callee_unowned",
    "g": "@callee_guaranteed",
    "x": "@callee_owned",
    "t": "@convention(thin)",
}

_IMPL_FUNCTION_CONVENTIONS = {
    "B": "block",
    "C": "c",
    "M": "method",
    "O": "objc_method",
    "K": "closure",
    "W": "witness_method",
}

_METATYPE_POPPED_TYPE = {
    "a": "TypeMetadataAccessFunction",
    "b": "CanonicalSpecializedGenericTypeMetadataAccessFunction",
    "D": "TypeMetadataDemanglingCache",
    "f": "FullTypeMetadata",
    "i": "TypeMetadataInstantiationFunction",
    "I": "TypeMetadataInstantiationCache",
    "l": "TypeMetadataSingletonInitializationCache",
    "L": "TypeMetadataLazyCache",
    "m": "Metaclass",
    "M": "CanonicalSpecializedGenericMetaclass",
    "n": "NominalTypeDescriptor",
    "N": "NoncanonicalSpecializedGenericTypeMetadata",
    "o": "ClassMetadataBaseOffset",
    "P": "GenericTypeMetadataPattern",
    "r": "TypeMetadataCompletionFunction",
    "s": "ObjCResilientClassStub",
    "t": "FullObjCResilientClassStub",
    "u": "MethodLookupFunction",
    "U": "ObjCMetadataUpdateFunction",
    "z": "CanonicalPrespecializedGenericTypeCachingOnceToken",
}

_METATYPE_POPPED_NODE = {
    "g": "OpaqueTypeDescriptorAccessor",
    "h": "OpaqueTypeDescriptorAccessorImpl",
    "j": "OpaqueTypeDescriptorAccessorKey",
    "J": "NoncanonicalSpecializedGenericTypeMetadataCache",
    "k": "OpaqueTypeDescriptorAccessorVar",
    "K": "MetadataInstantiationCache",
    "q": "Uniquable",
    "Q": "OpaqueTypeDescriptor",
}

_THUNK_PLAIN = {
    "o": "ObjCAttribute",
    "O": "NonObjCAttribute",
    "D": "DynamicAttribute",
    "d": "DirectMethodReferenceAttribute",
    "E": "DistributedThunk",
    "F": "DistributedAccessor",
    "a": "PartialApplyObjCForwarder",
    "A": "PartialApplyForwarder",
    "m": "MergedFunction",
    "X": "DynamicallyReplaceableFunctionVar",
    "x": "DynamicallyReplaceableFunctionKey",
    "I": "DynamicallyReplaceableFunctionImpl",
    "u": "AsyncFunctionPointer",
}

_THUNK_OF_ENTITY = {
    "c": "CurryThunk",
    "j": "DispatchThunk",
    "q": "MethodDescriptor",
    "S": "ProtocolSelfConformanceWitness",
}

_GENERIC_SPECIALIZATIONS = {
    "g": "GenericSpecialization",
    "G": "GenericSpecializationNotReAbstracted",
    "B": "GenericSpecializationInResilienceDomain",
    "s": "GenericSpecializationPrespecialized",
    "i": "InlinedGenericFunction",
}

_WITNESS_OF_CONFORMANCE = {
    "P": "ProtocolWitnessTable",
    "p": "ProtocolWitnessTablePattern",
    "G": "GenericProtocolWitnessTable",
    "I": "GenericProtocolWitnessTableInstantiationFunction",
    "r": "ResilientProtocolWitnessTable",
    "a": "ProtocolWitnessTableAccessor",
}

_OUTLINED_VALUE_WITNESSES = {
    "y": "OutlinedCopy",
    "e": "OutlinedConsume",
    "r": "OutlinedRetain",
    "s": "OutlinedRelease",
    "b": "OutlinedInitializeWithTake",
    "c": "OutlinedInitializeWithCopy",
    "d": "OutlinedAssignWithTake",
    "f": "OutlinedAssignWithCopy",
    "h": "OutlinedDestroy",
}

_SPECIAL_FUNCTION_TYPES = {
    "E": "NoEscapeFunctionType",
    "A": "EscapingAutoClosureType",
    "f": "ThinFunctionType",
    "K": "AutoClosureType",
    "U": "UncurriedFunctionType",
    "L": "EscapingObjCBlock",
    "B": "ObjCBlock",
    "C": "CFunctionPointer",
}

_SPECIAL_TYPE_WRAPPERS = {
    "o": "Unowned",
    "u": "Unmanaged",
    "w": "Weak",
    "b": "SILBoxType",
    "D": "DynamicSelf",
    "p": "ExistentialMetatype",
}

_FUNCTION_ENTITIES = {
    "D": ("none", "Deallocator"),
    "d": ("none", "Destructor"),
    "E": ("none", "IVarDestroyer"),
    "e": ("none", "IVarInitializer"),
    "i": ("none", "Initializer"),
    "C": ("type-and-maybe-private-name", "Allocator"),
    "c": ("type-and-maybe-private-name", "Constructor"),
    "U": ("type-and-index", "ExplicitClosure"),
    "u": ("type-and-index", "ImplicitClosure"),
    "A": ("index", "DefaultArgumentInitializer"),
    "P": ("none", "PropertyWrapperBackingInitializer"),
    "W": ("none", "PropertyWrapperInitFromProjectedValue"),
}

_ACCESSORS = {
    "m": "MaterializeForSet",
    "s": "Setter",
    "g": "Getter",
    "G": "GlobalGetter",
    "w": "WillSet",
    "W": "DidSet",
    "r": "ReadAccessor",
    "M": "ModifyAccessor",
    "i": "InitAccessor",
}

_MUTABLE_ADDRESSORS = {
    "O": "OwningMutableAddressor",
    "o": "NativeOwningMutableAddressor",
    "P": "NativePinningMutableAddressor",
    "u": "UnsafeMutableAddressor",
}

_ADDRESSORS = {
    "O": "OwningAddressor",
    "o": "NativeOwningAddressor",
    "p": "NativePinningAddressor",
    "u": "UnsafeAddressor",
}

#: Constraint kind and how the constrained type is written, for each `R` operator.
_GENERIC_REQUIREMENTS = {
    "v": ("pack-marker", "generic"),
    "c": ("base-class", "assoc"),
    "C": ("base-class", "compound-assoc"),
    "b": ("base-class", "generic"),
    "B": ("base-class", "substitution"),
    "t": ("same-type", "assoc"),
    "T": ("same-type", "compound-assoc"),
    "s": ("same-type", "generic"),
    "S": ("same-type", "substitution"),
    "m": ("layout", "assoc"),
    "M": ("layout", "compound-assoc"),
    "l": ("layout", "generic"),
    "L": ("layout", "substitution"),
    "p": ("protocol", "assoc"),
    "P": ("protocol", "compound-assoc"),
    "Q": ("protocol", "substitution"),
    "h": ("same-shape", "generic"),
}

#: `ValueWitnessMangling.def`, in file order -- the index is the enumerator's value, and
#: the printer looks the spelling back up by it.
_VALUE_WITNESS_NAMES = [
    "AllocateBuffer",
    "AssignWithCopy",
    "AssignWithTake",
    "DeallocateBuffer",
    "Destroy",
    "DestroyBuffer",
    "DestroyArray",
    "InitializeBufferWithCopyOfBuffer",
    "InitializeBufferWithCopy",
    "InitializeWithCopy",
    "InitializeBufferWithTake",
    "InitializeWithTake",
    "ProjectBuffer",
    "InitializeBufferWithTakeOfBuffer",
    "InitializeArrayWithCopy",
    "InitializeArrayWithTakeFrontToBack",
    "InitializeArrayWithTakeBackToFront",
    "StoreExtraInhabitant",
    "GetExtraInhabitantIndex",
    "GetEnumTag",
    "DestructiveProjectEnumData",
    "DestructiveInjectEnumTag",
    "GetEnumTagSinglePayload",
    "StoreEnumTagSinglePayload",
]

_VALUE_WITNESSES = {
    code: at
    for at, code in enumerate(
        [
            "al",
            "ca",
            "ta",
            "de",
            "xx",
            "XX",
            "Xx",
            "CP",
            "Cp",
            "cp",
            "Tk",
            "tk",
            "pr",
            "TK",
            "Cc",
            "Tt",
            "tT",
            "xs",
            "xg",
            "ug",
            "up",
            "ui",
            "et",
            "st",
        ]
    )
}

_MACRO_EXPANSIONS = {
    "a": ("AccessorAttachedMacroExpansion", True, False),
    "r": ("MemberAttributeAttachedMacroExpansion", True, False),
    "f": ("FreestandingMacroExpansion", False, True),
    "m": ("MemberAttachedMacroExpansion", True, False),
    "p": ("PeerAttachedMacroExpansion", True, False),
    "c": ("ConformanceAttachedMacroExpansion", True, False),
    "e": ("ExtensionAttachedMacroExpansion", True, False),
    "u": ("MacroExpansionUniqueName", False, False),
}

_MACRO_EXPANSION_KINDS = frozenset(kind for kind, _, _ in _MACRO_EXPANSIONS.values())

# Function-signature specialisation parameter kinds. The low values are alternatives; the
# high ones are flags that combine, which is why they are numbers rather than names.
_PARAM_CONSTANT_PROP_FUNCTION = 0
_PARAM_CONSTANT_PROP_GLOBAL = 1
_PARAM_CONSTANT_PROP_INTEGER = 2
_PARAM_CONSTANT_PROP_FLOAT = 3
_PARAM_CONSTANT_PROP_STRING = 4
_PARAM_CLOSURE_PROP = 5
_PARAM_BOX_TO_VALUE = 6
_PARAM_BOX_TO_STACK = 7
_PARAM_IN_OUT_TO_OUT = 8
_PARAM_CONSTANT_PROP_KEY_PATH = 9
_PARAM_DEAD = 1 << 6
_PARAM_OWNED_TO_GUARANTEED = 1 << 7
_PARAM_SROA = 1 << 8
_PARAM_GUARANTEED_TO_OWNED = 1 << 9
_PARAM_EXISTENTIAL_TO_GENERIC = 1 << 10

_PARAM_KINDS_WITH_PAYLOAD = frozenset(
    [
        _PARAM_CONSTANT_PROP_FUNCTION,
        _PARAM_CONSTANT_PROP_GLOBAL,
        _PARAM_CONSTANT_PROP_STRING,
        _PARAM_CONSTANT_PROP_KEY_PATH,
        _PARAM_CLOSURE_PROP,
    ]
)

#: The letter that starts a flag-set parameter, its base value, and the flags that may
#: follow it. The order the flags are tried in is the order they are mangled in.
_PARAM_FLAG_SETS = {
    "e": (
        _PARAM_EXISTENTIAL_TO_GENERIC,
        (("D", _PARAM_DEAD), ("G", _PARAM_OWNED_TO_GUARANTEED), ("O", _PARAM_GUARANTEED_TO_OWNED), ("X", _PARAM_SROA)),
    ),
    "d": (
        _PARAM_DEAD,
        (("G", _PARAM_OWNED_TO_GUARANTEED), ("O", _PARAM_GUARANTEED_TO_OWNED), ("X", _PARAM_SROA)),
    ),
    "g": (_PARAM_OWNED_TO_GUARANTEED, (("X", _PARAM_SROA),)),
    "o": (_PARAM_GUARANTEED_TO_OWNED, (("X", _PARAM_SROA),)),
    "x": (_PARAM_SROA, ()),
    "i": (_PARAM_BOX_TO_VALUE, ()),
    "s": (_PARAM_BOX_TO_STACK, ()),
    "r": (_PARAM_IN_OUT_TO_OUT, ()),
}


def _param_kind(value):
    return Node("FunctionSignatureSpecializationParamKind", index=value)


def _parent_opaque_return_types(parent, visited):
    """Point each `OpaqueReturnType` at the declaration whose return type it is.

    `some P` has no name; it is identified by the declaration that returns it. The
    reference stores the parent as a raw pointer in an index slot, which is only ever
    compared, never printed -- so the node itself is stored here instead.
    """
    if parent is None or visited is None:
        return None
    if visited.kind == "OpaqueReturnType":
        if visited.children and visited.last.kind == "OpaqueReturnTypeParent":
            return parent
        visited.add(Node("OpaqueReturnTypeParent", index=id(parent)))
        return parent
    if visited.kind in ("Function", "Variable", "Subscript"):
        # A nested declaration's opaque return type refers to *it*, not to this one.
        return parent
    for child in visited.children:
        _parent_opaque_return_types(parent, child)
    return parent


def _demangle_h(demangler):
    """`H` -- protocol conformance references and runtime records.

    The fall-through is the interesting case: `H` followed by anything else is not an
    operator at all but the start of an identifier, so both characters are given back.
    """
    char = demangler.next_char()
    if char == "A":
        return demangler.demangle_dependent_protocol_conformance_associated()
    if char == "C":
        return demangler.demangle_concrete_protocol_conformance()
    if char == "D":
        return demangler.demangle_dependent_protocol_conformance_root()
    if char == "I":
        return demangler.demangle_dependent_protocol_conformance_inherited()
    if char == "P":
        return demangler.with_child("ProtocolConformanceRefInTypeModule", demangler.pop_protocol())
    if char == "p":
        return demangler.with_child("ProtocolConformanceRefInProtocolModule", demangler.pop_protocol())
    if char == "c":
        return demangler.with_child("ProtocolConformanceDescriptorRecord", demangler.pop_protocol_conformance())
    if char == "n":
        return demangler.with_popped_type("NominalTypeDescriptorRecord")
    if char == "o":
        return demangler.with_child("OpaqueTypeDescriptorRecord", demangler.pop())
    if char == "r":
        return demangler.with_child("ProtocolDescriptorRecord", demangler.pop_protocol())
    if char == "F":
        return Node("AccessibleFunctionRecord")
    demangler.push_back()
    demangler.push_back()
    return demangler.demangle_identifier()


def _demangle_suffix(demangler):
    """`.<n>` is IRGen's own disambiguator, appended after a complete name."""
    demangler.push_back()
    return Node("Suffix", text=demangler.consume_all())


_OPERATORS = {
    "A": Demangler.demangle_multi_substitutions,
    "B": Demangler.demangle_builtin_type,
    "C": lambda self: self.demangle_any_generic_type("Class"),
    "D": Demangler.demangle_type_mangling,
    "E": Demangler.demangle_extension_context,
    "F": Demangler.demangle_plain_function,
    "G": Demangler.demangle_bound_generic_type,
    "H": _demangle_h,
    "I": Demangler.demangle_impl_function_type,
    "K": lambda self: Node("ThrowsAnnotation"),
    "L": Demangler.demangle_local_identifier,
    "M": Demangler.demangle_metatype,
    "N": lambda self: self.with_child("TypeMetadata", self.pop("Type")),
    "O": lambda self: self.demangle_any_generic_type("Enum"),
    "P": lambda self: self.demangle_any_generic_type("Protocol"),
    "Q": Demangler.demangle_archetype,
    "R": Demangler.demangle_generic_requirement,
    "S": Demangler.demangle_standard_substitution,
    "T": Demangler.demangle_thunk_or_specialization,
    "V": lambda self: self.demangle_any_generic_type("Structure"),
    "W": Demangler.demangle_witness,
    "X": Demangler.demangle_special_type,
    "Y": Demangler.demangle_type_annotation,
    "Z": lambda self: self.with_child("Static", self.pop(is_entity)),
    "a": lambda self: self.demangle_any_generic_type("TypeAlias"),
    "c": lambda self: self.pop_function_type("FunctionType"),
    "d": lambda self: Node("VariadicMarker"),
    "f": Demangler.demangle_function_entity,
    "g": Demangler.demangle_retroactive_conformance,
    "h": lambda self: self.make_type_or_none(self.with_child("Shared", self.pop_type_and_get_child())),
    "i": Demangler.demangle_subscript,
    "l": lambda self: self.demangle_generic_signature(False),
    "m": lambda self: self.make_type_or_none(self.with_child("Metatype", self.pop("Type"))),
    "n": lambda self: self.make_type_or_none(self.with_child("Owned", self.pop_type_and_get_child())),
    "o": Demangler.demangle_operator_identifier,
    "p": Demangler.demangle_protocol_list_type,
    "q": lambda self: self.make_type_or_none(self.demangle_generic_param_index()),
    "r": lambda self: self.demangle_generic_signature(True),
    "s": lambda self: Node("Module", text=STDLIB_NAME),
    "t": Demangler.pop_tuple,
    "u": Demangler.demangle_generic_type,
    "v": Demangler.demangle_variable,
    "w": Demangler.demangle_value_witness,
    "x": lambda self: self.make_type_or_none(self.dependent_generic_param_type(0, 0)),
    "y": lambda self: Node("EmptyList"),
    "z": lambda self: self.make_type_or_none(self.with_child("InOut", self.pop_type_and_get_child())),
    "_": lambda self: Node("FirstElementMarker"),
    ".": _demangle_suffix,
}


def demangle_symbol(name, resolver=None):
    """Read `name` into a `Global` node, or return `None` if it is not readable.

    Swift 3's mangling is a different grammar with its own demangler in the compiler, and
    it is dispatched to here on the same test the reference uses: `_Tt` -- or, more
    exactly, `_T` not followed by `0`, since `_T0` is Swift 4.
    """
    if name.startswith("_T") and not name.startswith("_T0"):
        from ._old_demangler import demangle_old_symbol

        return demangle_old_symbol(name)
    return Demangler(name, resolver).demangle_symbol()


def demangle_type(name, resolver=None):
    """Read `name` as a type rather than a whole symbol, for `_TtGSa...`-style names.

    This is also the entry a metadata typeref takes: those are types, and they carry no
    `$s` prefix for `demangle_symbol` to find.
    """
    demangler = Demangler(name, resolver)
    demangler.parse_and_push()
    found = demangler.pop()
    return found if found is not None else Node("Suffix", text=name)
