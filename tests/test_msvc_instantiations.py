"""Template instantiations the MSVC reader remembers, and what a replay has to keep.

A template's argument list is its own back-reference scope, so a heavy name spells whole
instantiations out again and again: every argument list `std::basic_string<char, ...>`
appears in writes it in full. `_Demangler.templateInstantiation` therefore remembers each
one it reads, by its name and the text it spans, and replays the spelling when the same
text comes round again. A replay is right only where reading the text again would give
the same answer, and three things decide that which no name in the corpora puts to the
test:

- the `in_pointee_return` it was read under, which a function type among its arguments
  writes into the spelling: `std::function<void (void)>` inside a pointed-to function's
  return type, `std::function<void __cdecl(void)>` anywhere else;
- how deep reading it went, so that a replay further down the name stops at `max_depth`
  exactly where reading it again would;
- whether it holds something that reads deeper than the cursor can see -- a nested
  symbol, which has a cursor of its own, or an operator name, whose dynamic-initializer
  form looks ahead with another -- which makes it `unrepeatable`, read every time.

Every expected value is what the reader gave before it remembered anything (5b0463a), so
each table says what reading every instantiation afresh spells, or which bound it stops
at. Each was checked to fail with its invariant broken: with `in_pointee_return` left out
of the key, with the `max_depth` test left out of a replay, and with `unrepeatable`
ignored.
"""

import pytest

import demangle
from demangle.schemes.msvc._parser import _Bail, _Demangler, _LimitHit

