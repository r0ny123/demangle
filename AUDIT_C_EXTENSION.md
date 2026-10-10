# Audit: Compiled C Extension vs. Pure Python Demangling

## Executive Summary

This investigation explores the user's inquiry:
> *"Further reductions would require moving core parsing algorithms to a compiled C extension, which would violate the library's design requirement of being a pure, dependency-free Python implementation.*
>
> *Can you chase this on another branch, branching off another branch, if it drastically inprove the speed and performance then we should drop the idea of pure python. Run them under /boost"*

We created an experimental branch `boost/c-extension` branching off `fix/parser-bounds-and-performance`, built a native CPython extension (`src/demangle/_speedups.c`), implemented accelerated components (`FastReader`, C token and identifier scanners), and benchmarked performance against both the standard benchmarks and the entire **Boost 1.84 MSVC corpus** (`tests/conformance/msvc-boost.txt`, 5,843 symbols) as well as the Itanium corpora.

### Key Conclusions:
1. **Piecemeal C Accelerator (`FastReader` / Token Scanning)**:
   - **Boost Corpus (MSVC)**: Pure Python achieves **12,685 names/s** (78.83 µs/name) vs. C-accelerated **12,363 names/s** (80.89 µs/name) — **0.97x speedup** (Pure Python is 2.6% faster).
   - **Itanium Corpus (libstdc++)**: Pure Python achieves **27,233 names/s** (36.72 µs/name) vs. C-accelerated `FastReader` **25,103 names/s** (39.84 µs/name) — **0.92x speedup** (Pure Python is 8.5% faster).
   - **Standard Benchmark Suite (`benchmarks/bench.py`)**: Pure Python achieves **36,469 cold names/s** vs. C-accelerated **36,034 cold names/s** — **1.00x speedup** (within noise).
   - **Why**: Demangling CPU time is dominated by AST node allocations, dictionary substitutions, recursive grammar dispatch, and Python string constructions. Crossing the CPython C-API boundary for micro-operations (cursors, single-character lookahead, token slices) introduces function invocation, argument parsing (`PyArg_ParseTuple`), and object boxing (`PyTuple_Pack`, `PyUnicode_FromOrdinal`) overhead. Furthermore, Python's built-in string primitives (`str.find`, `str[start:end]`, `in DIGITS`) are already implemented in vectorized, highly tuned C inside CPython.

2. **End-to-End Native C Demangler**:
   - Benchmarking native C demangling (`__cxa_demangle`) against pure Python across 6,231 real-world Itanium symbols:
     - Pure Python: **26,015 names/s** (38.4 µs/name)
     - Native C: **962,707 names/s** (1.04 µs/name)
     - **Speedup: 37.0x**!
   - However, native C demangling does not produce `demangle`'s structured AST, cannot support MSVC/Rust/Swift/D/Nim/Pascal uniformly, and differs in formatted string representation on 44% of symbols.

3. **Strategic Recommendation**:
   - **Do NOT drop pure Python for piecemeal C extensions.** Adding binary build steps, compiler dependencies, platform-specific wheels, and potential compatibility hurdles across Python 3.11-3.14, PyPy, and free-threaded builds yields zero throughput gain (and a 3-8% slowdown on token scanning).
   - Dropping pure Python is only justified if the *entire recursive descent parser and AST builder* are rewritten in C/C++/Rust. Given the library's design requirement of being zero-dependency, pure-Python, and easily auditable, pure Python remains the optimal architectural choice.

---

## 1. Benchmarking the Boost Corpus (`benchmarks/audit_boost.py`)

The Boost corpus (`tests/conformance/msvc-boost.txt`) represents the most complex C++ mangling workloads: 5,843 decorated symbols with deeply nested templates, function pointer parameters, type qualifiers, and multiple back-references from 29 `boost_*-vc143` libraries.

Ran `benchmarks/audit_boost.py --save benchmarks/audit_boost_results.json`:

