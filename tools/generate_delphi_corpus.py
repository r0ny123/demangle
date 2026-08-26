#!/usr/bin/env python3
"""Sample a Delphi/C++Builder conformance corpus from real export tables.

There is no Delphi compiler here, and TDUMP is a Windows RAD Studio tool. What stands
in is the export names of real BPLs and C++Builder DLLs, scored against the spelling
Embarcadero's unmangler prints. This script:

* reads those names from PE export tables (a `.bpl` / `.dll` / `.exe`), or from a
  TDUMP dump in the YAML form `zed-0xff/unmangler` records (`samples/borland.yaml`);
* keeps a small sample of each parser kind so the checked-in corpus covers the
  constructs that actually occur, not only the unmangler's own unit tests.

The expected column is this library's reading. Agreement with TDUMP over the whole dump
is measured separately and pinned in `tests/test_conformance.py`.
"""

from __future__ import annotations

import argparse
import struct
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from demangle.schemes.delphi._parser import (  # noqa: E402
    DemangleFailure,
    parse_delphi_symbol,
)


def pe_export_names(path: Path):
    """Yield export names from a PE32 or PE32+ image."""
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise SystemExit(f"{path} is not a PE image")
    (e_lfanew,) = struct.unpack_from("<I", data, 0x3C)
    if data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        raise SystemExit(f"{path} is not a PE image")
    coff = e_lfanew + 4
    (_, nsections, _, _, _, opt_size, _) = struct.unpack_from("<HHIIIHH", data, coff)
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x10B:  # PE32
        export_rva, export_size = struct.unpack_from("<II", data, opt + 96)
    elif magic == 0x20B:  # PE32+
        export_rva, export_size = struct.unpack_from("<II", data, opt + 112)
    else:
        raise SystemExit(f"{path} has an unknown optional-header magic")
    if not export_rva or not export_size:
        return

    sections = []
    sec = opt + opt_size
    for _ in range(nsections):
        _, _, vsize, va, raw_size, raw_ptr = struct.unpack_from("<8sIIIII", data, sec)[:6]
        sections.append((va, max(vsize, raw_size), raw_ptr))
        sec += 40

    def rva_to_off(rva):
        for va, size, raw in sections:
            if va <= rva < va + size:
                return raw + (rva - va)
        raise SystemExit(f"{path}: RVA {rva:#x} is not in a section")

    off = rva_to_off(export_rva)
    _, _, _, _, _, _, _, nnames, _, names_rva = struct.unpack_from("<IIHHIIIIII", data, off)
    names_off = rva_to_off(names_rva)
    for i in range(nnames):
        (name_rva,) = struct.unpack_from("<I", data, names_off + 4 * i)
        start = rva_to_off(name_rva)
        end = data.index(b"\0", start)
        yield data[start:end].decode("latin1")


def load_tdump_yaml(path: Path):
    """Mangled names from a TDUMP dump. Wrapped values are ignored; names are enough."""
    names = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("! '"):
            continue
        rest = line[3:]
        cut = rest.find("': ")
        if cut < 0:
            continue
        names.append(rest[:cut])
    return names


def sample(names, per_kind):
    by_kind = defaultdict(list)
    refused = 0
    for name in names:
        if not name.startswith("@"):
            continue
        try:
            symbol = parse_delphi_symbol(name)
        except DemangleFailure:
            refused += 1
            continue
        by_kind[symbol.kind].append((name, symbol.text))
    rows = []
    for kind in sorted(by_kind):
        group = by_kind[kind]
        step = max(1, len(group) // per_kind)
        rows.extend(group[::step][:per_kind])
    rows.sort(key=lambda item: item[0])
    return rows, by_kind, refused


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="PE images and/or borland.yaml")
    parser.add_argument("--per-kind", type=int, default=8)
    parser.add_argument("--out", type=Path, default=ROOT / "tests" / "conformance" / "delphi-real-world.txt")
    arguments = parser.parse_args()

    names = []
    sources = []
    for path in arguments.inputs:
        if not path.exists():
            sys.exit(f"{path} does not exist")
        if path.suffix.lower() in {".yaml", ".yml"}:
            found = load_tdump_yaml(path)
            sources.append(f"{path.name} ({len(found)} names)")
            names.extend(found)
        else:
            found = list(pe_export_names(path))
            sources.append(f"{path.name} ({len(found)} exports)")
            names.extend(found)

    rows, by_kind, refused = sample(names, arguments.per_kind)
    counts = ", ".join(f"{kind} {len(group)}" for kind, group in sorted(by_kind.items()))
    header = (
        "# Borland/Embarcadero Delphi and C++Builder exports.\n"
        "#\n"
        "# Sampled by tools/generate_delphi_corpus.py from real PE export tables / a TDUMP\n"
        "# dump of them. There is no Delphi compiler here; the expected column is this\n"
        "# library's reading, and agreement with TDUMP over the whole dump is pinned in\n"
        "# tests/test_conformance.py.\n"
        "#\n"
        f"# Sources: {'; '.join(sources)}.\n"
        f"# Readable: {sum(len(g) for g in by_kind.values())}; refused: {refused}; "
        f"kinds: {counts}.\n"
    )
    arguments.out.write_text(header + "".join(f"{m}\t{t}\n" for m, t in rows), encoding="utf-8")
    print(f"wrote {len(rows)} rows to {arguments.out}")
    print("kinds:", counts)
    print("refused:", refused)


if __name__ == "__main__":
    main()
