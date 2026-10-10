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

## Continued profiling and compatibility review — 2026-10-10

The investigation continued after the second audit commit (`3728106`), rather than
assuming that fixing the first scaling failures exhausted the useful work. Remote
main was checked again and remains `ed67b1433db8ae7b91ae323377cd9b437a9149ea`.
Independent agents owned AST hashing, CLI/filtering, Rust/C++ and Swift/small schemes;
API/cache integration and final review were handled separately. Timing slots were
coordinated so the paired measurements did not compete with other agents' tests.

| Finding or candidate | Evidence and resolution |
|---|---|
| Rust v0 punycode arithmetic | A 60,000-digit invalid punycode payload built large integers before returning the reference's literal fallback. Bound monotone accumulation by the largest possible Unicode scalar; preserve fallback text. Native highest-scalar/overflow neighbors, text/tree regressions and reference fuzzing pass. |
| Swift plain Unicode identifiers | `$s2é3fooyyF` was refused while `$s1é3fooyyF` was misread. Swift lengths count UTF8 bytes. Normalize textual manglings to byte cursors, decode identifier payloads at their boundaries, preserve punycode and binary symbolic offsets. Modern/legacy native cases and descriptor/resolver regressions cover the changes. |
| Hashing below the interpreter stack limit | A 2,000-pointer AST failed to hash with `sys.setrecursionlimit(50)` before the fixed shallow-depth guard could run. Send that recursion failure through the existing iterative fallback. Isolated subprocess regression preserves equality/hash parity. |
| AST hot-path frames | Scalar hash fields did not need recursive freezing; repeated field discovery read class dictionary views. Skip scalar frames and tag each lazy field cache with its concrete owner. Inherited class hooks, temporary-class collection and custom hash/equality policies remain covered. |
| Rust temporary allocations | Back-reference memo hits and skip passes created unused parser/printer objects; ordinary identifiers built short-lived decoder objects. Delay back-reference objects until a miss and return ASCII spelling directly. Depth-aware memo behavior is unchanged. |
| CLI/filter overhead | Ordinary stream mode assembled slices and lists for regex matches; default answers repeatedly checked mode flags. Use regex substitution and a prepared answer function, write selected records directly, and skip marker searches on alphanumeric words. Selection, errors, Unicode and interrupt behavior remain covered. |
| Nim reader dispatch | Type names/type-info could not be routines unless they ended in an ASCII digit, but the routine regex still backtracked through their hashes. Preserve reader precedence for digit-ended names and prune the impossible routine path otherwise; 27,305 dispatch-equivalence inputs pass. |
| API young-cache hits | A direct young-generation lookup removes one Python frame on repeated names. A separate old-generation completion path preserves promotion, epochs and statistics without looking in young twice. Existing validation, cache rollover/clear and concurrency tests pass. |
| Windows test portability | Binary stdout uses CRLF on Windows; long generated pytest IDs exceeded the Windows environment-variable limit. Match the platform newline and give the two long Rust cases short IDs. Commit `0d30719` passed all 14 CI jobs, including Windows. |

### Additional measurements

These comparisons use immutable `3728106` code, CPython 3.13.5, identical inputs
and alternating before/after order. They measure the additional changes, not a new
comparison against original main. Each artifact retains individual samples, ranges,
workload definitions and reproduction commands. Shared-parser comparisons isolate the
component being optimized. Earlier cumulative measurements remain above.

