//! One symbol per line in, one spelling per line out; a name it cannot read comes back
//! unchanged. That is the shape `llvm-cxxfilt` and `c++filt` answer in, so the
//! differential tools can ask this the same way they ask those.
//!
//! `{:#}` is rustc-demangle's own "no hash" formatting -- `foo` rather than
//! `foo::h05af221e174051e9` -- which is what `rustfilt` prints and what this library
//! spells by default.

use std::io::{self, BufRead, Write};

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    for line in stdin.lock().lines() {
        let name = line.expect("stdin is not utf-8");
        match rustc_demangle::try_demangle(&name) {
            Ok(demangled) => writeln!(out, "{:#}", demangled),
            Err(_) => writeln!(out, "{}", name),
        }
        .expect("stdout closed");
    }
}