| Implementation | Workload | Wall Time | Throughput | Latency (µs/symbol) | Speedup Ratio |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Pure Python** (PR #49 baseline) | 5,843 Boost symbols | 0.4606s | **12,685 names/s** | 78.83 µs | 1.00x (baseline) |
| **C Accelerated** (`FastReader` + C scanner) | 5,843 Boost symbols | 0.4726s | **12,363 names/s** | 80.89 µs | 0.97x (-2.6%) |

### Profiling Breakdown on Boost Symbols:
In 1,000 Boost symbols, the parser executes over 1,035,000 function calls:
- `nameFragment`: 46,323 calls (0.278s cumulative)
- `templateInstantiation`: 11,558 calls (0.258s cumulative)
- `qualifiedNameBody`: 15,868 calls (0.286s cumulative)
- `typeBody`: 26,825 calls (0.278s cumulative)
- `render`: 37,086 calls (0.035s cumulative)

In MSVC parsing, identifiers are scanned via `@` terminators. Python's `self.text.find("@", pos)` directly leverages glibc `memchr` (vectorized SSE/AVX assembly) without Python-to-C wrapper overhead. Delegating to a custom C extension `_fast_msvc_identifier` requires argument unpacking and 2-tuple packing (`PyTuple_Pack`), which is actually slower than Python's inlined `str.find` + slice.

---

## 2. Standard Benchmark Suite (`benchmarks/bench.py`)

Ran `benchmarks/bench.py` on `boost/c-extension`:

| Phase | Names | Pure Python | C Accelerated | Change | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **cold** | 14,101 | 36,469 names/s (27.42 µs) | 36,034 names/s (27.75 µs) | -1.2% | Within noise |
| **warm** | 42,303 | 4,815,966 names/s (0.21 µs) | 4,879,100 names/s (0.20 µs) | +1.3% | Within noise |
| **negative** | 14,101 | 2,220,318 names/s (0.45 µs) | 2,244,742 names/s (0.45 µs) | +1.1% | Within noise |
| **structured** | 636 | 26,552 names/s (37.66 µs) | 26,115 names/s (38.29 µs) | -1.6% | Within noise |

---

## 3. End-to-End C Demangling Ceiling

To measure what a fully compiled C/C++ engine would achieve:
We benchmarked 6,231 Itanium symbols (`itanium-real-world.txt` + `itanium-libstdcxx.txt`) against compiled `__cxa_demangle`:
- **Pure Python `demangle.demangle`**: 0.2395s (**26,015 names/s**, 38.4 µs/name)
- **Native C `__cxa_demangle`**: 0.0065s (**962,707 names/s**, 1.04 µs/name)
- **Speedup**: **37.0x**

A compiled C parser is ~37x faster when the whole parsing pipeline stays in C. However, it cannot be achieved incrementally by wrapping micro-operations in C extension functions.

---

## 4. Root Cause Analysis: Why Was the Connection Dropping?

Investigation of the CLI server logs in `/home/ubuntu/.gemini/antigravity-cli/log/`:
```
I1010 15:58:49.436805     195 common.go:410] Terminal gone, shutting down
I1010 15:58:49.439972       1 common.go:470] CLI program exited, shutting down
...
I1010 15:58:49.499027  445173 server.go:3924] Remote control disabled, stopping connection
I1010 15:58:49.499058     687 remote_control_v2.go:2090] Connection loop exited: context canceled
```

### Explanation:
1. **Interactive Terminal Disconnection**: The Antigravity CLI process is attached to the client's terminal session / browser web socket. When the browser tab is refreshed, the laptop goes to sleep, or the client network disconnects, the PTY/terminal stream closes.
2. **Graceful CLI Shutdown**: The CLI process detects `Terminal gone, shutting down` and exits gracefully.
3. **Remote Control Cancellation**: When the CLI exits, the backend remote control context is canceled (`context canceled`), halting running tasks.
4. **Resumption**: When the user reconnects, a new CLI process spawns, reads SQLite / Protobuf session state, and notifies the agent of pending task results.
