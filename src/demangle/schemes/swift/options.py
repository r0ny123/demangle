"""How much of a Swift name to spell.

The compiler's own printer takes a `DemangleOptions` and consults it in fifty-one places.
This carries the ones the reference's `SimplifiedUIDemangleOptions()` bundle actually
changes over `test/Demangle/Inputs/simplified-manglings.txt`, which is what
`swift-demangle --simplified` prints and what Xcode and LLDB show a user.

Fields are named for the reference's, lower-cased. True is what the toolchain prints by
default, so `SwiftOptions()` is the full spelling and `SwiftOptions.simplified()` is the
bundle.
"""

from dataclasses import dataclass, replace

__all__ = ["DEFAULT_OPTIONS", "SIMPLIFIED_OPTIONS", "SwiftOptions"]


@dataclass(frozen=True, slots=True)
class SwiftOptions:
    """Which parts of a Swift name to print."""

    synthesize_sugar_on_types: bool = True
    """Spell `Swift.Optional<T>` as `T?`, `Swift.Array<T>` as `[T]`, and the dictionary
    and inline-array forms with them.

    `SynthesizeSugarOnTypes`. The struct's own default is off; `swift-demangle` turns it
    on unless given `--disable-sugar`, and so does this, because the tool's output is
    what a reader has seen. Off is still reachable, and the printer itself needs it: a
    specialisation's propagated function is a mangled name the reference demangles
    again with the struct's defaults, so *that* one comes out unsugared inside a name
    that is otherwise sugared.
    """

    display_module_names: bool = True
    """Qualify a name with the module that declares it: `Swift.Int`, not `Int`.

    `DisplayModuleNames`. The dot goes with it -- the reference prints nothing for the
    module node, and the separator is written only after a context that produced output.
    """

    show_function_argument_types: bool = True
    """Spell a function's parameters and result, rather than their labels alone.

    `ShowFunctionArgumentTypes`. False turns `(Swift.Int) -> Swift.UInt` into `(_:)`: the
    parameter list keeps its shape and its labels, and everything after it -- `async`, a
    thrown error, `-> result` -- is left out, because what remains is a signature the way
    a user refers to one rather than a type.
    """

    display_where_clauses: bool = True
    """Spell the requirements of a generic signature: `<A where B: Runcible>`, not `<A>`.

    `DisplayWhereClauses`. The parameter list stays; what goes is everything after the
    `where`.
    """

    display_entity_types: bool = True
    """Spell the type a declaration has, where it is written after a colon.

    `DisplayEntityTypes`. `bar : Int` becomes `bar`. Only the colon form: a function's
    signature is written *around* its name rather than after it, and
    `show_function_argument_types` is what governs that one.
    """

    display_protocol_conformances: bool = True
    """Spell which protocol a witness conforms to, and where: `bar : barrable in foo`.

    `DisplayProtocolConformances`. False leaves the type alone -- `protocol witness table
    for bar` -- which is what identifies the entry; the conformance is what makes it long.
    """

    display_generic_specializations: bool = True
    """Spell what a specialisation was specialised *with*.

    `DisplayGenericSpecializations`. False writes `specialized X` in place of
    `generic specialization <Int> of X` and of the whole
    `function signature specialization <Arg[0] = ...> of X`, once, however many
    specialisation layers a symbol carries.
    """

    display_extension_contexts: bool = True
    """Spell the module an extension was written in: `(extension in Foo):A.test()`.

    `DisplayExtensionContexts`, and the anonymous-context form with it.
    """

    display_unmangled_suffix: bool = True
    """Report what followed a name the demangler could read only part of.

    `DisplayUnmangledSuffix`. True writes ` with unmangled suffix "..."`.
    """

    show_private_discriminators: bool = True
    """Spell the file-private discriminator: `(getPrivateClass in _DISC)`, not the name.

    `ShowPrivateDiscriminators`.
    """

    shorten_partial_apply: bool = True
    """Write `partial apply forwarder for f` rather than `partial apply for f`.

    `ShortenPartialApply`, inverted, like `shorten_value_witness`.
    """

    shorten_thunk: bool = True
    """Spell a thunk in full: `reabstraction thunk helper from A to B`, not `thunk for B`.

    `ShortenThunk`, inverted. It reaches a dozen kinds -- the reabstraction thunks, the
    autodiff ones, `merged `, `distributed thunk `, the dynamically-replaceable trio --
    and in each the short form is the last child alone, or nothing at all.
    """

    show_async_resume_partial: bool = True
    """Spell an async resume point: `(1) await resume partial function for f()`.

    `ShowAsyncResumePartial`. False leaves the function it resumes into.
    """

    shorten_value_witness: bool = True
    """Write `destroy value witness for T` rather than `destroy for T`.

    `ShortenValueWitness`, inverted: the reference's flag says *shorten*, and every field
    here says *print it in full*, so that `SwiftOptions()` is the toolchain default with
    no field set to False.
    """

    @classmethod
    def simplified(cls) -> "SwiftOptions":
        """The reference's `SimplifiedUIDemangleOptions()`, as far as it differs here.

        Upstream's bundle sets fourteen flags. The rest either match this printer's
        behaviour already (`SynthesizeSugarOnTypes`, `QualifyEntities`) or change nothing
        over the 217 vectors, and a flag no vector exercises is a flag with no reference
        behind it -- so it is left out rather than guessed at.
        """
        return replace(
            cls(),
            display_module_names=False,
            show_function_argument_types=False,
            display_where_clauses=False,
            display_entity_types=False,
            display_protocol_conformances=False,
            display_generic_specializations=False,
            display_extension_contexts=False,
            display_unmangled_suffix=False,
            show_private_discriminators=False,
            shorten_partial_apply=False,
            shorten_thunk=False,
            show_async_resume_partial=False,
            shorten_value_witness=False,
        )


DEFAULT_OPTIONS = SwiftOptions()
SIMPLIFIED_OPTIONS = SwiftOptions.simplified()
