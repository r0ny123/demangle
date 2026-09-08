// GCC 12's closure-prefix numbering. Compile with
//
//     g++ -std=c++20 -fabi-version=17 -fabi-compat-version=17 -c closure_prefix.cpp
//
// for the old spelling (GCC 12's default, and every version before it), and with
// `-fabi-version=18` -- GCC 13's default -- or clang++ for the ABI's, which records the
// prefix before the `M` as a substitution candidate. GCC 13 without the flags emits
// both, the old as an alias. The corpus rows are the old spellings; their expected
// column is what the declarations below say the parameters are.
namespace ns {
template <class T> struct Box {};
inline auto g1 = [](auto a, Box<int> b, Box<int> c) { return a; };
inline auto g3 = [](Box<int> b, Box<int> c) { return 1; };
inline auto g4 = [](auto a, auto b, Box<int> c, Box<int> d) { return a; };
inline auto g5 = []<class... Ts>(Ts... ts, Box<int> c, Box<int> d) { return 1; };
inline auto nested = [](Box<int> b) { return [](Box<int> c, Box<int> d) { return 1; }; };
int use() { Box<int> b; return g1(1, b, b) + g3(b, b) + g4(1, 2.0, b, b) + g5.operator()<int>(1, b, b) + nested(b)(b, b); }
}
