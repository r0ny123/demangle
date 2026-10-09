# Correctness and performance audit — 2026-10-09

Baseline: `ed67b1433db8ae7b91ae323377cd9b437a9149ea`, confirmed against remote
`HEAD` and `main` with `git ls-remote` during this audit. The working tree was clean
at the start. Measurements use CPython 3.13.5 on Linux x86-64. Findings below are
reproduced issues, not a claim that all possible defects have been eliminated.

## Investigation ledger

| Area | Reproduction and impact | Resolution and evidence |
|---|---|---|
| Forced plugin parsing | A plugin raising `ValueError` leaked through strict parsing/signatures when its language was forced, while automatic detection wrapped it. | Consistent chained `ParseError`; preserve existing domain and operational errors. API regressions. |
| Empty-name validation | `demangle("")` silently accepted invalid language, style, or limits. | Validate configuration before returning; valid empty names remain empty. API regressions. |
| CLI stream filtering | Annotations such as `@Override` and ELF version markers were rewritten despite library text filtering preserving them. | Apply the existing suppression policy to stdin text, selection modes, and JSON records. Explicit symbol arguments retain their meaning. CLI regressions. |
| AST extension fields | Legal string `__slots__ = "payload"` was iterated as characters, losing payload in serialization/equality/matching. | Normalize slot declarations, exclude runtime metadata, cache fields per concrete class. Inheritance and serialization regressions. |
| AST pack output bound | `_Z1fIJ` + 20 `i` + `EEviA1234567890_T_` rendered 471 characters but old AST bound was 233; `max_output=300` accepted it. | Carry rendered pack cardinality through distributed declarators. Public parsing and nested/member-pointer bound regressions. |
| AST clone/template bounds | Ten GNU `.cold` suffixes rendered 143 characters with old size 63; an empty GNU operator template rendered 12 with old size 11. | Account for every clone label and GNU opening-angle spacing; public output-limit and direct-builder regressions. |
| Rust legacy lengths | A length with 5,000 leading zeros hit Python's integer-string digit limit despite denoting the same length as `1`. A 60,000-digit invalid length built huge integers. | Bounded decimal accumulation and early impossible-length rejection. Text/tree parity and malformed-length regressions. |
| Rust legacy traversal | Repeated suffix slicing made many-component paths and escaped components quadratic. | Index cursors; all 3,066 legacy corpus entries retain their outputs, plus 50,000 seeded escaped-component differential cases. |
| Objective-C syntax | `-[Foo take:other]` accepted a missing terminal selector colon; `$` regex anchors accepted trailing newlines in multiple symbol forms. | Require complete selectors and exact end anchors. Refusal regressions. |
| Objective-C category splitting | Every underscore revalidated and copied both halves of the remaining identifier. | Validate shared characters once, then find the preferred split and ambiguity. Exhaustive short-body equivalence and long-category regressions. |
| Objective-C selector/type splitting | Each underscore revalidated the selector prefix, creating quadratic work. | Match the valid selector prefix once, then scan preferred split and ambiguity; 97,655 exhaustive split comparisons preserve readings. |
| Swift ELF metadata | Undersized relocation/symbol entries leaked `struct.error` or interpreted unrelated bytes as fields; undersized header entries overlapped records. | Validate ELF64 entry widths and distinguish absent optional sizes from explicit zero. Header, dynamic-table, section-table, and EOF regressions. |
| Benchmark gate | With all phase results absent, the original gate printed “no regression against baseline” and exited 0. | Reject missing baseline phases; regressions cover each phase and all phases missing. |
| Differential replay | Empty/comment-only corpora reported `0/0 exact` and exited 0. | Reject zero aggregate replayed names. Tool subprocess regressions. |
| Invariant corpus selection | An unmatched explicit selector checked zero corpora and reported no invariant broken. | Reject missing usable corpora with argument error. Tool subprocess regressions. |
| Fuzzer workload options | Negative counts/lengths skipped work and returned success. | Reject negative values in invariants, mutate, and enumerate; preserve zero-count dry runs. Tool subprocess regressions. |

## Measurements

Pairs alternate before/after order, on an otherwise idle machine. The same interpreter,
inputs, parser dependencies, and workload sizes are used. Scripts check output parity;
AST serialization additionally checks exact dictionaries. Ranges show observed sample
min/max, not confidence intervals. No committed benchmark baseline was relaxed.

