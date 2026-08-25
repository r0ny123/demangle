"""A recursive-descent parser for the Itanium C++ ABI mangling scheme.

This is the scheme GCC and Clang emit, and by extension almost every C++ toolchain
outside the Microsoft ecosystem. It is also used, in its older form, by Rust's legacy
mangling.

The parser is written against `core.builder.Builder`: it recognises grammar productions
and reports them, never constructing output itself. See ARCHITECTURE.md for why.

Structure of this file mirrors the specification's own ordering -- names, then types,
then template arguments, then expressions -- so a production can be found by its
section number. Grammar comments quote the ABI verbatim.

Reference: Itanium C++ ABI section 5.1, https://itanium-cxx-abi.github.io/cxx-abi/abi.html
A transcription of the productions is vendored at docs/specs/itanium-grammar.txt.
"""

from ...core.errors import LimitExceeded, NotMangledError, ParseError
from ...core.limits import DEFAULT_LIMITS
from ...core.reader import DIGITS, Reader
from .options import DEFAULT_OPTIONS
from .substitutions import SubstitutionTable, TemplateArgumentTable
from .tables import (
    BUILTIN_TYPES,
    CONSTRUCTOR_KINDS,
    DESTRUCTOR_KINDS,
    EXTENDED_BUILTIN_TYPES,
    INFIX_OPERATORS,
    OPERATORS,
    PRECEDENCE,
    PREFIX_OPERATORS,
    PRIMARY_PRECEDENCE,
    QUALIFIER_LETTERS,
    QUALIFIER_ORDER,
    RIGHT_ASSOCIATIVE,
    SPECIAL_ENCODING_NAMES,
    SPECIAL_TYPE_NAMES,
    STD_ABBREVIATIONS,
    STD_ABBREVIATIONS_EXPANDED,
    STD_ABBREVIATIONS_EXPANDED_GNU,
    UNARY_PRECEDENCE,
)

__all__ = ["ItaniumParser", "detect", "parse"]

#: Characters that can open a <type>. Used only to decide whether an ambiguous
#: expression position holds a type, so it errs towards inclusion.
_TYPE_STARTERS = frozenset("vwbcahstijlmxynofdegzPRODCGUFAMTSN0123456789")

#: <template-param-decl> introducers. None can be confused with a <template-param>,
#: which is always `T_` or `T` followed by digits.
_PARAMETER_DECLARATIONS = frozenset({"Ty", "Tk", "Tn", "Tt", "Tp"})


def detect(name):
    """Cheap test for "is this plausibly an Itanium mangled name".

    Runs on every symbol a caller passes, including the overwhelming majority that are
    not mangled at all, so it does no work beyond a prefix comparison.
    """
    return name.startswith("_Z") or name.startswith("__Z") or name.startswith("_GLOBAL__")


