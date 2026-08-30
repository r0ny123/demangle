"""The demangling tree Swift's own demangler builds.

A Swift symbol is not read into a string. The compiler's demangler builds a tree of
`Node`s, each with a kind, optionally a text or an index, and children; a separate printer
walks that tree to produce the spelling. Two things force this shape:

* The mangling is **postfix**. `demangleOperator` pushes onto a stack and operators pop
  their operands back off, so an operand is read long before the thing that consumes it.
  There is no point at which a string could be accumulated left to right.
* The printer's decisions depend on *context* -- whether a type is a function's result,
  whether a class is being named as a type or as a declaration's parent -- which is not
  available where the type is read.

The kind names are the compiler's own, transcribed from `DemangleNodes.def`, and the two
predicates below come from the same file: `CONTEXT_NODE` marks the kinds that can be a
parent for a nested declaration, which is what `popContext` looks for.
"""

__all__ = [
    "CONTEXT_KINDS",
    "FUNCTION_ATTR_KINDS",
    "Node",
    "is_any_generic",
    "is_decl_name",
    "is_entity",
    "is_requirement",
]

ALL_KINDS = frozenset(
    [
        "Allocator",
        "AnonymousContext",
        "AnyProtocolConformanceList",
        "ArgumentTuple",
        "AssociatedType",
        "AssociatedTypeRef",
        "AssociatedTypeMetadataAccessor",
        "DefaultAssociatedTypeMetadataAccessor",
        "AccessorAttachedMacroExpansion",
        "AssociatedTypeWitnessTableAccessor",
        "BaseWitnessTableAccessor",
        "AutoClosureType",
        "BoundGenericClass",
        "BoundGenericEnum",
        "BoundGenericStructure",
        "BoundGenericProtocol",
        "BoundGenericOtherNominalType",
        "BoundGenericTypeAlias",
        "BoundGenericFunction",
        "BuiltinTypeName",
        "BuiltinTupleType",
        "CFunctionPointer",
        "ClangType",
        "Class",
        "ClassMetadataBaseOffset",
        "ConcreteProtocolConformance",
        "ConformanceAttachedMacroExpansion",
        "Constructor",
        "CoroutineContinuationPrototype",
        "Deallocator",
        "DeclContext",
        "DefaultArgumentInitializer",
        "DependentAssociatedConformance",
        "DependentAssociatedTypeRef",
        "DependentGenericConformanceRequirement",
        "DependentGenericParamCount",
        "DependentGenericParamType",
        "DependentGenericSameTypeRequirement",
        "DependentGenericSameShapeRequirement",
        "DependentGenericLayoutRequirement",
        "DependentGenericParamPackMarker",
        "DependentGenericParamValueMarker",
        "DependentGenericSignature",
        "DependentGenericType",
        "DependentMemberType",
        "DependentPseudogenericSignature",
        "DependentProtocolConformanceRoot",
        "DependentProtocolConformanceInherited",
        "DependentProtocolConformanceAssociated",
        "DependentProtocolConformanceOpaque",
        "PackProtocolConformance",
        "AsyncRemoved",
        "RepresentationChanged",
        "DroppedArgument",
        "MacroExpansionLoc",
        "Destructor",
        "DidSet",
        "Directness",
        "DistributedThunk",
        "DistributedAccessor",
        "DynamicAttribute",
        "DirectMethodReferenceAttribute",
        "DynamicSelf",
        "DynamicallyReplaceableFunctionImpl",
        "DynamicallyReplaceableFunctionKey",
        "DynamicallyReplaceableFunctionVar",
        "Enum",
        "EnumCase",
        "ErrorType",
        "EscapingAutoClosureType",
        "NoEscapeFunctionType",
        "ConcurrentFunctionType",
        "GlobalActorFunctionType",
        "DifferentiableFunctionType",
        "ExistentialMetatype",
        "ExplicitClosure",
        "Extension",
        "ExtensionAttachedMacroExpansion",
        "PreambleAttachedMacroExpansion",
        "BodyAttachedMacroExpansion",
        "FieldOffset",
        "FreestandingMacroExpansion",
        "FullTypeMetadata",
        "Function",
        "FunctionSignatureSpecialization",
        "FunctionSignatureSpecializationParam",
        "FunctionSignatureSpecializationReturn",
        "FunctionSignatureSpecializationParamKind",
        "FunctionSignatureSpecializationParamPayload",
        "FunctionType",
        "ConstrainedExistential",
        "ConstrainedExistentialRequirementList",
        "ConstrainedExistentialSelf",
        "GenericPartialSpecialization",
        "GenericPartialSpecializationNotReAbstracted",
        "GenericProtocolWitnessTable",
        "GenericProtocolWitnessTableInstantiationFunction",
        "ResilientProtocolWitnessTable",
        "GenericSpecialization",
        "GenericSpecializationNotReAbstracted",
        "GenericSpecializationInResilienceDomain",
        "GenericSpecializationParam",
        "GenericSpecializationPrespecialized",
        "InlinedGenericFunction",
        "GenericTypeMetadataPattern",
        "Getter",
        "Global",
        "GlobalGetter",
        "Identifier",
        "Index",
        "IVarInitializer",
        "IVarDestroyer",
        "ImplEscaping",
        "ImplConvention",
        "ImplDifferentiabilityKind",
        "ImplParameterResultDifferentiability",
        "ImplFunctionAttribute",
        "ImplFunctionConvention",
        "ImplFunctionConventionName",
        "ImplFunctionType",
        "ImplInvocationSubstitutions",
        "ImplicitClosure",
        "ImplParameter",
        "ImplPatternSubstitutions",
        "ImplResult",
        "ImplYield",
        "ImplErrorResult",
        "InOut",
        "InfixOperator",
        "Initializer",
        "InitAccessor",
        "Isolated",
        "KeyPathGetterThunkHelper",
        "KeyPathSetterThunkHelper",
        "KeyPathUnappliedMethodThunkHelper",
        "KeyPathAppliedMethodThunkHelper",
        "KeyPathEqualsThunkHelper",
        "KeyPathHashThunkHelper",
        "LazyProtocolWitnessTableAccessor",
        "LazyProtocolWitnessTableCacheVariable",
        "LocalDeclName",
        "Macro",
        "MacroExpansionUniqueName",
        "MaterializeForSet",
        "MemberAttachedMacroExpansion",
        "MemberAttributeAttachedMacroExpansion",
        "MergedFunction",
        "Metatype",
        "MetatypeRepresentation",
        "Metaclass",
        "MethodLookupFunction",
        "ObjCMetadataUpdateFunction",
        "ObjCResilientClassStub",
        "FullObjCResilientClassStub",
        "ModifyAccessor",
        "Module",
        "NativeOwningAddressor",
        "NativeOwningMutableAddressor",
        "NativePinningAddressor",
        "NativePinningMutableAddressor",
        "NominalTypeDescriptor",
        "NominalTypeDescriptorRecord",
        "NonObjCAttribute",
        "Number",
        "ObjCAsyncCompletionHandlerImpl",
        "CheckedObjCAsyncCompletionHandlerImpl",
        "ObjCAttribute",
        "ObjCBlock",
        "EscapingObjCBlock",
        "OtherNominalType",
        "OwningAddressor",
        "OwningMutableAddressor",
        "PartialApplyForwarder",
        "PartialApplyObjCForwarder",
        "PeerAttachedMacroExpansion",
        "PostfixOperator",
        "PrefixOperator",
        "PrivateDeclName",
        "PropertyDescriptor",
        "PropertyWrapperBackingInitializer",
        "PropertyWrapperInitFromProjectedValue",
        "Protocol",
        "ProtocolSymbolicReference",
        "ProtocolConformance",
        "ProtocolConformanceRefInTypeModule",
        "ProtocolConformanceRefInProtocolModule",
        "ProtocolConformanceRefInOtherModule",
        "ProtocolDescriptor",
        "ProtocolDescriptorRecord",
        "ProtocolConformanceDescriptor",
        "ProtocolConformanceDescriptorRecord",
        "ProtocolList",
        "ProtocolListWithClass",
        "ProtocolListWithAnyObject",
        "ProtocolSelfConformanceDescriptor",
        "ProtocolSelfConformanceWitness",
        "ProtocolSelfConformanceWitnessTable",
        "ProtocolWitness",
        "ProtocolWitnessTable",
        "ProtocolWitnessTableAccessor",
        "ProtocolWitnessTablePattern",
        "ReabstractionThunk",
        "ReabstractionThunkHelper",
        "ReabstractionThunkHelperWithSelf",
        "ReabstractionThunkHelperWithGlobalActor",
        "ReadAccessor",
        "RelatedEntityDeclName",
        "RetroactiveConformance",
        "ReturnType",
        "Shared",
        "Owned",
        "SILBoxType",
        "SILBoxTypeWithLayout",
        "SILBoxLayout",
        "SILBoxMutableField",
        "SILBoxImmutableField",
        "Setter",
        "SpecializationPassID",
        "IsSerialized",
        "Static",
        "Structure",
        "Subscript",
        "Suffix",
        "Integer",
        "NegativeInteger",
        "IsolatedDeallocator",
        "ImplErasedIsolation",
        "ImplNonisolatedNonsendingIsolation",
        "ImplCalledOnceFunction",
        "ImplCoroutineKind",
        "ImplSendingResult",
        "ImplParameterSending",
        "ImplParameterIsolated",
        "ImplParameterImplicitLeading",
        "CalledOnceFunctionType",
        "CoroFunctionPointer",
        "DefaultOverride",
        "PropertyWrappedFieldInitAccessor",
        "YieldingBorrowAccessor",
        "YieldingMutateAccessor",
        "BorrowAccessor",
        "MutateAccessor",
        "BuiltinBorrow",
        "BuiltinFixedArray",
        "DependentGenericInverseConformanceRequirement",
        "OutlinedInitializeWithTakeNoValueWitness",
        "OutlinedInitializeWithCopyNoValueWitness",
        "OutlinedAssignWithTakeNoValueWitness",
        "OutlinedAssignWithCopyNoValueWitness",
        "OutlinedDestroyNoValueWitness",
        "Sending",
        "SendingResultFunctionType",
        "ConstValue",
        "IsolatedAnyFunctionType",
        "NonIsolatedCallerFunctionType",
        "TypedThrowsAnnotation",
        "ThinFunctionType",
        "Tuple",
        "TupleElement",
        "TupleElementName",
        "Pack",
        "SILPackDirect",
        "SILPackIndirect",
        "PackExpansion",
        "PackElement",
        "PackElementLevel",
        "Type",
        "TypeSymbolicReference",
        "TypeAlias",
        "TypeList",
        "TypeMangling",
        "TypeMetadata",
        "TypeMetadataAccessFunction",
        "TypeMetadataCompletionFunction",
        "TypeMetadataInstantiationCache",
        "TypeMetadataInstantiationFunction",
        "TypeMetadataSingletonInitializationCache",
        "TypeMetadataDemanglingCache",
        "TypeMetadataLazyCache",
        "UncurriedFunctionType",
        "UnknownIndex",
        "UnsafeAddressor",
        "UnsafeMutableAddressor",
        "ValueWitness",
        "ValueWitnessTable",
        "Variable",
        "VTableThunk",
        "VTableAttribute",
        "WillSet",
        "ReflectionMetadataBuiltinDescriptor",
        "ReflectionMetadataFieldDescriptor",
        "ReflectionMetadataAssocTypeDescriptor",
        "ReflectionMetadataSuperclassDescriptor",
        "GenericTypeParamDecl",
        "CurryThunk",
        "SILThunkIdentity",
        "DispatchThunk",
        "MethodDescriptor",
        "ProtocolRequirementsBaseDescriptor",
        "AssociatedConformanceDescriptor",
        "DefaultAssociatedConformanceAccessor",
        "BaseConformanceDescriptor",
        "AssociatedTypeDescriptor",
        "AsyncAnnotation",
        "ThrowsAnnotation",
        "EmptyList",
        "FirstElementMarker",
        "VariadicMarker",
        "OutlinedBridgedMethod",
        "OutlinedCopy",
        "OutlinedConsume",
        "OutlinedRetain",
        "OutlinedRelease",
        "OutlinedInitializeWithTake",
        "OutlinedInitializeWithCopy",
        "OutlinedAssignWithTake",
        "OutlinedAssignWithCopy",
        "OutlinedDestroy",
        "OutlinedEnumGetTag",
        "OutlinedEnumTagStore",
        "OutlinedEnumProjectDataForLoad",
        "OutlinedVariable",
        "OutlinedReadOnlyObject",
        "AssocTypePath",
        "LabelList",
        "ModuleDescriptor",
        "ExtensionDescriptor",
        "AnonymousDescriptor",
        "AssociatedTypeGenericParamRef",
        "SugaredOptional",
        "SugaredArray",
        "SugaredInlineArray",
        "SugaredDictionary",
        "SugaredParen",
        "AccessorFunctionReference",
        "OpaqueType",
        "OpaqueTypeDescriptorSymbolicReference",
        "OpaqueTypeDescriptor",
        "OpaqueTypeDescriptorRecord",
        "OpaqueTypeDescriptorAccessor",
        "OpaqueTypeDescriptorAccessorImpl",
        "OpaqueTypeDescriptorAccessorKey",
        "OpaqueTypeDescriptorAccessorVar",
        "OpaqueReturnType",
        "OpaqueReturnTypeOf",
        "CanonicalSpecializedGenericMetaclass",
        "CanonicalSpecializedGenericTypeMetadataAccessFunction",
        "MetadataInstantiationCache",
        "NoncanonicalSpecializedGenericTypeMetadata",
        "NoncanonicalSpecializedGenericTypeMetadataCache",
        "GlobalVariableOnceFunction",
        "GlobalVariableOnceToken",
        "GlobalVariableOnceDeclList",
        "CanonicalPrespecializedGenericTypeCachingOnceToken",
        "AsyncFunctionPointer",
        "AutoDiffFunction",
        "AutoDiffFunctionKind",
        "AutoDiffSelfReorderingReabstractionThunk",
        "AutoDiffSubsetParametersThunk",
        "AutoDiffDerivativeVTableThunk",
        "DifferentiabilityWitness",
        "NoDerivative",
        "IndexSubset",
        "AsyncAwaitResumePartialFunction",
        "AsyncSuspendResumePartialFunction",
        "AccessibleFunctionRecord",
        "CompileTimeLiteral",
        "BackDeploymentThunk",
        "BackDeploymentFallback",
        "ExtendedExistentialTypeShape",
        "Uniquable",
        "UniqueExtendedExistentialTypeShapeSymbolicReference",
        "NonUniqueExtendedExistentialTypeShapeSymbolicReference",
        "SymbolicExtendedExistentialType",
        # Built by the *runtime's* symbolic-reference resolver rather than by
        # `lib/Demangling`, so no mangled text reaches it. Listed because this set
        # mirrors `DemangleNodes.def`, and a name missing from it reads as a gap.
        "ObjectiveCProtocolSymbolicReference",
        # `DemangleNodes.def` names these three not directly but through
        # `#define REF_STORAGE(Name, ...) NODE(Name)` over `swift/AST/ReferenceStorage.def`.
        "Weak",
        "Unowned",
        "Unmanaged",
        # In `DemangleNodes.def` up to 5.10.1 and not after: upstream deleted the flag,
        # and the shipped runtime still holds symbols carrying it. See
        # tools/swift-demangle-reference/README.md.
        "MetatypeParamsRemoved",
        "HasSymbolQuery",
        "OpaqueReturnTypeIndex",
        "OpaqueReturnTypeParent",
    ]
)

