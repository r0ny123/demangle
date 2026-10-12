"""Opt-in browser regressions for the built playground site.

Run `mkdocs build --strict` first, then set DEMANGLE_BROWSER_TESTS=1. The normal suite
stays independent of Chromium and the Pyodide CDN; the real-runtime smoke is separately
opt-in through DEMANGLE_PYODIDE_SMOKE=1.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

if os.environ.get("DEMANGLE_BROWSER_TESTS") != "1":
    pytest.skip("set DEMANGLE_BROWSER_TESTS=1 to run browser tests", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "site"
PLAYGROUND = "/playground/"
pytestmark = pytest.mark.browser

FAKE_WORKER = r"""
"use strict";
const channel = new BroadcastChannel("demangle-playground-browser-test");
function astFor(symbol) {
    if (symbol === "graph") return {
        kind: "root", id: 1,
        alias: { $ref: 2 },
        children: [{ kind: "shared", id: 2, text: "shared target" }]
    };
    if (symbol === "large-graph") return {
        kind: "root", items: Array.from({length: 1800}, (_, i) => ({kind: "entry", text: `entry-${i}`}))
    };
    return { kind: "function", name: { kind: "name", text: symbol }, parameters: [] };
}
function resultFor(request) {
    if (request.batch) return {batch: true, rows: request.symbol.split("\n").map(line => line.trim() ? resultFor({...request, batch: false, symbol: line.trim()}) : {symbol: "", state: "empty", success: true, empty: true, output_variants: {full: "", both: "", "base-name": "", "no-params": ""}})};
    const symbol = request.symbol;
    const invalid = symbol === "invalid";
    const demangled = invalid ? symbol : `demangled(${symbol})`;
    return {
        success: !invalid, empty: false, state: invalid ? "invalid" : "valid", symbol,
        error: invalid ? "Injected parse failure" : null,
        language: request.language === "auto" ? "itanium" : request.language,
        detected_language: request.language === "auto" ? "itanium" : request.language,
        demangled,
        output_variants: {
            full: demangled,
            both: `${symbol} ==> ${demangled}`,
            "base-name": invalid ? symbol : `base(${symbol})`,
            "no-params": invalid ? symbol : `no-params(${symbol})`
        },
        qualified_name: `qualified(${symbol})`, base_name: `base(${symbol})`,
        namespace: "demo", parameters: [], return_type: "void",
        calling_convention: "cdecl", qualifiers: [], special: null,
        ast: invalid ? null : astFor(symbol)
    };
}
setTimeout(() => self.postMessage({type: "ready", version: "test", runtimeVersion: "test"}), 400);
self.onmessage = ({data}) => {
    if (data.type !== "demangle") return;
    channel.postMessage({type: "request", symbol: data.symbol, language: data.language, style: data.style, id: data.id});
    const delay = data.symbol === "slow" ? 240 : 15;
    setTimeout(() => self.postMessage({type: "result", id: data.id, result: resultFor(data)}), delay);
};
"""


@pytest.fixture(scope="session")
def site_server():
    if not (SITE / "playground" / "index.html").is_file():
        pytest.fail("built site missing; run `mkdocs build --strict` first")

    class QuietHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(SITE), **kwargs)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=3)
    server.server_close()


@pytest.fixture(scope="session")
def browser():
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright
    with sync_playwright() as playwright:
        executable = os.environ.get("DEMANGLE_CHROMIUM_PATH")
        instance = playwright.chromium.launch(
            executable_path=executable,
            headless=True,
            args=["--no-sandbox"],
        )
        yield instance
        instance.close()


@pytest.fixture
def context(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 1100})
    context.add_init_script(
        """window.__demangleWorkerEvents = [];
        const channel = new BroadcastChannel("demangle-playground-browser-test");
        channel.onmessage = event => window.__demangleWorkerEvents.push(event.data);
        window.__clipboardWrites = [];
        Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {
          writeText: async text => window.__clipboardWrites.push(text)
        }});"""
    )
    if os.environ.get("DEMANGLE_CDN_PROXY_FETCH") == "1":

        def fetch_cdn(route):
            try:
                with urlopen(route.request.url) as response:
                    headers = {
                        key: value
                        for key, value in response.headers.items()
                        if key.lower() not in {"content-encoding", "content-length", "transfer-encoding", "connection"}
                    }
                    route.fulfill(status=response.status, headers=headers, body=response.read())
            except Exception:
                route.abort()

        context.route("https://cdn.jsdelivr.net/**", fetch_cdn)
    yield context
    context.close()


def open_playground(context, base_url: str, *, worker_script: str = FAKE_WORKER):
    page = context.new_page()
    page.route(
        "**/playground-worker.js",
        lambda route: route.fulfill(body=worker_script, content_type="application/javascript"),
    )
    page.goto(base_url + PLAYGROUND, wait_until="domcontentloaded")
    return page


def requests(page):
    return page.evaluate("window.__demangleWorkerEvents.filter(event => event.type === 'request')")


def wait_ready(page):
    page.wait_for_function("document.querySelector('#playground-status-badge').classList.contains('status-ready')")


def test_startup_preserves_input_until_worker_is_ready_and_clear_resets_results(context, site_server):
    page = open_playground(context, site_server)
    field = page.locator("#playground-input-symbol")
    field.fill("startup-symbol")
    page.locator("#playground-select-scheme").select_option("swift")
    page.locator('label:has(input[name="pg-style"][value="gnu"])').click()
    wait_ready(page)
    page.get_by_text("demangled(startup-symbol)", exact=True).wait_for()
    sent = requests(page)
    assert [item["symbol"] for item in sent] == ["startup-symbol"]
    assert sent[0]["language"] == "swift"
    assert sent[0]["style"] == "gnu"

    page.locator("#btn-clear-input").click()
    assert field.input_value() == ""
    page.locator(".output-placeholder").wait_for()
    assert len(requests(page)) == 1


def test_shared_link_round_trips_symbol_scheme_style_and_view(context, site_server):
    page = open_playground(context, site_server)
    wait_ready(page)
    symbol = '_Z3a&b?"value"'
    page.locator("#playground-input-symbol").fill(symbol)
    page.locator("#playground-select-scheme").select_option("itanium")
    page.locator('label:has(input[name="pg-style"][value="gnu"])').click()
    page.locator('label:has(input[name="pg-format"][value="both"])').click()
    page.get_by_text(f"{symbol} ==> demangled({symbol})", exact=True).wait_for()
    page.locator("#btn-share").click()
    page.wait_for_function("window.__clipboardWrites.length === 1")
    shared_url = page.evaluate("window.__clipboardWrites[0]")
    assert page.evaluate("new URL(location.href).hash")

    restored = context.new_page()
    restored.route(
        "**/playground-worker.js",
        lambda route: route.fulfill(body=FAKE_WORKER, content_type="application/javascript"),
    )
    restored.goto(shared_url, wait_until="domcontentloaded")
    wait_ready(restored)
    assert restored.locator("#playground-input-symbol").input_value() == symbol
    assert restored.locator("#playground-select-scheme").input_value() == "itanium"
    assert restored.locator('input[name="pg-style"][value="gnu"]').is_checked()
    assert restored.locator('input[name="pg-format"][value="both"]').is_checked()
    restored.get_by_text(f"{symbol} ==> demangled({symbol})", exact=True).wait_for()


def test_latest_request_coalesces_queued_inputs_and_ignores_stale_result(context, site_server):
    page = open_playground(context, site_server)
    wait_ready(page)
    field = page.locator("#playground-input-symbol")
    field.fill("slow")
    page.wait_for_function("window.__demangleWorkerEvents.some(event => event.symbol === 'slow')")
    field.fill("middle")
    field.fill("latest")
    page.wait_for_function("window.__demangleWorkerEvents.filter(event => event.type === 'request').length === 2")
    page.get_by_text("demangled(latest)", exact=True).wait_for()
    assert [item["symbol"] for item in requests(page)] == ["slow", "latest"]
    assert page.locator("#output-display").inner_text() == "demangled(latest)"


def test_format_views_update_cached_output_without_another_worker_request(context, site_server):
    page = open_playground(context, site_server)
    wait_ready(page)
    page.locator("#playground-input-symbol").fill("format-symbol")
    page.get_by_text("demangled(format-symbol)", exact=True).wait_for()
    assert len(requests(page)) == 1

    for view, expected in (
        ("both", "format-symbol ==> demangled(format-symbol)"),
        ("base-name", "base(format-symbol)"),
        ("no-params", "no-params(format-symbol)"),
        ("full", "demangled(format-symbol)"),
    ):
        page.locator(f'label:has(input[name="pg-format"][value="{view}"])').click()
        assert page.locator("#output-display").inner_text() == expected
    assert len(requests(page)) == 1


def test_batch_results_preserve_lines_copy_download_and_cached_views(context, site_server, tmp_path):
    page = open_playground(context, site_server)
    wait_ready(page)
    page.locator("#playground-batch-mode").check()
    page.locator("#playground-input-symbol").fill("first\n\ninvalid\nfirst\n")
    page.wait_for_function(
        "document.querySelector('#output-status-indicator').textContent === '5 lines: 2 parsed, 1 failed, 2 blank'"
    )
    rows = page.locator(".batch-table tbody tr")
    assert rows.count() == 5
    assert [rows.nth(i).locator("th").inner_text() for i in range(5)] == ["1", "2", "3", "4", "5"]
    assert "Invalid" in rows.nth(2).inner_text()
    assert "Injected parse failure" in rows.nth(2).inner_text()
    assert page.locator("#playground-signature-panel").is_hidden()
    assert page.locator("#playground-ast-section").is_hidden()
    sent = len(requests(page))
    page.locator('label:has(input[name="pg-format"][value="base-name"])').click()
    assert len(requests(page)) == sent
    page.locator("#btn-copy-output").click()
    page.wait_for_function("window.__clipboardWrites.length === 1")
    expected = "base(first)\n\ninvalid\nbase(first)\n"
    assert page.evaluate("window.__clipboardWrites[0]") == expected
    with page.expect_download() as download_info:
        page.locator("#btn-download-output").click()
    download = download_info.value
    assert download.suggested_filename == "demangled.txt"
    target = tmp_path / download.suggested_filename
    download.save_as(target)
    assert target.read_text() == expected


def test_batch_share_round_trip_and_invalid_options_restore_defaults(context, site_server):
    page = open_playground(context, site_server)
    wait_ready(page)
    symbol = "first\n\n<&é>\n"
    page.locator("#playground-batch-mode").check()
    page.locator("#playground-input-symbol").fill(symbol)
    page.locator("#playground-select-scheme").select_option("rust")
    page.locator('label:has(input[name="pg-style"][value="gnu"])').click()
    page.locator('label:has(input[name="pg-format"][value="both"])').click()
    page.locator("#btn-share").click()
    page.wait_for_function("window.__clipboardWrites.length === 1")
    shared = page.evaluate("window.__clipboardWrites[0]")
    page.goto(shared, wait_until="domcontentloaded")
    wait_ready(page)
    assert page.locator("#playground-batch-mode").is_checked()
    assert page.locator("#playground-input-symbol").input_value() == symbol
    assert page.locator("#playground-select-scheme").input_value() == "rust"
    assert page.locator('input[name="pg-style"][value="gnu"]').is_checked()
    assert page.locator('input[name="pg-format"][value="both"]').is_checked()
    page.evaluate("location.hash = 'symbol=single&language=unknown&style=wrong&view=wrong&mode=wrong'")
    page.get_by_text("demangled(single)", exact=True).wait_for()
    assert not page.locator("#playground-batch-mode").is_checked()
    assert page.locator("#playground-select-scheme").input_value() == "auto"
    assert page.locator('input[name="pg-style"][value="llvm"]').is_checked()
    assert page.locator('input[name="pg-format"][value="full"]').is_checked()


def test_batch_coalescing_clear_and_limits_disable_stale_exports(context, site_server):
    page = open_playground(context, site_server)
    wait_ready(page)
    page.locator("#playground-batch-mode").check()
    field = page.locator("#playground-input-symbol")
    field.fill("slow")
    page.wait_for_function("window.__demangleWorkerEvents.some(event => event.symbol === 'slow')")
    field.fill("latest\ninvalid")
    page.wait_for_function(
        "document.querySelector('#output-status-indicator').textContent === '2 lines: 1 parsed, 1 failed, 0 blank'"
    )
    assert "demangled(latest)" in page.locator("#batch-results").inner_text()
    count = len(requests(page))
    field.fill("\n" * 500)
    page.get_by_text("Limit exceeded", exact=True).wait_for()
    assert len(requests(page)) == count
    assert page.locator("#btn-copy-output").is_disabled()
    assert "500 lines and 256 KiB" in page.locator("#output-error-message").inner_text()
    page.locator("#btn-clear-input").click()
    assert field.input_value() == ""
    assert not page.locator("#playground-batch-mode").is_checked()
    assert page.locator("#batch-results").is_hidden()


def test_share_length_and_clipboard_failures_are_announced_without_breaking_runtime(context, site_server):
    page = open_playground(context, site_server)
    wait_ready(page)
    field = page.locator("#playground-input-symbol")
    field.fill("x" * 8000)
    page.locator("#btn-share").click()
    assert "too long" in page.locator("#playground-feedback").inner_text()
    assert not page.evaluate("location.hash")
    assert page.evaluate("window.__clipboardWrites.length") == 0
    field.fill("usable")
    page.get_by_text("demangled(usable)", exact=True).wait_for()
    page.evaluate("() => { navigator.clipboard.writeText = async () => {throw new Error('denied');}; }")
    page.locator("#btn-share").click()
    page.get_by_text("Copy failed", exact=True).wait_for()
    assert "Clipboard unavailable" in page.locator("#playground-feedback").inner_text()
    assert page.locator("#playground-feedback").get_attribute("role") == "status"
    assert "status-ready" in page.locator("#playground-status-badge").get_attribute("class")


def test_native_radio_and_batch_keyboard_controls_have_focus_and_result_announcements(context, site_server):
    page = open_playground(context, site_server)
    wait_ready(page)
    page.locator('input[name="pg-style"][value="llvm"]').focus()
    page.keyboard.press("ArrowRight")
    assert page.locator('input[name="pg-style"][value="gnu"]').is_checked()
    page.locator("#playground-batch-mode").focus()
    page.keyboard.press("Space")
    assert page.locator("#playground-batch-mode").is_checked()
    assert page.locator("#playground-batch-mode").evaluate("node => getComputedStyle(node).outlineStyle") != "none"
    page.locator("#playground-input-symbol").fill("keyboard")
    page.wait_for_function(
        "document.querySelector('#output-status-indicator').textContent === '1 line: 1 parsed, 0 failed, 0 blank'"
    )
    assert page.locator("#output-status-indicator").get_attribute("role") == "status"
    assert page.locator(".output-container").get_attribute("aria-busy") == "false"


def test_every_preset_sends_its_symbol_scheme_and_style_to_the_worker(context, site_server):
    page = open_playground(context, site_server)
    wait_ready(page)
    presets = page.locator(".preset-btn")
    total = presets.count()
    for index in range(total):
        preset = presets.nth(index)
        expected_symbol = preset.get_attribute("data-symbol")
        expected_scheme = preset.get_attribute("data-scheme")
        preset.click()
        page.wait_for_function(
            "count => window.__demangleWorkerEvents.filter(event => event.type === 'request').length === count",
            arg=index + 1,
        )
        page.wait_for_function(
            "symbol => document.querySelector('#output-display').textContent === `demangled(${symbol})`",
            arg=expected_symbol,
        )
        sent = requests(page)[-1]
        assert sent["symbol"] == expected_symbol
        assert sent["language"] == expected_scheme
        assert sent["style"] == preset.get_attribute("data-style")
    assert total >= 8


def test_ast_tabs_support_arrow_home_end_keyboard_navigation(context, site_server):
    page = open_playground(context, site_server)
    wait_ready(page)
    tree = page.locator("#tab-btn-tree")
    raw = page.locator("#tab-btn-json")
    tree.focus()
    page.keyboard.press("ArrowRight")
    assert raw.get_attribute("aria-selected") == "true"
    assert page.locator("#ast-json-panel").is_visible()
    assert page.evaluate("document.activeElement.id") == "tab-btn-json"
    page.keyboard.press("Home")
    assert tree.get_attribute("aria-selected") == "true"
    page.keyboard.press("End")
    assert raw.get_attribute("aria-selected") == "true"


def test_ast_reference_navigation_reopens_collapsed_path(context, site_server):
    page = open_playground(context, site_server)
    wait_ready(page)
    page.locator("#playground-input-symbol").fill("graph")
    page.locator(".ast-ref-badge").wait_for()
    page.locator("#btn-ast-collapse").click()
    assert page.locator("#ast-tree-container > details").evaluate("node => node.open")
    page.locator(".ast-ref-badge").click()
    target = page.locator("#ast-node-id-2")
    assert target.evaluate("node => node.open")
    assert page.evaluate("document.activeElement.closest('details')?.id") == "ast-node-id-2"


def test_large_ast_paginates_and_keeps_full_graph_in_raw_json(context, site_server):
    page = open_playground(context, site_server)
    wait_ready(page)
    page.locator("#playground-input-symbol").fill("large-graph")
    page.locator(".ast-summary").first.wait_for()
    array_branch = page.locator("#ast-tree-container details").nth(1)
    array_summary = array_branch.locator(":scope > summary")
    array_summary.focus()
    page.keyboard.press("Enter")
    page.wait_for_function("node => node.open", arg=array_branch.element_handle())
    load_more = page.locator(".ast-load-more")
    load_more.wait_for()
    clicks = 0
    while load_more.is_visible() and not load_more.is_disabled() and clicks < 40:
        load_more.click()
        clicks += 1
    assert clicks > 1
    assert load_more.is_disabled()
    assert page.locator("#ast-render-notice").is_visible()
    page.locator("#tab-btn-json").click()
    assert '"text": "entry-1799"' in page.locator("#ast-json-content").inner_text()


@pytest.mark.parametrize("storage", ["blocked", "invalid"])
def test_theme_storage_cannot_prevent_runtime_startup(context, site_server, storage):
    if storage == "blocked":
        context.add_init_script("""Object.defineProperty(window, 'localStorage', {get() {
          throw new DOMException('Storage blocked', 'SecurityError');
        }});""")
    else:
        context.add_init_script("localStorage.setItem('demangle-theme', 'invalid');")
    page = open_playground(context, site_server)
    page.emulate_media(color_scheme="dark")
    wait_ready(page)
    button = page.locator("#btn-theme-toggle")
    assert button.inner_text() == "☀️ Light"
    assert page.locator("html").get_attribute("data-theme") is None
    button.click()
    assert page.locator("html").get_attribute("data-theme") == "light"
    assert button.inner_text() == "🌙 Dark"
    page.locator("#playground-input-symbol").fill("usable")
    page.wait_for_function("document.querySelector('#output-status-indicator').textContent === 'Valid'")


def test_theme_follows_system_until_overridden_and_persists(context, site_server):
    page = open_playground(context, site_server)
    page.emulate_media(color_scheme="light")
    wait_ready(page)
    button = page.locator("#btn-theme-toggle")
    assert button.inner_text() == "🌙 Dark"
    page.emulate_media(color_scheme="dark")
    page.wait_for_function("document.querySelector('#btn-theme-toggle').textContent === '☀️ Light'")
    button.click()
    assert page.evaluate("localStorage.getItem('demangle-theme')") == "light"
    page.reload(wait_until="domcontentloaded")
    wait_ready(page)
    assert page.locator("html").get_attribute("data-theme") == "light"
    assert button.inner_text() == "🌙 Dark"


@pytest.mark.parametrize("modifier", ["Control", "Meta"])
def test_keyboard_shortcuts_focus_demangle_and_clear(context, site_server, modifier):
    page = open_playground(context, site_server)
    wait_ready(page)
    symbol = page.locator("#playground-input-symbol")
    symbol.fill("shortcut")
    page.locator("#btn-theme-toggle").focus()
    page.keyboard.press("Escape")
    assert symbol.input_value() == "shortcut"
    page.keyboard.press(f"{modifier}+k")
    assert symbol.evaluate("node => document.activeElement === node")
    assert symbol.evaluate("node => node.selectionStart === 0 && node.selectionEnd === node.value.length")
    page.keyboard.press(f"{modifier}+Enter")
    page.wait_for_function("document.querySelector('#output-status-indicator').textContent === 'Valid'")
    assert page.locator("#output-display").inner_text() == "demangled(shortcut)"
    page.keyboard.press("Escape")
    assert symbol.input_value() == ""
    assert page.locator("#btn-copy-output").is_disabled()


@pytest.mark.parametrize("stage", ["startup", "parse"])
def test_stalled_worker_times_out_and_retry_recovers(context, site_server, stage):
    page = context.new_page()
    page.clock.install()
    workers = 0

    def worker_route(route):
        nonlocal workers
        workers += 1
        if workers == 1:
            script = (
                "self.onmessage = () => {};"
                if stage == "startup"
                else FAKE_WORKER.replace(
                    'const delay = data.symbol === "slow" ? 240 : 15;',
                    'if (data.symbol === "stalled") return; const delay = 15;',
                )
            )
        else:
            script = FAKE_WORKER
        route.fulfill(body=script, content_type="application/javascript")

    page.route("**/playground-worker.js", worker_route)
    page.goto(site_server + PLAYGROUND, wait_until="domcontentloaded")
    symbol = page.locator("#playground-input-symbol")
    if stage == "parse":
        wait_ready(page)
    symbol.fill("stalled")
    if stage == "parse":
        page.wait_for_function("window.__demangleWorkerEvents.some(event => event.symbol === 'stalled')")
    page.clock.fast_forward(90_001 if stage == "startup" else 15_001)
    page.locator("#btn-retry-runtime").wait_for(state="visible")
    assert page.locator("#btn-copy-output").is_disabled()
    assert page.locator("#btn-copy-ast").is_disabled()
    assert symbol.input_value() == "stalled"
    page.locator("#btn-retry-runtime").click()
    wait_ready(page)
    page.wait_for_function("document.querySelector('#output-status-indicator').textContent === 'Valid'")
    assert workers == 2
    assert page.locator("#output-display").inner_text() == "demangled(stalled)"


def test_runtime_failure_exposes_retry_and_retry_initializes_worker(context, site_server):
    page = context.new_page()
    workers = 0

    def worker_route(route):
        nonlocal workers
        workers += 1
        script = (
            'setTimeout(() => self.postMessage({type:"error",message:"Injected startup failure"}), 50);'
            if workers == 1
            else FAKE_WORKER
        )
        route.fulfill(body=script, content_type="application/javascript")

    page.route("**/playground-worker.js", worker_route)
    page.goto(site_server + PLAYGROUND, wait_until="domcontentloaded")
    page.get_by_role("button", name="Retry runtime").wait_for(state="visible")
    page.locator("#btn-retry-runtime").click()
    wait_ready(page)
    assert workers == 2
    assert "Injected startup failure" not in page.locator("#playground-status-text").inner_text()


def test_request_error_preserves_input_and_runtime_and_next_request_recovers(context, site_server):
    worker_script = FAKE_WORKER.replace(
        'if (data.type !== "demangle") return;',
        'if (data.type !== "demangle") return; if (data.symbol === "request-error") {self.postMessage({type: "request-error", id: data.id, message: "Injected request failure"}); return;}',
    )
    page = open_playground(context, site_server, worker_script=worker_script)
    wait_ready(page)
    page.locator("#playground-batch-mode").check()
    field = page.locator("#playground-input-symbol")
    field.fill("request-error")
    page.get_by_text("Error", exact=True).wait_for()
    assert field.input_value() == "request-error"
    assert "Injected request failure" in page.locator("#output-error-message").inner_text()
    assert page.locator("#output-error-banner").is_visible()
    assert page.locator("#btn-retry-runtime").is_hidden()
    assert page.locator("#btn-copy-output").is_disabled()
    assert "status-ready" in page.locator("#playground-status-badge").get_attribute("class")
    field.fill("recovered")
    page.wait_for_function(
        "document.querySelector('#output-status-indicator').textContent === '1 line: 1 parsed, 0 failed, 0 blank'"
    )
    assert "demangled(recovered)" in page.locator("#batch-results").inner_text()


@pytest.mark.parametrize("width", [320, 375, 768, 1024, 1440])
@pytest.mark.parametrize("font_scale", [1, 2])
def test_layout_fits_viewport_with_enlarged_root_font(context, site_server, width, font_scale):
    page = open_playground(context, site_server)
    page.set_viewport_size({"width": width, "height": 1000})
    page.evaluate(
        "scale => { const root = document.documentElement; root.style.fontSize = `${parseFloat(getComputedStyle(root).fontSize) * scale}px`; }",
        font_scale,
    )
    wait_ready(page)
    page.wait_for_timeout(100)
    metrics = page.evaluate(
        """() => {
          const root = document.querySelector('#playground-app');
          const bounds = root.getBoundingClientRect();
          const siblings = [...root.children].map(element => element.getBoundingClientRect());
          const controls = [...root.querySelectorAll('button, select, input, textarea')]
            .filter(element => element.getClientRects().length)
            .map(element => element.getBoundingClientRect());
          const groupContent = [...root.querySelectorAll('.control-group')].flatMap(group =>
            [...group.querySelectorAll(':scope > .select-wrapper > select, :scope > .segmented-control')]
              .map(element => [group.getBoundingClientRect(), element.getBoundingClientRect()])
          );
          const segmentOverlaps = [...root.querySelectorAll('.segmented-control')].flatMap(control => {
            const boxes = [...control.querySelectorAll('.segmented-option')].map(element => element.getBoundingClientRect());
            const overlaps = [];
            for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
              if (boxes[i].left < boxes[j].right - 0.5 && boxes[j].left < boxes[i].right - 0.5 &&
                  boxes[i].top < boxes[j].bottom - 0.5 && boxes[j].top < boxes[i].bottom - 0.5) overlaps.push([i, j]);
            }
            return overlaps;
          });
              const overflowing = [...document.body.querySelectorAll('*')].filter(element => {
                const box = element.getBoundingClientRect();
                return box.width > 0 && box.height > 0 && box.right > document.documentElement.clientWidth + 1;
              }).slice(0, 20).map(element => ({tag: element.tagName, id: element.id, className: String(element.className).slice(0, 60), left: element.getBoundingClientRect().left, right: element.getBoundingClientRect().right}));
          return {
            documentWidth: document.documentElement.scrollWidth,
            viewportWidth: document.documentElement.clientWidth,
            rootLeft: bounds.left, rootRight: bounds.right,
            siblingBoxes: siblings.map(box => [box.top, box.bottom]),
            controlBoxes: controls.map(box => [box.left, box.right]),
            groupContent: groupContent.map(([group, child]) => [group.left, group.right, group.top, group.bottom, child.left, child.right, child.top, child.bottom]),
                segmentOverlaps, overflowing
          };
        }"""
    )
    assert metrics["documentWidth"] <= metrics["viewportWidth"], json.dumps(metrics["overflowing"])
    assert metrics["rootLeft"] >= 0 and metrics["rootRight"] <= width
    assert all(
        metrics["siblingBoxes"][i][1] <= metrics["siblingBoxes"][i + 1][0] + 1
        for i in range(len(metrics["siblingBoxes"]) - 1)
    )
    assert all(left >= 0 and right <= width for left, right in metrics["controlBoxes"])
    assert all(
        left <= child_left + 1 and child_right <= right + 1 and top <= child_top + 1 and child_bottom <= bottom + 1
        for left, right, top, bottom, child_left, child_right, child_top, child_bottom in metrics["groupContent"]
    )
    assert all(not overlaps for overlaps in metrics["segmentOverlaps"])


@pytest.mark.skipif(os.environ.get("DEMANGLE_PYODIDE_SMOKE") != "1", reason="real CDN runtime is opt-in")
def test_real_pyodide_runtime_version_presets_invalid_symbol_and_default_auto(context, site_server):
    page = context.new_page()
    page.goto(site_server + PLAYGROUND, wait_until="domcontentloaded")
    page.wait_for_function(
        "document.querySelector('#playground-engine-version').textContent.includes('Pyodide 0.26.4') || "
        "document.querySelector('#playground-status-badge').classList.contains('status-error')",
        timeout=120_000,
    )
    assert "Pyodide 0.26.4" in page.locator("#playground-engine-version").inner_text(), page.locator(
        "#playground-status-text"
    ).inner_text()
    assert page.locator("#playground-select-scheme").input_value() == "auto"

    page.locator("#playground-input-symbol").fill("_ZNSt6vectorIiSaIiEE9push_backERKi")
    page.get_by_text("Valid", exact=True).wait_for(timeout=30_000)
    assert page.locator("#detected-scheme-tag").inner_text() == "itanium"

    presets = page.locator(".preset-btn")
    for index in range(presets.count()):
        preset = presets.nth(index)
        expected_scheme = preset.get_attribute("data-scheme")
        preset.click()
        page.wait_for_function(
            "['Valid', 'Unchanged'].includes(document.querySelector('#output-status-indicator').textContent)",
            timeout=30_000,
        )
        assert page.locator("#playground-select-scheme").input_value() == expected_scheme
        assert page.locator("#detected-scheme-tag").inner_text() == expected_scheme

    # Style and output-format changes are wired to a successful real result as well.
    itanium = page.locator('.preset-btn[data-scheme="itanium"]').first
    itanium.click()
    page.wait_for_function("document.querySelector('#output-status-indicator').textContent === 'Valid'")
    page.locator('label:has(input[name="pg-style"][value="gnu"])').click()
    page.wait_for_function("document.querySelector('#output-status-indicator').textContent === 'Valid'")
    page.locator('label:has(input[name="pg-format"][value="both"])').click()
    assert " ==> " in page.locator("#output-display").inner_text()

    page.locator("#playground-input-symbol").fill("not-a-mangled-symbol")
    page.get_by_text("Unrecognized", exact=True).wait_for()
    assert page.locator("#output-error-banner").is_visible()

    page.locator("#playground-input-symbol").fill("_Z")
    page.get_by_text("Invalid", exact=True).wait_for()
    page.locator("#playground-input-symbol").fill("_ZN3foo3barEi\n\nmemcpy\n_Z\n")
    page.locator("#playground-select-scheme").select_option("auto")
    page.locator("#playground-batch-mode").check()
    page.wait_for_function(
        "document.querySelector('#output-status-indicator').textContent === '5 lines: 1 parsed, 2 failed, 2 blank'",
        timeout=30_000,
    )
    rows = page.locator(".batch-table tbody tr")
    assert "foo::bar(int)" in rows.nth(0).inner_text()
    assert "Unrecognized" in rows.nth(2).inner_text()
    assert "Invalid" in rows.nth(3).inner_text()
    page.locator('label:has(input[name="pg-format"][value="full"])').click()
    page.locator("#btn-copy-output").click()
    page.wait_for_function("window.__clipboardWrites.length === 1")
    assert page.evaluate("window.__clipboardWrites[0]") == "foo::bar(int)\n\nmemcpy\n_Z\n"


@pytest.mark.parametrize("width", [320, 768])
def test_populated_batch_layout_and_actions_fit_with_enlarged_text(context, site_server, width):
    page = open_playground(context, site_server)
    page.set_viewport_size({"width": width, "height": 1000})
    page.evaluate("document.documentElement.style.fontSize = '32px'")
    wait_ready(page)
    page.locator("#playground-batch-mode").check()
    page.locator("#playground-input-symbol").fill("long-" + "x" * 100 + "\ninvalid")
    page.wait_for_function("document.querySelector('#output-status-indicator').textContent.startsWith('2 lines:')")
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    metrics = page.locator(".playground-output-card .card-header").evaluate(
        """header => {
          const boxes = [...header.children].map(node => node.getBoundingClientRect());
          const [a, b] = boxes;
          const table = document.querySelector('.batch-table');
          return {overlap: a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1,
            tableFits: table.scrollWidth <= table.clientWidth + 1};
        }"""
    )
    assert not metrics["overlap"]
    assert metrics["tableFits"]
