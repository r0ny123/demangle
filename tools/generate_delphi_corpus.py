#!/usr/bin/env python3
"""Build the Delphi/C++Builder conformance corpora from a real TDUMP dump.

There is no Delphi compiler here, and TDUMP is a Windows RAD Studio tool. What stands in
is a recorded dump: `zed-0xff/unmangler` ships `samples/borland.yaml`, which its
`misc/2_tdump.rb` produces by running the real `tdump.exe -q -um` under wine over the
export tables of real BPLs and C++Builder DLLs. The keys are the mangled names and the
values are what Embarcadero's own unmangler printed for them.

**The expected column is that reference spelling, never this library's own reading.** A
corpus scored against its own output measures nothing, and the whole-table agreement it
is supposed to establish then rests on a number no test can check. Both files this
writes are replayed: `delphi-tdump.txt` is every entry in the dump, and
`delphi-real-world.txt` is a per-kind sample of it for a reader to look at.

A PE image can be passed as well, but only to report coverage. Its exports come with no
reference spelling, so they are counted and never written.

Reading the dump needs PyYAML -- the emitter uses the explicit `? key` / `: value` form
for long keys and folds long plain scalars across lines, and a hand-rolled reader that
missed either silently dropped 87 of 11,373 entries. Nothing in the package itself gains
a dependency; this script is run by hand when the dump changes.
"""

from __future__ import annotations

import argparse
import struct
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFORMANCE = ROOT / "tests" / "conformance"
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
    """(mangled, reference spelling) pairs from a TDUMP dump."""
    try:
        import yaml
    except ImportError:  # pragma: no cover - a by-hand tool, not part of the package
        raise SystemExit("reading a TDUMP dump needs PyYAML: pip install pyyaml") from None
    loaded = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(loaded, dict):
        raise SystemExit(f"{path} is not a mangled-name mapping")
    rejected = [key for key, value in loaded.items() if not isinstance(key, str) or not isinstance(value, str)]
    if rejected:
        raise SystemExit(f"{path} holds {len(rejected)} non-string entries")
    return sorted(loaded.items())


def sort_by_kind(pairs):
    """Group `(mangled, reference spelling)` by what this parser calls each name.

    The kind is only used to spread the sample across the constructs that occur. The
    spelling written out is the reference's, whatever this parser made of the name.
    """
    by_kind = defaultdict(list)
    refused = 0
    for name, spelling in pairs:
        if not name.startswith("@"):
            continue
        try:
            symbol = parse_delphi_symbol(name)
        except DemangleFailure:
            refused += 1
            continue
        by_kind[symbol.kind].append((name, spelling))
    return by_kind, refused


def sample(by_kind, per_kind):
    rows = []
    for kind in sorted(by_kind):
        group = by_kind[kind]
        step = max(1, len(group) // per_kind)
        rows.extend(group[::step][:per_kind])
    rows.sort(key=lambda item: item[0])
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="borland.yaml and/or PE images")
    parser.add_argument("--per-kind", type=int, default=8)
    parser.add_argument("--out", type=Path, default=CONFORMANCE / "delphi-real-world.txt")
    parser.add_argument("--whole", type=Path, default=CONFORMANCE / "delphi-tdump.txt")
    arguments = parser.parse_args()

    pairs = []
    sources = []
    unreferenced = 0
    for path in arguments.inputs:
        if not path.exists():
            sys.exit(f"{path} does not exist")
        if path.suffix.lower() in {".yaml", ".yml"}:
            found = load_tdump_yaml(path)
            sources.append(f"{path.name} ({len(found)} names)")
            pairs.extend(found)
        else:
            exports = [name for name in pe_export_names(path) if name.startswith("@")]
            unreferenced += len(exports)
            sources.append(f"{path.name} ({len(exports)} exports, no reference spelling: counted only)")

    if not pairs:
        sys.exit("no reference spellings to write: pass a TDUMP dump, not only PE images")

    by_kind, refused = sort_by_kind(pairs)
    readable = sum(len(group) for group in by_kind.values())
    counts = ", ".join(f"{kind} {len(group)}" for kind, group in sorted(by_kind.items()))
    provenance = (
        "# Borland/Embarcadero Delphi and C++Builder exports, with the spelling\n"
        "# Embarcadero's own unmangler prints for each.\n"
        "#\n"
        "# Written by tools/generate_delphi_corpus.py from a TDUMP dump -- the real\n"
        "# tdump.exe -q -um, run over the export tables of real BPLs and C++Builder DLLs.\n"
        "# The expected column is that reference output, not this library's reading.\n"
        "#\n"
        f"# Sources: {'; '.join(sources)}.\n"
        f"# Readable: {readable}; refused: {refused}; kinds: {counts}.\n"
    )

    whole = sorted(by_kind_rows(by_kind))
    arguments.whole.write_text(
        provenance + "#\n# Every readable entry in the dump.\n" + rows_to_text(whole), encoding="utf-8"
    )
    rows = sample(by_kind, arguments.per_kind)
    arguments.out.write_text(
        provenance
        + f"#\n# A sample of {arguments.per_kind} per kind; the whole table is delphi-tdump.txt.\n"
        + rows_to_text(rows),
        encoding="utf-8",
    )
    print(f"wrote {len(whole)} rows to {arguments.whole}")
    print(f"wrote {len(rows)} rows to {arguments.out}")
    print("kinds:", counts)
    print("refused:", refused)
    if unreferenced:
        print(f"PE exports counted but not written (no reference spelling): {unreferenced}")


def by_kind_rows(by_kind):
    for group in by_kind.values():
        yield from group


def rows_to_text(rows):
    return "".join(f"{name}\t{spelling}\n" for name, spelling in rows)


if __name__ == "__main__":
    main()
