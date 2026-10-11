# Interactive Demangling Playground

Explore mangled symbols interactively in your browser. All demangling, scheme detection, structured signature extraction, and AST graph parsing run entirely client-side using **WebAssembly** via [Pyodide](https://pyodide.org/). Symbols stay in your browser. The runtime is downloaded from a CDN on first load; demangling needs no server requests after initialization.

<div style="margin: 2rem 0; text-align: center;">
  <a href="https://r0ny123.github.io/demangle/playground/" class="md-button md-button--primary" style="font-size: 1.1rem; padding: 0.75rem 2.25rem;">
    🚀 Launch Fullscreen Playground
  </a>
</div>

## Key Features

- **100% Client-Side Privacy**: Runs locally in your browser via [Pyodide](https://pyodide.org/) WebAssembly. No symbols or code leave your machine.
- **Dedicated Web Worker**: Parsing executes in a dedicated Web Worker off the main thread, keeping the user interface completely non-blocking during large syntax graph rendering.
- **14 Scheme Coverage**: Auto-detects or manually targets all 14 supported ABI schemes:
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
- **Formatting Styles**: Switch between standard LLVM style and GNU style (spacing, template formatting).
- **Output Formats**: Full demangled signature, both (input and demangled), base name only, or signature without parameters.
- **Structured Signature Decomposition**: Unpacks symbols into qualified name, base name, namespace scope, parameter list, return type, calling convention, and qualifiers via `demangle.signature()`.
- **Interactive AST Inspector**: Explore the abstract syntax tree hierarchically with collapsible nodes, lazy pagination for large graphs, jump navigation for shared references (`$ref`), or copy complete JSON syntax graphs.
- **Permalinks & Sharing**: State is automatically encoded into URL hash fragments for one-click sharing.
