// C++17/20 constructs, chosen for the manglings they produce rather than for behaviour.
// Concepts, coroutines, fold expressions, deduction guides, structured bindings, spaceship
// and NTTP class types all reach the mangler through productions that the plainer sources
// in features.cpp never exercise.
#if __has_include(<compare>)
#include <compare>
#endif
#if __has_include(<coroutine>) && (defined(__cpp_impl_coroutine) || defined(__cpp_coroutines))
#define MODERN_HAS_COROUTINES 1
#include <coroutine>
#endif
#include <cstddef>
#include <optional>
#include <string>
#include <tuple>
#include <type_traits>
#include <utility>
#include <vector>

// Everything below needs C++17 at least. The generator sweeps older standards too, so
// the file compiles to nothing there rather than failing the build for them.
#if __cplusplus >= 201703L

namespace modern {

// -- concepts and requires-clauses -------------------------------------------
// Guarded so this file still compiles at the older standards the generator sweeps;
// the concept manglings only appear in the C++20 objects, which is the point.
#if defined(__cpp_concepts) && __cpp_concepts >= 201907L
template <typename T>
concept Integral = std::is_integral_v<T>;

template <typename T>
concept Sized = requires(T value) {
    { value.size() } -> std::convertible_to<std::size_t>;
};

template <Integral T> T constrained(T value) { return value + 1; }
template int constrained<int>(int);
template long constrained<long>(long);

template <typename T> requires Sized<T>
std::size_t measured(const T& value) { return value.size(); }
template std::size_t measured<std::string>(const std::string&);

template <typename T> auto abbreviated(T&& value) { return value; }
inline int useAbbreviated(int value) { return abbreviated(value); }

auto terse(Integral auto value) { return value * 2; }
inline int useTerse(int value) { return terse(value); }
#endif  // __cpp_concepts

// -- fold expressions and variadics ------------------------------------------
template <typename... Ts> auto summed(Ts... values) { return (values + ... + 0); }
inline int useSummed() { return summed(1, 2, 3) + static_cast<int>(summed(1.0, 2.0f)); }

template <typename... Ts> struct Bundle {
    std::tuple<Ts...> items;
    static constexpr std::size_t count = sizeof...(Ts);
    auto first() const { return std::get<0>(items); }
};
template struct Bundle<int, char, double>;
// Not explicitly instantiated: that would instantiate `first()` too, and `std::get<0>`
// on an empty tuple is deleted. Naming the type is enough to get its manglings, and an
// empty pack in a template argument list is exactly the shape worth capturing.
inline std::size_t emptyBundleCount() {
    Bundle<> bundle;
    return bundle.count + sizeof(bundle);
}

// -- non-type template parameters --------------------------------------------
template <auto Value> struct Fixed { static constexpr auto value = Value; };
template struct Fixed<42>;
template struct Fixed<'x'>;
template struct Fixed<true>;

template <std::size_t N> struct Buffer { char storage[N]; };
template struct Buffer<16>;

// -- spaceship ---------------------------------------------------------------
#if defined(__cpp_impl_three_way_comparison) && __cpp_impl_three_way_comparison >= 201907L
struct Ordered {
    int value;
    auto operator<=>(const Ordered&) const = default;
    bool operator==(const Ordered&) const = default;
};
std::strong_ordering compare(const Ordered& a, const Ordered& b) { return a <=> b; }
#endif

// -- coroutines --------------------------------------------------------------
#ifdef MODERN_HAS_COROUTINES
struct Task {
    struct promise_type {
        Task get_return_object() { return {}; }
        std::suspend_never initial_suspend() noexcept { return {}; }
        std::suspend_never final_suspend() noexcept { return {}; }
        void return_void() {}
        void unhandled_exception() {}
    };
};
Task coroutine(int count) {
    for (int index = 0; index < count; ++index) co_await std::suspend_never{};
}
#endif

// -- structured bindings and lambdas -----------------------------------------
auto structured() {
    auto [first, second] = std::pair<int, double>{1, 2.0};
    return first + static_cast<int>(second);
}

#if defined(__cpp_generic_lambdas) && __cpp_generic_lambdas >= 201707L
auto genericLambda() {
    auto lambda = []<typename T>(T value) { return value + value; };
    return lambda(21);
}
#endif

auto nestedLambdas() {
    auto outer = [](int a) { return [a](int b) { return [a, b](int c) { return a + b + c; }; }; };
    return outer(1)(2)(3);
}

// -- noexcept, ref-qualifiers, trailing returns ------------------------------
struct Qualified {
    void plain() noexcept;
    void lref() & noexcept;
    void rref() && noexcept;
    auto trailing() const -> std::optional<std::vector<int>>;
};
void Qualified::plain() noexcept {}
void Qualified::lref() & noexcept {}
void Qualified::rref() && noexcept {}
auto Qualified::trailing() const -> std::optional<std::vector<int>> { return {}; }

#if defined(__cpp_noexcept_function_type)
void takesNoexceptPointer(void (*)() noexcept);
void takesNoexceptPointer(void (*)() noexcept) {}
#endif

// -- inline namespaces and nested inline -------------------------------------
inline namespace v2 {
namespace deep { namespace deeper {
struct Nested { int method() const; };
int Nested::method() const { return 0; }
} }  // namespace deep::deeper
}  // namespace v2

}  // namespace modern

#endif  // __cplusplus >= 201703L

int main() { return 0; }
