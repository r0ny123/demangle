import unittest
from pathlib import Path

import demangle
from demangle.schemes.msvc._parser import demangle_msvc_symbol, parse_msvc_symbol, parse_msvc_type
from demangle.schemes.msvc.nodes import render

# Expected spellings are llvm-undname's; names are from LLVM's test corpus unless marked.
DEMANGLED = [
    ("?foo@@YAXI@Z", "void __cdecl foo(unsigned int)"),
    ("?foo@@YAXN@Z", "void __cdecl foo(double)"),
    ("?foo_pad@@YAXPAD@Z", "void __cdecl foo_pad(char *)"),
    ("?foo_pbd@@YAXPBD@Z", "void __cdecl foo_pbd(char const *)"),
    ("?foo_qad@@YAXQAD@Z", "void __cdecl foo_qad(char *const)"),
    ("?foo_papad@@YAXPAPAD@Z", "void __cdecl foo_papad(char **)"),
    ("?foo_pbqad@@YAXPBQAD@Z", "void __cdecl foo_pbqad(char *const *)"),
    ("?foo_aad@@YAXAAD@Z", "void __cdecl foo_aad(char &)"),
    ("?foo_aay144h@@YAXAAY144H@Z", "void __cdecl foo_aay144h(int (&)[5][5])"),
    ("?foo_aay144cbh@@YAXAAY144$$CBH@Z", "void __cdecl foo_aay144cbh(int const (&)[5][5])"),
    ("?foo_piad@@YAXPIAD@Z", "void __cdecl foo_piad(char *__restrict)"),
    ("?foo_p6ahxz@@YAXP6AHXZ@Z", "void __cdecl foo_p6ahxz(int (__cdecl *)(void))"),
    ("??0foo@@QAE@XZ", "public: __thiscall foo::foo(void)"),
    ("??1foo@@QAE@XZ", "public: __thiscall foo::~foo(void)"),
    ("??Hfoo@@QAEHH@Z", "public: int __thiscall foo::operator+(int)"),
    ("??_V@YAXPAX@Z", "void __cdecl operator delete[](void *)"),
    ("?static_method@foo@@SAPAV1@XZ", "public: static class foo * __cdecl foo::static_method(void)"),
    ("?d@foo@@0FB", "private: static short const foo::d"),
    ("?e@foo@@1JC", "protected: static long volatile foo::e"),
    ("?Char16Var@@3_SA", "char16_t Char16Var"),
    ("?h2@@3QBHB", "int const *const h2"),
    ("?mbb@S@@QAEX_N0@Z", "public: void __thiscall S::mbb(bool, bool)"),
    ("?f@@YAXHZZ", "void __cdecl f(int, ...)"),
    # `void f(...)`: a `Z` with nothing in front of it is the whole list.
    ("?f@@YAXZZ", "void __cdecl f(...)"),
    ("?g@@YAHZZ", "int __cdecl g(...)"),
    ("??0P@ns@@QEAA@ZZ", "public: __cdecl ns::P::P(...)"),
    ("??BQ@ns@@QEBAHZZ", "public: int __cdecl ns::Q::operator int(...) const"),
    # the declarator cases: a name or a further pointer belongs inside its own type
    ("?j@@3P6GHCE@ZA", "int (__stdcall *j)(signed char, unsigned char)"),
    ("?g@@3PAP6AHXZA", "int (__cdecl **g)(void)"),
    ("?f@@YAPAY01HXZ", "int (* __cdecl f(void))[2]"),
    ("?f@@YAAAY01HXZ", "int (& __cdecl f(void))[2]"),
    ("?ret_fnptrarray@@YAP6AXQAH@ZXZ", "void (__cdecl * __cdecl ret_fnptrarray(void))(int *const)"),
    ("?color3@@3QAY02$$CBNA", "double const (*const color3)[3]"),
    ("?f@@YAXY01H@Z", "void __cdecl f(int[2])"),
    ("?b11@@YAPAPBDXZ", "char const ** __cdecl b11(void)"),
    ("?foo_abc@@YAXV?$A@DV?$B@D@@V?$C@D@@@@@Z", "void __cdecl foo_abc(class A<char, class B<char>, class C<char>>)"),
    # a return type, alone among positions, carries a qualifier of its own, at all three
    # sites a return type is parsed
    ("?f@@YA?AUMatrix@@XZ", "struct Matrix __cdecl f(void)"),
    ("?f@@YA?BUMatrix@@XZ", "struct Matrix const __cdecl f(void)"),
    ("?f@@YAXP6A?AUMatrix@@XZ@Z", "void __cdecl f(struct Matrix (__cdecl *)(void))"),
    ("?f@@YAX$$A6A?AUMatrix@@XZ@Z", "void __cdecl f(struct Matrix __cdecl(void))"),
    ("?g@@3P6A?AUMatrix@@XZA", "struct Matrix (__cdecl *g)(void)"),
    # clang's Microsoft mangler emits _L/_M for __int128, but llvm-undname cannot read them
    # back, so these two are spelled from the mangler's table rather than the reference's
    ("?f@@YAX_L@Z", "void __cdecl f(__int128)"),
    ("?f@@YAX_M@Z", "void __cdecl f(unsigned __int128)"),
    # read out of real PDBs rather than the LLVM corpus
    ("??_7type_info@@6B@", "const type_info::`vftable'"),
    (
        "?__crt_rotate_pointer_value@@YAIIH@Z",
        "unsigned int __cdecl __crt_rotate_pointer_value(unsigned int, int)",
    ),
]

# Measured against llvm-undname 22.1.7 on the shipped corpus.
UNIQUE_CORPUS_NAMES = 609
CORPUS_NAMES_UNDERSTOOD = 609

# Forms this demangler does not model. Each must come back exactly as it went in: a wrong
# expansion is worse than a decorated name, because it matches neither spelling.
DECLINED = [
    "?x@@3PAY02Hz",  # truncated
    "?",
    "??",
    "?@@YAXXZ",  # empty name fragment
    "?a@1@@YAXXZ",  # name back-reference past the end of the table
    "??0@@QAE@XZ",  # constructor with no class to name it after
    "?f@@YAX_Y@Z",  # unknown extended basic type
    "?f@@YAX$$CZ@Z",  # $$C without a qualifier
    # an unknown calling-convention byte: the reference spells it as nothing, but a
    # convention silently dropped from a signature is not worth reporting
    "??_EDerived@@$4PPPPPPPM@A@EA$PEAXI@Z",
    "?f@A@simple@@$R477PPPPPPPM@7A$XXZ",
    "?f@@YAX$$A6ZXZ@Z",  # function type argument with an unknown calling convention
    "??_7type_info@@6Z@",  # vftable with an unknown qualifier
    "??_7type_info@@6B@X",  # vftable with trailing bytes
    "?foo@@YAXI@ZX",  # function with trailing bytes
    "?g@@YAXPAUS@@PA1@Z",  # argument back-reference past the end of the table
    "?g@@YAX0@Z",  # argument back-reference with nothing recorded yet
    "?f@@YAXPAHPB0@Z",  # a qualifier in front of a back-reference, which MSVC does not form
    "?f@@YAX_",  # truncated extended type
    # a parameter list is closed by the throw specification, so a name that stops before it
    # is truncated however plausible the prefix looks
    "?f@@YAXHZ",  # variadic marker, then nothing where the throw specification belongs
    "?a2@@YAHX",  # void parameter list with no terminator at all
    "?b7@@YANAAMXZ",  # parameters, then a variadic marker standing in for the terminator
    # __int8/__int16/__int32 are spelled with the plain char/short/int codes, so no mangler
    # emits these and a name carrying one is not MSVC-decorated
    "?f@@YAX_H@Z",
    "?f@@YAX_D@Z",
    "?f@@YA?ZUMatrix@@XZ",  # return type carrying a qualifier that is not one
    "??0?$5Class@QAH@@QAE@XZ",  # template name starting with a digit
    "?e@FTypeWithQuals@@3U?K@A",  # tag type named by an operator rather than an identifier
    # a special name takes a signature or a storage class by which code it is, never both
    "??_7A@B@ad@@YAXPEBQEAD@Z",  # vftable given a function signature
    "??7Base@@6B@",  # operator! given the vftable storage class
    "?foo_pbqbd@@YAXPEBBBD@Z",  # reference under an enclosing qualifier, which C++ has no form for
    "??_?@@YAXXZ",  # unknown extended operator
    # the declarator placeholder is a NUL; an identifier carrying one would otherwise be
    # mistaken for the slot a pointer writes itself into, yielding "class a(*)b"
    "?f@@YAXPAVa\x00b@@@Z",
    "?a\x00b@@YAXPAY01D@Z",
]


# Back-reference behaviour, each pair checked against llvm-undname. A name is recorded for
# later reference only when it is not already held and while the table is under ten entries,
# and a template instantiation is read in its own scope.
BACKREFS = [
    # the table holds the function's own name first, so "1" is the first type named after it
    ("?f@@YAXVA@@V1@@Z", "void __cdecl f(class A, class A)"),
    # a repeat is not recorded again: the table is f, A, B, so "1" is still A
    ("?f@@YAXVA@@VB@@VA@@V1@@Z", "void __cdecl f(class A, class B, class A, class A)"),
    # the tenth entry is the last one recorded, so "9" is I and J never enters the table
    (
        "?f@@YAXVA@@VB@@VC@@VD@@VE@@VF@@VG@@VH@@VI@@VJ@@V9@@Z",
        "void __cdecl f(class A, class B, class C, class D, class E, class F, class G, "
        "class H, class I, class J, class I)",
    ),
    # a template opens its own scope, taking index 0 itself, so its first argument is 1
    (
        "?foo_abbb@@YAXV?$A@V?$B@D@@V1@V1@@@@Z",
        "void __cdecl foo_abbb(class A<class B<char>, class B<char>, class B<char>>)",
    ),
    # the rendered template belongs to the enclosing scope: 1 is B<char> and 2 is N
    ("?b_foo@@YA?AV?$B@D@N@@V12@@Z", "class N::B<char> __cdecl b_foo(class N::B<char>)"),
    (
        "?abc_foo@@YA?AV?$A@DV?$B@D@N@@V?$C@D@2@@N@@XZ",
        "class N::A<char, class N::B<char>, class N::C<char>> __cdecl abc_foo(void)",
    ),
    # the symbol's own template name is the exception: it is not recorded, so 0 is N
    ("??$f@H@N@@YAXV0@@Z", "void __cdecl N::f<int>(class N)"),
]

# Shapes the grammar does not allow, each confirmed refused by llvm-undname.
BACKREF_DECLINED = [
    "?f@@YAXVA@@VB@@VA@@V3@@Z",  # A is recorded once, so there is no fourth name
    "??$f@H@N@@YAXV1@@Z",  # only N is recorded, and the symbol's own template name is not
    "?f2@@YAXBDPAD@Z",  # "B" would introduce a volatile reference, which C++ cannot write
    "?foo_qay04h@@YAXBEAY04H@Z",
    "?h@@YAXPAHPA0@Z",  # an argument back-reference is a whole argument, never a pointee
    "?h@@YAXPAHAA0@Z",
    "?g@@YAXUS@@PA0@Z",  # the reference refuses this too, whatever the back-reference names
    # declined rather than refused: the reference spells this "int &const *", a qualifier on
    # a reference that C++ has no form for, so the decorated name stays the better answer
    "?f@@YAXPBAAH@Z",
]


# Rules derived from llvm-undname probes, each pinned with the name that proved it.
GRAMMAR_RULES = [
    # the "6" storage form belongs to the vftable family only
    ("??_7x@@6B@", "const x::`vftable'"),
    ("??_8x@@6B@", "const x::`vbtable'"),
    ("??_Sx@@6B@", "const x::`local vftable'"),
    # a data symbol's trailing qualifier belongs to what its outermost pointer points at
    ("?s@@3PADB", "char const *s"),
    ("?s@@3PADD", "char const volatile *s"),
    ("?s@@3QBDD", "char const volatile *const s"),
    ("?s@@3PAPADB", "char *const *s"),
    ("?s@@3HB", "int const s"),
    # a sigil abuts a type that ends in neither an alphanumeric character nor ">"
    ("?f@@YAXPAUS@@@Z", "void __cdecl f(struct S *)"),
    ("?f@@YAXPAUS_@@@Z", "void __cdecl f(struct S_*)"),
    ("?f@@YAXPAUS$@@@Z", "void __cdecl f(struct S$*)"),
    ("?f@@YAXPAV?$B@VA@@@@@Z", "void __cdecl f(class B<class A> *)"),
    # ... while a named declarator is spaced off whatever precedes it
    ("?fooE@@YA?AW4E$@@XZ", "enum E$ __cdecl fooE(void)"),
    # "$$C" qualifies an array element or a template argument
    ("?f@@YAXAAY144$$CBH@Z", "void __cdecl f(int const (&)[5][5])"),
    ("?f@@YAXV?$T@$$CBH@@@Z", "void __cdecl f(class T<int const>)"),
    # a function pointer takes no __ptr64 modifier, but is otherwise read
    ("?p@@3R6AHHH@ZA", "int (__cdecl *volatile p)(int, int)"),
    ("?p@@3Q6AHHH@ZA", "int (__cdecl *const p)(int, int)"),
]

