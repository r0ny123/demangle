# demangle Playground

[![Open Playground](https://img.shields.io/badge/Open%20Playground-r0ny123.github.io-2ea44f?logo=googlechrome&logoColor=white)](https://r0ny123.github.io/demangle/playground/)

Read, decompose and explore mangled symbol names interactively in your browser. Everything runs locally on **WebAssembly** via [Pyodide](https://pyodide.org/). Nothing to install, no backend servers, and zero network data transmission.

## Features

- **Zero-Network Privacy**: Demangling, signature extraction, and AST parsing execute 100% client-side in WebAssembly. Your symbol names and binary details never leave your machine.
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
- **URL Permalinks**: Automatically syncs symbol and option state into URL hash fragments (`#symbol=...&language=...`) for one-click sharing.
- **Light & Dark Themes**: Synchronized with system preferences and persistent across reloads.

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl / Cmd + K` | Focus mangled symbol input |
| `Esc` | Clear symbol input |
| `Ctrl / Cmd + Enter` | Force re-evaluation |

## Running Locally

To run the standalone playground locally:

```bash
# From repository root:
python -m http.server 8000
```

Then open `http://localhost:8000/playground/` in your browser.
