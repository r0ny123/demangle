"""Reading the Swift 3 mangling.

Swift changed its mangling in 4.0; `_T` followed by anything but `0` is the older one,
and it is still what the ObjC runtime holds for a Swift class, so it turns up in any
Apple binary with interop in it. The compiler keeps a separate demangler for it --
`lib/Demangling/OldDemangler.cpp` -- and this is a port of that.

It is a much easier read than the current one. The old mangling is *prefix*, so this is
plain recursive descent: `demangle_entity` reads the context, then the name, then the
type, in the order they are written. There is no node stack and no word table. What it
does share is the tree it builds, so `_printer.py` spells the result with no idea which
mangling it came from.

Two things are its own:

* **Substitutions are positional.** `S_`, `S0_`, `S1_` index a list of everything
  nominal seen so far, and a handful of one-letter codes -- `Sa`, `SS`, `Sq` -- stand for
  the standard library's common types, as they do in the current mangling.
* **Punycode is marked by `X` in front of the length**, not by a `00` prefix, and the
  encoding is the same one `_punycode.py` implements.
"""

from ._node import Node
from ._punycode import PunycodeError
from ._punycode import decode as punycode_decode

__all__ = ["demangle_old_symbol"]

STDLIB_NAME = "Swift"
MANGLING_MODULE_OBJC = "__C"

MAX_DEPTH = 1024

#: `demangleValueWitnessKind`, in the order `ValueWitnessMangling.def` declares them.
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

_TOP_LEVEL_ATTRIBUTES = {
    "To": "ObjCAttribute",
    "TO": "NonObjCAttribute",
    "TD": "DynamicAttribute",
    "Td": "DirectMethodReferenceAttribute",
    "TV": "VTableAttribute",
}

# Function-signature specialisation parameter kinds, as in the current mangling.
_PARAM_CONSTANT_PROP_FUNCTION = 0
_PARAM_CONSTANT_PROP_GLOBAL = 1
_PARAM_CONSTANT_PROP_INTEGER = 2
_PARAM_CONSTANT_PROP_FLOAT = 3
_PARAM_CONSTANT_PROP_STRING = 4
_PARAM_CLOSURE_PROP = 5
_PARAM_BOX_TO_VALUE = 6
_PARAM_BOX_TO_STACK = 7
_PARAM_IN_OUT_TO_OUT = 8
_PARAM_DEAD = 1 << 6
_PARAM_OWNED_TO_GUARANTEED = 1 << 7
_PARAM_SROA = 1 << 8
_PARAM_GUARANTEED_TO_OWNED = 1 << 9


class _Reader:
    """`NameSource`. Reading past the end is not an error; it yields nothing."""

    __slots__ = ("end", "pos", "text")

    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.end = len(text)

    def __bool__(self):
        return self.pos < self.end

    def peek(self):
        return self.text[self.pos] if self.pos < self.end else ""

    def next(self):
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

    def slice(self, count):
        if self.pos + count > self.end:
            return None
        found = self.text[self.pos : self.pos + count]
        self.pos += count
        return found

    def read_until(self, char):
        """Everything up to `char`, or `None` if it never comes."""
        at = self.text.find(char, self.pos)
        if at < 0:
            self.pos = self.end
            return None
        found = self.text[self.pos : at]
        self.pos = at
        return found

    def rest(self):
        return self.text[self.pos :]


#: `demangleSubstitutionIndex`. Not the current mangling's set: `Sc` is `UnicodeScalar`.
_STANDARD_SUBSTITUTIONS = {
    "a": ("Structure", "Array"),
    "b": ("Structure", "Bool"),
    "c": ("Structure", "UnicodeScalar"),
    "d": ("Structure", "Double"),
    "f": ("Structure", "Float"),
    "i": ("Structure", "Int"),
    "V": ("Structure", "UnsafeRawPointer"),
    "v": ("Structure", "UnsafeMutableRawPointer"),
    "P": ("Structure", "UnsafePointer"),
    "p": ("Structure", "UnsafeMutablePointer"),
    "q": ("Enum", "Optional"),
    "Q": ("Enum", "ImplicitlyUnwrappedOptional"),
    "R": ("Structure", "UnsafeBufferPointer"),
    "r": ("Structure", "UnsafeMutableBufferPointer"),
    "S": ("Structure", "String"),
    "u": ("Structure", "UInt"),
}

MANGLING_MODULE_CLANG_IMPORTER = "__C_Synthesized"

#: As in the current mangling's `_OPERATOR_CHARS`.
_OPERATOR_CHARS = "& @/= >    <*!|+?%-~   ^ ."

_NOMINAL_TYPES = {"V": "Structure", "O": "Enum", "C": "Class", "P": "Protocol"}

_BOUND_GENERIC_KINDS = {
    "Class": "BoundGenericClass",
    "Structure": "BoundGenericStructure",
    "Enum": "BoundGenericEnum",
}


#: `Node::IndexType`: unsigned 64 bits, allowed to wrap.
_UINT64_MASK = (1 << 64) - 1