# ... and the shapes those same rules refuse, each confirmed refused by llvm-undname
GRAMMAR_DECLINED = [
    "??_9x@@6B@",  # vcall, typeof and the local static guard take a storage class this
    "??_Ax@@6B@",  # parser does not model, never the vftable family's "6" form
    "??_Bx@@6B@",
    "?p@@3PE6AHHH@ZA",  # __ptr64 is not written in front of a function type
    "?p@@3RE6AHHH@ZA",
    "?f@@YAX$$CBH@Z",  # "$$C" is not a parameter of its own, nor a pointee
    "?f@@YAXPA$$CBH@Z",
    "?f@@YAXAA$$CBH@Z",
    "?f@@YAXAAY144$$CZH@Z",  # ... and in those positions it still needs a real qualifier
]


ANONYMOUS_NAMESPACE = [
    ("?x@?A0x12345678@@3HA", "int `anonymous namespace'::x"),
    ("?x@?A@@3HA", "int `anonymous namespace'::x"),
    ("?x@?A0xABCDEF12@N@@3HA", "int N::`anonymous namespace'::x"),
    ("?f@?A0x1@@YAXXZ", "void __cdecl `anonymous namespace'::f(void)"),
    # the discriminator, not the spelling, is what a later back-reference resolves to
    ("?f@?A0x1@@YAXV1@@Z", "void __cdecl `anonymous namespace'::f(class 0x1)"),
    ("?f@?A0x1@N@@YAXV2@@Z", "void __cdecl N::`anonymous namespace'::f(class N)"),
    # a leading "??A" is operator[], which a namespace fragment must not claim
    ("??AFoo@@QAGXXZ", "public: void __stdcall Foo::operator[](void)"),
    # the discriminator is whatever stands before the "@", not only "0x" and hex digits
    ("?x@?A0@@3HA", "int `anonymous namespace'::x"),
    ("?x@?A0x@@3HA", "int `anonymous namespace'::x"),
    ("?x@?A0xZZ@@3HA", "int `anonymous namespace'::x"),
    ("?x@?ABANANA@@3HA", "int `anonymous namespace'::x"),
    # ... and it is that discriminator a back-reference resolves to, whatever it spells
    ("?f@?ABANANA@@YAXV1@@Z", "void __cdecl `anonymous namespace'::f(class BANANA)"),
    # only where a scope is being named, though: the innermost name of a *type* is never
    # a namespace, so this one is the class "?A0x1"
    ("?f@@YAXPAU?A0x1@@@Z", "void __cdecl f(struct ?A0x1 *)"),
]


class MsvcAnonymousNamespaceTestSuite(unittest.TestCase):
    def test_an_unnamed_namespace_is_spelled_and_recorded_the_way_it_is_mangled(self):
        for mangled, expected in ANONYMOUS_NAMESPACE:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_a_discriminator_that_runs_to_the_end_is_refused(self):
        # it is delimited by an "@", and a name without one is not a name
        self.assertEqual(demangle_msvc_symbol("?x@?A0x1"), "?x@?A0x1")


# A name that opens with "?" where no code claims it. The reference reads three name
# positions with three functions -- `demangleUnqualifiedSymbolName`,
# `demangleUnqualifiedTypeName` and `demangleNameScopePiece` -- and each claims a
# different set: a template anywhere, an operator only where a symbol names itself, a
# namespace or a scope number only where a scope is being named. Everything else reaches
# the same `demangleSimpleName` a plain name does, and keeps its "?". Every spelling below
# was put to llvm-undname 18.1.3.
QUESTION_NAMES = [
    # the innermost name of a type is not an operator: "?B" here is a class, not
    # operator-conversion
    ("?f@@YAXPAU?B@A@@@Z", "void __cdecl f(struct A::?B *)"),
    ("?f@@YAXPAU?DEcoder@N@@@Z", "void __cdecl f(struct N::?DEcoder *)"),
    # ... including through a pointer to member
    ("?f@@YAXP8?D@N@@AEXXZ@Z", "void __cdecl f(void (__thiscall N::?D::*)(void))"),
    # it is recorded for back-references like any other name: "?B" and "A" take 1 and 2
    ("?f@@YAXPAU?B@A@@PAU01@@Z", "void __cdecl f(struct A::?B *, struct ?B::f *)"),
    ("?f@@YAXPAUX@?B@A@@PAU2@@Z", "void __cdecl f(struct A::?B::X *, struct ?B *)"),
    # a scope number is a number and then the symbol it belongs to. "?0B@" has the digit
    # but no symbol after it, so it is not one, and what is left is a name
    ("?f@@YAXPAU?0B@@@Z", "void __cdecl f(struct ?0B *)"),
    ("?x@?Q@@3HA", "int ?Q::x"),
    # empty after the "?" is a name too: the reference stops at the first "@" that is not
    # the first character, and the "?" is that character
    ("?f@@YAXPAU?@N@@@Z", "void __cdecl f(struct N::?*)"),
    # and the codes that *are* claimed still are, in the positions that claim them
    ("??AFoo@@QAGXXZ", "public: void __stdcall Foo::operator[](void)"),
    ("?x@?A0x1@@3HA", "int `anonymous namespace'::x"),
    ("?x@?@??f@@YAXXZ@4HA", "int `void __cdecl f(void)'::`0'::x"),
    ("?x@?0??f@@YAXXZ@4HA", "int `void __cdecl f(void)'::`1'::x"),
    ("?x@?BB@??f@@YAXXZ@4HA", "int `void __cdecl f(void)'::`17'::x"),
    ("??$f@H@@YAXXZ", "void __cdecl f<int>(void)"),
]


class MsvcQuestionNameTestSuite(unittest.TestCase):
    def test_a_name_no_code_claims_keeps_its_question_mark(self):
        for mangled, expected in QUESTION_NAMES:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_a_name_that_never_reaches_an_at_sign_is_refused(self):
        for mangled in ("?f@@YAXPAU?B", "?f@@YAXPAUX@?B"):
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)


class MsvcTypeOnlyTestSuite(unittest.TestCase):
    """A bare type encoding has no symbol in it, so it makes none of a symbol's allowances."""

    def test_a_template_standing_as_the_type_is_recorded_for_back_references(self):
        # the one name a symbol does not record is its own, and there is no symbol here
        tree = parse_msvc_type("P6AXV?$A@H@@V0@@Z")
        self.assertIsNotNone(tree)
        self.assertEqual(render(tree), "void (__cdecl *)(class A<int>, class A<int>)")
        self.assertEqual(
            demangle_msvc_symbol("??_R0P6AXV?$A@H@@V0@@Z@8"),
            "void (__cdecl *`RTTI Type Descriptor')(class A<int>, class A<int>)",
        )

    def test_the_class_a_type_names_is_never_an_operator(self):
        tree = parse_msvc_type("PAU?B@A@@")
        self.assertIsNotNone(tree)
        self.assertEqual(render(tree), "struct A::?B *")


MEMBER_POINTERS_AND_INTEGERS = [
    ("?f@@YAXP8S@@AEXXZ@Z", "void __cdecl f(void (__thiscall S::*)(void))"),
    ("?f@@YAXP8S@@BEXXZ@Z", "void __cdecl f(void (__thiscall S::*)(void) const)"),
    ("?f@@YAXP8S@@DEXXZ@Z", "void __cdecl f(void (__thiscall S::*)(void) const volatile)"),
    ("?f@@YAXP8N@S@@AEXXZ@Z", "void __cdecl f(void (__thiscall S::N::*)(void))"),
    ("?f@@YAXQ8S@@AEXXZ@Z", "void __cdecl f(void (__thiscall S::*const)(void))"),
    ("?f@@YAXP8S@@AAHH@Z@Z", "void __cdecl f(int (__cdecl S::*)(int))"),
    ("?f@@YAXP8?$T@$01@@AEXXZ@Z", "void __cdecl f(void (__thiscall T<2>::*)(void))"),
    # a single digit is itself plus one; anything larger is nibbles "A" to "P" ended by "@"
    ("??$f@$00@@YAXXZ", "void __cdecl f<1>(void)"),
    ("??$f@$0A@@@YAXXZ", "void __cdecl f<0>(void)"),
    ("??$f@$0M@@@YAXXZ", "void __cdecl f<12>(void)"),
    ("??$f@$0BAA@@@YAXXZ", "void __cdecl f<256>(void)"),
    ("??$f@$0?0@@YAXXZ", "void __cdecl f<-1>(void)"),
    # the accumulator is 64 bits and wraps, and a magnitude that wraps to zero keeps its sign
    (
        "??0?$LongLongTemplate@$0HPPPPPPPPPPPPPPPPPP@@@QAE@XZ",
        "public: __thiscall LongLongTemplate<18446744073709551615>::LongLongTemplate<18446744073709551615>(void)",
    ),
    (
        "??0?$LongLongTemplate@$0?IAAAAAAAAAAAAAAAAAA@@@QEAA@XZ",
        "public: __cdecl LongLongTemplate<-0>::LongLongTemplate<-0>(void)",
    ),
]


LOCAL_SCOPES = [
    ("?x@?0??f@@YAXXZ@4HA", "int `void __cdecl f(void)'::`1'::x"),
    ("?x@?1??f@@YAXXZ@4HA", "int `void __cdecl f(void)'::`2'::x"),
    ("?x@?2??f@@YAXXZ@4HA", "int `void __cdecl f(void)'::`3'::x"),
    # the enclosing name continues this name's back-reference table rather than opening its
    # own, so the "0" below is the outer N and not the enclosing symbol's own first fragment
    ("?N@?1??SN@?$NS@H@0@QEAAHXZ@4HA", "int `public: int __cdecl N::NS<int>::SN(void)'::`2'::N"),
    ("?M@?0??L@@YAHXZ@YA?AURetVal@1@H@Z", "struct L::RetVal __cdecl `int __cdecl L(void)'::`1'::M(int)"),
]


UNALIGNED_AND_LITERALS = [
    # "__unaligned" qualifies the pointee, after its own const and volatile
    ("?f@@YAPFAHXZ", "int __unaligned * __cdecl f(void)"),
    ("?f@@YAXPFBH@Z", "void __cdecl f(int const __unaligned *)"),
    ("?f@@YAXAFAH@Z", "void __cdecl f(int __unaligned &)"),
    ("?f@@YAXQFAH@Z", "void __cdecl f(int __unaligned *const)"),
    # ... and travels with them, so a pointer to an unaligned pointer keeps it
    ("?f@@YAXPFAPFAH@Z", "void __cdecl f(int __unaligned *__unaligned *)"),
    ("?f@@YAXPFAPAH@Z", "void __cdecl f(int *__unaligned *)"),
    ("?f@@YAXPIFAH@Z", "void __cdecl f(int __unaligned *__restrict)"),
    # a user-defined literal takes its suffix from the identifier after the code
    ("??__K_deg@@YAHO@Z", 'int __cdecl operator ""_deg(long double)'),
    ("??__Kmm@@YAHO@Z", 'int __cdecl operator ""mm(long double)'),
    # "@" is the scope spelled zero
    ("?M@?@??L@@YAHXZ@4HA", "int `int __cdecl L(void)'::`0'::M"),
]


TRAILING_QUALIFIERS = [
    # a plain type takes it directly
    ("?s@@3HB", "int const s"),
    # a class, struct or enum does too - "const MyClass instance" is spelled this way
    ("?inst@@3Urecord@@B", "struct record const inst"),
    ("?inst@@3VMyClass@@B", "class MyClass const inst"),
    ("?inst@@3W4E@@B", "enum E const inst"),
    # a pointer passes it to what it points at, not to itself
    ("?s@@3PADB", "char const *s"),
    ("?a@@3PAUS@@B", "struct S const *a"),
    ("?s@@3PAPADB", "char *const *s"),
    ("?s@@3QBDD", "char const volatile *const s"),
    # and an array passes it on to its element, the way C spells one
    ("?arr@@3QAY01HB", "int const (*const arr)[2]"),
]


