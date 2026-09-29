// A non-type template parameter whose *type* mentions the earlier type parameters, and
// whose *value* is a function template specialisation whose signature back-references
// them. clang writes the parameter's type with `Tn`, which puts `T_` and `T0_` into the
// substitution table; `S6_` inside `makeAllOfComposite<TemplateName>`'s signature names
// the `T_` entry, and under that specialisation it is `am::TemplateName`. llvm-cxxfilt 18
// resolves it to `am::Bindable<am::TemplateName>` -- the argument bound to `T_` where the
// entry was made -- and so doubles the wrapper.
//
// Reduced from clang's own `makeMatcherAutoMarshall`, of which 227 instantiations in the
// shipped libclang-cpp are read wrongly by llvm-cxxfilt.
namespace llvmx {
template <class T>
struct ArrayRef {};
}  // namespace llvmx

namespace am {
struct TemplateName {};

template <class T>
struct Bindable {};

template <class T>
struct Matcher {};

template <class T>
Bindable<T> makeAllOfComposite(llvmx::ArrayRef<const Matcher<T>*>) {
    return {};
}

template <class ReturnType, class ArgType, ReturnType (*Func)(llvmx::ArrayRef<const ArgType*>)>
int makeMatcherAutoMarshall(int) {
    return 0;
}

int use() {
    return makeMatcherAutoMarshall<Bindable<TemplateName>, Matcher<TemplateName>,
                                   &makeAllOfComposite<TemplateName>>(0);
}
}  // namespace am
