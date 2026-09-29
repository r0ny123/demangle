## What this changes

<!-- One or two sentences. If it closes an issue, say "Fixes #N". -->

## Checklist

<!-- CONTRIBUTING.md explains each of these. -->

- [ ] `hatch run check` passes (or its steps without Hatch; see CONTRIBUTING.md).
- [ ] New behaviour comes with a test; new *reference-derived* behaviour comes with a
      corpus entry.
- [ ] A conformance number that moved is stated under *Numbers that moved*, which way
      and why, with the pin in `tests/test_conformance.py` updated in the same PR.
- [ ] A benchmark that moved is stated under *Numbers that moved*.
- [ ] A change to a parser's shape rules has been through the fuzzers:
      `tools/enumerate.py`, `tools/mutate.py` and `tools/invariants.py`.

## Numbers that moved

<!-- Conformance counts, benchmark figures, or "none". -->
