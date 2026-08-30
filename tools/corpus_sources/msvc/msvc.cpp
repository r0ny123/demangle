// Corpus source for the MSVC scheme: real symbols from a real compiler.
//
// Freestanding on purpose. `clang++ --target=x86_64-pc-windows-msvc` has no MS headers
// on a Linux box, and the point here is the mangling rather than the library. What it
// covers is the shapes a mangler reaches that hand-written vectors tend not to: a member
// function whose return type wraps its declarator, the four RTTI records, vftables and
// vbtables, thunks through multiple and virtual inheritance, guards and the dynamic
// initialiser and atexit stubs, the anonymous namespace, local scopes, and the extended
// integer types.
//
// Two defects came out of the first run of this file against `llvm-undname` 18.1: a
// member function's qualifiers written past what its return type wraps, and a dynamic
// initialiser for a *qualified* variable -- which is every namespace-scope object with a
// non-trivial constructor -- refused outright.

namespace ns {

struct Base {
  virtual ~Base();
  virtual int v(int) const;
  virtual int w(int) volatile;
  int data;
  static int stat;
};
struct Other {
  virtual ~Other();
  virtual int o();
};
struct Derived : Base, Other {
  ~Derived() override;
  int v(int) const override;
  int o() override;
};

Base::~Base() {}
Other::~Other() {}
Derived::~Derived() {}
int Base::v(int a) const { return a; }
int Base::w(int a) volatile { return a; }
int Derived::v(int a) const { return a + 1; }
int Derived::o() { return 2; }
int Base::stat = 0;

extern const char arr[4];
const char arr[4] = {};
extern int freefn(int);
int freefn(int a) { return a; }

// Return types that wrap the declarator, which is where a member qualifier goes.
struct Q {
  const char (&r1() const)[4];
  const char (*r2() volatile)[4];
  int (*r3() const)(int);
  int (&r4() const)(int);
  int (Q::*r5() const)(int);
  int m(int);
  __int128 big(unsigned __int128) const;
  operator int() const;
  int operator+(int) const;
  int operator[](int) const;
  int operator()(int, char) const;
  Q &operator=(const Q &) &;
  Q &operator=(Q &&) &&;
  bool operator==(const Q &) const noexcept;
};
const char (&Q::r1() const)[4] { return arr; }
const char (*Q::r2() volatile)[4] { return &arr; }
int (*Q::r3() const)(int) { return freefn; }
int (&Q::r4() const)(int) { return freefn; }
int (Q::*Q::r5() const)(int) { return &Q::m; }
int Q::m(int a) { return a; }
__int128 Q::big(unsigned __int128) const { return 0; }
Q::operator int() const { return 0; }
int Q::operator+(int a) const { return a; }
int Q::operator[](int a) const { return a; }
int Q::operator()(int a, char) const { return a; }
Q &Q::operator=(const Q &) & { return *this; }
Q &Q::operator=(Q &&) && { return *this; }
bool Q::operator==(const Q &) const noexcept { return true; }

template <class T, class U> struct Pair {
  T first;
  U second;
  T get() const;
};
template <class T, class U> T Pair<T, U>::get() const { return first; }
template struct Pair<int, char>;
template struct Pair<const char *, double>;

template <int N, unsigned long long M, bool B> struct Value {
  int f() const;
};
template <int N, unsigned long long M, bool B> int Value<N, M, B>::f() const { return N; }
template struct Value<-3, 42, true>;
template struct Value<0, 0, false>;

template <class... Ts> struct Pack {
  int f(Ts...) const;
};
template <class... Ts> int Pack<Ts...>::f(Ts...) const { return 0; }
template struct Pack<int, char, double>;
template struct Pack<>;

template <template <class, class> class TT> struct Higher {
  int f() const;
};
template <template <class, class> class TT> int Higher<TT>::f() const { return 0; }
template struct Higher<Pair>;

template <class T> T identity(T value) { return value; }
template int identity<int>(int);
template const char *identity<const char *>(const char *);

template <class T> struct Traits {
  using type = T;
};
template <class T> typename Traits<T>::type dependent(T v) { return v; }
template int dependent<int>(int);

int __stdcall stdcall_fn(int a) { return a; }
int __fastcall fastcall_fn(int a) { return a; }
int __vectorcall vectorcall_fn(int a) { return a; }
extern "C" int extern_c_fn(int a) { return a; }

enum class Scoped : unsigned short { one, two };
enum Plain { plain_one };
int takes_enums(Scoped, Plain) { return 0; }

int takes_arrays(int (&a)[3], int (*b)[3], const int (&c)[2][2]) { return a[0] + (*b)[0] + c[0][0]; }
int takes_pointers(const int *const *p, int *__restrict q) { return **p + *q; }
int takes_members(int Q::*, int (Q::*)(int)) { return 0; }
int takes_chars(char8_t, char16_t, char32_t, wchar_t) { return 0; }
int takes_refs(int &&a, const int &b) { return a + b; }

namespace inner {
struct Nested {
  struct Deeper {
    int f() const;
  };
};
int Nested::Deeper::f() const { return 0; }
}  // namespace inner

namespace {
struct Anon {
  int f() const;
};
int Anon::f() const { return 0; }
int anon_fn() { return 0; }
}  // namespace
int uses_anon() { return anon_fn() + Anon().f(); }

int with_lambda() {
  auto one = [](int a) { return a; };
  auto two = [&](int a) { return one(a) + 1; };
  return two(1);
}

struct WithStatics {
  static int counter();
};
int WithStatics::counter() {
  static int n = 0;
  return ++n;
}

const char *literal() { return "hello, world"; }
const wchar_t *wliteral() { return L"hello"; }

struct Thrower {
  Thrower();
};
Thrower::Thrower() {}
int makes_temporary() {
  Thrower t;
  return 0;
}

// Force the vtables, RTTI records, thunks, guards and stubs to be emitted.
Base *make_base() { return new Derived(); }
Other *make_other() { return new Derived(); }
void destroy(Base *p) { delete p; }
void destroy_array(Base *p) { delete[] p; }
Derived *down(Base *p) { return dynamic_cast<Derived *>(p); }
const void *what(Base *p) { return &typeid(*p); }
int guarded() {
  static Derived d;
  return d.o();
}
struct WithNew {
  static void *operator new(unsigned long long);
  static void operator delete(void *);
  static void *operator new[](unsigned long long);
  static void operator delete[](void *);
};
void *WithNew::operator new(unsigned long long) { return nullptr; }
void WithNew::operator delete(void *) {}
void *WithNew::operator new[](unsigned long long) { return nullptr; }
void WithNew::operator delete[](void *) {}
struct VirtBase : virtual Base {
  int v(int) const override;
};
int VirtBase::v(int a) const { return a; }
VirtBase *make_virt() { return new VirtBase(); }

}  // namespace ns

// Namespace-scope objects with non-trivial constructors: the dynamic initialiser and
// atexit stubs, qualified and unqualified.
namespace outer {
namespace deeper {
ns::Thrower g;
}  // namespace deeper
ns::Thrower h;
}  // namespace outer
ns::Thrower top;

int main() { return 0; }
