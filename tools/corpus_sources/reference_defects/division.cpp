template <int N> struct I {};
template <class T> void half_more(I<(sizeof(T) + 1) / 2>) {}
template <class T> void quarter(I<sizeof(T) / 2 / 2>) {}
void use() { half_more<int>({}); quarter<int>({}); }
