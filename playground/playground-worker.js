/* Python runs in a dedicated module worker; symbols never leave this browser. */
// Served by this site, not a CDN: docs/hooks.py fetches the runtime when the site is built and
// checks every file against a pinned SHA-256, so this page loads nothing from a third party.
import { loadPyodide } from "./assets/pyodide/pyodide.mjs";

let runner, batchRunner;

async function download(name, read) {
    const response = await fetch(new URL(name, import.meta.url));
    if (!response.ok) throw new Error(`${name} download failed: HTTP ${response.status}`);
    return read(response);
}

try {
    const [pyodide, archive, bridge] = await Promise.all([
        loadPyodide({ indexURL: new URL("assets/pyodide/", import.meta.url).href }),
        download("assets/demangle.zip", response => response.arrayBuffer()),
        download("playground_bridge.py", response => response.text()),
    ]);
    pyodide.FS.writeFile("/demangle.zip", new Uint8Array(archive));
    pyodide.runPython('import sys; sys.path.insert(0, "/demangle.zip"); import demangle');
    pyodide.runPython(bridge);
    // Retain one proxy for the lifetime of this worker; terminate() releases the runtime.
    runner = pyodide.globals.get("playground_demangle");
    batchRunner = pyodide.globals.get("playground_batch");
    self.postMessage({ type: "ready", version: pyodide.runPython("demangle.__version__"), runtimeVersion: pyodide.version });
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
