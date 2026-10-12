# Performance pass, October 2026

Baseline: `03bd234e6684a7b42672bd962da3f21cfa3e1adb`. Measurements use
CPython 3.13.5 on the same Linux runner. This is a pure Python package and a static
Pyodide-worker playground; there is no server or database workload.

## Library

The existing `bench.py` corpus workloads and `audit_api.py`/`audit_core.py` paired
comparison protocol were reused. `end_to_end.py` adds startup, bounded batch,
AST/JSON serialization and library/CLI streaming measurements. Traced memory is
measured separately from wall time. Three independent runs alternate checkouts;
each reports seven timing repetitions. The table shows the median of those three
run medians, in milliseconds. Raw ranges are retained in `results/library-*.json`.

| Workload | Inputs | Before | After |
| --- | ---: | ---: | ---: |
| Cold mixed demangling | 14,101 | 310.09 | 309.36 |
| Warm cache lookup | 42,303 | 10.14 | 10.07 |
| Ordinary names | 14,101 | 7.82 | 7.83 |
| Structured Itanium parsing | 636 | 17.43 | 17.35 |
| Swift demangling | 256 | 15.15 | 15.36 |
| Swift parsing | 256 | 18.13 | 18.00 |
| Mixed signature extraction | 1,280 | 83.07 | 75.73 |
| Warm batch generator | 5,120 | 1.16 | 1.15 |
| AST dictionaries and JSON | 1,280 | 31.17 | 32.76 |
| Warm mixed-text stream | 5,120 | 9.79 | 9.86 |
| CLI pipe, including subprocess startup | 5,120 | 116.43 | 118.77 |
| Import in a fresh process | — | 16.62 | 16.92 |
| First Itanium/MSVC/Rust/Swift calls | 4 | 15.91 | 16.64 |
| Preload remaining languages | — | 5.41 | 5.65 |

Signature extraction repeatedly spelled the same root and shared child nodes.
An identity memo now lives for one extraction, under that extraction's spelling
policy. It retains node references to prevent identity reuse by temporary plugin
nodes, and never survives into another call. Mutable plugin trees therefore remain
supported. Parsing, rendering rules and the module-level demangle cache are unchanged.

The mixed signature gain is **8.8%** in the full-checkout measurements. A separate
nine-trial paired audit, sharing the same parsers, reports **10.7%**, including
**14.8%** for Rust. Every one of the 1,280 signatures compares field for field before
timing. cProfile calls fall from 1,195,759 to 991,020 (17.1%); profiling overhead is
excluded from reported wall times. Other table movements are runner variance, not
claimed improvements. An initial noisy run gave apparent large gains in unchanged
serialization/startup code; those readings were discarded and measurements repeated.

The cold corpus retains about 4.23 MB of traced Python allocations in both versions.
AST JSON peaks near 5.54 MB in both. Signature peaks remain near 160 KB; the memo is
temporary. Direct extraction on constructed shared graphs with pointer depths
40/120/400 produced identical fields and peaks of 12,748/25,548/70,348 bytes before
versus 11,218/21,458/57,298 after. These graph checks test extraction itself, rather
than bypassing public parsing bounds in a timing claim. CLI memory figures cover the
benchmark parent process, not the spawned interpreter's heap.

The cold parser profile is spread across Itanium productions, Rust path printing,
legacy decoding and reader operations. Existing caches, lazy imports, output limits
and graph-aware serialization already remove the large avoidable costs. Changing
grammar dispatch or cache policy for small possible gains would introduce more
complexity than this evidence supports. The 5% serialization movement has identical
code and traced peak, and is within the observed runner spread.

## Playground

The batch bridge previously encoded each row for byte accounting and then encoded
the entire response again. It now encodes each distinct symbol once per batch and
joins those same encodings into the response. Separate normal/limit caches preserve
the existing output-budget transition. Default ASCII escaping makes string length
equal to UTF-8 byte length; Unicode, blank lines, ordering and exact JSON bytes are
covered by parity checks and the budget regression.

`bench_playground_bridge.py` shares the current parser between both serializers and
alternates their order over 51 trials. Median milliseconds for 500 lines:

| Input | Before | After | Reduction |
| --- | ---: | ---: | ---: |
| Repeated valid symbol | 1.582 | 0.113 | 92.9% |
| Mixed repeated symbols | 1.660 | 0.182 | 89.0% |
| Blank lines | 1.405 | 0.063 | 95.5% |
| Unique plain symbols | 3.482 | 2.955 | 15.1% |

The speedup trades 40–122 KB of additional temporary traced peak allocations for
retaining encoded rows. The cache is local to the existing 500-line, 256-KiB-input
batch bound, and preserves the existing 4-MiB output-budget behavior.