CONTEXT_KINDS = frozenset(
    [
        "Allocator",
        "AnonymousContext",
        "Class",
        "Constructor",
        "Deallocator",
        "DefaultArgumentInitializer",
        "Destructor",
        "DidSet",
        "Enum",
        "ExplicitClosure",
        "Extension",
        "Function",
        "Getter",
        "GlobalGetter",
        "IVarInitializer",
        "IVarDestroyer",
        "ImplicitClosure",
        "Initializer",
        "InitAccessor",
        "MaterializeForSet",
        "ModifyAccessor",
        "Module",
        "NativeOwningAddressor",
        "NativeOwningMutableAddressor",
        "NativePinningAddressor",
        "NativePinningMutableAddressor",
        "OtherNominalType",
        "OwningAddressor",
        "OwningMutableAddressor",
        "PropertyWrapperBackingInitializer",
        "PropertyWrapperInitFromProjectedValue",
        "Protocol",
        "ProtocolSymbolicReference",
        "ReadAccessor",
        "Setter",
        "Static",
        "Structure",
        "Subscript",
        "TypeSymbolicReference",
        "TypeAlias",
        "UnsafeAddressor",
        "UnsafeMutableAddressor",
        "Variable",
        "WillSet",
        "OpaqueReturnTypeOf",
        "AutoDiffFunction",
    ]
)