MEMBER_QUALIFIERS_AND_SCOPES = [
    # "$$A8" is a function type carrying what only a member function may carry
    ("??$f@$$A8@@BAHXZ@@YAXXZ", "void __cdecl f<int __cdecl(void) const>(void)"),
    ("??$f@$$A8@@IAAHXZ@@YAXXZ", "void __cdecl f<int __cdecl(void) __restrict>(void)"),
    ("??$f@$$A8@@GBAHXZ@@YAXXZ", "void __cdecl f<int __cdecl(void) const &>(void)"),
    ("??$f@$$A8@@HBAHXZ@@YAXXZ", "void __cdecl f<int __cdecl(void) const &&>(void)"),
    # a scope number is written the way a template argument's is, nibbles included
    ("?M@?L@??L@@YAHXZ@4HA", "int `int __cdecl L(void)'::`11'::M"),
    ("?x@?1??f@@YAXXZ@4HA", "int `void __cdecl f(void)'::`2'::x"),
    ("?M@?@??L@@YAHXZ@4HA", "int `int __cdecl L(void)'::`0'::M"),
]


MEMBER_DATA_POINTERS = [
    ("?f@@YAXPQfoo@@H@Z", "void __cdecl f(int foo::*)"),
    ("?f@@YAXPRfoo@@D@Z", "void __cdecl f(char const foo::*)"),
    ("?f@@YAXPSfoo@@H@Z", "void __cdecl f(int volatile foo::*)"),
    ("?f@@YAXPTfoo@@H@Z", "void __cdecl f(int const volatile foo::*)"),
    ("?f@@YAXQQfoo@@H@Z", "void __cdecl f(int foo::*const)"),
    ("?f@@YAXPQ?$T@H@@H@Z", "void __cdecl f(int T<int>::*)"),
    # as a data symbol it repeats the qualifier and names its class again by back-reference
    ("?m@@3PQfoo@@HQ1@", "int foo::*m"),
    ("?m@@3PRfoo@@DR1@", "char const foo::*m"),
    ("?m@@3PQfoo@@HR1@", "int const foo::*m"),
]


LATER_FORMS = [
    # a pack separator and an empty pack stand between arguments without being one
    ("??$f@H$$ZH@@YAXXZ", "void __cdecl f<int, int>(void)"),
    ("??$f@$$$V@@YAXXZ", "void __cdecl f<>(void)"),
    # an extent is written the way a template argument's number is
    ("?i@@3PAY0BE@HA", "int (*i)[20]"),
    # what runs around an object with a non-trivial lifetime, whose name is recorded
    ("??__EFoo@@YAXXZ", "void __cdecl `dynamic initializer for 'Foo''(void)"),
    ("??__FFoo@@YAXXZ", "void __cdecl `dynamic atexit destructor for 'Foo''(void)"),
    ("??__EFoo@@YAXU0@@Z", "void __cdecl `dynamic initializer for 'Foo''(struct Foo)"),
    # a name mangled although it is extern "C"
    ("?overloaded_fn@@$$J0YAXXZ", 'extern "C" void __cdecl overloaded_fn(void)'),
    # a vftable may say which base it is the table for
    ("??_7A@B@@6BC@D@@@", "const B::A::`vftable'{for `D::C'}"),
    ("??_8A@B@@7BC@D@@@", "const B::A::`vbtable'{for `D::C'}"),
    # a member function pointer keeps a data symbol's qualifier after its parameters
    ("?p@@3P8B@@EAA?CHXZES1@", "int volatile (__cdecl B::*p)(void) volatile"),
    # a parenthesised pointer declarator abuts the sigil; a function declarator does not
    ("?FunArr@@3PAY0BE@P6AHHH@ZA", "int (__cdecl *(*FunArr)[20])(int, int)"),
    ("?f@@YAP6AHXZXZ", "int (__cdecl * __cdecl f(void))(void)"),
]


LATEST_FORMS = [
    # a second spelling of the empty pack, and an alias template named rather than described
    ("??$templ_fun_with_ty_pack@$$V@@YAXXZ", "void __cdecl templ_fun_with_ty_pack<>(void)"),
    ("??$f@$$YAliasA@PR20047@@@PR20047@@YAXXZ", "void __cdecl PR20047::f<PR20047::AliasA>(void)"),
    # a vftable may name more than one base
    ("??_7A@B@@6BC@D@@E@F@@@", "const B::A::`vftable'{for `D::C's `F::E'}"),
    # __restrict qualifies a member function, next to its reference qualifier
    ("?foo@A@PR19361@@QIGAEXXZ", "public: void __thiscall PR19361::A::foo(void) __restrict &"),
    # and on a data symbol it qualifies the pointer, once however often it is spelled
    ("?h3@@3QAHIA", "int *const __restrict h3"),
    ("?h3@@3QIAHA", "int *const __restrict h3"),
    ("?h3@@3QIAHIA", "int *const __restrict h3"),
    ("?h3@@3PAHIA", "int *__restrict h3"),
]


FINAL_FORMS = [
    # a template whose name is an operator, and an operator that leaves its return empty
    ("??$?HH@S@@QEAAAEAU0@H@Z", "public: struct S & __cdecl S::operator+<int>(int)"),
    ("??RFoo@@QBE@XZ", "public: __thiscall Foo::operator()(void) const"),
    ("??RFoo@@QBEHXZ", "public: int __thiscall Foo::operator()(void) const"),
    # the type as written rather than as a parameter would decay it, extent and all
    ("??$f@$$BY01H@@YAXXZ", "void __cdecl f<int[2]>(void)"),
    ("??0?$Class@$$BY0A@H@@QAE@XZ", "public: __thiscall Class<int[]>::Class<int[]>(void)"),
    ("??0?$Class@$$BY04QAH@@QAE@XZ", "public: __thiscall Class<int *const[5]>::Class<int *const[5]>(void)"),
    # two conventions spelled with an attribute, and two spelled with nothing
    ("?swift_func@@YSXXZ", "void __attribute__((__swiftcall__)) swift_func(void)"),
    ("?f@@YWXXZ", "void __attribute__((__swiftasynccall__)) f(void)"),
    ("?f@@YTXXZ", "void f(void)"),
    # a convention spelled with nothing keeps the parentheses a pointer needs
    ("?f@@YAXP6ZHXZ@Z", "void __cdecl f(int ( *)(void))"),
    ("?f@@YAXP8S@@AZXXZ@Z", "void __cdecl f(void ( S::*)(void))"),
]


THUNKS_AND_ADDRESSES = [
    # the address of a symbol, read in the template's own back-reference scope
    ("??$f@$1?x@@3HA@@YAXXZ", "void __cdecl f<&int x>(void)"),
    ("??$f@VBar@@$1?x@0@3HA@@YAXXZ", "void __cdecl f<class Bar, &int f::x>(void)"),
    ("??$f@$E?x@@3HA@@YAXXZ", "void __cdecl f<int x>(void)"),
    # a thunk that adjusts "this" on the way through
    (
        "??_EBase@@G3AEPAXI@Z",
        "[thunk]: private: void * __thiscall Base::`vector deleting dtor'`adjustor{4}'(unsigned int)",
    ),
    (
        "??_EDerived@@$4PPPPPPPM@A@EAAPEAXI@Z",
        "[thunk]: public: virtual void * __cdecl Derived::`vector deleting dtor'`vtordisp{-4, 0}'(unsigned int)",
    ),
    # the reference prints an adjustor's displacement as a 32-bit unsigned value, so a
    # negative one is its two's complement and negative zero is zero
    (
        "??_EDerived@ns@@W?A@EAAPEAXI@Z",
        "[thunk]: public: virtual void * __cdecl ns::Derived::`vector deleting dtor'`adjustor{0}'(unsigned int)",
    ),
    (
        "??_EDerived@ns@@W?B@EAAPEAXI@Z",
        "[thunk]: public: virtual void * __cdecl ns::Derived::`vector deleting dtor'`adjustor{4294967295}'(unsigned int)",
    ),
    # a vcall names no access and carries no parameters
    ("??_9Base@@$B7AA", "[thunk]: __cdecl Base::`vcall'{8, {flat}}"),
    # a conversion operator's name is its return type, in the vtordisp form too
    (
        "??BEDerived@@$4PPPPPPPM@A@EAAPEAXI@Z",
        "[thunk]: public: virtual void * __cdecl EDerived::operator void *`vtordisp{-4, 0}'(unsigned int)",
    ),
    (
        "??BEDerived@@$4PPPPPPPM@A@EAAHXZ",
        "[thunk]: public: virtual int __cdecl EDerived::operator int`vtordisp{-4, 0}'(void)",
    ),
]


COMPLETING_FORMS = [
    # a literal is spelled by its contents, which the length counts with the terminator
    ("??_C@_02PCEFGMJL@hi?$AA@", '"hi"'),
    ("??_C@_00CNPNBAHC@?$AA@", '""'),
    ("??_C@_0M@LACCLLLM@Hello?5world?$AA@", '"Hello world"'),
    # a wide literal is two bytes to the character, most significant first
    ("??_C@_19FINJPIIF@?$AAw?$AAi?$AAd?$AAe?$AA?$AA@", 'L"wide"'),
    # only 32 bytes of a string are ever written, so a longer one is cut short and says so
    ("??_C@_05ABCDEFGH@hi?$AA@", '"hi\\0"...'),
    # Over sixteen wide characters only 32 bytes are written, so the terminator the
    # reference drops (where two declared bytes remain) is past the end: all sixteen show.
    (
        "??_C@_1CK@GINHBNC@?$AAa?$AAb?$AAc?$AAd?$AAe?$AAf?$AAg?$AAh?$AAi?$AAj?$AAk?$AAl?$AAm?$AAn?$AAo?$AAp@",
        'L"abcdefghijklmnop"',
    ),
    # Past 64 declared bytes the reference calls the wide string truncated and says so,
    # and then spells every character it has -- terminator included, if one was written.
    (
        "??_C@_1EE@GINHBNC@?$AAa?$AAb?$AAc?$AAd?$AAe?$AAf?$AAg?$AAh?$AAi?$AAj?$AAk?$AAl?$AAm?$AAn?$AAo?$AAp@",
        'L"abcdefghijklmnop"...',
    ),
    # Where the declared length falls exactly on the last character written, that one is
    # the terminator and is dropped; here eight declared bytes over four characters.
    ("??_C@_17EEHFKJGG@?$AAt?$AAe?$AAx?$AAx@", 'L"tex"'),
    # the rest of the RTTI family names a class, and the descriptor says where the base sits
    ("??_R1A@?0A@EA@Base@@8", "Base::`RTTI Base Class Descriptor at (0, -1, 0, 64)'"),
    ("??_R2Base@@8", "Base::`RTTI Base Class Array'"),
    ("??_R3Base@@8", "Base::`RTTI Class Hierarchy Descriptor'"),
    ("??_R4Base@@6B@", "const Base::`RTTI Complete Object Locator'"),
    # a conversion operator is named by the type it converts to, which it writes as its return
    ("??BBase@@QEAAHXZ", "public: int __cdecl Base::operator int(void)"),
    ("??BFoo@@QBEPAHXZ", "public: int * __thiscall Foo::operator int *(void) const"),
    # converting to a *member* pointer: the name ends in `Bar::*` but is not a pointee,
    # so it must not be bracketed
    ("??BFoo@@QEAAPEQBar@@HXZ", "public: int Bar::* __cdecl Foo::operator int Bar::*(void)"),
    ("??BFoo@@QEAAQEQBar@@HXZ", "public: int Bar::*const __cdecl Foo::operator int Bar::*const(void)"),
    # the shape it must still bracket: a conversion operator returning a function pointer
    (
        "??BFoo@@QEAAP6AHH@ZXZ",
        "public: int (__cdecl * __cdecl Foo::operator int (__cdecl *)(int)(void))(int)",
    ),
    ("?g@@YAPEQBar@@HXZ", "int Bar::* __cdecl g(void)"),
    # a placeholder the compiler writes where a type would go
    ("?f@@YA?A?<decltype-auto>@@XZ", "<decltype-auto> __cdecl f(void)"),
    # a name replaced by a hash of itself, and what may follow it
    ("??@a6a285da2eea70dba6b578022be61d81@", "??@a6a285da2eea70dba6b578022be61d81@"),
    ("??@a6a285da2eea70dba6b578022be61d81@asdf", "??@a6a285da2eea70dba6b578022be61d81@"),
    # `llvm-undname` 18.1 writes `struct _x` (its `insertSpaceIfNeeded` only spaces after
    # an alphanumeric), which declares a different variable; we keep the space
    ("?x@@3U_@@A", "struct _ x"),
    ("?x@@3V$@@A", "class $ x"),
    ("?x@@3UA_@@A", "struct A_ x"),
    # a guard, and what runs for a static with a lifetime
    ("??_Bx@@51", "x::`local static guard'{2}"),
    ("??__Jx@@51", "x::`local static thread guard'{2}"),
    ("??__E?i@C@@0HA@@YAXXZ", "void __cdecl `dynamic initializer for `private: static int C::i''(void)"),
]