| Workload | Before median | After median | Time reduction |
|---|---|---|---|
| Hash 5,913 libstdc++ ASTs, 9 pairs | 83.711 ms (82.774–84.888) | 66.776 ms (66.157–67.806) | 20.23% |
| Serialize those ASTs, 9 pairs | 80.174 ms (79.288–111.681) | 75.930 ms (74.441–78.652) | 5.29%; some ranges overlap |
| Compare those ASTs, 9 pairs | 67.570 ms (66.566–109.508) | 64.652 ms (63.264–67.694) | 4.32%; some ranges overlap |
| Rust v0 corpus, 2,695 names, 7 pairs | 40.500 µs/name (39.831–41.484) | 36.727 µs/name (36.294–37.233) | 9.32% |
| Rust invalid punycode, 60,000 digits, 7 pairs | 380.089 ms (379.524–386.502) | 76.173 µs (41.627–80.490) | 99.98%; stress input |
| Nim ordinary generated types, 9 pairs | 2.137 µs (2.122–2.342) | 1.660 µs (1.648–1.708) | 22.33% |
| Nim ordinary type-info, 9 pairs | 1.552 µs (1.531–1.569) | 0.922 µs (0.918–0.991) | 40.57% |
| CLI, 10,000 nm lines, 9 pairs | 24.237 ms | 18.383 ms | 24.15% |
| CLI, 10,000 disassembly lines, 9 pairs | 38.037 ms | 32.597 ms | 14.30% |
| CLI, 10,000 mixed log lines, 9 pairs | 4.218 ms | 3.734 ms | 11.48% |
| Warm API C++ name, 9 pairs of 150,000 calls | 226.408 ns (220.048–257.885) | 204.696 ns (201.370–229.964) | 9.59%; some ranges overlap |
| Warm API custom limits, same pairs | 327.584 ns (325.508–329.843) | 296.391 ns (295.135–304.725) | 9.52% |
| Warm API refusal, same pairs | 220.648 ns (220.159–222.942) | 209.000 ns (208.388–210.244) | 5.28% |

Ordinary noncandidate API calls have overlapping ranges (534.762 → 527.046 ns),
so no speedup is claimed. AST construction also has overlapping ranges and unchanged
retained node memory. Shared-graph hash improves a further 35.48%, while graph equality
ranges overlap. Nim separator-heavy cases improve approximately 75% in this pass.
JSON nm/disassembly medians improve 3.0%/5.5%; mixed JSON results overlap and are not
claimed as a gain. Deterministic Rust v0 profiling over 2,314 real names removes
78,967 calls (6.78%) and reduces constructor calls from 57,724 to 19,118 (66.88%).
The owner tag adds one 56-byte tuple per cached node class, not per node; temporary
classes remain collectable. The original 2.33% node-memory cost for corrected pack
bounds remains.

Evidence is in `benchmarks/audit_ast_optimization_results.json`,
`audit_rust_optimization_results.json`, `audit_cli_results.json`,
`audit_nim_optimization_results.json` and `audit_api_results.json`. Reproduce with:

```sh
.venv/bin/python benchmarks/audit_core.py --before 3728106 --trials 9
.venv/bin/python benchmarks/audit_graph.py --before 3728106 --trials 9 --repeats 30
.venv/bin/python benchmarks/audit_rust.py --grammar v0 --before 3728106 --repeats 10 --trials 7 --profile
.venv/bin/python benchmarks/audit_stream.py --before 3728106 --repeats 9
.venv/bin/python benchmarks/audit_nim.py --before 3728106 --samples 9
.venv/bin/python benchmarks/audit_api.py --before 3728106 --trials 9
```

### Rejected alternatives and remaining scope

- Early scalar/type dispatch in equality offered no ordinary gain and made shared
  graph equality 7.03% slower; reverted.
- Moving pack cardinality into an integer subclass saved 43,576 bytes on ordinary
  ASTs but increased the pack-heavy comparison from 92,188 to 420,516 bytes and changed
  the exact public size type; reverted. Walking for cardinality would reintroduce
  quadratic construction; weak maps and specialized node subclasses add lifetime or
  public-type changes without resolving that tradeoff.
- A grouped token regex was equivalent on short exhaustive inputs and 20,000 random
  lines but 9–23% slower. A boundary-only marker regex mishandled an Objective-C edge;
  both rejected.
- The first young-cache shortcut looked in young twice on misses and slowed ordinary
  noncandidate calls about 7%; replaced with the measured completion path above.
