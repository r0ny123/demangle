# Contributing

The project is meant to be approachable one piece at a time. Adding a scheme, fixing a
spelling, or contributing a corpus should not require understanding the whole codebase.

## Getting set up

The project builds with [Hatch](https://hatch.pypa.io/) and installs with
[uv](https://docs.astral.sh/uv/). You need neither -- plain pip works -- but together
they turn the whole check into one command that finishes in seconds.

```console
git clone https://github.com/r0ny123/demangle
cd demangle

# Creates the environment and runs everything a pull request must pass.
hatch run check
```

The pieces are available on their own: `hatch run test`, `cover`, `lint`, `fmt`,
`bench`, `differential`. `hatch run test:test` runs the suite across Python 3.11 through
3.13, and `hatch run docs:serve` previews the documentation site.

Without Hatch:

```console
uv pip install -e . --group dev     # or: pip install --group dev  (pip 25.1+)
pytest
```

Development requirements are [dependency groups](https://peps.python.org/pep-0735/),
not extras. An extra is a *published* feature of the distribution -- `pip install
demangle[dev]` would appear on PyPI as something users are invited to install -- and a
linter is not a feature of a demangler. The groups are `test`, `format`, `lint`, `docs`,
and `dev`, which includes the first three.

The toolchain is deliberately the fast one: **ruff** for linting and formatting (it
replaces black, isort, flake8 and pyupgrade), **ty** for type checking (it replaces
mypy), **uv** for installing. Both checkers are written in Rust and cover the whole tree
in well under a second, which is what makes it reasonable to gate every commit on them.
Both are pinned exactly; Dependabot proposes the bumps.

The reference demanglers are optional but useful. On Debian or Ubuntu:

```console
apt-get install llvm clang g++ binutils
```

## Reporting a conformance bug

The most valuable report is small and complete:

1. the mangled name,
2. what the reference demangler prints (`llvm-cxxfilt`, `llvm-undname`, or
   `rustc-demangle`),
3. what this library prints.

Add the pair to the matching file in `tests/conformance/` and bump the pinned count in
`tests/test_conformance.py` in the same commit, so the expected number always matches
what the suite actually achieves.

## The rules that matter

Most of the codebase is ordinary Python. Three rules are not negotiable, because the
design rests on them. Two are fully enforced by a test; the first is enforced as far as
a test can reach, and ARCHITECTURE.md says where it does not:

1. **Parsers never build their own output.** Write against the `Builder` protocol
   (`core/builder.py`). Building a string directly is locally convenient and breaks both
   `parse()` and every future output format.
2. **`core` never imports a scheme, and schemes never import each other.** A scheme has
   to stay replaceable in isolation.
3. **No third-party dependencies.** The dependency-free promise is the reason a lot of
   people can use this at all. CI asserts it against a clean install of the built wheel,
   not just against the source tree.

## Working on the Itanium parser

Substitution numbering is where correctness lives. The rules are ABI section 5.1.10 and
are implemented in `schemes/itanium/substitutions.py`, which rejects any attempt to
record a production the specification does not call a candidate.

The specification's prose is genuinely ambiguous in places. Do not guess — ask the
reference implementation. `tools/probe_substitutions.py` appends `S_`, `S0_`, `S1_` to a
name under construction, which makes the reference print its own substitution table
back at you. Several decisions in the parser were settled that way, and each carries a
comment naming the probe that settled it.

## Adding a scheme

See [docs/adding-a-scheme.md](docs/adding-a-scheme.md). In short: three functions and a
`LanguagePlugin`, in a new directory under `src/demangle/schemes/`.

## Before you open a pull request

```console
hatch run check
```

or, without Hatch:

```console
ruff check . && ruff format --check . && ty check .
pytest
python tools/differential.py
python benchmarks/bench.py --check
```

`tools/differential.py` with no arguments replays every checked-in corpus under the
style and language it was recorded with, and exits non-zero if anything disagrees.

If a change moves a conformance number, say which way and why in the commit message.
If it moves a benchmark, say that too.

## CI and workflows

Workflows run with `permissions: contents: read` and grant more only where a job needs
it. Checkout uses `persist-credentials: false`, because nothing in CI pushes and a token
left in `.git/config` is readable by every later step. Releases publish through PyPI
Trusted Publishing, so there is no long-lived API token in repository secrets.

Two rules for anyone editing `.github/`:

- Never use `pull_request_target`. It runs with a writable token against the fork's
  code, which makes any pull request arbitrary code execution with our secrets.
- Never interpolate untrusted values -- issue titles, branch names, PR bodies -- into a
  `run:` block. Pass them through `env:` and reference the variable.

## Style

- Comments explain *why*, especially where the code looks odd because a specification or
  a reference implementation says so. Quote the section.
- Names are spelled out. This is a codebase people read while holding an ABI document in
  the other hand; `substitution_table` beats `st`.
- New behaviour comes with a test. New *reference-derived* behaviour comes with a corpus
  entry.