COMPLETING_DECLINED = [
    # a conversion operator's thunk with no return type has no type to convert to
    "??BEDerived@@$4PPPPPPPM@A@EAA@XZ",
    "??_R0?AUBase@@@8X",  # a type descriptor ends where it ends
    "??$f@$X@@YAXXZ",  # "$" introduces one of a fixed set, and "X" is not among them
    "??_R2Base@@8X",  # nor does the rest of the RTTI family carry anything after its storage
    "??_Bx@@51X",  # nor a guard
    # a guard's number is a scope index and has no negative; the reference refuses `?0`
    "??_Bx@@5?0",
    "??_Bx@@5?9",
    "??__Jx@@5?0",
    "??__EFoo@@3HA",  # what runs code takes a signature, never a storage class
    "??_C@_12ABCDEFGH@hi?$AA@",  # a wide literal is two bytes to the character, so never an odd count
    # more wide characters than the declared length has room for; LLVM's main branch
    # refuses these (18.1 counted past zero and printed strings no compiler wrote)
    "??_C@_1K@GINHBNC@?$AAh?$AAe?$AAl?$AAl?$AAo?$AA?$AA@",
    "??_C@_13EEHFKJGG@?$AAt?$AAe?$AAx?$AAx@",
    "??_C@_15EEHFKJGG@?$AAt?$AAe?$AAx?$AAx@",
    "??_C@_11EEHFKJGG@?$AAt?$AAe@",
    "??_C@_02ABCDEFGH@h?$Qi?$AA@",  # a byte is written as two nibbles from "A" to "P"
    "??_C@_02ABCDEFGH@hi?$AA@X",  # and nothing follows the literal
    # the reference decodes into a fixed 128-byte buffer and refuses past it
    "??_C@_0IC@ABCDEFGH@" + "a" * 129 + "@",
    "??_9Base@@$RB7AA",  # a thunk through a virtual base names an access this does not
    "??_7Base@@3HA",  # a vftable is written with its own storage class and no other
    "??__EFoo@@51",  # and what runs code takes no storage class at all, guard or otherwise
]


NOEXCEPT_ON_THE_SYMBOL = [
    # `_E` in place of the closing `Z`: clang writes it only inside a function type, but
    # the reference reads it on a symbol too
    ("?f@@YAXX_E", "void __cdecl f(void) noexcept"),
    ("?f@@YAX@_E", "void __cdecl f() noexcept"),
    ("?f@@YAXZ_E", "void __cdecl f(...) noexcept"),
    ("?f@@YAXHZ_E", "void __cdecl f(int, ...) noexcept"),
    ("?f@C@@SAXX_E", "public: static void __cdecl C::f(void) noexcept"),
    ("?f@C@@QEAAXX_E", "public: void __cdecl C::f(void) noexcept"),
    ("?f@C@@QEBAXX_E", "public: void __cdecl C::f(void) const noexcept"),
]


class MsvcNoexceptOnTheSymbolTestSuite(unittest.TestCase):
    def test_the_reference_spelling(self):
        for mangled, expected in NOEXCEPT_ON_THE_SYMBOL:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_the_marker_takes_the_place_of_the_terminator(self):
        """`_E` stands where `Z` would; a name with both, or with neither, is not one."""
        for mangled in ("?f@@YAXXZ_E", "?f@@YAXX", "?f@@YAXX_", "?f@@YAXX_F"):
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)


class MsvcCompletingFormsTestSuite(unittest.TestCase):
    def test_shapes_the_completing_forms_do_not_allow_are_refused(self):
        for mangled in COMPLETING_DECLINED:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)

    def test_the_completing_forms_match_the_reference(self):
        for mangled, expected in COMPLETING_FORMS:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_the_whole_reference_corpus_is_understood(self):
        corpus = [
            line.rstrip("\n").split("\t")
            for line in (Path(__file__).parent / "conformance" / "msvc-llvm-corpus.txt")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip() and not line.startswith("#") and "\t" in line
        ]
        self.assertEqual(len(corpus), UNIQUE_CORPUS_NAMES)
        for mangled, expected in corpus:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)


class MsvcThunkTestSuite(unittest.TestCase):
    def test_thunks_and_addresses_match_the_reference(self):
        for mangled, expected in THUNKS_AND_ADDRESSES:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_a_vcall_and_its_thunk_require_each_other(self):
        for mangled in (
            "??0f9Base@@$B7AA",
            "??_9Base10@@YAADMXZ",
            # the reference reads these; this declines them, as it does elsewhere, rather
            # than take a digit for a convention or ignore bytes after the name
            "??_9Base@@$B7A1",
            "??_9Base@@$B7AAX??_EDerived@@$4A@A@EA1PEAXI@Z",
            "??_EDerived@@$4A@A@EAAPEAXI@ZX",
        ):
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)


class MsvcFinalFormsTestSuite(unittest.TestCase):
    def test_the_final_forms_match_the_reference(self):
        for mangled, expected in FINAL_FORMS:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_shapes_these_forms_do_not_allow_are_refused(self):
        for mangled in (
            "?overloaded_fn@@$$J00YAXXZ",  # the marker counts with one digit, not two
            "??0?$Class@$$B$0?9@@QAE@XZ",  # "$$B" introduces a type, and an integer is not one
            "??BFoo@@QBE@XZ",  # a conversion operator's return names what it converts to
            # the reference takes any byte as a convention; this requires a letter, so a
            # name mangled with something else declines
            "?f@@Y1XXZ",
            "?f@@YAXP61HXZ@Z",
            "?f@@YAXP8S@@A1XXZ@Z",
            "??$f@$$A61HXZ@@YAXXZ",
        ):
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)


class MsvcLatestFormsTestSuite(unittest.TestCase):
    def test_the_latest_forms_match_the_reference(self):
        for mangled, expected in LATEST_FORMS:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)


class MsvcLaterFormsTestSuite(unittest.TestCase):
    def test_the_later_forms_match_the_reference(self):
        for mangled, expected in LATER_FORMS:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_the_shapes_these_forms_do_not_allow_are_refused(self):
        for mangled in (
            "??__E?i@C@0HA@@YAXXZ",  # what it runs for is named plainly, not by a special name
            "??__EFooTypeWithQuals@@3U?$S@$$A8@@GBAHXZ@1@A",  # it runs code, so it takes a signature
            "?overloaded_fn@@$$JYAXXZ",  # the marker counts characters, so a digit belongs here
            "??__K_deg@@YAXU0@@Z",  # a literal operator's suffix is not recorded, so 0 names nothing
            "?i@@3PAY0?0HA",  # an array does not have a negative extent
            "??_7A@@6B?0@@",  # nor is a base named by anything but a name
        ):
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)


class MsvcMemberDataPointerTestSuite(unittest.TestCase):
    def test_a_pointer_into_a_class_is_spelled_around_the_class(self):
        for mangled, expected in MEMBER_DATA_POINTERS:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_a_reference_into_a_class_is_refused(self):
        # C++ has no reference to member, however much "AT..." looks like one
        self.assertEqual(demangle_msvc_symbol("?k@@3ATfoo@@DT1@"), "?k@@3ATfoo@@DT1@")

    def test_a_member_type_the_reference_spells_differently_is_declined(self):
        # "PQfoo@@SAPEAX" is "void **foo::*" there, dropping qualifiers this would keep, and
        # nothing on the producer side settles which is right
        self.assertEqual(demangle_msvc_symbol("?f@@YAXPQfoo@@SAPEAX@Z"), "?f@@YAXPQfoo@@SAPEAX@Z")


class MsvcMemberQualifierTestSuite(unittest.TestCase):
    def test_member_qualifiers_and_scope_numbers_match_the_reference(self):
        for mangled, expected in MEMBER_QUALIFIERS_AND_SCOPES:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_a_qualifier_written_twice_is_refused(self):
        # each of them is written at most once, so "HH" is not a name
        for mangled in ("??$f@$$A8@@HHBAHXZ@@YAXXZ", "??$f@$$A8@@IIAAHXZ@@YAXXZ"):
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)

    def test_a_named_class_is_not_written_in_this_position(self):
        self.assertEqual(demangle_msvc_symbol("??$f@$$A8S@@AEHXZ@@YAXXZ"), "??$f@$$A8S@@AEHXZ@@YAXXZ")

    def test_a_qualifier_the_table_does_not_hold_is_refused(self):
        self.assertEqual(demangle_msvc_symbol("??$f@$$A8@@GZAHXZ@@YAXXZ"), "??$f@$$A8@@GZAHXZ@@YAXXZ")

    def test_a_qualified_fragment_that_is_neither_a_namespace_nor_a_scope_is_a_name(self):
        # "?Q" names no scope -- the numbers stop at P and "?A" is the unnamed namespace --
        # so what is left is an identifier that happens to keep its "?"
        self.assertEqual(demangle_msvc_symbol("?x@?Q@@3HA"), "int ?Q::x")


class MsvcTrailingQualifierTestSuite(unittest.TestCase):
    def test_the_storage_forms_a_data_symbol_may_take(self):
        cases = [
            # __ptr64 stands in front of the qualifier, where something is pointed at
            ("?s@@3PEAHEA", "int *s"),
            ("?$RT1@NeedsReferenceTemporary@@3AEBHEB", "int const &NeedsReferenceTemporary::$RT1"),
            # a pointer into a class spells its storage the long way, "E" included
            ("?m@@3PEFRfoo@@DER1@", "char const __unaligned foo::*m"),
            # and a name with no signature at all is spelling its linkage
            ("?extern_c_func@@9", 'extern "C" extern_c_func'),
            ("?local@?1??extern_c_func@@9@4HA", "int `extern \"C\" extern_c_func'::`2'::local"),
        ]
        for mangled, expected in cases:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_storage_forms_the_grammar_does_not_pair_are_refused(self):
        for mangled in (
            "?s@@3HEA",  # nothing is pointed at, so no __ptr64 belongs here
            "?s@@3HEB",
            "?memptr1@@3RESB@@HEA",  # a pointer into a class takes the long form, not this
            "?extern_c_func@@9X",  # the linkage marker ends the name, so nothing follows it
        ):
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)

    def test_a_data_symbol_with_bytes_after_it_is_refused(self):
        self.assertEqual(demangle_msvc_symbol("?s@@3HBX"), "?s@@3HBX")

    def test_a_data_symbols_trailing_qualifier_lands_where_it_is_declared(self):
        for mangled, expected in TRAILING_QUALIFIERS:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)


class MsvcUnalignedAndLiteralTestSuite(unittest.TestCase):
    def test_the_forms_are_spelled_the_way_the_reference_spells_them(self):
        for mangled, expected in UNALIGNED_AND_LITERALS:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_an_unknown_double_underscore_operator_is_refused(self):
        for mangled in ("??__N@@YAHO@Z", "??__Z@@YAHO@Z", "??__@@YAHO@Z"):
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)

    def test_the_two_operators_written_with_that_prefix(self):
        for mangled, expected in [
            ("??__L_deg@@YAHO@Z", "int __cdecl _deg::operator co_await(long double)"),
            (
                "??__MSpaceship@hard@@QEBAHAEBU01@@Z",
                "public: int __cdecl hard::Spaceship::operator<=>(struct hard::Spaceship const &) const",
            ),
        ]:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_a_signature_ends_with_z_or_with_the_noexcept_marker(self):
        """`demangleThrowSpecification` takes `Z` or `_E` and refuses anything else."""
        self.assertEqual(
            demangle_msvc_symbol("?takes_noexcept@hard@@YAHP6AHH@_EA6AHH@_E@Z"),
            "int __cdecl hard::takes_noexcept(int (__cdecl *)(int) noexcept, int (__cdecl &)(int) noexcept)",
        )
        for mangled in ("?f@@YAHP6AHH@_F@Z", "?f@@YAHP6AHH@_@Z"):
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)


