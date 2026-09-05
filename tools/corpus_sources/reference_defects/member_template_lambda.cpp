// A generic lambda inside a function template, whose own `auto` parameter is written
// as a back reference to the enclosing template's `T_`. Both compilers canonicalise a
// template type parameter by level and index, so the closure's implicit `auto` -- the
// first parameter of its own invented template -- is the same node to them as `g`'s
// `T`, which entered the substitution table in `g`'s signature. `UlS1_E_` names that
// entry, and read under the closure it is the closure's `auto`; llvm-cxxfilt 18 prints
// what the entry was bound to where it was made, `int`, and spells `[](auto x)` as
// `'lambda'(int)`. GNU c++filt 2.42 agrees with the declaration.
//
// Four shapes: the parameter alone, after a parameter that *is* `T`, under `const&`,
// and with a pack. Every one of them is emitted identically by g++ 13.3.0 and
// clang++ 18.1.3.
struct S {
    template <class T> void g(T) {
        auto a = [](auto x) { return x; };
        a(1);
        auto b = [](T x, auto y) { return y; };
        b(1, 2);
        auto d = [](const auto &x) { return x; };
        d(1);
    }
};
template void S::g<int>(int);

template <class T> void f(T &&) {
    auto c = [](auto &&a, auto... b) {};
    c(1);
}
template void f<int>(int &&);