class OldDemangler:
    """The reference's `OldDemangler`. `None` means failure, as `nullptr` does there."""

    def __init__(self, text):
        self.reader = _Reader(text)
        #: Everything nominal seen so far. `S_`, `S0_`, `S1_` index this list.
        self.substitutions = []

    def _swift_type(self, kind, name):
        return Node(kind, children=[Node("Module", text=STDLIB_NAME), Node("Identifier", text=name)])

    def _natural(self):
        reader = self.reader
        char = reader.next()
        if not ("0" <= char <= "9"):
            return None
        number = ord(char) - ord("0")
        while True:
            char = reader.peek()
            if not ("0" <= char <= "9"):
                return number
            # The reference wraps at 64 bits and its tests pin it
            # (`_Ttu4222222222222222222222222_rW_2T_2TJ_`).
            number = (10 * number + (ord(char) - ord("0"))) & _UINT64_MASK
            reader.next()

    def _index(self):
        """`_` is 0 and `<n>_` is n+1, as in the current mangling."""
        reader = self.reader
        if reader.next_if("_"):
            return 0
        number = self._natural()
        if number is None or not reader.next_if("_"):
            return None
        return (number + 1) & _UINT64_MASK

    def _index_as_node(self, kind="Number"):
        found = self._index()
        return None if found is None else Node(kind, index=found)

    def _builtin_size(self):
        number = self._natural()
        if number is None or not self.reader.next_if("_"):
            return None
        return number

    def demangle_top_level(self):
        reader = self.reader
        if not reader.next_if("_T"):
            return None
        top = Node("Global")

        if reader.next_if("TS"):
            while True:
                attribute = self.demangle_specialized_attribute(0)
                if attribute is None:
                    return None
                top.add(attribute)
                # A specialisation header does not share substitutions with what follows.
                self.substitutions = []
                if not reader.next_if("_TTS"):
                    break
            if not reader.next_if("_T"):
                return None
        else:
            for prefix, kind in _TOP_LEVEL_ATTRIBUTES.items():
                if reader.next_if(prefix):
                    top.add(Node(kind))
                    break

        found = self.demangle_global(0)
        if found is None:
            return None
        top.add(found)
        if reader:
            top.add(Node("Suffix", text=reader.rest()))
        return top

    def demangle_identifier(self, depth, kind=None):
        """`X` in front of the length marks a punycoded name; `o` marks an operator."""
        reader = self.reader
        if not reader:
            return None
        punycoded = reader.next_if("X")

        is_operator = False
        if reader.next_if("o"):
            is_operator = True
            if kind is not None:
                return None
            kind = {"p": "PrefixOperator", "P": "PostfixOperator", "i": "InfixOperator"}.get(reader.next())
            if kind is None:
                return None
        if kind is None:
            kind = "Identifier"

        length = self._natural()
        if length is None:
            return None
        identifier = reader.slice(length)
        if identifier is None:
            return None

        if punycoded:
            try:
                identifier = punycode_decode(identifier)
            except PunycodeError:
                return None
        if not identifier:
            return None

        if is_operator:
            spelled = []
            for char in identifier:
                if ord(char) > 127:
                    spelled.append(char)
                    continue
                if not ("a" <= char <= "z"):
                    return None
                mapped = _OPERATOR_CHARS[ord(char) - ord("a")]
                if mapped == " ":
                    return None
                spelled.append(mapped)
            identifier = "".join(spelled)

        return Node(kind, text=identifier)

    def demangle_decl_name(self, depth):
        reader = self.reader
        if reader.next_if("L"):
            discriminator = self._index_as_node()
            if discriminator is None:
                return None
            name = self.demangle_identifier(depth + 1)
            if name is None:
                return None
            return Node("LocalDeclName", children=[discriminator, name])
        if reader.next_if("P"):
            discriminator = self.demangle_identifier(depth + 1)
            if discriminator is None:
                return None
            name = self.demangle_identifier(depth + 1)
            if name is None:
                return None
            return Node("PrivateDeclName", children=[discriminator, name])
        return self.demangle_identifier(depth + 1)

    def demangle_substitution_index(self, depth):
        reader = self.reader
        if not reader:
            return None
        if reader.next_if("o"):
            return Node("Module", text=MANGLING_MODULE_OBJC)
        if reader.next_if("C"):
            return Node("Module", text=MANGLING_MODULE_CLANG_IMPORTER)
        found = _STANDARD_SUBSTITUTIONS.get(reader.peek())
        if found is not None:
            reader.next()
            return self._swift_type(*found)
        at = self._index()
        if at is None or at >= len(self.substitutions):
            return None
        return self.substitutions[at]

    def demangle_module(self, depth):
        reader = self.reader
        if reader.next_if("s"):
            return Node("Module", text=STDLIB_NAME)
        if reader.next_if("S"):
            module = self.demangle_substitution_index(depth + 1)
            if module is None or module.kind != "Module":
                return None
            return module
        module = self.demangle_identifier(depth + 1, "Module")
        if module is None:
            return None
        self.substitutions.append(module)
        return module

    def demangle_declaration_name(self, kind, depth):
        context = self.demangle_context(depth + 1)
        if context is None:
            return None
        name = self.demangle_decl_name(depth + 1)
        if name is None:
            return None
        declaration = Node(kind, children=[context, name])
        self.substitutions.append(declaration)
        return declaration

    def demangle_protocol_name(self, depth):
        protocol = self.demangle_protocol_name_impl(depth)
        if protocol is None:
            return None
        return Node("Type", children=[protocol])

    def demangle_protocol_name_given_context(self, context, depth):
        name = self.demangle_decl_name(depth + 1)
        if name is None:
            return None
        protocol = Node("Protocol", children=[context, name])
        self.substitutions.append(protocol)
        return protocol

    def demangle_protocol_name_impl(self, depth):
        """`S` is ambiguous here: it may substitute the protocol or its context, so the
        two readings have to be told apart by what comes back."""
        if depth > MAX_DEPTH:
            return None
        reader = self.reader
        if reader.next_if("S"):
            found = self.demangle_substitution_index(depth + 1)
            if found is None:
                return None
            if found.kind == "Protocol":
                return found
            if found.kind != "Module":
                return None
            return self.demangle_protocol_name_given_context(found, depth + 1)
        if reader.next_if("s"):
            return self.demangle_protocol_name_given_context(Node("Module", text=STDLIB_NAME), depth + 1)
        return self.demangle_declaration_name("Protocol", depth + 1)

    def demangle_nominal_type(self, depth):
        reader = self.reader
        if reader.next_if("S"):
            return self.demangle_substitution_index(depth + 1)
        kind = _NOMINAL_TYPES.get(reader.peek())
        if kind is None:
            return None
        reader.next()
        return self.demangle_declaration_name(kind, depth + 1)

    def demangle_bound_generic_args(self, nominal, depth):
        if not nominal.children:
            return None
        parent = nominal.first
        if parent.kind not in ("Module", "Function", "Extension"):
            parent = self.demangle_bound_generic_args(parent, depth + 1)
            if parent is None:
                return None
            rebuilt = Node(nominal.kind, children=[parent, *nominal.children[1:]])
            nominal = rebuilt

        args = Node("TypeList")
        reader = self.reader
        while not reader.next_if("_"):
            found = self.demangle_type(depth + 1)
            if found is None:
                return None
            args.add(found)
            if not reader:
                return None
        if not args.children:
            return nominal

        kind = _BOUND_GENERIC_KINDS.get(nominal.kind)
        if kind is None:
            return None
        return Node(kind, children=[Node("Type", children=[nominal]), args])

    def demangle_bound_generic_type(self, depth):
        nominal = self.demangle_nominal_type(depth + 1)
        if nominal is None:
            return None
        return self.demangle_bound_generic_args(nominal, depth + 1)

    def demangle_context(self, depth):
        reader = self.reader
        if not reader:
            return None
        if reader.next_if("E"):
            module = self.demangle_module(depth + 1)
            if module is None:
                return None
            found = self.demangle_context(depth + 1)
            if found is None:
                return None
            return Node("Extension", children=[module, found])
        if reader.next_if("e"):
            module = self.demangle_module(depth + 1)
            if module is None:
                return None
            signature = self.demangle_generic_signature(depth + 1)
            if signature is None:
                return None
            found = self.demangle_context(depth + 1)
            if found is None:
                return None
            return Node("Extension", children=[module, found, signature])
        if reader.next_if("S"):
            return self.demangle_substitution_index(depth + 1)
        if reader.next_if("s"):
            return Node("Module", text=STDLIB_NAME)
        if reader.next_if("G"):
            return self.demangle_bound_generic_type(depth + 1)
        if _starts_an_entity(reader.peek()):
            return self.demangle_entity(depth + 1)
        return self.demangle_module(depth + 1)

    def demangle_protocol_list(self, depth):
        types = Node("TypeList")
        protocols = Node("ProtocolList", children=[types])
        while not self.reader.next_if("_"):
            protocol = self.demangle_protocol_name(depth + 1)
            if protocol is None:
                return None
            types.add(protocol)
        return protocols

    def demangle_protocol_conformance(self, depth):
        found = self.demangle_type(depth + 1)
        if found is None:
            return None
        protocol = self.demangle_protocol_name(depth + 1)
        if protocol is None:
            return None
        context = self.demangle_context(depth + 1)
        if context is None:
            return None
        return Node("ProtocolConformance", children=[found, protocol, context])

    def demangle_entity(self, depth):
        """`[Z] <kind> <context> <name> [<type>]`.

        The old mangling wrote an accessor as the *outer* node with the variable inside
        it, which is the reverse of what the printer expects, so `wrap` rebuilds it the
        way the current mangling produces.
        """
        if depth > MAX_DEPTH:
            return None
        reader = self.reader
        is_static = reader.next_if("Z")

        basic = None
        for letter, kind in (("F", "Function"), ("v", "Variable"), ("I", "Initializer"), ("i", "Subscript")):
            if reader.next_if(letter):
                basic = kind
                break
        if basic is None:
            return self.demangle_nominal_type(depth + 1)

        context = self.demangle_context(depth + 1)
        if context is None:
            return None

        kind = None
        has_type = True
        wrap = False
        name = None

        simple = _ENTITY_WITHOUT_TYPE.get(reader.peek())
        if simple is not None:
            reader.next()
            kind, has_type = simple, False
        elif reader.next_if("C"):
            kind = "Allocator"
        elif reader.next_if("c"):
            kind = "Constructor"
        elif reader.peek() in ("a", "l"):
            table = _MUTABLE_ADDRESSORS if reader.next() == "a" else _ADDRESSORS
            kind = table.get(reader.next())
            if kind is None:
                return None
            wrap = True
            name = self.demangle_decl_name(depth + 1)
            if name is None:
                return None
        elif reader.peek() in _ACCESSORS:
            wrap = True
            kind = _ACCESSORS[reader.next()]
            name = self.demangle_decl_name(depth + 1)
            if name is None:
                return None
        elif reader.peek() in ("U", "u"):
            kind = "ExplicitClosure" if reader.next() == "U" else "ImplicitClosure"
            name = self._index_as_node()
            if name is None:
                return None
        elif basic == "Initializer":
            if reader.next_if("A"):
                kind = "DefaultArgumentInitializer"
                name = self._index_as_node()
                if name is None:
                    return None
            elif reader.next_if("i"):
                kind = "Initializer"
            else:
                return None
            has_type = False
        else:
            kind = basic
            name = self.demangle_decl_name(depth + 1)
            if name is None:
                return None

        entity = Node(kind)
        if wrap:
            if name is None:
                # Unreachable; guarded because the reference dereferences it unguarded.
                return None
            # The old mangling spells a subscript's accessor `subscript`; that name is dropped.
            is_subscript = False
            if name.kind == "Identifier" and name.text == "subscript":
                is_subscript = True
                name = None
            elif name.kind == "PrivateDeclName" and len(name.children) > 1 and name.child(1).text == "subscript":
                is_subscript = True
                name = Node("PrivateDeclName", children=[name.first])

            wrapped = Node("Subscript" if is_subscript else "Variable", children=[context])
            if not is_subscript:
                wrapped.add(name)
            if has_type:
                found = self.demangle_type(depth + 1)
                if found is None:
                    return None
                wrapped.add(found)
            if is_subscript and name is not None:
                wrapped.add(name)
            entity.add(wrapped)
        else:
            entity.add(context)
            if name is not None:
                entity.add(name)
            if has_type:
                found = self.demangle_type(depth + 1)
                if found is None:
                    return None
                entity.add(found)

        return Node("Static", children=[entity]) if is_static else entity

    def _dependent_generic_param_type(self, depth, index):
        return Node("DependentGenericParamType", children=[Node("Index", index=depth), Node("Index", index=index)])

    def demangle_generic_param_index(self, depth):
        reader = self.reader
        if reader.next_if("d"):
            param_depth = self._index()
            if param_depth is None:
                return None
            index = self._index()
            if index is None:
                return None
            return self._dependent_generic_param_type(param_depth + 1, index)
        if reader.next_if("x"):
            return self._dependent_generic_param_type(0, 0)
        index = self._index()
        if index is None:
            return None
        return self._dependent_generic_param_type(0, index + 1)

    def demangle_dependent_member_type_name(self, base, depth):
        reader = self.reader
        if reader.next_if("S"):
            associated = self.demangle_substitution_index(depth + 1)
            if associated is None or associated.kind != "DependentAssociatedTypeRef":
                return None
        else:
            protocol = None
            if reader.next_if("P"):
                protocol = self.demangle_protocol_name(depth + 1)
                if protocol is None:
                    return None
            identifier = self.demangle_identifier(depth + 1)
            if identifier is None:
                return None
            associated = Node("DependentAssociatedTypeRef", children=[identifier])
            if protocol is not None:
                associated.add(protocol)
            self.substitutions.append(associated)
        return Node("DependentMemberType", children=[base, associated])

    def demangle_associated_type_simple(self, depth):
        base = self.demangle_generic_param_index(depth + 1)
        if base is None:
            return None
        return self.demangle_dependent_member_type_name(Node("Type", children=[base]), depth + 1)

    def demangle_associated_type_compound(self, depth):
        base = self.demangle_generic_param_index(depth + 1)
        if base is None:
            return None
        while not self.reader.next_if("_"):
            base = self.demangle_dependent_member_type_name(Node("Type", children=[base]), depth + 1)
            if base is None:
                return None
        return base

    def demangle_dependent_type(self, depth):
        reader = self.reader
        if not reader:
            return None
        char = reader.peek()
        if char != "d" and char != "_" and not ("0" <= char <= "9"):
            base = self.demangle_type(depth + 1)
            if base is None:
                return None
            return self.demangle_dependent_member_type_name(base, depth + 1)
        return self.demangle_generic_param_index(depth + 1)

    def demangle_constrained_type(self, depth):
        """A constrained type is always a parameter or one of its associated types, so
        the `q` that would introduce a generic parameter elsewhere is left off."""
        reader = self.reader
        if reader.next_if("w"):
            found = self.demangle_associated_type_simple(depth + 1)
        elif reader.next_if("W"):
            found = self.demangle_associated_type_compound(depth + 1)
        else:
            found = self.demangle_generic_param_index(depth + 1)
        if found is None:
            return None
        return Node("Type", children=[found])

    def demangle_generic_signature(self, depth, pseudogeneric=False):
        kind = "DependentPseudogenericSignature" if pseudogeneric else "DependentGenericSignature"
        signature = Node(kind)
        count = None
        reader = self.reader
        while reader.peek() not in ("R", "r"):
            if reader.next_if("z"):
                count = 0
            else:
                count = self._index()
                if count is None:
                    return None
                count += 1
            signature.add(Node("DependentGenericParamCount", index=count))
        if count is None:
            # No counts written at all means exactly one parameter.
            signature.add(Node("DependentGenericParamCount", index=1))
        if reader.next_if("r"):
            return signature
        if not reader.next_if("R"):
            return None
        while not reader.next_if("r"):
            requirement = self.demangle_generic_requirement(depth + 1)
            if requirement is None:
                return None
            signature.add(requirement)
        return signature

    def demangle_metatype_representation(self, depth):
        reader = self.reader
        for letter, spelling in (("t", "@thin"), ("T", "@thick"), ("o", "@objc_metatype")):
            if reader.next_if(letter):
                return Node("MetatypeRepresentation", text=spelling)
        return None

    def demangle_generic_requirement(self, depth):
        constrained = self.demangle_constrained_type(depth + 1)
        if constrained is None:
            return None
        reader = self.reader
        if reader.next_if("z"):
            second = self.demangle_type(depth + 1)
            if second is None:
                return None
            return Node("DependentGenericSameTypeRequirement", children=[constrained, second])
        if reader.next_if("l"):
            return self._layout_requirement(constrained, depth)

        if not reader:
            return None
        char = reader.peek()
        if char == "C":
            constraint = self.demangle_type(depth + 1)
            if constraint is None:
                return None
        elif char == "S":
            reader.next()
            found = self.demangle_substitution_index(depth + 1)
            if found is None:
                return None
            if found.kind in ("Protocol", "Class"):
                named = found
            elif found.kind == "Module":
                named = self.demangle_protocol_name_given_context(found, depth + 1)
                if named is None:
                    return None
            else:
                return None
            constraint = Node("Type", children=[named])
        else:
            constraint = self.demangle_protocol_name(depth + 1)
            if constraint is None:
                return None
        return Node("DependentGenericConformanceRequirement", children=[constrained, constraint])

    def _layout_requirement(self, constrained, depth):
        reader = self.reader
        size = alignment = None
        name = None
        for letter in ("U", "R", "N", "T"):
            if reader.next_if(letter):
                name = letter
                break
        if name is None:
            for letter in ("E", "M"):
                if reader.next_if(letter):
                    size = self._natural()
                    if size is None or not reader.next_if("_"):
                        return None
                    alignment = self._natural()
                    if alignment is None:
                        return None
                    name = letter
                    break
        if name is None:
            for letter in ("e", "m"):
                if reader.next_if(letter):
                    size = self._natural()
                    if size is None:
                        return None
                    name = letter
                    break
        if name is None:
            return None
        requirement = Node(
            "DependentGenericLayoutRequirement",
            children=[constrained, Node("Identifier", text=name)],
        )
        if size is not None:
            requirement.add(Node("Number", index=size))
            if alignment is not None:
                requirement.add(Node("Number", index=alignment))
        return requirement

    def demangle_archetype_type(self, depth):
        def associated(root):
            name = self.demangle_identifier(depth + 1)
            if name is None:
                return None
            found = Node("AssociatedTypeRef", children=[root, name])
            self.substitutions.append(found)
            return found

        reader = self.reader
        if reader.next_if("Q"):
            root = self.demangle_archetype_type(depth + 1)
            return None if root is None else associated(root)
        if reader.next_if("S"):
            found = self.demangle_substitution_index(depth + 1)
            return None if found is None else associated(found)
        if reader.next_if("s"):
            return associated(Node("Module", text=STDLIB_NAME))
        return None

    def demangle_tuple(self, variadic, depth):
        tuple_ = Node("Tuple")
        element = None
        reader = self.reader
        while not reader.next_if("_"):
            if not reader:
                return None
            element = Node("TupleElement")
            if _starts_an_identifier(reader.peek()):
                label = self.demangle_identifier(depth + 1, "TupleElementName")
                if label is None:
                    return None
                element.add(label)
            found = self.demangle_type(depth + 1)
            if found is None:
                return None
            element.add(found)
            tuple_.add(element)
        if variadic and element is not None:
            # The marker goes first; the reference gets there by reversing twice.
            element.children.insert(0, Node("VariadicMarker"))
        return tuple_

    def demangle_type(self, depth):
        found = self.demangle_type_impl(depth)
        if found is None:
            return None
        return Node("Type", children=[found])

    def demangle_function_type(self, kind, depth):
        reader = self.reader
        throws = concurrent = asynchronous = False
        differentiability = None
        global_actor = None
        if reader:
            throws = reader.next_if("z")
            concurrent = reader.next_if("y")
            asynchronous = reader.next_if("Z")
            if reader.next_if("D"):
                char = reader.next()
                if char not in ("f", "r", "d", "l"):
                    return None
                differentiability = ord(char)
            if reader.next_if("Y"):
                global_actor = self.demangle_type(depth + 1)
                if global_actor is None:
                    return None
        parameters = self.demangle_type(depth + 1)
        if parameters is None:
            return None
        result = self.demangle_type(depth + 1)
        if result is None:
            return None

        block = Node(kind)
        if throws:
            block.add(Node("ThrowsAnnotation"))
        if asynchronous:
            block.add(Node("AsyncAnnotation"))
        if concurrent:
            block.add(Node("ConcurrentFunctionType"))
        if differentiability is not None:
            block.add(Node("DifferentiableFunctionType", index=differentiability))
        if global_actor is not None:
            block.add(Node("GlobalActorFunctionType", children=[global_actor]))
        block.add(Node("ArgumentTuple", children=[parameters]))
        block.add(Node("ReturnType", children=[result]))
        return block

    def demangle_type_impl(self, depth):
        if depth > MAX_DEPTH:
            return None
        reader = self.reader
        if not reader:
            return None
        char = reader.next()

        if char == "B":
            return self._builtin_type()
        if char == "a":
            return self.demangle_declaration_name("TypeAlias", depth + 1)
        if char in _FUNCTION_TYPES:
            return self.demangle_function_type(_FUNCTION_TYPES[char], depth + 1)
        if char == "D":
            found = self.demangle_type(depth + 1)
            return None if found is None else Node("DynamicSelf", children=[found])
        if char == "E":
            if not reader.next_if("R") or not reader.next_if("R"):
                return None
            return Node("ErrorType", text="")
        if char == "G":
            return self.demangle_bound_generic_type(depth + 1)
        if char == "M":
            found = self.demangle_type(depth + 1)
            return None if found is None else Node("Metatype", children=[found])
        if char == "P":
            if reader.next_if("M"):
                found = self.demangle_type(depth + 1)
                return None if found is None else Node("ExistentialMetatype", children=[found])
            return self.demangle_protocol_list(depth + 1)
        if char == "Q":
            if reader.next_if("u"):
                return Node("OpaqueReturnType")
            if reader.next_if("U"):
                ordinal = self._index()
                if ordinal is None:
                    return None
                return Node("OpaqueReturnType", children=[Node("OpaqueReturnTypeIndex", index=ordinal)])
            return self.demangle_archetype_type(depth + 1)
        if char == "q":
            return self.demangle_dependent_type(depth + 1)
        if char == "x":
            return self._dependent_generic_param_type(0, 0)
        if char == "w":
            return self.demangle_associated_type_simple(depth + 1)
        if char == "W":
            return self.demangle_associated_type_compound(depth + 1)
        if char in ("R", "k"):
            kind = "InOut" if char == "R" else "NoDerivative"
            # The reference reads the *impl*: `inout` wraps the bare type, not a `Type`.
            found = self.demangle_type_impl(depth + 1)
            return None if found is None else Node(kind, children=[found])
        if char == "S":
            return self.demangle_substitution_index(depth + 1)
        if char in ("T", "t"):
            return self.demangle_tuple(char == "t", depth + 1)
        if char == "u":
            signature = self.demangle_generic_signature(depth + 1)
            if signature is None:
                return None
            found = self.demangle_type(depth + 1)
            if found is None:
                return None
            return Node("DependentGenericType", children=[signature, found])
        if char == "X":
            return self._extended_type(depth)
        kind = _NOMINAL_TYPE_MARKERS.get(char)
        if kind is not None:
            return self.demangle_declaration_name(kind, depth + 1)
        return None

    def _builtin_type(self):
        reader = self.reader
        if not reader:
            return None
        char = reader.next()
        simple = _OLD_BUILTINS.get(char)
        if simple is not None:
            return Node("BuiltinTypeName", text=simple)
        if char in ("f", "i"):
            size = self._builtin_size()
            if size is None:
                return None
            stem = "Builtin.FPIEEE" if char == "f" else "Builtin.Int"
            return Node("BuiltinTypeName", text=f"{stem}{size}")
        if char == "v":
            count = self._natural()
            if count is None or not reader.next_if("B"):
                return None
            if reader.next_if("i"):
                size = self._builtin_size()
                return None if size is None else Node("BuiltinTypeName", text=f"Builtin.Vec{count}xInt{size}")
            if reader.next_if("f"):
                size = self._builtin_size()
                return None if size is None else Node("BuiltinTypeName", text=f"Builtin.Vec{count}xFPIEEE{size}")
            if reader.next_if("p"):
                return Node("BuiltinTypeName", text=f"Builtin.Vec{count}xRawPointer")
        return None

    def _extended_type(self, depth):
        """`X` and a second letter: the types with a spelling rather than a name."""
        reader = self.reader
        if reader.next_if("b"):
            found = self.demangle_type(depth + 1)
            return None if found is None else Node("SILBoxType", children=[found])
        if reader.next_if("B"):
            return self._sil_box_with_layout(depth)
        if reader.next_if("M"):
            representation = self.demangle_metatype_representation(depth + 1)
            if representation is None:
                return None
            found = self.demangle_type(depth + 1)
            return None if found is None else Node("Metatype", children=[representation, found])
        if reader.next_if("P"):
            if reader.next_if("M"):
                representation = self.demangle_metatype_representation(depth + 1)
                if representation is None:
                    return None
                found = self.demangle_type(depth + 1)
                if found is None:
                    return None
                return Node("ExistentialMetatype", children=[representation, found])
            return self.demangle_protocol_list(depth + 1)
        if reader.next_if("f"):
            return self.demangle_function_type("ThinFunctionType", depth + 1)
        for letter, kind in (("o", "Unowned"), ("u", "Unmanaged"), ("w", "Weak")):
            if reader.next_if(letter):
                found = self.demangle_type(depth + 1)
                return None if found is None else Node(kind, children=[found])
        if reader.next_if("F"):
            return self.demangle_impl_function_type(depth + 1)
        return None

    def _sil_box_with_layout(self, depth):
        reader = self.reader
        signature = None
        if reader.next_if("G"):
            signature = self.demangle_generic_signature(depth)
            if signature is None:
                return None
        layout = Node("SILBoxLayout")
        while not reader.next_if("_"):
            if reader.next_if("m"):
                kind = "SILBoxMutableField"
            elif reader.next_if("i"):
                kind = "SILBoxImmutableField"
            else:
                return None
            found = self.demangle_type(depth + 1)
            if found is None:
                return None
            layout.add(Node(kind, children=[found]))
        arguments = None
        if signature is not None:
            arguments = Node("TypeList")
            while not reader.next_if("_"):
                found = self.demangle_type(depth + 1)
                if found is None:
                    return None
                arguments.add(found)
        box = Node("SILBoxTypeWithLayout", children=[layout])
        if signature is not None:
            box.add(signature)
            box.add(arguments)
        return box

    def demangle_impl_function_type(self, depth):
        found = Node("ImplFunctionType")
        reader = self.reader
        if not self._impl_callee_convention(found, depth + 1):
            return None
        if reader.next_if("C"):
            convention = _IMPL_CONVENTION_NAMES.get(reader.next())
            if convention is None:
                return None
            found.add(Node("ImplFunctionConvention", children=[Node("ImplFunctionConventionName", text=convention)]))
        if reader.next_if("h"):
            found.add(Node("ImplFunctionAttribute", text="@Sendable"))
        if reader.next_if("H"):
            found.add(Node("ImplFunctionAttribute", text="@async"))

        pseudogeneric = False
        if reader.next_if("G") or (pseudogeneric := reader.next_if("g")):
            signature = self.demangle_generic_signature(depth + 1, pseudogeneric)
            if signature is None:
                return None
            found.add(signature)
        if not reader.next_if("_"):
            return None
        for kind in ("ImplParameter", "ImplResult"):
            while not reader.next_if("_"):
                item = self._impl_parameter_or_result(kind, depth + 1)
                if item is None:
                    return None
                found.add(item)
        return found

    def _impl_convention(self, context):
        """One letter meaning different things in callee, parameter and result position."""
        reader = self.reader
        for letter, spellings in _IMPL_CONVENTIONS.items():
            if reader.next_if(letter):
                return spellings[context]
        return ""

    def _impl_callee_convention(self, found, depth):
        attribute = "@convention(thin)" if self.reader.next_if("t") else self._impl_convention(0)
        if not attribute:
            return False
        found.add(Node("ImplConvention", text=attribute))
        return True

    def _impl_parameter_or_result(self, kind, depth):
        if self.reader.next_if("z"):
            if kind != "ImplResult":
                return None
            kind = "ImplErrorResult"
        context = 1 if kind == "ImplParameter" else 2
        convention = self._impl_convention(context)
        if not convention:
            return None
        found = self.demangle_type(depth + 1)
        if found is None:
            return None
        return Node(kind, children=[Node("ImplConvention", text=convention), found])

    def demangle_generic_specialization(self, specialization, depth):
        if depth > MAX_DEPTH:
            return None
        reader = self.reader
        while not reader.next_if("_"):
            parameter = Node("GenericSpecializationParam")
            found = self.demangle_type(depth + 1)
            if found is None:
                return None
            parameter.add(found)
            while not reader.next_if("_"):
                conformance = self.demangle_protocol_conformance(depth + 1)
                if conformance is None:
                    return None
                parameter.add(conformance)
            specialization.add(parameter)
        return specialization

    def _constant_prop(self, parent, depth):
        reader = self.reader
        if reader.next_if("fr"):
            name = self.demangle_identifier(depth + 1)
            if name is None or not reader.next_if("_"):
                return False
            parent.add(_param_kind(_PARAM_CONSTANT_PROP_FUNCTION))
            parent.add(name)
            return True
        if reader.next_if("g"):
            name = self.demangle_identifier(depth + 1)
            if name is None or not reader.next_if("_"):
                return False
            parent.add(_param_kind(_PARAM_CONSTANT_PROP_GLOBAL))
            parent.add(name)
            return True
        for prefix, kind in (("i", _PARAM_CONSTANT_PROP_INTEGER), ("fl", _PARAM_CONSTANT_PROP_FLOAT)):
            if reader.next_if(prefix):
                text = reader.read_until("_")
                if text is None or not reader.next_if("_"):
                    return False
                parent.add(_param_kind(kind))
                parent.add(_param_payload(text))
                return True
        if reader.next_if("s"):
            if not reader.next_if("e"):
                return False
            encoding = reader.peek()
            if encoding not in ("0", "1"):
                return False
            reader.next()
            if not reader.next_if("v"):
                return False
            text = self.demangle_identifier(depth + 1)
            if text is None or not reader.next_if("_"):
                return False
            parent.add(_param_kind(_PARAM_CONSTANT_PROP_STRING))
            # The string was read as an identifier; the printer tells it from this inline
            # payload by node kind.
            parent.add(_param_payload("u8" if encoding == "0" else "u16"))
            parent.add(text)
            return True
        return False

    def _closure_prop(self, parent, depth):
        reader = self.reader
        name = self.demangle_identifier(depth + 1)
        if name is None:
            return False
        parent.add(_param_kind(_PARAM_CLOSURE_PROP))
        parent.add(_param_payload(name.text))
        while reader.peek() != "_":
            found = self.demangle_type(depth + 1)
            if found is None:
                break
            parent.add(found)
        return reader.next_if("_")

    def demangle_function_signature_specialization(self, specialization, depth):
        reader = self.reader
        while not reader.next_if("_"):
            parameter = Node("FunctionSignatureSpecializationParam")
            if reader.next_if("n_"):
                pass  # The parameter was left as it was.
            elif reader.next_if("cp"):
                if not self._constant_prop(parameter, depth + 1):
                    return None
            elif reader.next_if("cl"):
                if not self._closure_prop(parameter, depth + 1):
                    return None
            elif reader.next_if("i_"):
                parameter.add(_param_kind(_PARAM_BOX_TO_VALUE))
            elif reader.next_if("k_"):
                parameter.add(_param_kind(_PARAM_BOX_TO_STACK))
            elif reader.next_if("r_"):
                parameter.add(_param_kind(_PARAM_IN_OUT_TO_OUT))
            else:
                value = 0
                for letter, flag in (
                    ("d", _PARAM_DEAD),
                    ("g", _PARAM_OWNED_TO_GUARANTEED),
                    ("o", _PARAM_GUARANTEED_TO_OWNED),
                    ("s", _PARAM_SROA),
                ):
                    if reader.next_if(letter):
                        value |= flag
                if not reader.next_if("_") or not value:
                    return None
                parameter.add(_param_kind(value))
            specialization.add(parameter)
        return specialization

    def demangle_specialized_attribute(self, depth):
        reader = self.reader
        not_reabstracted = False
        if reader.next_if("g") or (not_reabstracted := reader.next_if("r")):
            kind = "GenericSpecializationNotReAbstracted" if not_reabstracted else "GenericSpecialization"
            specialization = Node(kind)
            if reader.next_if("q"):
                specialization.add(Node("IsSerialized"))
            specialization.add(Node("SpecializationPassID", index=ord(reader.next() or "\x00") - 48))
            return self.demangle_generic_specialization(specialization, depth + 1)
        if reader.next_if("f"):
            specialization = Node("FunctionSignatureSpecialization")
            if reader.next_if("q"):
                specialization.add(Node("IsSerialized"))
            specialization.add(Node("SpecializationPassID", index=ord(reader.next() or "\x00") - 48))
            return self.demangle_function_signature_specialization(specialization, depth + 1)
        return None

    def demangle_reabstract_signature(self, signature, depth):
        reader = self.reader
        if reader.next_if("G"):
            generics = self.demangle_generic_signature(depth + 1)
            if generics is None:
                return False
            signature.add(generics)
        for _ in range(2):
            found = self.demangle_type(depth + 1)
            if found is None:
                return False
            signature.add(found)
        return True

    def demangle_global(self, depth):
        if depth > MAX_DEPTH or not self.reader:
            return None
        reader = self.reader

        if reader.next_if("M"):
            kind = _METADATA_KINDS.get(reader.peek())
            if kind is not None:
                reader.next()
                if kind == "ProtocolDescriptor":
                    child = self.demangle_protocol_name(depth + 1)
                else:
                    child = self.demangle_type(depth + 1)
                return None if child is None else Node(kind, children=[child])
            child = self.demangle_type(depth + 1)
            return None if child is None else Node("TypeMetadata", children=[child])

        if reader.next_if("PA"):
            kind = "PartialApplyObjCForwarder" if reader.next_if("o") else "PartialApplyForwarder"
            forwarder = Node(kind)
            if reader.next_if("__T"):
                inner = self.demangle_global(depth + 1)
                if inner is None:
                    return None
                forwarder.add(inner)
            return forwarder

        if reader.next_if("t"):
            found = self.demangle_type(depth + 1)
            return None if found is None else Node("TypeMangling", children=[found])

        if reader.next_if("w"):
            code = reader.next() + reader.next()
            kind = _VALUE_WITNESSES.get(code)
            if kind is None:
                return None
            found = self.demangle_type(depth + 1)
            if found is None:
                return None
            return Node("ValueWitness", children=[Node("Index", index=kind), found])

        if reader.next_if("W"):
            return self._witness(depth)

        if reader.next_if("T"):
            if reader.next_if("R"):
                thunk = Node("ReabstractionThunkHelper")
                return thunk if self.demangle_reabstract_signature(thunk, depth + 1) else None
            if reader.next_if("r"):
                thunk = Node("ReabstractionThunk")
                return thunk if self.demangle_reabstract_signature(thunk, depth + 1) else None
            if reader.next_if("W"):
                conformance = self.demangle_protocol_conformance(depth + 1)
                if conformance is None:
                    return None
                entity = self.demangle_entity(depth + 1)
                if entity is None:
                    return None
                return Node("ProtocolWitness", children=[conformance, entity])
            return None

        return self.demangle_entity(depth + 1)

    def _witness(self, depth):
        reader = self.reader
        if reader.next_if("V"):
            found = self.demangle_type(depth + 1)
            return None if found is None else Node("ValueWitnessTable", children=[found])
        if reader.next_if("v"):
            directness = {"d": 0, "i": 1}.get(reader.next())
            if directness is None:
                return None
            entity = self.demangle_entity(depth + 1)
            if entity is None:
                return None
            return Node("FieldOffset", children=[Node("Directness", index=directness), entity])
        conformance_kind = _WITNESS_OF_CONFORMANCE.get(reader.peek())
        if conformance_kind is not None:
            reader.next()
            conformance = self.demangle_protocol_conformance(depth + 1)
            return None if conformance is None else Node(conformance_kind, children=[conformance])
        if reader.peek() in ("l", "L"):
            kind = (
                "LazyProtocolWitnessTableAccessor" if reader.next() == "l" else "LazyProtocolWitnessTableCacheVariable"
            )
            found = self.demangle_type(depth + 1)
            if found is None:
                return None
            conformance = self.demangle_protocol_conformance(depth + 1)
            return None if conformance is None else Node(kind, children=[found, conformance])
        if reader.next_if("t"):
            conformance = self.demangle_protocol_conformance(depth + 1)
            if conformance is None:
                return None
            name = self.demangle_decl_name(depth + 1)
            if name is None:
                return None
            return Node("AssociatedTypeMetadataAccessor", children=[conformance, name])
        if reader.next_if("T"):
            conformance = self.demangle_protocol_conformance(depth + 1)
            if conformance is None:
                return None
            name = self.demangle_decl_name(depth + 1)
            if name is None:
                return None
            protocol = self.demangle_protocol_name(depth + 1)
            if protocol is None:
                return None
            return Node("AssociatedTypeWitnessTableAccessor", children=[conformance, name, protocol])
        return None