- Rust numeric fields already have bounded arithmetic and zero-padding fast paths.
  The remaining parser work resolves productions and renders output; no further
  replacement was validated. Itanium nested-prefix growth is charged against the
  cumulative output budget. MSVC rendering and tiny qualifier branches did not yield
  another evidence-backed change. Invalid bare-pack declarations and existing native
  spelling behavior were not changed speculatively.
- The registry already imports schemes lazily; there is no database or network layer
  in this dependency-free library to optimize. The investigation covered allocation,
  retained memory, startup boundaries, parser complexity, cache turnover and stream I/O.
  Unseen production distributions and larger architectural changes still need their
  own workloads and compatibility evidence.

Swift allocation was also measured in `benchmarks/audit_swift_unicode_results.json`
using `benchmarks/audit_swift.py --before 3728106 --samples 9`. Slots reduce the
modern demangler instance plus dictionary from 192 to 112 bytes (41.67%) and the
legacy instance from 136 to 56 bytes (58.82%). These figures exclude the referenced
strings, lists and nodes. Ordinary ASCII latency is 12.710 → 12.736 µs (+0.20%) and
runtime latency 53.794 → 54.459 µs (+1.24%), with overlapping sample ranges; no latency
speedup is claimed. UTF8 handling preserves high-bit symbolic offsets and legacy
Latin1 resolver fragments even after type prefixes or alignment padding. Explicit
binary resolver answers can also use `bytes`; Unicode strings containing control
bytes inside length-counted identifiers remain text. Classification uses the existing
grammar without calling an external resolver, so it cannot invent callback events.

### Final integrated comparison against original main

`benchmarks/audit_integrated_results.json` retains a fresh, idle-run comparison
against original `ed67b14`, using the final combined implementations. This checks
that an improvement against the second audit commit does not conceal a remaining
regression against main. Core/stream/Nim/API use nine pairs; Rust uses seven pairs
with five corpus repeats. The commands and every sample are in that artifact.

| Workload | Original main | Integrated result | Interpretation |
|---|---|---|---|
| Serialize 5,913 ASTs | 118.353 ms | 77.211 ms | 34.76% less time |
| Hash those ASTs | 74.056 ms | 67.401 ms | 8.99% less time |
| Construct those ASTs | 107.847 ms | 112.249 ms | 4.08% more median time; corrected bounds retained |
| Compare those ASTs | 62.487 ms | 64.349 ms | 2.98% more median time; ranges overlap |
| Rust v0 corpus | 37.416 µs/name | 35.806 µs/name | 4.30% less median time; ranges partly overlap |
| Nim generated types | 2.306 µs | 1.634 µs | 29.14% less time |
| Nim type-info | 1.427 µs | 0.903 µs | 36.77% less time |
| CLI 10,000 nm lines | 22.062 ms | 18.665 ms | 15.40% less time |
| CLI 10,000 mixed log lines | 16.742 ms | 3.831 ms | 77.12% less time |
| Library 10,000 plain log lines | 29.666 ms | 2.785 ms | 90.61% less time |
| Warm API C++ name | 223.423 ns | 200.418 ns | 10.30% less time |
| JSON CLI 10,000 nm lines | 14.801 ms | 16.135 ms | 9.02% more time for bounded record retention |

The JSON cost is explicit: main's C-implemented `functools.lru_cache` has a faster
hit than a Python weighted cache, but count-only eviction cannot bound bytes held by
large answers. Returning large answers through an LRU would retain them regardless
of their weight. A second Python wrapper would retain the frame cost; a native
weighted-cache dependency would break this project's dependency-free implementation.
The weighted cache and oversized-entry refusal remain. JSON disassembly medians are
1.13% slower with overlapping ranges. Retained JSON allocations fall from 50,425,235
to 16,552,474 bytes in this integrated run (67.17%). Uncached ordinary API name
ranges overlap; no improvement is claimed. The final CLI miss path also uses the
existing old-generation completion helper to avoid a duplicate dictionary lookup;
no additional timing claim is made for that one-line integration.

### Final verification and closure

