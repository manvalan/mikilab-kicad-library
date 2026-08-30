#!/usr/bin/env python3
"""
find_missing_3d_models.py
===========================

Reports every single-part MIKILAB component that has a footprint but no
3D model, and prints ready-to-open search links for each one (SnapEDA,
Octopart, UltraLibrarian) to speed up manually finding and importing the
missing model via import_component.py --update (or mikilab_lib.update_component()).

This does not download or scrape anything -- SnapEDA/UltraLibrarian
require a logged-in browser session to download a model, and Octopart's
search UI sits behind a bot-detection challenge, so there is no reliable
unattended way to fetch these. This script only narrows down *which*
components need attention and *where* to look.

Bulk, multi-package category libraries (e.g. symbols/other/ti.kicad_sym,
which pairs with a footprints/other/ti.pretty containing many unrelated
footprints for many different chips) are excluded -- a single 3D model
doesn't apply to a whole such library, only to one real part.

Usage:
    python3 scripts/find_missing_3d_models.py [--category CAT] [--csv OUTPUT.csv]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mikilab_lib as mikilab


def missing_3d_models(category: str | None = None) -> list[mikilab.ComponentInfo]:
    """Single-part components (exactly one footprint file in their
    .pretty dir) that have a footprint but no 3D model."""
    return [
        c for c in mikilab.list_components(category=category)
        if c.footprint_path and not c.model_paths and len(c.footprint_files) == 1
    ]


def search_links(query: str) -> dict[str, str]:
    q = quote(query)
    return {
        "snapeda": f"https://www.snapeda.com/search/?q={q}&search-type=parts",
        "octopart": f"https://octopart.com/search?q={q}",
        "ultralibrarian": "https://app.ultralibrarian.com/search",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", choices=mikilab.lc.CATEGORIES)
    parser.add_argument("--csv", help="Also write the report to this CSV file")
    args = parser.parse_args()

    missing = sorted(missing_3d_models(args.category), key=lambda c: (c.category, c.name))

    if not missing:
        print("Nessun componente singolo senza modello 3D.")
        return 0

    print(f"{len(missing)} componenti senza modello 3D:\n")

    rows = []
    for c in missing:
        links = search_links(c.name)
        print(f"{c.category:15} {c.name}")
        print(f"    footprint     : {c.footprint_path}/{c.footprint_files[0]}.kicad_mod")
        print(f"    SnapEDA       : {links['snapeda']}")
        print(f"    Octopart      : {links['octopart']}")
        print(f"    UltraLibrarian: {links['ultralibrarian']}  (cerca a mano \"{c.name}\")")
        print()
        rows.append({
            "name": c.name,
            "category": c.category,
            "footprint": f"{c.footprint_path}/{c.footprint_files[0]}.kicad_mod",
            "snapeda_search": links["snapeda"],
            "octopart_search": links["octopart"],
            "ultralibrarian_search": links["ultralibrarian"],
        })

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"Scritto anche {args.csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