class MsvcLocalScopeTestSuite(unittest.TestCase):
    def test_a_scope_inside_a_function_names_the_function_and_which_scope(self):
        for mangled, expected in LOCAL_SCOPES:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_a_local_scope_without_an_enclosing_name_is_refused(self):
        for mangled in ("?x@?1@4HA", "?x@?1?@4HA", "?x@?1??@4HA"):
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)


class MsvcMemberPointerTestSuite(unittest.TestCase):
    def test_member_pointers_and_template_integers_match_the_reference(self):
        for mangled, expected in MEMBER_POINTERS_AND_INTEGERS:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_shapes_neither_form_allows_are_refused(self):
        for mangled in (
            "?f@@YAXPE8S@@AEAXXZ@Z",  # __ptr64 is not written in front of a member function
            "?f@@YAX$0A@@Z",  # an integer is a template argument, never a parameter
            "??$f@$0@@YAXXZ",  # ... and needs digits
            "?f@@YAXP8?0S@@@AEXXZ@Z",  # nor is a constructor a class to point into
        ):
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)


class MsvcGrammarRuleTestSuite(unittest.TestCase):
    def test_rules_spell_names_the_way_the_reference_does(self):
        for mangled, expected in GRAMMAR_RULES:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_shapes_those_rules_forbid_are_refused(self):
        for mangled in GRAMMAR_DECLINED:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)


class MsvcBackReferenceTestSuite(unittest.TestCase):
    def test_back_references_resolve_the_way_the_mangler_numbered_them(self):
        for mangled, expected in BACKREFS:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_names_the_back_reference_rules_forbid_are_refused(self):
        for mangled in BACKREF_DECLINED:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)

    def test_a_reference_and_a_back_referenced_argument_still_read(self):
        # the two refusals above are positional, not a retreat from these forms
        self.assertEqual(demangle_msvc_symbol("?f@@YAXADPAD@Z"), "void __cdecl f(char *const volatile &)")
        self.assertEqual(demangle_msvc_symbol("?h@@YAXPAH0@Z"), "void __cdecl h(int *, int *)")


# Settled by probing llvm-undname directly: the reference corpus exercises none of these.
PROBED_RULES = [
    # an attribute-spelled calling convention carries a space of its own in front of a
    # declarator, where __cdecl and __vectorcall carry only the separator
    ("?j@@3P6SHH@ZA", "int (__attribute__((__swiftcall__))  *j)(int)"),
    ("?g@@YAP6SHH@ZXZ", "int (__attribute__((__swiftcall__))  * __cdecl g(void))(int)"),
    ("?memptrtofun3@@3P8B@@EAWXXZEQ1@", "void (__attribute__((__swiftasynccall__))  B::*memptrtofun3)(void)"),
    ("?swift_func@@YSXXZ", "void __attribute__((__swiftcall__)) swift_func(void)"),
    # a convention spelled with nothing leaves no gap behind it either
    ("??0foo@@QAV@XZ", "public: foo::foo(void)"),
    ("??1foo@@QEAZ@XZ", "public: foo::~foo(void)"),
    # a member function's modifiers are written __ptr64, __restrict, __unaligned, then a
    # reference qualifier, and are spelled back in that same order
    ("??0foo@@QEIFAA@XZ", "public: __cdecl foo::foo(void) __restrict __unaligned"),
    ("??0foo@@QEGAA@XZ", "public: __cdecl foo::foo(void) &"),
    ("??0foo@@QFGAA@XZ", "public: __cdecl foo::foo(void) __unaligned &"),
    # a vcall is the slot it dispatches through, written with no qualifier and no convention
    ("??_9Base@@$B7AA", "[thunk]: __cdecl Base::`vcall'{8, {flat}}"),
    # an operator name ending in "()" is a name rather than a parameter list, so the sigil
    # in front of it abuts as it does any other declarator
    ("??RBasy@@3ABHB", "int const &Basy::operator()"),
    ("??Rmemptrtofun6@@3P8B@@EAA?BHXZEQ1@", "int const (__cdecl B::*memptrtofun6::operator())(void)"),
    # "$$B" says a type is written as it stands rather than as a parameter would decay it
    ("??0?$C@$$BH@@QAE@XZ", "public: __thiscall C<int>::C<int>(void)"),
    ("??0?$C@$$BY04H@@QAE@XZ", "public: __thiscall C<int[5]>::C<int[5]>(void)"),
    # a "::*" deep inside a rendered parameter is not the declarator's own, so the enclosing
    # signature a local scope carries does not change how that scope's name is spaced
    ("?g@?1??f@@YAXP8Owner@@AEXXZ@Z@YVXXZ", "void `void __cdecl f(void (__thiscall Owner::*)(void))'::`2'::g(void)"),
    ("?g@?1??f@@YAXH@Z@YVXXZ", "void `void __cdecl f(int)'::`2'::g(void)"),
    ("??_R1BA@?0A@EA@Base@@8", "Base::`RTTI Base Class Descriptor at (16, -1, 0, 64)'"),
    ("??__FFoo@@YAXXZ", "void __cdecl `dynamic atexit destructor for 'Foo''(void)"),
]
PROBED_DECLINED = [
    "??0foo@@QIEAA@XZ",  # the modifiers are written in one order, and each at most once
    "??0foo@@QEFIAA@XZ",
    "??0foo@@QGFAA@XZ",
    "??0foo@@QGIAA@XZ",
    "??0foo@@QEGHAA@XZ",
    "??_9Base@@$B7DA",  # a vcall carries neither a qualifier nor a convention of its own
    "??_9Base@@$B7FAA",
    "??_9Base?h1@@3QAHA",  # ... and it is never spelled with storage
    "??BBa@@3HA",  # a conversion operator reads what it converts to from its return slot
    "?f@@YAX$$BY01H@Z",  # "$$B" stands where an argument stands and nowhere else
    "?f@@YAXQAY04$$BH@Z",
    "??0?$C@$$B6AXXZ@@QAE@XZ",  # ... and never over a function type
    "??0?$C@$$BY04$$BH@@QAE@XZ",  # ... nor nested inside another argument
    "??0?$C@$$BY04$04$$CBH@@QAE@XZ",  # an integer is an argument, not an array's element
    "??0?$C@$$BYA@H@@QAE@XZ",  # an array of no dimensions is not a type
    "?f@@YAXQF6AXXZ@Z",  # no modifier stands in front of a function type
    "?m@@3RF8B@@EAAHXZEQ1@",
    "?m@@3PE8B@@EAAHXZEQ1@",
    "??_R1A@4?0A@EA@Base@@8",  # how far the table reaches and its flags are not negative
    "??_R1A@1A@?0A@EA@Base@@8",
    "??__F1Foo@@YAXXZ",  # a digit there stands for an earlier name, and there is none
]


#: Spellings the mutation fuzzer found, each read off `llvm-undname` 18.1.3.
MUTATION_RULES = [
    # `outputQualifiers` tests a bitmask, const first, so read order never reaches output.
    ("?s4@PR13182@@3PCDD", "char const volatile *PR13182::s4"),
    ("?s4@PR13182@@3RCDD", "char const volatile *volatile PR13182::s4"),
    ("?s@@3QBDD", "char const volatile *const s"),
    # `__unaligned` comes after `__restrict`, on a pointer and on a pointee alike
    ("?f@@YAPFAPIAHXZ", "int *__restrict __unaligned * __cdecl f(void)"),
    ("?f@@YAPFAPIQS@@HXZ", "int S::*__restrict __unaligned * __cdecl f(void)"),
    ("?f@@YAPFBHXZ", "int const __unaligned * __cdecl f(void)"),
    # a qualifier inside a template argument is not a duplicate of the symbol's own
    (
        "?h@FTypeWithQuals@@3U?$S@$$A8@@HCAHXZ@1@C",
        "struct FTypeWithQuals::S<int __cdecl(void) volatile &&> volatile FTypeWithQuals::h",
    ),
    # `extern "C"` goes after the access specifier *and* after `static` or `virtual`
    ("?overloaded_fn@@$$J0EAAHH@Z", 'private: virtual extern "C" int __cdecl overloaded_fn(int)'),
    ("?overloaded_fn@@$$J0SAHH@Z", 'public: static extern "C" int __cdecl overloaded_fn(int)'),
    ("?overloaded_fn@@$$J0YAHH@Z", 'extern "C" int __cdecl overloaded_fn(int)'),
    # `demanglePointerExtQualifiers` reads optional `E`, `I`, `F`, in that order, each once
    ("?h3@@3QEIAHFA", "int __unaligned *const __restrict h3"),
    ("?h3@@3QEIAHEIFA", "int __unaligned *const __restrict h3"),
    ("?h3@@3QEIAHEIA", "int *const __restrict h3"),
]

#: Shapes those rules forbid.
MUTATION_DECLINED = [
    # the order is fixed, so `I` before `E` is not a name
    "?h3@@3QEIAHIEA",
    # `$$J0` is one literal; the digit is part of the marker, not a field
    "?overloaded_fn@@$$J3YAXXZ",
    "?overloaded_fn@@$$J4YAXXZ",
    "?overloaded_fn@@$$JYAXXZ",
    # only a pointer points into a class: C++ has no reference to member
    "?l@@3A8foo@@AEHH@ZA",
    # `demangleVcallThunkNode` consumes `$B` only; a `??_9` with a vtordisp slot is two thunks
    "??_9Derived@@$4PPPPPPPM@A@EAAPEAXI@Z",
    # `$$Y` names an alias template only where a template argument stands
    "?f@@YAX$$YURetVal@@@Z",
    "??$f@PA$$YURetVal@@@@YAXXZ",
    # the initialiser stub's name ends with its variable; no scope may follow
    "??__E?i@C@@0HA@e@@QEAAHXZ",
    "??__E?i@C@@0HA@gle_no_backref1@@YAXQAHQAH@Z",
]


#: Taken off `clang++ --target=x86_64-pc-windows-msvc` object files. Member qualifiers go
#: inside whatever the return type wraps around the declarator, as
#: `FunctionSignatureNode::outputPost` writes them.
CLANG_EMITTED = [
    ("?b7@S@@QEBAAEAY01$$CBDXZ", "public: char const (& __cdecl S::b7(void) const)[2]"),
    ("?b8@S@@QECAAEAY01$$CBDXZ", "public: char const (& __cdecl S::b8(void) volatile)[2]"),
    ("?b9@S@@QEBAPEAY01$$CBDXZ", "public: char const (* __cdecl S::b9(void) const)[2]"),
    ("?ba@S@@QEBAP6AHH@ZXZ", "public: int (__cdecl * __cdecl S::ba(void) const)(int)"),
    ("?bc@S@@QEBAA6AHH@ZXZ", "public: int (__cdecl & __cdecl S::bc(void) const)(int)"),
    ("?f@S@@QEBAXXZ", "public: void __cdecl S::f(void) const"),
]

#: `_L`/`_M` (`__int128`/`unsigned __int128`): clang emits them but `llvm-undname` 18.1's
#: `demanglePrimitiveType` has no case for them.
CLANG_EMITTED_LLVM_REFUSES = [
    ("?bd@S@@QEBA_L_M@Z", "public: __int128 __cdecl S::bd(unsigned __int128) const"),
    ("?alpha@@YAX_L@Z", "void __cdecl alpha(__int128)"),
    ("?beta@@YAX_M@Z", "void __cdecl beta(unsigned __int128)"),
]


