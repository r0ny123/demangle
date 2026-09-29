"""Spelling a Swift demangling tree.

A port of the compiler's `lib/Demangling/NodePrinter.cpp`. It is a separate pass from the
demangler for a reason that shows up everywhere in it: how a node is spelled depends on
where it sits. The same `Class` node is `Foundation.NSData` as a type and `NSData` as the
prefix of a method's name; a `Type` under a function's result gets ` -> ` before it and
the same node elsewhere does not.

Two mechanisms carry that context, and both are the reference's:

* **`print` returns a node.** When an entity's context cannot be printed as a prefix --
  because it is an extension, or a local declaration -- `printEntity` prints the entity
  first and hands the context back, and the caller spells it as ` in <context>`. That is
  why the main function has a return value at all.
* **`as_prefix_context`.** The same node prints differently when it is the `Abc.` in
  `Abc.def()`, and the flag says which it is.

The options are the ones `swift-demangle` uses with no flags: sugar on (`[Int]` rather
than `Array<Int>`) and every "display" option left at its default of on. They are
constants here rather than a parameter because a demangling that does not match the
toolchain's is not useful, and every one of them changes the answer.
"""

from ._demangler import _VALUE_WITNESS_NAMES, STDLIB_NAME, demangle_symbol
from ._old_demangler import demangle_old_symbol
from .options import DEFAULT_OPTIONS, SwiftOptions

__all__ = ["print_root"]

#: The reference gives up beyond this depth; a malformed name can nest without bound.
MAX_DEPTH = 768

#: The module prefix LLDB gives its own symbols.
LLDB_EXPRESSIONS_MODULE_NAME_PREFIX = "__lldb_expr_"

_SIMPLE_TYPES = frozenset(
    [
        "AssociatedType",
        "AssociatedTypeRef",
        "BoundGenericClass",
        "BoundGenericEnum",
        "BoundGenericStructure",
        "BoundGenericProtocol",
        "BoundGenericOtherNominalType",
        "BoundGenericTypeAlias",
        "BoundGenericFunction",
        "BuiltinTypeName",
        "BuiltinTupleType",
        "Class",
        "DependentGenericType",
        "DependentMemberType",
        "DependentGenericParamType",
        "DynamicSelf",
        "Enum",
        "ErrorType",
        "ExistentialMetatype",
        "Metatype",
        "MetatypeRepresentation",
        "Module",
        "Tuple",
        "Pack",
        "SILPackDirect",
        "SILPackIndirect",
        "ConstrainedExistentialRequirementList",
        "ConstrainedExistentialSelf",
        "Protocol",
        "ProtocolSymbolicReference",
        "ReturnType",
        "SILBoxType",
        "SILBoxTypeWithLayout",
        "Structure",
        "OtherNominalType",
        "TupleElementName",
        "TypeAlias",
        "TypeList",
        "LabelList",
        "TypeSymbolicReference",
        "SugaredOptional",
        "SugaredArray",
        "SugaredInlineArray",
        "SugaredDictionary",
        "SugaredParen",
        # No operator a suffix could bind to, so no brackets: `$_Sg` is `0?`, not `(0)?`.
        "BuiltinFixedArray",
        "BuiltinBorrow",
        "Integer",
        "NegativeInteger",
    ]
)

#: Inside a metatype these spell `.Protocol` rather than `.Type`.
_EXISTENTIAL_TYPES = frozenset(
    [
        "ExistentialMetatype",
        "ProtocolList",
        "ProtocolListWithClass",
        "ProtocolListWithAnyObject",
    ]
)

#: A declarator that reads as a function needs no space between it and what precedes it.
_NO_SPACE_BEFORE = frozenset(
    [
        "FunctionType",
        "NoEscapeFunctionType",
        "UncurriedFunctionType",
        "DependentGenericType",
    ]
)

_DIRECTNESS = ("direct", "indirect")

_VALUE_WITNESS_SPELLINGS = {at: name[0].lower() + name[1:] for at, name in enumerate(_VALUE_WITNESS_NAMES)}

_DIFFERENTIABILITY = {
    ord("f"): "@differentiable(_forward) ",
    ord("r"): "@differentiable(reverse) ",
    ord("l"): "@differentiable(_linear) ",
    ord("d"): "@differentiable ",
}

_AUTO_DIFF_KINDS = {
    ord("f"): "forward-mode derivative",
    ord("r"): "reverse-mode derivative",
    ord("d"): "differential",
    ord("p"): "pullback",
}

# Function-signature specialisation parameter kinds, as in `_demangler`.
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
_PARAM_CONSTANT_PROP_STRUCT = 10
_PARAM_CLOSURE_PROP_PREVIOUS_ARG = 11
_PARAM_ESCAPING_CLOSURE_PROP = 12


class _Invalid(Exception):
    """`setInvalid`: the tree is one the printer cannot spell.

    The reference sets a flag and keeps going, returning the empty string at the end. An
    exception gets to the same place and stops the wasted work in between.
    """


def generic_parameter_name(depth, index):
    """`A`, `B`, ... `Z`, `AA`, and a depth suffix past the innermost signature."""
    name = []
    while True:
        name.append(chr(ord("A") + index % 26))
        index //= 26
        if not index:
            break
    if depth:
        name.append(str(depth))
    return "".join(name)


#: `demangleSymbolAsString(text)`'s defaults, which differ from the tool's in one flag:
#: no sugar, so a propagated function says `Swift.Optional<Swift.String>`.
_PAYLOAD_OPTIONS = SwiftOptions(synthesize_sugar_on_types=False)


def _demangle_either(text):
    """A specialisation's payload is itself a mangled name, and not always a current one:
    a Swift 3 symbol's payload is Swift 3 too. The reference reaches both through one
    entry point, so this tries each."""
    found = demangle_symbol(text)
    if found is None:
        found = demangle_old_symbol(text)
    return found


def _spell_payload(text):
    """`demangleSymbolAsString(text)`, falling back to the text itself as the reference does."""
    return print_root(_demangle_either(text), _PAYLOAD_OPTIONS) or text


def _quoted(text):
    """`QuotedString`: C-style escaping, which a mangled Clang type may need."""
    out = ['"']
    for char in text:
        if char == "\\":
            out.append("\\\\")
        elif char == '"':
            out.append('\\"')
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        elif " " <= char <= "~":
            out.append(char)
        else:
            out.append(f"\\x{ord(char) & 0xFF:02X}")
    out.append('"')
    return "".join(out)


def _is_swift_module(node):
    return node.kind == "Module" and node.text == STDLIB_NAME


def _is_identifier(node, wanted):
    return node.kind == "Identifier" and node.text == wanted


def _is_class_type(node):
    return node.kind == "Class"


def _needs_space_before_type(node):
    while node.kind == "Type":
        if not node.children:
            return True
        node = node.first
    return node.kind not in _NO_SPACE_BEFORE


def _child_of_kind(node, kind):
    if node is None:
        return None
    for child in node.children:
        if child is not None and child.kind == kind:
            return child
    return None


def _c_int(number):
    """`(int)` of a node index, which is what the reference hands `printEntity`.

    An index is 64 bits unsigned and the old mangling lets it wrap, so on a name with a
    25-digit closure number the reference prints the low 32 bits of the wrapped value,
    signed -- and a negative one not at all, since `printEntity` prints an extra index
    only where it is not negative. Applied again after the `+ 1` the closure, macro and
    unique-name forms add, because the reference adds it in `int` too. Real names never
    reach either wrap; this is so a junk name prints the same junk as the reference
    rather than different junk.
    """
    number &= 0xFFFFFFFF
    return number - 0x100000000 if number >= 0x80000000 else number