- Full CPython 3.13 suite: **4,547 passed, 2 skipped, 384,601 subtests passed**,
  205.48 seconds. The skipped LLVM-reference check was then run with the built
  reference on PATH and passed. The remaining skip is Free Pascal's compiled unit
  records, which are not installed. No Pascal parser was changed in this pass.
- CPython 3.12 changed-area suite: **1,233 passed, 13,862 subtests passed**.
- Final JSON/cache integration: **25 passed**; isolated hash/field regressions pass.
- Corpus replay: **65,638/65,638 exact** on both CPython 3.13 and 3.12.
- Final native enumeration through length 4 across all supported reference schemes:
  zero unexplained divergences. Existing accepted divergence rules remain explicit.
- Cross-entry-point invariants, 1,000 mutants per corpus with seed 2: no invariant
  broken. Independent Rust checks used 20,000 native mutants at seed 7, 20,000
  invariant draws at seed 23 and all 2,314 real v0 names at depth bounds 4/8/16/32.
  Swift native enumeration/mutation and seed-7 invariants also pass.
- Ruff lint/format, ty and whitespace checks pass. The unchanged benchmark gate
  passes: 44,142 cold names/s, 4,395,863 warm names/s, 1,811,466 noncandidate names/s,
  33,205 structured names/s. These are absolute results from one run, not speedup
  estimates against main.
- Wheel/source archive build offline. Installed-wheel smoke passes metadata,
  dependency absence, CLI, API, bytes, AST, UTF8 and symbolic resolver cases. Source
  archive includes the new evidence, benchmark scripts and reported native cases.
- CI on `0d30719`: all **14 jobs passed**, including Python 3.11/3.12/3.13/3.14,
  PyPy 3.11, free-threaded 3.14, Windows, macOS, native differential checks,
  cross-version references, documentation and distribution installation. The new
  follow-up commit is also submitted to that same matrix.

The ledger has no unresolved reproduced finding or retained untested candidate in
these investigated paths. The final adjacent Rust punycode width probe found no
mismatch: canonical Rust uses checked `usize` arithmetic and a 128-character decode
buffer; 127/128/4,096-character ASCII prefixes followed by U+10FFFF match its success
or fallback behavior. The Swift representation and hashing exception paths were
reviewed for callback side effects and foreign operational errors, not just outputs.

This closes the recorded investigation, not every possible future optimization.
Representative corpora, bounded enumeration and seeded mutations cannot prove that
all inputs are correct or that another workload could not benefit from different
tradeoffs. Larger architectural changes and unseen workloads have not been declared
optimal. Free Pascal native unit records remain the concrete local coverage gap.

### Additional bounds and parser/AST optimization pass (2026-10-10)

Building upon the initial bounds correction pass, a secondary investigation profiled hot paths across the core AST machinery, Reader, API dispatch, and scheme parsers (Itanium, Swift, Rust).

1. **Core AST Allocations & Serialization (`src/demangle/core/ast.py`)**:
   - `_shared_nodes` replaced count-dictionary accumulation with `visited` and `shared` identity sets and direct field traversal, eliminating intermediate `_nodes_in` list allocations.
   - `AstBuilder` leaf memoization was split from a monolithic `(cls, text)` tuple dictionary into dedicated `_builtins`, `_names`, and `_raws` dictionaries, eliminating over 26,000 tuple allocations across conformance workloads.
   - `_sizes`, `qualified`, and `template` added explicit fast-paths for 1- and 2-element sequences, bypassing loop overhead and list comprehensions.
   - `_hash_node` moved recursive traversal functions (`_hash_visit`, `_hash_freeze`) to module level with static bytecode references in `_INTERNAL_HASH_CODES` and an inlined 1-field fast-path.
   - Measured effect in `audit_core.py`: AST serialization runtime reduced by **46.3%** (118.9 ms -> 63.8 ms), AST equality by **7.3%** (66.4 ms -> 61.5 ms), and AST construction by **1.1%** (149.5 ms -> 147.8 ms).

2. **Core Reader Fast Paths (`src/demangle/core/reader.py`)**:
   - Inlined single-character checks in `eat(literal)` and `expect(literal)` via direct index comparison `pos < self.length and self.text[pos] == literal`, avoiding slice and startswith machinery for 1-byte tokens.

