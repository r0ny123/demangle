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
    const assetBase = new URL(".", document.currentScript.src);
    const MAX_TREE_NODES = 1500;
    const TREE_PAGE_SIZE = 50;
    let worker = null;
    let isReady = false;
    let activeRequest = null;
    let pendingRequest = null;
    let latestRequestId = 0;
    let cachedResult = null;
    let runtimeTimer = null;
    let treeNodes = 0;
    let renderedNodes = new WeakMap();
    let referencePaths = new Map();
    let debounceTimer = null;

    // DOM Elements
    let elStatusBadge, elInput, elScheme, elStyleRadios, elFormatRadios;
    let elDetectedTag, elOutputDisplay, elOutputStatus, elErrorBanner, elErrorMessage;
    let elSigQualified, elSigBase, elSigNamespace, elSigParams, elSigReturn, elSigCallingConv, elSigQualifiers, elSigSpecial;
    let elTreeContainer, elJsonContent, elTabTree, elTabJson, elPanelTree, elPanelJson;
    let btnClear, btnCopyOutput, btnCopyAst, btnExpandAll, btnCollapseAll;

    function initDomReferences() {
        elStatusBadge = document.getElementById("playground-status-badge");
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

    function treeLimitReached() {
        const notice = document.getElementById("ast-render-notice");
        notice.hidden = false;
        notice.textContent = "Tree display limited to 1,500 entries. Raw JSON and Copy AST contain the complete graph.";
    }

    function indexReferences(root) {
        referencePaths = new Map();
        const stack = [[root, []]];
        while (stack.length) {
            const [data, parents] = stack.pop();
            if (!data || typeof data !== "object" || "$ref" in data) continue;
            const path = [...parents, data];
            if (data.id !== undefined) referencePaths.set(data.id, path);
            for (const value of Object.values(data)) {
                if (value && typeof value === "object") stack.push([value, path]);
            }
        }
    }

    function revealReference(id) {
        const path = referencePaths.get(id);
        if (!path) return;
        for (let i = 0; i < path.length; i++) {
            const branch = renderedNodes.get(path[i]);
            if (!branch) { treeLimitReached(); return; }
            branch.open = true;
            branch.populate(path[i + 1]);
        }
        const target = renderedNodes.get(path[path.length - 1]);
        target.querySelector("summary").focus();
        target.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
        target.classList.add("ast-node-highlight");
        setTimeout(() => target.classList.remove("ast-node-highlight"), 1500);
    }

    // Only create children when opened, and page wide arrays under a global DOM budget.
    function buildAstDom(data, roleKey = "root", isRoot = false) {
        treeNodes++;
        if (data === null || typeof data !== "object") {
            const leaf = document.createElement("div");
            leaf.className = "ast-leaf";
            leaf.textContent = `${roleKey}: ${JSON.stringify(data)}`;
            return leaf;
        }
        if ("$ref" in data) {
            const leaf = document.createElement("div");
            leaf.className = "ast-leaf ast-ref-item";
            const role = document.createElement("span");
            role.className = "ast-role";
            role.textContent = `${roleKey}:`;
            const button = document.createElement("button");
            button.type = "button";
            button.className = "ast-ref-badge";
            button.textContent = `↗ ref #${data.$ref}`;
            button.title = `Jump to shared node #${data.$ref}`;
            button.addEventListener("click", () => revealReference(data.$ref));
            leaf.append(role, button);
            return leaf;
        }
        const isArray = Array.isArray(data);
        const kind = data.kind || "object";
        const details = document.createElement("details");
        details.className = `ast-branch ast-kind-${sanitizeClass(kind)}`;
        if (data.id !== undefined) details.id = `ast-node-id-${data.id}`;
        renderedNodes.set(data, details);
        const summary = document.createElement("summary");
        summary.className = "ast-summary";
        summary.tabIndex = 0;
        const preview = data.text || data.spelling || data.convention || "";
        summary.textContent = isArray ? `${roleKey} [${data.length}]` : `${isRoot ? "" : roleKey + ": "}${kind}${data.id !== undefined ? " #" + data.id : ""}${preview ? " — " + preview : ""}`;
        details.append(summary);
        const list = document.createElement("ul");
        list.className = "ast-list";
        details.append(list);
        const priority = ["name", "inner", "parameters", "returns", "pointer_auth", "arguments", "parts", "text", "spelling"];
        const keys = Object.keys(data).filter(k => k !== "kind" && k !== "id");
        if (!isArray) keys.sort((a, b) => {
            const ia = priority.indexOf(a), ib = priority.indexOf(b);
            return (ia < 0 ? priority.length : ia) - (ib < 0 ? priority.length : ib) || a.localeCompare(b);
        });
        let cursor = 0;
        let more = null;
        details.populate = (target, all = false, nextPage = false) => {
            if (more) more.remove();
            const targetIndex = target ? keys.findIndex(k => data[k] === target) : -1;
            const end = all ? keys.length : Math.max(nextPage ? cursor + TREE_PAGE_SIZE : cursor || TREE_PAGE_SIZE, targetIndex + 1);
            while (cursor < Math.min(end, keys.length) && treeNodes < MAX_TREE_NODES) {
                const key = keys[cursor++];
                const item = document.createElement("li");
                item.className = "ast-list-item";
                item.append(buildAstDom(data[key], isArray ? `[${key}]` : key));
                list.append(item);
            }
            if (cursor < keys.length) {
                more = document.createElement("button");
                more.type = "button";
                more.className = "btn-action-small ast-load-more";
                more.textContent = `Show more (${keys.length - cursor} remaining)`;
                more.disabled = treeNodes >= MAX_TREE_NODES;
                more.addEventListener("click", () => details.populate(undefined, false, true));
                list.append(more);
                if (more.disabled) treeLimitReached();
            }
        };
        details.addEventListener("toggle", () => { if (details.open) details.populate(); });
        if (isRoot) { details.open = true; details.populate(); }
        return details;
    }

    function renderOutput(res) {
        const format = getSelectedRadio(elFormatRadios, "full");
        elOutputDisplay.textContent = res.output_variants?.[format] ?? res.demangled ?? res.symbol;
    }

    function renderResults(res) {
        treeNodes = 0;
        renderedNodes = new WeakMap();
        document.getElementById("ast-render-notice").hidden = true;
        btnCopyOutput.disabled = !res || res.empty;
        btnCopyAst.disabled = !res?.ast;
        btnExpandAll.disabled = !res?.ast;
        btnCollapseAll.disabled = !res?.ast;
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
            renderOutput(res);

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
        renderOutput(res);

        // Structured signature panel
        if (elSigQualified) elSigQualified.textContent = res.qualified_name || "—";
        if (elSigBase) elSigBase.textContent = res.base_name || "—";
        if (elSigNamespace) elSigNamespace.textContent = res.namespace || "—";

        if (elSigParams) {
            if (Array.isArray(res.parameters) && res.parameters.length > 0) {
                elSigParams.innerHTML = res.parameters.slice(0, TREE_PAGE_SIZE).map((p) => `<span class="param-chip">${escapeHtml(p)}</span>`).join("");
                if (res.parameters.length > TREE_PAGE_SIZE) {
                    const note = document.createElement("span");
                    note.textContent = `… ${res.parameters.length - TREE_PAGE_SIZE} more (see Raw JSON)`;
                    elSigParams.append(note);
                }
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
                indexReferences(res.ast);
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

    function requestKey() {
        return JSON.stringify([elInput.value.trim(), elScheme.value, getSelectedRadio(elStyleRadios, "llvm")]);
    }

    function dispatchPending() {
        if (!isReady || activeRequest || !pendingRequest) return;
        activeRequest = pendingRequest;
        pendingRequest = null;
        updateStatus("busy", "Demangling…");
        worker.postMessage({ type: "demangle", ...activeRequest });
        clearTimeout(runtimeTimer);
        runtimeTimer = setTimeout(() => runtimeFailed("Demangling took too long. Try a shorter symbol or retry."), 15000);
    }

    function executeDemangle() {
        clearTimeout(debounceTimer);
        latestRequestId++;
        const key = requestKey();
        if (!elInput.value.trim()) {
            pendingRequest = null;
            renderResults({ empty: true });
            if (isReady) updateStatus("ready", "Ready (WebAssembly)");
            return;
        }
        if (cachedResult?.key === key) {
            pendingRequest = null;
            renderResults(cachedResult.data);
            if (isReady) updateStatus("ready", "Ready (WebAssembly)");
            return;
        }
        pendingRequest = { id: latestRequestId, key, symbol: elInput.value.trim(), language: elScheme.value, style: getSelectedRadio(elStyleRadios, "llvm") };
        btnCopyOutput.disabled = true;
        btnCopyAst.disabled = true;
        elOutputStatus.textContent = "Updating";
        elOutputStatus.className = "output-status-pill pill-idle";
        dispatchPending();
    }

    function scheduleDemangle() {
        latestRequestId++;
        pendingRequest = null;
        btnCopyOutput.disabled = true;
        btnCopyAst.disabled = true;
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(executeDemangle, DEBOUNCE_MS);
    }

    function setupEventListeners() {
        if (elInput) {
            elInput.addEventListener("input", scheduleDemangle);
        }
        if (elScheme) {
            elScheme.addEventListener("change", executeDemangle);
        }
        elStyleRadios.forEach((r) => r.addEventListener("change", executeDemangle));
        elFormatRadios.forEach((r) => r.addEventListener("change", () => {
            if (cachedResult?.key === requestKey()) renderOutput(cachedResult.data);
        }));

        if (btnClear) {
            btnClear.addEventListener("click", () => {
                if (elInput) {
                    elInput.value = "";
                    clearPresetSelection();
                    executeDemangle();
                    elInput.focus();
                }
            });
        }

        // Preset buttons
        document.querySelectorAll(".preset-btn").forEach((btn) => {
            btn.addEventListener("click", () => {
                clearPresetSelection();
                btn.classList.add("active");
                btn.setAttribute("aria-pressed", "true");

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

        const tabs = [elTabTree, elTabJson];
        const panels = [elPanelTree, elPanelJson];
        function selectTab(index, focus = false) {
            tabs.forEach((tab, i) => {
                tab.classList.toggle("active", i === index);
                tab.setAttribute("aria-selected", String(i === index));
                tab.tabIndex = i === index ? 0 : -1;
                panels[i].hidden = i !== index;
            });
            if (focus) tabs[index].focus();
        }
        tabs.forEach((tab, index) => {
            tab.addEventListener("click", () => selectTab(index));
            tab.addEventListener("keydown", event => {
                if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
                event.preventDefault();
                selectTab(event.key === "Home" ? 0 : event.key === "End" ? 1 : 1 - index, true);
            });
        });

        // Tree Expansion / Collapse
        if (btnExpandAll && elTreeContainer) {
            btnExpandAll.addEventListener("click", () => {
                const queue = [elTreeContainer.querySelector("details")];
                for (let index = 0; index < queue.length; index++) {
                    const branch = queue[index];
                    branch.open = true;
                    branch.populate(undefined, true);
                    queue.push(...branch.querySelectorAll(":scope > ul > li > details"));
                }
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

        btnCopyOutput.addEventListener("click", () => copyText(btnCopyOutput, elOutputDisplay.textContent));
        btnCopyAst.addEventListener("click", () => copyText(btnCopyAst, elJsonContent.textContent));
        document.getElementById("btn-share").addEventListener("click", () => {
            const url = new URL(location.href);
            url.hash = new URLSearchParams({ symbol: elInput.value, language: elScheme.value, style: getSelectedRadio(elStyleRadios, "llvm"), view: getSelectedRadio(elFormatRadios, "full") }).toString();
            history.replaceState(null, "", url);
            copyText(document.getElementById("btn-share"), url.href);
        });
        document.getElementById("btn-retry-runtime").addEventListener("click", initPyodideRuntime);
        window.addEventListener("hashchange", () => { restoreSharedState(); executeDemangle(); });
        elInput.addEventListener("input", clearPresetSelection);
    }

    function clearPresetSelection() {
        document.querySelectorAll(".preset-btn").forEach(button => {
            button.classList.remove("active");
            button.setAttribute("aria-pressed", "false");
        });
    }

    async function copyText(button, text) {
        const label = button.querySelector(".copy-label");
        try {
            await navigator.clipboard.writeText(text);
            label.textContent = "Copied!";
        } catch {
            label.textContent = "Copy failed";
            updateStatus("error", "Clipboard unavailable. Select and copy the text, or copy the shared URL from the address bar.");
        }
        setTimeout(() => { label.textContent = button.dataset.label; }, 1800);
    }

    function restoreSharedState() {
        const params = new URLSearchParams(location.hash.slice(1));
        if (!params.has("symbol")) return;
        clearPresetSelection();
        elInput.value = params.get("symbol");
        const language = params.get("language");
        if ([...elScheme.options].some(o => o.value === language)) elScheme.value = language;
        for (const [radios, key] of [[elStyleRadios, "style"], [elFormatRadios, "view"]]) {
            if ([...radios].some(r => r.value === params.get(key))) radios.forEach(r => { r.checked = r.value === params.get(key); });
        }
    }

    function runtimeFailed(message) {
        clearTimeout(runtimeTimer);
        worker?.terminate();
        worker = null;
        isReady = false;
        activeRequest = null;
        updateStatus("error", message);
        elOutputStatus.textContent = "Unavailable";
        elOutputStatus.className = "output-status-pill pill-invalid";
        btnCopyOutput.disabled = true;
        btnCopyAst.disabled = true;
        document.getElementById("btn-retry-runtime").hidden = false;
    }

    function initPyodideRuntime() {
        worker?.terminate();
        clearTimeout(runtimeTimer);
        isReady = false;
        activeRequest = null;
        cachedResult = null;
        document.getElementById("btn-retry-runtime").hidden = true;
        updateStatus("loading", "Loading WebAssembly runtime…");
        try {
            worker = new Worker(new URL("playground-worker.js", assetBase));
            worker.onmessage = ({ data }) => {
                if (data.type === "ready") {
                    clearTimeout(runtimeTimer);
                    isReady = true;
                    document.getElementById("playground-engine-version").textContent = `demangle ${data.version} · Pyodide ${data.runtimeVersion}`;
                    updateStatus("ready", "Ready (WebAssembly)");
                    executeDemangle();
                } else if (data.type === "result") {
                    clearTimeout(runtimeTimer);
                    const request = activeRequest;
                    activeRequest = null;
                    if (request && data.id === latestRequestId) {
                        cachedResult = { key: request.key, data: data.result };
                        renderResults(data.result);
                        updateStatus("ready", "Ready (WebAssembly)");
                    }
                    dispatchPending();
                } else if (data.type === "error") {
                    runtimeFailed(data.message);
                }
            };
            worker.onerror = event => {
                event.preventDefault();
                runtimeFailed("Could not start the runtime. Check your connection and retry.");
            };
            runtimeTimer = setTimeout(() => runtimeFailed("Runtime loading timed out. Check your connection and retry."), 90000);
        } catch (error) {
            runtimeFailed(`Could not start the runtime: ${error.message}`);
        }
    }

    function init() {
        initDomReferences();
        restoreSharedState();
        setupEventListeners();
        renderResults({ empty: true });
        initPyodideRuntime();
    }
    window.addEventListener("pagehide", event => { if (!event.persisted) worker?.terminate(); });
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
