// The second half of the MSVC corpus: what a newer standard adds to the mangling.
//
// Freestanding, like `msvc.cpp` beside it. Four defects came out of the first run of
// this file against `llvm-undname` 18.1: `operator<=>` and `operator co_await`, both
// written with the `??__` prefix and neither in the table here; the `_E` that ends a
// `noexcept` signature where a `Z` ends every other one; and a deduced return type
// written as a back reference to an earlier one, which is what a lambda nested inside
// another lambda produces.
//
// Two productions here are newer than the `llvm-undname` on this box. `$M <type>
// <integer>` -- an `auto` non-type template argument -- is refused by 18.1 and read by
// 20.1, so it is pinned by `tests/test_msvc.py` against 20 rather than recorded in the
// corpus. `_V`, the marker on a C++23 explicit object member function, is refused by
// both, and this scheme refuses it too: there is no reference spelling to follow yet and
// inventing one is how a demangler starts inventing.

namespace hard {

using FN = int(int) noexcept;
int noexcept_fn(int) noexcept { return 0; }
int takes_noexcept(FN *p, FN &r) { return p(0) + r(0); }

struct WithRef {
  int f() &;
  int g() &&;
  int h() const &;
  int i() const && noexcept;
  static int s(int);
  auto deduced() { return 1; }
  decltype(auto) deduced2() { return 1; }
};
int WithRef::f() & { return 0; }
int WithRef::g() && { return 0; }
int WithRef::h() const & { return 0; }
int WithRef::i() const && noexcept { return 0; }
int WithRef::s(int a) { return a; }

template <auto V> struct AutoNT {
  int f() const;
};
template <auto V> int AutoNT<V>::f() const { return 0; }
template struct AutoNT<42>;
template struct AutoNT<'c'>;
template struct AutoNT<nullptr>;

struct HasMember {
  int m;
  int fn(int);
};
template <int HasMember::*P> struct MemPtrNT {
  int f() const;
};
template <int HasMember::*P> int MemPtrNT<P>::f() const { return 0; }
template struct MemPtrNT<&HasMember::m>;
template <int (HasMember::*P)(int)> struct MemFnNT {
  int f() const;
};
template <int (HasMember::*P)(int)> int MemFnNT<P>::f() const { return 0; }
template struct MemFnNT<&HasMember::fn>;

extern int global;
int global = 0;
template <int *P> struct PtrNT {
  int f() const;
};
template <int *P> int PtrNT<P>::f() const { return 0; }
template struct PtrNT<&global>;

inline namespace v1 {
int inline_ns_fn() { return 0; }
}  // namespace v1

template <class T> int var_template = 0;
template int var_template<int>;

int nested_lambdas() {
  auto outer = [](int a) {
    auto inner = [=](int b) { return a + b; };
    return inner(1);
  };
  return outer(2);
}

struct Spaceship {
  int operator<=>(const Spaceship &) const;
  int operator co_await();
};
int Spaceship::operator<=>(const Spaceship &) const { return 0; }
int Spaceship::operator co_await() { return 0; }

unsigned long long operator""_ull(unsigned long long v) { return v; }
const char *operator""_str(const char *s, unsigned long long) { return s; }
unsigned long long uses_literals() { return 1_ull + (unsigned long long)"x"_str[0]; }

thread_local int tls = 1;
struct TlsCtor {
  TlsCtor();
};
thread_local TlsCtor tls_obj;
TlsCtor::TlsCtor() {}

int takes_nullptr(decltype(nullptr)) { return 0; }
int takes_unaligned(int __unaligned *p) { return *p; }

struct Big {
  int a, b, c;
};
auto structured() {
  Big b{1, 2, 3};
  auto [x, y, z] = b;
  return x + y + z;
}
// The same deduced return type with a qualifier in front of it, which is what makes
// `?A?<auto>@@` into `?B?<auto>@@`. `CustomTypeNode::outputPre` in LLVM's `MSNodes.cpp`
// prints the identifier and nothing else, where every other type node prints its
// qualifiers first, so `llvm-undname` 18.1 loses the `const` here. See
// `tests/conformance/msvc-reference-defects.txt`.
const auto structured_const() {
  Big b{4, 5, 6};
  auto [x, y, z] = b;
  return x + y + z;
}

constexpr int cx(int a) { return a; }
constinit int ci = 7;

template <class T> concept Small = sizeof(T) <= 4;
template <Small T> int constrained(T v) { return 0; }
template int constrained<int>(int);

#if __cplusplus >= 202302L
struct Explicit {
  int f(this Explicit &self, int a);
};
int Explicit::f(this Explicit &self, int a) { return a; }

struct Sub {
  int operator[](int, int) const;
  static int operator()(int);
};
int Sub::operator[](int a, int) const { return a; }
int Sub::operator()(int a) { return a; }
#endif

}  // namespace hard
