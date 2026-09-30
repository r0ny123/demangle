"""The Metrowerks CodeWarrior demangler, ported from `encounter/cwdemangle`.

CodeWarrior is the other pre-Itanium C++ mangling, and the reason it is a scheme of its
own rather than a sixth style of `gnuv2` is that libiberty never read it: `cplus-dem.c`
has no CodeWarrior flag, `demangle-expected` has no vectors for it, and binutils has
never demangled one. It is what Nintendo GameCube and Wii titles, and a great deal of
other 1990s and 2000s embedded work, were built with -- which is why the reference is a
decompilation project's tool rather than a compiler vendor's.

The reference is `encounter/cwdemangle`, dedicated to the public domain under CC0-1.0.
This is a transcription of its `demangle`, with its function names kept.

The encoding looks like ARM's and is not:

* a template argument list is written *literally*, `single_ptr<10CModelData>`, brackets
  and commas and all, rather than being encoded -- so finding where the name ends and
  the signature begins means counting `<` and `>` before looking for the `__`;
* `Q2` introduces a qualified name whose component count is a single digit, and each
  component is length-prefixed and may itself carry a template argument list;
* a pointer-to-member is `M<class>F` followed by two hidden parameters, `PCvPv` or
  `PCvPCv`, and which of the two decides whether the member function is `const`;
* the spelling is the reference's own: `const char*` with the `*` against the type, and
  `void (*)(const char*, void*)` built from a `pre`/`post` pair around the declarator.

Two options are the reference's: `omit_empty_parameters`, which is on by default and
turns `(void)` into `()`, and `mw_extensions`, which is *off* by default because the
Metrowerks extension types `__int128` and `__vec2x32float__` are spelled `1` and `2`
and collide with template argument literals -- the reference says so, and this keeps its
default rather than guessing.
"""

__all__ = [
    "CodeWarriorSymbol",
    "DemangleFailure",
    "demangle_codewarrior",
]

#: The reference has no depth bound; untrusted symbol tables need one.
MAX_DEPTH = 200

#: `e` is the ellipsis, read as a type as the reference does.
FUNDAMENTAL = {
    "i": "int",
    "b": "bool",
    "c": "char",
    "s": "short",
    "l": "long",
    "x": "long long",
    "f": "float",
    "d": "double",
    "w": "wchar_t",
    "v": "void",
    "e": "...",
}

#: Ambiguous with a template argument literal, so read only under `mw_extensions`.
MW_EXTENSIONS = {1: "__int128", 2: "__vec2x32float__"}

#: Follow a leading `__`. `vt` is the vtable, spelt `__vtable` as the reference does.
SPECIAL_FUNCTIONS = {
    "nw": "operator new",
    "nwa": "operator new[]",
    "dl": "operator delete",
    "dla": "operator delete[]",
    "pl": "operator+",
    "mi": "operator-",
    "ml": "operator*",
    "dv": "operator/",
    "md": "operator%",
    "er": "operator^",
    "ad": "operator&",
    "or": "operator|",
    "co": "operator~",
    "nt": "operator!",
    "as": "operator=",
    "lt": "operator<",
    "gt": "operator>",
    "apl": "operator+=",
    "ami": "operator-=",
    "amu": "operator*=",
    "adv": "operator/=",
    "amd": "operator%=",
    "aer": "operator^=",
    "aad": "operator&=",
    "aor": "operator|=",
    "ls": "operator<<",
    "rs": "operator>>",
    "ars": "operator>>=",
    "als": "operator<<=",
    "eq": "operator==",
    "ne": "operator!=",
    "le": "operator<=",
    "ge": "operator>=",
    "aa": "operator&&",
    "oo": "operator||",
    "pp": "operator++",
    "mm": "operator--",
    "cm": "operator,",
    "rm": "operator->*",
    "rf": "operator->",
    "cl": "operator()",
    "vc": "operator[]",
    "vt": "__vtable",
}


class DemangleFailure(Exception):
    """This name is not a CodeWarrior symbol."""


class _Options:
    """The reference's `DemangleOptions`, plus the depth counter the port needs."""

    __slots__ = ("depth", "mw_extensions", "omit_empty_parameters")

    def __init__(self, omit_empty_parameters=True, mw_extensions=False):
        self.omit_empty_parameters = omit_empty_parameters
        self.mw_extensions = mw_extensions
        self.depth = 0


class _Depth:
    """Bounds one production's nesting, so a hostile name gives up rather than recurses."""

    __slots__ = ("options",)

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        self.options.depth += 1
        if self.options.depth > MAX_DEPTH:
            raise DemangleFailure("nested too deeply")
        return self

    def __exit__(self, *exc):
        self.options.depth -= 1
        return False