#: (mangled, spelled), each holding one instantiation twice under different
#: `in_pointee_return`.
POINTEE_RETURN_CASES = [
    # Each pair in both orders. Outside any pointed-to function's return type the convention
    # is spelled; inside one, or inside two, it is not.
    (
        "?f@@YAXV?$function@$$A6AXXZ@std@@P6A?AV?$function@$$A6AXXZ@std@@XZ@Z",
        "void __cdecl f(class std::function<void __cdecl(void)>, class std::function<void (void)> (__cdecl *)(void))",
    ),
    (
        "?f@@YAXP6A?AV?$function@$$A6AXXZ@std@@XZV?$function@$$A6AXXZ@std@@@Z",
        "void __cdecl f(class std::function<void (void)> (__cdecl *)(void), class std::function<void __cdecl(void)>)",
    ),
    (
        "?f@@YAXV?$function@$$A6AXXZ@std@@P8C@@EAA?AV?$function@$$A6AXXZ@std@@XZ@Z",
        "void __cdecl f(class std::function<void __cdecl(void)>, class std::function<void (void)> (__cdecl C::*)(void))",
    ),
    (
        "?f@@YAXP8C@@EAA?AV?$function@$$A6AXXZ@std@@XZV?$function@$$A6AXXZ@std@@@Z",
        "void __cdecl f(class std::function<void (void)> (__cdecl C::*)(void), class std::function<void __cdecl(void)>)",
    ),
    (
        "?f@@YAXP6AXV?$function@$$A6AXXZ@std@@@ZP6A?AV?$function@$$A6AXXZ@std@@XZ@Z",
        "void __cdecl f(void (__cdecl *)(class std::function<void __cdecl(void)>), class std::function<void (void)> (__cdecl *)(void))",
    ),
    (
        "?f@@YAXP6A?AV?$function@$$A6AXXZ@std@@XZP6AXV?$function@$$A6AXXZ@std@@@Z@Z",
        "void __cdecl f(class std::function<void (void)> (__cdecl *)(void), void (__cdecl *)(class std::function<void __cdecl(void)>))",
    ),
    (
        "?f@@YAXV?$function@$$A6AXXZ@std@@P6AP6A?AV?$function@$$A6AXXZ@std@@XZXZ@Z",
        "void __cdecl f(class std::function<void __cdecl(void)>, class std::function<void (void)> (__cdecl * (__cdecl *)(void))(void))",
    ),
    (
        "?f@@YAXP6AP6A?AV?$function@$$A6AXXZ@std@@XZXZV?$function@$$A6AXXZ@std@@@Z",
        "void __cdecl f(class std::function<void (void)> (__cdecl * (__cdecl *)(void))(void), class std::function<void __cdecl(void)>)",
    ),
    # One template deeper, so that the outer instantiation's key decides as well.
    (
        "?f@@YAXV?$Y@V?$function@$$A6AXXZ@std@@@@P6A?AV?$Y@V?$function@$$A6AXXZ@std@@@@XZ@Z",
        "void __cdecl f(class Y<class std::function<void __cdecl(void)>>, class Y<class std::function<void (void)>> (__cdecl *)(void))",
    ),
    (
        "?f@@YAXP6A?AV?$Y@V?$function@$$A6AXXZ@std@@@@XZV?$Y@V?$function@$$A6AXXZ@std@@@@@Z",
        "void __cdecl f(class Y<class std::function<void (void)>> (__cdecl *)(void), class Y<class std::function<void __cdecl(void)>>)",
    ),
    (
        "?f@@YAXV?$Y@V?$function@$$A6AXXZ@std@@@@V?$Y@P6A?AV?$function@$$A6AXXZ@std@@XZ@@@Z",
        "void __cdecl f(class Y<class std::function<void __cdecl(void)>>, class Y<class std::function<void (void)> (__cdecl *)(void)>)",
    ),
    (
        "?f@@YAXV?$Y@P6A?AV?$function@$$A6AXXZ@std@@XZ@@V?$Y@V?$function@$$A6AXXZ@std@@@@@Z",
        "void __cdecl f(class Y<class std::function<void (void)> (__cdecl *)(void)>, class Y<class std::function<void __cdecl(void)>>)",
    ),
    (
        "?f@@YAXP6AXV?$function@$$A6AXXZ@std@@@ZV?$Y@P6A?AV?$function@$$A6AXXZ@std@@XZ@@@Z",
        "void __cdecl f(void (__cdecl *)(class std::function<void __cdecl(void)>), class Y<class std::function<void (void)> (__cdecl *)(void)>)",
    ),
    (
        "?f@@YAXV?$Y@P6A?AV?$function@$$A6AXXZ@std@@XZ@@P6AXV?$function@$$A6AXXZ@std@@@Z@Z",
        "void __cdecl f(class Y<class std::function<void (void)> (__cdecl *)(void)>, void (__cdecl *)(class std::function<void __cdecl(void)>))",
    ),
    # Other conventions and other function types: the mark carries whichever was written.
    (
        "?f@@YAXV?$function@$$A6GHH@Z@std@@P6A?AV?$function@$$A6GHH@Z@std@@XZ@Z",
        "void __cdecl f(class std::function<int __stdcall(int)>, class std::function<int (int)> (__cdecl *)(void))",
    ),
    (
        "?f@@YAXP6A?AV?$function@$$A6GHH@Z@std@@XZV?$function@$$A6GHH@Z@std@@@Z",
        "void __cdecl f(class std::function<int (int)> (__cdecl *)(void), class std::function<int __stdcall(int)>)",
    ),
    (
        "?f@@YAXP6AXV?$function@$$A6GHH@Z@std@@@ZP6AP6A?AV?$function@$$A6GHH@Z@std@@XZXZ@Z",
        "void __cdecl f(void (__cdecl *)(class std::function<int __stdcall(int)>), class std::function<int (int)> (__cdecl * (__cdecl *)(void))(void))",
    ),
    (
        "?f@@YAXP6AP6A?AV?$function@$$A6GHH@Z@std@@XZXZP6AXV?$function@$$A6GHH@Z@std@@@Z@Z",
        "void __cdecl f(class std::function<int (int)> (__cdecl * (__cdecl *)(void))(void), void (__cdecl *)(class std::function<int __stdcall(int)>))",
    ),
    (
        "?f@@YAXV?$function@$$A6Q_NXZ@std@@P8C@@EAA?AV?$function@$$A6Q_NXZ@std@@XZ@Z",
        "void __cdecl f(class std::function<bool __vectorcall(void)>, class std::function<bool (void)> (__cdecl C::*)(void))",
    ),
    (
        "?f@@YAXP8C@@EAA?AV?$function@$$A6Q_NXZ@std@@XZV?$function@$$A6Q_NXZ@std@@@Z",
        "void __cdecl f(class std::function<bool (void)> (__cdecl C::*)(void), class std::function<bool __vectorcall(void)>)",
    ),
    (
        "?f@@YAXV?$Y@V?$function@$$A6Q_NXZ@std@@@@P6A?AV?$Y@V?$function@$$A6Q_NXZ@std@@@@XZ@Z",
        "void __cdecl f(class Y<class std::function<bool __vectorcall(void)>>, class Y<class std::function<bool (void)>> (__cdecl *)(void))",
    ),
    (
        "?f@@YAXP6A?AV?$Y@V?$function@$$A6Q_NXZ@std@@@@XZV?$Y@V?$function@$$A6Q_NXZ@std@@@@@Z",
        "void __cdecl f(class Y<class std::function<bool (void)>> (__cdecl *)(void), class Y<class std::function<bool __vectorcall(void)>>)",
    ),
    # As the symbol's own return type, and among a data symbol's template arguments.
    (
        "?f@@YA?AV?$function@$$A6AXXZ@std@@P6A?AV?$function@$$A6AXXZ@std@@XZ@Z",
        "class std::function<void __cdecl(void)> __cdecl f(class std::function<void (void)> (__cdecl *)(void))",
    ),
    (
        "?f@@YAP6A?AV?$function@$$A6AXXZ@std@@XZV?$function@$$A6AXXZ@std@@@Z",
        "class std::function<void (void)> (__cdecl * __cdecl f(class std::function<void __cdecl(void)>))(void)",
    ),
    (
        "?x@@3V?$Z@P6A?AV?$function@$$A6AXXZ@std@@XZV?$function@$$A6AXXZ@std@@@@A",
        "class Z<class std::function<void (void)> (__cdecl *)(void), class std::function<void __cdecl(void)>> x",
    ),
    (
        "?x@@3V?$Z@V?$function@$$A6AXXZ@std@@P6A?AV?$function@$$A6AXXZ@std@@XZ@@A",
        "class Z<class std::function<void __cdecl(void)>, class std::function<void (void)> (__cdecl *)(void)> x",
    ),
    # One and two deep spell alike and differ in what is recorded: the name is recorded
    # once, so `g` takes index 3 -- unless a replay under the wrong depth records a
    # second spelling of it first. And the record reached by `V1@`, read in either order.
    (
        "?f@@YAXP6AP6A?AV?$function@$$A6AXXZ@std@@XZXZP6A?AV?$function@$$A6AXXZ@std@@XZVg@@V3@@Z",
        "void __cdecl f(class std::function<void (void)> (__cdecl * (__cdecl *)(void))(void), class std::function<void (void)> (__cdecl *)(void), class g, class g)",
    ),
    (
        "?f@@YAXP6A?AV?$function@$$A6AXXZ@std@@XZP6AP6A?AV?$function@$$A6AXXZ@std@@XZXZVg@@V3@@Z",
        "void __cdecl f(class std::function<void (void)> (__cdecl *)(void), class std::function<void (void)> (__cdecl * (__cdecl *)(void))(void), class g, class g)",
    ),
    (
        "?f@@YAXP6A?AV?$function@$$A6AXXZ@std@@XZV?$function@$$A6AXXZ@std@@V1@@Z",
        "void __cdecl f(class std::function<void (void)> (__cdecl *)(void), class std::function<void __cdecl(void)>, class function<void __cdecl(void)>)",
    ),
    (
        "?f@@YAXV?$function@$$A6AXXZ@std@@P6A?AV?$function@$$A6AXXZ@std@@XZV1@@Z",
        "void __cdecl f(class std::function<void __cdecl(void)>, class std::function<void (void)> (__cdecl *)(void), class function<void __cdecl(void)>)",
    ),
]

