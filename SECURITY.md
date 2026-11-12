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
- An exception escaping `demangle()` for any string.
- `RecursionError` or stack exhaustion escaping any entry point.
- Non-deterministic output for identical input.
- Control characters or other unexpected content reaching output that a caller would
  reasonably treat as a plain identifier.

Out of scope:

- Incorrect but harmless spellings. Those are conformance bugs — open a normal issue
  with the mangled name, what the reference demangler prints, and what this prints.
- Resource use above the configured `Limits` when a caller has deliberately raised them.

## Defences

- `demangle()` never raises for a name it cannot read: every such failure returns the
  input unchanged. What it does raise on is a mistake in the call itself — a
  `language` or `style` that does not exist, or a name that is not a string.
- Recursion depth, output length, substitution count and input length are all bounded,
  with defaults set well above anything a real compiler emits, and are configurable
  per call through `Limits`. Every registered scheme is checked against the input bound
  by `tests/test_limits.py`, so a scheme added later has to keep the promise rather than
  quietly not be covered.
- Bounds are enforced *while* a name is read, not checked on the finished result. A
  bound observed only afterwards is a report: a short Rust name can ask for millions of
  bound lifetimes, and the string that the output bound would then reject has already
  been built.
- Hitting a bound ends the reading. It is not a "this name is not mine": the scheme
  claimed the name and then ran out of the budget the caller set, so the name is offered
  to no other scheme, where a laxer one would read the mangling itself and build a
  declaration out of the encoding. A caller who lowers a limit is defending against
  hostile input, which is the last place to start guessing.
- The result cache `demangle()` keeps is bounded in bytes as well as entries, so
  a stream of long hostile names cannot grow it past about 100 MB.
- `KeyboardInterrupt`, `SystemExit` and `MemoryError` are never swallowed by the
  best-effort paths — they mean the process is in trouble, not that a name is
  malformed.
- The library is safe to call from several threads. `tests/test_concurrency.py` asserts
  the strong form — every thread agrees with the single-threaded answer character for
  character — over every scheme's corpus, and CI runs it on a free-threaded build where
  the GIL is not there to hide a shared mutable parser.
- The package has no runtime dependencies, so it contributes no transitive supply chain.
  This is enforced by a test, not just stated.
- The property-based suite runs the parsers against arbitrary text, mangling-alphabet
  text, arbitrary bytes, every truncation of a known-good name, and inputs constructed
  to defeat a naive parser.

### Measured worst case

How much work one symbol can cause is measured rather than assumed, by growing a
repeated unit until it reaches the input bound, for every scheme, and recording wall
time and peak allocation. Apart from the one bounded case below, nothing is
superlinear. The worst shape in the package is a Swift name at the largest length the
default `max_input` allows (64K characters), and it is linear in the input; every other
scheme's worst is cheaper.

That case is quadratic and bounded rather than removed. Every Itanium `<prefix>` is a
substitution candidate and each entry holds the whole prefix, so N components record
O(N²) characters without any single one crossing `max_output`. The characters the table
records are capped at sixteen times the output bound, a hundred times what the largest
of the 217,730 Itanium symbols in a stock Ubuntu 24.04 records.

The fuzzers ([Fuzzing and the reference
demanglers](https://r0ny123.github.io/demangle/testing/)) check on each name that
nothing but a `DemanglingError` escapes, that the tree renders exactly what the text
path spelled in both styles, and that no substitution-table sentinel reaches a builder.
The mutation runs are under an address-space cap, so a runaway allocation reports the
name that caused it rather than being killed.

## How a release is built, and how to check one

- Releases are published from GitHub Actions through PyPI
  [Trusted Publishing](https://docs.pypi.org/trusted-publishers/). There is no PyPI API
  token, in the repository or anywhere else, for anyone to steal; the upload is authorised
  by a short-lived OIDC token that only `release.yml`, running in the `pypi` environment of
  this repository, can obtain, and that environment waits for a maintainer's approval.
- Every file on PyPI carries an attestation that names this repository and that workflow.
  PyPI shows it under *Verified details*, and it can be checked from a shell:

  ```console
  $ python -m pip install pypi-attestations
  $ python -m pypi_attestations verify pypi --repository https://github.com/r0ny123/demangle \
        pypi:demangle-0.7.0-py3-none-any.whl
  OK: demangle-0.7.0-py3-none-any.whl
  ```

- The wheel is reproducible: `uv build` at the release tag, with the hatchling that
  `uv.lock` pins, gives the file on PyPI byte for byte, so its SHA-256 can be compared
  with the one PyPI lists.
- Nothing is resolved while a release is built, or anywhere in CI. uv is installed from
  `tools/uv-requirements.txt` and everything else from `uv.lock`, each file checked against
  a hash that is in the repository, and the build runs without a network. A new release
  of a build or test dependency reaches a workflow only through a reviewed change to those
  files, which Dependabot proposes once the release has been public for a week.
- Every GitHub Action is pinned to a commit, workflows are given a read-only token unless
  a job says otherwise, and no workflow runs code from a fork with a secret or a
  writable token.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.7.x | yes |
| < 0.7 | no |

Until 1.0, security fixes land on the latest released minor version only. There is no
long-term support branch and no backporting; upgrading to the current minor version is
the fix.
