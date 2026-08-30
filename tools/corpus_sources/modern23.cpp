// C++20 and C++23: what the newest standards add to the Itanium mangling.
//
// Constrained templates and requires-clauses, coroutines, the defaulted spaceship,
// abbreviated function templates, `auto` and class-type non-type template parameters,
// deducing `this`, the multi-argument subscript and the static call operator. The last
// four are behind an `#if`, so this file still compiles at C++20.
//
// One defect came out of the first run of it: `sizeof...` over a parameter that is not
// bound to a pack writes an ellipsis after the operand, and over one spelled by its own
// mangled name inside a requires-clause -- which is what clang emits for any constrained
// variadic template -- it writes `sizeof...(T...)`. This wrote neither.

#include <coroutine>
#include <compare>
#include <concepts>
#include <cstddef>

namespace m {
template <class T> concept Small = sizeof(T) <= 4;
template <Small T> int constrained(T) { return 0; }
template int constrained<int>(int);

template <class T> requires requires(T t) { t.f(); } int requires_clause(T) { return 0; }
struct HasF { void f(); };
template int requires_clause<HasF>(HasF);

int abbreviated(auto v) { return 0; }
template int abbreviated<char>(char);
int constrained_abbrev(Small auto v) { return 0; }
template int constrained_abbrev<int>(int);

struct Spaceship { auto operator<=>(const Spaceship &) const = default; int a; };
bool uses_spaceship(const Spaceship &a, const Spaceship &b) { return a < b; }

struct Task {
  struct promise_type {
    Task get_return_object();
    std::suspend_never initial_suspend() noexcept;
    std::suspend_never final_suspend() noexcept;
    void return_void();
    void unhandled_exception();
  };
};
Task coro(int) { co_return; }

#if __cplusplus >= 202302L
struct Explicit { int f(this Explicit &self, int a); };
int Explicit::f(this Explicit &self, int a) { return a; }

struct Sub { int operator[](int, int) const; static int operator()(int); };
int Sub::operator[](int a, int) const { return a; }
int Sub::operator()(int a) { return a; }
#endif

template <auto V> struct AutoNT { int f() const; };
template <auto V> int AutoNT<V>::f() const { return 0; }
template struct AutoNT<42>;
template struct AutoNT<'c'>;

struct Lit { int a; constexpr Lit(int v) : a(v) {} };
template <Lit L> struct ClassNT { int f() const; };
template <Lit L> int ClassNT<L>::f() const { return 0; }
template struct ClassNT<Lit{7}>;

#if __cplusplus >= 202302L
int with_static_lambda() { return [](int a) static { return a; }(1); }
#endif
consteval int ce(int a) { return a; }
constinit int ci = 7;
thread_local int tls = 1;

template <class... Ts> struct Pack { int f(Ts...) const requires (sizeof...(Ts) > 0); };
template <class... Ts> int Pack<Ts...>::f(Ts...) const requires (sizeof...(Ts) > 0) { return 0; }
template struct Pack<int, char>;
}