#: mangled -> (the least `max_depth` that reads it, how it reads). Swept against the
#: reader before it remembered anything, at every `max_depth` from 1 to 47: each bound
#: below the first that reads is refused as a `recursion depth` of that bound, and each
#: from it on reads as the default limits do.
DEPTH_BOUNDARIES = {
    # The same instantiation twice, the second under one or three pointers.
    "?f@@YAXV?$A@H@@PAV?$A@H@@@Z": (4, "void __cdecl f(class A<int>, class A<int> *)"),
    "?f@@YAXV?$A@H@@PAPAPAV?$A@H@@@Z": (6, "void __cdecl f(class A<int>, class A<int> ***)"),
    "?f@@YAXV?$A@V?$A@H@@@@PAV?$A@V?$A@H@@@@@Z": (6, "void __cdecl f(class A<class A<int>>, class A<class A<int>> *)"),
    "?f@@YAXV?$A@V?$A@H@@@@PAPAPAV?$A@V?$A@H@@@@@Z": (
        8,
        "void __cdecl f(class A<class A<int>>, class A<class A<int>> ***)",
    ),
    "?f@@YAXV?$A@V?$A@V?$A@V?$A@H@@@@@@@@PAV?$A@V?$A@V?$A@V?$A@H@@@@@@@@@Z": (
        10,
        "void __cdecl f(class A<class A<class A<class A<int>>>>, class A<class A<class A<class A<int>>>> *)",
    ),
    "?f@@YAXV?$A@V?$A@V?$A@V?$A@H@@@@@@@@PAPAPAV?$A@V?$A@V?$A@V?$A@H@@@@@@@@@Z": (
        12,
        "void __cdecl f(class A<class A<class A<class A<int>>>>, class A<class A<class A<class A<int>>>> ***)",
    ),
    # The deeper one first, so that the replay is the shallower; and three times, each deeper.
    "?f@@YAXPAPAPAV?$A@V?$A@H@@@@V?$A@V?$A@H@@@@@Z": (
        8,
        "void __cdecl f(class A<class A<int>> ***, class A<class A<int>>)",
    ),
    "?f@@YAXV?$A@V?$A@H@@@@PAV?$A@V?$A@H@@@@PAPAV?$A@V?$A@H@@@@@Z": (
        7,
        "void __cdecl f(class A<class A<int>>, class A<class A<int>> *, class A<class A<int>> **)",
    ),
    # A replay that is the deepest point of the instantiation around it, which `peak` has to
    # carry out; an argument deeper than the instantiation after it, which `peak` must not
    # drop back from; and an array, whose extents count toward the bound.
    "?f@@YAXV?$B@V?$A@H@@PAV?$A@H@@@@PAPAV?$B@V?$A@H@@PAV?$A@H@@@@@Z": (
        8,
        "void __cdecl f(class B<class A<int>, class A<int> *>, class B<class A<int>, class A<int> *> **)",
    ),
    "?f@@YAXV?$B@PAPAPAHV?$A@H@@@@PAPAV?$B@PAPAPAHV?$A@H@@@@@Z": (
        8,
        "void __cdecl f(class B<int ***, class A<int>>, class B<int ***, class A<int>> **)",
    ),
    "?f@@YAXV?$C@$$BY30000H@@PAPAV?$C@$$BY30000H@@@Z": (
        10,
        "void __cdecl f(class C<int[1][1][1][1]>, class C<int[1][1][1][1]> **)",
    ),
    # Among a template's arguments, and two nests deep enough to move the boundary.
    "?x@@3V?$Z@V?$A@V?$A@V?$A@H@@@@@@PAPAV?$A@V?$A@V?$A@H@@@@@@@@A": (
        11,
        "class Z<class A<class A<class A<int>>>, class A<class A<class A<int>>> **> x",
    ),
    "?f@@YAXV?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@H@@@@@@@@@@@@@@@@PAPAV?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@H@@@@@@@@@@@@@@@@@Z": (
        19,
        "void __cdecl f(class A<class A<class A<class A<class A<class A<class A<class A<int>>>>>>>>, class A<class A<class A<class A<class A<class A<class A<class A<int>>>>>>>> **)",
    ),
    "?f@@YAXV?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@H@@@@@@@@@@@@@@@@@@@@@@@@PAPAPAV?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@H@@@@@@@@@@@@@@@@@@@@@@@@@Z": (
        28,
        "void __cdecl f(class A<class A<class A<class A<class A<class A<class A<class A<class A<class A<class A<class A<int>>>>>>>>>>>>, class A<class A<class A<class A<class A<class A<class A<class A<class A<class A<class A<class A<int>>>>>>>>>>>> ***)",
    ),
}