def _fail(message="not a CodeWarrior symbol"):
    raise DemangleFailure(message)


def parse_qualifiers(text):
    """The `PRCVUS` run in front of a type: `(pre, post, rest)`.

    `pre` is what goes before the type and `post` after it, which is how `PCc` becomes
    `const char*` and `CPc` becomes `char* const`: a `P` seen while `pre` is non-empty
    moves what `pre` holds behind the star.
    """
    pre = ""
    post = ""
    at = 0
    while at < len(text):
        character = text[at]
        if character == "P":
            if not pre:
                post = "*" + post
            else:
                post = f"* {pre.rstrip()}" + post
                pre = ""
        elif character == "R":
            if not pre:
                post = "&" + post
            else:
                post = f"& {pre.rstrip()}" + post
                pre = ""
        elif character == "C":
            pre += "const "
        elif character == "V":
            pre += "volatile "
        elif character == "U":
            pre += "unsigned "
        elif character == "S":
            pre += "signed "
        else:
            break
        at += 1
    return pre, post.rstrip(), text[at:]


def parse_digits(text):
    """A leading run of digits as `(value, rest)`."""
    at = 0
    while at < len(text) and text[at].isdigit():
        at += 1
    if at == 0:
        _fail("expected a count")
    return int(text[:at]), text[at:]


def demangle_template_args(text, options):
    """Split a literal `name<args>` into `(name, "<spelled, args>")`.

    The arguments are written out in the symbol rather than encoded, so the split is on
    the *first* `<` and the *last* `>`, and each argument is then read as a type.
    """
    with _Depth(options):
        start = text.find("<")
        if start < 0:
            return text, ""
        end = text.rfind(">")
        if end < start:
            _fail("unbalanced template argument list")
        args = text[start + 1 : end]
        name = text[:start]
        spelled = "<"
        while args:
            argument, argument_post, rest = demangle_arg(args, options)
            spelled += argument + argument_post
            if not rest:
                break
            spelled += ", "
            args = rest[1:]
        return name, spelled + ">"


def demangle_name(text, options):
    """A length-prefixed name: `(bare name, name with its template arguments, rest)`."""
    size, rest = parse_digits(text)
    if len(rest) < size:
        _fail("name runs past the end of the symbol")
    name, args = demangle_template_args(rest[:size], options)
    return name, f"{name}{args}", rest[size:]


def demangle_qualified_name(text, options):
    """`Q2` and a digit, then that many length-prefixed names: `rstl::basic_string<...>`."""
    if not text.startswith("Q"):
        return demangle_name(text, options)
    if len(text) < 3 or not text[1].isdigit():
        _fail("a qualified name needs a component count")
    count = int(text[1])
    rest = text[2:]
    last_class = ""
    qualified = ""
    for at in range(count):
        class_name, full, rest = demangle_name(rest, options)
        qualified += full
        last_class = class_name
        if at < count - 1:
            qualified += "::"
    return last_class, qualified, rest


def demangle_arg(text, options):
    """One type, as `(pre, post, rest)`.

    `pre` and `post` go either side of whatever the type declares, which is what makes a
    function pointer spellable: `void (*` and `)(const char*, void*)`.
    """
    with _Depth(options):
        return _demangle_arg(text, options)


