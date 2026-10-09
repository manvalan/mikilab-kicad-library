#!/usr/bin/env python3
"""
import_easyeda.py
=================

Import a component from JLCPCB/LCSC's EasyEDA library by LCSC part code
(e.g. C45044), converted to KiCad by easyeda2kicad
(https://github.com/uPesy/easyeda2kicad.py -- `pip install easyeda2kicad`,
no account needed).

Usage:
    python3 scripts/import_easyeda.py --lcsc C45044 [--name RTL8201F-VB-CG]
        [--category rf] [--update] [--allow-pin-mismatch]

easyeda2kicad writes <out>.kicad_sym, <out>.pretty/<fp>.kicad_mod and
<out>.3dshapes/<model>.{wrl,step}. The footprint's (model ...) block
points at that temporary absolute .wrl path, so it is dropped before
import; the STEP is then linked through import_model (${MIKILAB}/...)
and the original offset/rotation of the EasyEDA block is restored.

Like the SnapEDA/UltraLibrarian front-ends, all collision/manifest/
lib-table rules come from import_component's core.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_common as lc
import import_component as ic

ROOT = lc.LIBRARY_ROOT

_LCSC_RE = re.compile(r"^C\d+$")
_MODEL_BLOCK_RE = re.compile(r"\n?[ \t]*\(model\s.*?\n[ \t]*\)[ \t]*(?=\n)", re.S)
_XYZ_RE = r"\({key}\s*\(xyz\s+([^)]*)\)\)"
_UNQUOTED_GENERATOR_RE = re.compile(r'\((generator(?:_version)?)\s+([^\s"()]+)\)')


def find_easyeda2kicad() -> str | None:
    return shutil.which("easyeda2kicad")


def convert(lcsc: str, out_dir: Path) -> None:
    exe = find_easyeda2kicad()
    if exe is None:
        raise ic.ImportError_("easyeda2kicad not found -- install it with: python3 -m pip install easyeda2kicad")
    proc = subprocess.run(
        [exe, "--full", "--lcsc_id", lcsc, "--output", str(out_dir / "lib")],
        cwd=out_dir, capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0 or not (out_dir / "lib.kicad_sym").is_file():
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
        raise ic.ImportError_(f"easyeda2kicad failed for {lcsc}: {' | '.join(tail) or 'no output'}")


def quote_generator(path: Path) -> None:
    """easyeda2kicad writes '(generator https://github.com/...)' unquoted,
    which KiCad's parser rejects (the whole library fails to load)."""
    text = path.read_text(encoding="utf-8")
    fixed = _UNQUOTED_GENERATOR_RE.sub(r'(\1 "\2")', text)
    if fixed != text:
        path.write_text(fixed, encoding="utf-8")


def split_model_block(footprint: Path) -> dict[str, str]:
    """Remove the (model ...) block(s) from footprint in place; return the
    offset/rotate/scale xyz strings of the first one."""
    text = footprint.read_text(encoding="utf-8")
    m = _MODEL_BLOCK_RE.search(text)
    if not m:
        return {}
    transform = {}
    for key in ("offset", "scale", "rotate"):
        km = re.search(_XYZ_RE.format(key=key), m.group(0))
        if km:
            transform[key] = km.group(1).strip()
    footprint.write_text(_MODEL_BLOCK_RE.sub("", text), encoding="utf-8")
    return transform


def restore_transform(footprint: Path, transform: dict[str, str]) -> None:
    text = footprint.read_text(encoding="utf-8")
    for key, xyz in transform.items():
        text = re.sub(_XYZ_RE.format(key=key), f"({key} (xyz {xyz}))", text, count=1)
    footprint.write_text(text, encoding="utf-8")


def import_lcsc(lcsc: str, name: str | None = None, category: str | None = None,
                update: bool = False, require_footprint: bool = False,
                allow_mismatch: bool = False) -> tuple[str, list[str]]:
    """Convert and import one LCSC part. Does not regenerate the lib
    tables. Returns (component name, report lines); raises ic.ImportError_."""
    lcsc = lcsc.strip().upper()
    if not _LCSC_RE.match(lcsc):
        raise ic.ImportError_(f"'{lcsc}' is not an LCSC part code (expected C<digits>)")

    with tempfile.TemporaryDirectory(prefix="easyeda_") as tmp:
        out = Path(tmp)
        convert(lcsc, out)

        symbol = out / "lib.kicad_sym"
        footprints = sorted((out / "lib.pretty").glob("*.kicad_mod"))
        models = sorted((out / "lib.3dshapes").glob("*.step")) or sorted((out / "lib.3dshapes").glob("*.wrl"))
        footprint = footprints[0] if footprints else None
        model = models[0] if models else None
        for path in (symbol, footprint):
            if path is not None:
                quote_generator(path)

        sym_name = re.search(r'\(symbol\s+"([^"]+)"', symbol.read_text(encoding="utf-8", errors="replace"))
        name = name or (sym_name.group(1) if sym_name else lcsc)
        category = category or lc.classify(name)
        manifest_rows: list[list[str]] = []
        report = [f"Importing '{name}' from EasyEDA/LCSC {lcsc} into category '{category}'"]
        for label, path in (("symbol", symbol), ("footprint", footprint), ("model", model)):
            report.append(f"  found {label}: {path.relative_to(out) if path else '(none)'}")

        try:
            ic.verify_symbol_footprint(symbol, footprint, report, require_footprint, allow_mismatch)
            ic.import_symbol(name, category, symbol, manifest_rows, report, update=update)
        except ic.ImportError_ as e:
            lc.append_manifest_rows(ROOT, [["symbol", f"lcsc:{lcsc}", "", "ERROR", "", str(e)]])
            raise

        if footprint is not None:
            transform = split_model_block(footprint)
            fp_path, fp_nick, fp_name, _ = ic.import_footprint(name, category, footprint, manifest_rows, report, update=update)

            if model is not None:
                ic.import_model(name, category, model, fp_path, manifest_rows, report)
                if fp_path is not None and transform:
                    restore_transform(fp_path, transform)

            sym_path = ROOT / "symbols" / category / f"{name}.kicad_sym"
            text = sym_path.read_text(encoding="utf-8")
            new_text, changed = lc.set_symbol_footprint_property(text, f"{fp_nick}:{fp_name}")
            if changed:
                sym_path.write_text(new_text, encoding="utf-8")
                report.append(f"  Linked symbol Footprint property -> {fp_nick}:{fp_name}")

        for row in manifest_rows:
            row[1] = f"lcsc:{lcsc}/{Path(row[1]).name}"  # temp dir paths are meaningless later
        lc.append_manifest_rows(ROOT, manifest_rows)

    return name, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a component from EasyEDA/LCSC by LCSC part code.")
    parser.add_argument("--lcsc", required=True, help="LCSC part code, e.g. C45044")
    parser.add_argument("--name", help="Component name (default: the EasyEDA symbol name)")
    parser.add_argument("--category", choices=lc.CATEGORIES, help="MIKILAB category (default: auto-detected)")
    parser.add_argument("--update", action="store_true", help="Replace an existing component's symbol/footprint in place instead of refusing")
    parser.add_argument("--allow-pin-mismatch", action="store_true", help="Import even if some symbol pins have no matching footprint pad")
    args = parser.parse_args()

    try:
        _, report = import_lcsc(args.lcsc, args.name, args.category, update=args.update,
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