#: As `DEPTH_BOUNDARIES`, for instantiations that hold a nested symbol or an operator
#: name, each written twice.
UNREPEATABLE_BOUNDARIES = {
    # Operator names, each read afresh, although only a dynamic initializer's -- which looks
    # ahead with a cursor of its own -- reaches further than this cursor sees: a templated
    # operator, a pointer to member of one, a templated constructor, `<=>`, a literal
    # operator, and dynamic initializers and atexit destructors.
    "?f@@YAXV?$A@$$Y?$?HH@@@@V?$A@$$Y?$?HH@@@@@Z": (
        5,
        "void __cdecl f(class A<operator+<int>>, class A<operator+<int>>)",
    ),
    "?f@@YAXV?$A@$$Y?$?HH@@@@PAPAV?$A@$$Y?$?HH@@@@@Z": (
        7,
        "void __cdecl f(class A<operator+<int>>, class A<operator+<int>> **)",
    ),
    "?f@@YAXV?$A@PQ?$?HH@@H@@PAPAV?$A@PQ?$?HH@@H@@@Z": (
        7,
        "void __cdecl f(class A<int operator+<int>::*>, class A<int operator+<int>::*> **)",
    ),
    "?f@@YAXV?$A@$$Y?$?0H@S@@@@PAPAV?$A@$$Y?$?0H@S@@@@@Z": (
        7,
        "void __cdecl f(class A<S::S<int>>, class A<S::S<int>> **)",
    ),
    "?f@@YAXV?$A@$$Y?$?__MH@@@@PAPAV?$A@$$Y?$?__MH@@@@@Z": (
        7,
        "void __cdecl f(class A<operator<=><int>>, class A<operator<=><int>> **)",
    ),
    "?f@@YAXV?$A@$$Y?$?__KabcH@@@@@PAPAV?$A@$$Y?$?__KabcH@@@@@@Z": (
        6,
        'void __cdecl f(class A<operator ""abcH<>>, class A<operator ""abcH<>> **)',
    ),
    "?f@@YAXV?$A@$$Y?$?__Ex@@@@@V?$A@$$Y?$?__Ex@@@@@@Z": (
        5,
        "void __cdecl f(class A<`dynamic initializer for 'x''<>>, class A<`dynamic initializer for 'x''<>>)",
    ),
    "?f@@YAXV?$A@$$Y?$?__Ex@@@@@PAPAV?$A@$$Y?$?__Ex@@@@@@Z": (
        7,
        "void __cdecl f(class A<`dynamic initializer for 'x''<>>, class A<`dynamic initializer for 'x''<>> **)",
    ),
    "?f@@YAXV?$A@$$Y?$?__Ex@y@@@@@PAPAV?$A@$$Y?$?__Ex@y@@@@@@Z": (
        7,
        "void __cdecl f(class A<`dynamic initializer for 'y::x''<>>, class A<`dynamic initializer for 'y::x''<>> **)",
    ),
    "?f@@YAXV?$A@$$Y?$?__Fx@@@@@PAPAV?$A@$$Y?$?__Fx@@@@@@Z": (
        7,
        "void __cdecl f(class A<`dynamic atexit destructor for 'x''<>>, class A<`dynamic atexit destructor for 'x''<>> **)",
    ),
    # Nested symbols, read by a cursor of their own: an address, a symbol as it is, one whose
    # template name is recorded and reached, a pointer to member function, an operator
    # member, and symbols whose own types go deeper.
    "?f@@YAXV?$A@$1?x@@3HA@@V?$A@$1?x@@3HA@@@Z": (4, "void __cdecl f(class A<&int x>, class A<&int x>)"),
    "?f@@YAXV?$A@$1?x@@3HA@@PAPAV?$A@$1?x@@3HA@@@Z": (6, "void __cdecl f(class A<&int x>, class A<&int x> **)"),
    "?f@@YAXV?$A@$E?x@@3HA@@PAPAV?$A@$E?x@@3HA@@@Z": (6, "void __cdecl f(class A<int x>, class A<int x> **)"),
    "?f@@YAXV?$A@$1??$x@H@@3HA$1?1@3HA@@V?$A@$1??$x@H@@3HA$1?1@3HA@@@Z": (
        5,
        "void __cdecl f(class A<&int x<int>, &int x<int>>, class A<&int x<int>, &int x<int>>)",
    ),
    "?f@@YAXV?$A@$1??$x@H@@3HA$1?1@3HA@@PAPAV?$A@$1??$x@H@@3HA$1?1@3HA@@@Z": (
        7,
        "void __cdecl f(class A<&int x<int>, &int x<int>>, class A<&int x<int>, &int x<int>> **)",
    ),
    "?f@@YAXV?$A@$H?g@S@@QAEXXZA@@@PAPAV?$A@$H?g@S@@QAEXXZA@@@@Z": (
        6,
        "void __cdecl f(class A<{public: void __thiscall S::g(void), 0}>, class A<{public: void __thiscall S::g(void), 0}> **)",
    ),
    "?f@@YAXV?$A@$1??Hfoo@@QAEHH@Z@@PAPAV?$A@$1??Hfoo@@QAEHH@Z@@@Z": (
        6,
        "void __cdecl f(class A<&public: int __thiscall foo::operator+(int)>, class A<&public: int __thiscall foo::operator+(int)> **)",
    ),
    "?f@@YAXV?$A@$1?x@@3PAPAPAHA@@PAPAV?$A@$1?x@@3PAPAPAHA@@@Z": (
        9,
        "void __cdecl f(class A<&int ***x>, class A<&int ***x> **)",
    ),
    "?f@@YAXV?$A@$1?x@@3V?$A@H@@A@@PAPAV?$A@$1?x@@3V?$A@H@@A@@@Z": (
        8,
        "void __cdecl f(class A<&class A<int> x>, class A<&class A<int> x> **)",
    ),
    "?f@@YAXV?$B@V?$A@H@@$1?x@@3V?$A@H@@AV?$A@H@@@@PAPAV?$B@V?$A@H@@$1?x@@3V?$A@H@@AV?$A@H@@@@@Z": (
        8,
        "void __cdecl f(class B<class A<int>, &class A<int> x, class A<int>>, class B<class A<int>, &class A<int> x, class A<int>> **)",
    ),
}


