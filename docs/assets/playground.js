/**
 * demangle Interactive In-Browser Playground
 * Client-side WebAssembly execution via Pyodide
 */

(function () {
    "use strict";

    // Guard: Only initialize if the playground container exists on this page
    const elApp = document.getElementById("playground-app");
    if (!elApp) {
        return;
    }

    // Prevent duplicate initializations
    if (window.__DEMANGLE_PLAYGROUND_INITIALIZED__) {
        return;
    }
    window.__DEMANGLE_PLAYGROUND_INITIALIZED__ = true;

    const DEBOUNCE_MS = 50;
    let pyodideInstance = null;
    let isReady = false;
    let isBusy = false;
    let debounceTimer = null;

    // DOM Elements
    let elStatusBadge, elStatusText, elInput, elScheme, elStyleRadios, elFormatRadios;
    let elDetectedTag, elOutputDisplay, elOutputStatus, elErrorBanner, elErrorMessage;
    let elSigQualified, elSigBase, elSigNamespace, elSigParams, elSigReturn, elSigCallingConv, elSigQualifiers, elSigSpecial;
    let elTreeContainer, elJsonContent, elTabTree, elTabJson, elPanelTree, elPanelJson;
    let btnClear, btnCopyOutput, btnCopyAst, btnExpandAll, btnCollapseAll;

    const PYTHON_BRIDGE_CODE = `
import json
import demangle
from demangle._signature import _labelled

def playground_demangle(symbol, language=None, style="llvm", output_format="full"):
    sym = (symbol or "").strip()
    if not sym:
        return json.dumps({
            "empty": True,
            "success": True,
            "symbol": "",
            "output": "",
            "demangled": "",
            "detected_language": None,
            "language": None,
            "qualified_name": None,
            "base_name": None,
            "namespace": None,
            "parameters": None,
            "return_type": None,
            "calling_convention": None,
            "qualifiers": [],
            "special": None,
            "ast": None,
            "error": None,
        })

    lang = language.strip().lower() if (language and language.strip().lower() not in ("auto", "")) else None
    style_val = style if style in ("llvm", "gnu") else "llvm"
    
    try:
        detected = demangle.detect(sym)
    except Exception:
        detected = None

    effective_lang = lang or detected

    res = {
        "empty": False,
        "symbol": sym,
        "style": style_val,
        "detected_language": detected,
        "language": effective_lang,
        "error": None,
    }

    # 1. Signature extraction and formatted output
    try:
        sig = demangle.signature(sym, language=lang, style=style_val)
        res["success"] = True
        res["language"] = sig.language
        res["demangled"] = sig.demangled
        res["qualified_name"] = sig.qualified_name
        res["base_name"] = sig.base_name
        res["namespace"] = sig.namespace
        res["parameters"] = list(sig.parameters) if sig.parameters is not None else None
        res["return_type"] = sig.return_type
        res["calling_convention"] = sig.calling_convention
        res["qualifiers"] = list(sig.qualifiers)
        res["special"] = sig.special

        if output_format == "both":
            res["output"] = f"{sym} ==> {sig.demangled}"
        elif output_format == "base-name":
            res["output"] = sig.base_name
        elif output_format == "no-params":
            dec = getattr(sig, "decoration", "") or ""
            res["output"] = f"{_labelled(sig)}{dec}"
        else:
            res["output"] = sig.demangled

    except demangle.DemanglingError as e:
        res["success"] = False
        res["error"] = str(e)
        try:
            fallback = demangle.demangle(sym, language=lang, style=style_val)
        except Exception:
            fallback = sym
        res["demangled"] = fallback
        res["qualified_name"] = None
        res["base_name"] = None
        res["namespace"] = None
        res["parameters"] = None
        res["return_type"] = None
        res["calling_convention"] = None
        res["qualifiers"] = []
        res["special"] = None
        if output_format == "both":
            res["output"] = f"{sym} ==> {fallback}"
        else:
            res["output"] = fallback
    except Exception as e:
        res["success"] = False
        res["error"] = str(e)
        res["demangled"] = sym
        res["output"] = sym
        res["qualified_name"] = None
        res["base_name"] = None
        res["namespace"] = None
        res["parameters"] = None
        res["return_type"] = None
        res["calling_convention"] = None
        res["qualifiers"] = []
        res["special"] = None

    # 2. AST tree parsing
    try:
        tree = demangle.parse(sym, language=lang, style=style_val)
        res["ast"] = tree.to_dict()
    except Exception as e:
        res["ast"] = None
        if not res.get("error"):
            res["error"] = str(e)

    return json.dumps(res)
`;

    function initDomReferences() {
        elStatusBadge = document.getElementById("playground-status-badge");
        elStatusText = document.getElementById("playground-status-text");
        elInput = document.getElementById("playground-input-symbol");
        elScheme = document.getElementById("playground-select-scheme");
        elStyleRadios = document.querySelectorAll('input[name="pg-style"]');
        elFormatRadios = document.querySelectorAll('input[name="pg-format"]');
        elDetectedTag = document.getElementById("detected-scheme-tag");

        elOutputDisplay = document.getElementById("output-display");
        elOutputStatus = document.getElementById("output-status-indicator");
        elErrorBanner = document.getElementById("output-error-banner");
        elErrorMessage = document.getElementById("output-error-message");

        elSigQualified = document.getElementById("sig-qualified");
        elSigBase = document.getElementById("sig-base");
        elSigNamespace = document.getElementById("sig-namespace");
        elSigParams = document.getElementById("sig-params");
        elSigReturn = document.getElementById("sig-return");
        elSigCallingConv = document.getElementById("sig-calling-conv");
        elSigQualifiers = document.getElementById("sig-qualifiers");
        elSigSpecial = document.getElementById("sig-special");

        elTreeContainer = document.getElementById("ast-tree-container");
        elJsonContent = document.getElementById("ast-json-content");
        elTabTree = document.getElementById("tab-btn-tree");
        elTabJson = document.getElementById("tab-btn-json");
        elPanelTree = document.getElementById("ast-tree-panel");
        elPanelJson = document.getElementById("ast-json-panel");

        btnClear = document.getElementById("btn-clear-input");
        btnCopyOutput = document.getElementById("btn-copy-output");
        btnCopyAst = document.getElementById("btn-copy-ast");
        btnExpandAll = document.getElementById("btn-ast-expand");
        btnCollapseAll = document.getElementById("btn-ast-collapse");
    }

    function updateStatus(state, message) {
        if (!elStatusBadge) return;
        elStatusBadge.className = `playground-status status-${state}`;
        const spinnerHtml = state === "loading" || state === "busy" ? '<span class="status-spinner" aria-hidden="true"></span> ' : '';
        elStatusBadge.innerHTML = `${spinnerHtml}<span id="playground-status-text">${escapeHtml(message)}</span>`;
    }

    function escapeHtml(str) {
        if (str === null || str === undefined) return "";
        return String(str)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function sanitizeClass(name) {
        return String(name || "").toLowerCase().replace(/[^a-z0-9_-]/g, "-");
    }

    function getSelectedRadio(radios, fallback) {
        for (const r of radios) {
            if (r.checked) return r.value;
        }
        return fallback;
    }

    function base64ToUint8Array(base64) {
        const binStr = atob(base64);
        const len = binStr.length;
        const bytes = new Uint8Array(len);
        for (let i = 0; i < len; i++) {
            bytes[i] = binStr.charCodeAt(i);
        }
        return bytes;
    }

    async function loadDemangleBytes() {
        if (window.DEMANGLE_BUNDLE_BASE64) {
            return base64ToUint8Array(window.DEMANGLE_BUNDLE_BASE64);
        }
        const resp = await fetch(new URL("../assets/demangle.whl", window.location.href));
        if (!resp.ok) {
            throw new Error(`Failed to fetch demangle.whl: HTTP ${resp.status}`);
        }
        const buf = await resp.arrayBuffer();
        return new Uint8Array(buf);
    }

    function loadScript(url) {
        return new Promise((resolve, reject) => {
            const script = document.createElement("script");
            script.src = url;
            script.onload = () => resolve();
            script.onerror = () => reject(new Error(`Failed to load script ${url}`));
            document.head.appendChild(script);
        });
    }

    // Build collapsible AST Tree DOM
    function buildAstDom(data, roleKey = "root", isRoot = true) {
        if (data === null || data === undefined) {
            const leaf = document.createElement("div");
            leaf.className = "ast-leaf";
            leaf.innerHTML = `<span class="ast-role">${escapeHtml(roleKey)}:</span> <span class="ast-val-null">null</span>`;
            return leaf;
        }

        if (typeof data !== "object") {
            const leaf = document.createElement("div");
            leaf.className = "ast-leaf";
            let valClass = typeof data === "number" ? "ast-val-num" : (typeof data === "boolean" ? "ast-val-bool" : "ast-val-str");
            leaf.innerHTML = `<span class="ast-role">${escapeHtml(roleKey)}:</span> <span class="${valClass}">${escapeHtml(JSON.stringify(data))}</span>`;
            return leaf;
        }

        // Shared Graph Reference ($ref)
        if ("$ref" in data) {
            const leaf = document.createElement("div");
            leaf.className = "ast-leaf ast-ref-item";
            leaf.innerHTML = `
                <span class="ast-role">${escapeHtml(roleKey)}:</span>
                <span class="ast-ref-badge" data-ref-target="${data.$ref}" title="Jump to shared node #${data.$ref}">
                    <span class="ast-ref-icon">🔗</span> ref #${data.$ref}
                </span>`;
            const badge = leaf.querySelector(".ast-ref-badge");
            if (badge) {
                badge.addEventListener("click", (e) => {
                    e.stopPropagation();
                    const targetNode = document.getElementById(`ast-node-id-${data.$ref}`);
                    if (targetNode) {
                        targetNode.open = true;
                        targetNode.scrollIntoView({ behavior: "smooth", block: "center" });
                        targetNode.classList.add("ast-node-highlight");
                        setTimeout(() => targetNode.classList.remove("ast-node-highlight"), 1500);
                    }
                });
            }
            return leaf;
        }

        // Array of items
        if (Array.isArray(data)) {
            if (data.length === 0) {
                const leaf = document.createElement("div");
                leaf.className = "ast-leaf";
                leaf.innerHTML = `<span class="ast-role">${escapeHtml(roleKey)}:</span> <span class="ast-val-empty">[]</span>`;
                return leaf;
            }

            const details = document.createElement("details");
            details.className = "ast-branch ast-array-branch";
            details.open = true;

            const summary = document.createElement("summary");
            summary.className = "ast-summary";
            summary.innerHTML = `
                <span class="ast-role">${escapeHtml(roleKey)}</span>
                <span class="ast-count-badge">[${data.length}]</span>`;
            details.appendChild(summary);

            const list = document.createElement("ul");
            list.className = "ast-list";
            data.forEach((child, idx) => {
                const li = document.createElement("li");
                li.className = "ast-list-item";
                li.appendChild(buildAstDom(child, `[${idx}]`, false));
                list.appendChild(li);
            });
            details.appendChild(list);
            return details;
        }

        // Compound Node Object
        const kind = data.kind || "object";
        const idVal = data.id !== undefined ? data.id : null;

        const details = document.createElement("details");
        details.className = `ast-branch ast-node-branch ast-kind-${sanitizeClass(kind)}`;
        if (idVal !== null) {
            details.id = `ast-node-id-${idVal}`;
        }
        details.open = true;

        let preview = "";
        if (data.text) {
            preview = `<span class="ast-preview">"${escapeHtml(data.text)}"</span>`;
        } else if (data.spelling) {
            preview = `<span class="ast-preview">"${escapeHtml(data.spelling)}"</span>`;
        } else if (kind === "pointer_auth") {
            preview = `<span class="ast-preview-ptrauth">key=${data.key}, disc=${data.discriminator}, addr=${data.address_discriminated}</span>`;
        } else if (kind === "function" && data.convention) {
            preview = `<span class="ast-preview">${escapeHtml(data.convention)}</span>`;
        }

        const summary = document.createElement("summary");
        summary.className = "ast-summary";
        summary.innerHTML = `
            ${!isRoot ? `<span class="ast-role">${escapeHtml(roleKey)}:</span>` : ""}
            <span class="ast-kind-badge ast-badge-${sanitizeClass(kind)}">${escapeHtml(kind)}</span>
            ${idVal !== null ? `<span class="ast-id-badge" title="Node ID #${idVal}">#${idVal}</span>` : ""}
            ${preview}`;
        details.appendChild(summary);

        const list = document.createElement("ul");
        list.className = "ast-list";

        const priorityOrder = ["name", "inner", "parameters", "returns", "pointer_auth", "arguments", "parts", "text", "spelling"];
        const skipKeys = new Set(["kind", "id"]);
        const keys = Object.keys(data).filter((k) => !skipKeys.has(k));
        keys.sort((a, b) => {
            const ia = priorityOrder.indexOf(a);
            const ib = priorityOrder.indexOf(b);
            if (ia !== -1 && ib !== -1) return ia - ib;
            if (ia !== -1) return -1;
            if (ib !== -1) return 1;
            return a.localeCompare(b);
        });

        keys.forEach((k) => {
            const li = document.createElement("li");
            li.className = "ast-list-item";
            li.appendChild(buildAstDom(data[k], k, false));
            list.appendChild(li);
        });

        details.appendChild(list);
        return details;
    }

    function renderResults(res) {
        if (!res || res.empty) {
            if (elOutputStatus) {
                elOutputStatus.className = "output-status-pill pill-idle";
                elOutputStatus.textContent = "Idle";
            }
            if (elDetectedTag) elDetectedTag.textContent = "—";
            if (elErrorBanner) elErrorBanner.style.display = "none";
            if (elOutputDisplay) {
                elOutputDisplay.innerHTML = '<span class="output-placeholder">Enter a symbol above to view demangled output...</span>';
            }

            if (elSigQualified) elSigQualified.textContent = "—";
            if (elSigBase) elSigBase.textContent = "—";
            if (elSigNamespace) elSigNamespace.textContent = "—";
            if (elSigParams) elSigParams.textContent = "—";
            if (elSigReturn) elSigReturn.textContent = "—";
            if (elSigCallingConv) elSigCallingConv.textContent = "—";
            if (elSigQualifiers) elSigQualifiers.textContent = "—";
            if (elSigSpecial) elSigSpecial.textContent = "—";

            if (elTreeContainer) {
                elTreeContainer.innerHTML = '<div class="ast-placeholder">Enter a symbol above to inspect its parsed syntax graph...</div>';
            }
            if (elJsonContent) elJsonContent.textContent = "{}";
            return;
        }

        // Scheme tag
        if (elDetectedTag) {
            elDetectedTag.textContent = res.detected_language || res.language || "none";
        }

        // Demangle failure / error state
        if (!res.success) {
            if (elOutputStatus) {
                elOutputStatus.className = "output-status-pill pill-invalid";
                elOutputStatus.textContent = "Invalid";
            }
            if (elErrorBanner) elErrorBanner.style.display = "flex";
            if (elErrorMessage) elErrorMessage.textContent = res.error || "Failed to demangle symbol";
            if (elOutputDisplay) elOutputDisplay.textContent = res.output || res.symbol;

            if (elSigQualified) elSigQualified.textContent = "—";
            if (elSigBase) elSigBase.textContent = "—";
            if (elSigNamespace) elSigNamespace.textContent = "—";
            if (elSigParams) elSigParams.textContent = "—";
            if (elSigReturn) elSigReturn.textContent = "—";
            if (elSigCallingConv) elSigCallingConv.textContent = "—";
            if (elSigQualifiers) elSigQualifiers.textContent = "—";
            if (elSigSpecial) elSigSpecial.textContent = "—";

            if (elTreeContainer) {
                elTreeContainer.innerHTML = '<div class="ast-placeholder-error">No AST available for unparseable symbol.</div>';
            }
            if (elJsonContent) elJsonContent.textContent = JSON.stringify({ error: res.error, symbol: res.symbol }, null, 2);
            return;
        }

        // Success state
        if (elOutputStatus) {
            elOutputStatus.className = "output-status-pill pill-valid";
            elOutputStatus.textContent = "Valid";
        }
        if (elErrorBanner) elErrorBanner.style.display = "none";
        if (elOutputDisplay) elOutputDisplay.textContent = res.output;

        // Structured signature panel
        if (elSigQualified) elSigQualified.textContent = res.qualified_name || "—";
        if (elSigBase) elSigBase.textContent = res.base_name || "—";
        if (elSigNamespace) elSigNamespace.textContent = res.namespace || "—";

        if (elSigParams) {
            if (Array.isArray(res.parameters) && res.parameters.length > 0) {
                elSigParams.innerHTML = res.parameters.map((p) => `<span class="param-chip">${escapeHtml(p)}</span>`).join("");
            } else if (Array.isArray(res.parameters) && res.parameters.length === 0) {
                elSigParams.innerHTML = '<span class="param-chip param-empty">() (void / no parameters)</span>';
            } else {
                elSigParams.textContent = "None (not encoded)";
            }
        }

        if (elSigReturn) elSigReturn.textContent = res.return_type || "—";
        if (elSigCallingConv) elSigCallingConv.textContent = res.calling_convention || "—";
        if (elSigQualifiers) elSigQualifiers.textContent = res.qualifiers && res.qualifiers.length ? res.qualifiers.join(", ") : "—";
        if (elSigSpecial) elSigSpecial.textContent = res.special || "—";

        // AST Visualizer
        if (res.ast) {
            if (elTreeContainer) {
                elTreeContainer.innerHTML = "";
                elTreeContainer.appendChild(buildAstDom(res.ast, "root", true));
            }
            if (elJsonContent) {
                elJsonContent.textContent = JSON.stringify(res.ast, null, 2);
            }
        } else {
            if (elTreeContainer) {
                elTreeContainer.innerHTML = '<div class="ast-placeholder">AST unavailable for this symbol.</div>';
            }
            if (elJsonContent) {
                elJsonContent.textContent = "{}";
            }
        }
    }

    function executeDemangle() {
        if (!isReady || !pyodideInstance || isBusy) return;

        const sym = elInput ? elInput.value.trim() : "";
        const scheme = elScheme ? elScheme.value : "auto";
        const style = getSelectedRadio(elStyleRadios, "llvm");
        const fmt = getSelectedRadio(elFormatRadios, "full");

        try {
            isBusy = true;
            updateStatus("busy", "Demangling...");
            const runner = pyodideInstance.globals.get("playground_demangle");
            const rawJson = runner(sym, scheme, style, fmt);
            const data = JSON.parse(rawJson);
            renderResults(data);
            updateStatus("ready", "Ready (WebAssembly)");
        } catch (err) {
            console.error("Demangling execution error:", err);
            renderResults({
                success: false,
                symbol: sym,
                error: err.message,
                output: sym,
            });
            updateStatus("ready", "Ready (WebAssembly)");
        } finally {
            isBusy = false;
        }
    }

    function scheduleDemangle() {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(executeDemangle, DEBOUNCE_MS);
    }

    function setupEventListeners() {
        if (elInput) {
            elInput.addEventListener("input", scheduleDemangle);
            elInput.addEventListener("change", executeDemangle);
        }
        if (elScheme) {
            elScheme.addEventListener("change", executeDemangle);
        }
        elStyleRadios.forEach((r) => r.addEventListener("change", executeDemangle));
        elFormatRadios.forEach((r) => r.addEventListener("change", executeDemangle));

        if (btnClear) {
            btnClear.addEventListener("click", () => {
                if (elInput) {
                    elInput.value = "";
                    executeDemangle();
                    elInput.focus();
                }
            });
        }

        // Preset buttons
        document.querySelectorAll(".preset-btn").forEach((btn) => {
            btn.addEventListener("click", () => {
                document.querySelectorAll(".preset-btn").forEach((b) => b.classList.remove("active"));
                btn.classList.add("active");

                const sym = btn.dataset.symbol || "";
                if (elInput) elInput.value = sym;
                if (btn.dataset.scheme && elScheme) elScheme.value = btn.dataset.scheme;
                if (btn.dataset.style) {
                    elStyleRadios.forEach((r) => {
                        r.checked = r.value === btn.dataset.style;
                    });
                }
                if (btn.dataset.format) {
                    elFormatRadios.forEach((r) => {
                        r.checked = r.value === btn.dataset.format;
                    });
                }
                executeDemangle();
            });
        });

        // Tab switcher
        if (elTabTree && elTabJson && elPanelTree && elPanelJson) {
            elTabTree.addEventListener("click", () => {
                elTabTree.classList.add("active");
                elTabTree.setAttribute("aria-selected", "true");
                elTabJson.classList.remove("active");
                elTabJson.setAttribute("aria-selected", "false");
                elPanelTree.style.display = "block";
                elPanelJson.style.display = "none";
            });

            elTabJson.addEventListener("click", () => {
                elTabJson.classList.add("active");
                elTabJson.setAttribute("aria-selected", "true");
                elTabTree.classList.remove("active");
                elTabTree.setAttribute("aria-selected", "false");
                elPanelJson.style.display = "block";
                elPanelTree.style.display = "none";
            });
        }

        // Tree Expansion / Collapse
        if (btnExpandAll && elTreeContainer) {
            btnExpandAll.addEventListener("click", () => {
                elTreeContainer.querySelectorAll("details").forEach((d) => {
                    d.open = true;
                });
            });
        }

        if (btnCollapseAll && elTreeContainer) {
            btnCollapseAll.addEventListener("click", () => {
                elTreeContainer.querySelectorAll("details").forEach((d) => {
                    d.open = false;
                });
                const root = elTreeContainer.querySelector(":scope > details");
                if (root) root.open = true;
            });
        }

        // Copy Handlers
        if (btnCopyOutput && elOutputDisplay) {
            btnCopyOutput.addEventListener("click", async () => {
                const text = elOutputDisplay.textContent;
                if (!text || text.includes("Enter a symbol above")) return;
                try {
                    await navigator.clipboard.writeText(text);
                    const label = btnCopyOutput.querySelector(".copy-label");
                    if (label) {
                        const orig = label.textContent;
                        label.textContent = "Copied! ✓";
                        setTimeout(() => {
                            label.textContent = orig;
                        }, 1800);
                    }
                } catch (e) {
                    console.error("Failed to copy:", e);
                }
            });
        }

        if (btnCopyAst && elJsonContent) {
            btnCopyAst.addEventListener("click", async () => {
                const text = elJsonContent.textContent;
                if (!text || text === "{}") return;
                try {
                    await navigator.clipboard.writeText(text);
                    const label = btnCopyAst.querySelector(".copy-label");
                    if (label) {
                        const orig = label.textContent;
                        label.textContent = "Copied! ✓";
                        setTimeout(() => {
                            label.textContent = orig;
                        }, 1800);
                    }
                } catch (e) {
                    console.error("Failed to copy:", e);
                }
            });
        }
    }

    async function initPyodideRuntime() {
        initDomReferences();
        setupEventListeners();

        updateStatus("loading", "Booting Pyodide WebAssembly runtime...");

        try {
            if (typeof loadPyodide === "undefined") {
                updateStatus("loading", "Loading Pyodide from CDN...");
                await loadScript("https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js");
            }

            pyodideInstance = await loadPyodide({
                indexURL: "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/",
            });

            updateStatus("loading", "Mounting demangle bundle into virtual filesystem...");
            const wheelBytes = await loadDemangleBytes();
            pyodideInstance.FS.writeFile("/demangle.whl", wheelBytes);

            updateStatus("loading", "Preloading demangle schemes and AST compiler...");
            await pyodideInstance.runPythonAsync(`
import sys
if "/demangle.whl" not in sys.path:
    sys.path.insert(0, "/demangle.whl")
import demangle
demangle.preload()
` + PYTHON_BRIDGE_CODE);

            isReady = true;
            updateStatus("ready", "Ready (WebAssembly)");

            // If an initial input or preset is set, trigger execution
            const firstPreset = document.querySelector(".preset-btn");
            if (firstPreset) {
                firstPreset.click();
            } else if (elInput && elInput.value.trim()) {
                executeDemangle();
            }
        } catch (err) {
            console.error("Pyodide initialization failure:", err);
            updateStatus("error", `Failed to initialize: ${err.message}`);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initPyodideRuntime);
    } else {
        initPyodideRuntime();
    }
})();
