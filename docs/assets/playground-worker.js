/* Python runs in a dedicated worker; symbols never leave this browser. */
"use strict";
const PYODIDE_VERSION = "0.26.4";
let runner;

async function initialize() {
    try {
        const runtimeURL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
        importScripts(`${runtimeURL}pyodide.js`);
        const assetBase = new URL(".", self.location.href);
        const [pyodide, archive, bridge] = await Promise.all([
            loadPyodide({ indexURL: runtimeURL }),
            fetch(new URL("demangle.zip", assetBase)).then(response => {
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
        self.postMessage({ type: "ready", version: pyodide.runPython("demangle.__version__"), runtimeVersion: PYODIDE_VERSION });
        self.onmessage = ({ data }) => {
            if (data.type !== "demangle") return;
            try {
                const result = JSON.parse(runner(data.symbol, data.language, data.style));
                self.postMessage({ type: "result", id: data.id, result });
            } catch (error) {
                self.postMessage({ type: "error", message: `Demangling failed: ${error.message}` });
            }
        };
    } catch (error) {
        self.postMessage({ type: "error", message: `Runtime initialization failed: ${error.message}` });
    }
}
initialize();
