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
    """

    gnu_expression_spelling: bool = False
    """Spell expressions inside types the way GNU c++filt does.

    The two references diverge throughout expression syntax, and neither is more correct
    -- they are printing the same expression. GNU writes `decltype ({parm#1}+{parm#1})`
    where LLVM writes `decltype(fp + fp)`: a space after the keyword, no spaces around
    binary operators, function parameters numbered from one, and a call's callee
    parenthesised.
    """

    symbolic_constraint_parameters: bool = True
    """Spell a template parameter inside a requires-clause by name rather than by value.

    llvm-cxxfilt prints `T` where GNU c++filt substitutes the bound argument. The clause
    itself is never printed, but it contributes entries to the substitution table that
    the signature refers back to, so the choice is visible in the output.
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
)
