#!/usr/bin/env python3
"""
import_component.py
====================

Import a single component (symbol + optional footprint + optional 3D model)
into the MIKILAB KiCad library.

Usage:
    python3 scripts/import_component.py \\
        --name TPS7A2018PDBVR \\
        --symbol /path/to/TPS7A2018PDBVR.kicad_sym \\
        --footprint /path/to/SOT95P280X145-5N.kicad_mod \\
        --model /path/to/TPS7A2018PDBVR.step \\
        --category power

A component may be imported with just a symbol, symbol+footprint, or
symbol+footprint+3D model. --category is optional; if omitted it is
inferred from --name using the same classification rules used elsewhere
in this library.

To remove a component instead:
    python3 scripts/import_component.py --name TPS7A2018PDBVR --remove

This deletes its symbol, its footprint library (if any) and its 3D
model(s) (if any), logs the removal to MANIFEST.csv, and regenerates
sym-lib-table/fp-lib-table. Only --name is required; every other option
is ignored in --remove mode.

Never overwrites an existing component. Never silently duplicates a
footprint that already exists byte-for-byte elsewhere in the library --
it is reused instead. A footprint with a colliding filename but different
content is given a distinct, semantically-derived name and the collision
is documented in the report and in MANIFEST.csv.

After a successful import, sym-lib-table and fp-lib-table are fully
regenerated from the contents of the library directory tree, which
guarantees there is never more than one "(version 7)" entry and that
every library on disk is registered exactly once.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_common as lc

ROOT = lc.LIBRARY_ROOT


class ImportError_(Exception):
    pass


def validate_args(args) -> None:
    if not args.name.strip():
        raise ImportError_("--name must not be empty")

    for label, value in (("--symbol", args.symbol), ("--footprint", args.footprint), ("--model", args.model)):
        if value is not None and not Path(value).expanduser().is_file():
            raise ImportError_(f"{label} does not exist or is not a file: {value}")

    if args.model and not args.footprint:
        raise ImportError_("--model requires --footprint (a 3D model needs a footprint to attach to)")

    if args.category and args.category not in lc.CATEGORIES:
        raise ImportError_(
            f"--category '{args.category}' is not one of the known categories: "
            + ", ".join(lc.CATEGORIES)
        )


def import_symbol(name: str, category: str, src: Path, manifest_rows: list, report: list, update: bool = False) -> Path:
    bad_name = lc.check_component_name(name)
    if bad_name:
        raise ImportError_(bad_name)

    existing = lc.find_symbol_by_name(ROOT, name)
    if existing is not None:
        if not update:
            raise ImportError_(
                f"A component named '{name}' already exists: {existing.relative_to(ROOT)}. "
                f"Refusing to overwrite -- choose a different --name, pass --update to "
                f"replace it in place, or remove the existing component first."
            )

        digest = lc.sha256_file(src)
        shutil.copy2(src, existing)
        manifest_rows.append(["symbol", str(src), str(existing.relative_to(ROOT)), "UPDATED", digest, f"category={category}"])
        report.append(f"  SYMBOL   UPDATED    {existing.relative_to(ROOT)}")
        return existing

    dst = ROOT / "symbols" / category / f"{name}.kicad_sym"
    dst.parent.mkdir(parents=True, exist_ok=True)

    digest = lc.sha256_file(src)
    shutil.copy2(src, dst)

    manifest_rows.append(["symbol", str(src), str(dst.relative_to(ROOT)), "NEW", digest, f"category={category}"])
    report.append(f"  SYMBOL   NEW        {dst.relative_to(ROOT)}")
    return dst


def import_footprint(name: str, category: str, src: Path, manifest_rows: list, report: list, update: bool = False):
    """Returns (footprint_path_or_None_if_reused, fp_nickname, fp_name, status)."""
    bad_name = lc.check_component_name(name)
    if bad_name:
        raise ImportError_(bad_name)

    digest = lc.sha256_file(src)
    pretty_dir = ROOT / "footprints" / category / f"{name}.pretty"
    basename = src.name

    if update and pretty_dir.is_dir():
        stale = [p for p in pretty_dir.glob("*.kicad_mod") if p.name != basename]
        for p in stale:
            p.unlink()
            report.append(f"  FOOTPRINT REMOVED    stale variant {p.relative_to(ROOT)}")

        target = pretty_dir / basename
        pretty_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        manifest_rows.append(["footprint", str(src), str(target.relative_to(ROOT)), "UPDATED", digest, f"category={category}"])
        report.append(f"  FOOTPRINT UPDATED    {target.relative_to(ROOT)}")
        return target, lc.fp_nickname(pretty_dir), target.stem, "UPDATED"

    existing = lc.find_footprint_by_hash(ROOT, digest)
    if existing is not None:
        pretty_dir = existing.parent
        nickname = lc.fp_nickname(pretty_dir)
        manifest_rows.append([
            "footprint", str(src), str(existing.relative_to(ROOT)), "DUPLICATE", digest,
            f"reused existing identical footprint instead of duplicating; category={category}",
        ])
        report.append(f"  FOOTPRINT DUPLICATE  reused {existing.relative_to(ROOT)}")
        return None, nickname, existing.stem, "DUPLICATE"

    colliding = lc.find_footprints_by_basename(ROOT, basename)
    if colliding:
        new_basename = f"{name}_{src.stem}{src.suffix}"
        note = (
            f"filename collision with {', '.join(str(p.relative_to(ROOT)) for p in colliding)} "
            f"(different content, verified by SHA256) -> renamed to '{new_basename}'"
        )
        target = pretty_dir / new_basename
        status = "RENAMED_COLLISION"
    else:
        target = pretty_dir / basename
        status = "NEW"
        note = f"category={category}"

    pretty_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, target)

    manifest_rows.append(["footprint", str(src), str(target.relative_to(ROOT)), status, digest, note])
    report.append(f"  FOOTPRINT {status:11} {target.relative_to(ROOT)}")

    nickname = lc.fp_nickname(pretty_dir)
    return target, nickname, target.stem, status


def remove_component(name: str, manifest_rows: list, report: list) -> None:
    """Remove a component's symbol, its footprint library, and its 3D
    model(s). Category is taken from where the symbol currently lives, so
    the caller only needs --name."""
    sym_path = lc.find_symbol_by_name(ROOT, name)
    if sym_path is None:
        raise ImportError_(f"No component named '{name}' found under symbols/ -- nothing to remove.")

    category = sym_path.parent.name

    digest = lc.sha256_file(sym_path)
    sym_path.unlink()
    manifest_rows.append(["symbol", str(sym_path.relative_to(ROOT)), "", "REMOVED", digest, f"category={category}"])
    report.append(f"  SYMBOL   REMOVED    {sym_path.relative_to(ROOT)}")

    pretty_dir = ROOT / "footprints" / category / f"{name}.pretty"
    if pretty_dir.is_dir():
        for mod in sorted(pretty_dir.glob("*.kicad_mod")):
            digest = lc.sha256_file(mod)
            manifest_rows.append(["footprint", str(mod.relative_to(ROOT)), "", "REMOVED", digest, f"category={category}"])
            report.append(f"  FOOTPRINT REMOVED    {mod.relative_to(ROOT)}")
        shutil.rmtree(pretty_dir)
        report.append(f"  Removed directory {pretty_dir.relative_to(ROOT)}")

    models_dir = ROOT / "3dmodels" / category
    if models_dir.is_dir():
        for model in sorted(models_dir.glob(f"{name}.*")):
            if model.suffix.lower() not in lc.MODEL_EXTENSIONS:
                continue
            digest = lc.sha256_file(model)
            model.unlink()
            manifest_rows.append(["3d-model", str(model.relative_to(ROOT)), "", "REMOVED", digest, f"category={category}"])
            report.append(f"  3D MODEL  REMOVED    {model.relative_to(ROOT)}")


def import_model(name: str, category: str, src: Path, footprint_path: Path | None, manifest_rows: list, report: list):
    dst = ROOT / "3dmodels" / category / f"{name}{src.suffix.lower()}"
    dst.parent.mkdir(parents=True, exist_ok=True)

    digest = lc.sha256_file(src)
    final, status = lc.unique_destination(dst, digest)

    if status != "DUPLICATE":
        shutil.copy2(src, final)

    manifest_rows.append(["3d-model", str(src), str(final.relative_to(ROOT)), status, digest, f"category={category}"])
    report.append(f"  3D MODEL  {status:11} {final.relative_to(ROOT)}")

    if footprint_path is None:
        report.append(
            "  NOTE: footprint was reused from an existing shared library; the 3D model "
            "was copied but NOT embedded in that footprint (it may already be used by "
            "another component with a different 3D body -- assign it manually per-instance "
            "in the PCB editor if needed)."
        )
        return

    text = footprint_path.read_text(encoding="utf-8")
    model_uri = "${MIKILAB}/" + str(final.relative_to(ROOT))

    if "(model " in text:
        report.append(
            f"  NOTE: {footprint_path.relative_to(ROOT)} already has a 3D model reference; "
            f"leaving it untouched. New model is available at {final.relative_to(ROOT)}."
        )
        return

    block = (
        f"  (model {model_uri}\n"
        f"    (offset (xyz 0 0 0))\n"
        f"    (scale (xyz 1 1 1))\n"
        f"    (rotate (xyz 0 0 0))\n"
        f"  )\n"
    )
    assert text.rstrip().endswith(")")
    idx = text.rstrip().rfind(")")
    text = text.rstrip()[:idx] + block + ")\n"
    footprint_path.write_text(text, encoding="utf-8")
    report.append(f"  Linked 3D model into {footprint_path.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a single component into the MIKILAB library.")
    parser.add_argument("--name", required=True, help="Component name (used as the symbol/footprint base name)")
    parser.add_argument("--category", choices=lc.CATEGORIES, help="MIKILAB category (auto-detected from --name if omitted)")
    parser.add_argument("--symbol", help="Path to the source .kicad_sym file (required unless --remove)")
    parser.add_argument("--footprint", help="Path to the source .kicad_mod file")
    parser.add_argument("--model", help="Path to the source 3D model (.step/.stp/.wrl/.wrz)")
    parser.add_argument("--update", action="store_true", help="Replace an existing component's symbol/footprint in place instead of refusing")
    parser.add_argument("--remove", action="store_true", help="Remove an existing component (symbol + its footprint library + its 3D model(s)) and regenerate the lib-tables; only --name is required, every other option is ignored")
    args = parser.parse_args()

    name = args.name.strip()
    if not name:
        print("ERROR: --name must not be empty")
        return 1

    if args.remove:
        manifest_rows = []
        report = [f"Removing '{name}'"]
        try:
            remove_component(name, manifest_rows, report)
        except ImportError_ as e:
            print(f"ERROR: {e}")
            return 1

        lc.append_manifest_rows(ROOT, manifest_rows)
        sym_entries = lc.write_sym_lib_table(ROOT)
        fp_entries = lc.write_fp_lib_table(ROOT)
        lc.write_global_tables(ROOT)
        report.append(f"Regenerated sym-lib-table ({len(sym_entries)} libraries) and fp-lib-table ({len(fp_entries)} libraries)")
        report.append("Regenerated sym-lib-table.global and fp-lib-table.global")
        report.append(
            "NOTE: if this library is registered globally in KiCad (README.md section 1), "
            "also re-run the merge step to update ~/Library/Preferences/kicad/*/sym-lib-table "
            "and fp-lib-table, or the removed component will still resolve there."
        )

        print("\n".join(report))
        print("\nOK.")
        return 0

    if not args.symbol:
        print("ERROR: --symbol is required unless --remove is given")
        return 1

    try:
        validate_args(args)
    except ImportError_ as e:
        print(f"ERROR: {e}")
        return 1

    category = args.category or lc.classify(name)
    manifest_rows: list[list[str]] = []
    report: list[str] = [f"Importing '{name}' into category '{category}'"]

    try:
        import_symbol(name, category, Path(args.symbol).expanduser().resolve(), manifest_rows, report, update=args.update)
    except ImportError_ as e:
        print(f"ERROR: {e}")
        lc.append_manifest_rows(ROOT, [["symbol", args.symbol, "", "ERROR", "", str(e)]])
        return 1

    fp_path = None
    if args.footprint:
        fp_path, fp_nick, fp_name, _ = import_footprint(
            name, category, Path(args.footprint).expanduser().resolve(), manifest_rows, report, update=args.update
        )

        if args.model:
            model_target = fp_path if fp_path is not None else None
            import_model(name, category, Path(args.model).expanduser().resolve(), model_target, manifest_rows, report)

        sym_path = ROOT / "symbols" / category / f"{name}.kicad_sym"
        text = sym_path.read_text(encoding="utf-8")
        new_ref = f"{fp_nick}:{fp_name}"
        new_text, changed = lc.set_symbol_footprint_property(text, new_ref)
        if changed:
            sym_path.write_text(new_text, encoding="utf-8")
            report.append(f"  Linked symbol Footprint property -> {new_ref}")
        else:
            report.append(
                f"  NOTE: could not find a 'Footprint' property in {sym_path.relative_to(ROOT)} "
                f"to update automatically -- set it manually to '{new_ref}'"
            )

    lc.append_manifest_rows(ROOT, manifest_rows)

    sym_entries = lc.write_sym_lib_table(ROOT)
    fp_entries = lc.write_fp_lib_table(ROOT)
    lc.write_global_tables(ROOT)
    report.append(f"Regenerated sym-lib-table ({len(sym_entries)} libraries) and fp-lib-table ({len(fp_entries)} libraries)")
    report.append("Regenerated sym-lib-table.global and fp-lib-table.global")
    report.append(
        "NOTE: if this library is registered globally in KiCad (README.md section 1), "
        "also re-run the merge step to update ~/Library/Preferences/kicad/*/sym-lib-table "
        "and fp-lib-table, or the new component will not show up there."
    )

    print("\n".join(report))
    print("\nOK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
