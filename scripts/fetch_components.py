#!/usr/bin/env python3
"""
fetch_components.py
===================

Download a JSON list of components from SnapEDA, Ultra Librarian and
JLCPCB/LCSC (EasyEDA), add them to the MIKILAB library and validate the
library.

Usage:
    python3 scripts/fetch_components.py --list test/componenti-librerie-cad.json [--dry-run]
        [--credentials credentials.json] [--only U3,J1]
        [--sources snapeda,ultralibrarian,easyeda] [--no-download] [--update]
        [--allow-pin-mismatch] [--report out.json]

List format (see test/componenti-librerie-cad.json):
    {"componenti": [{"ref": "U5", "mpn": "RTL8201F-VB-CG", "funzione": "...",
                     "produttore": "...", "package": "...", "lcsc": "C45044",
                     "snapeda": "<url>", "ultralibrarian": "<url>",
                     # optional overrides:
                     "nome": "<component name>", "categoria": "<category>",
                     "salta": true}, ...]}
Entries sharing the same MPN are processed once.

For every component, sources are tried in order (credentials.json
"source_order" or --sources) until one yields a usable part:
  1. a zip already in download_dir is reused (<download_dir>/<source>/<NAME>.zip,
     or any zip in download_dir whose file name contains the MPN -- so
     zips downloaded by hand are picked up too);
  2. otherwise it is downloaded through the browser (eda_download.py),
     or, without Playwright, the search page is opened and the zip is
     waited for in download_dir;
     For "easyeda" there is no zip: the list's "lcsc" code is converted
     with easyeda2kicad (import_easyeda.py; no account, no browser);
  3. the zip is imported with import_snapeda / import_ultralibrarian.
     A part is accepted only if it has BOTH symbol and footprint and
     every symbol pin number has a matching footprint pad; otherwise
     nothing is copied and the next source is tried.

Components already in the library (as a symbol file or as a symbol name
inside any library) are skipped unless --update is given.

At the end the lib tables (project and .global) are regenerated,
check_library.py is run, and a JSON report is written next to the list
(<list>.report.json) unless --report says otherwise.

Exit code: 0 if every component was imported or already present and the
library check passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_common as lc
import import_component as ic
import import_snapeda
import import_ultralibrarian
import import_easyeda
import eda_download
import model_check

ROOT = lc.LIBRARY_ROOT
SOURCES = ("snapeda", "ultralibrarian", "easyeda")
BROWSER_SOURCES = ("snapeda", "ultralibrarian")  # need an account + browser
# credentials.json in the library root is git-ignored; the committed
# credentials.example.json is the empty template to copy from.
DEFAULT_CREDENTIALS = ROOT / "credentials.json"

# Category from the list's free-text "funzione" (Italian/English), matched
# on whole words and tried in order; lc.classify() on the name is the
# fallback. An explicit "categoria" in the list always wins.
FUNCTION_RULES = [
    ("memory", r"ram|ddr\d*l?|emmc|flash|eeprom|sram|nand|memoria"),
    ("microcontrollers", r"soc|mcu|cpu|microcontroller\w*"),
    ("interface", r"usb-[abc]|rj45|connettore|connector|slot|microsd|hdmi"),
    ("rf", r"wi-?fi|bluetooth|bt|antenna|ethernet|phy|rf"),
    ("power", r"pmic|buck|boost|ldo|regolatore|regulator|load switch|tvs|ferrite|vbus"),
    ("interface", r"usb|esd|uart|i2c|spi"),
    ("mechanical", r"pulsant\w*|button\w*|switch\w*"),
    ("logic", r"rtc"),
]


def classify_part(name: str, funzione: str) -> str:
    text = funzione.lower()
    for category, pattern in FUNCTION_RULES:
        if re.search(rf"\b(?:{pattern})\b", text):
            return category
    return lc.classify(name)


_SYMBOL_NAME_RE = re.compile(r'^\s*\(symbol\s+"([^"]+)"', re.M)
_UNIT_SUFFIX_RE = re.compile(r"_\d+_\d+$")


def norm_mpn(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def component_name(mpn: str) -> str:
    """File-system/lib-table safe name derived from the MPN."""
    name = re.sub(r"[^A-Za-z0-9.+-]+", "_", mpn)
    return re.sub(r"^[^A-Za-z0-9]+", "", name).rstrip("_")


# ---------------------------------------------------------------- config


def credentials_path(cli_value: str | None) -> Path:
    """--credentials, else $MIKILAB_CREDENTIALS, else <library>/credentials.json."""
    value = cli_value or os.environ.get("MIKILAB_CREDENTIALS")
    return Path(value).expanduser() if value else DEFAULT_CREDENTIALS


def is_unset(value) -> bool:
    """Empty strings and '*****' placeholders (as in credentials.example.json) count as unset."""
    return not isinstance(value, str) or not value.strip() or set(value.strip()) == {"*"}


KEYCHAIN_SERVICE = "mikilab.{source}"


def keychain_account(source: str) -> tuple[str, str] | None:
    """(username, password) of a SnapEDA/UltraLibrarian account saved in the
    macOS login Keychain by scripts/credentials_keychain.py, or None."""
    service = KEYCHAIN_SERVICE.format(source=source)
    try:
        attrs = subprocess.run(["security", "find-generic-password", "-s", service],
                               capture_output=True, text=True, timeout=30)
        if attrs.returncode != 0:
            return None
        m = re.search(r'"acct"<blob>="([^"]*)"', attrs.stdout)
        pwd = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not m or pwd.returncode != 0:
        return None
    return m.group(1), pwd.stdout.rstrip("\n")


def assert_not_tracked(path: Path) -> None:
    """Refuse to run if the credentials file is (or is about to be) committed."""
    try:
        rel = path.resolve().relative_to(ROOT)
    except ValueError:
        return  # outside the repo
    proc = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--cached", "--", str(rel)],
                          capture_output=True, text=True)
    if proc.stdout.strip():
        raise SystemExit(f"ERROR: {rel} is tracked by git -- run: git rm --cached {rel}  (it must stay out of GitHub)")


def load_config(path: Path, need_credentials: bool) -> dict:
    if not path.is_file():
        if need_credentials:
            raise SystemExit(f"ERROR: credentials file not found: {path}\n"
                             f"       create it with: cp {ROOT / 'credentials.example.json'} {path}")
        return {}
    assert_not_tracked(path)
    if path.stat().st_mode & 0o077:
        print(f"WARNING: {path} is readable by other users -- run: chmod 600 {path}")
    cfg = json.loads(path.read_text(encoding="utf-8"))

    for src in BROWSER_SOURCES:
        acc = cfg.get(src)
        if acc is None:
            continue
        if not isinstance(acc, dict):
            raise SystemExit(f"ERROR: {path}: '{src}' must be an object")
        if not is_unset(acc.get("password")):
            print(f"WARNING: {path}: password '{src}' in chiaro nel file -- spostala nel Portachiavi:\n"
                  f"         python3 scripts/credentials_keychain.py set {src}")
        # The macOS Keychain wins over the file: there the password never sits in clear.
        keychain = keychain_account(src)
        if keychain:
            acc["username"], acc["password"] = keychain
        if acc.get("enabled", True) and (is_unset(acc.get("username")) or is_unset(acc.get("password"))):
            print(f"WARNING: credenziali '{src}' non impostate -- il login andra' fatto a mano nel browser "
                  f"(per salvarle: python3 scripts/credentials_keychain.py set {src})")
            acc["username"] = acc["password"] = ""
    order = cfg.get("source_order", list(SOURCES))
    bad = [s for s in order if s not in SOURCES]
    if bad:
        raise SystemExit(f"ERROR: {path}: unknown source(s) in source_order: {bad}")
    return cfg


def enabled_sources(cfg: dict, cli_sources: str | None) -> list[str]:
    order = cli_sources.split(",") if cli_sources else cfg.get("source_order", list(SOURCES))
    for s in order:
        if s not in SOURCES:
            raise SystemExit(f"ERROR: unknown source '{s}' (use: {', '.join(SOURCES)})")
    return [s for s in order if cfg.get(s, {}).get("enabled", True)]


# ----------------------------------------------------------- list / plan


def load_list(path: Path, only: set[str] | None) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("componenti", data if isinstance(data, list) else None)
    if not isinstance(items, list):
        raise SystemExit(f"ERROR: {path}: expected a 'componenti' array")

    parts: dict[str, dict] = {}
    for item in items:
        mpn = (item.get("mpn") or "").strip()
        if not mpn:
            continue
        if only and item.get("ref") not in only and mpn not in only:
            continue
        name = component_name(item.get("nome") or mpn)
        bad = lc.check_component_name(name) if name else "empty name"
        if bad:
            raise SystemExit(f"ERROR: {mpn}: {bad}")
        if name in parts:
            parts[name]["refs"].append(item.get("ref", "?"))
            continue
        category = item.get("categoria")
        if category and category not in lc.CATEGORIES:
            raise SystemExit(f"ERROR: {mpn}: categoria '{category}' not in {', '.join(lc.CATEGORIES)}")
        if not category:
            category = classify_part(name, item.get("funzione") or "")
        parts[name] = {
            "name": name, "mpn": mpn, "category": category,
            "refs": [item.get("ref", "?")],
            "lcsc": (item.get("lcsc") or "").strip() or None,
            "urls": {"snapeda": item.get("snapeda"), "ultralibrarian": item.get("ultralibrarian")},
            "skip": bool(item.get("salta")),
        }
    return list(parts.values())


def library_symbol_index() -> dict[str, Path]:
    """normalized symbol name -> library file, over every library."""
    index: dict[str, Path] = {}
    for path in lc.discover_symbol_libraries(ROOT):
        index.setdefault(norm_mpn(path.stem), path)
        text = path.read_text(encoding="utf-8", errors="replace")
        for sym in _SYMBOL_NAME_RE.findall(text):
            if not _UNIT_SUFFIX_RE.search(sym):
                index.setdefault(norm_mpn(sym), path)
    return index


def zip_matches(zip_stem: str, mpn: str) -> bool:
    """True if a downloaded zip is for exactly this MPN: '<MPN>.zip' (SnapEDA),
    'ul_<MPN>.zip' (Ultra Librarian), also with the browser's ' (1)' duplicate
    suffix. A substring match is not enough: 'RK805' must not pick 'RK805-1.zip'."""
    stem = re.sub(r"\s*\(\d+\)$", "", zip_stem)
    stem = re.sub(r"(?i)^ul_", "", stem)
    return norm_mpn(stem) == norm_mpn(mpn)


def find_cached_zip(download_dir: Path, source: str, part: dict) -> Path | None:
    exact = download_dir / source / f"{part['name']}.zip"
    if exact.is_file():
        return exact
    loose = sorted(
        (p for p in download_dir.glob("*.zip") if zip_matches(p.stem, part["mpn"])),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    for p in loose:
        if detect_source(p) == source:
            return p
    return None


def detect_source(zip_path: Path) -> str:
    """UL zips are named ul_<MPN>.zip and/or carry several footprint
    variants plus a timestamp-named symbol; everything else is treated as
    SnapEDA."""
    if zip_path.stem.lower().startswith("ul_") or zip_path.parent.name == "ultralibrarian":
        return "ultralibrarian"
    if zip_path.parent.name == "snapeda":
        return "snapeda"
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile:
        return "snapeda"
    mods = [n for n in names if n.lower().endswith(".kicad_mod")]
    if len(mods) > 1 or any(re.search(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.kicad_sym$", n) for n in names):
        return "ultralibrarian"
    return "snapeda"


# ------------------------------------------------------------- download


class ManualDownloader:
    """No Playwright: open the search page in the default browser and wait
    for the user to save the zip into download_dir."""

    def __init__(self, download_dir: Path, timeout_s: int):
        self.download_dir = download_dir
        self.timeout_s = timeout_s

    def fetch(self, source, mpn, url, creds, target: Path) -> Path | None:
        self.download_dir.mkdir(parents=True, exist_ok=True)
        before = {p: p.stat().st_mtime for p in self.download_dir.glob("*.zip")}
        webbrowser.open(url or eda_download.search_url(source, mpn))
        print(f"      scarica lo zip KiCad di {mpn} in {self.download_dir} (attesa max {self.timeout_s}s, Ctrl-C per saltare)")
        deadline = time.time() + self.timeout_s
        try:
            while time.time() < deadline:
                for p in self.download_dir.glob("*.zip"):
                    if before.get(p) != p.stat().st_mtime and zip_matches(p.stem, mpn):
                        time.sleep(1)  # let the browser finish writing
                        target.parent.mkdir(parents=True, exist_ok=True)
                        p.replace(target)
                        return target
                time.sleep(2)
        except KeyboardInterrupt:
            print("      saltato")
        return None

    def close(self):
        pass


def make_downloader(cfg: dict, download_dir: Path):
    if eda_download.HAVE_PLAYWRIGHT:
        return eda_download.BrowserDownloader(cfg, log=print)
    print("NOTE: playwright non installato -- modalita' assistita (apro le pagine, tu scarichi lo zip).")
    print("      Per il download automatico: python3 -m pip install playwright && python3 -m playwright install chromium")
    return ManualDownloader(download_dir, int(cfg.get("download_timeout_seconds", 180)))


# ---------------------------------------------------------------- import


def import_part_zip(zip_path: Path, source: str, part: dict, update: bool, allow_mismatch: bool) -> list[str]:
    importer = import_ultralibrarian if source == "ultralibrarian" else import_snapeda
    _, report = importer.import_zip(
        zip_path, part["name"], part["category"], update=update,
        require_footprint=True, allow_mismatch=allow_mismatch,
    )
    return report


def check_models(result: dict, render_dir: Path) -> None:
    """3D model orientation check + isometric render for an imported part."""
    result["model3d"] = []
    for fp in sorted(ROOT.glob(f"footprints/*/{result['name']}.pretty/*.kicad_mod")):
        status, msgs = model_check.check_footprint(fp)
        entry = {"footprint": str(fp.relative_to(ROOT)), "status": status, "messages": msgs}
        if status != "SKIP":
            png = render_dir / f"{result['name']}.png"
            if model_check.render_footprint(fp, png):
                entry["render"] = str(png)
        result["model3d"].append(entry)
        print(f"  3D {status:4} {result['name']:28} {'; '.join(msgs)}")


def run_check() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_library.py")],
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    parser = argparse.ArgumentParser(description="Download a JSON list of components and add them to the MIKILAB library.")
    parser.add_argument("--list", required=True, help="JSON component list (see test/componenti-librerie-cad.json)")
    parser.add_argument("--credentials", help="credentials/config JSON (default: $MIKILAB_CREDENTIALS, else credentials.json in the library root -- git-ignored; template credentials.example.json, schema docs/credentials.schema.json)")
    parser.add_argument("--only", help="comma-separated refs or MPNs to process (default: all)")
    parser.add_argument("--sources", help="comma-separated source order, overrides credentials.json (snapeda,ultralibrarian,easyeda)")
    parser.add_argument("--dry-run", action="store_true", help="only show what would be done")
    parser.add_argument("--no-download", action="store_true", help="only import zips already in download_dir")
    parser.add_argument("--update", action="store_true", help="re-import components already in the library (replace in place)")
    parser.add_argument("--allow-pin-mismatch", action="store_true", help="import even if some symbol pins have no footprint pad (reported as warning)")
    parser.add_argument("--report", help="JSON report path (default: <list>.report.json)")
    args = parser.parse_args()

    list_path = Path(args.list).expanduser().resolve()
    cfg = load_config(credentials_path(args.credentials), need_credentials=not (args.dry_run or args.no_download))
    sources = enabled_sources(cfg, args.sources)
    download_dir = Path(cfg.get("download_dir", "~/Downloads/mikilab")).expanduser()
    only = {s.strip() for s in args.only.split(",")} if args.only else None

    parts = load_list(list_path, only)
    index = library_symbol_index()
    print(f"{len(parts)} componenti distinti da {list_path.name}; sorgenti: {', '.join(sources)}; zip in {download_dir}\n")

    results = []
    todo = []
    for part in parts:
        existing = index.get(norm_mpn(part["name"])) or index.get(norm_mpn(part["mpn"]))
        if part["skip"]:
            results.append({**part, "status": "SKIPPED", "detail": "salta=true nella lista"})
        elif existing and not args.update:
            results.append({**part, "status": "PRESENT", "detail": str(existing.relative_to(ROOT))})
        else:
            todo.append(part)

    if args.dry_run:
        for r in results:
            print(f"  {r['status']:8} {r['name']:28} {r['detail']}")
        for part in todo:
            cached = {s: find_cached_zip(download_dir, s, part) for s in sources if s in BROWSER_SOURCES}
            have = [f"{s}={p.name}" for s, p in cached.items() if p]
            if "easyeda" in sources and part["lcsc"]:
                have.append(f"easyeda={part['lcsc']}")
            have = ", ".join(have) or "da scaricare"
            print(f"  {'TODO':8} {part['name']:28} cat={part['category']:16} {have}")
        return 0

    downloader = None
    try:
        for n, part in enumerate(todo, 1):
            print(f"[{n}/{len(todo)}] {part['name']} ({', '.join(part['refs'])}) -> {part['category']}")
            attempts = []
            for source in sources:
                if source == "easyeda":
                    if not part["lcsc"]:
                        attempts.append("easyeda: nessun codice LCSC nella lista")
                        continue
                    if args.no_download:
                        attempts.append("easyeda: saltato (--no-download)")
                        continue
                    print(f"    easyeda: conversione {part['lcsc']} con easyeda2kicad")
                    try:
                        _, report = import_easyeda.import_lcsc(
                            part["lcsc"], part["name"], part["category"], update=args.update,
                            require_footprint=True, allow_mismatch=args.allow_pin_mismatch)
                    except ic.ImportError_ as e:
                        print(f"    easyeda: SCARTATO -- {e}")
                        attempts.append(f"easyeda: {e}")
                        continue
                    print("\n".join("    " + line for line in report))
                    results.append({**part, "status": "IMPORTED", "source": "easyeda", "detail": "; ".join(attempts)})
                    break

                zip_path = find_cached_zip(download_dir, source, part)
                if zip_path is None and not args.no_download:
                    if downloader is None:
                        downloader = make_downloader(cfg, download_dir)
                    elif attempts or n > 1:
                        time.sleep(float(cfg.get("delay_seconds", 5)))
                    print(f"    {source}: download")
                    target = download_dir / source / f"{part['name']}.zip"
                    try:
                        zip_path = downloader.fetch(source, part["mpn"], part["urls"].get(source), cfg.get(source, {}), target)
                    except Exception as e:
                        attempts.append(f"{source}: download fallito ({e})")
                        continue
                if zip_path is None:
                    attempts.append(f"{source}: nessuno zip")
                    continue

                print(f"    {source}: import {zip_path.name}")
                try:
                    report = import_part_zip(zip_path, source, part, args.update, args.allow_pin_mismatch)
                except ic.ImportError_ as e:
                    print(f"    {source}: SCARTATO -- {e}")
                    attempts.append(f"{source}: {e}")
                    continue
                print("\n".join("    " + line for line in report))
                results.append({**part, "status": "IMPORTED", "source": source, "zip": str(zip_path), "detail": "; ".join(attempts)})
                break
            else:
                results.append({**part, "status": "FAILED", "detail": "; ".join(attempts)})
                print(f"    FALLITO: {'; '.join(attempts)}")
    finally:
        if downloader is not None:
            downloader.close()

    imported = [r for r in results if r["status"] == "IMPORTED"]
    if imported:
        render_dir = list_path.with_suffix(".renders")
        print(f"\nControllo orientamento modelli 3D (render in {render_dir})")
        for r in imported:
            check_models(r, render_dir)

    if imported:
        lc.write_sym_lib_table(ROOT)
        lc.write_fp_lib_table(ROOT)
        lc.write_global_tables(ROOT)
        print("\nRigenerate sym-lib-table, fp-lib-table e le versioni .global")

    print("\nValidazione libreria (check_library.py)...")
    check_rc, check_out = run_check()
    summary = [l for l in check_out.splitlines() if l.startswith(("Errors:", "Warnings:", "RESULT:"))]
    if check_rc:
        print(check_out)
    else:
        print("  " + "\n  ".join(summary))

    print("\nRiepilogo:")
    for r in results:
        warn3d = " [3D DA VERIFICARE]" if any(m["status"] == "WARN" for m in r.get("model3d", [])) else ""
        print(f"  {r['status']:8} {r['name']:28} {r.get('source', ''):14} {r['detail']}{warn3d}")
    if imported:
        print(f"\nControlla nei render il pin 1 (una rotazione di 180 gradi non e' rilevabile in automatico): {render_dir}")

    report_path = Path(args.report) if args.report else list_path.with_suffix(".report.json")
    report_path.write_text(json.dumps({
        "list": str(list_path),
        "date": datetime.now().isoformat(timespec="seconds"),
        "library_check": {"ok": check_rc == 0, "summary": summary},
        "components": [{k: v for k, v in r.items() if k != "skip"} for r in results],
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nReport: {report_path}")
    if imported:
        print("NOTE: se la libreria e' registrata globalmente in KiCad (README sezione 1), riesegui il merge delle lib-table globali.")

    failed = any(r["status"] == "FAILED" for r in results)
    return 1 if failed or check_rc else 0


if __name__ == "__main__":
    sys.exit(main())