class Printer:
    """The reference's `NodePrinter`, and the options it consults."""

    def __init__(self, options=DEFAULT_OPTIONS):
        self.options = options
        #: Whether `specialized ` has been written; the reference latches it so nested
        #: specialisation layers say it once.
        self._said_specialized = False
        self.out = []
        #: Characters written so far. `print_entity` writes a `.` only if the last call
        #: produced characters, as the reference measures it.
        self.length = 0

    def write(self, text):
        self.out.append(text)
        self.length += len(text)

    def result(self):
        return "".join(self.out)

    def is_simple_type(self, node):
        kind = node.kind
        if kind == "Type":
            return self.is_simple_type(node.first)
        if kind == "ProtocolList":
            return len(node.first.children) <= 1
        if kind == "ProtocolListWithAnyObject":
            return len(node.first.first.children) == 0
        return kind in _SIMPLE_TYPES

    def print_children(self, node, depth, separator=None):
        if node is None:
            return
        for at, child in enumerate(node.children):
            if separator is not None and at:
                self.write(separator)
            self.print(child, depth + 1)

    def print_with_parens(self, node, depth):
        needs = not self.is_simple_type(node)
        if needs:
            self.write("(")
        self.print(node, depth + 1)
        if needs:
            self.write(")")

    def print_optional_index(self, node):
        if node.index is not None:
            self.write(f"#{node.index} ")

    def print_context(self, context):
        """Whether a context is worth naming.

        The standard library and `__C` are named; a module the debugger invented is not
        worth hiding, and the reference names it too. This returns `False` only for the
        `HidingCurrentModule` option, which is unset here -- but the shape is kept because
        the caller's control flow depends on it.
        """
        return True

    def find_sugar(self, node):
        """Which of the four sugared spellings, if any, a bound generic type has."""
        if len(node.children) == 1 and node.kind == "Type":
            return self.find_sugar(node.first)
        if len(node.children) != 2:
            return None
        if node.kind not in ("BoundGenericEnum", "BoundGenericStructure"):
            return None
        unbound = node.first.first
        arguments = node.child(1)
        if len(unbound.children) < 2 or not _is_swift_module(unbound.first):
            return None
        name = unbound.child(1)
        if node.kind == "BoundGenericEnum":
            if _is_identifier(name, "Optional") and len(arguments.children) == 1:
                return "optional"
            if _is_identifier(name, "ImplicitlyUnwrappedOptional") and len(arguments.children) == 1:
                return "unwrapped-optional"
            return None
        if _is_identifier(name, "Array") and len(arguments.children) == 1:
            return "array"
        if _is_identifier(name, "Dictionary") and len(arguments.children) == 2:
            return "dictionary"
        return None

    def print_bound_generic_no_sugar(self, node, depth):
        if len(node.children) < 2:
            return
        self.print(node.first, depth + 1)
        self.write("<")
        self.print_children(node.child(1), depth, ", ")
        self.write(">")

    def print_bound_generic(self, node, depth):
        if len(node.children) < 2:
            return
        if len(node.children) != 2 or node.kind == "BoundGenericClass" or not self.options.synthesize_sugar_on_types:
            self.print_bound_generic_no_sugar(node, depth)
            return
        if node.kind == "BoundGenericProtocol":
            # `T as P`: the conforming type, then the protocol it is being seen as.
            self.print_children(node.child(1), depth)
            self.write(" as ")
            self.print(node.first, depth + 1)
            return

        sugar = self.find_sugar(node)
        if sugar is None:
            self.print_bound_generic_no_sugar(node, depth)
        elif sugar in ("optional", "unwrapped-optional"):
            self.print_with_parens(node.child(1).first, depth)
            self.write("?" if sugar == "optional" else "!")
        elif sugar == "array":
            self.write("[")
            self.print(node.child(1).first, depth + 1)
            self.write("]")
        else:
            self.write("[")
            self.print(node.child(1).first, depth + 1)
            self.write(" : ")
            self.print(node.child(1).child(1), depth + 1)
            self.write("]")

    def print_function_parameters(self, labels, parameters, depth, show_types=True):
        """The parameter list, with or without the types in it.

        Without them the list keeps its shape and its labels and nothing else, so
        `(Swift.Int) -> Swift.UInt` is `(_:)` and a labelled two-parameter function is
        `(from:to:)` -- which is how a Swift programmer refers to it. A parameter that
        carried no label at all is written `_:`, and there are no separators, because
        `(_:_:)` is one token in that spelling rather than a list.
        """
        if parameters.kind != "ArgumentTuple":
            raise _Invalid
        found = parameters.first.first
        if found.kind != "Tuple":
            # One unnamed parameter, which is not written as a tuple.
            if not show_types:
                self.write("(_:)")
                return
            self.write("(")
            self.print(found, depth + 1)
            self.write(")")
            return

        has_labels = labels is not None and labels.children
        self.write("(")
        for at, parameter in enumerate(found.children):
            if at and show_types:
                self.write(", ")
            if has_labels:
                label = labels.child(at)
                self.write(label.text if label.kind == "Identifier" else "_")
                self.write(":")
                if show_types:
                    self.write(" ")
            elif not show_types:
                name = _child_of_kind(parameter, "TupleElementName")
                self.write(f"{name.text}:" if name is not None else "_:")
            if show_types:
                self.print(parameter, depth + 1)
        self.write(")")

    def print_function_type(self, labels, node, depth):
        if len(node.children) < 2:
            raise _Invalid

        def convention_with_clang_type(name):
            self.write(f"@convention({name}")
            if node.first.kind == "ClangType":
                self.write(', mangledCType: "')
                self.print(node.first, depth + 1)
                self.write('"')
            self.write(") ")

        kind = node.kind
        if kind in ("AutoClosureType", "EscapingAutoClosureType"):
            self.write("@autoclosure ")
        elif kind == "ThinFunctionType":
            self.write("@convention(thin) ")
        elif kind == "CalledOnceFunctionType":
            self.write("@called(once) ")
        elif kind == "CFunctionPointer":
            convention_with_clang_type("c")
        elif kind in ("EscapingObjCBlock", "ObjCBlock"):
            if kind == "EscapingObjCBlock":
                self.write("@escaping ")
            convention_with_clang_type("block")

        arguments_at = len(node.children) - 2
        at = 0
        sendable = asynchronous = sending_result = False
        thrown = nonisolated_caller = None
        differentiability = None
        # The reverse of the mangling's order: the order the demangler adds these.
        if node.child(at).kind == "ClangType":
            at += 1
        if node.child(at).kind == "SendingResultFunctionType":
            at += 1
            sending_result = True
        if node.child(at).kind == "IsolatedAnyFunctionType":
            self.print(node.child(at), depth + 1)
            at += 1
        if node.child(at).kind == "NonIsolatedCallerFunctionType":
            # Held back: it goes after the differentiability.
            nonisolated_caller = node.child(at)
            at += 1
        if node.child(at).kind == "GlobalActorFunctionType":
            self.print(node.child(at), depth + 1)
            at += 1
        if node.child(at).kind == "DifferentiableFunctionType":
            differentiability = node.child(at).index
            at += 1
        if node.child(at).kind in ("ThrowsAnnotation", "TypedThrowsAnnotation"):
            thrown = node.child(at)
            at += 1
        if node.child(at).kind == "ConcurrentFunctionType":
            at += 1
            sendable = True
        if node.child(at).kind == "AsyncAnnotation":
            at += 1
            asynchronous = True

        if differentiability is not None:
            self.write(_DIFFERENTIABILITY.get(differentiability, ""))
        if nonisolated_caller is not None:
            self.print(nonisolated_caller, depth + 1)
        if sendable:
            self.write("@Sendable ")

        show_types = self.options.show_function_argument_types
        self.print_function_parameters(labels, node.child(arguments_at), depth, show_types)
        if not show_types:
            # Everything after the parameter list belongs to the *type*; the reference
            # returns here.
            return
        if asynchronous:
            self.write(" async")
        if thrown is not None:
            self.print(thrown, depth + 1)
        # Written here, not by `ReturnType`: a sending result puts a word between
        # (`-> sending T`).
        returns = node.child(arguments_at + 1)
        self.write(" -> ")
        if sending_result:
            self.write("sending ")
        if not returns.children:
            self.write(returns.text)
        else:
            self.print_children(returns, depth)

    def print_impl_function_type(self, node, depth):
        """A lowered SIL type: `(params) -> (results)`, with the attributes in front."""
        pattern_substitutions = None
        invocation_substitutions = None
        sending_result = None
        state = 0  # 0 attributes, 1 inputs, 2 results

        def transition(wanted):
            nonlocal state
            while state != wanted:
                if state == 0:
                    if pattern_substitutions is not None:
                        self.write("@substituted ")
                        self.print(pattern_substitutions.first, depth + 1)
                        self.write(" ")
                    self.write("(")
                elif state == 1:
                    self.write(") -> ")
                    if sending_result is not None:
                        self.print(sending_result, depth + 1)
                        self.write(" ")
                    self.write("(")
                state += 1

        for child in node.children:
            if child.kind == "ImplParameter":
                if state == 1:
                    self.write(", ")
                transition(1)
                self.print(child, depth + 1)
            elif child.kind in ("ImplResult", "ImplYield", "ImplErrorResult"):
                if state == 2:
                    self.write(", ")
                transition(2)
                self.print(child, depth + 1)
            elif child.kind == "ImplPatternSubstitutions":
                pattern_substitutions = child
            elif child.kind == "ImplInvocationSubstitutions":
                invocation_substitutions = child
            elif child.kind == "ImplSendingResult":
                sending_result = child
            else:
                self.print(child, depth + 1)
                self.write(" ")
        transition(2)
        self.write(")")

        if pattern_substitutions is not None:
            self.write(" for <")
            self.print_children(pattern_substitutions.child(1), depth)
            self.write(">")
        if invocation_substitutions is not None:
            self.write(" for <")
            self.print_children(invocation_substitutions.first, depth)
            self.write(">")

    def print_generic_signature(self, node, depth):
        """`<A, B where A: P>`, with the parameter counts turned back into names."""
        self.write("<")
        count = len(node.children)

        parameters = 0
        while parameters < count and node.child(parameters).kind == "DependentGenericParamCount":
            parameters += 1

        first_requirement = parameters
        while first_requirement < count:
            child = node.child(first_requirement)
            if child.kind == "Type":
                child = child.first
            if child.kind not in ("DependentGenericParamPackMarker", "DependentGenericParamValueMarker"):
                break
            first_requirement += 1

        def is_pack(at_depth, at_index):
            for pack_at in range(parameters, first_requirement):
                child = node.child(pack_at)
                if child.kind != "DependentGenericParamPackMarker":
                    continue
                child = child.first
                if child.kind != "Type":
                    continue
                child = child.first
                if child.kind != "DependentGenericParamType":
                    continue
                # The reference compares index against child 0.
                if at_index == child.first.index and at_depth == child.child(1).index:
                    return True
            return False

        def value_type(at_depth, at_index):
            """`(is a value parameter, the type to spell after it or None)`.

            The reference reads *both* the parameter and its type out of the marker's
            first child, which is the `Type` wrapping the parameter alone -- so the type
            is a child that is not there, and it prints `let A` with no `: Int` after it.
            Reading past the end is null there and an IndexError here, which refused the
            whole name; the two are the same answer and this says so.
            """
            for value_at in range(parameters, first_requirement):
                child = node.child(value_at)
                if child.kind != "DependentGenericParamValueMarker":
                    continue
                child = child.first
                if child.kind != "Type":
                    continue
                parameter = child.first
                if parameter.kind != "DependentGenericParamType":
                    continue
                if at_index == parameter.first.index and at_depth == parameter.child(1).index:
                    return True, (child.child(1) if len(child.children) > 1 else None)
            return False, None

        for at_depth in range(parameters):
            if at_depth:
                self.write("><")
            for index in range(node.child(at_depth).index):
                if index:
                    self.write(", ")
                if index >= 128:
                    # Only reachable for a malformed count; the reference caps it too.
                    self.write("...")
                    break
                if is_pack(at_depth, index):
                    self.write("each ")
                is_value, found = value_type(at_depth, index)
                if is_value:
                    self.write("let ")
                self.write(generic_parameter_name(at_depth, index))
                if found is not None:
                    self.write(": ")
                    self.print(found, depth + 1)

        if first_requirement != count and self.options.display_where_clauses:
            self.write(" where ")
            for at in range(first_requirement, count):
                if at > first_requirement:
                    self.write(", ")
                self.print(node.child(at), depth + 1)
        self.write(">")

    def print_function_sig_specialization_params(self, node, depth):
        """One parameter of a function-signature specialisation.

        Two cursors, as the reference has: `at` walks the children looking for the next
        *kind*, and reads the payload the mangling wrote inline beside it -- an integer's
        digits, a string's encoding. `argument` walks the same children skipping every
        kind and payload, and serves the operands that were popped off the stack: the
        types, the identifiers.

        One cursor would not do it, because a single parameter can carry several kinds --
        `pSSi3Si0` is a propagated struct, an integer, a struct, an integer -- and their
        two sorts of operand are interleaved differently from the kinds themselves.
        """
        at = 0
        argument = 0
        end = len(node.children)
        while at < end:
            child = node.child(at)
            if child.index is None:
                return
            kind = child.index
            if kind in (_PARAM_BOX_TO_VALUE, _PARAM_BOX_TO_STACK, _PARAM_IN_OUT_TO_OUT):
                self.print(node.child(at), depth + 1)
                at += 1
            elif kind in (_PARAM_CONSTANT_PROP_FUNCTION, _PARAM_CONSTANT_PROP_GLOBAL):
                self.write("[")
                self.print(node.child(at), depth + 1)
                at += 1
                self.write(" : ")
                argument = self.print_next_param_child(node, argument, kind, depth)
                self.write("]")
            elif kind in (_PARAM_CONSTANT_PROP_INTEGER, _PARAM_CONSTANT_PROP_FLOAT):
                if at + 2 > end:
                    return
                self.write("[")
                self.print(node.child(at), depth + 1)
                at += 1
                self.write(" : ")
                self.print(node.child(at), depth + 1)
                at += 1
                self.write("]")
            elif kind == _PARAM_CONSTANT_PROP_STRING:
                if at + 2 > end:
                    return
                self.write("[")
                self.print(node.child(at), depth + 1)
                at += 1
                self.write(" : ")
                self.print(node.child(at), depth + 1)
                at += 1
                self.write("'")
                argument = self.print_next_param_child(node, argument, kind, depth)
                self.write("'")
                self.write("]")
            elif kind == _PARAM_CONSTANT_PROP_KEY_PATH:
                self.write("[")
                self.print(node.child(at), depth + 1)
                at += 1
                self.write(" : ")
                argument = self.print_next_param_child(node, argument, kind, depth)
                self.write("<")
                argument = self.print_next_param_child(node, argument, kind, depth)
                self.write(",")
                argument = self.print_next_param_child(node, argument, kind, depth)
                self.write(">]")
            elif kind == _PARAM_CONSTANT_PROP_STRUCT:
                self.write("[")
                self.print(node.child(at), depth + 1)
                at += 1
                self.write(" : ")
                argument = self.print_next_param_child(node, argument, kind, depth)
                self.write("]")
            elif kind in (_PARAM_CLOSURE_PROP, _PARAM_ESCAPING_CLOSURE_PROP):
                if at + 2 > end:
                    return
                self.write("[")
                self.print(node.child(at), depth + 1)
                at += 1
                self.write(" : ")
                self.print(node.child(at), depth + 1)
                at += 1
                self.write(", Argument Types : [")
                while at < end:
                    following = node.child(at)
                    if following.kind != "Type":
                        break
                    self.print(following, depth + 1)
                    at += 1
                    if at < end and node.child(at).text is not None:
                        self.write(", ")
                self.write("]")
            elif kind == _PARAM_CLOSURE_PROP_PREVIOUS_ARG:
                if at + 2 > end:
                    return
                self.write("[")
                self.print(node.child(at), depth + 1)
                at += 1
                self.write(" ")
                self.print(node.child(at), depth + 1)
                at += 1
                self.write("]")
            else:
                self.print(node.child(at), depth + 1)
                at += 1

    def print_next_param_child(self, node, argument, kind, depth):
        """The next operand of a parameter: what was popped rather than written inline.

        Returns where the operand cursor reached, so the caller can pass it back in --
        several of the kinds take more than one.
        """
        end = len(node.children)
        while argument < end:
            child = node.child(argument)
            argument += 1
            if child.kind in (
                "FunctionSignatureSpecializationParamKind",
                "FunctionSignatureSpecializationParamPayload",
            ):
                continue
            if kind in (_PARAM_CONSTANT_PROP_FUNCTION, _PARAM_CONSTANT_PROP_GLOBAL):
                # A mangled name; the reference demangles it, falling back to the raw text.
                self.write(_spell_payload(child.text))
            elif kind == _PARAM_CONSTANT_PROP_STRING and child.text and child.text.startswith("_"):
                # `_` escapes a string constant that would otherwise start with a digit.
                self.write(child.text[1:])
            else:
                self.print(child, depth + 1)
            return argument
        return argument

    def print_specialization_prefix(self, node, description, depth, param_prefix=""):
        if not self.options.display_generic_specializations:
            if not self._said_specialized:
                self.write("specialized ")
                self._said_specialized = True
            return
        if node.first is not None and node.first.kind == "RepresentationChanged":
            self.write("representation changed of ")
            return
        self.write(description)
        self.write(" <")
        separator = ""
        argument = 0
        for child in node.children:
            if child.kind in ("SpecializationPassID", "MetatypeParamsRemoved", "DroppedArgument"):
                continue
            if child.kind == "IsSerialized":
                self.write(separator)
                separator = ", "
                self.print(child, depth + 1)
                continue
            if child.children:
                self.write(separator)
                self.write(param_prefix)
                separator = ", "
                if child.kind == "FunctionSignatureSpecializationParam":
                    self.write(f"Arg[{argument}] = ")
                    self.print_function_sig_specialization_params(child, depth)
                elif child.kind == "FunctionSignatureSpecializationReturn":
                    self.write("Return = ")
                    self.print_function_sig_specialization_params(child, depth)
                else:
                    self.print(child, depth + 1)
            argument += 1
        self.write("> of ")

    def print(self, node, depth, as_prefix_context=False):
        """Spell `node`, returning a context to be printed after it, or `None`.

        The return value is what makes ` in <context>` possible: an entity whose context
        cannot be written as a prefix prints itself first and hands the context back.
        """
        if depth > MAX_DEPTH:
            self.write("<<too complex>>")
            return None
        if node is None:
            self.write("<null node pointer>")
            return None

        kind = node.kind

        prefix = _PREFIX_THEN_FIRST_CHILD.get(kind)
        if prefix is not None:
            self.write(prefix)
            self.print(node.first, depth + 1)
            return None

        literal = _JUST_TEXT.get(kind)
        if literal is not None:
            if kind in _THUNK_LEADS and not self.options.shorten_thunk:
                # The short form of a wrapper is nothing: what it leads is the symbol it wraps.
                return None
            self.write(literal)
            return None

        entity = _ENTITY_KINDS.get(kind)
        if entity is not None:
            style, has_name, extra_name, overwrite = entity
            return self.print_entity(
                node,
                depth,
                as_prefix_context,
                style,
                has_name,
                extra_name,
                overwrite_name=overwrite,
            )

        handler = _HANDLERS.get(kind)
        if handler is not None:
            return handler(self, node, depth, as_prefix_context)

        # Anything left has no spelling; the reference asserts.
        raise _Invalid

    def print_abstract_storage(self, node, depth, as_prefix_context, extra_name):
        if node.kind == "Variable":
            return self.print_entity(node, depth, as_prefix_context, "colon", True, extra_name)
        if node.kind == "Subscript":
            return self.print_entity(
                node, depth, as_prefix_context, "colon", False, extra_name, overwrite_name="subscript"
            )
        raise _Invalid

    def print_entity(
        self,
        entity,
        depth,
        as_prefix_context,
        style,
        has_name,
        extra_name="",
        extra_index=-1,
        overwrite_name="",
    ):
        """Spell a declaration: its context, its name, and its type.

        The context goes in front -- `Foo.bar()` -- unless it cannot, in which case it is
        returned for the caller to spell as ` in Foo`. It cannot when the name has spaces
        in it (`closure #1 in ...` reads wrongly the other way round) or when the context
        is itself something that needs a type printed.
        """
        generic_arguments = None
        if entity.kind == "BoundGenericFunction":
            generic_arguments = entity.child(1)
            entity = entity.first

        multi_word = " " in extra_name
        local_name = has_name and entity.child(1).kind == "LocalDeclName"
        if local_name:
            multi_word = True

        if as_prefix_context and (style != "none" or multi_word):
            # A context printed as a prefix cannot carry a type, so hand it back.
            return entity

        postfix_context = None
        context = entity.first
        if self.print_context(context):
            if multi_word:
                postfix_context = context
            else:
                before = self.length
                postfix_context = self.print(context, depth + 1, True)
                if self.length != before:
                    self.write(".")

        if has_name or overwrite_name:
            if extra_name and multi_word:
                self.write(extra_name)
                if extra_index >= 0:
                    self.write(str(extra_index))
                self.write(" of ")
                extra_name = ""
                extra_index = -1
            before = self.length
            if overwrite_name:
                self.write(overwrite_name)
            else:
                name = entity.child(1)
                if name.kind != "PrivateDeclName":
                    self.print(name, depth + 1)
                private = _child_of_kind(entity, "PrivateDeclName")
                if private is not None:
                    self.print(private, depth + 1)
            if self.length != before and extra_name:
                self.write(".")

        if extra_name:
            self.write(extra_name)
            if extra_index >= 0:
                self.write(str(extra_index))

        if style != "none":
            found = _child_of_kind(entity, "Type")
            if found is None:
                raise _Invalid
            found = found.first
            if style == "function":
                # If what is there is not actually a function type, fall back to a colon.
                inner = found
                while inner.kind == "DependentGenericType":
                    inner = inner.child(1).first
                if inner.kind not in (
                    "FunctionType",
                    "NoEscapeFunctionType",
                    "UncurriedFunctionType",
                    "CFunctionPointer",
                    "ThinFunctionType",
                ):
                    style = "colon"
            if style == "colon":
                if self.options.display_entity_types:
                    self.write(" : ")
                    self.print_entity_type(entity, found, generic_arguments, depth)
            else:
                if multi_word or _needs_space_before_type(found):
                    self.write(" ")
                self.print_entity_type(entity, found, generic_arguments, depth)

        if not as_prefix_context and postfix_context is not None:
            if entity.kind in (
                "DefaultArgumentInitializer",
                "Initializer",
                "PropertyWrapperBackingInitializer",
                "PropertyWrappedFieldInitAccessor",
                "PropertyWrapperInitFromProjectedValue",
            ):
                self.write(" of ")
            else:
                self.write(" in ")
            self.print(postfix_context, depth + 1)
            postfix_context = None
        return postfix_context

    def print_entity_type(self, entity, found, generic_arguments, depth):
        labels = _child_of_kind(entity, "LabelList")
        if labels is None and generic_arguments is None:
            self.print(found, depth + 1)
            return
        if generic_arguments is not None:
            self.write("<")
            self.print_children(generic_arguments, depth, ", ")
            self.write(">")
        if found.kind == "DependentGenericType":
            if generic_arguments is None:
                self.print(found.first, depth + 1)
            dependent = found.child(1)
            if _needs_space_before_type(dependent):
                self.write(" ")
            found = dependent.first
        self.print_function_type(labels, found, depth)


