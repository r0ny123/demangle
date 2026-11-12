/* Python runs in a dedicated worker; symbols never leave this browser. */
"use strict";
const PYODIDE_VERSION = "0.26.4";
let runner, batchRunner;

async function initialize() {
    try {
        // Served by this site, not a CDN: docs/hooks.py fetches the runtime when the site is
        // built and checks every file against a pinned SHA-256, so this page loads nothing
        // from a third party.
        const runtimeURL = new URL("assets/pyodide/", self.location.href).href;
        importScripts(`${runtimeURL}pyodide.js`);
        const assetBase = new URL(".", self.location.href);
        const [pyodide, archive, bridge] = await Promise.all([
            loadPyodide({ indexURL: runtimeURL }),
            fetch(new URL("assets/demangle.zip", assetBase)).then(response => {
                if (!response.ok) throw new Error(`Package download failed: HTTP ${response.status}`);
                return response.arrayBuffer();
            }),
            fetch(new URL("playground_bridge.py", assetBase)).then(response => {
                if (!response.ok) throw new Error(`Bridge download failed: HTTP ${response.status}`);
                return response.text();
            }),
        ]);
        pyodide.FS.writeFile("/demangle.zip", new Uint8Array(archive));
        await pyodide.runPythonAsync(`
import sys
sys.path.insert(0, "/demangle.zip")
import demangle
`);
        await pyodide.runPythonAsync(bridge);
        // Retain one proxy for the lifetime of this worker; terminate() releases the runtime.
        runner = pyodide.globals.get("playground_demangle");
        batchRunner = pyodide.globals.get("playground_batch");
        self.postMessage({ type: "ready", version: pyodide.runPython("demangle.__version__"), runtimeVersion: PYODIDE_VERSION });
        self.onmessage = ({ data }) => {
            if (data.type !== "demangle") return;
            try {
                const result = JSON.parse((data.batch ? batchRunner : runner)(data.symbol, data.language, data.style));
                self.postMessage({ type: "result", id: data.id, result });
            } catch (error) {
                self.postMessage({ type: "request-error", id: data.id, message: `Demangling failed: ${error.message}` });
            }
        };
    } catch (error) {
        self.postMessage({ type: "error", message: `Runtime initialization failed: ${error.message}` });
    }
}
initialize();
