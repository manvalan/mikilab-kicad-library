#!/usr/bin/env python3
"""
credentials_keychain.py
=======================

Keeps the SnapEDA / UltraLibrarian accounts in the macOS login Keychain
instead of credentials.json, so no password ever sits in a file.
fetch_components.py and fetch_worker.py read them from there.

    python3 scripts/credentials_keychain.py set snapeda        # asks username, then password
    python3 scripts/credentials_keychain.py set ultralibrarian
    python3 scripts/credentials_keychain.py status
    python3 scripts/credentials_keychain.py delete snapeda

The password is typed into the prompt of macOS's own `security` tool:
it is not echoed, not passed on the command line (where other processes
could see it) and not written anywhere else. After "set" the password
in credentials.json is replaced by "*******".
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_components as fc


def service(source: str) -> str:
    return fc.KEYCHAIN_SERVICE.format(source=source)


def blank_file_password(source: str, username: str, cred_path: Path) -> None:
    if not cred_path.is_file():
        return
    cfg = json.loads(cred_path.read_text(encoding="utf-8"))
    acc = cfg.setdefault(source, {"enabled": True})
    acc["username"] = ""
    acc["password"] = "*******"
    mode = cred_path.stat().st_mode & 0o777
    cred_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cred_path.chmod(mode)


def cmd_set(source: str, cred_path: Path) -> int:
    username = input(f"{source} username: ").strip()
    if not username:
        print("ERROR: username vuoto")
        return 1
    # Remove a previous entry (possibly with another username) first.
    subprocess.run(["security", "delete-generic-password", "-s", service(source)],
                   capture_output=True)
    # "-w" as the last argument makes `security` prompt for the password itself.
    print("password (non viene mostrata):")
    proc = subprocess.run(["security", "add-generic-password", "-U",
                           "-s", service(source), "-a", username,
                           "-l", f"MIKILAB {source}", "-w"])
    if proc.returncode != 0:
        print("ERROR: salvataggio nel Portachiavi non riuscito")
        return 1
    blank_file_password(source, username, cred_path)
    print(f"OK: account {source} salvato nel Portachiavi (servizio {service(source)}).")
    return 0


def cmd_status() -> int:
    for source in fc.BROWSER_SOURCES:
        acc = fc.keychain_account(source)
        print(f"{source:15} {'Portachiavi: ' + acc[0] if acc else 'non nel Portachiavi'}")
    return 0


def cmd_delete(source: str) -> int:
    proc = subprocess.run(["security", "delete-generic-password", "-s", service(source)],
                          capture_output=True)
    print("eliminato" if proc.returncode == 0 else "non presente")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SnapEDA/UltraLibrarian accounts in the macOS Keychain.")
    parser.add_argument("action", choices=("set", "status", "delete"))
    parser.add_argument("source", nargs="?", choices=fc.BROWSER_SOURCES)
    parser.add_argument("--credentials", help="credentials.json whose password is blanked after 'set'")
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("ERROR: il Portachiavi è disponibile solo su macOS")
    if args.action in ("set", "delete") and not args.source:
        parser.error("serve la sorgente: snapeda o ultralibrarian")
    if args.action == "set":
        return cmd_set(args.source, fc.credentials_path(args.credentials))
    if args.action == "delete":
        return cmd_delete(args.source)
    return cmd_status()


if __name__ == "__main__":
    sys.exit(main())