#: Attributes that wrap a whole symbol rather than appearing inside its type. They are
#: popped off the stack before anything else, which is why they are a set of their own.
FUNCTION_ATTR_KINDS = frozenset(
    [
        "FunctionSignatureSpecialization",
        "GenericSpecialization",
        "GenericSpecializationPrespecialized",
        "InlinedGenericFunction",
        "GenericSpecializationNotReAbstracted",
        "GenericPartialSpecialization",
        "GenericPartialSpecializationNotReAbstracted",
        "GenericSpecializationInResilienceDomain",
        "ObjCAttribute",
        "NonObjCAttribute",
        "DynamicAttribute",
        "DirectMethodReferenceAttribute",
        "VTableAttribute",
        "PartialApplyForwarder",
        "PartialApplyObjCForwarder",
        "OutlinedVariable",
        "OutlinedReadOnlyObject",
        "OutlinedBridgedMethod",
        "MergedFunction",
        "DistributedThunk",
        "DistributedAccessor",
        "DynamicallyReplaceableFunctionImpl",
        "DynamicallyReplaceableFunctionKey",
        "DynamicallyReplaceableFunctionVar",
        "AsyncFunctionPointer",
        "AsyncAwaitResumePartialFunction",
        "AsyncSuspendResumePartialFunction",
        "AccessibleFunctionRecord",
        "BackDeploymentThunk",
        "BackDeploymentFallback",
        "HasSymbolQuery",
        "CoroFunctionPointer",
        "DefaultOverride",
    ]
)

