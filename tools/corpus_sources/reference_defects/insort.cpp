// `insort`'s first two parameters are of type `I`, its own first template argument.
// g++ writes them as a back-reference to the `T_` entry contributed by `legalize`'s
// signature, which is nested inside `insort`'s second template argument. Resolving that
// entry where it was recorded gives `nn::BB*`; resolving it where it is read gives
// `nn::Update<nn::BB*>*`, which is what the declaration says. llvm-cxxfilt 18 prints the
// former, GNU c++filt 2.42 the latter.
namespace nn {
struct BB;

template <class T>
struct Update {};

template <class L>
struct Wrap {
    L l;
};

template <class I, class C>
void insort(I a, I b, C c);

template <class T>
void legalize(Update<T>* p) {
    auto cmp = [](Update<T> const& a, Update<T> const& b) { return true; };
    insort<Update<T>*, Wrap<decltype(cmp)>>(p, p, Wrap<decltype(cmp)>{cmp});
}

template void legalize<BB*>(Update<BB*>*);
}  // namespace nn