| Workload | Before median (range) | After median (range) | Interpretation |
|---|---|---|---|
| Serialize 5,913 libstdc++ ASTs, 9 pairs | 120.325 ms (118.896–128.567) | 89.087 ms (88.337–90.761) | 25.96% less time |
| Directly parse the same 5,913 ASTs, 9 pairs | 106.074 ms (104.596–109.275) | 109.088 ms (107.923–126.104) | 2.84% more time for corrected accounting |
| Rust path, 16,000 components, 7 pairs | 21.808 ms (21.762–22.159) | 11.642 ms (11.590–11.860) | 46.62% less time |
| Rust component, 14,000 dot segments before an escape, 7 pairs | 18.229 ms (17.377–18.432) | 4.024 ms (3.987–4.362) | 77.92% less time |
| Rust component, 14,000 escapes, 7 pairs | 11.434 ms (11.195–11.748) | 3.330 ms (3.291–3.556) | 70.87% less time |
| Refuse Rust 60,000-digit invalid length, 7 pairs | 241.159 ms (241.002–242.175) | 0.0180 ms (0.0036–0.0328) | Early rejection replaces large-integer arithmetic; microsecond timing is noisy |
| Rust legacy corpus, per name, 7 pairs | 6.792 µs (6.766–7.109) | 6.698 µs (6.666–7.136) | Overlapping variation; no ordinary-workload speedup claim |
| Objective-C ordinary categories, 9 pairs | 7.959 µs (7.898–8.096) | 6.880 µs (6.795–6.978) | 13.56% less time |
| Objective-C category, 1,000 separators, 9 pairs | 3,741.213 µs (3,735.757–3,826.766) | 13.013 µs (12.476–13.288) | 99.65% less time |
| Objective-C selector/types, 1,000 separators, 9 pairs | 4,128.710 µs (4,116.925–4,149.736) | 13.329 µs (12.436–13.879) | 99.68% less time |
| Objective-C invalid selector prefix, 1,000 separators, 9 pairs | 542.876 µs (532.404–681.369) | 8.928 µs (8.481–9.257) | 98.36% less time |
| Objective-C ordinary selector/types, 9 pairs | 7.386 µs (7.340–7.545) | 7.463 µs (7.422–7.514) | Overlapping variation; no improvement claim |

Retained AST node bytes over 32,759 unique libstdc++ nodes increased from 1,866,320 to
1,909,896: +43,576 bytes (+2.33%). This counts `sys.getsizeof` of unique node objects,
excluding containers and allocator overhead. Extra cardinality slots apply to
declarator/pack nodes; leaves and most composite nodes retain their original size.
Long Rust workloads are stress cases within the default input bound, not estimates of
the frequency of such symbols in production.

Reproduce with the checkout's virtualenv (or an equivalent editable development install):

```sh
.venv/bin/python benchmarks/audit_core.py
.venv/bin/python benchmarks/audit_rust.py --repeats 10 --trials 7
.venv/bin/python benchmarks/audit_objc.py
.venv/bin/python benchmarks/bench.py --check
```

The audit scripts default to the immutable baseline revision above and accept
`--before` to select another revision. They print individual timing samples as well as
summary statistics. Exact timings depend on the machine and its load.
The measured samples are retained in `benchmarks/audit_results.json`.

## Verification and coverage

Initial full suite: 4,264 tests and 384,500 subtests passed, two tests skipped. One
failure was stale editable-install metadata (`0.3.0` versus source `0.5.1`); rebuilding
the editable install offline corrected the environment without changing source.
Final integrated results:

- `pytest`: **4,345 passed, two skipped, 384,500 subtests passed** (194.46 seconds).
  The tightened public AST bound tests were additionally rerun: eight passed.
- `ruff check .`, `ruff format --check .`, and `ty check .`: passed.
- `tools/differential.py`: **65,608/65,608 exact**.
- `benchmarks/bench.py --check`: passed without changing the committed baseline.
  Measured throughput was 43,484 cold names/s, 3,946,898 warm names/s, 1,740,514
  negative names/s, and 34,157 structured names/s. These absolute values describe
  this run; they are not before/after speedup claims.
- `tools/invariants.py --count 4000 --seed 23 --quiet`: all selected corpora passed.
  Independent Rust sweeps used seeds 7 and 19; C++/MSVC seed 7; Objective-C used
  20,000 mutants per corpus, other smaller schemes and Swift 2,000 per corpus.
- GNU Ada/D enumeration through length three: 14,742 candidates, zero unexplained
  divergences. Ada/D mutation with seed 23: 20,000 mutants per scheme, zero
  unexplained divergences (one D discrepancy covered by an existing accepted rule).
- Additional structural checks: 3,742 deterministic AST size/render comparisons
  across LLVM/GNU; 9,840 category split comparisons; 97,655 selector split
  comparisons; 5,000 structured ELF metadata/truncation mutations. All passed.
- Offline `uv build`: wheel and source distribution built successfully. New tests
  and benchmark artifacts are present in the source archive. An isolated virtualenv
  installed the wheel without dependencies and passed metadata, API, bytes,
  AST/signature, Rust, output-limit, all-scheme preload, and CLI smoke checks.

Investigation covered API validation, plugin errors, allow-lists, cache keys, bytes and
surrogateescape, decorations, stream chunk boundaries, CLI modes, signatures, AST
fields and output accounting, core registry/cache/style, parser lengths/depth/output,
Swift ELF loading, all smaller schemes, fuzz tooling, packaging, and CI gates.
Targeted suites and seeded mutations supplement frozen conformance corpora.

Rejected: a marker-suppression microoptimization whose measured ranges overlapped,
and speculative timeout/configuration changes without a reproduced failure. Existing
documented reference divergences were preserved rather than changed without evidence.

Limits: this environment has GNU `c++filt` but lacks LLVM demanglers, a Rust reference
toolchain, and `swift-demangle`. Fresh comparison against those external references
cannot be completed here; their frozen corpora still run. Cross-version Python,
Windows/macOS, free-threaded Python, documentation builds, and the full distribution-install
matrix remain CI responsibilities. No claim is
made about unseen production workloads or exhaustive correctness.
