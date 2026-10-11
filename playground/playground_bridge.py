"""Small Python entry point used by the browser playground's Pyodide worker."""

from __future__ import annotations

import json

from demangle._signature import _SEPARATORS, _extract, _labelled, _Reading
from demangle.api import _read
from demangle.core.ast import builder_for
from demangle.core.limits import DEFAULT_LIMITS
from demangle.core.style import get_style


def _empty(symbol: str = "") -> dict[str, object]:
    return {
        "empty": True,
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


def playground_demangle(symbol: str, language: str | None = None, style: str = "llvm") -> str:
    """Return structured demangling data and all CLI-style output variants as JSON."""
    sym = (symbol or "").strip()
    if not sym:
        return json.dumps(_empty())

    lang = language.strip().lower() if language and language.strip().lower() not in ("auto", "") else None
    style_name = style if style in ("llvm", "gnu") else "llvm"
    try:
        detected = None
        try:
            from demangle import detect

            detected = detect(sym)
        except Exception:
            pass

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
        return json.dumps(
            {
                "empty": False,
                "success": True,
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
                "ast": tree.to_dict(),
                "error": None,
            }
        )
    except Exception as exc:
        fields = _empty(sym)
        fields.update(
            {
                "empty": False,
                "success": False,
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
        return json.dumps(fields)
