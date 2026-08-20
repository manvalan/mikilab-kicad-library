#!/usr/bin/env python3
"""
import_ultralibrarian.py
=========================

Import a component from an UltraLibrarian KiCad export zip.

UltraLibrarian's "Download" -> KiCad zip produces (naming/nesting can vary
a bit by download, so files are found by extension/pattern, not by exact
path):
    - one .kicad_sym symbol file, usually named after a timestamp
      (e.g. "2026-08-20_07-44-49.kicad_sym") rather than the part number --
      the real name lives inside the file as the top-level symbol name.
    - a "footprints.pretty/" (or similarly named) directory holding one or
      more .kicad_mod files. UltraLibrarian frequently exports three pad-size
      variants of the *same* footprint -- nominal, "-L" (least material
      condition) and "-M" (most material condition) -- e.g.
      "VQFN24_RGE_CORNERPADS_TEX.kicad_mod", "...-L.kicad_mod",
      "...-M.kicad_mod". Only one is wired into the symbol's Footprint
      property; the others are alternates the user may swap in manually
      and are intentionally left un-imported (see report).
    - optionally a 3D model (.step/.stp/.wrl/.wrz), sometimes in a separate
      "3D" folder or a separate download altogether -- this script picks
      one up if present, and proceeds without it if not.

Usage:
    python3 scripts/import_ultralibrarian.py --zip ~/Downloads/ul_XTAC5212IRGER.zip \\
        [--name XTAC5212IRGER] [--category ic]

If --name is omitted, it's taken from the symbol file's own top-level
symbol name (this is the reliable identifier, since the file itself is
timestamp-named). If --category is omitted, it's auto-detected the same
way as import_component.py.

This is a thin front-end: it only locates, disambiguates and unzips
files, then hands off to import_component's core (import_symbol/
import_footprint/import_model) for every validation, collision, and
lib-table rule already established there -- no logic is duplicated.
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

_FOOTPRINT_PROP_RE = re.compile(r'\(property\s+"Footprint"\s+"([^"]*)"')
_FOOTPRINT_NAME_RE = re.compile(r'\(footprint\s+"([^"]+)"')


def find_all(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in suffixes
    )


def guess_name(symbol_path: Path) -> str:
    text = symbol_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'\(symbol\s+"([^"]+)"', text)
    if not m:
        raise ic.ImportError_(f"Could not find a top-level symbol name in {symbol_path}")
    return m.group(1)


def symbol_footprint_ref(symbol_path: Path) -> str | None:
    text = symbol_path.read_text(encoding="utf-8", errors="replace")
    m = _FOOTPRINT_PROP_RE.search(text)
    if not m or not m.group(1).strip():
        return None
    return m.group(1)


def pick_footprint(candidates: list[Path], footprint_ref: str | None, report: list) -> Path:
    """UltraLibrarian often exports several pad-size variants of the same
    footprint (nominal / -L / -M). Prefer the one whose internal footprint
    name matches the symbol's Footprint property exactly (this is the one
    UltraLibrarian actually wired up); fall back to the first candidate and
    note the ambiguity."""
    if len(candidates) == 1:
        return candidates[0]

    ref_name = footprint_ref.split(":", 1)[-1] if footprint_ref else None

    if ref_name:
        for cand in candidates:
            text = cand.read_text(encoding="utf-8", errors="replace")
            m = _FOOTPRINT_NAME_RE.search(text)
            if m and m.group(1) == ref_name and cand.stem == ref_name:
                others = ", ".join(c.name for c in candidates if c != cand)
                report.append(
                    f"  NOTE: {len(candidates)} footprint variants found "
                    f"({', '.join(c.name for c in candidates)}); picked '{cand.name}' "
                    f"as it matches the symbol's Footprint property and is left "
                    f"unimported: {others}"
                )
                return cand

    report.append(
        f"  WARNING: {len(candidates)} footprint variants found "
        f"({', '.join(c.name for c in candidates)}) and none matched the symbol's "
        f"Footprint property unambiguously; picked '{candidates[0].name}' -- verify "
        f"this is the right pad-size variant."
    )
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a component from an UltraLibrarian KiCad export zip.")
    parser.add_argument("--zip", required=True, help="Path to the UltraLibrarian .zip download")
    parser.add_argument("--name", help="Component name (default: taken from the symbol's own name)")
    parser.add_argument("--category", choices=lc.CATEGORIES, help="MIKILAB category (default: auto-detected)")
    parser.add_argument("--update", action="store_true", help="Replace an existing component's symbol/footprint in place instead of refusing")
    args = parser.parse_args()

    zip_path = Path(args.zip).expanduser().resolve()
    if not zip_path.is_file():
        print(f"ERROR: --zip file not found: {zip_path}")
        return 1

    with tempfile.TemporaryDirectory(prefix="ultralibrarian_") as tmp:
        tmp_dir = Path(tmp)
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(tmp_dir)
        except zipfile.BadZipFile:
            print(f"ERROR: {zip_path} is not a valid zip file")
            return 1

        symbols = find_all(tmp_dir, (".kicad_sym",))
        if not symbols:
            print(f"ERROR: no .kicad_sym file found inside {zip_path.name}")
            return 1
        if len(symbols) > 1:
            print(f"ERROR: multiple .kicad_sym files found inside {zip_path.name}: "
                  + ", ".join(str(p.relative_to(tmp_dir)) for p in symbols))
            return 1
        symbol = symbols[0]

        footprint_candidates = find_all(tmp_dir, (".kicad_mod",))
        model_candidates = find_all(tmp_dir, MODEL_EXTS)
        model = model_candidates[0] if model_candidates else None

        try:
            name = args.name or guess_name(symbol)
        except ic.ImportError_ as e:
            print(f"ERROR: {e}")
            return 1

        category = args.category or lc.classify(name)
        manifest_rows: list[list[str]] = []
        report: list[str] = [f"Importing '{name}' from UltraLibrarian zip {zip_path.name} into category '{category}'"]
        report.append(f"  found symbol: {symbol.relative_to(tmp_dir)}")
        if footprint_candidates:
            report.append(
                f"  found {len(footprint_candidates)} footprint file(s): "
                + ", ".join(str(p.relative_to(tmp_dir)) for p in footprint_candidates)
            )
        else:
            report.append("  found footprint: (none)")
        report.append(f"  found model: {model.relative_to(tmp_dir) if model else '(none)'}")
        if len(model_candidates) > 1:
            report.append(
                f"  NOTE: {len(model_candidates)} 3D model files found; picked "
                f"'{model.relative_to(tmp_dir)}', ignored: "
                + ", ".join(str(p.relative_to(tmp_dir)) for p in model_candidates[1:])
            )

        try:
            ic.import_symbol(name, category, symbol, manifest_rows, report, update=args.update)
        except ic.ImportError_ as e:
            print(f"ERROR: {e}")
            lc.append_manifest_rows(ROOT, [["symbol", str(zip_path), "", "ERROR", "", str(e)]])
            return 1

        if footprint_candidates:
            footprint_ref = symbol_footprint_ref(symbol)
            footprint = pick_footprint(footprint_candidates, footprint_ref, report)

            fp_path, fp_nick, fp_name, _ = ic.import_footprint(name, category, footprint, manifest_rows, report, update=args.update)

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

    sym_entries = lc.write_sym_lib_table(ROOT)
    fp_entries = lc.write_fp_lib_table(ROOT)
    report.append(f"Regenerated sym-lib-table ({len(sym_entries)} libraries) and fp-lib-table ({len(fp_entries)} libraries)")

    print("\n".join(report))
    print("\nOK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
