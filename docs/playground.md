# Interactive Demangling Playground

Explore mangled symbols interactively in your browser. All demangling, scheme detection, structured signature extraction, and AST graph parsing run entirely client-side using **WebAssembly** via [Pyodide](https://pyodide.org/). Symbols stay in your browser. The runtime is downloaded from a CDN on first load; demangling needs no server requests after initialization.

<div id="playground-app" class="playground-root">
  <!-- Top Status & Runtime Banner -->
  <div class="playground-header">
    <div class="playground-header-info">
      <span class="playground-title-badge">⚡ In-Browser WebAssembly Engine</span>
      <span id="playground-engine-version" class="playground-engine-tag">Pyodide • Pure-Python demangle</span>
    </div>
    <div id="playground-status-badge" class="playground-status status-loading" role="status" aria-live="polite">
      <span class="status-spinner" aria-hidden="true"></span>
      <span id="playground-status-text">Loading Pyodide runtime...</span>
    </div>
    <button type="button" id="btn-retry-runtime" class="btn-action-small" hidden>Retry runtime</button>
  </div>

  <!-- Presets Toolbar -->
  <div class="playground-presets-section">
    <div class="presets-label">
      <span>Preset Examples:</span>
      <span class="presets-hint">(Click to load representative vector)</span>
    </div>
    <div class="presets-grid" id="playground-presets-container">
      <button type="button" aria-pressed="false" class="preset-btn preset-bleeding" data-symbol="??__MS@@QEAA?AVstrong_ordering@std@@AEBU0@@Z" data-scheme="msvc" data-style="llvm" data-format="full" title="MSVC C++20 three-way comparison operator<=>">
        <span class="preset-badge">C++20</span> MSVC Spaceship
      </button>
      <button type="button" aria-pressed="false" class="preset-btn preset-bleeding" data-symbol="?foo@@YAXPEAPE__ptrauth20OK@AH@Z" data-scheme="msvc" data-style="llvm" data-format="full" title="MSVC ARM64e pointer authentication qualifiers">
        <span class="preset-badge">ARM64e</span> MSVC ptrauth
      </button>
      <button type="button" aria-pressed="false" class="preset-btn preset-bleeding" data-symbol="$sSRyxG15Synchronization19AtomicRepresentableABRi1_zrlMc" data-scheme="swift" data-style="llvm" data-format="full" title="Swift 6 inverted protocol descriptor ~Deinitable">
        <span class="preset-badge">Swift 6</span> ~Deinitable
      </button>
      <button type="button" aria-pressed="false" class="preset-btn" data-symbol="_ZNSt6vectorIiSaIiEE9push_backERKi" data-scheme="itanium" data-style="llvm" data-format="full" title="Itanium C++ std::vector::push_back">
        <span class="preset-badge">C++</span> Itanium Vector
      </button>
      <button type="button" aria-pressed="false" class="preset-btn" data-symbol="_RNvC6_123foo3bar" data-scheme="rust" data-style="llvm" data-format="full" title="Rust v0 mangling path">
        <span class="preset-badge">Rust</span> Rust v0
      </button>
      <button type="button" aria-pressed="false" class="preset-btn" data-symbol="_D3std5stdio12__ModuleInfoZ" data-scheme="d" data-style="llvm" data-format="full" title="D Language ModuleInfo">
        <span class="preset-badge">D</span> std.stdio
      </button>
      <button type="button" aria-pressed="false" class="preset-btn" data-symbol="@Unit@TMyClass@MyMethod$qqrv" data-scheme="delphi" data-style="llvm" data-format="full" title="Delphi / C++Builder method">
        <span class="preset-badge">Delphi</span> TMyClass
      </button>
      <button type="button" aria-pressed="false" class="preset-btn" data-symbol="+[NSString stringWithFormat:]" data-scheme="objc" data-style="llvm" data-format="full" title="Objective-C class method">
        <span class="preset-badge">Obj-C</span> NSString
      </button>
      <button type="button" aria-pressed="false" class="preset-btn" data-symbol="Java_com_example_Native_compute" data-scheme="jni" data-style="llvm" data-format="full" title="Java Native Interface method">
        <span class="preset-badge">JNI</span> Java Native
      </button>
    </div>
  </div>

  <!-- Input Card -->
  <div class="playground-card playground-input-card">
    <div class="card-header">
      <label for="playground-input-symbol" class="card-title">Mangled Symbol Input</label>
      <div class="card-actions">
        <button type="button" id="btn-share" class="btn-copy" data-label="Copy link" title="Copy a link containing this symbol and selected settings"><span class="copy-label">Copy link</span></button>
        <button type="button" id="btn-clear-input" class="btn-action-small" title="Clear input">Clear</button>
      </div>
    </div>
    <div class="input-wrapper">
      <textarea id="playground-input-symbol" class="playground-textarea" rows="3" spellcheck="false" placeholder="Type or paste a mangled symbol (e.g. _ZNSt6vector... or ??__MS...)"></textarea>
    </div>
  </div>

  <!-- Controls Bar: Scheme, Style, Format -->
  <div class="playground-controls-grid">
    <!-- Scheme Selector -->
    <div class="control-group">
      <label for="playground-select-scheme" class="control-label">Scheme / Language</label>
      <div class="select-wrapper">
        <select id="playground-select-scheme" class="playground-select">
          <option value="auto" selected>Auto-detect (default)</option>
          <option value="ada">Ada / GNAT (ada)</option>
          <option value="codewarrior">CodeWarrior C++ (codewarrior)</option>
          <option value="d">D (d)</option>
          <option value="delphi">Delphi / C++Builder (delphi)</option>
          <option value="gnuv2">GNU v2 C++ (gnuv2)</option>
          <option value="go">Go (go)</option>
          <option value="itanium">Itanium C++ (itanium)</option>
          <option value="jni">Java Native Interface (jni)</option>
          <option value="msvc">MSVC C++ (msvc)</option>
          <option value="nim">Nim (nim)</option>
          <option value="objc">Objective-C (objc)</option>
          <option value="pascal">Free Pascal (pascal)</option>
          <option value="rust">Rust (rust)</option>
          <option value="swift">Swift (swift)</option>
        </select>
      </div>
      <div class="control-subtext">
        <span>Detected: </span>
        <span id="detected-scheme-tag" class="detected-tag">—</span>
      </div>
    </div>

    <!-- Style Selector -->
    <div class="control-group">
      <span class="control-label">Formatting Style</span>
      <div class="segmented-control" id="control-group-style" role="group" aria-label="Formatting style">
        <label class="segmented-option">
          <input type="radio" name="pg-style" value="llvm" checked>
          <span>LLVM</span>
        </label>
        <label class="segmented-option">
          <input type="radio" name="pg-style" value="gnu">
          <span>GNU</span>
        </label>
      </div>
      <div class="control-subtext">Scheme-specific spelling</div>
    </div>

    <!-- Output Format Selector -->
    <div class="control-group">
      <span class="control-label">Output View</span>
      <div class="segmented-control" id="control-group-format" role="group" aria-label="Output view">
        <label class="segmented-option">
          <input type="radio" name="pg-format" value="full" checked>
          <span>Full</span>
        </label>
        <label class="segmented-option">
          <input type="radio" name="pg-format" value="both">
          <span>Both</span>
        </label>
        <label class="segmented-option">
          <input type="radio" name="pg-format" value="base-name">
          <span>Base Name</span>
        </label>
        <label class="segmented-option">
          <input type="radio" name="pg-format" value="no-params">
          <span>No Params</span>
        </label>
      </div>
      <div class="control-subtext">Matches CLI display modes</div>
    </div>
  </div>

  <!-- Demangled Output Card -->
  <div class="playground-card playground-output-card">
    <div class="card-header">
      <div class="card-title-group">
        <span class="card-title">Demangled Output</span>
        <span id="output-status-indicator" class="output-status-pill pill-idle">Idle</span>
      </div>
      <button type="button" id="btn-copy-output" class="btn-copy" data-label="Copy" disabled title="Copy demangled string">
        <span class="copy-icon">📋</span> <span class="copy-label">Copy</span>
      </button>
    </div>
    <div class="output-container">
      <div id="output-error-banner" class="playground-error-banner" style="display: none;" role="alert">
        <span class="error-icon">⚠️</span>
        <span id="output-error-message" class="error-message"></span>
      </div>
      <div id="output-display" class="output-code-display" tabindex="0">
        <span class="output-placeholder">Enter a symbol above to view demangled output...</span>
      </div>
    </div>
  </div>

  <!-- Structured Signature Panel -->
  <div class="playground-card playground-sig-card" id="playground-signature-panel">
    <div class="card-header">
      <span class="card-title">Structured Signature Decomposition</span>
      <span class="card-badge">demangle.signature()</span>
    </div>
    <div class="sig-grid">
      <div class="sig-cell">
        <span class="sig-label">Qualified Name</span>
        <code id="sig-qualified" class="sig-val">—</code>
      </div>
      <div class="sig-cell">
        <span class="sig-label">Base Name</span>
        <code id="sig-base" class="sig-val">—</code>
      </div>
      <div class="sig-cell">
        <span class="sig-label">Namespace / Scope</span>
        <code id="sig-namespace" class="sig-val">—</code>
      </div>
      <div class="sig-cell">
        <span class="sig-label">Parameters</span>
        <div id="sig-params" class="sig-val-params">—</div>
      </div>
      <div class="sig-cell">
        <span class="sig-label">Return Type</span>
        <code id="sig-return" class="sig-val">—</code>
      </div>
      <div class="sig-cell">
        <span class="sig-label">Calling Convention</span>
        <code id="sig-calling-conv" class="sig-val">—</code>
      </div>
      <div class="sig-cell">
        <span class="sig-label">Qualifiers / Modifiers</span>
        <code id="sig-qualifiers" class="sig-val">—</code>
      </div>
      <div class="sig-cell">
        <span class="sig-label">Special Entity</span>
        <code id="sig-special" class="sig-val">—</code>
      </div>
    </div>
  </div>

  <!-- Interactive AST Visualizer Section -->
  <div class="playground-card playground-ast-card" id="playground-ast-section">
    <div class="card-header">
      <div class="ast-tab-bar" role="tablist" aria-label="AST view">
        <button type="button" id="tab-btn-tree" class="tab-btn active" role="tab" aria-selected="true" aria-controls="ast-tree-panel">
          🌳 AST Tree View
        </button>
        <button type="button" id="tab-btn-json" class="tab-btn" role="tab" tabindex="-1" aria-selected="false" aria-controls="ast-json-panel">
          { } Raw JSON
        </button>
      </div>
      <div class="ast-actions">
        <button type="button" id="btn-ast-expand" class="btn-action-small" disabled title="Expand all nodes">Expand All</button>
        <button type="button" id="btn-ast-collapse" class="btn-action-small" disabled title="Collapse all nodes">Collapse All</button>
        <button type="button" id="btn-copy-ast" class="btn-copy" data-label="Copy AST" disabled title="Copy complete AST JSON">
          <span class="copy-icon">📋</span> <span class="copy-label">Copy AST</span>
        </button>
      </div>
    </div>

    <!-- Tree View Panel -->
    <div id="ast-tree-panel" class="ast-panel active" role="tabpanel" aria-labelledby="tab-btn-tree" tabindex="0">
      <p id="ast-render-notice" class="ast-render-notice" role="status" hidden></p>
      <div id="ast-tree-container" class="ast-tree-root">
        <div class="ast-placeholder">Enter a symbol above to inspect its parsed syntax graph...</div>
      </div>
    </div>

    <!-- Raw JSON Panel -->
    <div id="ast-json-panel" class="ast-panel" role="tabpanel" aria-labelledby="tab-btn-json" tabindex="0" hidden>
      <pre class="ast-json-pre"><code id="ast-json-content" class="language-json">{}</code></pre>
    </div>
  </div>
</div>

Use **Copy link** to share the current symbol, scheme, style, and output view. Sharing explicitly includes the symbol in the URL fragment; no link is created automatically. The runtime banner shows the library version used by the current page.
