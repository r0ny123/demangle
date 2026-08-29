// `operator()<int>` of a generic lambda. Its parameter is the lambda's own `auto`
// parameter, written `T_` where the lambda is declared and entering the substitution
// table there; `S0_` in the signature of `operator()<int>` names that entry, and under
// that specialisation it is `int`. llvm-cxxfilt 18 prints `auto`.
namespace modern {
void genericLambda() {
    auto f = [](auto x) { return x; };
    f(1);
}
}  // namespace modern
