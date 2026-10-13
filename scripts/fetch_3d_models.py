#!/usr/bin/env python3
"""
fetch_3d_models.py
==================

Add the missing 3D models to the MIKILAB library from Ultra Librarian
and SnapEDA, when they have one.

For every single-part component with a footprint but no 3D model (the
same list as find_missing_3d_models.py):
  1. a zip already in download_dir is reused (same rules as
     fetch_components.py), otherwise it is downloaded through the browser
     (eda_download.py: login automatic, captcha and Download by hand);
  2. only the STEP (or WRL) model is taken from the zip: symbol and
     footprint in the library stay as they are;
  3. the model is copied to 3dmodels/<category>/ and linked into the
     component's footprint;
  4. model_check.py verifies the placement; if the body comes out turned
     or upside down the usual fixed rotations are tried (SnapEDA models
     are often upright: rotate -90 on X) and the first one that passes is
     kept; a body sitting under the board is lifted by its lowest
     point (offset Z). If nothing passes, the model is removed again (the zip stays in
     download_dir for a manual look).
  5. an isometric render of each linked model is written (pin 1 check).

Usage:
    python3 scripts/fetch_3d_models.py [--dry-run] [--only NAME,MPN] [--category CAT]
        [--sources ultralibrarian,snapeda] [--no-download] [--limit N] [--report out.json]

Accounts and download_dir come from the same places as fetch_components.py
(Keychain, .secrets/account.json, credentials.json).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_common as lc
import import_component as ic
import fetch_components as fc
import find_missing_3d_models as fm
import model_check

ROOT = lc.LIBRARY_ROOT
SOURCES = ("ultralibrarian", "snapeda")  # Ultra Librarian exports STEP explicitly
MODEL_PREFERENCE = (".step", ".stp", ".wrl", ".wrz")
# Tried in order when model_check says the body is turned or upside down.
# No 180 deg flips: they pass the geometric check but put pin 1 on the wrong side.
ROTATIONS = ((0, 0, 0), (-90, 0, 0), (90, 0, 0), (0, 0, 90))

_MPN_RE = re.compile(r'\(property\s+"(?:MPN|Manufacturer_Part_Number|MP)"\s+"([^"]+)"', re.I)
_MODEL_BLOCK_RE = re.compile(r"\n?[ \t]*\(model [^\n]*\n(?:[ \t]+\([^\n]*\n)*?[ \t]*\)\n", re.M)
_OFFSET_RE = re.compile(r"(\(model [^\n]*\n[ \t]*\(offset \(xyz )[^)]*(\)\))")
_ROTATE_RE = re.compile(r"(\(model [^\n]*\n(?:[^\n]*\n)*?[ \t]*\(rotate \(xyz )[^)]*(\)\))")


def component_mpn(c) -> str:
    """MPN property of the component's symbol, else its name."""
    text = (ROOT / c.symbol_path).read_text(encoding="utf-8", errors="replace")
    m = _MPN_RE.search(text)
    return m.group(1).strip() if m and m.group(1).strip() else c.name


def footprint_file(c) -> Path:
    return ROOT / c.footprint_path / f"{c.footprint_files[0]}{lc.FOOTPRINT_EXT}"


