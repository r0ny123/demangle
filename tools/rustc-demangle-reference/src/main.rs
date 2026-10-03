//! One symbol per line in, one spelling per line out (unreadable names unchanged).
//!
//! `{:#}` is rustc-demangle's "no hash" formatting, as `rustfilt` prints and this library
//! spells by default; `--keep-hash` asks for `{}`, which `demangle --keep-hash` is scored
//! against.

use std::io::{self, BufRead, Write};

fn main() {
    let keep_hash = std::env::args().any(|flag| flag == "--keep-hash");
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = io::BufWriter::new(stdout.lock());
    for line in stdin.lock().lines() {
        let name = line.expect("stdin is not utf-8");
        match rustc_demangle::try_demangle(&name) {
            Ok(demangled) => {
                if keep_hash {
                    writeln!(out, "{}", demangled)
                } else {
                    writeln!(out, "{:#}", demangled)
                }
            }
            Err(_) => writeln!(out, "{}", name),
        }
        .expect("stdout closed");
    }
}
