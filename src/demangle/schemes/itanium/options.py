"""Caller-selectable policy for Itanium output.

Some questions a demangler faces have no single right answer -- the two reference
implementations answer them differently, and both are correct. Those belong here, as
explicit options with a documented default, rather than being decided silently in the
parser.

Frozen, so an options object can be shared between threads and used as a cache key.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ItaniumOptions:
    """Output policy for the Itanium demangler."""

    expand_std_abbreviations: bool = False
    """Spell `Ss` as `std::basic_string<char, std::char_traits<char>, ...>`.

    False (the default) gives llvm-cxxfilt's short form, `std::string`. True gives GNU
    c++filt's expansion. The types are identical; only the spelling differs.
    """

    gnu_closure_spelling: bool = False
    """Spell lambdas and unnamed types the way GNU c++filt does.

    False gives llvm-cxxfilt's `'lambda'(int)` and `'unnamed'`. True gives GNU's
    `{lambda(int)#1}` and `{unnamed type#1}`, which number from one rather than
    suffixing the raw index.

    It also numbers a generic lambda's invented parameters. `[](auto a, auto b)` is
    mangled as references to parameters the closure never declared (ABI 5.1.8):
    llvm-cxxfilt spells every one of them `auto`, and GNU spells them `auto:1` and
    `auto:2` by index -- so `Ul T0_ T_ E` is `{lambda(auto:2, auto:1)#1}`. The number is
    the only thing that tells two of them apart.
    """

    gnu_expression_spelling: bool = False
    """Spell expressions inside types the way GNU c++filt does.

    The two references diverge throughout expression syntax, and neither is more correct
    -- they are printing the same expression. GNU writes `decltype ({parm#1}+{parm#1})`
    where LLVM writes `decltype(fp + fp)`: a space after the keyword, no spaces around
    binary operators, function parameters numbered from one, and a call's callee
    parenthesised. A floating-point literal is the hex the name carries, in brackets
    after the type -- `(double)[4048f5c28f5c28f6]` -- where LLVM decodes it and prints
    `0x1.8f5c28f5c28f6p+5`.
    """

    symbolic_constraint_parameters: bool = True
    """Spell a template parameter inside a requires-clause by name rather than by value.

    llvm-cxxfilt prints `T` where GNU c++filt substitutes the bound argument. The clause
    itself is never printed, but it contributes entries to the substitution table that
    the signature refers back to, so the choice is visible in the output.

    A clause names parameters of enclosing templates, and not all of them are in scope --
    which is the reason llvm-cxxfilt spells them symbolically in the first place. Where
    there is nothing to substitute, this falls back to that spelling rather than refusing
    the name: an option chooses how a name is spelled and must not change which names
    read at all.
    """

    gnu_nullptr_spelling: bool = False
    """Spell `Dn` as `decltype(nullptr)` rather than `std::nullptr_t`.

    False gives llvm-cxxfilt's spelling. True gives GNU c++filt's, which writes the
    expression the type is defined as rather than the library typedef for it. Both name
    the same type.

    Found by `tools/differential.py --cross`, comparing GNU against several LLVM builds:
    no name in the gnu-style corpus carried a `Dn` at all, so nothing here had ever been
    asked the question.
    """

    gnu_angle_spacing: bool = False
    """Separate two consecutive closing angle brackets with a space.

    C++03 needed it -- `A<B<int> >`, because `>>` was the shift operator -- and GNU
    c++filt still writes it where llvm-cxxfilt does not. It belongs here as well as on
    the style's spelling builder because a template argument list spelled into *text*,
    inside an unresolved name or a vendor qualifier, never reaches the builder.
    """

    gnu_complex_spelling: bool = False
    """Spell C99's `_Complex` and `_Imaginary` the way GNU c++filt does.

    Both references write the qualifier *after* the type it qualifies -- `double complex*`
    is a pointer to a complex double, not a complex pointer -- and differ only in the
    word. False (the default) gives llvm-cxxfilt's `double complex` and
    `double imaginary`; True gives GNU c++filt's `double _Complex` and
    `double _Imaginary`, which are the keywords C99 actually spells.
    """

    gnu_vector_spelling: bool = False
    """Spell a `Dv` vector type the way GNU c++filt does.

    False (the default) gives llvm-cxxfilt's `int vector[4]`; True gives GNU c++filt's
    `int __vector(4)`, which is how GCC's own `__attribute__((vector_size))` diagnostics
    write it. The distinction matters more than most: SIMD code mangles `Dv` constantly,
    so this is not an obscure corner of the grammar.
    """

    gnu_special_name_spelling: bool = False
    """Spell the thread-local special names the way GNU c++filt does.

    False gives llvm-cxxfilt's `thread-local initialization routine for x` and
    `thread-local wrapper routine for x`; True gives GNU's `TLS init function for x` and
    `TLS wrapper function for x`. They name the same entity. Only `TH` and `TW` differ:
    every other special name is spelled identically by both references.
    """

    gnu_entity_operand_spelling: bool = False
    """Spell an embedded `<mangled-name>` under a unary operator the way GNU does.

    `X ad L _Z... E E` -- the address of a function, passed as a template argument -- is
    where nearly all of these appear. llvm-cxxfilt prints the whole declaration after
    the `&`: `&llvm::sandboxir::SwitchInst::setCondition(llvm::sandboxir::Value*)`. GNU
    c++filt prints `&llvm::sandboxir::SwitchInst::setCondition`, which is what the
    source wrote, and falls back to bracketing the declaration -- `&(A::f() const)` --
    for every shape that is not a bare qualified function name.

    The rule, read off c++filt rather than guessed: the name alone for `&` of a
    function whose name is qualified and which carries no return type, no cv- or
    ref-qualifier and no requires-clause; brackets around anything else that is not a
    plain data name. So `&A::f` and `&std::f` and `&A::~A`, but `&(f())` for an
    unqualified one, `&(void A::f<int>())` for a template, `&(f()::x)` for a local
    entity and `&(vtable for A)` for a special name.

    A call is the other place one appears, `decltype(h(t))` with `h` resolved --
    `clL_Z1hiEfp_E`. libiberty prints the callee through its name alone, "function
    call used in an expression should not have printed types of the function
    arguments", so c++filt writes `h({parm#1})` and `A::s({parm#1})` where llvm-cxxfilt
    writes `h(int)(fp)`. The name is an operand and bracketed unless it is a plain
    one: `(h<int>)({parm#1})`, `(A::s const)({parm#1})`, `(h()::x)({parm#1})`.
    """

    gnu_default_argument_scope: bool = False
    """Name the default argument a local entity was declared in.

    `Z <encoding> Ed [<number>] _ <entity>` says the entity -- usually a lambda -- was
    written in a default argument rather than in the function body. GNU c++filt spells
    that scope, `f(int, double)::{default arg#1}::x`; llvm-cxxfilt drops it and prints
    `f(int, double)::x`, which is the same name for two different entities if a function
    has one lambda in its body and another in a default argument.
    """

    gnu_friend_spelling: bool = False
    """Mark a friend declared inside its class the way GNU c++filt does.

    `<unqualified-name> ::= F <name>` says the function was declared inside the class it
    is a friend of. False (the default) gives llvm-cxxfilt's `A::friend f()`, the word
    before the name; True gives GNU c++filt's `A::f[friend]()`, a bracketed suffix after
    the name and its ABI tags but before its template arguments -- `A::f[friend]<int>`.
    """

    gnu_unresolved_scope_substitution: bool = False
    """Record the scope of an `srN` unresolved name as a substitution, the way g++ does.

    `srN T_ 3foo E 1v` is `T::foo::v`. The ABI says the qualifier levels of an
    unresolved name are not substitution candidates, Clang writes names that way, and
    llvm-cxxfilt reads them that way: `T` is recorded, `T::foo` is not. g++ mangles the
    scope as a nested-name *type* and records it, so the parameter after
    `decltype(T::foo::v + 1)` in `_Z2e1I1QEDTplsrNT_3fooE1vLi1EES1_S2_` is `S2_`,
    `Q::foo`, where Clang writes it out again as `NS1_3fooE`. GNU c++filt reads every
    `srN` as a type and so numbers as g++ does.

    Neither reader can be right for both compilers: a back-reference written after the
    scope counts one entry more under g++ than under Clang. False (the default) reads
    as the ABI and llvm-cxxfilt do, and takes `S2_` in the g++ name above to be the
    `decltype` itself. True reads as c++filt does.
    """

    local_name_return_type: bool = True
    """Show the return type of the function enclosing a local name.

    For `Z <function encoding> E <entity>`, llvm-cxxfilt prints the enclosing template
    function's return type and GNU c++filt does not. True (the default) follows LLVM.
    """


DEFAULT_OPTIONS = ItaniumOptions()
GNU_OPTIONS = ItaniumOptions(
    expand_std_abbreviations=True,
    gnu_nullptr_spelling=True,
    gnu_closure_spelling=True,
    local_name_return_type=False,
    gnu_expression_spelling=True,
    symbolic_constraint_parameters=False,
    gnu_complex_spelling=True,
    gnu_angle_spacing=True,
    gnu_vector_spelling=True,
    gnu_special_name_spelling=True,
    gnu_entity_operand_spelling=True,
    gnu_default_argument_scope=True,
    gnu_friend_spelling=True,
    gnu_unresolved_scope_substitution=True,
)
