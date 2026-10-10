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

Initial limits: LLVM, Rust and Swift references were missing during the first pass.
The follow-up below resolves those limitations with freshly built native references. Cross-version Python,
Windows/macOS, free-threaded Python, documentation builds, and the full distribution-install
matrix remain CI responsibilities. No claim is
made about unseen production workloads or exhaustive correctness.


## Follow-up investigation — 2026-10-10

Remote main was checked again and remains `ed67b1433db8ae7b91ae323377cd9b437a9149ea`.
The first audit commit is `0f56a94`. The draft PR is
https://github.com/r0ny123/demangle/pull/49. Native references previously missing are
now available: LLVM 19 cxxfilt/undname, rustc-demangle 0.1.28, the pinned Swift
reference, and the GCC 8.3 GNUv2 reference.

| Finding | Resolution and evidence |
|---|---|
| Itanium Unicode lengths | ABI lengths count UTF-8 bytes, whereas Python slicing counted characters. Fixed source names, nested fast paths and ABI tags; native GNU/LLVM examples and partial-byte regressions. |
| D Unicode lengths and offsets | `_D2éi` was refused and `_D1éi` accepted; GNU does the reverse. Reader tracks bytes, decoding identifiers at their boundaries. `_D2éQdi` verifies byte-distance back references. Native probes and regressions pass. |
| GNUv2 Unicode lengths | `f__2éFv` was misread as `éF::f(void)`. Convert ABI byte counts to cursor character counts for length-prefixed names. Classes, nested names, templates, pointer parameters and 30 special-form/style probes match native GNUv2. |
| Stream Unicode boundaries | A Unicode suffix could cause a refused whole name to be demangled partially. Preserve attached non-ASCII token characters; use the original ASCII regex on ASCII lines. API/CLI regressions. |
| CLI error handling | Empty explicit names and failed type/tree/JSON requests could exit successfully. Return failure consistently; CLI regressions. |
| JSON cache retention | Repeated distinct large refused names retained about 50 MB under the count-only cache. Bound retained character storage and individual entry sizes; tracemalloc measures a 67.2% reduction. |
| Duplicate cache updates | Replacing a young entry accumulated its weight repeatedly and aged out neighbors. Track replacement weights; regression verifies retained neighbors and updated values. |
| Shared AST hashing/equality | Small shared Rust graphs caused exponential traversal. Per-call memoization and iterative deep-graph traversal preserve structural hashes and custom overrides; graph/deep-tree/cycle/mutation regressions. |
| Rust v0 limits | Oversized numeric fields consumed unnecessary arithmetic, and memoized back references could bypass depth limits. Bound accumulation and cache subtree height; text/tree depth parity and numeric regressions. Skip long valid zero padding in C. |
| Nim/Pascal end anchors | Trailing newlines could be accepted as complete names. Exact end anchors and refusal regressions. |
| Nim/JNI repeated separators | Malformed separator-heavy names caused quadratic work. Validate suffixes once and prune impossible candidates. Nim compared on 78,124 short bodies; JNI on 55,987 inputs. |
| GNUv2 decimal conversion | Long zero-padded counts hit the interpreter integer-conversion limit. Bound significant-digit conversion; native reference regression. |
| Swift Mach-O bounds | Load commands/segments could read outside their declared command region. Validate command extent, minimum sizes and section counts; focused regressions. |

### Follow-up measurements

Raw samples, ranges, reference revisions and reproduction parameters are in
`benchmarks/audit_followup_results.json`. Same interpreter and inputs, alternating
before/after order, with output parity checked. Core, graph, Rust v0 and stream
comparisons below use the original `ed67b14` baseline. Nim and JNI use `0f56a94`;
those parser files are unchanged between the two baseline revisions.