def read(mangled, max_depth=None):
    """What `demangle_strict` makes of `mangled`, or the bound that stopped it."""
    limits = demangle.DEFAULT_LIMITS if max_depth is None else demangle.Limits(max_depth=max_depth)
    try:
        return demangle.demangle_strict(mangled, language="msvc", limits=limits)
    except demangle.LimitExceeded as error:
        return error.limit_name, error.limit_value


def assert_reads_from(mangled, first, spelled):
    """Refused naming each bound below `first`, and read as `spelled` from it on and under
    the default limits. Every bound from 1 is tried, since a replay accepted too deep reads
    a name below `first`, and two past it, to see the reading hold."""
    for max_depth in range(1, first + 3):
        expected = spelled if max_depth >= first else ("recursion depth", max_depth)
        assert read(mangled, max_depth) == expected, f"max_depth={max_depth}"
    assert read(mangled) == spelled


class TestAReplayKeepsThePointeeReturnItWasReadUnder:
    """The calling-convention mark a function type writes among a template's arguments
    carries how many pointed-to return types it stood inside, and the spelling the
    cursor remembers carries the mark. Replayed under a different `in_pointee_return`,
    `std::function<void __cdecl(void)>` would turn up inside a callback's return type,
    or `std::function<void (void)>` outside one."""

    @pytest.mark.parametrize(("mangled", "spelled"), POINTEE_RETURN_CASES)
    def test_each_occurrence_is_spelled_for_where_it_stands(self, mangled, spelled):
        assert demangle.demangle_strict(mangled, language="msvc") == spelled


