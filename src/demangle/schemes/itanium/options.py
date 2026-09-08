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

    It also names a constructor or destructor of a closure or unnamed type the way
    libiberty does, after the last source name it read: `A::{unnamed type#1}::~A()`,
    where llvm-cxxfilt writes `A::'unnamed'::~()` because the type has no name to
    repeat. ICU ships the shape in `MicroProps::{unnamed type#1}::~MicroProps()`.
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

    undeduced_auto_substitution: bool | None = None
    """Count an undeduced `auto` -- `Da`, and `decltype(auto)`, `Dc` -- as a substitution
    candidate, the way Apple's clang does.

    Not a spelling but a numbering, and the one place two compilers number the same
    name differently. The ABI leaves builtin types out of the substitution table, and
    GCC and upstream clang leave `auto` out with them; Clang through 6.0 counted an
    undeduced `auto` by accident, `-fclang-abi-compat=6` still does, and Apple's clang
    has kept that rule in every version since, so every `S<n>_` after a deduced return
    type in a Mach-O symbol is one higher than GCC or upstream clang would write. Read
    by the wrong rule, such a name resolves its back-references to the wrong entries:
    of the 17,310 names carrying a `Da` in Homebrew's bottles of Boost, folly, protobuf,
    ceres and the rest, 6,385 come back as a plausible-looking wrong declaration and
    2,300 refuse, under llvm-cxxfilt and c++filt alike.

    None, the default, decides by the symbol's form: a name with the extra leading
    underscore a Mach-O symbol table carries, `__Z...`, is read by Apple's rule, and a
    bare `_Z...` by everyone else's -- and either way a name whose back-references run
    past the table under one rule is read again under the other, since that is the one
    rule known to move them. True or False forces a rule and skips the retry. Nothing in
    the name itself says which compiler wrote it, so a Mach-O name from upstream clang
    with a back-reference in range under both rules is read by Apple's; set False for
    those.
    """

    closure_prefix_substitution: bool | None = None
    """Count a closure prefix -- the `ns::g1` of `ns::g1::'lambda'(...)`, a lambda in a
    variable's or a member's initializer, written `2ns2g1M...` -- as a substitution
    candidate, the way the ABI says and upstream clang and GCC 13 do.

    The other numbering, and the other place two compilers number the same name
    differently. GCC through 12 wrote the `M` but never entered the prefix before it in
    the table (`-fabi-version=17` and below; 18, GCC 13's default, fixed it), and
    Apple's clang never has: Homebrew's macOS bottles of Apache Arrow, DuckDB and
    RocksDB carry 663 names that read only with the prefix left out and none that read
    only with it in, where upstream clang 13 through 18 -- targeting Darwin included --
    enter it. So every `S<n>_` after the lambda's opening in such a name is one *lower*
    than the ABI says. Read by the ABI's rule, the back-references resolve one entry
    early, and what comes out is a lie every demangler tells:
    `_ZNK2ns2g3MUlNS_3BoxIiEES1_E_clES1_S1_` is `ns::g3::'lambda'(ns::Box<int>,
    ns::Box<int>)::operator()(ns::Box<int>, ns::Box<int>) const` -- and llvm-cxxfilt 18,
    LLVM's main branch and c++filt 2.42 all print `ns::Box` bare, a template with no
    arguments, as the second parameter; RocksDB's `[](const Endpoint&, const Endpoint&)`
    comes back `(rocksdb::Endpoint const&, rocksdb::Endpoint const)`. GCC 13 still
    emits the old spelling as an alias beside the new, so a binary built today can
    carry both.

    None, the default, decides by the symbol's form, as `undeduced_auto_substitution`
    does: a name with the extra leading underscore a Mach-O symbol table carries,
    `__Z...`, is read by Apple's rule, and a bare `_Z...` by the ABI's -- and either way
    a name whose back-references then run past the table, or name something no type
    can be (the closure prefix itself, or a template with no arguments after it), is
    read again under the other rule. That catches every misnumbered name whose shifted
    references land on one of those; a name that reads either way is one the retry
    never touches. True or False forces a rule and skips the retry: False for an ELF
    binary known to be GCC 12's or older, True for a Mach-O one known to be upstream
    clang's, since either's shifted references may land on a type and read as a
    different type without tripping anything.
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
    every other special name is spelled identically by both references. It also numbers
    a reference temporary by its seq-id, `reference temporary #0 for f()::x`, where
    llvm-cxxfilt writes `reference temporary for f()::x`.
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

    gnu_empty_pack_spelling: bool = False
    """Print an expansion over an empty pack as an empty entry, the way GNU c++filt does.

    `std::thread::thread<F, Args...>` instantiated with no arguments is
    `_ZNSt6threadC1IZ4mainEUlvE_JEvEEOT_DpOT0_`, and every program that starts a thread
    on a no-argument callable carries it. llvm-cxxfilt drops the empty pack from the
    list, `thread<main::'lambda'(), void>`; c++filt prints what it expands to, which
    is nothing, and keeps the comma: `thread<main::{lambda()#1}, , void>`. It does the
    same in a parameter list, `f(, int)`, and in a call's arguments, `g(, int)` --
    every comma-separated list -- and drops the empty entries at the *end* of a list,
    so `f<int, JE>` is `f<int>` there as here.

    False (the default) drops every one, as llvm-cxxfilt does. True keeps c++filt's.
    """

    gnu_exception_spec_first: bool = False
    """Write a function type's exception specification before its qualifiers.

    `M1AKDoFvvRE` is `void (A::*)() const & noexcept` to llvm-cxxfilt and
    `void (A::*)() noexcept const &` to GNU c++filt, which prints the specification --
    `noexcept`, `noexcept(...)`, `throw(...)`, `transaction_safe` -- first. Neither
    order is the one C++ declares them in for a member function. False (the default)
    gives llvm-cxxfilt's, True c++filt's.
    """

    gnu_objc_protocol_spelling: bool = False
    """Spell an Objective-C protocol qualifier as the vendor qualifier it is written as.

    `PU13objcproto3Bar11objc_object` is `id<Bar>` to llvm-cxxfilt, and `Foo<Bar>*` for
    a class; GNU c++filt knows nothing of the convention and prints the qualifier as it
    prints any vendor qualifier, after the type: `objc_object objcproto3Bar*` and
    `Foo objcproto3Bar*`. False (the default) gives the former, True the latter.
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
    gnu_empty_pack_spelling=True,
    gnu_objc_protocol_spelling=True,
    gnu_exception_spec_first=True,
)
