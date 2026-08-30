#!/usr/bin/env python3
"""
import_batch.py
================

Import every component found under a source directory into the MIKILAB
library, one subdirectory per component:

    batch_source/
        TPS7A2018PDBVR/
            TPS7A2018PDBVR.kicad_sym
            SOT95P280X145-5N.kicad_mod
            TPS7A2018PDBVR.step
        ESP32-S3-WROOM-1/
            ESP32-S3-WROOM-1.kicad_sym
            ESP32-S3-WROOM-1.kicad_mod

Usage:
    python3 scripts/import_batch.py --source /path/to/batch_source [--category power]

Each subdirectory name is used as the component --name. Exactly one
.kicad_sym is required per subdirectory; a .kicad_mod and a 3D model
(.step/.stp/.wrl/.wrz) are optional, matching import_component.py's
"symbol only / symbol+footprint / symbol+footprint+model" support.

This is a thin loop around import_component's core functions -- no
separate collision/validation logic is reimplemented here, so behaviour
(refuse existing names, dedupe identical footprints, rename real
collisions, regenerate lib tables) is identical to a single
import_component.py run, just repeated per subdirectory. The lib tables
are regenerated once at the end rather than after every component, for
speed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_common as lc
import import_component as ic

ROOT = lc.LIBRARY_ROOT

MODEL_EXTS = (".step", ".stp", ".wrl", ".wrz")


def find_one(directory: Path, suffixes: tuple[str, ...]) -> Path | None:
    matches = [p for p in sorted(directory.rglob("*")) if p.is_file() and p.suffix.lower() in suffixes]
    return matches[0] if matches else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-import components into the MIKILAB library.")
    parser.add_argument("--source", required=True, help="Directory containing one subdirectory per component")
    parser.add_argument("--category", choices=lc.CATEGORIES, help="Force this category for every component (default: auto-detect per component)")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    if not source.is_dir():
        print(f"ERROR: --source is not a directory: {source}")
        return 1

    component_dirs = sorted(p for p in source.iterdir() if p.is_dir())
    if not component_dirs:
        print(f"No component subdirectories found under {source}")
        return 1

    results = []

    for comp_dir in component_dirs:
        name = comp_dir.name
        symbol = find_one(comp_dir, (".kicad_sym",))
        footprint = find_one(comp_dir, (".kicad_mod",))
        model = find_one(comp_dir, MODEL_EXTS)

        if symbol is None:
            results.append((name, "ERROR", "no .kicad_sym file found in subdirectory"))
            continue

        category = args.category or lc.classify(name)
        manifest_rows: list[list[str]] = []
        report: list[str] = []

        try:
            ic.import_symbol(name, category, symbol, manifest_rows, report)

            fp_path = None
            if footprint is not None:
                fp_path, fp_nick, fp_name, _ = ic.import_footprint(name, category, footprint, manifest_rows, report)

                if model is not None:
                    ic.import_model(name, category, model, fp_path, manifest_rows, report)

                sym_path = ROOT / "symbols" / category / f"{name}.kicad_sym"
                text = sym_path.read_text(encoding="utf-8")
                new_text, changed = lc.set_symbol_footprint_property(text, f"{fp_nick}:{fp_name}")
                if changed:
                    sym_path.write_text(new_text, encoding="utf-8")

            lc.append_manifest_rows(ROOT, manifest_rows)
            results.append((name, "OK", f"category={category}"))

        except ic.ImportError_ as e:
            lc.append_manifest_rows(ROOT, manifest_rows)
            results.append((name, "ERROR", str(e)))

    sym_entries = lc.write_sym_lib_table(ROOT)
    fp_entries = lc.write_fp_lib_table(ROOT)
    lc.write_global_tables(ROOT)

    print(f"Batch import from {source}")
    print()
    for name, status, note in results:
        print(f"  {status:6} {name:30} {note}")

    print()
    print(f"Regenerated sym-lib-table ({len(sym_entries)} libraries) and fp-lib-table ({len(fp_entries)} libraries)")
    print("Regenerated sym-lib-table.global and fp-lib-table.global")
    print(
        "NOTE: if this library is registered globally in KiCad (README.md section 1), "
        "also re-run the merge step to update ~/Library/Preferences/kicad/*/sym-lib-table "
        "and fp-lib-table, or new components will not show up there."
    )

    ok = sum(1 for _, status, _ in results if status == "OK")
    errors = sum(1 for _, status, _ in results if status == "ERROR")
    print(f"\n{ok} imported, {errors} failed, out of {len(results)} component directories")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