The browser benchmark runs the actual Python bridge in Pyodide, with Chromium
151.0.7922.173. Its baseline site/archive was rebuilt from a pristine `git archive
03bd234`; it is not a fake-worker timing. Pyodide 0.26.4 worker computation plus
transfer for 500 repeated symbols measured 7.8–10.5 ms before versus 3.3 ms after.
Initial table rendering remains roughly 38–50 ms and is the larger remaining batch
cost. The bounded table is kept instead of introducing pagination or virtualization.

Cached format switches now update only output cells when the result identity is
unchanged. On the same 500-row table, 20 switches measured 4.5–4.9 ms median before
versus 0.8 ms after (about 83% less), with zero worker requests. Input/status cells,
caption, copy/download output and visual design are preserved. Single-mode/reset
rendering releases the hidden batch table and its cell references: a separate DOM
measurement shows 2,508 table descendants retained by the baseline versus zero after.

Both trees request five CDN runtime assets totaling 13,780,118 bytes. Cold proxy
fetches total about 1.47 seconds; a second browser context replays the same bodies
from memory with no remote fetch time. Current navigation-to-worker-ready measured
4.26 seconds cold and 2.85 seconds on replay; after DOMContentLoaded, the replay
still takes 2.67 seconds. Startup is therefore dominated by runtime delivery and
initialization, rather than the approximately 446-KB local source archive. Local
archive/bridge requests are logged separately; assets load concurrently in the worker.
These are bounded observations, not an Internet latency guarantee.

A temporary served-worker override also tested current stable Pyodide 0.29.3. All
nine built-in presets passed on both releases. The newer runtime downloads 12.28 MB
and its one warm startup observation was 2.40 versus 2.67 seconds after DOM load,
but repeated-batch worker/transfer time regressed from 3.3 to 6.9 ms. This mixed,
single-pair result does not justify a runtime migration; production stays on 0.26.4.
Runtime selection remains within our control, while CDN transfer varies externally.

Benchmark-only worker instrumentation reports 24 MiB of WebAssembly linear-memory
capacity on the pristine and current 0.26.4 builds. This measures buffer capacity,
not used heap or process RSS. Main-page DOM/heap counters depend on garbage collection
and the AST rendered after mode changes; no total-memory reduction is claimed. Raw
browser, cleanup, runtime-trial and bridge reports are in `results/playground-*.json`.

## Reproduction

```sh
.venv/bin/python benchmarks/bench.py --check
.venv/bin/python benchmarks/audit_signature.py --output /tmp/signature-paired.json
mkdir -p /tmp/demangle-baseline
git archive 03bd234e6684a7b42672bd962da3f21cfa3e1adb | tar -x -C /tmp/demangle-baseline
.venv/bin/python benchmarks/end_to_end.py --source /tmp/demangle-baseline --output /tmp/library-before.json
.venv/bin/python benchmarks/end_to_end.py --output /tmp/library-after.json
.venv/bin/python benchmarks/bench_playground_bridge.py > /tmp/bridge.json
.venv/bin/mkdocs build --strict
DEMANGLE_CHROMIUM_PATH=/usr/bin/chromium .venv/bin/python benchmarks/bench_playground_browser.py --runs 2 --check-presets > /tmp/browser.json
# Optional temporary served-worker trial; does not change production assets:
DEMANGLE_CHROMIUM_PATH=/usr/bin/chromium .venv/bin/python benchmarks/bench_playground_browser.py --runs 2 --check-presets --pyodide-version 0.29.3 > /tmp/runtime-trial.json
```

Alternate the before/after command order across three runs and compare run medians.
`--cprofile /tmp/profile.txt` optionally records cumulative and self-time hotspots.
Startup uses new interpreters with warm filesystem/bytecode caches. No network is
used by the library benchmarks. Serialized and streamed output hashes must agree
between the saved before/after reports; the harness checks completed corpus counts
and exact CLI/library stream parity on each run.

## Validation

The signature/API suite passed 367 tests, including corpus sweeps and style checks.
An additional regression exercises a shared mutable plugin tree across extractions.
The focused signature/benchmark run passed 169 tests; adding the core checks passed
292 tests. The full non-browser suite passed 4,609 tests and skipped three; its sole
failure was stale editable-install metadata (`0.5.2` versus source `0.6.0`). Refreshing
the local editable installation and rerunning that assertion passed. No source fix
or repeat full run was needed. Ruff, formatting, type checks and the existing
`bench.py --check` performance gate passed.
The browser suite collected 56 tests: 55 passed and one optional runtime smoke test
was skipped. All 25 playground Python tests passed. Separate actual-runtime benchmark
runs exercised every built-in preset, 9/9 on each source tree and runtime release.
