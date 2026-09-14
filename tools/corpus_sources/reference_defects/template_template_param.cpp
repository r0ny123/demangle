// A template template parameter applied to arguments contributes *two* substitution
// entries: the parameter, which 5.1.10 names as a candidate in its own right, and the
// specialisation built over it, which is a <type>. `llvm-cxxfilt` 18 records only the
// second, so every back reference at or after that index is one entry out.
//
// `f` is the shape where the shift runs off the end and the reference refuses the name
// outright; `g` is the shape where it lands one short and answers `char<int>`, which is
// not a type. g++ 13.3.0 and clang++ 18.1.3 emit byte-identical manglings for both.
// GNU c++filt 2.42 agrees with the declarations, and so does this library.

template <class T>
struct A {
  T v;
};

template <template <class> class C, class T>
void f(C<T>, C<T>, C<T>) {}

template <template <class> class C, class T>
void g(C<T>, C<int>) {}

void use() {
  f(A<int>{}, A<int>{}, A<int>{});
  g(A<char>{}, A<int>{});
}