def extract_model(zip_path: Path, out_dir: Path) -> Path | None:
    """The 3D model in the zip (STEP first), extracted to out_dir."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = [n for n in zf.namelist() if Path(n).suffix.lower() in MODEL_PREFERENCE and not n.startswith("__MACOSX")]
            if not names:
                return None
            names.sort(key=lambda n: (MODEL_PREFERENCE.index(Path(n).suffix.lower()), len(n)))
            target = out_dir / Path(names[0]).name
            target.write_bytes(zf.read(names[0]))
            return target
    except zipfile.BadZipFile:
        return None


def set_rotation(fp: Path, xyz: tuple[int, int, int]) -> None:
    text = fp.read_text(encoding="utf-8")
    new = _ROTATE_RE.sub(lambda m: f"{m.group(1)}{xyz[0]} {xyz[1]} {xyz[2]}{m.group(2)}", text, count=1)
    fp.write_text(new, encoding="utf-8")


def set_offset_z(fp: Path, z: float) -> None:
    text = fp.read_text(encoding="utf-8")
    fp.write_text(_OFFSET_RE.sub(lambda m: f"{m.group(1)}0 0 {z:g}{m.group(2)}", text, count=1), encoding="utf-8")


def unlink_model(fp: Path) -> None:
    text = fp.read_text(encoding="utf-8")
    fp.write_text(_MODEL_BLOCK_RE.sub("\n", text, count=1), encoding="utf-8")


def attach_and_check(c, model: Path) -> dict:
    """Copy the model into the library, link it to the footprint and
    keep the first rotation that model_check accepts."""
    fp = footprint_file(c)
    manifest_rows: list = []
    report: list[str] = []
    ic.import_model(c.name, c.category, model, fp, manifest_rows, report)
    lc.append_manifest_rows(ROOT, manifest_rows)
    if "(model " not in fp.read_text(encoding="utf-8"):
        return {"status": "FAILED", "detail": "modello non collegato al footprint"}

    messages: list[str] = []
    for xyz in ROTATIONS:
        set_rotation(fp, xyz)
        status, messages = model_check.check_footprint(fp)
        if status in ("OK", "SKIP"):
            detail = "" if xyz == (0, 0, 0) else f"ruotato {xyz}"
            return {"status": "ADDED", "check": status, "rotate": list(xyz), "detail": "; ".join(filter(None, [detail] + messages))}
    # Models placed under the board (top at z=0, or BGA balls below z=0):
    # lift the body by its lowest point.
    box = model_check.step_bbox(model)
    if box and box[0][2] < -0.01:
        set_rotation(fp, (0, 0, 0))
        set_offset_z(fp, round(-box[0][2], 3))
        status, messages = model_check.check_footprint(fp)
        if status in ("OK", "SKIP"):
            return {"status": "ADDED", "check": status, "rotate": [0, 0, 0],
                    "detail": "; ".join([f"alzato di {-box[0][2]:.2f} mm"] + messages)}
        set_offset_z(fp, 0)
    unlink_model(fp)
    copied = ROOT / manifest_rows[0][2] if manifest_rows and manifest_rows[0][3] != "DUPLICATE" else None
    if copied is not None and copied.is_file():
        copied.unlink()
    return {"status": "REJECTED", "detail": "il modello non combacia col footprint: " + "; ".join(messages)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Add missing 3D models from Ultra Librarian / SnapEDA.")
    parser.add_argument("--only", help="comma-separated component names or MPNs (default: every component without a model)")
    parser.add_argument("--category", choices=lc.CATEGORIES)
    parser.add_argument("--sources", help="comma-separated source order (default: ultralibrarian,snapeda)")
    parser.add_argument("--credentials", help="credentials/config JSON (as fetch_components.py)")
    parser.add_argument("--dry-run", action="store_true", help="only list what would be done")
    parser.add_argument("--no-download", action="store_true", help="only use zips already in download_dir")
    parser.add_argument("--limit", type=int, default=0, help="process at most N components")
    parser.add_argument("--report", help="JSON report path (default: none)")
    parser.add_argument("--render-dir", help="where to write the 3D renders (default: <download_dir>/renders)")
    args = parser.parse_args()

    sources = args.sources.split(",") if args.sources else list(SOURCES)
    for s in sources:
        if s not in SOURCES:
            raise SystemExit(f"ERROR: unknown source '{s}' (use: {', '.join(SOURCES)})")
    cfg = fc.load_config(fc.credentials_path(args.credentials), need_credentials=not (args.dry_run or args.no_download))
    download_dir = Path(cfg.get("download_dir", "~/Downloads/mikilab")).expanduser()
    render_dir = Path(args.render_dir).expanduser() if args.render_dir else download_dir / "renders"

    missing = sorted(fm.missing_3d_models(args.category), key=lambda c: (c.category, c.name))
    if args.only:
        wanted = {fc.norm_mpn(s) for s in args.only.split(",") if s.strip()}
        missing = [c for c in missing if fc.norm_mpn(c.name) in wanted or fc.norm_mpn(component_mpn(c)) in wanted]
    if args.limit:
        missing = missing[: args.limit]
    print(f"{len(missing)} componenti senza modello 3D; sorgenti: {', '.join(sources)}; zip in {download_dir}\n")

    if args.dry_run:
        for c in missing:
            part = {"name": c.name, "mpn": component_mpn(c)}
            cached = [f"{s}={p.name}" for s in sources if (p := fc.find_cached_zip(download_dir, s, part))]
            print(f"  {c.category:16} {c.name:30} mpn={part['mpn']:24} {', '.join(cached) or 'da scaricare'}")
        return 0

    results = []
    downloader = None
    try:
        for n, c in enumerate(missing, 1):
            part = {"name": c.name, "mpn": component_mpn(c)}
            print(f"[{n}/{len(missing)}] {c.name} (MPN {part['mpn']})")
            attempts: list[str] = []
            result = None
            for source in sources:
                zip_path = fc.find_cached_zip(download_dir, source, part)
                if zip_path is None and not args.no_download:
                    if downloader is None:
                        downloader = fc.make_downloader(cfg, download_dir)
                    else:
                        time.sleep(float(cfg.get("delay_seconds", 5)))
                    print(f"    {source}: download")
                    try:
                        zip_path = downloader.fetch(source, part["mpn"], None, cfg.get(source, {}),
                                                    download_dir / source / f"{c.name}.zip")
                    except Exception as e:
                        attempts.append(f"{source}: download fallito ({e})")
                        continue
                if zip_path is None:
                    attempts.append(f"{source}: nessuno zip")
                    continue
                with tempfile.TemporaryDirectory(prefix="model3d_") as tmp:
                    model = extract_model(zip_path, Path(tmp))
                    if model is None:
                        attempts.append(f"{source}: lo zip non contiene un modello 3D")
                        continue
                    print(f"    {source}: {model.name} da {zip_path.name}")
                    outcome = attach_and_check(c, model)
                if outcome["status"] == "ADDED":
                    result = {**outcome, "source": source, "zip": str(zip_path)}
                    break
                attempts.append(f"{source}: {outcome['detail']}")
            if result is None:
                result = {"status": "FAILED", "detail": "; ".join(attempts)}
            else:
                result["detail"] = "; ".join(filter(None, attempts + [result.get("detail", "")]))
                png = render_dir / f"{c.name}.png"
                if model_check.render_footprint(footprint_file(c), png):
                    result["render"] = str(png)
            results.append({"name": c.name, "mpn": part["mpn"], "category": c.category,
                            "footprint": str(footprint_file(c).relative_to(ROOT)), **result})
            print(f"    -> {result['status']} {result['detail']}")
    finally:
        if downloader is not None:
            downloader.close()

    print("\nRiepilogo:")
    for r in results:
        print(f"  {r['status']:8} {r['name']:30} {r.get('source', ''):14} {r['detail']}")
    if any(r["status"] == "ADDED" for r in results):
        print(f"\nControlla il pin 1 nei render (180 gradi non si rilevano in automatico): {render_dir}")
    if args.report:
        Path(args.report).write_text(json.dumps({
            "date": datetime.now().isoformat(timespec="seconds"),
            "components": results,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Report: {args.report}")
    return 0 if all(r["status"] == "ADDED" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
