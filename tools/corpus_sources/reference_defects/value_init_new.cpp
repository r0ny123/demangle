// `new T()` value-initialises the object; `new T` leaves it indeterminate. The mangling
// carries the difference as `pi E`, an empty parenthesised initialiser, after the type.
template <class T> auto value_init(T t) -> decltype(new T()) { return new T(); }
void use() { value_init(1); }