#: `<text>` then the first child.
_PREFIX_THEN_FIRST_CHILD = {
    "Static": "static ",
    "CurryThunk": "curry thunk of ",
    "SILThunkIdentity": "identity thunk of ",
    "DispatchThunk": "dispatch thunk of ",
    "MethodDescriptor": "method descriptor for ",
    "MethodLookupFunction": "method lookup function for ",
    "ObjCMetadataUpdateFunction": "ObjC metadata update function for ",
    "ObjCResilientClassStub": "ObjC resilient class stub for ",
    "FullObjCResilientClassStub": "full ObjC resilient class stub for ",
    "OutlinedRetain": "outlined retain of ",
    "OutlinedRelease": "outlined release of ",
    "OutlinedInitializeWithTake": "outlined init with take of ",
    "OutlinedInitializeWithCopy": "outlined init with copy of ",
    "OutlinedAssignWithTake": "outlined assign with take of ",
    "OutlinedAssignWithCopy": "outlined assign with copy of ",
    "OutlinedDestroy": "outlined destroy of ",
    "OutlinedInitializeWithTakeNoValueWitness": "outlined init with take of ",
    "OutlinedInitializeWithCopyNoValueWitness": "outlined init with copy of ",
    "OutlinedAssignWithTakeNoValueWitness": "outlined assign with take of ",
    "OutlinedAssignWithCopyNoValueWitness": "outlined assign with copy of ",
    "OutlinedDestroyNoValueWitness": "outlined destroy of ",
    "DeclContext": "",
    "Type": "",
    "InOut": "inout ",
    "Isolated": "isolated ",
    "Sending": "sending ",
    "ConstValue": "@const ",
    "CompileTimeLiteral": "_const ",
    "Shared": "__shared ",
    "Owned": "__owned ",
    "NoDerivative": "@noDerivative ",
    "Weak": "weak ",
    "Unowned": "unowned ",
    "Unmanaged": "unowned(unsafe) ",
    "ProtocolSelfConformanceWitnessTable": "protocol self-conformance witness table for ",
    "ProtocolWitnessTableAccessor": "protocol witness table accessor for ",
    "ProtocolWitnessTable": "protocol witness table for ",
    "ProtocolWitnessTablePattern": "protocol witness table pattern for ",
    "GenericProtocolWitnessTable": "generic protocol witness table for ",
    "GenericProtocolWitnessTableInstantiationFunction": (
        "instantiation function for generic protocol witness table for "
    ),
    "ResilientProtocolWitnessTable": "resilient protocol witness table for ",
    "ProtocolSelfConformanceWitness": "protocol self-conformance witness for ",
    "GenericTypeMetadataPattern": "generic type metadata pattern for ",
    "Metaclass": "metaclass for ",
    "ProtocolSelfConformanceDescriptor": "protocol self-conformance descriptor for ",
    "ProtocolConformanceDescriptor": "protocol conformance descriptor for ",
    "ProtocolConformanceDescriptorRecord": "protocol conformance descriptor runtime record for ",
    "ProtocolDescriptor": "protocol descriptor for ",
    "ProtocolDescriptorRecord": "protocol descriptor runtime record for ",
    "ProtocolRequirementsBaseDescriptor": "protocol requirements base descriptor for ",
    "FullTypeMetadata": "full type metadata for ",
    "TypeMetadata": "type metadata for ",
    "TypeMetadataAccessFunction": "type metadata accessor for ",
    "TypeMetadataInstantiationCache": "type metadata instantiation cache for ",
    "TypeMetadataInstantiationFunction": "type metadata instantiation function for ",
    "TypeMetadataSingletonInitializationCache": ("type metadata singleton initialization cache for "),
    "TypeMetadataCompletionFunction": "type metadata completion function for ",
    "TypeMetadataDemanglingCache": "demangling cache variable for type metadata for ",
    "TypeMetadataLazyCache": "lazy cache variable for type metadata for ",
    "AssociatedTypeDescriptor": "associated type descriptor for ",
    "DefaultAssociatedTypeMetadataAccessor": "default associated type metadata accessor for ",
    "ClassMetadataBaseOffset": "class metadata base offset for ",
    "PropertyDescriptor": "property descriptor for ",
    "NominalTypeDescriptor": "nominal type descriptor for ",
    "NominalTypeDescriptorRecord": "nominal type descriptor runtime record for ",
    "OpaqueTypeDescriptor": "opaque type descriptor for ",
    "OpaqueTypeDescriptorRecord": "opaque type descriptor runtime record for ",
    "OpaqueTypeDescriptorAccessor": "opaque type descriptor accessor for ",
    "OpaqueTypeDescriptorAccessorImpl": "opaque type descriptor accessor impl for ",
    "OpaqueTypeDescriptorAccessorKey": "opaque type descriptor accessor key for ",
    "OpaqueTypeDescriptorAccessorVar": "opaque type descriptor accessor var for ",
    "CoroutineContinuationPrototype": "coroutine continuation prototype for ",
    "ValueWitnessTable": "value witness table for ",
    "SILBoxType": "@box ",
    "ReflectionMetadataBuiltinDescriptor": "reflection metadata builtin descriptor ",
    "ReflectionMetadataFieldDescriptor": "reflection metadata field descriptor ",
    "ReflectionMetadataAssocTypeDescriptor": "reflection metadata associated type descriptor ",
    "ReflectionMetadataSuperclassDescriptor": "reflection metadata superclass descriptor ",
    "ModuleDescriptor": "module descriptor ",
    "AnonymousDescriptor": "anonymous descriptor ",
    "ExtensionDescriptor": "extension descriptor ",
    "CanonicalSpecializedGenericMetaclass": "specialized generic metaclass for ",
    "CanonicalSpecializedGenericTypeMetadataAccessFunction": (
        "canonical specialized generic type metadata accessor for "
    ),
    "MetadataInstantiationCache": "metadata instantiation cache for ",
    "NoncanonicalSpecializedGenericTypeMetadata": ("noncanonical specialized generic type metadata for "),
    "NoncanonicalSpecializedGenericTypeMetadataCache": (
        "cache variable for noncanonical specialized generic type metadata for "
    ),
    "CanonicalPrespecializedGenericTypeCachingOnceToken": (
        "flag for loading of canonical specialized generic type metadata for "
    ),
    "Uniquable": "uniquable ",
}