def _demangle_arg(text, options):
    if text.startswith("-"):
        # A negative template argument literal.
        value, rest = parse_digits(text[1:])
        return f"-{value}", "", rest

    pre, post, text = parse_qualifiers(text)
    result = pre

    if text[:1].isdigit():
        value, rest = parse_digits(text)
        if not rest or rest.startswith(","):
            # Nothing follows, so the digits were a literal rather than a length.
            if options.mw_extensions and value in MW_EXTENSIONS:
                return result + MW_EXTENSIONS[value], post, rest
            return result + str(value) + post, "", rest
        # Otherwise they were the length of a class name.
        _bare, qualified, rest = demangle_name(text, options)
        return result + qualified + post, "", rest

    if text.startswith("Q"):
        _bare, qualified, rest = demangle_qualified_name(text, options)
        return result + qualified + post, "", rest

    is_member = False
    const_member = False
    if text.startswith("M"):
        is_member = True
        _bare, member, rest = demangle_qualified_name(text[1:], options)
        pre = f"{member}::*{pre}"
        if not rest.startswith("F"):
            _fail("a pointer to member must be followed by a function type")
        text = rest

    if is_member or text.startswith("F"):
        text = text[1:]
        if is_member:
            # The member function pointer's hidden parameters; which is written encodes
            # `const`.
            if text.startswith("PCvPCv"):
                const_member = True
                text = text[6:]
            elif text.startswith("PCvPv"):
                text = text[5:]
            else:
                _fail("a pointer to member is missing its hidden parameters")
        elif post.startswith("*"):
            post = post[1:].lstrip()
            pre = f"*{pre}"
        else:
            _fail("a function type must be reached through a pointer")
        args, rest = demangle_function_args(text, options)
        if not rest.startswith("_"):
            _fail("a function type must be followed by its return type")
        return_pre, return_post, rest = demangle_arg(rest[1:], options)
        const = " const" if const_member else ""
        return f"{return_pre} ({pre}{post}", f")({args}){const}{return_post}", rest

    if text.startswith("A"):
        count, rest = parse_digits(text[1:])
        if not rest.startswith("_"):
            _fail("an array bound must be followed by its element type")
        element_pre, element_post, rest = demangle_arg(rest[1:], options)
        if post:
            post = f"({post})"
        return f"{pre}{element_pre}{post}", f"[{count}]{element_post}", rest

    code = text[:1]
    if code in FUNDAMENTAL:
        return result + FUNDAMENTAL[code] + post, "", text[1:]
    if options.mw_extensions and code in ("1", "2"):
        return result + MW_EXTENSIONS[int(code)] + post, "", text[1:]
    if code == "_":
        # A return type separator: the reference stops and leaves the `_` for its caller.
        return result, "", text
    _fail(f"unknown type code {code!r}")
    return None  # pragma: no cover - `_fail` always raises


def demangle_function_args(text, options):
    """The argument list, spelled, and whatever follows it."""
    result = ""
    ellipsis = False
    while text:
        if ellipsis:
            # `...` ends the list: no declaration has a parameter after it.
            _fail("a parameter follows ...")
        if result:
            result += ", "
        argument, argument_post, rest = demangle_arg(text, options)
        result += argument + argument_post
        ellipsis = argument_post == "" and argument.split()[-1:] == ["..."]
        text = rest
        if text.startswith(("_", ",")):
            break
    return result, text


def demangle_special_function(text, class_name, options):
    """A name after a leading `__`: a constructor, a destructor, or an operator."""
    if text.startswith("op"):
        # A conversion operator, whose target type is written straight after `op`.
        argument_pre, argument_post, _rest = demangle_arg(text[2:], options)
        return f"operator {argument_pre}{argument_post}"
    op, args = demangle_template_args(text, options)
    if op == "dt":
        return f"~{class_name}{args}"
    if op == "ct":
        return f"{class_name}{args}"
    spelling = SPECIAL_FUNCTIONS.get(op)
    if spelling is None:
        # Unknown: kept as written, `__` included, as the reference does.
        return f"__{op}{args}"
    return f"{spelling}{args}"


def find_split(text, special, options):
    """Where the function name ends and the signature begins.

    The first `__` at template depth zero -- the brackets have to be counted, because a
    template argument list is written literally and `map<x,y>__3std` has a `__` inside
    nothing. A special name starting `op` skips its conversion type first, since that
    type may itself hold one.
    """
    start = 0
    if special and text.startswith("op"):
        _pre, _post, rest = demangle_arg(text[2:], options)
        start = len(text) - len(rest)
    depth = 0
    for at in range(start, len(text)):
        character = text[at]
        if character == "<":
            depth += 1
        elif character == ">":
            depth -= 1
        elif character == "_" and depth == 0 and text[at + 1 : at + 2] == "_":
            return at
    _fail("no separator between the name and the signature")
    return None  # pragma: no cover - `_fail` always raises


class CodeWarriorSymbol:
    """What one of these names says, spelled and in pieces."""

    __slots__ = (
        "arguments_text",
        "parameters",
        "qualified_name",
        "return_type",
        "static_variable",
        "text",
    )

    def __init__(self, text, qualified_name, parameters, arguments_text, return_type, static_variable):
        self.text = text
        self.qualified_name = qualified_name
        self.parameters = parameters
        #: Exactly what the argument list contributed to `text`, parentheses included.
        self.arguments_text = arguments_text
        self.return_type = return_type
        #: The function-local static this symbol is the storage or the guard for, if any.
        self.static_variable = static_variable

    def __repr__(self):
        return f"CodeWarriorSymbol({self.text!r})"


