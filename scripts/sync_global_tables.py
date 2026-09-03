#!/usr/bin/env python3
"""Sync this library's MIKILAB_* entries into KiCad's global lib tables.

Regenerates sym-lib-table.global / fp-lib-table.global from the local
sym-lib-table / fp-lib-table, then merges any MIKILAB_* entries missing
from KiCad's global tables (~/Library/Preferences/kicad/10.0/), leaving
every other entry untouched. Backs up both global tables before writing.

KiCad must be fully closed (all windows, all sub-apps like eeschema /
pcbnew) before running this -- otherwise KiCad will overwrite the merged
tables with its in-memory copy when it exits.
"""
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

LIB_ROOT = Path(__file__).resolve().parent.parent
KICAD_DIR = Path.home() / "Library/Preferences/kicad/10.0"

TABLES = (
    ("sym-lib-table", "sym-lib-table.global"),
    ("fp-lib-table", "fp-lib-table.global"),
)


def running_kicad_processes():
    out = subprocess.run(["pgrep", "-fl", "KiCad|eeschema|pcbnew"],
                          capture_output=True, text=True)
    lines = [l for l in out.stdout.splitlines() if l.strip()]
    return lines


def main():
    procs = running_kicad_processes()
    if procs:
        print("KiCad (or a sub-app) is still running -- close it fully first:")
        for l in procs:
            print(" ", l)
        sys.exit(1)

    subprocess.run([sys.executable, str(LIB_ROOT / "scripts" / "generate_global_tables.py")], check=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    any_merged = False

    for global_name, generated_name in TABLES:
        target = KICAD_DIR / global_name
        if not target.exists():
            print(f"skip: {target} does not exist")
            continue

        target_text = target.read_text()
        existing_names = set(re.findall(r'\(lib \(name "([^"]+)"', target_text))

        generated_lines = (LIB_ROOT / generated_name).read_text().splitlines()
        new_libs = []
        for line in generated_lines:
            line = line.strip()
            if not line.startswith("(lib"):
                continue
            m = re.search(r'\(lib \(name "([^"]+)"', line)
            if m and m.group(1) not in existing_names:
                new_libs.append(line)

        if not new_libs:
            print(f"{target}: already up to date")
            continue

        backup = target.with_name(f"{global_name}.bak.{stamp}")
        backup.write_text(target_text)

        idx = target_text.rstrip().rfind(")")
        merged = (target_text.rstrip()[:idx] + "\n  " + "\n  ".join(new_libs)
                  + "\n" + target_text.rstrip()[idx:] + "\n")
        target.write_text(merged)
        any_merged = True
        print(f"Merged {len(new_libs)} new librar{'y' if len(new_libs)==1 else 'ies'} into {target} (backup: {backup.name})")
        for l in new_libs:
            m = re.search(r'\(lib \(name "([^"]+)"', l)
            print(f"    + {m.group(1)}")

    if not any_merged:
        print("Nothing to sync -- global tables already match the library.")
    else:
        print("Done. Start KiCad to pick up the new libraries.")


if __name__ == "__main__":
    main()
