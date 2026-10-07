# Contributing

The project is meant to be approachable one piece at a time. Adding a scheme, fixing a
spelling, or contributing a corpus should not require understanding the whole codebase.

Everyone taking part follows the [Code of Conduct](https://github.com/r0ny123/demangle/blob/main/CODE_OF_CONDUCT.md).

## Getting set up

The project builds with [Hatch](https://hatch.pypa.io/) and installs with
[uv](https://docs.astral.sh/uv/). You need neither -- plain pip works -- but together
they turn the local check into one command.

```console
git clone https://github.com/r0ny123/demangle
cd demangle

# Creates the environment, then runs lint, the tests, the differential check and the
# benchmark gate. CI runs more; see "Before you open a pull request".
hatch run check
```

The pieces are available on their own: `hatch run test`, `cover`, `lint`, `fmt`,
`bench`, `differential`. `hatch run test:test` runs the suite on Python 3.13 and 3.14,
and `hatch run docs:serve` previews the documentation site.

`hatch run quick` while iterating (about a minute); `hatch run check` before pushing
(about seven minutes on four cores). `quick` skips the tests marked `sweep` --
properties checked over every corpus name, which are most of the suite's time -- and CI
runs everything.

Without Hatch:

```console
uv pip install -e . --group dev     # or: pip install -e . --group dev  (pip 25.1+)
pytest
```

Development requirements are [dependency groups](https://peps.python.org/pep-0735/),
not extras. An extra is a *published* feature of the distribution -- `pip install
demangle[dev]` would appear on PyPI as something users are invited to install -- and a
linter is not a feature of a demangler. The groups are `test`, `format`, `lint`, `docs`,
and `dev`, which includes the first three.

The toolchain is deliberately the fast one: **ruff** for linting and formatting (it
replaces black, isort, flake8 and pyupgrade), **ty** for type checking (it replaces
mypy), **uv** for installing. Both checkers are written in Rust: ruff covers the whole
tree in under a second and ty in a couple of seconds, which is what makes it reasonable
to gate every commit on them. Both are pinned exactly; Dependabot proposes the bumps.

The reference demanglers are optional but useful. On Debian or Ubuntu:

```console
apt-get install llvm clang g++ binutils
```

## Reporting a conformance bug

Use the [conformance bug form](https://github.com/r0ny123/demangle/issues/new?template=conformance-bug.yml):
the name, what the reference prints, and what this library prints.
`python tools/repro.py NAME` prints all three -- this library in every style, and every
reference demangler for the scheme that is installed or built under `tools/` -- then a
report body ready to paste and the corpus line for the fix.

### Reported names

A fix for a reported name adds that line to `tests/conformance/reported/<scheme>.txt`,
where `<scheme>` is the language name (`demangle --list-languages`). One row per name:
the mangled name, a tab, the spelling the reference prints in the llvm style. A row
recorded in another style goes in `<scheme>-<style>.txt` instead. A comment line
`# #123` above a row names its issue. There is no count to update: every row must read
as recorded, under the language the file is named for, and a file for a new scheme is
picked up without an edit.

## The rules that matter

Most of the codebase is ordinary Python. Three rules are not negotiable, because the
design rests on them. Two are fully enforced by a test; the first is enforced as far as
a test can reach, and [ARCHITECTURE.md](ARCHITECTURE.md) says where it does not:

1. **Parsers never build their own output.** Write against the `Builder` protocol
   (`core/builder.py`); ARCHITECTURE.md says why.
2. **`core` never imports a scheme, and schemes never import each other**
   ([ARCHITECTURE.md](ARCHITECTURE.md#layout)).
3. **No third-party dependencies.** The dependency-free promise is the reason a lot of
   people can use this at all. CI asserts it against a clean install of the built wheel,
   not just against the source tree.

## Working on the Itanium parser

Substitution numbering is where correctness lives; [ARCHITECTURE.md](ARCHITECTURE.md)
has the rules.

The specification's prose is genuinely ambiguous in places. Do not guess -- ask the
reference implementation. `tools/probe_substitutions.py` appends `S_`, `S0_`, `S1_` to a
name under construction, which makes the reference print its own substitution table
back at you. Several decisions in the parser were settled that way, and each carries a
comment naming the probe that settled it.

## Adding a scheme

See [Adding a scheme](https://github.com/r0ny123/demangle/blob/main/docs/adding-a-scheme.md).

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

That is what `check` covers. CI also runs the three fuzzers against the built references,
the cross-version reference check, `mkdocs build --strict`, and a build of the
distributions with the wheel installed in a clean environment, so a pull request can fail
there with `check` passing.

`tools/differential.py` with no arguments replays every checked-in corpus under the
style and language it was recorded with, and exits non-zero if anything disagrees.

The [pull request template](https://github.com/r0ny123/demangle/blob/main/.github/pull_request_template.md)
has the checklist. What its items ask for, and why:

### Tests and corpus entries

New behaviour comes with a test. Behaviour taken from a reference demangler comes with a
corpus entry as well: the name and the reference's spelling, in the matching file under
`tests/conformance/`, so the claim is checked against what the reference said rather
than against what the test's author believed it said.

### Conformance numbers

The pass counts of the corpora outside `reported/` are pinned as exact numbers in
`tests/test_conformance.py` or the scheme's own test module, so a count that moves in
either direction fails the suite, listing the first ten names that miss and the
`tools/differential.py` command that shows them all. A pull request that moves one
updates the pin in the same pull request and says in its description which way the
number moved and why; so does one that moves a benchmark.

### The fuzzers

A change to a parser's *shape rules* -- what it accepts, rather than how it spells what
it accepts -- has been through `tools/enumerate.py`, `tools/mutate.py` and
`tools/invariants.py`.
[Fuzzing and the reference demanglers](https://github.com/r0ny123/demangle/blob/main/docs/testing.md)
has why, what each asks, and how to build the references for Rust, Swift and
pre-Itanium C++ that no distribution ships.

## How changes get merged

Leave `CHANGELOG.md` alone: the maintainer writes the entry when merging. A first-time
contributor's CI runs once a maintainer approves it, so the first push can sit for a
while before any checks appear. The easiest first contribution is a
[reported name](#reported-names): a report, or a fix with its line in
`tests/conformance/reported/`.

## CI and workflows

Workflows run with `permissions: contents: read` and grant more only where a job needs
it: the weekly `upstream.yml` has `issues: write`, because its one job files the issue
that says a reference's vectors moved ahead of a scheme. Checkout uses
`persist-credentials: false`, because nothing in CI pushes and a token left in
`.git/config` is readable by every later step. Releases publish through PyPI
Trusted Publishing, so there is no long-lived API token in repository secrets.

The test matrix is deliberately not a cross product. Both supported versions run on
Linux; macOS and Windows get one row each, the ceiling and the floor, so neither end of
the range is only ever exercised on Linux. A fifth row runs Ubuntu on the free-threaded
3.14t build, for `tests/test_concurrency.py`. This is a pure-Python library, and
everything that has ever differed between platforms differed in the harness -- a glob, a
subprocess, a console encoding -- which one row per operating system catches as well as
a full cross product would. Each extra row has a comment in `ci.yml` saying why it is
there. Add a row when a defect shows up that only that row would have caught, and say so
in its comment.

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
