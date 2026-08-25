// A spread of C++ constructs chosen for the *symbol names* they produce rather than for
// anything they compute. Anything whose mangling has a distinct shape belongs here.
#include <string>
#include <vector>
#include <map>
#include <memory>
#include <functional>
#include <stdexcept>
#include <algorithm>

namespace outer { namespace inner {

struct Base { virtual ~Base(); virtual int method(int) const; };
Base::~Base() {}
int Base::method(int x) const { return x; }

struct Derived : Base { int method(int) const override; ~Derived() override; };
int Derived::method(int x) const { return x + 1; }
Derived::~Derived() {}

template <typename T, int N = 3> struct Holder {
    T value;
    Holder() : value() {}
    explicit Holder(const T& v) : value(v) {}
    T get() const { return value; }
    template <typename U> U convert() const { return static_cast<U>(value); }
    bool operator==(const Holder& other) const { return value == other.value; }
    Holder& operator+=(const T& v) { value += v; return *this; }
    operator T() const { return value; }
};

template struct Holder<int>;
template struct Holder<std::string>;
template struct Holder<double, 7>;

template <typename... Args> int variadic(Args... args) { return sizeof...(args); }
template int variadic<int, char, double>(int, char, double);

template <typename T> auto deduced(T t) -> decltype(t + t) { return t + t; }
template int deduced<int>(int);

int overloaded(int);
int overloaded(int) { return 0; }
double overloaded(double, const char*);
double overloaded(double d, const char*) { return d; }
void overloaded(int (*)(char), int (&)[4], int Base::*);
void overloaded(int (*)(char), int (&)[4], int Base::*) {}
void refqual(std::string&&, const volatile int*);
void refqual(std::string&&, const volatile int*) {}

struct WithRefQualifiers {
    void lvalue() &;
    void rvalue() &&;
    void constLvalue() const &;
};
void WithRefQualifiers::lvalue() & {}
void WithRefQualifiers::rvalue() && {}
void WithRefQualifiers::constLvalue() const & {}

int withStatics() {
    static std::string local = "guarded";
    static int counter = 0;
    return static_cast<int>(local.size()) + counter++;
}

auto makeLambda() {
    int captured = 1;
    auto lambda = [captured](int x) { return x + captured; };
    return lambda(2);
}

namespace { int internalLinkage(int x) { return x * 2; } }
int useInternal(int x) { return internalLinkage(x); }

std::map<std::string, std::vector<std::unique_ptr<Base>>> containerSoup();
std::map<std::string, std::vector<std::unique_ptr<Base>>> containerSoup() { return {}; }

void throwsSomething() { throw std::runtime_error("no"); }

}}  // namespace outer::inner

extern "C" int plainC(int x) { return x; }

template <template <typename, int> class C, typename T> C<T, 3> templateTemplate(T) { return C<T, 3>(); }
template outer::inner::Holder<int, 3> templateTemplate<outer::inner::Holder, int>(int);

// `Dn`, the nullptr type. The two references spell it differently -- llvm-cxxfilt writes
// `std::nullptr_t` and GNU c++filt writes `decltype(nullptr)` -- and neither corpus
// carried one, so nothing here had ever asked the question. Found by cross-checking the
// references against each other rather than against us.
void takesNullptrType(decltype(nullptr));
void takesNullptrType(decltype(nullptr)) {}
void takesNullptrPointer(decltype(nullptr) *);
void takesNullptrPointer(decltype(nullptr) *) {}

int main() { return 0; }
