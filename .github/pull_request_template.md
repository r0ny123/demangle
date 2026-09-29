## What this changes

<!-- One or two sentences. If it closes an issue, say "Fixes #N". -->

## Checklist

<!-- CONTRIBUTING.md explains each of these. -->

- [ ] `hatch run check` passes, or `ruff check . && ruff format --check . && ty check .`,
      `pytest`, `python tools/differential.py` and `python benchmarks/bench.py --check`
      all do.
- [ ] New behaviour comes with a test; new *reference-derived* behaviour comes with a
      corpus entry.
- [ ] A conformance number that moved is stated in the PR description, which way and
      why, with the pin in `tests/test_conformance.py` updated in the same PR.
- [ ] A benchmark that moved is stated in the PR description.
- [ ] A change to a parser's shape rules has been through the fuzzers:
      `tools/enumerate.py`, `tools/mutate.py` and `tools/invariants.py`.

## Numbers that moved

<!-- Conformance counts, benchmark figures, or "none". -->