class ItaniumParser:
    """Parses one mangled name into one builder. Single use.

    State -- cursor, substitution table, template scope -- is per name, so an instance
    is cheap and never shared. The builder, by contrast, is stateless and shared.
    """

    __slots__ = (
        "_abbrev",
        "_abbrev_expanded",
        "_ctor_dtor",
        "_depth",
        "_drop_return",
        "_in_constraint",
        "_mangled",
        "_naming",
        "_packs",
        "_parameter_counts",
        "_precedence",
        "_scope_has_pack",
        "builder",
        "limits",
        "options",
        "reader",
        "subs",
        "targs",
    )

    def __init__(self, mangled, builder, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
        if len(mangled) > limits.max_input:
            raise LimitExceeded(mangled, "input length", limits.max_input)
        self._mangled = mangled
        self.options = options
        expanded = STD_ABBREVIATIONS_EXPANDED_GNU if options.expand_std_abbreviations else STD_ABBREVIATIONS_EXPANDED
        self._abbrev_expanded = expanded
        self._abbrev = expanded if options.expand_std_abbreviations else STD_ABBREVIATIONS
        self.reader = Reader(mangled)
        self.builder = builder
        self.limits = limits
        self.subs = SubstitutionTable(mangled, limits.max_substitutions)
        self.targs = TemplateArgumentTable()
        self._depth = 0
        # Precedence of the expression just parsed, read by a containing operator to
        # decide whether it needs brackets. Primary by default: most expressions are
        # names or literals and never need any.
        self._precedence = PRIMARY_PRECEDENCE
        # Identities of template arguments that were argument packs. A pack is already
        # spelled as its comma-separated members, so expanding it must not also append
        # an ellipsis.
        # Pack handles seen in this name, held by strong reference. Identity is the
        # right test -- a pack is the object the parser just built -- but `id()` is not:
        # CPython reuses an address once an object is collected, so an id-keyed set can
        # report a brand new handle as a pack it has never seen. That failure is silent
        # and data-dependent, which is the worst kind.
        self._packs = []

        # Whether the name just parsed was a constructor or destructor. They are the
        # one case where a template specialisation still encodes no return type.
        self._ctor_dtor = False
        # True while reading the name of the entity being declared, false once its
        # signature begins. Only a name's own template arguments become the `T_` scope;
        # a `basic_string<T_, T0_, T1_>` mentioned in a parameter list must resolve
        # against the enclosing function's arguments, not replace them.
        self._naming = True
        # Set for exactly one encoding: the function enclosing a local name, whose
        # return type GNU c++filt omits. The type is still parsed -- it is there in the
        # input either way -- and then discarded.
        self._drop_return = False
        # Whether the template arguments currently in scope include a parameter pack.
        # An expansion is written `Dp <type>`, and what it expands to is the pack's
        # members -- so when a pack is in scope the ellipsis has already been spent and
        # printing one would double it. With no pack in scope the expansion is
        # unexpanded and the ellipsis is the whole point.
        self._scope_has_pack = False
        # Per-kind counters for the synthetic `$T` / `$N` / `$TT` names a generic
        # lambda's declared template parameters are spelled with.
        self._parameter_counts = {}
        # True while reading a requires-clause, where parameters are spelled by name.
        self._in_constraint = False

    # -- recursion control -----------------------------------------------------

    def _enter(self):
        self._depth += 1
        if self._depth > self.limits.max_depth:
            raise LimitExceeded(self._mangled, "recursion depth", self.limits.max_depth)

    def _leave(self):
        self._depth -= 1

    # -- entry point -----------------------------------------------------------

    def parse(self):
        """<mangled-name> ::= _Z <encoding> [. <vendor-specific suffix>]"""
        reader = self.reader
        # A Mach-O symbol table carries the extra leading underscore the linker adds, so
        # `__Z...` and `_Z...` name the same thing. Strip it only when a second
        # underscore follows, or `_Z1fv` would lose the one the grammar needs.
        if reader.startswith("__Z"):
            reader.take()
        if not reader.eat("_Z"):
            raise NotMangledError(self._mangled, "not an Itanium mangled name")

        result = self.encoding()

        if not reader.eof:
            suffix = reader.remaining
            # A clone suffix -- `.cold`, `.part.0`, `.llvm.<hash>`, a coroutine's
            # `.actor` -- can only be identified here, as what the grammar could not
            # consume. It cannot be split off in advance: a `.` also occurs *inside*
            # identifiers, notably in the frame types Clang synthesises for coroutines.
            if suffix.startswith("."):
                result = self.builder.decorated(result, suffix)
            else:
                raise ParseError(self._mangled, reader.pos, f"unconsumed input {suffix!r}")

        rendered_length = len(self.builder.spell(result))
        if rendered_length > self.limits.max_output:
            raise LimitExceeded(self._mangled, "output length", self.limits.max_output)
        return result

    def encoding(self):
        """<encoding> ::= <function name> <bare-function-type> | <data name> | <special-name>"""
        special = self.special_name()
        if special is not None:
            return special

        name, quals, ref_qualifier, is_template = self.name()

        reader = self.reader
        if reader.eof or reader.peek() in "E.":
            # A data symbol: a name and nothing after it.
            return name
        return self.bare_function_type(name, quals, ref_qualifier, is_template)

    def bare_function_type(self, name, quals=(), ref_qualifier="", is_template=False):
        """<bare-function-type> ::= <signature type>+

        The first type is the return type *only* for a template specialisation: a plain
        function does not encode its return type, because overloads cannot differ by it.
        For a template they can, so it is part of the signature (5.1.5.3).
        """
        builder = self.builder
        was_naming = self._naming
        self._naming = False
        try:
            returns = self.type_() if is_template else None
            if self._drop_return:
                returns = None
                self._drop_return = False

            parameters = []
            reader = self.reader
            while not reader.eof and reader.peek() not in "E.":
                parameter = self.type_()
                # `Dp T_` over a pack bound to nothing expands to no parameters at all,
                # so it must not leave a separator behind.
                if builder.spell(parameter):
                    parameters.append(parameter)
        finally:
            self._naming = was_naming

        # `f(void)` is how the scheme spells "no parameters"; C++ writes `f()`.
        if len(parameters) == 1 and builder.spell(parameters[0]) == "void":
            parameters = []

        suffix = ""
        if quals:
            suffix += " " + " ".join(quals)
        if ref_qualifier:
            suffix += " " + ref_qualifier
        return builder.function(returns, parameters, suffix, name)

    # -- 5.1.4 special names ---------------------------------------------------

    def special_name(self):
        """Vtables, typeinfo, thunks, guard variables. None if this is not one."""
        reader = self.reader
        code = reader.peek2()

        if code in SPECIAL_TYPE_NAMES:
            reader.pos += 2
            return self.builder.special(SPECIAL_TYPE_NAMES[code], self.type_())

        if code == "GR":
            # GR <object name> _  /  GR <object name> <seq-id> _
            reader.pos += 2
            inner = self.name()[0]
            reader.seq_id()
            return self.builder.special(SPECIAL_ENCODING_NAMES["GR"], inner)

        if code in SPECIAL_ENCODING_NAMES:
            reader.pos += 2
            return self.builder.special(SPECIAL_ENCODING_NAMES[code], self.encoding())

        if code == "GT":
            reader.pos += 2
            marker = reader.take()
            label = "transaction clone for " if marker == "t" else "non-transaction clone for "
            return self.builder.special(label, self.encoding())

        if code == "Tc":
            # Tc <call-offset> <call-offset> <base encoding>
            reader.pos += 2
            self.call_offset()
            self.call_offset()
            return self.builder.special("covariant return thunk to ", self.encoding())

        if reader.peek() == "T" and reader.peek(1) in "hv":
            # T <call-offset> <base encoding>
            reader.take()
            virtual = reader.peek() == "v"
            self.call_offset()
            label = "virtual thunk to " if virtual else "non-virtual thunk to "
            return self.builder.special(label, self.encoding())

        return None

    def call_offset(self):
        """<call-offset> ::= h <nv-offset> _ | v <v-offset> _"""
        reader = self.reader
        kind = reader.take()
        if kind not in "hv":
            raise ParseError(self._mangled, reader.pos, "expected a call offset")
        reader.number()
        if kind == "v":
            reader.expect("_")
            reader.number()
        reader.expect("_")

    # -- 5.1.2 names -----------------------------------------------------------

    def name(self, as_type=False):
        """<name> ::= <nested-name> | <unscoped-name>
                    | <unscoped-template-name> <template-args> | <local-name>

        Returns (handle, cv-qualifiers, ref-qualifier, is-template-specialisation).
        The qualifiers belong to the *function* the name introduces rather than to the
        name, but the nested-name production is where they are encoded, so they travel
        back out with it.
        """
        reader = self.reader
        char = reader.peek()

        if char == "N":
            return self.nested_name(as_type)
        if char == "Z":
            return self.local_name(as_type), (), "", False

        if char == "S":
            # Either an abbreviation or a back-reference, each of which may be an
            # <unscoped-template-name> that template arguments then attach to.
            if reader.peek(1) == "t":
                reader.pos += 2
                inner = self.unqualified_name()
                base = self.builder.qualified([self.builder.name("std"), inner])
            else:
                base = self.substitution(as_scope=True)
            if reader.peek() == "I":
                # <unscoped-template-name> is a candidate in its own right, recorded
                # before the arguments that specialise it. Verified against both
                # reference demanglers: in `_ZSt4sortIPiEvT_S_`, `S_` is `std::sort`.
                self.subs.remember(base, "unscoped-template-name")
                return self.apply_template_args(base), (), "", True
            return base, (), "", False

        base = self.unqualified_name()
        if reader.peek() == "I":
            # <unscoped-template-name> is a substitution candidate in its own right
            # (5.1.10), recorded before the arguments that specialise it.
            self.subs.remember(base, "unscoped-template-name")
            return self.apply_template_args(base), (), "", True
        return base, (), "", False

    def apply_template_args(self, base):
        """Attach <template-args> to a name.

        Deliberately records nothing. A function template specialisation is not a
        substitution candidate: 5.1.10 excludes function names, and both reference
        demanglers reject `_Z1fIiEvS0_`, proving the table for `_Z1fIiEv...` holds only
        `f`. Where the specialisation is a *type* rather than a function, `_type()`
        records it, because there the candidate is <type>.
        """
        return self.builder.template(base, self.template_arguments(install_scope=True))

    def nested_name(self, as_type=False):
        """<nested-name> ::= N [<CV-qualifiers>] [<ref-qualifier>] <prefix> <unqualified-name> E
        | N [<CV-qualifiers>] [<ref-qualifier>] <template-prefix> <template-args> E
        """
        reader = self.reader
        reader.expect("N")
        quals = self.cv_qualifiers()
        ref_qualifier = ""
        if reader.eat("R"):
            ref_qualifier = "&"
        elif reader.eat("O"):
            ref_qualifier = "&&"

        parts = []
        is_template = False
        outer_ctor_dtor = self._ctor_dtor
        self._ctor_dtor = False
        try:
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated nested name")
                self._enter()
                try:
                    is_template = self.prefix_component(parts, as_type)
                finally:
                    self._leave()

            if not parts:
                raise ParseError(self._mangled, reader.pos, "empty nested name")
            # A template constructor -- `basic_string<allocator<char>>(char const*, ...)`
            # -- is a template, but constructors have no return type to encode, so the
            # leading type of the signature is a parameter like any other.
            if self._ctor_dtor:
                is_template = False
        finally:
            self._ctor_dtor = outer_ctor_dtor
        name = parts[0] if len(parts) == 1 else self.builder.qualified(parts)
        return name, quals, ref_qualifier, is_template

    def prefix_component(self, parts, as_type=False):
        """One component of a <prefix>, appended to `parts`.

        Returns whether this component was a template specialisation; only the answer
        for the final component reaches the caller, and this is where it is known.

        Every <prefix> is a substitution candidate (5.1.10) *except* the last, which is
        an <unqualified-name> -- and function and operator names are explicitly excluded.
        We cannot tell we are on the last component until the closing `E` is in sight,
        so the check is "is the next character `E`".
        """
        reader = self.reader
        builder = self.builder
        char = reader.peek()

        if char == "S":
            parts.append(self.substitution(as_scope=True, expanded=self._abbreviation_scopes_a_structor()))
            return False

        if char == "T":
            component = self.template_param()
            parts.append(component)
            self.subs.remember(component, "template-template-param")
            return False

        if char == "D" and reader.peek(1) in "tT":
            parts.append(self.decltype_())
            return False

        if char == "I":
            # <template-prefix> <template-args>: the arguments attach to the component
            # just read, and the pair becomes one substitutable component.
            if not parts:
                raise ParseError(self._mangled, reader.pos, "template arguments with no name")
            arguments = self.template_arguments(install_scope=True)
            parts[-1] = builder.template(parts[-1], arguments)
            combined = parts[0] if len(parts) == 1 else builder.qualified(parts)
            # Only an *interior* <template-prefix> <template-args> is a separate
            # candidate. When the closing `E` follows, this specialisation is the whole
            # nested-name, and the enclosing <type> production records it -- recording
            # here too would enter it twice and shift every later index by one.
            if reader.peek() != "E":
                self.subs.remember(combined, "prefix")
            return True

        if char == "M":
            # <closure-prefix> terminator; carries no spelling of its own.
            reader.take()
            return False

        if char == "Q":
            # A C++20 requires-clause, `Q <constraint-expression>`. It constrains the
            # template but is not part of its name, and neither reference demangler
            # prints it -- so it is parsed for its side effects on the substitution
            # table and otherwise discarded. Newer than the grammar snapshot in
            # docs/specs/.
            reader.take()
            self._in_constraint = True
            try:
                self.expression()
            finally:
                self._in_constraint = False
            return False

        component = self.unqualified_name(scope=parts)
        parts.append(component)
        if reader.peek() != "E":
            combined = parts[0] if len(parts) == 1 else builder.qualified(parts)
            self.subs.remember(combined, "prefix")
        return False

    def local_name(self, as_type=False):
        """<local-name> ::= Z <function encoding> E <entity name> [<discriminator>]
        | Z <function encoding> E s [<discriminator>]
        | Z <function encoding> Ed [<parameter number>] _ <entity name>
        """
        reader = self.reader
        builder = self.builder
        reader.expect("Z")
        # `Z <encoding> E` holds a complete function declaration, with template
        # parameters of its own. Without a fresh naming context its `T_` and `T0_`
        # resolve against whatever enclosing template mentioned this local entity --
        # which is exactly what happens when a lambda defined inside one function
        # template is passed as an argument to another.
        outer_naming = self._naming
        outer_scope = self.targs.snapshot()
        self._naming = True
        self._drop_return = not self.options.local_name_return_type
        try:
            outer = self.encoding()
        finally:
            self._drop_return = False

        if reader.eat("Ed"):
            if reader.peek() != "_":
                reader.number(allow_negative=False)
            reader.expect("_")
            try:
                entity_is_type = as_type or reader.peek() == "U"
                inner, quals, ref_qualifier, is_template = self.name()
                combined = builder.qualified([outer, inner])
                if not entity_is_type and not reader.eof and reader.peek() not in "E_":
                    combined = self.bare_function_type(combined, quals, ref_qualifier, is_template)
            finally:
                self._naming = outer_naming
                self.targs.restore(outer_scope)
            return combined

        reader.expect("E")

        if reader.eat("s"):
            self.discriminator()
            self._naming = outer_naming
            self.targs.restore(outer_scope)
            return builder.qualified([outer, builder.raw("string literal")])

        try:
            # A closure or unnamed type is a *type*, so nothing follows it. Any other
            # entity may be a function, in which case its signature does. Without this
            # test, `Z1gvEUlvE_S_` reads the following parameter as the lambda's
            # signature and the substitution table is a parameter short from then on.
            entity_is_type = as_type or reader.peek() == "U"
            inner, quals, ref_qualifier, is_template = self.name()
            # The signature is applied to the *combined* name, not to the entity alone:
            # a return type belongs at the front of the whole declaration, so a generic
            # lambda's `operator()` reads `auto f()::'lambda'<...>::operator()(...)` and
            # not `f()::auto 'lambda'...`.
            combined = builder.qualified([outer, inner])
            if not entity_is_type and not reader.eof and reader.peek() not in "E_":
                combined = self.bare_function_type(combined, quals, ref_qualifier, is_template)
            self.discriminator()
        finally:
            self._naming = outer_naming
            self.targs.restore(outer_scope)
        return combined

    def discriminator(self):
        """<discriminator> ::= _ <non-negative number> | __ <number> _

        Tells apart same-named entities in one function. It carries no spelling, but it
        has to be consumed or it looks like trailing junk.
        """
        reader = self.reader
        if reader.peek() != "_":
            return
        if reader.peek(1) == "_":
            saved = reader.pos
            reader.pos += 2
            try:
                reader.number(allow_negative=False)
                reader.expect("_")
            except ParseError:
                reader.pos = saved
            return
        if reader.peek(1) in DIGITS:
            reader.take()
            reader.number(allow_negative=False)

    # -- 5.1.2 unqualified names -----------------------------------------------

    def unqualified_name(self, scope=None):
        """<unqualified-name> ::= <operator-name> [<abi-tags>] | <ctor-dtor-name>
        | <source-name> | <unnamed-type-name>
        | DC <source-name>+ E
        """
        reader = self.reader
        builder = self.builder
        char = reader.peek()

        if char in DIGITS:
            return builder.name(self.source_name())

        if char == "L":
            # An internal-linkage name. The marker carries no spelling.
            reader.take()
            return self.unqualified_name(scope)

        if char == "C":
            return self.constructor_name(scope)

        if char == "D":
            following = reader.peek(1)
            if following in DESTRUCTOR_KINDS:
                reader.pos += 2
                self._ctor_dtor = True
                return builder.name("~" + self.enclosing_class_name(scope))
            if following == "C":
                # A structured binding declaration: DC <source-name>+ E
                reader.pos += 2
                names = []
                while not reader.eat("E"):
                    if reader.eof:
                        raise ParseError(self._mangled, reader.pos, "unterminated structured binding")
                    names.append(self.source_name())
                return builder.name("[" + ", ".join(names) + "]")

        if char == "U":
            return self.unnamed_type_name()

        return builder.name(self.operator_name() + self.abi_tags())

    def constructor_name(self, scope):
        """<ctor-dtor-name> ::= C1 | C2 | C3 | CI1 <base class type> | CI2 <base class type>"""
        reader = self.reader
        reader.expect("C")
        self._ctor_dtor = True
        if reader.eat("I"):
            # An inheriting constructor names the base it inherits from.
            reader.take()
            self.type_()
            return self.builder.name(self.enclosing_class_name(scope))
        marker = reader.take()
        if marker not in CONSTRUCTOR_KINDS:
            raise ParseError(self._mangled, reader.pos, f"unknown constructor variant {marker!r}")
        return self.builder.name(self.enclosing_class_name(scope) + self.abi_tags())

    def enclosing_class_name(self, scope):
        """The bare class name that a constructor or destructor repeats.

        `Foo::Foo` is encoded as "the class, then a constructor marker" -- the marker
        has no spelling of its own, so the name is read back off the scope we are
        standing in. A specialisation's constructor drops the arguments: the constructor
        of `Foo<int>` is spelled `Foo<int>::Foo`, not `Foo<int>::Foo<int>`.
        """
        if not scope:
            raise ParseError(self._mangled, self.reader.pos, "constructor outside any class scope")
        spelled = self.builder.spell(scope[-1])
        # Drop everything the class name carries but the constructor does not: template
        # arguments (`Foo<int>::Foo`, never `Foo<int>::Foo<int>`) and ABI tags
        # (`failure[abi:cxx11]::failure`). Both attach directly to the class name, so
        # cutting at whichever comes first removes them and nothing else.
        cut = min((index for index in (spelled.find("<"), spelled.find("[")) if index > 0), default=-1)
        if cut > 0:
            spelled = spelled[:cut]
        # Then take the last component. A scope reached through an abbreviation arrives
        # as one part rather than as separate prefixes -- `Sa` is the single component
        # `std::allocator` -- and the class name is only its tail, so without this the
        # constructor of `std::allocator<char>` reads `std::allocator<char>::std::allocator`.
        separator = spelled.rfind("::")
        return spelled[separator + 2 :] if separator >= 0 else spelled

    def source_name(self):
        """<source-name> ::= <positive length number> <identifier>"""
        reader = self.reader
        length = int(reader.digits())
        if length <= 0:
            raise ParseError(self._mangled, reader.pos, "source name of non-positive length")
        text = reader.take_exactly(length)
        if text.startswith("_GLOBAL__N"):
            # The compiler's spelling for an anonymous namespace.
            return "(anonymous namespace)"
        return text + self.abi_tags()

    def abi_tags(self):
        """<abi-tags> ::= <abi-tag>* where <abi-tag> ::= B <source-name>"""
        tags = []
        while self.reader.peek() == "B":
            self.reader.take()
            tags.append(f"[abi:{self.source_name()}]")
        return "".join(tags)

    def unnamed_type_name(self):
        """<unnamed-type-name> ::= Ut [<number>] _ | <closure-type-name>

        <closure-type-name> ::= Ul <lambda-sig> E [<number>] _
        """
        reader = self.reader
        reader.expect("U")

        if reader.eat("t"):
            index = reader.digits() if reader.peek() in DIGITS else ""
            reader.expect("_")
            if self.options.gnu_closure_spelling:
                return self.builder.raw(f"{{unnamed type#{int(index) + 2 if index else 1}}}")
            return self.builder.raw(f"'unnamed{index}'")

        if reader.eat("l"):
            # A generic lambda declares its template parameters first, and they become
            # the `T_` scope its own signature is written against.
            declarations = []
            saved_counts = self._parameter_counts
            saved_scope = self.targs.snapshot()
            self._parameter_counts = {}
            try:
                while reader.peek2() in _PARAMETER_DECLARATIONS:
                    binding, declaration = self.template_param_decl()
                    declarations.append(declaration)
                    self.targs.add(self.builder.raw(binding))
                parameters = []
                while not reader.eat("E"):
                    if reader.eof:
                        raise ParseError(self._mangled, reader.pos, "unterminated lambda signature")
                    parameters.append(self.builder.spell(self.type_()))
            finally:
                self._parameter_counts = saved_counts
                self.targs.restore(saved_scope)
            template_header = f"<{', '.join(declarations)}>" if declarations else ""
            if parameters == ["void"]:
                parameters = []
            index = reader.digits() if reader.peek() in DIGITS else ""
            reader.expect("_")
            if self.options.gnu_closure_spelling:
                number = int(index) + 2 if index else 1
                return self.builder.raw(f"{{lambda{template_header}({', '.join(parameters)})#{number}}}")
            return self.builder.raw(f"'lambda{index}'{template_header}({', '.join(parameters)})")

        raise ParseError(self._mangled, reader.pos, "unknown unnamed-type-name")

    def operator_name(self):
        """<operator-name>, including conversions and literal operators."""
        reader = self.reader
        code = reader.peek2()

        if code == "cv":
            # A conversion operator names the type it converts to. Its operand may
            # reference template parameters, so it parses as a full type.
            reader.pos += 2
            return "operator " + self.builder.spell(self.type_())

        if code == "li":
            reader.pos += 2
            return 'operator"" ' + self.source_name()

        if code and code[0] == "v" and code[1] in DIGITS:
            # A vendor extended operator: v <digit> <source-name>
            reader.pos += 2
            return "operator " + self.source_name()

        if code in OPERATORS:
            reader.pos += 2
            spelling, needs_space = OPERATORS[code]
            return "operator" + (" " if needs_space else "") + spelling

        raise ParseError(self._mangled, reader.pos, f"unknown operator code {code!r}")

    # -- 5.1.10 substitutions --------------------------------------------------

    def _abbreviation_scopes_a_structor(self):
        """Whether an abbreviation at the cursor is the scope of a constructor or destructor.

        It decides how the abbreviation is spelled. `Ss::c_str` prints as
        `std::string::c_str()`, but `Ss`'s constructor prints as
        `std::basic_string<char, ...>::basic_string()` -- because the constructor is
        named for the class, and the class is the template, not the typedef. Both
        reference demanglers agree, and it is why `_ZNSdC1EOSd` spells its scope in full
        while spelling the very same abbreviation short as a parameter type.
        """
        reader = self.reader
        if reader.peek2() not in STD_ABBREVIATIONS:
            return False
        following = reader.text[reader.pos + 2 : reader.pos + 4]
        if len(following) != 2:
            return False
        return (following[0] == "C" and following[1] in CONSTRUCTOR_KINDS) or (
            following[0] == "D" and following[1] in DESTRUCTOR_KINDS
        )

    def substitution(self, as_scope=False, expanded=False):
        """<substitution> ::= S <seq-id> _ | S_ | St | Sa | Sb | Ss | Si | So | Sd

        `as_scope` is accepted for call-site clarity; the abbreviation spelling is a
        single policy set by `ItaniumOptions`, because both reference demanglers spell
        an abbreviation the same way in scope and in type position.
        """
        reader = self.reader
        reader.expect("S")
        code = "S" + reader.peek()
        table = self._abbrev_expanded if expanded else self._abbrev
        if code in table:
            reader.take()
            # An abbreviation is pre-defined: referring to it adds no dictionary entry,
            # because the encoder never had to add one either.
            return self.builder.raw(table[code])
        return self.subs.lookup(reader.seq_id())

    def template_param(self):
        """<template-param> ::= T_ | T <parameter-2 non-negative number> _
                              | TL <level> _ [<parameter-2 non-negative number>] _

        The `TL` form names a parameter of an enclosing template by level as well as by
        index, which Clang emits inside the constraints of a nested template. It is
        newer than the grammar snapshot in docs/specs/.
        """
        reader = self.reader
        reader.expect("T")

        if reader.eat("L"):
            reader.digits()
            reader.expect("_")
            level_index = reader.integer(allow_negative=False) + 1 if reader.peek() != "_" else 0
            reader.expect("_")
            return self._symbolic_parameter(level_index)

        # Tp/Ts mark a pack expansion of the parameter; the pack was recorded as one
        # argument, so the marker only needs consuming.
        reader.eat("p") or reader.eat("s")
        index = 0 if reader.peek() == "_" else reader.integer(allow_negative=False) + 1
        reader.expect("_")

        if self._in_constraint and self.options.symbolic_constraint_parameters:
            # Inside a requires-clause the references spell a parameter symbolically --
            # `T`, `T0` -- rather than substituting the argument bound to it. The clause
            # itself is not printed, but the entry it adds to the substitution table is
            # referred to from the signature, so the spelling matters.
            return self._symbolic_parameter(index)
        bound = self.targs.lookup(index)
        if bound is not None:
            return bound
        # A return type is encoded before the arguments that bind its parameters, so a
        # name may legitimately reference one we do not know yet. The reference
        # demanglers spell that `auto`.
        return self.builder.raw("auto")

    def _symbolic_parameter(self, index):
        """A template parameter spelled by name rather than by the argument bound to it."""
        return self.builder.raw("T" + ("" if index == 0 else str(index - 1)))

    def decltype_(self):
        """<decltype> ::= Dt <expression> E | DT <expression> E"""
        reader = self.reader
        reader.expect("D")
        marker = reader.take()
        if marker not in "tT":
            raise ParseError(self._mangled, reader.pos, "expected a decltype")
        expression = self.expression()
        reader.expect("E")
        keyword = "decltype " if self.options.gnu_expression_spelling else "decltype"
        return self.builder.raw(f"{keyword}({expression})")

    # -- 5.1.5 types -----------------------------------------------------------

    def cv_qualifiers(self):
        """<CV-qualifiers> ::= [r] [V] [K], returned in C++'s canonical order."""
        reader = self.reader
        found = set()
        while reader.peek() in QUALIFIER_LETTERS:
            found.add(QUALIFIER_LETTERS[reader.take()])
        if not found:
            return ()
        return tuple(qualifier for qualifier in QUALIFIER_ORDER if qualifier in found)

    def type_(self):
        self._enter()
        try:
            return self._type()
        finally:
            self._leave()

    def _type(self):
        reader = self.reader
        builder = self.builder
        subs = self.subs
        char = reader.peek()

        # <builtin-type>: never a substitution candidate (5.1.10).
        if char in BUILTIN_TYPES:
            reader.take()
            return builder.builtin(BUILTIN_TYPES[char])

        if char in QUALIFIER_LETTERS:
            qualifiers = self.cv_qualifiers()
            if reader.peek() == "F":
                # 5.1.5.3: <function-type> ::= [<CV-qualifiers>] [<exception-spec>] [Dx]
                # F [Y] <bare-function-type> [<ref-qualifier>] E. The qualifiers are part
                # of *this* production, so `KFbvE` is the single component
                # `bool () const` -- not a `bool ()` that a qualifier is then applied to.
                # Recording both would enter one component too many and shift every
                # later back-reference.
                return subs.remember(builder.qualify(self.function_type(), qualifiers), "type")
            inner = self.type_()
            return subs.remember(builder.qualify(inner, qualifiers), "type")

        if char == "P":
            reader.take()
            return subs.remember(builder.pointer(self.type_()), "type")
        if char == "R":
            reader.take()
            return subs.remember(builder.reference(self.type_()), "type")
        if char == "O":
            reader.take()
            return subs.remember(builder.rvalue_reference(self.type_()), "type")
        if char == "C":
            reader.take()
            inner = self.type_()
            return subs.remember(builder.raw(f"std::complex<{builder.spell(inner)}>"), "type")
        if char == "G":
            reader.take()
            inner = self.type_()
            return subs.remember(builder.raw(f"_Imaginary {builder.spell(inner)}"), "type")

        if char == "U":
            # <type> ::= U <source-name> [<template-args>] <type>  -- vendor qualifier
            reader.take()
            qualifier = self.source_name()
            if reader.peek() == "I":
                arguments = self.template_arguments()
                rendered = ", ".join(builder.spell(argument) for argument in arguments)
                qualifier += f"<{rendered}>"
            inner = self.type_()
            return subs.remember(builder.vendor_qualify(inner, qualifier), "type")

        if char == "F":
            return subs.remember(self.function_type(), "type")
        if char == "A":
            return subs.remember(self.array_type(), "type")
        if char == "M":
            return subs.remember(self.member_pointer_type(), "type")

        if char == "T":
            component = self.template_param()
            if reader.peek() == "I":
                # <template-template-param> <template-args>. The parameter itself is
                # *not* recorded again: it was reached through <template-param>, which
                # resolves to an entity already in the table, and 5.1.10 forbids
                # entering the same entity twice. Confirmed by probing
                # `_Z16templateTemplateIN5outer5inner6HolderEiET_IT0_Li3EE`, where the
                # reference has one `outer::inner::Holder` entry, not two.
                return subs.remember(builder.template(component, self.template_arguments()), "type")
            # A <template-param> reached through <type> is a <type>, and <type> is a
            # candidate. Confirmed by `_ZSt4sortIPiEvT_S1_`, where `S1_` resolves to
            # `int*` -- the entry the `T_` parameter itself contributed.
            return subs.remember(component, "type")

        if char == "S":
            if reader.peek(1) == "t":
                # `St <unqualified-name>` is an <unscoped-name>, not a bare
                # abbreviation: the name that follows belongs to it.
                return subs.remember(self.class_enum_type(), "type")
            component = self.substitution()
            if reader.peek() == "I":
                return subs.remember(builder.template(component, self.template_arguments()), "type")
            return component

        if char == "D":
            extended = self.extended_type()
            if extended is not None:
                return extended

        if char in DIGITS or char in "NZ" or char == "L":
            return subs.remember(self.class_enum_type(), "type")

        raise ParseError(self._mangled, reader.pos, f"unknown type code {char!r}")

    def extended_type(self):
        """The `D`-introduced types: extended builtins, decltype, packs, vectors."""
        reader = self.reader
        builder = self.builder
        pair = reader.peek2()

        if pair in EXTENDED_BUILTIN_TYPES:
            reader.pos += 2
            if pair in ("DB", "DU"):
                # _BitInt(N): DB <number> _ | DB <expression> _
                width = reader.digits() if reader.peek() in DIGITS else self.expression()
                reader.expect("_")
                return builder.raw(f"{EXTENDED_BUILTIN_TYPES[pair]}({width})")
            return builder.builtin(EXTENDED_BUILTIN_TYPES[pair])

        if pair == "DF":
            # DF <number> _ : an extended floating-point type, _FloatN.
            reader.pos += 2
            width = reader.digits()
            reader.eat("x")
            reader.expect("_")
            return builder.builtin(f"_Float{width}")

        if pair in ("Dt", "DT"):
            return self.subs.remember(self.decltype_(), "type")

        if pair == "Dp":
            reader.pos += 2
            inner = self.type_()
            if any(inner is pack for pack in self._packs) or self._scope_has_pack:
                # The expansion is a <type> in its own right and is recorded as one,
                # separately from the type it expands: `Dp R T1_` contributes both the
                # `R T1_` entry and the expansion's. Both reference demanglers do this,
                # and a name referring past them comes out short otherwise.
                #
                # `inner` is a pack, and every declarator between here and the `T_` has
                # already distributed over its members -- so `Dp O T_` over three
                # arguments arrives as three rvalue references, fully spelled. Expansion
                # is what those members *are*; an ellipsis would be spelling it twice.
                return self.subs.remember(inner, "type")
            # No pack in scope: this is an unexpanded expansion, and the ellipsis is the
            # whole content of it.
            return self.subs.remember(builder.pack(inner), "type")

        if pair == "Dv":
            return self.subs.remember(self.vector_type(), "type")

        # <function-type> ::= [<CV-qualifiers>] [<exception-spec>] [Dx] F ... E, so an
        # exception specification introduces a function type rather than wrapping one.
        # Spelling it around the result instead loses the declarator: a pointer to a
        # `void () noexcept` would come out `void () noexcept*` rather than
        # `void (*)() noexcept`.
        if pair == "Do":
            reader.pos += 2
            return self.subs.remember(self.function_type(" noexcept"), "type")

        if pair == "DO":
            # throw(<expression>)
            reader.pos += 2
            condition = self.expression()
            reader.expect("E")
            return self.subs.remember(self.function_type(f" throw({condition})"), "type")

        if pair == "Dw":
            # throw(<type>...)
            reader.pos += 2
            thrown = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated throw specification")
                thrown.append(builder.spell(self.type_()))
            return self.subs.remember(self.function_type(f" throw({', '.join(thrown)})"), "type")

        if pair == "Dx":
            # A transaction-safe function type.
            reader.pos += 2
            return self.subs.remember(self.function_type(" transaction_safe"), "type")

        return None

    def vector_type(self):
        """<type> ::= Dv <number> _ <type> | Dv _ <expression> _ <type>"""
        reader = self.reader
        reader.expect("Dv")
        size = self.expression() if reader.eat("_") else reader.digits()
        reader.expect("_")
        inner = self.type_()
        return self.builder.raw(f"{self.builder.spell(inner)} vector[{size}]")

    def class_enum_type(self):
        """<class-enum-type> ::= <name> | Ts <name> | Tu <name> | Te <name>

        `as_type` matters for a <local-name>: whether a signature follows the entity is
        not decidable from the grammar alone, only from where the name sits.
        `Z <encoding> E <entity>` reached as an <encoding> may be a local *function*, and
        the types after it are its parameters; reached as a <type> it names a local class,
        and what follows belongs to whatever mentioned it.
        """
        name, _, _, _ = self.name(as_type=True)
        return name

    def function_type(self, exception_spec=""):
        """<function-type> ::= [<CV-qualifiers>] [<exception-spec>] [Dx] F [Y]
                               <bare-function-type> [<ref-qualifier>] E

        `exception_spec` is already-spelled text from the caller, which read it before
        the `F` because that is where the grammar puts it.
        """
        reader = self.reader
        builder = self.builder
        reader.eat("Dx")  # a transaction-safe function
        reader.expect("F")
        reader.eat("Y")  # extern "C"

        returns = self.type_()
        parameters = []
        suffix = ""
        while not reader.eat("E"):
            if reader.eof:
                raise ParseError(self._mangled, reader.pos, "unterminated function type")
            if reader.peek() == "R" and reader.peek(1) == "E":
                reader.take()
                suffix = " &"
                continue
            if reader.peek() == "O" and reader.peek(1) == "E":
                reader.take()
                suffix = " &&"
                continue
            parameter = self.type_()
            if builder.spell(parameter):
                parameters.append(parameter)

        if len(parameters) == 1 and builder.spell(parameters[0]) == "void":
            parameters = []
        return builder.function(returns, parameters, suffix + exception_spec)

    def array_type(self):
        """<array-type> ::= A [<number>] _ <type> | A <expression> _ <type>"""
        reader = self.reader
        reader.expect("A")
        if reader.peek() == "_":
            dimension = ""
        elif reader.peek() in DIGITS:
            dimension = reader.digits()
        else:
            dimension = self.expression()
        reader.expect("_")
        return self.builder.array(self.type_(), dimension)

    def member_pointer_type(self):
        """<pointer-to-member-type> ::= M <class type> <member type>"""
        reader = self.reader
        reader.expect("M")
        owner = self.type_()
        member = self.type_()
        return self.builder.member_pointer(owner, member)

    # -- 5.1.5.10 template arguments -------------------------------------------

    def template_param_decl(self):
        """<template-param-decl> ::= Ty | Tk <concept-name> | Tn <type>
                                   | Tt <template-param-decl>* E | Tp <template-param-decl>

        Declares a template parameter rather than supplying an argument. Newer than the
        grammar snapshot in docs/specs/, and emitted by Clang for constrained templates
        and for generic lambdas.

        Returns `(binding, declaration)`. In an ordinary argument list both are
        discarded -- the references print nothing there. In a generic lambda's signature
        both are used: the lambda is spelled `'lambda'<typename $T>($T)`, so the
        declaration is printed and `$T` is what `T_` resolves to inside it. The names
        are numbered per kind, first unsuffixed: `$T`, `$T0`, `$T1`.
        """
        reader = self.reader
        pair = reader.peek2()
        reader.pos += 2

        if pair == "Ty":
            binding = self._parameter_name("T")
            return binding, f"typename {binding}"
        if pair == "Tk":
            # A constrained parameter: the concept it must satisfy, then the parameter.
            concept = self.builder.spell(self.name()[0])
            binding = self._parameter_name("T")
            return binding, f"{concept} {binding}"
        if pair == "Tn":
            kind = self.builder.spell(self.type_())
            binding = self._parameter_name("N")
            return binding, f"{kind} {binding}"
        if pair == "Tp":
            binding, declaration = self.template_param_decl()
            return binding, f"{declaration}..."
        if pair == "Tt":
            inner = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated template parameter list")
                inner.append(self.template_param_decl()[1])
            binding = self._parameter_name("TT")
            return binding, f"template<{', '.join(inner)}> typename {binding}"
        raise ParseError(self._mangled, reader.pos, f"unknown template parameter declaration {pair!r}")

    def _parameter_name(self, kind):
        """The synthetic name for a declared parameter.

        llvm-cxxfilt leaves the first unsuffixed -- `$T`, `$T0`, `$T1` -- while GNU
        c++filt numbers from zero throughout: `$T0`, `$T1`.
        """
        index = self._parameter_counts.get(kind, 0)
        self._parameter_counts[kind] = index + 1
        if self.options.gnu_closure_spelling:
            return f"${kind}{index}"
        return f"${kind}" + ("" if index == 0 else str(index - 1))

    def template_arguments(self, install_scope=False):
        """<template-args> ::= I <template-arg>+ E

        `T_` names a parameter of the innermost enclosing template *declaration*, so
        which argument list is in scope matters and the two callers differ:

        `install_scope=True` -- these arguments belong to the entity being named, so
        they become the `T_` scope, replacing any outer one. In
        `Class<char>::method<char const*>`, `T_` inside the signature is `char const*`,
        not `char`. They are installed one at a time because a later argument may
        reference an earlier one.

        `install_scope=False` -- these arguments belong to a *type* mentioned in
        passing, such as the `std::allocator<char>` inside a parameter list. They are
        not template parameters of anything being declared here, so they leave the
        scope untouched; letting them append would corrupt every later `T_`.
        """
        reader = self.reader
        reader.expect("I")
        install_scope = install_scope and self._naming
        if install_scope:
            self.targs.restore(())
            self._scope_has_pack = False
        # Whatever these arguments contain, it is a type mentioned in passing, not the
        # entity being declared -- so a nested argument list inside one of them must not
        # install a scope of its own. Without this, the arguments of the
        # `__normal_iterator<wchar_t*, ...>` passed to a `basic_string` constructor
        # replace the constructor's own, and every `T_` in the signature resolves to
        # `wchar_t*`.
        was_naming = self._naming
        self._naming = False
        arguments = []
        try:
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated template argument list")
                self._enter()
                try:
                    argument, is_empty_pack = self.template_arg()
                finally:
                    self._leave()
                if argument is None:
                    # A <template-param-decl>: it declares a parameter rather than
                    # supplying an argument, and neither prints nor occupies a slot.
                    continue
                if not is_empty_pack:
                    arguments.append(argument)
                if install_scope:
                    self.targs.add(argument)
        finally:
            self._naming = was_naming
        return arguments

    def template_arg(self):
        """<template-arg> ::= <type> | X <expression> E | <expr-primary> | J <template-arg>* E

        Returns `(handle, is_empty_pack)`. The handle is None for a
        <template-param-decl>, which declares a parameter rather than supplying one.

        Emptiness is returned rather than recorded on the parser because argument lists
        nest: an empty pack inside `AnalysisManager<Module, JE>` would otherwise still be
        flagged when the enclosing `PassManager<Function, AnalysisManager<...>>` finished
        its own argument, and the enclosing argument would be dropped.
        """
        reader = self.reader
        builder = self.builder

        if reader.peek2() in _PARAMETER_DECLARATIONS:
            self.template_param_decl()
            return None, False

        if reader.peek() == "Q":
            # A requires-clause closing out an argument list. Parsed for its effect on
            # the substitution table; neither reference prints it.
            reader.take()
            self._in_constraint = True
            try:
                self.expression()
            finally:
                self._in_constraint = False
            return None, False

        if reader.eat("X"):
            expression = self.expression()
            reader.expect("E")
            return builder.raw(expression), False

        if reader.peek() == "L":
            return builder.raw(self.expr_primary()), False

        if reader.eat("J"):
            members = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated argument pack")
                member, _ = self.template_arg()
                if member is not None:
                    members.append(member)
            self._scope_has_pack = True
            handle = builder.parameter_pack(members)
            self._packs.append(handle)
            # An empty pack still occupies an argument position for `T_` numbering, but
            # contributes nothing to spell: `AnalysisManager<Module, JE>` is
            # `AnalysisManager<llvm::Module>`, not `AnalysisManager<llvm::Module, >`.
            # Asked of the builder rather than counted here, because a pack whose only
            # member expands another, empty pack is itself empty and only the builder
            # knows that -- it is what flattened them.
            return handle, not builder.spell(handle)

        return self.type_(), False

    # -- 5.1.6.1 literals ------------------------------------------------------

    def expr_primary(self):
        """<expr-primary> ::= L <type> <value number> E | L <mangled-name> E | L _Z <encoding> E"""
        reader = self.reader
        builder = self.builder
        reader.expect("L")

        if reader.startswith("_Z"):
            # A reference to a declared entity rather than a value (5.1.6.2): a complete
            # mangled name embedded in this one. It is parsed with *this* parser's state
            # rather than a fresh one, because the compiler writes substitutions inside
            # it that index the enclosing name's table -- Clang emits exactly that for
            # the address of a function template passed as a non-type argument, and a
            # fresh table makes every one of those unresolvable.
            reader.expect("_Z")
            was_naming = self._naming
            outer_scope = self.targs.snapshot()
            self._naming = True
            try:
                handle = self.encoding()
            finally:
                # The substitution table is shared deliberately, but the `T_` scope is
                # not: the embedded entity has template parameters of its own, and
                # letting them stand would leave every later `T_` in the enclosing name
                # resolving against the wrong argument list.
                self._naming = was_naming
                self.targs.restore(outer_scope)
            reader.expect("E")
            return builder.spell(handle)

        kind = self.type_()
        spelling = builder.spell(kind)

        if reader.eat("E"):
            # A literal with no value: how the scheme writes `nullptr`.
            return "nullptr" if spelling == "std::nullptr_t" else f"({spelling})0"

        start = reader.pos
        while not reader.eat("E"):
            if reader.eof:
                raise ParseError(self._mangled, reader.pos, "unterminated literal")
            reader.take()
        value = reader.text[start : reader.pos - 1]
        return self.spell_literal(spelling, value)

    @staticmethod
    def spell_literal(kind, value):
        """Render a literal the way the reference demanglers do.

        The scheme encodes the value as digits with a leading `n` for negative, and
        leaves the suffix implied by the type. Reproducing the suffix matters: `1` and
        `1u` are different template arguments and must not print alike.
        """
        if value.startswith("n"):
            value = "-" + value[1:]

        if kind == "bool":
            return {"0": "false", "1": "true"}.get(value, f"(bool){value}")
        if kind == "int":
            return value
        suffixes = {
            "unsigned int": "u",
            "long": "l",
            "unsigned long": "ul",
            "long long": "ll",
            "unsigned long long": "ull",
        }
        if kind in suffixes:
            return value + suffixes[kind]
        if kind == "char":
            return f"(char){value}"
        return f"({kind}){value}"

    # -- 5.1.6 expressions -----------------------------------------------------

    # -- 5.1.6 unresolved names ------------------------------------------------

    def unresolved_name(self):
        """<unresolved-name> ::= [gs] <base-unresolved-name>
                               | sr <unresolved-type> <base-unresolved-name>
                               | srN <unresolved-type> <unresolved-qualifier-level>+ E
                                     <base-unresolved-name>
                               | [gs] sr <unresolved-qualifier-level>+ E <base-unresolved-name>

        A name written in a template that the compiler could not resolve, because it
        depends on a parameter: `std::is_signed_v<T>` inside an `enable_if`. These reach
        a mangled name through SFINAE return types, which is why they are everywhere in
        heavily templated C++ and absent from simple test cases.

        The two `sr` forms without `N` are told apart by what follows: an
        <unresolved-qualifier-level> is a <simple-id> and so begins with a digit, while
        an <unresolved-type> begins with `T`, `D` or `S`.
        """
        reader = self.reader
        prefix = "::" if reader.eat("gs") else ""

        if not reader.eat("sr"):
            return prefix + self.base_unresolved_name()

        levels = []
        if reader.eat("N"):
            levels.append(self.unresolved_type())
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated qualifier levels")
                levels.append(self.simple_id())
        elif reader.peek() in DIGITS:
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated qualifier levels")
                levels.append(self.simple_id())
        else:
            levels.append(self.unresolved_type())

        levels.append(self.base_unresolved_name())
        return prefix + "::".join(levels)

    def unresolved_type(self):
        """<unresolved-type> ::= <template-param> [<template-args>] | <decltype> | <substitution>"""
        reader = self.reader
        builder = self.builder
        if reader.peek() == "T":
            component = self.template_param()
            if reader.peek() == "I":
                component = builder.template(component, self.template_arguments())
            return builder.spell(self.subs.remember(component, "unresolved-type"))
        if reader.peek() == "D":
            return builder.spell(self.subs.remember(self.decltype_(), "unresolved-type"))
        return builder.spell(self.substitution())

    def simple_id(self):
        """<simple-id> ::= <source-name> [<template-args>]"""
        text = self.source_name()
        if self.reader.peek() == "I":
            text += self.spelled_template_arguments()
        return text

    def spelled_template_arguments(self):
        """A template argument list rendered as text, for use inside a name."""
        arguments = self.template_arguments()
        rendered = ", ".join(self.builder.spell(argument) for argument in arguments)
        return f"<{rendered}>"

    def base_unresolved_name(self):
        """<base-unresolved-name> ::= <simple-id> | on <operator-name> [<template-args>]
        | dn <destructor-name>
        """
        reader = self.reader
        if reader.eat("on"):
            text = self.operator_name()
            if reader.peek() == "I":
                text += self.spelled_template_arguments()
            return text
        if reader.eat("dn"):
            return "~" + self.destructor_name()
        return self.simple_id()

    def destructor_name(self):
        """<destructor-name> ::= <unresolved-type> | <simple-id>"""
        if self.reader.peek() in DIGITS:
            return self.simple_id()
        return self.unresolved_type()

    def expression_name(self):
        """A name appearing in an expression -- the callee of a call, most often.

        Records nothing for the name itself. Section 5.1.10 excludes "function and
        operator names other than extern \"C\" functions" from the candidate set, and
        adds that "we do not substitute for expressions, though names appearing in them
        might be substituted": a name in an expression may *refer* to an existing entry,
        but it does not create one.

        Template arguments applied to it still record their own types, which is how
        `_ZSt12construct_atIcJRKcEE...cl7declvalIT0_EE...` reaches `S4_` -- that entry is
        the `T0_` inside `declval`'s argument list, not `declval` itself.
        """
        reader = self.reader
        builder = self.builder
        if reader.peek() in DIGITS:
            text = self.source_name()
            if reader.peek() == "I":
                arguments = self.template_arguments()
                rendered = ", ".join(builder.spell(argument) for argument in arguments)
                return f"{text}<{rendered}>"
            return text
        return builder.spell(self.type_())

    def initialiser(self):
        """<initializer> ::= pi <expression>* E -- a parenthesised initialiser list."""
        reader = self.reader
        if not reader.eat("pi"):
            expression = self.expression()
            reader.eat("E")
            return f"({expression})"
        arguments = []
        while not reader.eat("E"):
            if reader.eof:
                raise ParseError(self._mangled, reader.pos, "unterminated initialiser")
            arguments.append(self.expression())
        return f"({', '.join(arguments)})"

    def expression(self):
        """A constant expression appearing in a type or template argument.

        Only the shapes a compiler actually emits into a name are modelled. The full
        expression grammar is enormous, and most of it cannot reach a mangled name
        because it is not part of any signature.

        Sets `_precedence`, which a containing operator reads immediately afterwards to
        decide whether this operand needs brackets.
        """
        self._enter()
        try:
            self._precedence = PRIMARY_PRECEDENCE
            return self._expression()
        finally:
            self._leave()

    def _spell_parameter(self, index):
        """A reference to a function parameter, `fp_` / `fp0_` / `fpT_`."""
        if self.options.gnu_expression_spelling:
            return f"{{parm#{int(index) + 2 if index else 1}}}"
        return f"fp{index}"

    def _operand(self, binding):
        """One operand, bracketed only when it binds more loosely than its operator."""
        text = self.expression()
        return f"({text})" if self._precedence < binding else text

    def _expression(self):
        reader = self.reader
        builder = self.builder

        if reader.peek() == "L":
            return self.expr_primary()
        if reader.peek() == "T":
            return builder.spell(self.template_param())

        pair = reader.peek2()

        if pair == "fp":
            # A function parameter reference (5.1.5.9).
            reader.pos += 2
            reader.eat("T")
            index = reader.digits() if reader.peek() in DIGITS else ""
            reader.eat("_")
            return self._spell_parameter(index)
        if pair == "fL":
            reader.pos += 2
            reader.digits()
            reader.eat("p")
            index = reader.digits() if reader.peek() in DIGITS else ""
            reader.eat("_")
            return self._spell_parameter(index)

        if pair == "sr":
            return self.unresolved_name()
        if pair == "sZ":
            reader.pos += 2
            return f"sizeof...({builder.spell(self.template_param())})"
        if pair == "sP":
            reader.pos += 2
            members = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated sizeof... pack")
                members.append(builder.spell(self.template_arg()))
            return f"sizeof...({', '.join(members)})"
        if pair == "st":
            reader.pos += 2
            return f"sizeof({builder.spell(self.type_())})"
        if pair == "sz":
            reader.pos += 2
            return f"sizeof({self.expression()})"
        if pair == "at":
            reader.pos += 2
            return f"alignof({builder.spell(self.type_())})"
        if pair == "az":
            reader.pos += 2
            return f"alignof({self.expression()})"
        if pair == "ti":
            reader.pos += 2
            return f"typeid({builder.spell(self.type_())})"
        if pair == "te":
            reader.pos += 2
            return f"typeid({self.expression()})"
        if pair == "nx":
            reader.pos += 2
            return f"noexcept({self.expression()})"

        if pair == "cl":
            reader.pos += 2
            target = self.expression()
            if self.options.gnu_expression_spelling:
                target = f"({target})"
            arguments = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated call expression")
                arguments.append(self.expression())
            return f"{target}({', '.join(arguments)})"

        if pair == "cv":
            reader.pos += 2
            kind = builder.spell(self.type_())
            if reader.eat("_"):
                arguments = []
                while not reader.eat("E"):
                    if reader.eof:
                        raise ParseError(self._mangled, reader.pos, "unterminated conversion")
                    arguments.append(self.expression())
                return f"({kind})({', '.join(arguments)})"
            return f"({kind})({self.expression()})"

        if pair == "tl":
            reader.pos += 2
            kind = builder.spell(self.type_())
            members = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated braced initialiser")
                members.append(self.expression())
            return f"{kind}{{{', '.join(members)}}}"

        if pair == "il":
            reader.pos += 2
            members = []
            while not reader.eat("E"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated initialiser list")
                members.append(self.expression())
            return "{" + ", ".join(members) + "}"

        if pair == "qu":
            reader.pos += 2
            condition = self.expression()
            when_true = self.expression()
            when_false = self.expression()
            self._precedence = PRECEDENCE["qu"]
            return f"({condition}) ? ({when_true}) : ({when_false})"

        if pair in ("dt", "pt"):
            # <expression> ::= dt <expression> <unresolved-name>  (and `pt` for `->`)
            reader.pos += 2
            owner = self.expression()
            joiner = "." if pair == "dt" else "->"
            return owner + joiner + self.unresolved_name()

        if pair == "ix":
            reader.pos += 2
            owner = self.expression()
            return f"{owner}[{self.expression()}]"

        if pair == "gs":
            # A leading `::` forcing global scope. It introduces either a global-scope
            # allocation -- `::new`, `::delete` -- or a global-scope name, and only the
            # next production says which.
            if reader.text[reader.pos + 2 : reader.pos + 4] in ("nw", "na", "dl", "da"):
                reader.pos += 2
                self._precedence = UNARY_PRECEDENCE
                return "::" + self.expression()
            return self.unresolved_name()

        if pair == "sp":
            # A pack expansion inside an expression, under the same rule as `Dp`.
            reader.pos += 2
            expanded = self.expression()
            return expanded if self._scope_has_pack else expanded + "..."

        if pair == "nw" or pair == "na":
            reader.pos += 2
            arguments = []
            while not reader.eat("_"):
                if reader.eof:
                    raise ParseError(self._mangled, reader.pos, "unterminated new expression")
                arguments.append(self.expression())
            kind = builder.spell(self.type_())
            keyword = "new" if pair == "nw" else "new[]"
            gap = " " if self.options.gnu_expression_spelling else ""
            placement = f"{gap}({', '.join(arguments)})" if arguments else ""
            self._precedence = UNARY_PRECEDENCE
            if reader.eat("E"):
                return f"{keyword}{placement} {kind}"
            return f"{keyword}{placement} {kind}{self.initialiser()}"

        if pair in ("dl", "da"):
            reader.pos += 2
            keyword = "delete" if pair == "dl" else "delete[]"
            return f"{keyword} {self.expression()}"

        if pair in ("dc", "sc", "cc", "rc"):
            reader.pos += 2
            casts = {"dc": "dynamic_cast", "sc": "static_cast", "cc": "const_cast", "rc": "reinterpret_cast"}
            kind = builder.spell(self.type_())
            return f"{casts[pair]}<{kind}>({self.expression()})"

        if pair in PREFIX_OPERATORS:
            reader.pos += 2
            # A prefix operator brackets anything that is not already primary, including
            # another prefix operator: the references print `!(!true)`, not `!!true`.
            operand = self._operand(PRIMARY_PRECEDENCE)
            self._precedence = UNARY_PRECEDENCE
            return f"{PREFIX_OPERATORS[pair]}{operand}"

        if pair in INFIX_OPERATORS:
            reader.pos += 2
            binding = PRECEDENCE.get(pair, 1)
            # The side that does *not* absorb an equal-precedence neighbour needs the
            # brackets: for a left-grouping operator that is the right operand.
            right_associative = pair in RIGHT_ASSOCIATIVE
            left = self._operand(binding + 1 if right_associative else binding)
            right = self._operand(binding if right_associative else binding + 1)
            self._precedence = binding
            gap = "" if self.options.gnu_expression_spelling else " "
            return f"{left}{gap}{INFIX_OPERATORS[pair]}{gap}{right}"

        # A bare name here is an <unresolved-name>: the grammar says so, and it matters
        # because a name in an expression creates no substitution entry while a <type>
        # does. A constraint like `Q 5Sized I T_ E` must contribute the `T` its argument
        # list mentions and nothing for `Sized` itself.
        if reader.peek() in DIGITS:
            return self.unresolved_name()

        # What remains that could open a type, is one: array bounds and non-type
        # template arguments both arrive here.
        if reader.peek() in _TYPE_STARTERS:
            return builder.spell(self.type_())

        raise ParseError(self._mangled, reader.pos, "unrecognised expression")


def parse(mangled, builder, limits=DEFAULT_LIMITS, options=DEFAULT_OPTIONS):
    """Parse an Itanium mangled name into `builder`, returning its handle."""
    return ItaniumParser(mangled, builder, limits, options).parse()
