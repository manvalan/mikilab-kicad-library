#!/usr/bin/env python3
"""
library_index.py
================

Compact JSON index of every symbol in the MIKILAB library, written by
fetch_worker.py into the folder shared with ComponentVault so the apps
(iPad/Mac) can tell which BOM components are already in the library --
also offline, from their cached copy -- and request only the missing ones.

Per symbol:
    name       symbol name (what a schematic references)
    lib        library nickname (MIKILAB_...)
    category   library category directory
    footprint  Footprint property ("nick:name"), may be empty
    lcsc       LCSC code from an "LCSC"/"LCSC Part" property, if any
    mpn        manufacturer part number property (MPN/MP/...), if any
    own        true for a component library of its own (one .kicad_sym per
               part, as created by the importers), false for the shared
               multi-symbol libraries (e.g. Power_Protection)
    model3d    MIKILAB footprints only: whether the footprint has a 3D model

Usage:
    python3 scripts/library_index.py [-o index.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_common as lc

ROOT = lc.LIBRARY_ROOT

_TOP_SYMBOL_RE = re.compile(r'^\s{0,2}\(symbol\s+"([^"]+)"', re.M)
_UNIT_RE = re.compile(r"_\d+_\d+$")
_PROPERTY_RE = re.compile(r'\(property\s+"([^"]+)"\s+"([^"]*)"')
LCSC_KEYS = {"lcsc", "lcsc part", "lcsc_part", "lcsc part #", "jlcpcb part"}
MPN_KEYS = {"mpn", "mp", "manufacturer_part_number", "manufacturer part number", "mfr part", "mfr. part #"}


def footprint_has_model(fp_ref: str, fp_index: dict[str, Path], cache: dict[str, bool]) -> bool | None:
    if ":" not in fp_ref or not fp_ref.startswith("MIKILAB_"):
        return None
    if fp_ref not in cache:
        nick, _, name = fp_ref.partition(":")
        path = fp_index.get(nick)
        mod = path / f"{name}.kicad_mod" if path else None
        cache[fp_ref] = bool(mod and mod.is_file() and "(model " in mod.read_text(encoding="utf-8", errors="replace"))
    return cache[fp_ref]


def build_index(library_name: str = "MIKILAB") -> dict:
    fp_index = {lc.fp_nickname(d): d for d in lc.discover_footprint_libraries(ROOT)}
    model_cache: dict[str, bool] = {}
    used: set[str] = set()
    components = []

    for path in lc.discover_symbol_libraries(ROOT):
        nickname = lc.unique_nickname(lc.sym_nickname(path), used)  # same as the lib tables
        text = path.read_text(encoding="utf-8", errors="replace")
        starts = [(m.start(), m.group(1)) for m in _TOP_SYMBOL_RE.finditer(text) if not _UNIT_RE.search(m.group(1))]
        own = len(starts) == 1
        for i, (pos, name) in enumerate(starts):
            block = text[pos: starts[i + 1][0] if i + 1 < len(starts) else len(text)]
            props = {k.strip().lower(): v.strip() for k, v in _PROPERTY_RE.findall(block[:20000])}
            footprint = props.get("footprint", "")
            entry = {"name": name, "lib": nickname, "category": path.parent.name}
            if footprint:
                entry["footprint"] = footprint
            lcsc = next((props[k] for k in LCSC_KEYS if props.get(k)), "")
            if re.fullmatch(r"C\d+", lcsc, re.I):
                entry["lcsc"] = lcsc.upper()
            mpn = next((props[k] for k in MPN_KEYS if props.get(k)), "")
            if mpn and mpn != name:
                entry["mpn"] = mpn
            if own:
                entry["own"] = True
            has_model = footprint_has_model(footprint, fp_index, model_cache)
            if has_model is not None:
                entry["model3d"] = has_model
            components.append(entry)

    return {
        "format": 1,
        "library": library_name,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(components),
        "components": components,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the MIKILAB library index (JSON).")
    parser.add_argument("-o", "--output", help="output file (default: stdout)")
    args = parser.parse_args()
    data = json.dumps(build_index(), ensure_ascii=False, separators=(",", ":"))
    if args.output:
        Path(args.output).write_text(data, encoding="utf-8")
    else:
        print(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
