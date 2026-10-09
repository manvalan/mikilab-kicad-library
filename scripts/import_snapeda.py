#!/usr/bin/env python3
"""
import_snapeda.py
==================

Import a component from a SnapEDA (SnapMagic Search) KiCad export zip.

SnapEDA's "Download KiCad" button produces a .zip containing (naming and
nesting vary a bit by download, so this is found by file extension, not
by exact path):
    - one .kicad_sym symbol file
    - one .kicad_mod footprint file (sometimes inside a "<name>.pretty/" dir)
    - optionally one 3D model (.step/.stp/.wrl/.wrz)

Usage:
    python3 scripts/import_snapeda.py --zip ~/Downloads/TPS7A2018PDBVR.zip \\
        [--name TPS7A2018PDBVR] [--category power]

If --name is omitted, it's taken from the symbol file's own top-level
symbol name. If --category is omitted, it's auto-detected the same way
as import_component.py.

This is a thin front-end: it only locates and unzips files, then hands
off to import_component's core (import_symbol/import_footprint/
import_model) for every validation, collision, and lib-table rule
already established there -- no logic is duplicated.
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_common as lc
import import_component as ic

ROOT = lc.LIBRARY_ROOT

MODEL_EXTS = (".step", ".stp", ".wrl", ".wrz")


def find_first(root: Path, suffixes: tuple[str, ...]) -> Path | None:
    matches = sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in suffixes
    )
    return matches[0] if matches else None


def guess_name(symbol_path: Path) -> str:
    text = symbol_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'\(symbol\s+"([^"]+)"', text)
    if not m:
        raise ic.ImportError_(f"Could not find a top-level symbol name in {symbol_path}")
    return m.group(1)


def import_zip(zip_path: Path, name: str | None = None, category: str | None = None,
               update: bool = False, require_footprint: bool = False,
               allow_mismatch: bool = False) -> tuple[str, list[str]]:
    """Import one SnapEDA KiCad zip into the library (symbol, footprint,
    3D model, MANIFEST rows). Does not regenerate the lib tables -- the
    caller does that once. Returns (component name, report lines);
    raises ic.ImportError_ on failure."""
    with tempfile.TemporaryDirectory(prefix="snapeda_") as tmp:
        tmp_dir = Path(tmp)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmp_dir)
        except zipfile.BadZipFile:
            raise ic.ImportError_(f"{zip_path} is not a valid zip file")

        symbol = find_first(tmp_dir, (".kicad_sym",))
        if symbol is None:
            raise ic.ImportError_(f"no .kicad_sym file found inside {zip_path.name}")

        footprint = find_first(tmp_dir, (".kicad_mod",))
        model = find_first(tmp_dir, MODEL_EXTS)

        name = name or guess_name(symbol)
        category = category or lc.classify(name)
        manifest_rows: list[list[str]] = []
        report: list[str] = [f"Importing '{name}' from SnapEDA zip {zip_path.name} into category '{category}'"]
        for label, path in (("symbol", symbol), ("footprint", footprint), ("model", model)):
            report.append(f"  found {label}: {path.relative_to(tmp_dir) if path else '(none)'}")

        try:
            ic.verify_symbol_footprint(symbol, footprint, report, require_footprint, allow_mismatch)
            ic.import_symbol(name, category, symbol, manifest_rows, report, update=update)
        except ic.ImportError_ as e:
            lc.append_manifest_rows(ROOT, [["symbol", str(zip_path), "", "ERROR", "", str(e)]])
            raise

        if footprint is not None:
            fp_path, fp_nick, fp_name, _ = ic.import_footprint(name, category, footprint, manifest_rows, report, update=update)

            if model is not None:
                ic.import_model(name, category, model, fp_path, manifest_rows, report)

            sym_path = ROOT / "symbols" / category / f"{name}.kicad_sym"
            text = sym_path.read_text(encoding="utf-8")
            new_text, changed = lc.set_symbol_footprint_property(text, f"{fp_nick}:{fp_name}")
            if changed:
                sym_path.write_text(new_text, encoding="utf-8")
                report.append(f"  Linked symbol Footprint property -> {fp_nick}:{fp_name}")
        elif model is not None:
            report.append("  NOTE: 3D model found but no footprint to attach it to -- skipped")

        lc.append_manifest_rows(ROOT, manifest_rows)

    return name, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a component from a SnapEDA KiCad export zip.")
    parser.add_argument("--zip", required=True, help="Path to the SnapEDA .zip download")
    parser.add_argument("--name", help="Component name (default: taken from the symbol's own name)")
    parser.add_argument("--category", choices=lc.CATEGORIES, help="MIKILAB category (default: auto-detected)")
    parser.add_argument("--update", action="store_true", help="Replace an existing component's symbol/footprint in place instead of refusing")
    parser.add_argument("--allow-pin-mismatch", action="store_true", help="Import even if some symbol pins have no matching footprint pad")
    args = parser.parse_args()

    zip_path = Path(args.zip).expanduser().resolve()
    if not zip_path.is_file():
        print(f"ERROR: --zip file not found: {zip_path}")
        return 1

    try:
        _, report = import_zip(zip_path, args.name, args.category, update=args.update,
                               allow_mismatch=args.allow_pin_mismatch)
    except ic.ImportError_ as e:
        print(f"ERROR: {e}")
        return 1

    sym_entries = lc.write_sym_lib_table(ROOT)
    fp_entries = lc.write_fp_lib_table(ROOT)
    lc.write_global_tables(ROOT)
    report.append(f"Regenerated sym-lib-table ({len(sym_entries)} libraries) and fp-lib-table ({len(fp_entries)} libraries)")
    report.append("Regenerated sym-lib-table.global and fp-lib-table.global")

    print("\n".join(report))
    print("\nOK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