def demangle_codewarrior(mangled, omit_empty_parameters=True, mw_extensions=False):
    """Read one CodeWarrior symbol. Raises `DemangleFailure` where it is not one."""
    options = _Options(omit_empty_parameters, mw_extensions)
    try:
        return _demangle(mangled, options)
    except RecursionError as error:
        raise DemangleFailure("nested too deeply") from error
    except (IndexError, ValueError) as error:
        # The reference returns None where a slice runs off the end; this says so.
        raise DemangleFailure(str(error) or "malformed name") from error


def _demangle(text, options):
    if not text.isascii():
        _fail("not ASCII")

    static_variable = ""
    return_pre = ""
    return_post = ""
    qualified = ""
    constant = False

    # Wii: `@LOCAL@<function>@<variable>`, guard `@GUARD@...`.
    guard = text.startswith("@GUARD@")
    if guard or text.startswith("@LOCAL@"):
        text = text[7:]
        at = text.rfind("@")
        if at < 0:
            _fail("a local static needs its variable name")
        rest, variable = text[:at], text[at:]
        static_variable = f"{variable[1:]} guard" if guard else variable[1:]
        text = rest

    special = text.startswith("__")
    if special:
        text = text[2:]

    at = find_split(text, special, options)
    # Any further underscores belong to the name rather than the separator.
    while text[at + 2 : at + 3] == "_":
        at += 1
    name, rest = text[:at], text[at:]

    if special:
        if name == "init":
            # `__init__<x>__<class>`: a guard variable; the real separator is further on.
            inner = rest[2:].find("__")
            if inner < 0:
                _fail("a static initialiser needs a second separator")
            name = text[: inner + 6]
            rest = rest[inner + 2 :]
    else:
        if len(name) > 1 and name[0] == "Q" and name[1].isdigit():
            # A qualified name in the name's own seat: `Q23foo3bar__Fv` is
            # `foo::bar()`, not a function called `Q23foo3bar`.
            _bare, name, name_rest = demangle_qualified_name(name, options)
            if name_rest:
                _fail("trailing characters in qualified name")
        else:
            base, args = demangle_template_args(name, options)
            name = f"{base}{args}"

    # GameCube: `<variable>$localstatic<n>$<function>`.
    first = name.find("$")
    if first >= 0:
        second = name[first + 1 :].find("$")
        if second < 0:
            _fail("a local static needs both separators")
        variable, after = name[:first], name[first + 1 :]
        variable_type, after = after[:second], after[second:]
        if not variable_type.startswith("localstatic"):
            _fail("only a local static is written with a `$`")
        # `$localstatic` does not carry the variable's name in a guard or an initialiser.
        static_variable = f"{variable_type} guard" if variable == "init" else variable
        name = after[1:]

    text = rest[2:]

    class_name = ""
    if not text.startswith("F"):
        class_name, qualified, text = demangle_qualified_name(text, options)

    if special:
        name = demangle_special_function(name, class_name, options)

    if text.startswith("C"):
        text = text[1:]
        constant = True

    parameters = None
    arguments_text = ""
    if text.startswith("F"):
        text = text[1:]
        args, text = demangle_function_args(text, options)
        if options.omit_empty_parameters and args == "void":
            arguments_text = "()"
            parameters = ()
        else:
            arguments_text = f"({args})"
            parameters = ("void",) if args == "void" else tuple(_split_arguments(args))
        name += arguments_text

    if text.startswith("_"):
        return_pre, return_post, text = demangle_arg(text[1:], options)

    if text:
        _fail("trailing characters")

    if constant:
        name += " const"
    qualified_name = f"{qualified}::{name}" if qualified else name
    spelled = qualified_name
    return_type = None
    if return_pre:
        return_type = (return_pre + return_post).strip()
        spelled = f"{return_pre} {spelled}{return_post}"
    if static_variable:
        spelled = f"{spelled}::{static_variable}"

    return CodeWarriorSymbol(
        text=spelled,
        qualified_name=qualified_name,
        parameters=parameters,
        arguments_text=arguments_text,
        return_type=return_type,
        static_variable=static_variable or None,
    )


def _split_arguments(spelled):
    """The argument list split at the commas that separate arguments.

    Counted rather than split, because a parameter may be a template or a function type
    with commas of its own. Only for the structured view; the spelling is the
    reference's, built whole.
    """
    depth = 0
    current = ""
    for character in spelled:
        if character in "<([":
            depth += 1
        elif character in ">)]":
            depth -= 1
        if character == "," and depth == 0:
            yield current.strip()
            current = ""
        else:
            current += character
    if current.strip():
        yield current.strip()