def _param_kind(value):
    return Node("FunctionSignatureSpecializationParamKind", index=value)


def _param_payload(text):
    return Node("FunctionSignatureSpecializationParamPayload", text=text)


def _starts_an_identifier(char):
    return char != "" and (char == "o" or "0" <= char <= "9")


def _starts_an_entity(char):
    return char in ("F", "I", "v", "i", "Z", "C", "V", "O", "P", "S")


_ENTITY_WITHOUT_TYPE = {
    "D": "Deallocator",
    "Z": "IsolatedDeallocator",
    "d": "Destructor",
    "e": "IVarInitializer",
    "E": "IVarDestroyer",
}

_MUTABLE_ADDRESSORS = {
    "O": "OwningMutableAddressor",
    "o": "NativeOwningMutableAddressor",
    "p": "NativePinningMutableAddressor",
    "u": "UnsafeMutableAddressor",
}

_ADDRESSORS = {
    "O": "OwningAddressor",
    "o": "NativeOwningAddressor",
    "p": "NativePinningAddressor",
    "u": "UnsafeAddressor",
}

_ACCESSORS = {
    "g": "Getter",
    "G": "GlobalGetter",
    "s": "Setter",
    "m": "MaterializeForSet",
    "w": "WillSet",
    "W": "DidSet",
    "r": "ReadAccessor",
    "M": "ModifyAccessor",
}