_JUST_TEXT = {
    "NonObjCAttribute": "@nonobjc ",
    "ObjCAttribute": "@objc ",
    "DirectMethodReferenceAttribute": "super ",
    "DynamicAttribute": "dynamic ",
    "VTableAttribute": "override ",
    "IsSerialized": "serialized",
    "MetatypeParamsRemoved": "metatypes-removed",
    "BuiltinTupleType": "Builtin.TheTupleType",
    "UnknownIndex": "unknown index",
    "DynamicSelf": "Self",
    "ConstrainedExistentialSelf": "Self",
    "ErrorType": "<ERROR TYPE>",
    "ImplEscaping": "@escaping",
    "ImplErasedIsolation": "@isolated(any)",
    "ImplNonisolatedNonsendingIsolation": "@caller_isolated",
    "ImplCalledOnceFunction": "@called(once)",
    "ConcurrentFunctionType": "@Sendable ",
    "IsolatedAnyFunctionType": "@isolated(any) ",
    "NonIsolatedCallerFunctionType": "nonisolated(nonsending) ",
    "SendingResultFunctionType": "sending ",
    "ImplSendingResult": "sending",
    "AsyncAnnotation": " async",
    "ThrowsAnnotation": " throws",
    "EmptyList": " empty-list ",
    "FirstElementMarker": " first-element-marker ",
    "VariadicMarker": " variadic-marker ",
    "OpaqueReturnType": "some",
    "AsyncFunctionPointer": "async function pointer to ",
    "AsyncMainEntryPoint": "async main entry point",
    "HasSymbolQuery": "#_hasSymbol query for ",
    "MergedFunction": "merged ",
    "CoroFunctionPointer": "coro function pointer to ",
    "DefaultOverride": "default override of ",
    "DistributedThunk": "distributed thunk ",
    "DistributedAccessor": "distributed accessor for ",
    "AccessibleFunctionRecord": "accessible function runtime record for ",
    "DynamicallyReplaceableFunctionKey": "dynamically replaceable key for ",
    "DynamicallyReplaceableFunctionImpl": "dynamically replaceable thunk for ",
    "DynamicallyReplaceableFunctionVar": "dynamically replaceable variable for ",
    "BackDeploymentThunk": "back deployment thunk for ",
    "BackDeploymentFallback": "back deployment fallback for ",
    # Printed nowhere: the association is carried, not spelled.
    "AssociatedType": "",
    "LabelList": "",
    "OpaqueReturnTypeIndex": "",
    "OpaqueReturnTypeParent": "",
}

#: The `_JUST_TEXT` kinds the reference writes only `if (!Options.ShortenThunk)`.
#: Not `BackDeploymentFallback`: it names what a symbol is, not what wraps it.
_THUNK_LEADS = frozenset(
    {
        "MergedFunction",
        "DistributedThunk",
        "DistributedAccessor",
        "AccessibleFunctionRecord",
        "DynamicallyReplaceableFunctionKey",
        "DynamicallyReplaceableFunctionImpl",
        "DynamicallyReplaceableFunctionVar",
        "BackDeploymentThunk",
    }
)

#: Per declaration kind: type style, whether it has its own name, an extra name to
#: append, and a name to use instead of the node's.
_ENTITY_KINDS = {
    "Variable": ("colon", True, "", ""),
    "Function": ("function", True, "", ""),
    "BoundGenericFunction": ("function", True, "", ""),
    "Subscript": ("function", False, "", "subscript"),
    "GenericTypeParamDecl": ("none", True, "", ""),
    "Class": ("none", True, "", ""),
    "Structure": ("none", True, "", ""),
    "Enum": ("none", True, "", ""),
    "Protocol": ("none", True, "", ""),
    "TypeAlias": ("none", True, "", ""),
    "OtherNominalType": ("none", True, "", ""),
    "Initializer": ("none", False, "variable initialization expression", ""),
    "PropertyWrapperBackingInitializer": ("none", False, "property wrapper backing initializer", ""),
    "PropertyWrappedFieldInitAccessor": ("none", False, "property wrapped field init accessor", ""),
    "PropertyWrapperInitFromProjectedValue": ("none", False, "property wrapper init from projected value", ""),
    "Destructor": ("none", False, "deinit", ""),
    "IVarInitializer": ("none", False, "__ivar_initializer", ""),
    "IVarDestroyer": ("none", False, "__ivar_destroyer", ""),
}

_ABSTRACT_STORAGE = {
    "OwningAddressor": "owningAddressor",
    "OwningMutableAddressor": "owningMutableAddressor",
    "NativeOwningAddressor": "nativeOwningAddressor",
    "NativeOwningMutableAddressor": "nativeOwningMutableAddressor",
    "NativePinningAddressor": "nativePinningAddressor",
    "NativePinningMutableAddressor": "nativePinningMutableAddressor",
    "UnsafeAddressor": "unsafeAddressor",
    "UnsafeMutableAddressor": "unsafeMutableAddressor",
    "GlobalGetter": "getter",
    "Getter": "getter",
    "Setter": "setter",
    "MaterializeForSet": "materializeForSet",
    "WillSet": "willset",
    "DidSet": "didset",
    "ReadAccessor": "read",
    "YieldingBorrowAccessor": "yielding_borrow",
    "ModifyAccessor": "modify",
    "YieldingMutateAccessor": "yielding_mutate",
    "InitAccessor": "init",
    "BorrowAccessor": "borrow",
    "MutateAccessor": "mutate",
}

#: `<macro kind> @<name> expansion #<n>`, keyed by node kind.
_MACRO_EXPANSION_NAMES = {
    "AccessorAttachedMacroExpansion": "accessor macro @",
    # Verbatim from `swift/Basic/MacroRoles.def`, where this role is an identifier.
    "MemberAttributeAttachedMacroExpansion": "memberAttribute macro @",
    "MemberAttachedMacroExpansion": "member macro @",
    "PeerAttachedMacroExpansion": "peer macro @",
    "ConformanceAttachedMacroExpansion": "conformance macro @",
    "ExtensionAttachedMacroExpansion": "extension macro @",
    "PreambleAttachedMacroExpansion": "preamble macro @",
    "BodyAttachedMacroExpansion": "body macro @",
}

_SPECIALIZATION_PREFIXES = {
    "FunctionSignatureSpecialization": ("function signature specialization", ""),
    "GenericPartialSpecialization": ("generic partial specialization", "Signature = "),
    "GenericPartialSpecializationNotReAbstracted": ("generic not-reabstracted partial specialization", "Signature = "),
    "GenericSpecialization": ("generic specialization", ""),
    "GenericSpecializationInResilienceDomain": ("generic specialization", ""),
    "GenericSpecializationPrespecialized": ("generic pre-specialization", ""),
    "GenericSpecializationNotReAbstracted": ("generic not re-abstracted specialization", ""),
    "InlinedGenericFunction": ("inlined generic function", ""),
}

_LAYOUT_CONSTRAINT_NAMES = {
    "U": "_UnknownLayout",
    "R": "_RefCountedObject",
    "N": "_NativeRefCountedObject",
    "C": "AnyObject",
    "D": "_NativeClass",
    "T": "_Trivial",
    "E": "_Trivial",
    "e": "_Trivial",
    "M": "_TrivialAtMost",
    "m": "_TrivialAtMost",
}


def _handler(kind):
    def register(function):
        _HANDLERS[kind] = function
        return function

    return register


_HANDLERS = {}


def _simple(kind, spell):
    """Register a handler that writes something and returns no postfix context."""

    def run(self, node, depth, as_prefix_context):
        spell(self, node, depth)
        return None

    _HANDLERS[kind] = run


for _kind, _text in _ABSTRACT_STORAGE.items():
    _HANDLERS[_kind] = lambda self, node, depth, as_prefix_context, _name=_text: self.print_abstract_storage(
        node.first, depth, as_prefix_context, _name
    )

for _kind, (_description, _prefix) in _SPECIALIZATION_PREFIXES.items():
    _simple(
        _kind,
        lambda self, node, depth, _d=_description, _p=_prefix: self.print_specialization_prefix(node, _d, depth, _p),
    )

for _kind, _lead in _MACRO_EXPANSION_NAMES.items():
    _HANDLERS[_kind] = lambda self, node, depth, as_prefix_context, _lead=_lead: self.print_entity(
        node,
        depth,
        as_prefix_context,
        "none",
        True,
        _lead + _node_to_string(node.child(2)) + " expansion #",
        _c_int(_c_int(node.child(3).index) + 1),
    )