class TestAReplayStopsWhereReadingAgainWould:
    """A replay is refused where `depth` plus how far below its own level the
    instantiation reached passes `max_depth`, and it raises `peak` as reading it would,
    so that the instantiation around it records how deep it went in turn."""

    @pytest.mark.parametrize(("mangled", "first", "spelled"), [(m, *row) for m, row in DEPTH_BOUNDARIES.items()])
    def test_the_bound_falls_at_the_same_depth(self, mangled, first, spelled):
        assert_reads_from(mangled, first, spelled)


class TestWhatTheCursorCannotSeeIntoIsReadEveryTime:
    """A nested symbol's cursor and a dynamic initializer's probe go below any level this
    cursor's `peak` records, so a replay of what holds one could pass `max_depth` where
    reading it again stops. Both are counted in `unrepeatable`, and an instantiation that
    moved the count is never remembered. Nothing else about a replay of one differs in
    these names, so it is the bound that shows it."""

    @pytest.mark.parametrize(("mangled", "first", "spelled"), [(m, *row) for m, row in UNREPEATABLE_BOUNDARIES.items()])
    def test_the_bound_falls_at_the_same_depth(self, mangled, first, spelled):
        assert_reads_from(mangled, first, spelled)


class TestAReadThatFailsLeavesTheCursorAsOneThatCompletes:
    """`simple` is None while an instantiation is being read, until something in it is
    written, and `peak` counts from its own level. Every failure ends the whole parse
    today, but one that a caller caught must not leave either: `simple` goes back to what
    it was if nothing inside wrote it, `peak` never below what the enclosing read reached,
    and `depth` back to where it was, the level that passed the bound included."""

    def test_a_refusal_restores_simple_and_keeps_peak(self):
        # `A<int` with no closing "@": nothing inside writes `simple`
        cursor = _Demangler("A@H")
        cursor.peak = 5
        with pytest.raises(_Bail):
            cursor.templateInstantiation()
        assert cursor.simple is True
        assert cursor.peak == 5

    def test_a_refusal_keeps_what_was_written_and_reached(self):
        cursor = _Demangler("A@PAPAPAH")
        cursor.peak = 2
        with pytest.raises(_Bail):
            cursor.templateInstantiation()
        assert cursor.simple is False
        assert cursor.peak == 4

    def test_a_bound_restores_simple_and_depth_and_keeps_peak(self):
        cursor = _Demangler("A@PAPAPAPAH@", demangle.Limits(max_depth=3))
        with pytest.raises(_LimitHit):
            cursor.templateInstantiation()
        assert cursor.simple is True
        assert cursor.peak == 3
        assert cursor.depth == 0