| Workload | Before median | After median | Time reduction |
|---|---|---|---|
| Serialize 5,913 libstdc++ ASTs, 7 pairs | 119.356 ms (119.177–121.436) | 80.459 ms (79.868–81.745) | 32.6% |
| Hash 13-node shared Rust graph, 7 pairs | 7.315 ms (7.022–12.811) | 33.553 µs (29.903–39.157) | 99.54% |
| Compare shared Rust graphs, 7 pairs | 6.139 ms (6.063–10.445) | 22.650 µs (21.752–26.363) | 99.63% |
| Rust v0, 60,000 invalid base-62 digits | 429.814 ms | 64.607 µs | 99.98% |
| Rust v0, 60,000 invalid decimal digits | 243.958 ms | 61.602 µs | 99.97% |
| Rust v0, 60,000 valid padded digits | 4.967 ms | 50.913 µs | 98.98% |
| JNI Unicode path, 1,000 candidate separators | 521.779 ms | 675.973 µs | 99.87% |
| JNI, 1,000 empty components | 77.128 ms | 302.775 µs | 99.61% |
| Nim malformed type, 4,000 separators | 71.171 ms | 253.711 µs | 99.64% |
| Library filter, 10,000 plain log lines | 29.146 ms | 2.932 ms | 89.94% |
| Library filter, 10,000 nm lines | 24.172 ms | 19.732 ms | 18.37% |
| Library filter, 10,000 mixed log lines | 18.166 ms | 4.108 ms | 77.38% |
| CLI filter, 10,000 plain log lines | 28.989 ms | 2.609 ms | 91.00% |

These stress cases establish scaling and refusal costs, not a production workload
mix. Stream tests use warmed parsers and a discard sink to isolate filtering. JSON
retention over 256 distinct names of 65,536 characters falls from 50,425,235 to
16,552,537 traced bytes, a reduction of 33,872,698 bytes (67.2%). This counts actual
traced allocations; the cache's character-storage budget excludes object overhead.

Costs are retained where needed for correctness or bounds: ordinary Rust v0 corpus
parsing is 37.659 → 39.894 µs/name (+5.9%); ordinary Nim type-info is 1.402 →
1.539 µs (+9.8%), while ordinary Nim types improve 7.6%. Ordinary JNI has overlapping
variation (2.355 → 2.394 µs), so no improvement is claimed. Core AST construction is
106.374 → 109.762 ms (+3.2%); ordinary hashing is 73.633 → 83.427 ms (+13.3%) and
equality 62.029 → 68.005 ms (+9.6%). Retained node bytes still increase 2.33% from the
original baseline. Symbol-heavy CLI paths cost roughly 5–8% in this run, with some
sample ranges overlapping; the warmed JSON nm samples overlap substantially, so their
median difference is not claimed as a speedup. All raw samples are retained.

The two-line MSVC empty-qualifier fast path removes 1,952 Python calls (1.74%) and
976 sorting calls (59%) over 609 symbols; no separate wall-clock speedup is claimed.

Rejected approaches: including recursion depth in Rust memo keys or retaining only
the deepest-context memo increased calls 10–13%; cached subtree height avoids that.
Purely iterative AST hashing made ordinary hashing 131% slower than the first audit
commit; shallow recursion with per-call memoization and a deep-graph fallback reduces
that cost. Count-only JSON caching was rejected because it cannot bound retained
bytes. Unicode-wide regex scanning is avoided for ASCII input. An annotation check
on every CLI word was replaced with a marker-prefix guard after measurements exposed
its cost. Existing documented native-reference divergences remain unchanged.

### Follow-up verification and limits

- Full CPython 3.13 suite: **4,496 passed, 1 skipped, 384,560 subtests passed**.
- Final CLI/API/Rust/Nim edits: **794 passed, 4,741 subtests passed**.
- CPython 3.12 changed-area suite: **1,494 passed, 1 skipped, 15,807 subtests passed**.
- Frozen corpus replay on both interpreters: **65,629/65,629 exact**.
- Ruff lint/format, type checks and whitespace checks pass; benchmark gate passes
  without changing the baseline.
- Native enumeration through length 4 and 20,000 mutants per supported scheme at
  seed 1: no unexplained divergences. The earlier seed 0 run also passed.
- Cross-entry-point invariants, 1,000 mutants per corpus at seed 1: no invariant broken.
  An additional 10,776 Rust v0 text/tree comparisons at depth bounds 4/8/12/16 pass.
- Wheel and source archive rebuilt offline; isolated installed-wheel smoke verified.

The reproduced findings in the examined paths have been addressed. Follow-up review
covered adjacent Unicode name/offset handling, special forms, bounds/cache interactions,
text/tree parity and ordinary-workload regressions. No unresolved reproduced finding
is being concealed by a performance claim. Coverage is finite: native alphabets,
seeded mutations and representative corpora do not prove all inputs correct. Production
workload distributions, Windows/macOS, free-threaded Python, Python 3.11/3.14/PyPy and
documentation builds remain outside this environment's verification. This audit does
not establish globally optimal performance or that no bugs remain.
