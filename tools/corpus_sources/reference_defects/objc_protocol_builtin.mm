// clang++ -target arm64-apple-macos13 -fobjc-arc -c objc_protocol_builtin.mm
//
// Under protocols clang writes `id` and `Class` as the builtins `11objc_object` and
// `10objc_class`, which enter no substitution; only the protocol-qualified type does.
// The last is React Native's `RCTBridgingToVec`, as it appears in the SeaFlower macOS
// sample's symbol table.

@protocol P @end
@protocol NSObject @end
@interface NSArr<__covariant T> @end

void p(id<P> a, id<P> b) {}
void c1(Class<P> a, Class<P> b) {}
void i3(id<P> a, id b, id c) {}
void a2(NSArr<id<P>> *a, id<P> b) {}

namespace FB {
template <typename T, typename U> struct LazyVector {};
}
template <typename T> FB::LazyVector<T, id> RCTBridgingToVec(id v, T (^ctor)(id)) { return {}; }
template FB::LazyVector<id<NSObject>, id> RCTBridgingToVec(id, id<NSObject> (^)(id));
