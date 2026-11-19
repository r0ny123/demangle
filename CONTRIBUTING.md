# Contributing

You can work on this one piece at a time. Adding a scheme, fixing a spelling or
contributing a corpus shouldn't need the whole codebase in your head.

Everyone taking part follows the [Code of Conduct](https://github.com/r0ny123/demangle/blob/main/CODE_OF_CONDUCT.md).

## Getting set up

The project builds with [Hatch](https://hatch.pypa.io/) and installs with
[uv](https://docs.astral.sh/uv/). Plain pip works too, but with those two the local
check is one command.

```console
git clone https://github.com/r0ny123/demangle
cd demangle

# Creates the environment, then runs lint, the tests, the differential check and the
# benchmark gate. CI runs more; see "Before you open a pull request".
hatch run check
```

The pieces are available on their own: `hatch run test`, `cover`, `lint`, `fmt`,
`bench`, `differential`. `hatch run test:test` runs the suite on Python 3.11 to 3.14 and
PyPy 3.11, and `hatch run docs:serve` previews the documentation site.

Use `hatch run quick` while iterating (about a minute) and `hatch run check` before
pushing (about seven minutes on four cores). `quick` skips the tests marked `sweep`,
the properties checked over every corpus name, which are most of the suite's time. CI
runs everything.

Without Hatch:

```console
uv pip install -e . --group dev     # or: pip install -e . --group dev  (pip 25.1+)
pytest
```

Development requirements are [dependency groups](https://peps.python.org/pep-0735/),
not extras, so PyPI doesn't offer users a `demangle[dev]`. The groups are `test`,
`format`, `lint`, `docs`, `browser`, and `dev`, which includes the first three.

The browser suite is opt-in: it needs the browser dependency group, a built docs site,
and Chromium. It uses a deterministic fake worker for UI regressions; the real Pyodide
smoke test is separately opt-in.

```console
uv pip install -e . --group browser --group docs --group test
python -m playwright install chromium
python docs/build.py
DEMANGLE_BROWSER_TESTS=1 python -m pytest -q tests/browser/test_playground_browser.py

# Also start the real Pyodide runtime the build fetched, as the browser CI job does.
DEMANGLE_BROWSER_TESTS=1 DEMANGLE_PYODIDE_SMOKE=1 \
  python -m pytest -q tests/browser/test_playground_browser.py
```

So is the Ghidra test, which runs `integrations/demangle_ghidra.py` in a real headless
Ghidra: it compiles a small C++ program, imports it with analysis off, runs the script
twice, and checks the symbol table after each run. It needs Ghidra 11.3 or later with a
JDK it runs on, the `pyghidra` package for that Ghidra in the same environment as
`demangle`, and `g++`, `c++` or `clang++` on PATH; without any of them, or without both
variables below, it is skipped.

```console
pip install pyghidra
GHIDRA_INSTALL_DIR=/path/to/ghidra DEMANGLE_GHIDRA_TESTS=1 \
  python -m pytest -q tests/test_integrations_ghidra_live.py
```

Linting and formatting are **ruff**, type checking is **ty**. Both run over the whole
tree in a few seconds, and both are pinned exactly, because they add rules in patch
releases; Dependabot proposes the bumps.

Docstrings under `src/` follow the Google convention, which ruff's `D` rules check: a
one-line summary ending in a full stop, a blank line, then the rest, with `Args:`,
`Returns:` and `Raises:` sections where they say something the summary does not.

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

Most of the codebase is ordinary Python. Three rules aren't negotiable, because the
design rests on them. Tests enforce the last two fully and the first as far as a test
can reach:

1. **Parsers never build their own output.** Write against the `Builder` protocol
   (`core/builder.py`); [ARCHITECTURE.md](ARCHITECTURE.md#parsers-write-to-a-builder)
   says why, and what the test can't catch.
2. **`core` never imports a scheme, and schemes never import each other**
   ([ARCHITECTURE.md](ARCHITECTURE.md#layout)).
3. **No third-party dependencies.** It's why a lot of people can use this at all. CI
   checks it against a clean install of the built wheel, not just the source tree.

## Working on the Itanium parser

Substitution numbering is the part to get right;
[ARCHITECTURE.md](ARCHITECTURE.md#substitutions) has the rules.

The specification is ambiguous in places. Don't guess, ask the reference.
`tools/probe_substitutions.py` appends `S_`, `S0_`, `S1_` to a name under construction,
which makes the reference print its own substitution table back at you. Several
decisions in the parser were settled that way, and each has a comment naming the probe
that settled it.

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
the cross-version reference check, the docs build, and a build of the
distributions with the wheel installed in a clean environment, so a pull request can fail
there with `check` passing.

`tools/differential.py` with no arguments replays every checked-in corpus under the
style and language it was recorded with, and exits non-zero if anything disagrees.

`benchmarks/bench.py --check` is the gate. Two more scripts measure what it does not,
and neither runs in CI: `benchmarks/end_to_end.py` times start-up, batches, tree
serialisation and the command in a pipe, with `--source` to point it at another
checkout, and `benchmarks/bench_playground_browser.py` times the built playground in
Chromium, with `--pyodide-version` to try a different runtime.

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

A change to a parser's shape rules, meaning what it accepts, not how it spells it,
has been through `tools/enumerate.py`, `tools/mutate.py` and `tools/invariants.py`.
[Fuzzing and the reference demanglers](https://github.com/r0ny123/demangle/blob/main/docs/testing.md)
has why, what each asks, and how to build the references for Rust, Swift and
pre-Itanium C++ that no distribution ships.

## Preparing a release

Once the Unreleased section has notes worth publishing:

```console
python tools/prepare_release.py X.Y.Z --date YYYY-MM-DD
```

That updates the package and citation versions, moves the notes under a dated changelog
heading, refreshes the comparison links and runs the documentation metadata checks,
restoring the files if they fail. The version has to be a stable `X.Y.Z`, and `--date`
defaults to today.

Merge the change to `main` through a pull request. `tag-release.yml` creates the tag
and the release once push CI passes for that exact commit, then starts the PyPI
publishing workflow. If that start fails, rerun the tag-release job; an existing
release doesn't stop it. Active or successful publishing runs are left alone, failed
ones retry only their failed jobs, and a cancelled one needs a manual restart.

## How changes get merged

Pull request titles and commit subjects follow
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), such as
`fix(msvc): count scopes against max_output`, and `commits.yml` checks both. A squash
merge takes its subject from the title, and a release is `chore(release): X.Y.Z`.

Leave `CHANGELOG.md` alone: the maintainer writes the entry when merging. A first-time
contributor's CI runs once a maintainer approves it, so the first push can sit for a
while before any checks appear. The easiest first contribution is a
[reported name](#reported-names): a report, or a fix with its line in
`tests/conformance/reported/`.

## CI and workflows

Workflows run with `permissions: contents: read` and grant more only where a job needs
it: the weekly `upstream.yml` gives `issues: write` to its `drift` job alone, because that
job files the issue that says a reference's vectors moved ahead of a scheme; its `llvm`
job, which runs a third party's native wheel, has `contents: read` and no secret, and its
wheel is pinned by hash. Checkout uses
`persist-credentials: false`, because nothing in CI pushes and a token left in
`.git/config` is readable by every later step. `tag-release.yml` cuts a release after
push CI succeeds on `main` for the exact commit, through the API with `contents: write`
and `actions: write` on that one job; it starts `release.yml`, which publishes through
PyPI Trusted Publishing, so there is no long-lived API token in repository secrets. Its
`attest` job signs GitHub build provenance for every file first, with `id-token: write`
and `attestations: write`. The `pypi` environment still waits for a maintainer to approve.

The test matrix isn't a cross product. Every supported version runs on Linux, PyPy
3.11 included. macOS and Windows get one row each, the newest and the oldest Python,
and one more row runs the free-threaded 3.14t build for `tests/test_concurrency.py`.
Everything that has ever differed between platforms differed in the harness (a glob, a
subprocess, a console encoding), and one row per operating system catches that as well
as a cross product would.

The compiled wheels are the exception, because they are platform code: `wheels.yml`
builds and tests one for every platform and Python they ship for, and the `compiled`
job runs the whole suite and the Itanium fuzzers against one.

Each extra row has a comment in `ci.yml` saying why it's there. Add a row when a
defect shows up that only that row would have caught, and say so in its comment.

Two rules for anyone editing `.github/`:

- Never use `pull_request_target`. It runs with a writable token against the fork's
  code, which makes any pull request arbitrary code execution with our secrets.
- Never interpolate untrusted values (issue titles, branch names, PR bodies) into a
  `run:` block. Pass them through `env:` and reference the variable.

## Style

- Comments explain *why*, especially where the code looks odd because a specification or
  a reference implementation says so. Quote the section.
- Names are spelled out. This is a codebase people read while holding an ABI document in
  the other hand; `substitution_table` beats `st`.
