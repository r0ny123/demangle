// The parameter of `Prep`'s constructor is `Callable&`, and `Callable` is deduced as the
// closure type declared in `callit`. g++ writes that parameter as `R` applied to a
// back-reference, because the `T_` node inside `callit`'s own signature is already in the
// substitution table -- both are "template type parameter, level 1, index 0", which the
// mangler canonicalises to one node. Resolving the entry where it was recorded gives
// `void (&)()`; resolving it where it is read gives the closure type, which is what the
// declaration says.
extern void (*slot)();

struct Outer {
    struct Prep {
        template <class Callable>
        explicit Prep(Callable& c) {
            slot = [] {};  // captureless, so it gets a `_FUN` invoker of its own
        }
    };
};

template <class F>
void callit(F&& f) {
    auto closure = [&] { f(); };
    Outer::Prep p(closure);
}

void target();

void use() { callit(target); }