def _node_to_string(node):
    """`nodeToString`: spell one node on its own, for embedding in a name."""
    printer = Printer()
    try:
        printer.print(node, 0)
    except _Invalid:
        return ""
    return printer.result()


_simple("Global", lambda self, node, depth: self.print_children(node, depth))
_simple("TypeList", lambda self, node, depth: self.print_children(node, depth))
_simple("AnyProtocolConformanceList", lambda self, node, depth: _print_conformance_list(self, node, depth))
_simple("ConstrainedExistentialRequirementList", lambda self, node, depth: self.print_children(node, depth, ", "))
# The `.` after a context is written only if it produced output, so writing nothing
# (not even "") turns `Monads.Either` into `Either`.
_simple(
    "Module",
    lambda self, node, depth: self.write(node.text) if self.options.display_module_names else None,
)
_simple("Identifier", lambda self, node, depth: self.write(node.text))
_simple("ClangType", lambda self, node, depth: self.write(node.text))
_simple("BuiltinTypeName", lambda self, node, depth: self.write(node.text))
_simple("MetatypeRepresentation", lambda self, node, depth: self.write(node.text))
_simple("ImplConvention", lambda self, node, depth: self.write(node.text))
_simple("ImplFunctionAttribute", lambda self, node, depth: self.write(node.text))
_simple("Index", lambda self, node, depth: self.write(str(node.index)))
_simple("Number", lambda self, node, depth: self.write(str(node.index)))
_simple("Integer", lambda self, node, depth: self.write(str(node.index)))
_simple("NegativeInteger", lambda self, node, depth: self.write(str(node.index)))
_simple("SpecializationPassID", lambda self, node, depth: self.write(str(node.index)))
_simple("InfixOperator", lambda self, node, depth: self.write(f"{node.text} infix"))
_simple("PrefixOperator", lambda self, node, depth: self.write(f"{node.text} prefix"))
_simple("PostfixOperator", lambda self, node, depth: self.write(f"{node.text} postfix"))
_simple("OutlinedBridgedMethod", lambda self, node, depth: self.write(f"outlined bridged method ({node.text}) of "))
_simple("OutlinedVariable", lambda self, node, depth: self.write(f"outlined variable #{node.index} of "))
_simple("OutlinedReadOnlyObject", lambda self, node, depth: self.write(f"outlined read-only object #{node.index} of "))
_simple("Directness", lambda self, node, depth: self.write(f"{_DIRECTNESS[node.index]} "))
_simple("AccessorFunctionReference", lambda self, node, depth: self.write(f"accessor function at {node.index}"))
_simple("TypeSymbolicReference", lambda self, node, depth: self.write(f"type symbolic reference 0x{node.index:X}"))
_simple(
    "ProtocolSymbolicReference",
    lambda self, node, depth: self.write(f"protocol symbolic reference 0x{node.index:X}"),
)
_simple(
    "OpaqueTypeDescriptorSymbolicReference",
    lambda self, node, depth: self.write(f"opaque type symbolic reference 0x{node.index:X}"),
)
_simple(
    "UniqueExtendedExistentialTypeShapeSymbolicReference",
    lambda self, node, depth: self.write(f"unique existential shape symbolic reference 0x{node.index:X}"),
)
_simple(
    "NonUniqueExtendedExistentialTypeShapeSymbolicReference",
    lambda self, node, depth: self.write(f"non-unique existential shape symbolic reference 0x{node.index:X}"),
)


@_handler("Suffix")
def _print_suffix(self, node, depth, as_prefix_context):
    if self.options.display_unmangled_suffix:
        self.write(" with unmangled suffix " + _quoted(node.text))
    return None


@_handler("AnonymousContext")
def _print_anonymous_context(self, node, depth, as_prefix_context):
    if not self.options.display_extension_contexts:
        return None
    self.print(node.child(1), depth + 1)
    self.write(".(unknown context at ")
    self.print(node.first, depth + 1)
    self.write(")")
    if len(node.children) >= 3 and node.child(2).children:
        self.write("<")
        self.print(node.child(2), depth + 1)
        self.write(">")
    return None


@_handler("Extension")
def _print_extension(self, node, depth, as_prefix_context):
    if self.options.display_extension_contexts:
        self.write("(extension in ")
        self.print(node.first, depth + 1, True)
        self.write("):")
    self.print(node.child(1), depth + 1)
    if len(node.children) == 3:
        self.print(node.child(2), depth + 1)
    return None


@_handler("Macro")
def _print_macro(self, node, depth, as_prefix_context):
    style = "colon" if len(node.children) == 3 else "function"
    return self.print_entity(node, depth, as_prefix_context, style, True)


@_handler("FreestandingMacroExpansion")
def _print_freestanding_macro(self, node, depth, as_prefix_context):
    return self.print_entity(
        node,
        depth,
        as_prefix_context,
        "none",
        True,
        "freestanding macro expansion #",
        _c_int(_c_int(node.child(2).index) + 1),
    )


@_handler("MacroExpansionUniqueName")
def _print_macro_unique_name(self, node, depth, as_prefix_context):
    return self.print_entity(
        node, depth, as_prefix_context, "none", True, "unique name #", _c_int(_c_int(node.child(2).index) + 1)
    )


@_handler("ExplicitClosure")
def _print_explicit_closure(self, node, depth, as_prefix_context):
    # The flag that hides a function's parameters hides a closure's signature too.
    style = "function" if self.options.show_function_argument_types else "none"
    return self.print_entity(
        node, depth, as_prefix_context, style, False, "closure #", _c_int(_c_int(node.child(1).index) + 1)
    )


@_handler("ImplicitClosure")
def _print_implicit_closure(self, node, depth, as_prefix_context):
    style = "function" if self.options.show_function_argument_types else "none"
    return self.print_entity(
        node, depth, as_prefix_context, style, False, "implicit closure #", _c_int(_c_int(node.child(1).index) + 1)
    )


@_handler("DefaultArgumentInitializer")
def _print_default_argument(self, node, depth, as_prefix_context):
    return self.print_entity(
        node, depth, as_prefix_context, "none", False, "default argument ", _c_int(node.child(1).index)
    )


@_handler("Allocator")
def _print_allocator(self, node, depth, as_prefix_context):
    name = "__allocating_init" if _is_class_type(node.first) else "init"
    return self.print_entity(node, depth, as_prefix_context, "function", False, name)


@_handler("Constructor")
def _print_constructor(self, node, depth, as_prefix_context):
    return self.print_entity(node, depth, as_prefix_context, "function", len(node.children) > 2, "init")


@_handler("Deallocator")
def _print_deallocator(self, node, depth, as_prefix_context):
    name = "__deallocating_deinit" if _is_class_type(node.first) else "deinit"
    return self.print_entity(node, depth, as_prefix_context, "none", False, name)


@_handler("IsolatedDeallocator")
def _print_isolated_deallocator(self, node, depth, as_prefix_context):
    """The deinit of an actor-isolated type, which hops to its executor first."""
    name = "__isolated_deallocating_deinit" if _is_class_type(node.first) else "deinit"
    return self.print_entity(node, depth, as_prefix_context, "none", False, name)


@_handler("TypeMangling")
def _print_type_mangling(self, node, depth, as_prefix_context):
    if node.first.kind == "LabelList":
        self.print_function_type(node.first, node.child(1).first, depth)
    else:
        self.print(node.first, depth + 1)
    return None


@_handler("LocalDeclName")
def _print_local_decl_name(self, node, depth, as_prefix_context):
    self.print(node.child(1), depth + 1)
    self.write(f" #{node.first.index + 1}")
    return None


@_handler("PrivateDeclName")
def _print_private_decl_name(self, node, depth, as_prefix_context):
    show = self.options.show_private_discriminators
    if len(node.children) > 1:
        if show:
            self.write("(")
        self.print(node.child(1), depth + 1)
        if show:
            self.write(f" in {node.first.text})")
    elif show:
        self.write(f"(in {node.first.text})")
    return None


@_handler("RelatedEntityDeclName")
def _print_related_entity(self, node, depth, as_prefix_context):
    self.write(f"related decl '{node.first.text}' for ")
    self.print(node.child(1), depth + 1)
    return None


for _kind in (
    "FunctionType",
    "UncurriedFunctionType",
    "NoEscapeFunctionType",
    "AutoClosureType",
    "EscapingAutoClosureType",
    "ThinFunctionType",
    "CFunctionPointer",
    "ObjCBlock",
    "EscapingObjCBlock",
    "CalledOnceFunctionType",
):
    _simple(_kind, lambda self, node, depth: self.print_function_type(None, node, depth))

for _kind in (
    "BoundGenericClass",
    "BoundGenericStructure",
    "BoundGenericEnum",
    "BoundGenericProtocol",
    "BoundGenericOtherNominalType",
    "BoundGenericTypeAlias",
):
    _simple(_kind, lambda self, node, depth: self.print_bound_generic(node, depth))

for _kind in ("DependentGenericSignature", "DependentPseudogenericSignature"):
    _simple(_kind, lambda self, node, depth: self.print_generic_signature(node, depth))

_simple(
    "ArgumentTuple",
    lambda self, node, depth: self.print_function_parameters(
        None, node, depth, self.options.show_function_argument_types
    ),
)
_simple("ImplFunctionType", lambda self, node, depth: self.print_impl_function_type(node, depth))
_simple("TupleElementName", lambda self, node, depth: self.write(f"{node.text}: "))
_simple("AssocTypePath", lambda self, node, depth: self.print_children(node, depth, "."))


@_handler("Tuple")
def _print_tuple(self, node, depth, as_prefix_context):
    self.write("(")
    self.print_children(node, depth, ", ")
    self.write(")")
    return None


@_handler("TupleElement")
def _print_tuple_element(self, node, depth, as_prefix_context):
    label = _child_of_kind(node, "TupleElementName")
    if label is not None:
        self.write(f"{label.text}: ")
    found = _child_of_kind(node, "Type")
    if found is None:
        raise _Invalid
    self.print(found, depth + 1)
    if _child_of_kind(node, "VariadicMarker") is not None:
        self.write("...")
    return None


@_handler("Pack")
def _print_pack(self, node, depth, as_prefix_context):
    self.write("Pack{")
    self.print_children(node, depth, ", ")
    self.write("}")
    return None


for _kind, _mark in (("SILPackDirect", "@direct"), ("SILPackIndirect", "@indirect")):

    def _print_sil_pack(self, node, depth, as_prefix_context, _m=_mark):
        self.write(f"{_m} Pack{{")
        self.print_children(node, depth, ", ")
        self.write("}")
        return None

    _HANDLERS[_kind] = _print_sil_pack


@_handler("PackExpansion")
def _print_pack_expansion(self, node, depth, as_prefix_context):
    self.write("repeat ")
    self.print(node.first, depth + 1)
    return None


@_handler("PackElement")
def _print_pack_element(self, node, depth, as_prefix_context):
    self.write(f"/* level: {node.child(1).index} */ each ")
    self.print(node.first, depth + 1)
    return None


@_handler("ReturnType")
def _print_return_type(self, node, depth, as_prefix_context):
    self.write(" -> ")
    if not node.children:
        self.write(node.text)
    else:
        self.print_children(node, depth)
    return None