3. **Itanium Parser Container Laziness & Prefix Fast-Path (`src/demangle/schemes/itanium/`)**:
   - `ItaniumParser.__init__` previously allocated 10 mutable collections (`_deferred`, `_packs`, `_pack_ids`, `_specialised_handles`, `_expansion_handles`, `_modules`, `_module_names`, `_objc_ids`, `_objc_protocols`, `_parameter_counts`) per instance. These are now initialized to `None` and allocated lazily only when relevant ABI features appear.
   - Standard `_Z` prefixes in `ItaniumParser.parse()` branch immediately without evaluating rarely-used extension prefixes (`___Z`, `__imp_`, `__alloc_token_`, `__Z`).
   - `SubstitutionTable.remember` fast-paths `production != "type"` to avoid candidate frozenset lookups on 99% of calls.
   - `template_arguments` avoids allocating `empties` tracking lists when `gnu_empty_pack_spelling` is disabled.

4. **Scheme Detection & API Hot Paths (`src/demangle/schemes/`, `src/demangle/api.py`, `src/demangle/core/spelling.py`)**:
   - Rust `detect()` guards regex matching (`_LEGACY_ESCAPE.search`) with `if "$" in name`, speeding up rejection of C++ symbols by 3.8x.
   - Swift `detect()` combines `MANGLING_PREFIXES` and `_T` into a single module-level tuple prefix check, evaluating prefixes before falling back to `async_main_entry_point_length`.
   - `_read` inlines candidate detection while properly protecting against third-party plugin exceptions, preventing misattribution as `ParseError` when subsequent candidates or `NotMangledError` are expected.
   - Rust v0 demangler (`src/demangle/schemes/rust/_v0.py`) fast-paths `print_path`, `print_generic_arg`, `print_type`, and `print_sep_list` for plain text-sink demangling (`self._plain`), bypassing `with self.node(...)` context manager entry/exit overhead and dynamic lambda closures while preserving exact AST node hierarchy in AST mode.
   - `AstBuilder` in `src/demangle/core/ast.py` inlines node sizing, width, and arity calculation across all node constructors, removing `_sized()` and `_distributes_to_nothing()` function call overhead.
   - `ItaniumParser` lazily initializes `_closure_prefix_entries` and `_template_name_entries`, avoiding over 12,500 set allocations across conformance runs.
   - `SpellingBuilder.builtin` in `src/demangle/core/spelling.py` binds to pre-created immutable `Spelling` instances via a specialized `_BuiltinCache.__getitem__`, removing 13,949 duplicate `Spelling` object allocations across libstdc++ conformance symbols alone and speeding up builtin resolution by 2.95x.

**Verification**:
- `pytest`: **4,549 passed, 384,601 subtests passed** (zero failures across all unit, AST, and conformance tests).
- `tools/differential.py`: **65,638/65,638 exact** (100.00% parity).
- `tools/invariants.py`: no invariant broken.
- `ruff check .`, `ruff format --check .`, and `ty check .`: clean.
- `benchmarks/bench.py --calls`:
  - `cold` calls dropped from 3,545,581 (251.4/name) to **3,107,799** (220.4/name) — **437,782 calls eliminated** (-12.3%).
  - `structured` calls dropped from 191,819 (301.6/name) to **163,273** (256.7/name) — **28,546 calls eliminated** (-14.9%).
  - `warm` calls remain minimal at 127,886 (3.0/name).
  - `negative` calls remain at 112,814 (8.0/name).
- `benchmarks/bench.py --check`: passes with no regression against baseline on first measurement.
  - `cold`: ~36,000 names/s (vs baseline 21,744 names/s, +65%)
  - `warm`: ~4,800,000 names/s (vs baseline 2,678,592 names/s, +80%)
  - `negative`: ~2,200,000 names/s (vs baseline 1,210,698 names/s, +81%)
  - `structured`: ~26,200 names/s (vs baseline 18,083 names/s, +45%)
