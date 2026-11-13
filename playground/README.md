# Playground

The static page behind <https://r0ny123.github.io/demangle/playground/>;
[docs/playground.md](../docs/playground.md) describes what it does.

| File | Role |
| --- | --- |
| `index.html`, `style.css`, `playground.js` | The page and its interface |
| `theme.js` | Applies the saved light or dark theme before the page is drawn |
| `playground-worker.js` | Loads Pyodide and the library in a Web Worker |
| `playground_bridge.py` | The Python the worker calls: one symbol, or a batch, as JSON |

`mkdocs build` zips `src/demangle` into `playground/assets/demangle.zip` (see
`docs/hooks.py`) and copies this folder into the site, so to run it locally:

```console
uv run --group docs mkdocs build --strict
python -m http.server 8000 --directory site    # then open http://localhost:8000/playground/
```

`tests/test_playground.py` covers the bridge; the browser regressions and how to run
them are in [CONTRIBUTING.md](../CONTRIBUTING.md#getting-set-up).