@_handler("RetroactiveConformance")
def _print_retroactive_conformance(self, node, depth, as_prefix_context):
    if len(node.children) != 2:
        return None
    self.write("retroactive @ ")
    self.print(node.first, depth + 1)
    self.print(node.child(1), depth + 1)
    return None


@_handler("GenericSpecializationParam")
def _print_generic_specialization_param(self, node, depth, as_prefix_context):
    self.print(node.first, depth + 1)
    for at in range(1, len(node.children)):
        self.write(" with " if at == 1 else " and ")
        self.print(node.child(at), depth + 1)
    return None


@_handler("FunctionSignatureSpecializationParamPayload")
def _print_param_payload(self, node, depth, as_prefix_context):
    """What the mangling wrote inline: an integer's digits, a string's encoding, or the
    index of the argument a closure was propagated from."""
    if node.text is None:
        self.write(str(node.index))
        return None
    self.write(_spell_payload(node.text))
    return None


@_handler("FunctionSignatureSpecializationParamKind")
def _print_param_kind(self, node, depth, as_prefix_context):
    raw = node.index
    printed = False
    for flag, name in (
        (1 << 10, "Existential To Protocol Constrained Generic"),
        (1 << 6, "Dead"),
        (1 << 7, "Owned To Guaranteed"),
        (1 << 9, "Guaranteed To Owned"),
    ):
        if raw & flag:
            if printed:
                self.write(" and ")
            printed = True
            self.write(name)
    if raw & (1 << 8):
        if printed:
            self.write(" and ")
        self.write("Exploded")
        return None
    if printed:
        return None
    self.write(_PARAM_KIND_NAMES[raw])
    return None


_PARAM_KIND_NAMES = {
    _PARAM_BOX_TO_VALUE: "Value Promoted from Box",
    _PARAM_BOX_TO_STACK: "Stack Promoted from Box",
    _PARAM_IN_OUT_TO_OUT: "InOut Converted to Out",
    _PARAM_CONSTANT_PROP_FUNCTION: "Constant Propagated Function",
    _PARAM_CONSTANT_PROP_GLOBAL: "Constant Propagated Global",
    _PARAM_CONSTANT_PROP_INTEGER: "Constant Propagated Integer",
    _PARAM_CONSTANT_PROP_FLOAT: "Constant Propagated Float",
    _PARAM_CONSTANT_PROP_STRING: "Constant Propagated String",
    _PARAM_CONSTANT_PROP_KEY_PATH: "Constant Propagated KeyPath",
    _PARAM_CLOSURE_PROP: "Closure Propagated",
    _PARAM_CONSTANT_PROP_STRUCT: "Constant Propagated Struct",
    _PARAM_CLOSURE_PROP_PREVIOUS_ARG: "Same As Argument",
    _PARAM_ESCAPING_CLOSURE_PROP: "Escaping Closure Propagated",
}


for _kind, _lead in (
    ("LazyProtocolWitnessTableAccessor", "lazy protocol witness table accessor for type "),
    ("LazyProtocolWitnessTableCacheVariable", "lazy protocol witness table cache variable for type "),
):

    def _print_lazy_witness_table(self, node, depth, as_prefix_context, _l=_lead):
        self.write(_l)
        self.print(node.first, depth + 1)
        self.write(" and conformance ")
        self.print(node.child(1), depth + 1)
        return None

    _HANDLERS[_kind] = _print_lazy_witness_table


def _print_conformance_list(self, node, depth):
    """A bracketed, comma-separated list -- and nothing at all when it is empty."""
    if not node.children:
        return
    self.write("(")
    self.print_children(node, depth, ", ")
    self.write(")")


@_handler("PackProtocolConformance")
def _print_pack_protocol_conformance(self, node, depth, as_prefix_context):
    self.write("pack protocol conformance ")
    self.print_children(node, depth)
    return None


@_handler("DependentProtocolConformanceOpaque")
def _print_dependent_protocol_conformance_opaque(self, node, depth, as_prefix_context):
    self.write("opaque result conformance ")
    self.print(node.first, depth + 1)
    self.write(" of ")
    self.print(node.child(1), depth + 1)
    return None


@_handler("MacroExpansionLoc")
def _print_macro_expansion_loc(self, node, depth, as_prefix_context):
    """Where in the source an expansion came from, as far as the name records it."""
    for at, lead in enumerate(("module ", " file ", " line ", " column ")):
        if at >= len(node.children):
            break
        self.write(lead)
        self.print(node.child(at), depth + 1)
    return None


@_handler("VTableThunk")
def _print_vtable_thunk(self, node, depth, as_prefix_context):
    self.write("vtable thunk for ")
    self.print(node.child(1), depth + 1)
    self.write(" dispatching to ")
    self.print(node.first, depth + 1)
    return None


@_handler("ProtocolWitness")
def _print_protocol_witness(self, node, depth, as_prefix_context):
    self.write("protocol witness for ")
    self.print(node.child(1), depth + 1)
    self.write(" in conformance ")
    self.print(node.first, depth + 1)
    return None


for _kind, _lead in (
    ("PartialApplyForwarder", "partial apply forwarder"),
    ("PartialApplyObjCForwarder", "partial apply ObjC forwarder"),
):

    def _print_partial_apply(self, node, depth, as_prefix_context, _l=_lead):
        self.write(_l if self.options.shorten_partial_apply else "partial apply")
        if node.children:
            self.write(" for ")
            self.print_children(node, depth)
        return None

    _HANDLERS[_kind] = _print_partial_apply


for _kind, _lead in (
    ("KeyPathGetterThunkHelper", "key path getter for "),
    ("KeyPathSetterThunkHelper", "key path setter for "),
    # The method thunks say neither "for" nor "of": the reference's own wording.
    ("KeyPathUnappliedMethodThunkHelper", "key path unapplied method "),
    ("KeyPathAppliedMethodThunkHelper", "key path applied method "),
):

    def _print_key_path_accessor(self, node, depth, as_prefix_context, _l=_lead):
        self.write(_l)
        self.print(node.first, depth + 1)
        self.write(" : ")
        for at in range(1, len(node.children)):
            child = node.child(at)
            if child.kind == "IsSerialized":
                self.write(", ")
            self.print(child, depth + 1)
        return None

    _HANDLERS[_kind] = _print_key_path_accessor


for _kind, _word in (("KeyPathEqualsThunkHelper", "equality"), ("KeyPathHashThunkHelper", "hash")):

    def _print_key_path_index(self, node, depth, as_prefix_context, _w=_word):
        self.write(f"key path index {_w} operator for ")
        last = len(node.children)
        if node.child(last - 1).kind == "IsSerialized":
            last -= 1
        if node.child(last - 1).kind == "DependentGenericSignature":
            self.print(node.child(last - 1), depth + 1)
            last -= 1
        self.write("(")
        for at in range(last):
            if at:
                self.write(", ")
            self.print(node.child(at), depth + 1)
        self.write(")")
        return None

    _HANDLERS[_kind] = _print_key_path_index


@_handler("FieldOffset")
def _print_field_offset(self, node, depth, as_prefix_context):
    self.print(node.first, depth + 1)
    self.write("field offset for ")
    self.print(node.child(1), depth + 1, False)
    return None


@_handler("EnumCase")
def _print_enum_case(self, node, depth, as_prefix_context):
    self.write("enum case for ")
    self.print(node.first, depth + 1, False)
    return None


for _kind, _helper in (("ReabstractionThunk", False), ("ReabstractionThunkHelper", True)):

    def _print_reabstraction_thunk(self, node, depth, as_prefix_context, _h=_helper):
        if not self.options.shorten_thunk:
            self.write("thunk for ")
            self.print(node.last, depth + 1)
            return None
        self.write("reabstraction thunk ")
        if _h:
            self.write("helper ")
        at = 0
        if len(node.children) == 3:
            at = 1
            self.print(node.first, depth + 1)
            self.write(" ")
        self.write("from ")
        self.print(node.child(at + 1), depth + 1)
        self.write(" to ")
        self.print(node.child(at), depth + 1)
        return None

    _HANDLERS[_kind] = _print_reabstraction_thunk


@_handler("ReabstractionThunkHelperWithGlobalActor")
def _print_thunk_with_global_actor(self, node, depth, as_prefix_context):
    self.print(node.first, depth + 1)
    self.write(" with global actor constraint ")
    self.print(node.child(1), depth + 1)
    return None


@_handler("ReabstractionThunkHelperWithSelf")
def _print_thunk_with_self(self, node, depth, as_prefix_context):
    self.write("reabstraction thunk ")
    at = 0
    if len(node.children) == 4:
        at = 1
        self.print(node.first, depth + 1)
        self.write(" ")
    self.write("from ")
    self.print(node.child(at + 2), depth + 1)
    self.write(" to ")
    self.print(node.child(at + 1), depth + 1)
    self.write(" self ")
    self.print(node.child(at), depth + 1)
    return None


for _kind, _lead in (("AutoDiffFunction", ""), ("AutoDiffDerivativeVTableThunk", "vtable thunk for ")):

    def _print_auto_diff_function(self, node, depth, as_prefix_context, _l=_lead):
        end = 0
        while end != len(node.children) and node.child(end).kind != "AutoDiffFunctionKind":
            end += 1
        function_kind = node.child(end)
        parameters = node.child(end + 1)
        results = node.child(end + 2)
        self.write(_l)
        self.print(function_kind, depth + 1)
        self.write(" of ")
        signature = None
        for at in range(end):
            if at == end - 1 and node.child(at).kind == "DependentGenericSignature":
                signature = node.child(at)
                break
            self.print(node.child(at), depth + 1)
        if not self.options.shorten_thunk:
            return None
        self.write(" with respect to parameters ")
        self.print(parameters, depth + 1)
        self.write(" and results ")
        self.print(results, depth + 1)
        if signature is not None and self.options.display_where_clauses:
            self.write(" with ")
            self.print(signature, depth + 1)
        return None

    _HANDLERS[_kind] = _print_auto_diff_function


@_handler("AutoDiffSelfReorderingReabstractionThunk")
def _print_auto_diff_self_reordering(self, node, depth, as_prefix_context):
    self.write("autodiff self-reordering reabstraction thunk ")
    at = 0
    from_type = node.child(at)
    at += 1
    to_type = node.child(at)
    at += 1
    if not self.options.shorten_thunk:
        self.write("for ")
        self.print(from_type, depth + 1)
        return None
    signature = None
    if node.child(at).kind == "DependentGenericSignature":
        signature = node.child(at)
        at += 1
    self.write("for ")
    self.print(node.child(at), depth + 1)
    if signature is not None:
        self.print(signature, depth + 1)
        self.write(" ")
    self.write(" from ")
    self.print(from_type, depth + 1)
    self.write(" to ")
    self.print(to_type, depth + 1)
    return None


@_handler("AutoDiffSubsetParametersThunk")
def _print_auto_diff_subset(self, node, depth, as_prefix_context):
    # The four trailing children are the kind and three index subsets; at least one more
    # must name what is thunked, or the "from" clause comes out empty.
    if len(node.children) < 5:
        raise _Invalid
    self.write("autodiff subset parameters thunk for ")
    at = len(node.children) - 1
    to_parameters = node.child(at)
    at -= 1
    results = node.child(at)
    at -= 1
    parameters = node.child(at)
    at -= 1
    self.print(node.child(at), depth + 1)
    at -= 1
    self.write(" from ")
    if at == 0:
        self.print(node.first, depth + 1)
    else:
        for each in range(at):
            self.print(node.child(each), depth + 1)
    if not self.options.shorten_thunk:
        return None
    self.write(" with respect to parameters ")
    self.print(parameters, depth + 1)
    self.write(" and results ")
    self.print(results, depth + 1)
    self.write(" to parameters ")
    self.print(to_parameters, depth + 1)
    if at > 0:
        self.write(" of type ")
        self.print(node.child(at), depth + 1)
    return None