_DECL_NAME_KINDS = frozenset(
    [
        "Identifier",
        "LocalDeclName",
        "PrivateDeclName",
        "RelatedEntityDeclName",
        "PrefixOperator",
        "PostfixOperator",
        "InfixOperator",
        "TypeSymbolicReference",
        "ProtocolSymbolicReference",
    ]
)

_ANY_GENERIC_KINDS = frozenset(
    [
        "Structure",
        "Class",
        "Enum",
        "Protocol",
        "ProtocolSymbolicReference",
        "OtherNominalType",
        "TypeAlias",
        "TypeSymbolicReference",
        "BuiltinTupleType",
    ]
)

_REQUIREMENT_KINDS = frozenset(
    [
        "DependentGenericParamPackMarker",
        "DependentGenericParamValueMarker",
        "DependentGenericSameTypeRequirement",
        "DependentGenericSameShapeRequirement",
        "DependentGenericLayoutRequirement",
        "DependentGenericConformanceRequirement",
        "DependentGenericInverseConformanceRequirement",
    ]
)

#: `isContext` also answers yes for `BuiltinTupleType`, which is not a `CONTEXT_NODE`.
CONTEXT_KINDS = CONTEXT_KINDS | {"BuiltinTupleType"}


def is_decl_name(kind):
    return kind in _DECL_NAME_KINDS