_NOMINAL_TYPE_MARKERS = {"C": "Class", "V": "Structure", "O": "Enum", "P": "Protocol"}

_FUNCTION_TYPES = {
    "b": "ObjCBlock",
    "c": "CFunctionPointer",
    "F": "FunctionType",
    "f": "UncurriedFunctionType",
    "K": "AutoClosureType",
}

_OLD_BUILTINS = {
    "b": "Builtin.BridgeObject",
    "B": "Builtin.UnsafeValueBuffer",
    "O": "Builtin.UnknownObject",
    "o": "Builtin.NativeObject",
    "p": "Builtin.RawPointer",
    "t": "Builtin.SILToken",
    "w": "Builtin.Word",
}

_METADATA_KINDS = {
    "P": "GenericTypeMetadataPattern",
    "a": "TypeMetadataAccessFunction",
    "L": "TypeMetadataLazyCache",
    "m": "Metaclass",
    "n": "NominalTypeDescriptor",
    "f": "FullTypeMetadata",
    "p": "ProtocolDescriptor",
}

_WITNESS_OF_CONFORMANCE = {
    "P": "ProtocolWitnessTable",
    "G": "GenericProtocolWitnessTable",
    "I": "GenericProtocolWitnessTableInstantiationFunction",
    "a": "ProtocolWitnessTableAccessor",
}

_IMPL_CONVENTION_NAMES = {
    "b": "block",
    "c": "c",
    "m": "method",
    "O": "objc_method",
    "w": "witness_method",
}

#: By position: the callee's convention, a parameter's, a result's. "" means not valid there.
_IMPL_CONVENTIONS = {
    "a": ("", "", "@autoreleased"),
    "d": ("@callee_unowned", "@unowned", "@unowned"),
    "D": ("", "", "@unowned_inner_pointer"),
    "g": ("@callee_guaranteed", "@guaranteed", ""),
    "e": ("", "@deallocating", ""),
    "i": ("", "@in", "@out"),
    "l": ("", "@inout", ""),
    "o": ("@callee_owned", "@owned", "@owned"),
}


def demangle_old_symbol(name):
    """Read a Swift 3 mangled name into the same tree the current mangling produces."""
    return OldDemangler(name).demangle_top_level()
