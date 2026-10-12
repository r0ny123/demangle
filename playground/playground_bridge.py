"""Small Python entry point used by the browser playground's Pyodide worker."""

from __future__ import annotations

import json

from demangle import detect
from demangle._signature import _SEPARATORS, _extract, _labelled, _Reading
from demangle.api import _read
from demangle.core.ast import builder_for
from demangle.core.errors import LimitExceeded, NotMangledError, ParseError, reraise_if_operational
from demangle.core.limits import DEFAULT_LIMITS
from demangle.core.style import get_style


def _empty(symbol: str = "") -> dict[str, object]:
    return {
        "empty": True,
        "state": "empty",
        "success": True,
        "symbol": symbol,
        "output_variants": {"full": "", "both": "", "base-name": "", "no-params": ""},
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
        "decoration": "",
        "is_function": False,
        "is_data": False,
        "is_ctor_or_dtor": False,
        "is_special": False,
        "ast": None,
        "error": None,
    }


def _demangle(symbol: str, language: str | None, style: str, *, include_ast: bool = True) -> dict:
    """Parse once; batch rows omit the graph to bound transfer and rendering costs."""
    sym = (symbol or "").strip()
    if not sym:
        return _empty()

    lang = language.strip().lower() if language and language.strip().lower() not in ("auto", "") else None
    style_name = style if style in ("llvm", "gnu") else "llvm"
    detected = detect(sym)
    try:
        resolved_style = get_style(style_name)
        plugin, tree = _read(sym, builder_for(resolved_style), lang, resolved_style, DEFAULT_LIMITS)
        signature = _extract(_Reading(plugin.name, _SEPARATORS.get(plugin.name, "::"), resolved_style), tree)
        no_params = f"{_labelled(signature)}{signature.decoration}"
        variants = {
            "full": signature.demangled,
            "both": f"{sym} ==> {signature.demangled}",
            "base-name": signature.base_name,
            "no-params": no_params,
        }
        return {
            "empty": False,
            "success": True,
            "state": "unchanged" if signature.demangled == sym else "valid",
            "symbol": sym,
            "output_variants": variants,
            "demangled": signature.demangled,
            "detected_language": detected,
            "language": signature.language,
            "qualified_name": signature.qualified_name,
            "base_name": signature.base_name,
            "namespace": signature.namespace,
            "parameters": signature.parameters,
            "return_type": signature.return_type,
            "calling_convention": signature.calling_convention,
            "qualifiers": signature.qualifiers,
            "special": signature.special,
            "decoration": signature.decoration,
            "is_function": signature.is_function,
            "is_data": signature.is_data,
            "is_ctor_or_dtor": signature.is_ctor_or_dtor,
            "is_special": signature.is_special,
            "ast": tree.to_dict() if include_ast else None,
            "error": None,
        }
    except Exception as exc:
        reraise_if_operational(exc)
        if isinstance(exc, LimitExceeded):
            state = "limit"
        elif isinstance(exc, NotMangledError):
            state = "unrecognized"
        elif isinstance(exc, ParseError):
            state = "invalid"
        elif isinstance(exc, ValueError) and str(exc).startswith("unknown language"):
            state = "unsupported"
        else:
            state = "error"
        fields = _empty(sym)
        fields.update(
            {
                "empty": False,
                "success": False,
                "state": state,
                "demangled": sym,
                "detected_language": detected,
                "language": lang or detected,
                "output_variants": {
                    "full": sym,
                    "both": f"{sym} ==> {sym}",
                    "base-name": sym,
                    "no-params": sym,
                },
                "error": str(exc),
            }
        )
        return fields


MAX_BATCH_LINES = 500
MAX_BATCH_BYTES = 256 * 1024
MAX_BATCH_OUTPUT_BYTES = 4 * 1024 * 1024


def playground_demangle(symbol: str, language: str | None = None, style: str = "llvm") -> str:
    """Return structured demangling data and all CLI-style output variants as JSON."""
    return json.dumps(_demangle(symbol, language, style))


def playground_batch(symbols: str, language: str | None = None, style: str = "llvm") -> str:
    """Return ordered per-line results, preserving blank lines and caching duplicates."""
    lines = symbols.split("\n")
    if len(lines) > MAX_BATCH_LINES or len(symbols.encode("utf-8")) > MAX_BATCH_BYTES:
        raise ValueError("Batch input is limited to 500 lines and 256 KiB. Split it into smaller batches.")
    cache = {}
    limit_cache = {}
    encoded_rows = []
    output_bytes = 0
    for line in lines:
        symbol = line.strip()
        if symbol and output_bytes >= MAX_BATCH_OUTPUT_BYTES:
            encoded = limit_cache.get(symbol)
            if encoded is None:
                row = {
                    "symbol": symbol,
                    "state": "limit",
                    "success": False,
                    "empty": False,
                    "language": None,
                    "error": "Batch output exceeded 4 MiB. Split the input into smaller batches.",
                    "output_variants": {
                        "full": symbol,
                        "both": f"{symbol} ==> {symbol}",
                        "base-name": symbol,
                        "no-params": symbol,
                    },
                }
                encoded = json.dumps(row)
                limit_cache[symbol] = encoded
        else:
            cached = cache.get(symbol)
            if cached is None:
                result = _demangle(symbol, language, style, include_ast=False)
                row = {
                    key: result[key]
                    for key in ("symbol", "state", "success", "empty", "language", "error", "output_variants")
                }
                encoded = json.dumps(row)
                cache[symbol] = encoded
            else:
                encoded = cached
        # json.dumps defaults to ensure_ascii=True, so this string is ASCII and its
        # length is its UTF-8 byte length. Cache it per symbol and reuse the encoding
        # in the final response instead of encoding each occurrence again.
        output_bytes += len(encoded)
        encoded_rows.append(encoded)
    return '{"batch": true, "rows": [' + ", ".join(encoded_rows) + "]}"
