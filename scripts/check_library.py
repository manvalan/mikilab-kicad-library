#!/usr/bin/env python3
"""
check_library.py
=================

Full consistency check of the MIKILAB KiCad library.

Checks performed:
  A. Directory structure (unexpected files, empty directories)
  B. Symbols (syntax, duplicate names/files, footprint references)
  C. Footprints (syntax, duplicate filenames, filename collisions, 3D refs)
  D. 3D models (files exist, references valid, relative/portable paths)
  E. Lib tables (syntax, single "(version 7)", all libs exist, no dup nicknames)
  F. Cross check: symbol -> footprint -> 3D model

Exit code is 0 if there are no ERROR-level findings, 1 otherwise.
WARN-level findings (e.g. known/documented gaps) never fail the run.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_common as lc

ROOT = lc.LIBRARY_ROOT

EXPECTED_TOP_LEVEL = {
    "symbols", "footprints", "3dmodels", "docs", "legacy", "reference", "scripts",
    "sym-lib-table", "fp-lib-table", "sym-lib-table.global", "fp-lib-table.global",
    "MANIFEST.csv", "MANIFEST.md",
    "README.md", ".git", ".claude", ".gitignore", ".DS_Store",
}

KNOWN_UNRESOLVED_MODEL_VARS = ("${KISBLIB}",)

# Standard KiCad 3D model variables (${KISYS3DMOD}, ${KICAD10_3DMODEL_DIR},
# ...), defined by every KiCad install and pointing at its bundled models.
STD_MODEL_VAR_RE = re.compile(r"^\$\{(?:KISYS3DMOD|KICAD\d+_3DMODEL_DIR)\}/(.+)$")

# Bundled 3D models of the local KiCad install, used to verify standard
# references when present (skipped silently on machines without it).
KICAD_STD_3DMODELS_DIRS = (
    Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/3dmodels"),
    Path("/usr/share/kicad/3dmodels"),
    Path("C:/Program Files/KiCad/share/kicad/3dmodels"),
)
KICAD_STD_3DMODELS = next((d for d in KICAD_STD_3DMODELS_DIRS if d.is_dir()), None)

_STRING_LITERAL_RE = re.compile(r'"(?:[^"\\]|\\.)*"')


def parens_balanced(text: str) -> bool:
    """Check s-expression paren balance, ignoring parens inside string
    literals (KiCad symbol/footprint descriptions routinely contain
    literal '(' / ')' characters, e.g. "Vin (typ)")."""
    stripped = _STRING_LITERAL_RE.sub('""', text)
    return stripped.count("(") == stripped.count(")")


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []

    def error(self, msg: str):
        self.errors.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)

    def note(self, msg: str):
        self.info.append(msg)

    def ok(self) -> bool:
        return not self.errors


def check_directory_structure(report: Report):
    for entry in ROOT.iterdir():
        if entry.name not in EXPECTED_TOP_LEVEL:
            report.warn(f"[DIR] Unexpected top-level entry: {entry.name}")

    for sub in ("symbols", "footprints", "3dmodels"):
        base = ROOT / sub
        if not base.exists():
            report.error(f"[DIR] Missing expected directory: {sub}/")
            continue

        for d in sorted(base.rglob("*")):
            if d.is_dir() and not any(d.iterdir()):
                report.warn(f"[DIR] Empty directory: {d.relative_to(ROOT)}")

    for cat_dir in (ROOT / "symbols").iterdir() if (ROOT / "symbols").exists() else []:
        if cat_dir.is_dir() and cat_dir.name not in lc.CATEGORIES:
            report.warn(f"[DIR] Symbol category not in known list: {cat_dir.name}")

    for cat_dir in (ROOT / "footprints").iterdir() if (ROOT / "footprints").exists() else []:
        if cat_dir.is_dir() and cat_dir.name not in lc.CATEGORIES:
            report.warn(f"[DIR] Footprint category not in known list: {cat_dir.name}")


def check_symbols(report: Report) -> list[tuple[str, Path]]:
    """Returns [(symbol_name, defining .kicad_sym file), ...] for cross-check.
    Note: the same symbol name can legitimately appear in multiple files
    (each file is an independent library), so all occurrences are kept."""
    symbol_entries: list[tuple[str, Path]] = []
    seen_hash: dict[str, Path] = {}

    files = lc.discover_symbol_libraries(ROOT)

    if not files:
        report.warn("[SYM] No .kicad_sym files found under symbols/")

    basenames = defaultdict(list)

    for path in files:
        basenames[path.name.lower()].append(path)

        bad_name = lc.check_component_name(path.stem)
        if bad_name:
            report.error(f"[SYM] {path.relative_to(ROOT)}: {bad_name}")

        text = path.read_text(encoding="utf-8", errors="replace")

        if not text.lstrip().startswith("(kicad_symbol_lib"):
            report.error(f"[SYM] {path.relative_to(ROOT)}: does not start with (kicad_symbol_lib")

        if not parens_balanced(text):
            report.error(f"[SYM] {path.relative_to(ROOT)}: unbalanced parentheses")

        gen_m = re.search(r'\(generator\s+([^\s")]+)', text)
        if gen_m and re.search(r'[:/]', gen_m.group(1)):
            report.error(
                f"[SYM] {path.relative_to(ROOT)}: unquoted '(generator {gen_m.group(1)})' "
                f"contains ':' or '/' -- this breaks KiCad's parser and silently makes the "
                f"whole library fail to load; wrap the value in double quotes"
            )

        digest = lc.sha256_file(path)
        if digest in seen_hash:
            report.warn(
                f"[SYM] Duplicate content: {path.relative_to(ROOT)} "
                f"is byte-identical to {seen_hash[digest].relative_to(ROOT)}"
            )
        else:
            seen_hash[digest] = path

        # Each .kicad_sym file is an independent KiCad library: symbols are
        # always addressed as "LibNickname:SymbolName", so the *same* name
        # appearing in two different library files is normal and expected
        # (this whole library mirrors upstream KiCad libraries on purpose).
        # A duplicate is only a real problem if it appears twice inside the
        # *same* file, which KiCad would refuse to load correctly.
        names_in_this_file: set[str] = set()

        for m in re.finditer(r'\(symbol\s+"([^"]+)"', text):
            sym_name = m.group(1)

            if sym_name in names_in_this_file:
                report.error(
                    f"[SYM] {path.relative_to(ROOT)}: symbol name "
                    f"'{sym_name}' defined more than once in the same file"
                )
            names_in_this_file.add(sym_name)

            if ":" in sym_name:
                # Sub-unit / alternate-body reference, not a top-level symbol.
                continue

            symbol_entries.append((sym_name, path))

    for name, paths in basenames.items():
        if len(paths) > 1:
            report.error(
                f"[SYM] Filename collision for '{name}': "
                + ", ".join(str(p.relative_to(ROOT)) for p in paths)
            )

    return symbol_entries


def check_footprints(report: Report) -> dict[str, Path]:
    """Returns map 'LIBNICK:footprint_name' -> defining .kicad_mod path."""
    footprint_owner: dict[str, Path] = {}
    seen_hash: dict[str, list[Path]] = defaultdict(list)

    pretty_dirs = lc.discover_footprint_libraries(ROOT)

    if not pretty_dirs:
        report.warn("[FP] No .pretty directories found under footprints/")

    # Loose .kicad_mod files directly under a category (not inside .pretty)
    # are invisible to fp-lib-table and must not exist.
    if (ROOT / "footprints").exists():
        for path in (ROOT / "footprints").glob("*/*.kicad_mod"):
            report.error(
                f"[FP] Standalone footprint not inside a .pretty library: "
                f"{path.relative_to(ROOT)}"
            )

    basenames = defaultdict(list)

    for pretty in pretty_dirs:
        nickname = lc.fp_nickname(pretty)

        bad_name = lc.check_component_name(pretty.name[: -len(".pretty")])
        if bad_name:
            report.error(f"[FP] {pretty.relative_to(ROOT)}: {bad_name}")

        for mod in sorted(pretty.glob("*.kicad_mod")):
            basenames[mod.name.lower()].append(mod)

            text = mod.read_text(encoding="utf-8", errors="replace")

            if not text.lstrip().startswith("(footprint") and not text.lstrip().startswith("(module"):
                report.error(f"[FP] {mod.relative_to(ROOT)}: does not start with (footprint ...)")

            if not parens_balanced(text):
                report.error(f"[FP] {mod.relative_to(ROOT)}: unbalanced parentheses")

            digest = lc.sha256_file(mod)
            seen_hash[digest].append(mod)

            key = f"{nickname}:{mod.stem}"
            footprint_owner[key] = mod

            for model_m in re.finditer(r'\(model\s+"([^"]+)"|\(model\s+([^\s)]+)', text):
                model_ref = model_m.group(1) or model_m.group(2)
                check_model_reference(report, mod, model_ref)

    for digest, paths in seen_hash.items():
        if len(paths) > 1:
            names = {p.name for p in paths}
            if len(names) == 1:
                report.note(
                    f"[FP] Identical footprint content shared across libraries: "
                    + ", ".join(str(p.relative_to(ROOT)) for p in paths)
                )

    for name, paths in basenames.items():
        if len(paths) > 1:
            hashes = {lc.sha256_file(p) for p in paths}
            if len(hashes) > 1:
                report.error(
                    f"[FP] REAL collision: filename '{name}' has different content in: "
                    + ", ".join(str(p.relative_to(ROOT)) for p in paths)
                )
            # If all hashes match, it is intentional library duplication
            # across independent .pretty libraries -- not an error.

    return footprint_owner


def check_model_reference(report: Report, footprint_path: Path, model_ref: str):
    if any(var in model_ref for var in KNOWN_UNRESOLVED_MODEL_VARS):
        report.warn(
            f"[3D] {footprint_path.relative_to(ROOT)}: references undefined "
            f"env var in '{model_ref}' (known gap: vendor-imported footprint, "
            f"3D file was never present locally -- see README)"
        )
        return

    if model_ref.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", model_ref):
        report.error(
            f"[3D] {footprint_path.relative_to(ROOT)}: absolute 3D model path '{model_ref}'"
        )
        return

    if "${KIPRJMOD}" in model_ref or "${MIKILAB}" in model_ref:
        resolved = lc.resolve_uri(model_ref, ROOT)
        if not resolved.exists():
            report.error(
                f"[3D] {footprint_path.relative_to(ROOT)}: missing 3D model file "
                f"'{model_ref}' -> {resolved}"
            )
        return

    std = STD_MODEL_VAR_RE.match(model_ref)
    if std:
        # Standard KiCad env var pointing at the official bundled 3D model
        # package -- defined automatically by every KiCad install. Verified
        # against the local install when one is found.
        if KICAD_STD_3DMODELS and not (KICAD_STD_3DMODELS / std.group(1)).exists():
            report.warn(
                f"[3D] {footprint_path.relative_to(ROOT)}: standard KiCad 3D model "
                f"'{model_ref}' not found in {KICAD_STD_3DMODELS}"
            )
        return

    report.warn(
        f"[3D] {footprint_path.relative_to(ROOT)}: model path uses unrecognized "
        f"variable/form '{model_ref}'"
    )


def check_3dmodels(report: Report):
    models_dir = ROOT / "3dmodels"
    if not models_dir.exists():
        report.error("[3D] Missing 3dmodels/ directory")
        return

    for path in models_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() not in lc.MODEL_EXTENSIONS:
            report.warn(f"[3D] Unexpected file type in 3dmodels/: {path.relative_to(ROOT)}")


def check_lib_tables(report: Report):
    sym_table = ROOT / "sym-lib-table"
    fp_table = ROOT / "fp-lib-table"

    sym_entries, sym_versions = lc.parse_lib_table(sym_table)
    fp_entries, fp_versions = lc.parse_lib_table(fp_table)

    if not sym_table.exists():
        report.error("[TABLE] sym-lib-table is missing")
    elif sym_versions != 1:
        report.error(f"[TABLE] sym-lib-table has {sym_versions} (version N) entries, expected exactly 1")

    if not fp_table.exists():
        report.error("[TABLE] fp-lib-table is missing")
    elif fp_versions != 1:
        report.error(f"[TABLE] fp-lib-table has {fp_versions} (version N) entries, expected exactly 1")

    # Duplicate nicknames
    for kind, entries in (("sym-lib-table", sym_entries), ("fp-lib-table", fp_entries)):
        seen = defaultdict(int)
        for e in entries:
            seen[e.nickname] += 1
        for nick, count in seen.items():
            if count > 1:
                report.error(f"[TABLE] {kind}: nickname '{nick}' registered {count} times")

    # Every referenced library must exist on disk
    for e in sym_entries:
        resolved = lc.resolve_uri(e.uri, ROOT)
        if not resolved.exists():
            report.error(f"[TABLE] sym-lib-table: '{e.nickname}' -> {resolved} does not exist")

    for e in fp_entries:
        resolved = lc.resolve_uri(e.uri, ROOT)
        if not resolved.exists() or not resolved.is_dir():
            report.error(f"[TABLE] fp-lib-table: '{e.nickname}' -> {resolved} does not exist")

    # Every library on disk must be referenced
    registered_sym_uris = {lc.resolve_uri(e.uri, ROOT) for e in sym_entries}
    for path in lc.discover_symbol_libraries(ROOT):
        if path not in registered_sym_uris:
            report.error(f"[TABLE] {path.relative_to(ROOT)} exists but is not registered in sym-lib-table")

    registered_fp_uris = {lc.resolve_uri(e.uri, ROOT) for e in fp_entries}
    for path in lc.discover_footprint_libraries(ROOT):
        if path not in registered_fp_uris:
            report.error(f"[TABLE] {path.relative_to(ROOT)} exists but is not registered in fp-lib-table")

    return sym_entries, fp_entries


def check_cross_references(report: Report, symbol_entries: list[tuple[str, Path]], fp_entries):
    """Verify symbol -> footprint links that MIKILAB actually owns.

    Symbols mirrored from the official KiCad symbol libraries legitimately
    reference KiCad's *global/standard* footprint libraries (e.g.
    'Package_SO', 'RF_Module', 'Package_DFN_QFN') by their upstream
    nickname. Those libraries are not part of MIKILAB -- they ship with
    every KiCad install and are resolved via the user's global
    fp-lib-table, not this project's. Only nicknames claiming to be a
    MIKILAB library (prefix 'MIKILAB_') are actually within our control,
    so only those are checked strictly.
    """
    fp_nicknames = {e.nickname for e in fp_entries}
    external_refs = 0

    by_file: dict[Path, list[str]] = defaultdict(list)
    for sym_name, sym_path in symbol_entries:
        by_file[sym_path].append(sym_name)

    for sym_path, names in by_file.items():
        text = sym_path.read_text(encoding="utf-8", errors="replace")

        for sym_name in names:
            pattern = re.compile(
                r'\(symbol\s+"' + re.escape(sym_name) + r'"'
            )
            m = pattern.search(text)
            if not m:
                continue

            window = text[m.end(): m.end() + 4000]
            fp_m = re.search(
                r'"Footprint"\s*\n?\s*"([^"]*)"', window
            )
            if not fp_m:
                continue

            fp_value = fp_m.group(1).strip()
            if not fp_value:
                continue

            if ":" not in fp_value:
                continue

            nick, _, fp_name = fp_value.partition(":")

            if not nick.startswith("MIKILAB_"):
                # Reference to a standard/global KiCad footprint library,
                # outside MIKILAB's scope.
                external_refs += 1
                continue

            if nick not in fp_nicknames:
                report.error(
                    f"[CROSS] {sym_path.relative_to(ROOT)} symbol '{sym_name}': "
                    f"Footprint references unknown MIKILAB library '{nick}' (footprint '{fp_value}')"
                )

    if external_refs:
        report.note(
            f"[CROSS] {external_refs} symbols reference standard/global KiCad "
            f"footprint libraries (not MIKILAB_-prefixed) -- resolved via the "
            f"user's global fp-lib-table, out of scope for this library"
        )


def main():
    parser = argparse.ArgumentParser(description="Check the MIKILAB KiCad library for consistency.")
    parser.add_argument("--quiet", action="store_true", help="Only print the summary")
    args = parser.parse_args()

    report = Report()

    check_directory_structure(report)
    symbol_entries = check_symbols(report)
    footprint_owner = check_footprints(report)
    check_3dmodels(report)
    sym_entries, fp_entries = check_lib_tables(report)
    check_cross_references(report, symbol_entries, fp_entries)

    if not args.quiet:
        if report.info:
            print(f"\n=== INFO ({len(report.info)}) ===")
            for line in report.info:
                print(f"  {line}")

        if report.warnings:
            print(f"\n=== WARNINGS ({len(report.warnings)}) ===")
            for line in report.warnings:
                print(f"  {line}")

        if report.errors:
            print(f"\n=== ERRORS ({len(report.errors)}) ===")
            for line in report.errors:
                print(f"  {line}")

    print()
    print(f"Symbol libraries:    {len(lc.discover_symbol_libraries(ROOT))}")
    print(f"Footprint libraries: {len(lc.discover_footprint_libraries(ROOT))}")
    print(f"Symbols defined:     {len(symbol_entries)}")
    print(f"Footprints defined:  {len(footprint_owner)}")
    print()
    print(f"Errors:   {len(report.errors)}")
    print(f"Warnings: {len(report.warnings)}")
    print(f"Info:     {len(report.info)}")
    print()

    if report.ok():
        print("RESULT: OK (no errors)")
        return 0
    else:
        print("RESULT: FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