def is_any_generic(kind):
    return kind in _ANY_GENERIC_KINDS


def is_requirement(kind):
    return kind in _REQUIREMENT_KINDS


def is_entity(kind):
    """Deliberately loose, as in the reference: some kinds it accepts are not entities."""
    return kind == "Type" or kind in CONTEXT_KINDS


class Node:
    """One node of the demangling tree.

    A node carries *either* text or an index, never both -- the reference has separate
    payload slots and asserts on the wrong one -- so the two are kept apart here as well
    rather than collapsed into one `value` that a caller would have to interpret.
    """

    __slots__ = ("children", "index", "kind", "text")

    def __init__(self, kind, text=None, index=None, children=None):
        self.kind = kind
        self.text = text
        self.index = index
        self.children = children if children is not None else []

    @property
    def first(self):
        return self.children[0]

    @property
    def last(self):
        return self.children[-1]

    def child(self, at):
        return self.children[at]

    def add(self, child):
        self.children.append(child)
        return self

    def __len__(self):
        return len(self.children)

    def __iter__(self):
        return iter(self.children)

    def __repr__(self):
        payload = ""
        if self.text is not None:
            payload = f" {self.text!r}"
        elif self.index is not None:
            payload = f" #{self.index}"
        return f"<{self.kind}{payload} {len(self.children)} children>"

    def tree(self, depth=0):
        """The reference's `getNodeTreeAsString`, which is how its own tests read a tree."""
        payload = ""
        if self.text is not None:
            payload = f", {self.text!r}"
        elif self.index is not None:
            payload = f", index={self.index}"
        lines = ["  " * depth + f"kind={self.kind}{payload}"]
        lines.extend(child.tree(depth + 1) for child in self.children)
        return "\n".join(lines)
