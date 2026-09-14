// An inheriting constructor's `<base class type>` is a `<type>`, which 5.1.10 makes a
// substitution candidate -- and the two compilers disagree about whether to enter it.
//
// g++ 13.3.0 enters it and writes `_ZN1DCI21CENS0_4KindES1_`, whose `S0_` is that entry;
// clang++ 18.1.3 does not, and spells `C` again: `_ZN1DCI21CEN1C4KindES1_`, whose `S1_`
// is therefore one entry further along. Each name is unreadable under the other's rule.
// `llvm-undname`'s Itanium counterpart, `llvm-cxxfilt` 18, implements clang's and
// refuses g++'s outright; GNU `c++filt` 2.42 reads both parameter lists but names the
// constructor after the *base*, `D::C`, which is neither compiler's declaration.
//
// Both parameters below are `C::Kind`, which is what makes the disagreement visible: a
// reader that applies the wrong rule prints the second one as `C`.

struct C {
  struct Kind {
    int v;
  };
  C(Kind);
  C(Kind, Kind);
};

struct D : C {
  using C::C;
};

D one(C::Kind k) { return D(k); }
D two(C::Kind k) { return D(k, k); }
