// The same defect as insort.cpp, one declarator deeper. `insort`'s parameters are
// `Update<I>*` rather than `I`, so the entry g++ back-references is the composite
// `P N S1_ I T_ E E` built over the parameter rather than the bare parameter. The
// mangler reuses it across template scopes for the same reason -- it canonicalises a
// template type parameter by level and index, so the composite over one canonicalises
// the same way. With `I = nn::Update<nn::BB*>*` the parameters are
// `nn::Update<nn::Update<nn::BB*>*>*`; llvm-cxxfilt 18 prints `nn::Update<nn::BB*>*`,
// and GNU c++filt 2.42 prints what the declaration says.
namespace nn {
struct BB;

template <class T>
struct Update {};

template <class L>
struct Wrap {
    L l;
};

template <class I, class C>
void insort(Update<I>* a, Update<I>* b, C c);

template <class T>
void legalize(Update<T>* p) {
    auto cmp = [](Update<T> const& a, Update<T> const& b) { return true; };
    Update<Update<T>*>* q = nullptr;
    insort<Update<T>*, Wrap<decltype(cmp)>>(q, q, Wrap<decltype(cmp)>{cmp});
}

template void legalize<BB*>(Update<BB*>*);
}  // namespace nn
