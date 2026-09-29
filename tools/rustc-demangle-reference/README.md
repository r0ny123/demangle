# The Rust reference

`rustc-demangle` is the implementation the Rust project ships and everyone's tooling
uses, and `src/demangle/schemes/rust/_v0.py` is a port of it. `llvm-cxxfilt` and
`c++filt` each carry their *own* Rust reader -- LLVM's is a port of an older
rustc-demangle and binutils' is independent -- so where the three disagree, neither of
the two on this box is the one to follow.

This is a small front end over the crate itself, so the differential tools can ask
it the same way they ask the C++ demanglers. It is not built by default and is not a
dependency of the test suite; the tools use it when it is there and fall back to
`llvm-cxxfilt` when it is not.

    cargo build --release --manifest-path tools/rustc-demangle-reference/Cargo.toml

`target/` is ignored. Building it needs a Rust toolchain and one fetch from crates.io.