class MsvcClangEmittedTestSuite(unittest.TestCase):
    """Names a compiler wrote, which is the only ground truth a demangler has."""

    def test_a_member_qualifier_goes_inside_what_the_return_type_wraps(self):
        for mangled, expected in CLANG_EMITTED:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_a_dynamic_initializer_name_may_be_qualified(self):
        """`demangleInitFiniStub` reads a whole declarator and names the stub with it.

        So the variable's scopes go *inside* the quotes. This read the leading identifier
        and refused anything after it, which is every namespace-scope object with a
        non-trivial constructor -- `ns::Thrower g;` in a namespace is one -- and every
        function-local static, whose scope is written the same way.
        """
        for mangled, expected in [
            ("??__Eg@inner@outer@@YAXXZ", "void __cdecl `dynamic initializer for 'outer::inner::g''(void)"),
            ("??__Fh@outer@@YAXXZ", "void __cdecl `dynamic atexit destructor for 'outer::h''(void)"),
            ("??__Etop@@YAXXZ", "void __cdecl `dynamic initializer for 'top''(void)"),
            (
                "??__Fd@?1??guarded@ns@@YAHXZ@YAXXZ",
                "void __cdecl `dynamic atexit destructor for '`int __cdecl ns::guarded(void)'::`2'::d''(void)",
            ),
            # The `?`-prefixed form, where the variable carries its own storage class.
            (
                "??__E?i@C@@0HA@@YAXXZ",
                "void __cdecl `dynamic initializer for `private: static int C::i''(void)",
            ),
        ]:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_an_auto_non_type_template_argument(self):
        """`$M <type> <integer>`, which `llvm-undname` 18.1 refuses and 20.1 reads.

        The type is written so the argument's own type is recoverable and the reference
        spells only the value: `A<42>` rather than `A<(int)42>`, `A<99>` for a `char` and
        `A<1>` for a `bool`. Every expectation here is `llvm-undname` 20.1.2's output for
        a symbol `clang++ --target=x86_64-pc-windows-msvc` emitted; the 18.1 on this box
        refuses all four, which is why they are not in `msvc-clang.txt`.
        """
        for mangled, expected in [
            ("?f@?$A@$MH0CK@@t@@QEBAHXZ", "public: int __cdecl t::A<42>::f(void) const"),
            ("?f@?$A@$MD0GD@@t@@QEBAHXZ", "public: int __cdecl t::A<99>::f(void) const"),
            ("?f@?$A@$M_K06@t@@QEBAHXZ", "public: int __cdecl t::A<7>::f(void) const"),
            ("?f@?$A@$M_N00@t@@QEBAHXZ", "public: int __cdecl t::A<1>::f(void) const"),
            ("?f@?$AutoNT@$M$$T0A@@hard@@QEBAHXZ", "public: int __cdecl hard::AutoNT<0>::f(void) const"),
        ]:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_it_is_an_argument_rather_than_a_type(self):
        """It stands where an argument stands and nowhere a type may nest."""
        for mangled in ("?f@@YAX$MH0CK@@Z", "?f@@YAXPA$MH0CK@@Z"):
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)

    def test_the_int128_codes_are_read_although_the_reference_refuses_them(self):
        for mangled, expected in CLANG_EMITTED_LLVM_REFUSES:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)


class MsvcMutationRuleTestSuite(unittest.TestCase):
    """What `tools/mutate.py` found by damaging the reference's own corpus."""

    def test_the_spellings_match_the_reference(self):
        for mangled, expected in MUTATION_RULES:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_the_shapes_those_rules_forbid_are_refused(self):
        for mangled in MUTATION_DECLINED:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)

    def test_what_those_rules_still_read(self):
        for mangled, expected in [
            ("?overloaded_fn@@$$J0QEAAHH@Z", 'public: extern "C" int __cdecl overloaded_fn(int)'),
            ("?l@@3P8foo@@AEHH@ZEQ1@", "int (__thiscall foo::*l)(int)"),
            ("??_9Base@@$B7AA", "[thunk]: __cdecl Base::`vcall'{8, {flat}}"),
            ("??$f@$$YURetVal@@@@YAXXZ", "void __cdecl f<URetVal>(void)"),
            ("??__E?i@C@@0HA@@YAXXZ", "void __cdecl `dynamic initializer for `private: static int C::i''(void)"),
        ]:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)


class MsvcProbedRuleTestSuite(unittest.TestCase):
    def test_probed_rules_spell_names_the_way_the_reference_does(self):
        for mangled, expected in PROBED_RULES:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_shapes_those_rules_forbid_are_refused(self):
        for mangled in PROBED_DECLINED:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)


class MsvcDemanglerTestSuite(unittest.TestCase):
    def test_known_names_match_the_reference_spelling(self):
        for mangled, expected in DEMANGLED:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_unmodelled_forms_come_back_untouched(self):
        for mangled in DECLINED:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), mangled)

    def test_a_name_carrying_the_declarator_placeholder_is_refused(self):
        evil = "?f@@YAXPAVa\x00b@@@Z"

        self.assertEqual(demangle_msvc_symbol(evil), evil)

    def test_a_name_carrying_any_control_character_is_refused(self):
        # identifiers are copied verbatim, so these would reach the reported name
        for code in (0x01, 0x07, 0x0A, 0x0D, 0x1B, 0x7F):
            evil = f"?ctrl{chr(code)}@@YAXXZ"
            with self.subTest(code=code):
                self.assertEqual(demangle_msvc_symbol(evil), evil)

    def test_names_that_are_not_msvc_decorated_are_left_alone(self):
        for name in ("", "plain_name", "_ZN4test4funcEv", "_RNvC6_123foo3bar", "_ReadFile@20"):
            with self.subTest(name=name):
                self.assertEqual(demangle_msvc_symbol(name), name)

    def test_a_deeply_nested_type_stops_at_the_depth_bound(self):
        shallow = "?f@@YAX" + "PA" * 30 + "D@Z"
        deep = "?f@@YAX" + "PA" * 300 + "D@Z"

        self.assertTrue(demangle_msvc_symbol(shallow).startswith("void __cdecl f(char "))
        self.assertEqual(demangle_msvc_symbol(deep), deep)

    def test_a_deeply_nested_name_stops_at_the_depth_bound(self):
        # nesting in the *name* rather than the type: each level is another template
        deep = "?f@@YAX" + "V?$A@" * 200 + "H" + "@" * 200 + "@@Z"

        self.assertEqual(demangle_msvc_symbol(deep), deep)

    def test_the_depth_bound_is_the_callers(self):
        name = "?f@@YAX" + "PA" * 30 + "D@Z"

        self.assertEqual(demangle_msvc_symbol(name, demangle.Limits(max_depth=16)), name)
        self.assertTrue(demangle_msvc_symbol(name, demangle.Limits(max_depth=64)).startswith("void __cdecl f(char "))

    def test_a_name_deeper_than_the_interpreters_stack_is_declined(self):
        deep = "?f@@YAX" + "PA" * 3000 + "D@Z"

        self.assertEqual(demangle_msvc_symbol(deep, demangle.RELAXED_LIMITS), deep)
        self.assertIsNone(parse_msvc_symbol(deep, demangle.RELAXED_LIMITS))

    def test_a_result_that_would_balloon_is_refused(self):
        # each layer re-uses every earlier argument back-reference, so the rendered result
        # grows multiplicatively while the name itself stays short
        name = "?f@@YAXPAD" + "".join("P6AX" + str(index) * 9 + "@Z" for index in range(8)) + "@Z"

        self.assertLess(len(name), 200)
        self.assertEqual(demangle_msvc_symbol(name), name)

    def test_a_truncated_name_never_raises(self):
        source = "?static_method@foo@@SAPAV1@XZ"
        for end in range(len(source) + 1):
            with self.subTest(prefix=source[:end]):
                self.assertIsInstance(demangle_msvc_symbol(source[:end]), str)


class MsvcReferenceCorpusTestSuite(unittest.TestCase):
    """Measure the demangler against llvm-undname's output on LLVM's own corpus."""

    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parent / "conformance" / "msvc-llvm-corpus.txt"
        cls.corpus = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or "\t" not in line:
                continue
            mangled, expected = line.split("\t", 1)
            cls.corpus.append((mangled, expected))

    def test_no_name_is_given_a_third_spelling(self):
        """The guarantee: a name is either demangled correctly or returned untouched."""
        wrong = []
        for mangled, expected in self.corpus:
            got = demangle_msvc_symbol(mangled)
            if got not in (expected, mangled):
                wrong.append((mangled, got, expected))

        self.assertEqual(wrong, [])

    def test_the_share_that_is_understood_is_exactly_what_was_measured(self):
        """A ratchet in both directions: improving coverage means updating this number."""
        exact = sum(1 for mangled, expected in self.corpus if demangle_msvc_symbol(mangled) == expected)

        self.assertEqual(len(self.corpus), UNIQUE_CORPUS_NAMES)
        self.assertEqual(exact, CORPUS_NAMES_UNDERSTOOD)

    def test_every_name_survives_truncation_at_any_point(self):
        for mangled, _ in self.corpus:
            for end in range(len(mangled) + 1):
                self.assertIsInstance(demangle_msvc_symbol(mangled[:end]), str)


if __name__ == "__main__":
    unittest.main()


class TestOneElementQualifierAtATime(unittest.TestCase):
    """`$$C` qualifies an array element once; a second in a row is not a type.
    `llvm-undname` refuses `?f@@YAXAEAY111$$CB$$CBH@Z`, which folding the two read as
    `int const (&)[2][2]`. `tools/mutate.py --seed 9`."""

    def test_refused(self):
        self.assertEqual(demangle_msvc_symbol("?f@@YAXAEAY111$$CB$$CBH@Z"), "?f@@YAXAEAY111$$CB$$CBH@Z")

    def test_one_still_reads(self):
        self.assertEqual(demangle_msvc_symbol("?f@@YAXAEAY111$$CBH@Z"), "void __cdecl f(int const (&)[2][2])")


class TestAnExternCMarkerIsNotFollowedByAVariable(unittest.TestCase):
    """`$$J0` marks a function mangled although it is extern "C". A data-storage
    letter after it is not a function encoding. llvm-undname refuses
    `?overloaded_fn@@$$J04HA`; this printed `int overloaded_fn`.
    `?overloaded_fn@@$$J0YAXXZ` still reads. `tools/mutate.py --seed 23`.
    """

    def test_refused(self):
        self.assertEqual(demangle_msvc_symbol("?overloaded_fn@@$$J04HA"), "?overloaded_fn@@$$J04HA")
        self.assertEqual(demangle_msvc_symbol("?overloaded_fn@@$$h$$J04HA"), "?overloaded_fn@@$$h$$J04HA")

    def test_a_function_after_the_marker_still_reads(self):
        self.assertEqual(
            demangle_msvc_symbol("?overloaded_fn@@$$J0YAXXZ"),
            'extern "C" void __cdecl overloaded_fn(void)',
        )

    def test_the_variable_without_the_marker_still_reads(self):
        self.assertEqual(demangle_msvc_symbol("?overloaded_fn@@3HA"), "int overloaded_fn")


class TestDollarCOverAPointerThatIsAlreadyConst(unittest.TestCase):
    """`$$CB` over `S` -- const over `int *const volatile`. This spells the
    qualifier once; llvm-undname appends another `const`. No compiler writes
    `$$C` on a pointer that already carries it. The neighbour without `$$C`
    still agrees. `tools/mutate.py --seed 23`.
    """

    def test_the_qualifier_is_not_spelled_twice(self):
        self.assertEqual(
            demangle_msvc_symbol("?foo_aay144cbh@@YAXAAY144$$CBSAHH@Z"),
            "void __cdecl foo_aay144cbh(int *const volatile (&)[5][5], int)",
        )

    def test_without_dollar_c_the_pointer_still_agrees(self):
        self.assertEqual(
            demangle_msvc_symbol("?foo_aay144cbh@@YAXAAY144SAHH@Z"),
            "void __cdecl foo_aay144cbh(int *const volatile (&)[5][5], int)",
        )


class TestDollarCOverARestrictPointer(unittest.TestCase):
    """`$$CB` over `PIAD` -- const over `char *__restrict`. This spells the pointer's
    two qualifier words in the order the letters come in, `const __restrict`;
    llvm-undname writes `__restrict const`. C++ leaves the order after a `*` free, so
    both are the same declaration, and the two agree everywhere a compiler writes the
    shape: `?x@@3QIADA` is `char *const __restrict x` to both. They part only where an
    outer `$$C` re-qualifies a pointer that already carries `I`, which no compiler
    writes. `tools/mutate.py --seed 42`.
    """

    def test_the_two_words_come_in_the_order_the_letters_do(self):
        self.assertEqual(
            demangle_msvc_symbol("?r1@Q@ns@@QEBAAEAY03$$CBPIAD@Z"),
            "public: char *const __restrict (& __cdecl ns::Q::r1() const)[4]",
        )

    def test_without_dollar_c_the_pointer_carries_one_word(self):
        self.assertEqual(
            demangle_msvc_symbol("?r1@Q@ns@@QEBAAEAY03PIAD@Z"),
            "public: char *__restrict (& __cdecl ns::Q::r1() const)[4]",
        )

    def test_the_shape_a_compiler_writes_agrees_with_the_reference(self):
        self.assertEqual(demangle_msvc_symbol("?x@@3QIADA"), "char *const __restrict x")


