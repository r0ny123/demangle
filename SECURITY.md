# Security policy

## Reporting a vulnerability

Report privately through GitHub's
[security advisory form](https://github.com/r0ny123/demangle/security/advisories/new)
rather than a public issue.

Please include the input that triggers the problem, what happens, and what you expected.
A mangled name that reproduces it is worth more than a description.

## Threat model

This library parses **untrusted input by design**. A mangled name comes out of a symbol
table in a file the calling tool did not produce and cannot vouch for — very often
malware, a crash dump, or a binary from an unknown source. Anything a crafted name can
do to a consuming application is in scope.

Specifically in scope:

- Unbounded memory or CPU use from a short input (algorithmic complexity attacks).
- Uncaught exceptions escaping `demangle()`, which is documented never to raise.
- `RecursionError` or stack exhaustion escaping any entry point.
- Non-deterministic output for identical input.
- Control characters or other unexpected content reaching output that a caller would
  reasonably treat as a plain identifier.

Out of scope:

- Incorrect but harmless spellings. Those are conformance bugs — open a normal issue
  with the mangled name, what the reference demangler prints, and what this prints.
- Resource use above the configured `Limits` when a caller has deliberately raised them.

## Defences

- `demangle()` never raises. Every failure returns the input unchanged.
- Recursion depth, output length, substitution count and input length are all bounded,
  with defaults set well above anything a real compiler emits, and are configurable
  per call through `Limits`.
- `KeyboardInterrupt`, `SystemExit` and `MemoryError` are never swallowed by the
  best-effort paths — they mean the process is in trouble, not that a name is malformed.
- The package has no runtime dependencies, so it contributes no transitive supply chain.
  This is enforced by a test, not just stated.
- The property-based suite runs the parsers against arbitrary text, mangling-alphabet
  text, arbitrary bytes, every truncation of a known-good name, and inputs constructed
  to defeat a naive parser.

## Supported versions

Until 1.0, security fixes land on the latest released minor version only.