_simple("AutoDiffFunctionKind", lambda self, node, depth: self.write(_AUTO_DIFF_KINDS[node.index]))


@_handler("DifferentiabilityWitness")
def _print_differentiability_witness(self, node, depth, as_prefix_context):
    back = 4 if node.last.kind == "DependentGenericSignature" else 3
    at = len(node.children) - back
    self.write(_DIFFERENTIABILITY_WITNESS_KINDS[node.child(at).index])
    self.write(" differentiability witness for ")
    at = 0
    while at < len(node.children) and node.child(at).kind != "Index":
        self.print(node.child(at), depth + 1)
        at += 1
    at += 1
    self.write(" with respect to parameters ")
    self.print(node.child(at), depth + 1)
    at += 1
    self.write(" and results ")
    self.print(node.child(at), depth + 1)
    at += 1
    if at < len(node.children):
        self.write(" with ")
        self.print(node.child(at), depth + 1)
    return None


_DIFFERENTIABILITY_WITNESS_KINDS = {
    ord("f"): "forward-mode",
    ord("r"): "reverse-mode",
    ord("d"): "normal",
    ord("l"): "linear",
}


@_handler("IndexSubset")
def _print_index_subset(self, node, depth, as_prefix_context):
    self.write("{")
    printed = False
    for at, char in enumerate(node.text):
        if char != "S":
            continue
        if printed:
            self.write(", ")
        self.write(str(at))
        printed = True
    self.write("}")
    return None


@_handler("AssociatedConformanceDescriptor")
def _print_associated_conformance_descriptor(self, node, depth, as_prefix_context):
    return _associated_conformance(self, node, depth, "associated conformance descriptor for ")


@_handler("DefaultAssociatedConformanceAccessor")
def _print_default_associated_conformance(self, node, depth, as_prefix_context):
    return _associated_conformance(self, node, depth, "default associated conformance accessor for ")


def _associated_conformance(self, node, depth, lead):
    self.write(lead)
    self.print(node.first, depth + 1)
    self.write(".")
    self.print(node.child(1), depth + 1)
    self.write(": ")
    self.print(node.child(2), depth + 1)
    return None


@_handler("AssociatedTypeMetadataAccessor")
def _print_associated_type_metadata_accessor(self, node, depth, as_prefix_context):
    self.write("associated type metadata accessor for ")
    self.print(node.child(1), depth + 1)
    self.write(" in ")
    self.print(node.first, depth + 1)
    return None


@_handler("BaseConformanceDescriptor")
def _print_base_conformance_descriptor(self, node, depth, as_prefix_context):
    self.write("base conformance descriptor for ")
    self.print(node.first, depth + 1)
    self.write(": ")
    self.print(node.child(1), depth + 1)
    return None


@_handler("AssociatedTypeWitnessTableAccessor")
def _print_associated_type_witness_table(self, node, depth, as_prefix_context):
    self.write("associated type witness table accessor for ")
    self.print(node.child(1), depth + 1)
    self.write(" : ")
    self.print(node.child(2), depth + 1)
    self.write(" in ")
    self.print(node.first, depth + 1)
    return None


@_handler("BaseWitnessTableAccessor")
def _print_base_witness_table_accessor(self, node, depth, as_prefix_context):
    self.write("base witness table accessor for ")
    self.print(node.child(1), depth + 1)
    self.write(" in ")
    self.print(node.first, depth + 1)
    return None


@_handler("ValueWitness")
def _print_value_witness(self, node, depth, as_prefix_context):
    self.write(_VALUE_WITNESS_SPELLINGS[node.first.index])
    self.write(" value witness for " if self.options.shorten_value_witness else " for ")
    self.print(node.child(1), depth + 1)
    return None


@_handler("Metatype")
def _print_metatype(self, node, depth, as_prefix_context):
    at = 0
    if len(node.children) == 2:
        self.print(node.first, depth + 1)
        self.write(" ")
        at = 1
    found = node.child(at).first
    self.print_with_parens(found, depth)
    self.write(".Protocol" if found.kind in _EXISTENTIAL_TYPES else ".Type")
    return None


@_handler("ExistentialMetatype")
def _print_existential_metatype(self, node, depth, as_prefix_context):
    at = 0
    if len(node.children) == 2:
        self.print(node.first, depth + 1)
        self.write(" ")
        at = 1
    self.print(node.child(at), depth + 1)
    self.write(".Type")
    return None


@_handler("ConstrainedExistential")
def _print_constrained_existential(self, node, depth, as_prefix_context):
    self.write("any ")
    self.print(node.first, depth + 1)
    self.write("<")
    self.print(node.child(1), depth + 1)
    self.write(">")
    return None


@_handler("AssociatedTypeRef")
def _print_associated_type_ref(self, node, depth, as_prefix_context):
    self.print(node.first, depth + 1)
    self.write("." + node.child(1).text)
    return None


@_handler("ProtocolList")
def _print_protocol_list(self, node, depth, as_prefix_context):
    types = node.first if node.children else None
    if types is None:
        return None
    if not types.children:
        self.write("Any")
    else:
        self.print_children(types, depth, " & ")
    return None


@_handler("ProtocolListWithClass")
def _print_protocol_list_with_class(self, node, depth, as_prefix_context):
    if len(node.children) < 2:
        return None
    protocols = node.first
    self.print(node.child(1), depth + 1)
    self.write(" & ")
    if not protocols.children:
        return None
    self.print_children(protocols.first, depth, " & ")
    return None


@_handler("ProtocolListWithAnyObject")
def _print_protocol_list_with_any_object(self, node, depth, as_prefix_context):
    if not node.children:
        return None
    protocols = node.first
    if not protocols.children:
        return None
    types = protocols.first
    if types.children:
        self.print_children(types, depth, " & ")
        self.write(" & ")
    self.write(f"{STDLIB_NAME}.AnyObject")
    return None


@_handler("ProtocolConformance")
def _print_protocol_conformance(self, node, depth, as_prefix_context):
    if len(node.children) == 4:
        self.write("property behavior storage of ")
        self.print(node.child(2), depth + 1)
        self.write(" in ")
        self.print(node.first, depth + 1)
        self.write(" : ")
        self.print(node.child(1), depth + 1)
    else:
        self.print(node.first, depth + 1)
        if self.options.display_protocol_conformances:
            self.write(" : ")
            self.print(node.child(1), depth + 1)
            self.write(" in ")
            self.print(node.child(2), depth + 1)
    return None


@_handler("ImplDifferentiabilityKind")
def _print_impl_differentiability(self, node, depth, as_prefix_context):
    self.write("@differentiable")
    self.write(_IMPL_DIFFERENTIABILITY.get(node.index, ""))
    return None


_IMPL_DIFFERENTIABILITY = {ord("l"): "(_linear)", ord("f"): "(_forward)", ord("r"): "(reverse)"}


@_handler("ImplParameterResultDifferentiability")
@_handler("ImplParameterSending")
@_handler("ImplParameterIsolated")
@_handler("ImplParameterImplicitLeading")
def _print_impl_parameter_differentiability(self, node, depth, as_prefix_context):
    """A marker on one lowered parameter. Empty text is the default and prints nothing."""
    if node.text:
        self.write(node.text + " ")
    return None


@_handler("ImplCoroutineKind")
def _print_impl_coroutine_kind(self, node, depth, as_prefix_context):
    if node.text:
        self.write("@" + node.text)
    return None


@_handler("ImplFunctionConvention")
def _print_impl_function_convention(self, node, depth, as_prefix_context):
    self.write("@convention(")
    self.write(node.first.text)
    if len(node.children) == 2:
        self.write(', mangledCType: "')
        self.print(node.child(1), depth + 1)
        self.write('"')
    self.write(")")
    return None


for _kind, _lead in (("ImplErrorResult", "@error "), ("ImplYield", "@yields ")):

    def _print_impl_result(self, node, depth, as_prefix_context, _l=_lead):
        self.write(_l)
        self.print_children(node, depth, " ")
        return None

    _HANDLERS[_kind] = _print_impl_result


for _kind in ("ImplParameter", "ImplResult"):

    def _print_impl_parameter(self, node, depth, as_prefix_context):
        # `convention, marker*, type`; differentiability is always a marker. The
        # reference spells the markers at three and four children and none at five or
        # more (`$sBAIgHgIL_BAIegHgIL_TR`), so this follows the count, not each marker.
        self.print(node.first, depth + 1)
        self.write(" ")
        if len(node.children) in (3, 4):
            for child in node.children[1:-1]:
                self.print(child, depth + 1)
        self.print(node.last, depth + 1)
        return None

    _HANDLERS[_kind] = _print_impl_parameter


@_handler("ImplInvocationSubstitutions")
def _print_impl_invocation_substitutions(self, node, depth, as_prefix_context):
    self.write("for <")
    self.print_children(node.first, depth, ", ")
    self.write(">")
    return None


@_handler("ImplPatternSubstitutions")
def _print_impl_pattern_substitutions(self, node, depth, as_prefix_context):
    self.write("@substituted ")
    self.print(node.first, depth + 1)
    self.write(" for <")
    self.print_children(node.child(1), depth, ", ")
    self.write(">")
    return None


@_handler("DependentGenericConformanceRequirement")
def _print_conformance_requirement(self, node, depth, as_prefix_context):
    self.print(node.first, depth + 1)
    self.write(": ")
    self.print(node.child(1), depth + 1)
    return None


@_handler("DependentGenericLayoutRequirement")
def _print_layout_requirement(self, node, depth, as_prefix_context):
    self.print(node.first, depth + 1)
    self.write(": ")
    self.write(_LAYOUT_CONSTRAINT_NAMES.get(node.child(1).text, ""))
    if len(node.children) > 2:
        self.write("(")
        self.print(node.child(2), depth + 1)
        if len(node.children) > 3:
            self.write(", ")
            self.print(node.child(3), depth + 1)
        self.write(")")
    return None


@_handler("DependentGenericSameTypeRequirement")
def _print_same_type_requirement(self, node, depth, as_prefix_context):
    self.print(node.first, depth + 1)
    self.write(" == ")
    self.print(node.child(1), depth + 1)
    return None


@_handler("DependentGenericSameShapeRequirement")
def _print_same_shape_requirement(self, node, depth, as_prefix_context):
    self.print(node.first, depth + 1)
    self.write(".shape == ")
    self.print(node.child(1), depth + 1)
    self.write(".shape")
    return None


@_handler("DependentGenericParamType")
def _print_generic_param_type(self, node, depth, as_prefix_context):
    self.write(generic_parameter_name(node.first.index, node.child(1).index))
    return None


@_handler("DependentGenericType")
def _print_dependent_generic_type(self, node, depth, as_prefix_context):
    self.print(node.first, depth + 1)
    if _needs_space_before_type(node.child(1)):
        self.write(" ")
    self.print(node.child(1), depth + 1)
    return None


@_handler("DependentMemberType")
def _print_dependent_member_type(self, node, depth, as_prefix_context):
    self.print(node.first, depth + 1)
    self.write(".")
    self.print(node.child(1), depth + 1)
    return None