class TestAMemberPointersPointeeKeepsItsExtensionQualifiers(unittest.TestCase):
    """`PEQExt@1@PEIFAH` is `int __unaligned *__restrict ns::Ext::*`, and the two
    extension words belong to the pointee's own letters.

    llvm-undname 18.1 prints both on a pointer and neither when that pointer is a member
    pointer's pointee, so it answers `int *ns::Ext::*` -- one spelling for two
    declarations, since `?extended_plain@ns@@YAPEIFAHXZ` written from the same declarator
    keeps both words there and in this library. Compiler-emitted: `extended_member`,
    `takes_extended_member` and `extended_plain` in
    `tools/corpus_sources/msvc/msvc.cpp`, read back out of a
    `clang++ --target=x86_64-pc-windows-msvc` object file, and pinned against their
    declarations in `tests/conformance/msvc-reference-defects.txt`. `tools/mutate.py
    --seed 42` reached the same gap from the other end.
    """

    def test_a_member_pointers_pointee_keeps_both_words(self):
        self.assertEqual(
            demangle_msvc_symbol("?extended_member@ns@@YAPEQExt@1@PEIFAHXZ"),
            "int __unaligned *__restrict ns::Ext::* __cdecl ns::extended_member(void)",
        )

    def test_the_same_pointee_as_a_parameter(self):
        self.assertEqual(
            demangle_msvc_symbol("?takes_extended_member@ns@@YAXPEQExt@1@PEIFAH@Z"),
            "void __cdecl ns::takes_extended_member(int __unaligned *__restrict ns::Ext::*)",
        )

    def test_the_same_pointer_outside_a_member_pointer_is_where_the_two_agree(self):
        self.assertEqual(
            demangle_msvc_symbol("?extended_plain@ns@@YAPEIFAHXZ"),
            "int __unaligned *__restrict __cdecl ns::extended_plain(void)",
        )


class TestADynamicInitialiserOverANestedSymbolName(unittest.TestCase):
    """`??__E` takes a name, and a nested symbol `?<encoding>@` is one.

    MSVC writes `??__E?i@C@@0HA@@YAXXZ` for a static data member -- the initialised
    variable spelled with its own access and type inside the initialiser's name -- and
    `tests/conformance/msvc-arm64ec.txt` carries that shape out of a real binary.
    `llvm-undname` 18 reads the nested encoding where it is a variable and refuses it
    where it is a *function*, which nothing initialises. That shape is therefore
    reachable only by damaging one of the real ones, and this reads the text as it
    stands rather than deciding what a name may be initialised for. `tools/mutate.py
    --seed 65`.
    """

    def test_the_shape_a_compiler_writes(self):
        self.assertEqual(
            demangle_msvc_symbol("??__E?i@C@@0HA@@YAXXZ"),
            "void __cdecl `dynamic initializer for `private: static int C::i''(void)",
        )

    def test_a_function_encoding_in_the_same_place_is_read_as_one(self):
        self.assertEqual(
            demangle_msvc_symbol("??__E?i@C@@YAXXZ@@YAXXZ"),
            "void __cdecl `dynamic initializer for `void __cdecl C::i(void)''(void)",
        )

    def test_the_atexit_destructor_is_the_same_production(self):
        self.assertEqual(
            demangle_msvc_symbol("??__F?i@C@@0HA@@YAXXZ"),
            "void __cdecl `dynamic atexit destructor for `private: static int C::i''(void)",
        )


class TestAPointerToAMemberOfArrayType(unittest.TestCase):
    """`PEQA@@Y03H` is a pointer to a member of `A` whose type is `int[4]`, and the
    declarator an array brackets is the member pointer's: `int (A::*)[4]`. The array's
    renderer bracketed a declarator it could see opened with `*` or `&`, and `A::*`
    opens with the owner's name, so this wrote `int A::*[4]` -- an array of pointers to
    member, a different type. Every spelling here is `llvm-undname` 18's; the first was
    compiled by Clang 18 for the MSVC target from `int (A::*)[sizeof(T)]`."""

    CASES = (
        ("??$a8@H@@YAXPEQA@@Y03H@Z", "void __cdecl a8<int>(int (A::*)[4])"),
        ("?f@@YAXPEQA@@Y03H@Z", "void __cdecl f(int (A::*)[4])"),
        ("?f@@YAXPEQA@@Y03Y02H@Z", "void __cdecl f(int (A::*)[4][3])"),
        ("?f@@YAXQEQA@@Y03H@Z", "void __cdecl f(int (A::*const)[4])"),
        ("?f@@YAXPEQA@@Y03PEAH@Z", "void __cdecl f(int *(A::*)[4])"),
        ("?f@@YAXPEAPEQA@@Y03H@Z", "void __cdecl f(int (A::**)[4])"),
        ("?f@@YAXAEAPEQA@@Y03H@Z", "void __cdecl f(int (A::*&)[4])"),
        ("?f@@YAXPEQA@@Y03P6AHH@Z@Z", "void __cdecl f(int (__cdecl *(A::*)[4])(int))"),
        ("?f@@YAXPEQA@@Y03UB@@@Z", "void __cdecl f(struct B (A::*)[4])"),
        ("?f@@YAXPEQA@@Y03P8A@@EAAHXZ@Z", "void __cdecl f(int (__cdecl A::*(A::*)[4])(void))"),
        # not a member pointer to an array: unbracketed
        ("?f@@YAXPEAY03H@Z", "void __cdecl f(int (*)[4])"),
        ("?f@@YAXAEAY03H@Z", "void __cdecl f(int (&)[4])"),
        ("?f@@YAXPEQA@@PEAH@Z", "void __cdecl f(int *A::*)"),
    )

    def test_the_member_pointer_is_bracketed(self):
        for mangled, expected in self.CASES:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)


class MsvcWindowsBuildTestSuite(unittest.TestCase):
    """Four shapes the LLVM 18.1.8 Windows release wrote that nothing here had seen.

    436,644 decorated names from its static libraries, put to `llvm-undname` 18.1.3;
    every expected value below is what it printed.
    """

    def test_a_conversion_operator_may_be_a_template(self):
        """`??$?B<args>@`: `operator<A, B> T`, the type still read from the return slot,
        the arguments between the word and the type as `ConversionOperatorIdentifierNode`
        writes them. clangd's `LSPBinder::UntypedOutgoingMethod` declares one, and every
        lambda inside it names it as a scope -- 456 symbols."""
        self.assertEqual(
            demangle_msvc_symbol("??$?BH@S@@QEAAHXZ"),
            "public: int __cdecl S::operator<int> int(void)",
        )
        self.assertEqual(
            demangle_msvc_symbol("??$?BUA@@UB@@@S@@QEBA?AV?$V@H@@XZ"),
            "public: class V<int> __cdecl S::operator<struct A, struct B> class V<int>(void) const",
        )
        self.assertEqual(
            demangle_msvc_symbol("??$?BH@S@@QEAAAEAU0@H@Z"),
            "public: struct S & __cdecl S::operator<int> struct S &(int)",
        )

    def test_a_member_pointer_argument_under_a_wider_inheritance_model(self):
        """`$H`, `$I` and `$J` name a function and carry one, two and three offsets;
        `$F` and `$G` are the data-member forms with offsets alone. Bracketed, with the
        offsets after the name. clang writes `$H` for every
        `filtered_decl_iterator<ObjCMethodDecl, &isClassMethod>` -- 186 symbols."""
        cases = [
            ("?f@@YAXV?$X@$H?g@S@@QEAAXXZ3@@@Z", "void __cdecl f(class X<{public: void __cdecl S::g(void), 4}>)"),
            ("?f@@YAXV?$X@$H3@@@Z", "void __cdecl f(class X<{4}>)"),
            (
                "?f@@YAXV?$X@$I?g@S@@QEAAXXZ3A@@@@Z",
                "void __cdecl f(class X<{public: void __cdecl S::g(void), 4, 0}>)",
            ),
            (
                "?f@@YAXV?$X@$J?g@S@@QEAAXXZ3A@?1@@@Z",
                "void __cdecl f(class X<{public: void __cdecl S::g(void), 4, 0, -2}>)",
            ),
            ("?f@@YAXV?$X@$F3A@@@@Z", "void __cdecl f(class X<{4, 0}>)"),
            ("?f@@YAXV?$X@$G3A@?1@@@Z", "void __cdecl f(class X<{4, 0, -2}>)"),
            # a vcall thunk stands where the function would: clang's LazyOffsetPtr
            (
                "?f@@YAXV?$X@$H??_9S@@$B7AAA@@@@Z",
                "void __cdecl f(class X<{[thunk]: __cdecl S::`vcall'{8, {flat}}, 0}>)",
            ),
        ]
        for mangled, expected in cases:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_a_letter_escape_in_a_string_literal(self):
        """`?A` through `?Z` are 0xC1 through 0xDA and `?a` through `?z` 0xE1 through
        0xFA: `demangleCharLiteral`'s two tables, which UTF-8 text lands in. Only the
        digit escapes were read, and 34 literals were refused for a letter."""
        self.assertEqual(demangle_msvc_symbol("??_C@_02BDPOGFEI@?C?$LE?$AA@"), '"\\xC3\\xB4"')
        self.assertEqual(demangle_msvc_symbol("??_C@_02ABCDEFGH@?A?Z?$AA@"), '"\\xC1\\xDA"')
        self.assertEqual(demangle_msvc_symbol("??_C@_02ABCDEFGH@?a?z?$AA@"), '"\\xE1\\xFA"')

    def test_a_function_type_in_a_pointed_to_functions_return_type_has_no_convention(self):
        """`PointerTypeNode::outputPre` prints its pointee with `OF_NoCallingConvention`,
        a flag that reaches everything inside the return type -- so `std::function<void
        (void)>` there, and `std::function<void __cdecl(void)>` in the parameter list
        of the same pointer, or as a return type the declaration's own. 123 callback
        pointers in the build are spelled so."""
        cases = [
            (
                "?f@@YAXP6A?AV?$function@$$A6AXXZ@std@@XZ@Z",
                "void __cdecl f(class std::function<void (void)> (__cdecl *)(void))",
            ),
            (
                "?f@@YAXP8C@@EAA?AV?$function@$$A6AXXZ@std@@XZ@Z",
                "void __cdecl f(class std::function<void (void)> (__cdecl C::*)(void))",
            ),
            (
                "?x@@3P6A?AV?$function@$$A6AXXZ@std@@XZEA",
                "class std::function<void (void)> (__cdecl *x)(void)",
            ),
            (
                "?f@@YAXP6AXV?$function@$$A6AXXZ@std@@@Z@Z",
                "void __cdecl f(void (__cdecl *)(class std::function<void __cdecl(void)>))",
            ),
            (
                "?f@@YA?AV?$function@$$A6AXXZ@std@@XZ",
                "class std::function<void __cdecl(void)> __cdecl f(void)",
            ),
            # `memorizeIdentifier` rendered the recorded name with default flags, so a
            # back-reference spells the convention where the original dropped it
            (
                "?f@@YAXP6A?AV?$function@$$A6AXXZ@std@@XZV1@@Z",
                "void __cdecl f(class std::function<void (void)> (__cdecl *)(void), class function<void __cdecl(void)>)",
            ),
            (
                "?f@@YAXV?$X@P6A?AV?$function@$$A6AXXZ@std@@XZ@@V1@@Z",
                "void __cdecl f(class X<class std::function<void (void)> (__cdecl *)(void)>,"
                " class X<class std::function<void (void)> (__cdecl *)(void)>)",
            ),
        ]
        for mangled, expected in cases:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)


