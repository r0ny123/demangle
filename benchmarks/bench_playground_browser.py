"""Measure the real browser playground with a cold/warm CDN response proxy.

Build the docs first, then run with the repository's optional browser tools:

    mkdocs build --strict
    DEMANGLE_CHROMIUM_PATH=/usr/bin/chromium \
      .venv/bin/python benchmarks/bench_playground_browser.py

Use `--pyodide-version 0.29.3 --runs 2 --check-presets` for a bounded runtime
comparison. The requested version is injected into a temporary served worker;
the checked-in runtime version and assets are left untouched.

The first run fetches CDN assets through a local Python proxy. Later runs use the
same response bodies from memory, separating CDN transfer from worker startup.
No symbols are sent to the proxy. Results are JSON so they can be retained or
compared across machines; startup numbers include the local browser and server.
CDP heap and DOM counters describe the page process. The worker's WebAssembly
linear-memory capacity is read separately; it is not a process-RSS measurement.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import threading
import time
from collections import Counter
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright  # type: ignore[import-not-found,unresolved-import]

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
PLAYGROUND = "/playground/"
VERSION = "0.26.4"

WORKER_INSTRUMENTATION = """
(() => {
  const NativeWorker = window.Worker;
  window.__playgroundBench = {readyAt: null, requests: []};
  window.Worker = class extends NativeWorker {
    constructor(...args) {
      super(...args);
      this.addEventListener('message', event => {
        const data = event.data;
        if (data.type === 'ready') window.__playgroundBench.readyAt = performance.now();
        if (data.type === 'result') {
          const row = window.__playgroundBench.requests.find(item => item.id === data.id);
          if (!row) return;
          row.responseAt = performance.now();
          requestAnimationFrame(() => { row.nextFrameAt = performance.now(); });
        }
      });
      const post = this.postMessage.bind(this);
      this.postMessage = (data, ...rest) => {
        if (data.type === 'demangle') {
          window.__playgroundBench.requests.push({
            id: data.id,
            batch: Boolean(data.batch),
            inputBytes: new TextEncoder().encode(data.symbol).length,
            sentAt: performance.now(),
            responseAt: null,
            nextFrameAt: null
          });
        }
        return post(data, ...rest);
      };
    }
  };
})();
"""


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * percentile))], 2)


def _measure_action(page, symbol: str, *, batch: bool) -> dict[str, object]:
    before = page.evaluate("window.__playgroundBench.requests.length")
    if batch:
        if not page.locator("#playground-batch-mode").is_checked():
            page.locator("#playground-batch-mode").check()
            page.wait_for_function(
                "document.querySelector('#output-status-indicator').textContent.includes('line')",
                timeout=30_000,
            )
    elif page.locator("#playground-batch-mode").is_checked():
        page.locator("#playground-batch-mode").uncheck()
        page.wait_for_function(
            "document.querySelector('#playground-status-badge').classList.contains('status-ready')",
            timeout=30_000,
        )

    before = page.evaluate("window.__playgroundBench.requests.length")
    page.locator("#playground-input-symbol").fill(symbol)
    page.wait_for_function(
        "count => window.__playgroundBench.requests.length > count",
        arg=before,
        timeout=30_000,
    )
    request = page.evaluate("window.__playgroundBench.requests.at(-1)")
    page.wait_for_function(
        "id => { const item = window.__playgroundBench.requests.find(row => row.id === id); "
        "return item && item.nextFrameAt !== null; }",
        arg=request["id"],
        timeout=30_000,
    )
    finished = page.evaluate("id => window.__playgroundBench.requests.find(row => row.id === id)", arg=request["id"])
    return {
        "input_bytes": finished["inputBytes"],
        "worker_compute_and_transfer_ms": round(finished["responseAt"] - finished["sentAt"], 2),
        "render_to_next_frame_ms": round(finished["nextFrameAt"] - finished["responseAt"], 2),
        "dispatch_to_next_frame_ms": round(finished["nextFrameAt"] - finished["sentAt"], 2),
    }


def _measure_batch_format_switch(page) -> dict[str, object]:
    result = page.evaluate(
        """() => {
          const beforeCount = window.__playgroundBench.requests.length;
          const samples = [];
          for (let index = 0; index < 20; index += 1) {
            const value = index % 2 === 0 ? 'no-params' : 'both';
            const started = performance.now();
            const radio = document.querySelector(`input[name="pg-format"][value="${value}"]`);
            radio.checked = true;
            radio.dispatchEvent(new Event('change', {bubbles: true}));
            samples.push(performance.now() - started);
          }
          return {samples_ms: samples,
                  worker_requests_added: window.__playgroundBench.requests.length - beforeCount,
                  rows: document.querySelectorAll('.batch-table tbody tr').length};
        }"""
    )
    samples = result["samples_ms"]
    return {
        "switches": len(samples),
        "elapsed_ms": [round(value, 3) for value in samples],
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": _percentile(samples, 0.95),
        "worker_requests_added": result["worker_requests_added"],
        "rendered_rows": result["rows"],
    }


def _browser_memory(context, page) -> dict[str, int]:
    session = context.new_cdp_session(page)
    session.send("Performance.enable")
    metrics = {item["name"]: item["value"] for item in session.send("Performance.getMetrics")["metrics"]}
    workers = page.workers
    if len(workers) != 1:
        raise RuntimeError(f"Expected one playground worker, found {len(workers)}")
    wasm_capacity = workers[0].evaluate("self.__demangleBenchmarkRuntime._module.HEAP8.buffer.byteLength")
    return {
        "table_nodes": page.locator(".batch-table").evaluate_all(
            "tables => tables.reduce((count, table) => count + table.querySelectorAll('*').length, 0)"
        ),
        "page_dom_nodes": int(metrics.get("Nodes", 0)),
        "page_js_heap_used_bytes": int(metrics.get("JSHeapUsedSize", 0)),
        "page_js_heap_total_bytes": int(metrics.get("JSHeapTotalSize", 0)),
        "page_worker_scopes": int(metrics.get("WorkerGlobalScopes", 0)),
        "worker_wasm_heap_capacity_bytes": wasm_capacity,
    }


def _check_presets(page) -> dict[str, object]:
    presets = page.locator(".preset-btn")
    total = presets.count()
    for index in range(total):
        preset = presets.nth(index)
        expected_scheme = preset.get_attribute("data-scheme")
        before = page.evaluate("window.__playgroundBench.requests.length")
        preset.click()
        page.wait_for_function(
            "count => window.__playgroundBench.requests.length > count",
            arg=before,
            timeout=30_000,
        )
        request = page.evaluate("window.__playgroundBench.requests.at(-1)")
        page.wait_for_function(
            "id => { const item = window.__playgroundBench.requests.find(row => row.id === id); "
            "return item && item.nextFrameAt !== null; }",
            arg=request["id"],
            timeout=30_000,
        )
        state = page.locator("#output-status-indicator").inner_text()
        detected = page.locator("#detected-scheme-tag").inner_text()
        if state not in {"Valid", "Unchanged"} or detected != expected_scheme:
            raise RuntimeError(f"Preset {index + 1}/{total} ({expected_scheme}) returned {state!r} and {detected!r}")
    return {"passed": total, "total": total}


def _measure_run(
    browser,
    base_url: str,
    proxy_cache: dict[str, tuple],
    proxy_events: list[dict],
    run_number: int,
    pyodide_version: str,
    check_presets: bool,
):
    context = browser.new_context(viewport={"width": 1280, "height": 1000})
    context.add_init_script(WORKER_INSTRUMENTATION)
    worker_source = (SITE / "playground" / "playground-worker.js").read_text(encoding="utf-8")
    worker_marker = f'const PYODIDE_VERSION = "{VERSION}";'
    if worker_marker not in worker_source:
        raise RuntimeError(f"Worker version marker not found: {worker_marker}")
    worker_source = worker_source.replace(worker_marker, f'const PYODIDE_VERSION = "{pyodide_version}";')
    runtime_marker = '        pyodide.FS.writeFile("/demangle.zip", new Uint8Array(archive));'
    if runtime_marker not in worker_source:
        raise RuntimeError("Worker runtime marker not found for benchmark-only memory instrumentation")
    worker_source = worker_source.replace(
        runtime_marker,
        "        self.__demangleBenchmarkRuntime = pyodide;\n" + runtime_marker,
    )

    def fetch_cdn(route):
        url = route.request.url
        started = time.perf_counter()
        cached = url in proxy_cache
        if cached:
            status, headers, body = proxy_cache[url]
        else:
            with urlopen(url, timeout=60) as response:
                status = response.status
                headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() not in {"content-encoding", "content-length", "transfer-encoding", "connection"}
                }
                body = response.read()
            proxy_cache[url] = (status, headers, body)
        elapsed = (time.perf_counter() - started) * 1000
        proxy_events.append(
            {
                "run": run_number,
                "url": url,
                "cache": "warm" if cached else "cold",
                "ms": round(elapsed, 2),
                "bytes": len(body),
            }
        )
        route.fulfill(status=status, headers=headers, body=body)

    context.route("https://cdn.jsdelivr.net/**", fetch_cdn)
    context.route(
        "**/playground-worker.js",
        lambda route: route.fulfill(body=worker_source, content_type="application/javascript"),
    )
    page = context.new_page()
    page.goto(base_url + PLAYGROUND, wait_until="domcontentloaded")
    page.wait_for_function(
        "document.querySelector('#playground-engine-version').textContent.includes('Pyodide "
        + pyodide_version
        + "') || "
        "document.querySelector('#playground-status-badge').classList.contains('status-error')",
        timeout=120_000,
    )
    version = page.locator("#playground-engine-version").inner_text()
    if "Pyodide " + pyodide_version not in version:
        message = page.locator("#playground-status-text").inner_text()
        context.close()
        raise RuntimeError(f"Playground runtime failed: {message}")

    presets = _check_presets(page) if check_presets else None

    startup = page.evaluate(
        """() => {
          const nav = performance.getEntriesByType('navigation')[0];
          return {
            dom_content_loaded_ms: nav.domContentLoadedEventEnd,
            worker_ready_ms: window.__playgroundBench.readyAt,
            worker_ready_after_dom_ms: window.__playgroundBench.readyAt - nav.domContentLoadedEventEnd
          };
        }"""
    )

    single = _measure_action(page, "_ZNSt6vectorIiSaIiEE9push_backERKi", batch=False)
    format_cache = page.evaluate(
        """() => {
          const beforeCount = window.__playgroundBench.requests.length;
          const started = performance.now();
          const radio = document.querySelector('input[name="pg-format"][value="both"]');
          radio.checked = true;
          radio.dispatchEvent(new Event('change', {bubbles: true}));
          return {elapsed_ms: performance.now() - started,
                  worker_requests_added: window.__playgroundBench.requests.length - beforeCount};
        }"""
    )

    duplicate = "\n".join(["_ZN3foo3barEi"] * 500)
    duplicate_batch = _measure_action(page, duplicate, batch=True)
    duplicate_batch["rendered_rows"] = page.locator(".batch-table tbody tr").count()
    duplicate_batch["cached_format_switch"] = _measure_batch_format_switch(page)
    duplicate_batch["page_memory"] = _browser_memory(context, page)

    unique = "\n".join(f"plain_symbol_{index:04}" for index in range(500))
    unique_batch = _measure_action(page, unique, batch=True)
    unique_batch["rendered_rows"] = page.locator(".batch-table tbody tr").count()
    unique_batch["page_memory"] = _browser_memory(context, page)

    requests_before_cleanup = page.evaluate("window.__playgroundBench.requests.length")
    page.locator("#playground-batch-mode").uncheck()
    page.wait_for_function(
        "document.querySelector('.output-container').getAttribute('aria-busy') === 'false' && "
        "document.querySelector('#batch-results').hidden",
        timeout=30_000,
    )
    cleanup_memory = _browser_memory(context, page)
    batch_cleanup = {
        "table_nodes_before": unique_batch["page_memory"]["table_nodes"],
        "table_nodes_after": cleanup_memory["table_nodes"],
        "page_dom_nodes_before": unique_batch["page_memory"]["page_dom_nodes"],
        "page_dom_nodes_after": cleanup_memory["page_dom_nodes"],
        "page_js_heap_used_bytes_before": unique_batch["page_memory"]["page_js_heap_used_bytes"],
        "page_js_heap_used_bytes_after": cleanup_memory["page_js_heap_used_bytes"],
        "worker_requests_added": page.evaluate("window.__playgroundBench.requests.length") - requests_before_cleanup,
    }

    context.close()
    return {
        "proxy_mode": "cold CDN fetch" if run_number == 0 else "warm in-memory CDN replay",
        "pyodide_version": pyodide_version,
        "runtime": version,
        "presets": presets,
        "startup": startup,
        "single_symbol": single,
        "cached_format_change": {
            "elapsed_ms": round(format_cache["elapsed_ms"], 3),
            "worker_requests_added": format_cache["worker_requests_added"],
        },
        "batch_500_duplicate_symbols": duplicate_batch,
        "batch_500_unique_plain_names": unique_batch,
        "single_mode_cleanup_after_batch": batch_cleanup,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs", type=int, default=3, help="Browser launches within one Chromium process; first run fetches CDN assets"
    )
    parser.add_argument("--pyodide-version", default=VERSION, help="Temporarily serve this worker runtime version")
    parser.add_argument(
        "--source-label", default="working tree", help="Label the source checkout represented by this build"
    )
    parser.add_argument("--check-presets", action="store_true", help="Demangle every built-in preset in every run")
    parser.add_argument("--chromium-path", default=os.environ.get("DEMANGLE_CHROMIUM_PATH"))
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if not (SITE / "playground" / "index.html").is_file():
        parser.error("built site missing; run `mkdocs build --strict` first")

    local_requests: Counter[str] = Counter()
    local_bytes: Counter[str] = Counter()

    class QuietHandler(SimpleHTTPRequestHandler):
        def __init__(self, *handler_args, **kwargs):
            super().__init__(*handler_args, directory=str(SITE), **kwargs)

        def do_GET(self):
            relative = self.translate_path(self.path)
            try:
                size = Path(relative).stat().st_size
            except OSError:
                size = 0
            local_requests[self.path] += 1
            local_bytes[self.path] += size
            super().do_GET()

        def log_message(self, format, *handler_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    proxy_cache: dict[str, tuple] = {}
    proxy_events: list[dict] = []
    runs = []
    revision = None
    worktree_changed = None
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        worktree_changed = bool(
            subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        )
    except (OSError, subprocess.CalledProcessError):
        pass
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=args.chromium_path, headless=True, args=["--no-sandbox"]
            )
            browser_version = browser.version
            for index in range(args.runs):
                runs.append(
                    _measure_run(
                        browser,
                        base_url,
                        proxy_cache,
                        proxy_events,
                        index,
                        args.pyodide_version,
                        args.check_presets,
                    )
                )
            browser.close()
    finally:
        server.shutdown()
        server.server_close()

    cdn_by_run = []
    for index in range(args.runs):
        events = [item for item in proxy_events if item["run"] == index]
        cdn_by_run.append(
            {
                "run": index,
                "requests": len(events),
                "bytes": sum(item["bytes"] for item in events),
                "fetch_ms_sum": round(sum(item["ms"] for item in events), 2),
                "assets": [Path(item["url"].split("?", 1)[0]).name for item in events],
            }
        )

    print(
        json.dumps(
            {
                "baseline_revision": "03bd234",
                "measured_revision": revision,
                "worktree_changed": worktree_changed,
                "source_label": args.source_label,
                "browser": f"Chromium {browser_version}",
                "requested_pyodide_version": args.pyodide_version,
                "runs": runs,
                "cdn_proxy": cdn_by_run,
                "local_worker_assets": {
                    key: {"requests": local_requests[key], "bytes": local_bytes[key]}
                    for key in sorted(local_requests)
                    if "playground-worker.js" in key or "playground_bridge.py" in key or "demangle.zip" in key
                },
                "proxy_request_fetch_ms_median": _percentile([item["ms"] for item in proxy_events], 0.5),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