@_handler("DependentAssociatedTypeRef")
def _print_dependent_associated_type_ref(self, node, depth, as_prefix_context):
    if len(node.children) > 1:
        self.print(node.child(1), depth + 1)
        self.write(".")
    self.print(node.first, depth + 1)
    return None


@_handler("DifferentiableFunctionType")
def _print_differentiable_function_type(self, node, depth, as_prefix_context):
    self.write("@differentiable")
    self.write(_IMPL_DIFFERENTIABILITY.get(node.index, ""))
    self.write(" ")
    return None


#: `InvertibleProtocols.def`: the bit each suppressible conformance is written as.
_INVERTIBLE_PROTOCOLS = {0: "Swift.Copyable", 1: "Swift.Escapable"}


@_handler("DependentGenericInverseConformanceRequirement")
def _print_inverse_conformance(self, node, depth, as_prefix_context):
    """`A: ~Swift.Copyable` -- a conformance the declaration suppresses.

    The protocol is written as a bit index rather than a name, so one this reader has
    never heard of still prints as the bit it is, which is what the reference does.
    """
    self.print(node.first, depth + 1)
    self.write(": ~")
    bit = node.child(1).index
    self.write(_INVERTIBLE_PROTOCOLS.get(bit, f"Swift.<bit {bit}>"))
    return None


@_handler("BuiltinFixedArray")
def _print_builtin_fixed_array(self, node, depth, as_prefix_context):
    self.write("Builtin.FixedArray<")
    self.print(node.first, depth + 1)
    self.write(", ")
    self.print(node.child(1), depth + 1)
    self.write(">")
    return None


@_handler("BuiltinBorrow")
def _print_builtin_borrow(self, node, depth, as_prefix_context):
    self.write("Builtin.Borrow<")
    self.print(node.first, depth + 1)
    self.write(">")
    return None


@_handler("TypedThrowsAnnotation")
def _print_typed_throws_annotation(self, node, depth, as_prefix_context):
    """Swift 6 typed throws: the error type is named rather than implied."""
    self.write(" throws(")
    if len(node.children) == 1:
        self.print(node.first, depth + 1)
    self.write(")")
    return None


@_handler("GlobalActorFunctionType")
def _print_global_actor_function_type(self, node, depth, as_prefix_context):
    if node.children:
        self.write("@")
        self.print(node.first, depth + 1)
        self.write(" ")
    return None


@_handler("SILBoxTypeWithLayout")
def _print_sil_box_with_layout(self, node, depth, as_prefix_context):
    arguments = None
    if len(node.children) == 3:
        arguments = node.child(2)
        self.print(node.child(1), depth + 1)
        self.write(" ")
    self.print(node.first, depth + 1)
    if arguments is not None:
        self.write(" <")
        for at, child in enumerate(arguments.children):
            if at:
                self.write(", ")
            self.print(child, depth + 1)
        self.write(">")
    return None


@_handler("SILBoxLayout")
def _print_sil_box_layout(self, node, depth, as_prefix_context):
    self.write("{")
    for at, child in enumerate(node.children):
        if at:
            self.write(",")
        self.write(" ")
        self.print(child, depth + 1)
    self.write(" }")
    return None


for _kind, _word in (("SILBoxImmutableField", "let "), ("SILBoxMutableField", "var ")):

    def _print_sil_box_field(self, node, depth, as_prefix_context, _w=_word):
        self.write(_w)
        self.print(node.first, depth + 1)
        return None

    _HANDLERS[_kind] = _print_sil_box_field


@_handler("AssociatedTypeGenericParamRef")
def _print_associated_type_generic_param_ref(self, node, depth, as_prefix_context):
    self.write("generic parameter reference for associated type ")
    self.print_children(node, depth)
    return None


@_handler("ConcreteProtocolConformance")
def _print_concrete_protocol_conformance(self, node, depth, as_prefix_context):
    self.write("concrete protocol conformance ")
    if node.index is not None:
        self.write(f"#{node.index} ")
    self.print(node.first, depth + 1)
    self.write(" to ")
    self.print(node.child(1), depth + 1)
    conditional = node.child(2)
    if conditional is not None and conditional.children:
        self.write(" with conditional requirements: ")
        self.print(conditional, depth + 1)
    return None


@_handler("DependentAssociatedConformance")
def _print_dependent_associated_conformance(self, node, depth, as_prefix_context):
    self.write("dependent associated conformance ")
    self.print_children(node, depth)
    return None


for _kind, _lead in (
    ("DependentProtocolConformanceAssociated", "dependent associated protocol conformance "),
    ("DependentProtocolConformanceInherited", "dependent inherited protocol conformance "),
    ("DependentProtocolConformanceRoot", "dependent root protocol conformance "),
):

    def _print_dependent_conformance(self, node, depth, as_prefix_context, _l=_lead):
        self.write(_l)
        self.print_optional_index(node.child(2))
        self.print(node.first, depth + 1)
        # The reference writes " to " between conforming type and protocol.
        self.write(" to ")
        self.print(node.child(1), depth + 1)
        return None

    _HANDLERS[_kind] = _print_dependent_conformance


for _kind, _lead in (
    ("ProtocolConformanceRefInTypeModule", "protocol conformance ref (type's module) "),
    ("ProtocolConformanceRefInProtocolModule", "protocol conformance ref (protocol's module) "),
    ("ProtocolConformanceRefInOtherModule", "protocol conformance ref (retroactive) "),
):

    def _print_conformance_ref(self, node, depth, as_prefix_context, _l=_lead):
        self.write(_l)
        self.print_children(node, depth)
        return None

    _HANDLERS[_kind] = _print_conformance_ref


@_handler("SugaredOptional")
def _print_sugared_optional(self, node, depth, as_prefix_context):
    self.print_with_parens(node.first, depth)
    self.write("?")
    return None


@_handler("SugaredArray")
def _print_sugared_array(self, node, depth, as_prefix_context):
    self.write("[")
    self.print(node.first, depth + 1)
    self.write("]")
    return None


@_handler("SugaredInlineArray")
def _print_sugared_inline_array(self, node, depth, as_prefix_context):
    """`XSA` -- `[3 of Swift.Int]`, the count first."""
    self.write("[")
    self.print(node.first, depth + 1)
    self.write(" of ")
    self.print(node.child(1), depth + 1)
    self.write("]")
    return None


@_handler("SugaredDictionary")
def _print_sugared_dictionary(self, node, depth, as_prefix_context):
    self.write("[")
    self.print(node.first, depth + 1)
    self.write(" : ")
    self.print(node.child(1), depth + 1)
    self.write("]")
    return None


@_handler("SugaredParen")
def _print_sugared_paren(self, node, depth, as_prefix_context):
    self.write("(")
    self.print(node.first, depth + 1)
    self.write(")")
    return None


@_handler("OpaqueReturnTypeOf")
def _print_opaque_return_type_of(self, node, depth, as_prefix_context):
    self.write("<<opaque return type of ")
    self.print_children(node, depth)
    self.write(">>")
    return None


@_handler("OpaqueType")
def _print_opaque_type(self, node, depth, as_prefix_context):
    self.print(node.first, depth + 1)
    self.write(".")
    self.print(node.child(1), depth + 1)
    return None


for _kind, _lead in (
    ("GlobalVariableOnceToken", "one-time initialization token for "),
    ("GlobalVariableOnceFunction", "one-time initialization function for "),
):

    def _print_global_variable_once(self, node, depth, as_prefix_context, _l=_lead):
        self.write(_l)
        self.print(node.child(1), depth + 1)
        return None

    _HANDLERS[_kind] = _print_global_variable_once


@_handler("GlobalVariableOnceDeclList")
def _print_once_decl_list(self, node, depth, as_prefix_context):
    if len(node.children) == 1:
        self.print(node.first, depth + 1)
    else:
        self.write("(")
        for at, child in enumerate(node.children):
            if at:
                self.write(", ")
            self.print(child, depth + 1)
        self.write(")")
    return None


for _kind, _lead in (
    ("ObjCAsyncCompletionHandlerImpl", ""),
    ("CheckedObjCAsyncCompletionHandlerImpl", "checked "),
):

    def _print_objc_completion_handler(self, node, depth, as_prefix_context, _l=_lead):
        self.write(_l)
        self.write("@objc completion handler block implementation for ")
        if len(node.children) >= 4:
            self.print(node.child(3), depth + 1)
        self.print(node.first, depth + 1)
        self.write(" with result type ")
        self.print(node.child(1), depth + 1)
        self.write(_ERROR_FLAG_SPELLINGS.get(node.child(2).index, " <invalid error flag>"))
        return None

    _HANDLERS[_kind] = _print_objc_completion_handler


_ERROR_FLAG_SPELLINGS = {0: "", 1: " nonzero on error", 2: " zero on error"}


for _kind, _lead in (
    ("AsyncAwaitResumePartialFunction", " await resume partial function for "),
    ("AsyncSuspendResumePartialFunction", " suspend resume partial function for "),
):

    def _print_async_resume(self, node, depth, as_prefix_context, _l=_lead):
        if not self.options.show_async_resume_partial:
            return None
        self.write("(")
        self.print(node.first, depth + 1)
        self.write(")")
        self.write(_l)
        return None

    _HANDLERS[_kind] = _print_async_resume


@_handler("ExtendedExistentialTypeShape")
def _print_extended_existential_shape(self, node, depth, as_prefix_context):
    signature = node.first if len(node.children) == 2 else None
    found = node.last
    self.write("existential shape for ")
    if signature is not None:
        self.print(signature, depth + 1)
        self.write(" ")
    self.write("any ")
    self.print(found, depth + 1)
    return None


@_handler("SymbolicExtendedExistentialType")
def _print_symbolic_existential(self, node, depth, as_prefix_context):
    shape = node.first
    unique = shape.kind == "UniqueExtendedExistentialTypeShapeSymbolicReference"
    self.write(f"symbolic existential type ({'' if unique else 'non-'}unique) 0x{shape.index:X} <")
    self.print(node.child(1), depth + 1)
    if len(node.children) > 2:
        self.write(", ")
        self.print(node.child(2), depth + 1)
    self.write(">")
    return None


def print_root(root, options=DEFAULT_OPTIONS):
    """Spell a whole tree, or return `""` if it is one the printer cannot spell."""
    if root is None:
        return ""
    printer = Printer(options)
    try:
        printer.print(root, 0)
    except (_Invalid, IndexError, AttributeError, KeyError, RecursionError):
        # The reference asserts on a malformed tree.
        return ""
    return printer.result()


for _kind, _lead in (("OutlinedCopy", "outlined copy of "), ("OutlinedConsume", "outlined consume of ")):

    def _print_outlined_copy(self, node, depth, as_prefix_context, _l=_lead):
        # Alone among the outlined value operations, these may carry a generic signature.
        self.write(_l)
        self.print(node.first, depth + 1)
        if len(node.children) > 1:
            self.print(node.child(1), depth + 1)
        return None

    _HANDLERS[_kind] = _print_outlined_copy


for _kind, _lead in (
    ("OutlinedEnumGetTag", "outlined enum get tag of "),
    ("OutlinedEnumTagStore", "outlined enum tag store of "),
    ("OutlinedEnumProjectDataForLoad", "outlined enum project data for load of "),
):

    def _print_outlined_enum_operation(self, node, depth, as_prefix_context, _l=_lead):
        """The operand only. A generic signature, and for two of the three the enum case
        index, are read and kept on the node; the reference spells neither."""
        self.write(_l)
        self.print(node.first, depth + 1)
        return None

    _HANDLERS[_kind] = _print_outlined_enum_operation