class MsvcBoostBuildTestSuite(unittest.TestCase):
    """What Boost 1.84's MSVC 14.3 libraries write that the LLVM release did not.

    98,822 decorated names from the twelve `boost_*-vc143` NuGet packages, put to
    `llvm-undname` 18 and to LLVM's main branch. The release refuses a deduced return
    type; main reads it, and the expected values are what main prints.
    """

    def test_a_deduced_return_type(self):
        """`?A_P` is `auto` and `?A_T` is `decltype(auto)`, after the calling convention
        where a return type goes: a function declared with one and not yet defined, so
        the compiler had nothing to write but the keyword. `_P` and `_T` are types in
        their own right too -- clang writes a dependent `auto` that way anywhere -- so
        `$$QA_P` is `auto &&`.
        """
        cases = [
            (
                "??$_Get_unwrapped@AAPAD@std@@YA?A_TAAPAD@Z",
                "decltype(auto) __cdecl std::_Get_unwrapped<char *&>(char *&)",
            ),
            (
                "??$_Idl_distance@PADPAD@std@@YA?A_PABQAD0@Z",
                "auto __cdecl std::_Idl_distance<char *, char *>(char *const &, char *const &)",
            ),
            (
                "??$_Tuple_get@$0A@$$QAH@std@@YA$$QA_P$$QAV?$tuple@$$QAH@0@@Z",
                "auto && __cdecl std::_Tuple_get<0, int &&>(class std::tuple<int &&> &&)",
            ),
            ("?f@@YA?A_PXZ", "auto __cdecl f(void)"),
            ("?f@@YA?A_TXZ", "decltype(auto) __cdecl f(void)"),
            ("?f@@0_PA", "private: static auto f"),
            # `decltype(auto)` is one word, so a declarator is spaced off it; LLVM's main
            # branch looks only at the last character and writes `decltype(auto)f`
            ("?f@@3_TA", "decltype(auto) f"),
            ("?f@@3PEA_TEA", "decltype(auto) *f"),
            ("?f@@YAXPEA_T@Z", "void __cdecl f(decltype(auto) *)"),
        ]
        for mangled, expected in cases:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)
        for name in ("?f@@YA?A_XXZ", "?f@@YA?A_"):
            with self.subTest(mangled=name):
                self.assertEqual(demangle.demangle(name, language="msvc"), name)

    def test_a_vcall_thunk_carries_a_calling_convention(self):
        """`??_9C@@$B<slot>A<convention>`: after the slot, a literal `A` and then a
        convention read as any function's is. A 32-bit build writes `E`, `__thiscall`,
        for every one of them, and this took that letter for a second literal `A` --
        `demangleVcallThunkNode` consumes one `A` and then `demangleCallingConvention`
        -- so the three in Boost's x86 test framework were refused.
        """
        cases = [
            (
                "??_9test_observer@unit_test@boost@@$BA@AE",
                "[thunk]: __thiscall boost::unit_test::test_observer::`vcall'{0, {flat}}",
            ),
            ("??_9A@@$B7AE", "[thunk]: __thiscall A::`vcall'{8, {flat}}"),
            ("??_9A@@$B7AA", "[thunk]: __cdecl A::`vcall'{8, {flat}}"),
            ("??_9A@@$B7AQ", "[thunk]: __vectorcall A::`vcall'{8, {flat}}"),
            # a convention the reference spells with nothing at all
            ("??_9A@@$B7AZ", "[thunk]: A::`vcall'{8, {flat}}"),
        ]
        for mangled, expected in cases:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)
        for name in ("??_9A@@$B7DA", "??_9A@@$B7FAA", "??_9A@@$B7A", "??_9A@@$B7AEX"):
            with self.subTest(mangled=name):
                self.assertEqual(demangle.demangle(name, language="msvc"), name)

    def test_a_hashed_name_is_still_a_scope(self):
        """`??@<hash>@` is a decorated name too long for the linker, replaced by its
        MD5, and nothing of the original is in the symbol: the spelling is the name
        itself, as the reference prints it. A hashed *function* is still a scope, and
        409 of Boost's names are a catch block's variable inside one; the reference
        spells the scope the way it spells the name, and this read only a hash that
        opened the whole symbol.
        """
        cases = [
            (
                "?catch$0@?0???@3ddba3124f25df6569f8c4db1b2c5f5c@@4HA",
                "int `??@3ddba3124f25df6569f8c4db1b2c5f5c@'::`1'::catch$0",
            ),
            # the complete object locator's tag is the one thing that may follow the hash
            (
                "?x@?1???@0186a6a6b637290d321170469f0c2292@??_R4@@4HA",
                "int `??@0186a6a6b637290d321170469f0c2292@??_R4@'::`2'::x",
            ),
            (
                "??$f@$1??@0186a6a6b637290d321170469f0c2292@@@YAXXZ",
                "void __cdecl f<&??@0186a6a6b637290d321170469f0c2292@>(void)",
            ),
            ("??@0186a6a6b637290d321170469f0c2292@??_R4@", "??@0186a6a6b637290d321170469f0c2292@??_R4@"),
        ]
        for mangled, expected in cases:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)
        self.assertEqual(
            demangle.demangle("?x@?1???@0186a6a6b637290d321170469f0c2292@4HA", language="msvc"),
            "?x@?1???@0186a6a6b637290d321170469f0c2292@4HA",
        )


class MsvcSpellingWiderThanEightTimesItsNameTestSuite(unittest.TestCase):
    """The rendered result was bounded at eight times the name's length, and a name a
    compiler writes can pass that: a back-reference is two characters standing for a
    whole rendered type. Over 1,025,085 names from LLVM, Boost, ITK, OpenCV and Qt the
    widest is twelve times, and 80 pass eight; every one was refused. The bound is now
    thirty-two times, still under the absolute `max_output`.
    """

    NAME = (
        "??4?$_Iterator012@Ubidirectional_iterator_tag@std@@U?$pair@$$CBV?$basic_string@DU?$char_traits@D@std@@"
        "V?$allocator@D@2@@std@@V12@@2@HPBU32@ABU32@U_Iterator_base12@2@@std@@QAEAAU01@ABU01@@Z"
    )

    def test_the_widest_real_spelling_reads(self):
        """From OpenCV 5's static libraries, x86: 188 characters whose spelling is 2,217,
        as `llvm-undname` prints it -- six `U32@`s, each a 200-character `std::pair`."""
        spelled = demangle_msvc_symbol(self.NAME)
        self.assertEqual(len(spelled), 2217)
        self.assertTrue(spelled.startswith("public: struct std::_Iterator012<struct std::bidirectional_iterator_tag, "))
        self.assertTrue(spelled.endswith("struct std::_Iterator_base12> const &)"))
        self.assertEqual(
            spelled.count("class std::basic_string<char, struct std::char_traits<char>, class std::allocator<char>>"),
            18,
        )

    def test_a_cleanup_block_inside_such_a_function_reads_too(self):
        """`?dtor$0@?0??<function>@4HA`, which read for a short function and was refused
        for a long one, its scope being the whole spelling of that function."""
        self.assertEqual(demangle_msvc_symbol("?dtor$0@?0??f@@YAXXZ@4HA"), "int `void __cdecl f(void)'::`1'::dtor$0")
        nested = demangle_msvc_symbol("?dtor$0@?0?" + self.NAME + "@4HA")
        self.assertTrue(nested.startswith("int `public: struct _Iterator012<struct std::bidirectional_iterator_tag, "))
        self.assertTrue(nested.endswith("'::`1'::dtor$0"))

    def test_the_bound_still_holds(self):
        """A spelling that doubles at every level is stopped by the relative bound
        before the absolute one, and the name is refused rather than printed in part."""
        from demangle.core.errors import LimitExceeded

        # each level names a class whose two arguments are the previous level, twice
        name = "?x@@3V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$A@V?$B@HH@@V1@@@V1@@@V1@@@V1@@@V1@@@V1@@@V1@@@V1@@@V1@@@V1@@@V1@@@A"
        with self.assertRaises(LimitExceeded):
            demangle.demangle_strict(name, language="msvc")


class MsvcLlvmMainTestSuite(unittest.TestCase):
    """LLVM's own `llvm/test/Demangle/ms-*.test` at its main branch, 706 checks, put to
    this reader. Fifteen did not hold; four were the tests' own trailing junk and
    whitespace, and eleven were these two things.
    """

    def test_an_auto_non_type_template_argument_takes_every_form(self):
        """`$M <type> <nttp>`: after the deduced type comes any form an argument takes,
        written without its `$` -- an integer, a symbol's address, a pointer to member
        -- and only the value is spelled. This read the integer form alone.
        llvm-undname 18 refuses all of them; the expected values are LLVM main's own
        `ms-auto-templates.test`.
        """
        cases = [
            (
                "??0?$AutoNTTPClass@$MPEAH1?i@@3HA@@QEAA@XZ",
                "public: __cdecl AutoNTTPClass<&int i>::AutoNTTPClass<&int i>(void)",
            ),
            (
                "??0?$AutoNTTPClass@$MPEAH1?i@@3HA$MPEAH1?j@@3HA@@QEAA@XZ",
                "public: __cdecl AutoNTTPClass<&int i, &int j>::AutoNTTPClass<&int i, &int j>(void)",
            ),
            (
                "??0?$AutoNTTPClass@$MP6AHXZ1?Func@@YAHXZ@@QEAA@XZ",
                "public: __cdecl AutoNTTPClass<&int __cdecl Func(void)>::AutoNTTPClass<&int __cdecl Func(void)>(void)",
            ),
            ("??$AutoFunc@$MPEAH1?i@@3HA@@YA?A?<auto>@@XZ", "<auto> __cdecl AutoFunc<&int i>(void)"),
            (
                "??0?$AutoNTTPClass@$MP8S@@EAAXXZ1?f@1@QEAAXXZ@@QEAA@XZ",
                "public: __cdecl AutoNTTPClass<&public: void __cdecl S::f(void)>::AutoNTTPClass<&public: void __cdecl S::f(void)>(void)",
            ),
            (
                "??0?$AutoNTTPClass@$MP8M@@EAAXXZH?f@1@QEAAXXZA@@@QEAA@XZ",
                "public: __cdecl AutoNTTPClass<{public: void __cdecl M::f(void), 0}>::AutoNTTPClass<{public: void __cdecl M::f(void), 0}>(void)",
            ),
            (
                "??0?$AutoNTTPClass@$MP8V@@EAAXXZI?f@1@QEAAXXZA@A@@@QEAA@XZ",
                "public: __cdecl AutoNTTPClass<{public: void __cdecl V::f(void), 0, 0}>::AutoNTTPClass<{public: void __cdecl V::f(void), 0, 0}>(void)",
            ),
            (
                "??0?$AutoNTTPClass@$MPEQV@@HFBA@A@@@QEAA@XZ",
                "public: __cdecl AutoNTTPClass<{16, 0}>::AutoNTTPClass<{16, 0}>(void)",
            ),
            ("??0?$A@$MH0BA@@@QEAA@XZ", "public: __cdecl A<16>::A<16>(void)"),
        ]
        for mangled, expected in cases:
            with self.subTest(mangled=mangled):
                self.assertEqual(demangle_msvc_symbol(mangled), expected)

    def test_a_symbol_named_as_an_address_has_its_template_name_recorded(self):
        """`memorizeIdentifier(S->Name->getUnqualifiedIdentifier())` after the symbol
        behind `$1` is read: for a plain name it changes nothing, since reading the name
        recorded it, and for a template name it records what a symbol's own template
        name is otherwise the one exception to. `ms-cxx14.test`'s `Zoo`, which this
        refused. The record is the reference's, deduplicated: `?2` is still nothing.
        """
        self.assertEqual(
            demangle_msvc_symbol("?Zoo@@3U?$Foo@$1??$x@H@@3HA$1?1@3HA@@A"), "struct Foo<&int x<int>, &int x<int>> Zoo"
        )
        self.assertEqual(demangle_msvc_symbol("?Zoo@@3U?$Foo@$1?x@@3HA$1?1@3HA@@A"), "struct Foo<&int x, &int x> Zoo")
        for name in ("?Zoo@@3U?$Foo@$1?x@@3HA$1?2@3HA@@A", "?Zoo@@3U?$Foo@$1??$x@H@@3HA$1?2@3HA@@A"):
            with self.subTest(mangled=name):
                self.assertEqual(demangle.demangle(name, language="msvc"), name)


class TestAQualifierOnABackReferencedDeducedReturn(unittest.TestCase):
    """`?C?4@` is volatile in front of a back reference to `<auto>`. llvm-undname
    drops the qualifier, as it does for `?B?<auto>@@`. The encoding is kept. Found by
    `tools/mutate.py --seed 15`.
    """

    def test_the_volatile_is_kept(self):
        mangled = "??R<lambda_1>@?0???R<lambda_0>@?0??nested_lambdas@hard@@YAHXZ@QEBA?A?<auto>@@H@Z@QEBA?C?4@H@Z"
        self.assertEqual(
            demangle.demangle(mangled, language="msvc"),
            "public: <auto> volatile __cdecl `public: <auto> __cdecl "
            "`int __cdecl hard::nested_lambdas(void)'::`1'::<lambda_0>::operator()(int) const'"
            "::`1'::<lambda_1>::operator()(int) const",
        )
        self.assertEqual(demangle.demangle("?f@@YA?C?<auto>@@XZ", language="msvc"), "<auto> volatile __cdecl f(void)")
