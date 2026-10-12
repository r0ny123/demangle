# demangle Playground

[![Open Playground](https://img.shields.io/badge/Open%20Playground-r0ny123.github.io-2ea44f?logo=googlechrome&logoColor=white)](https://r0ny123.github.io/demangle/playground/)

Read, decompose and explore mangled symbol names interactively in your browser. Everything runs locally on **WebAssembly** via [Pyodide](https://pyodide.org/). Nothing to install and no backend server. The runtime is downloaded from a CDN; symbol processing stays in your browser.

## Features

- **Local Processing**: Demangling, signature extraction, and AST parsing execute 100% client-side in WebAssembly. Your symbol names and binary details never leave your machine.
- **Real-Time Evaluation**: Instant debounced demangling as you type or paste symbols.
- **Full Scheme Coverage**: Auto-detects or explicitly overrides all 14 supported ABI schemes:
  - Itanium C++ (GCC, Clang)
  - Microsoft Visual C++ (MSVC)
  - Swift (including Swift 6 invertible protocol constraints)
  - Rust (legacy and v0)
  - D
  - Go
  - Nim
  - Free Pascal
  - Delphi / C++Builder
  - Ada / GNAT
  - Java Native Interface (JNI)
  - Objective-C
  - CodeWarrior C++
  - GNU v2 (pre-Itanium g++ 2.x, cfront/ARM, Lucid, HP aCC)
- **Signature Decomposition**: Deconstructs symbols into qualified name, base name, namespace/scope, parameters list, return type, calling convention, and qualifiers.
- **Interactive AST Visualizer**: Collapsible syntax graph tree view with shared reference (`$ref`) jump navigation and formatted JSON export.
- **Batch Results**: Enable **Batch: one symbol per line** to preserve line order and blanks, view individual errors, and copy or download the selected output format. Limit: 500 lines / 256 KiB, with a 4 MiB output budget. Remaining symbols are preserved unchanged with a limit message when that budget is exceeded. Duplicate symbols are parsed once per batch; ASTs are only built for inspection in single-symbol mode.
- **URL Permalinks**: **Copy link** puts input, scheme, style, output view, and single/batch mode into a URL fragment. State changes do not automatically update the URL. The 8,000-character limit keeps links manageable; anyone with a link can read its input.
- **Clear Result States**: Unrecognized prefixes, malformed symbols, unsupported schemes, processing limits, and runtime failures are distinguished. **Unchanged** means parsing succeeded without changing the spelling.
- **Accessible Controls**: Native keyboard controls, result and clipboard announcements, and wrapping layouts support keyboard use and enlarged text.
- **Light & Dark Themes**: Synchronized with system preferences and persistent across reloads.

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl / Cmd + K` | Focus mangled symbol input |
| `Esc` | Clear symbol input |
| `Ctrl / Cmd + Enter` | Force re-evaluation |

## Running Locally

Build the site (including the Python archive), then serve it:

```bash
# From repository root:
uv run --group docs mkdocs build --strict
python -m http.server 8000 --directory site
```

Then open `http://localhost:8000/playground/` in your browser.

## Focused Validation

Run `uv run --group test pytest tests/test_playground.py` for bridge behavior, state classification, batch limits, caching, and CLI output parity. After building the site, run browser regressions:

```bash
DEMANGLE_BROWSER_TESTS=1 uv run --group browser --group test pytest tests/browser/test_playground_browser.py
```

Install Chromium with `uv run --group browser playwright install chromium` first, or set `DEMANGLE_CHROMIUM_PATH` to an existing executable. Browser regressions use a deterministic worker for UI behavior; the bridge tests exercise the actual library. Set `DEMANGLE_PYODIDE_SMOKE=1` when changing worker/bridge integration to also verify the real CDN runtime. This keeps routine UI checks fast and independent of network availability.
