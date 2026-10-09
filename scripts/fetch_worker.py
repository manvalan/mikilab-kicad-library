#!/usr/bin/env python3
"""
fetch_worker.py
===============

Worker for the ComponentVault KiCad requests, without any server: the
apps (macOS/iPad) and this worker share a folder -- iCloud Drive, a
Syncthing/Dropbox folder, an SMB share -- chosen in ComponentVault under
Settings > Shared folder.

    <shared>/kicad/jobs/<uuid>/request.json   written by the app
    <shared>/kicad/jobs/<uuid>/status.json    written by this worker
    <shared>/kicad/jobs/<uuid>/<name>.zip     KiCad files of a component
    <shared>/kicad/jobs/<uuid>/<name>.png     3D render
    <shared>/kicad/library_index.json         library index (library_index.py)
    <shared>/kicad/worker.json                heartbeat: "the Mac with KiCad is on"

The worker runs on the Mac that holds the library and credentials.json:
it claims each queued request, runs fetch_components.py on it, installs
the parts in the local library and leaves, per imported component, a zip
with its KiCad files (symbol, footprint library, 3D models) plus the 3D
render next to the request. The SnapEDA/UltraLibrarian passwords never
leave this machine and never enter the shared folder.

Configuration, in credentials.json (git-ignored):
    "componentvault": {"shared_dir": "~/Library/Mobile Documents/com~apple~CloudDocs/ComponentVault"}

Usage:
    python3 scripts/fetch_worker.py            # poll every 60 s until Ctrl-C
    python3 scripts/fetch_worker.py --once     # handle queued requests, then exit
    python3 scripts/fetch_worker.py --shared-dir PATH --interval 30
    python3 scripts/fetch_worker.py --index-only   # only (re)write the library index

Library changes are not committed: review them (render PNGs, git diff)
and commit as usual.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_common as lc
import fetch_components as fc
import library_index

ROOT = lc.LIBRARY_ROOT
_MODEL_RE = re.compile(r"\(model\s+\"?\$\{MIKILAB\}/([^\s\")]+)")
_LCSC_RE = re.compile(r"^C\d{1,12}$")

# A job whose worker died is taken over after this long.
STALE_AFTER = timedelta(hours=2)
# The heartbeat is rewritten at most this often (it is synced to every device).
HEARTBEAT_EVERY = timedelta(minutes=5)
MAX_ITEMS = 100


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, data: dict) -> None:
    """Atomic write: readers on other devices never see half a file."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class SharedFolder:
    def __init__(self, path: Path):
        self.root = path.expanduser()
        if not self.root.is_dir():
            raise SystemExit(f"ERROR: cartella condivisa non trovata: {self.root}")
        self.kicad = self.root / "kicad"
        self.jobs = self.kicad / "jobs"
        self.jobs.mkdir(parents=True, exist_ok=True)
        self._last_heartbeat: datetime | None = None

    def heartbeat(self, force: bool = False) -> None:
        now = datetime.now(timezone.utc)
        if not force and self._last_heartbeat and now - self._last_heartbeat < HEARTBEAT_EVERY:
            return
        write_json(self.kicad / "worker.json", {
            "host": socket.gethostname().split(".")[0],
            "lastSeen": now_iso(),
            "library": str(ROOT.name),
        })
        self._last_heartbeat = now

    def queued(self) -> list[Path]:
        """Job folders waiting for a worker, oldest first."""
        out = []
        for d in self.jobs.iterdir():
            if not d.is_dir() or not _is_uuid(d.name) or not (d / "request.json").is_file():
                continue
            status = read_json(d / "status.json")
            if status is None:
                out.append(d)
            elif status.get("status") == "running" and _older_than(status.get("updatedAt"), STALE_AFTER):
                out.append(d)
        return sorted(out, key=lambda d: (read_json(d / "request.json") or {}).get("createdAt", ""))

    def claim(self, job_dir: Path) -> bool:
        """Mark the job as ours. The O_EXCL lock file keeps two workers on
        the same folder from running the same request."""
        lock = job_dir / ".claim"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            status = read_json(job_dir / "status.json") or {}
            if not _older_than(status.get("updatedAt"), STALE_AFTER):
                return False
            lock.unlink(missing_ok=True)  # stale: the previous worker died
            return self.claim(job_dir)
        with os.fdopen(fd, "w") as f:
            f.write(socket.gethostname())
        self.set_status(job_dir, "running")
        return True

    def set_status(self, job_dir: Path, status: str, result: dict | None = None, error: str = "") -> None:
        write_json(job_dir / "status.json", {
            "status": status,
            "updatedAt": now_iso(),
            "worker": socket.gethostname().split(".")[0],
            "result": result or {},
            "error": error[:4000],
        })
        if status != "running":
            (job_dir / ".claim").unlink(missing_ok=True)


def _is_uuid(name: str) -> bool:
    try:
        return str(uuid.UUID(name)) == name.lower()
    except ValueError:
        return False


def _older_than(stamp: str | None, delta: timedelta) -> bool:
    try:
        when = datetime.fromisoformat(stamp or "")
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - when > delta


def clean_items(raw) -> tuple[list[dict], list[str]]:
    """Whitelist and validate the request fields. In particular no URLs,
    so a file in the shared folder can never make this Mac open an
    arbitrary page; names and categories are validated again by
    fetch_components.load_list."""
    items, rejected = [], []
    for it in (raw or [])[:MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        mpn = str(it.get("mpn") or "").strip()
        lcsc = str(it.get("lcsc") or "").strip().upper()
        if not mpn or len(mpn) > 128 or (lcsc and not _LCSC_RE.match(lcsc)):
            rejected.append(mpn[:40] or "?")
            continue
        item = {"mpn": mpn}
        if lcsc:
            item["lcsc"] = lcsc
        for key, limit in (("ref", 64), ("funzione", 256), ("nome", 128), ("categoria", 64)):
            value = str(it.get(key) or "").strip()
            if value:
                item[key] = value[:limit]
        if item.get("categoria") and item["categoria"] not in lc.CATEGORIES:
            del item["categoria"]
        items.append(item)
    return items, rejected


def package_component(name: str, out_dir: Path) -> Path | None:
    """Zip the component's own symbol file, footprint library and the
    MIKILAB 3D models its footprints reference, with library-relative paths."""
    sym = lc.find_symbol_by_name(ROOT, name)
    pretty = sorted(ROOT.glob(f"footprints/*/{name}.pretty"))
    if sym is None and not pretty:
        return None
    files = [sym] if sym else []
    for d in pretty:
        for mod in sorted(d.glob("*.kicad_mod")):
            files.append(mod)
            for rel in _MODEL_RE.findall(mod.read_text(encoding="utf-8", errors="replace")):
                model = (ROOT / rel).resolve()
                if ROOT in model.parents and model.is_file():
                    files.append(model)
    out = out_dir / f"{fc.component_name(name)}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in dict.fromkeys(files):
            zf.write(f, f.relative_to(ROOT))
    return out


LIBRARY_NAME = "MIKILAB"


def write_library_index(shared: SharedFolder) -> None:
    """Rewrite the library index in the shared folder if the components
    or the library name changed (generatedAt excluded from the comparison)."""
    index = library_index.build_index(LIBRARY_NAME)
    target = shared.kicad / "library_index.json"
    current = read_json(target)
    digest = lambda comps: hashlib.sha256(json.dumps(comps, sort_keys=True).encode()).hexdigest()
    if (current is not None and current.get("library") == index["library"]
            and digest(current.get("components")) == digest(index["components"])):
        return
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, target)
    print(f"  indice libreria aggiornato ({index['count']} simboli)")


def run_job(shared: SharedFolder, job_dir: Path, cred_path: Path) -> None:
    req = read_json(job_dir / "request.json") or {}
    items, rejected = clean_items(req.get("items"))
    print(f"[{time.strftime('%H:%M:%S')}] richiesta {job_dir.name}: {len(items)} componenti")
    if not items:
        shared.set_status(job_dir, "failed", {}, "nessun componente valido nella richiesta")
        return

    with tempfile.TemporaryDirectory(prefix="fetch_job_") as tmp:
        tmp = Path(tmp)
        list_path = tmp / "lista.json"
        list_path.write_text(json.dumps({"componenti": items}, ensure_ascii=False), encoding="utf-8")
        cmd = [sys.executable, str(ROOT / "scripts" / "fetch_components.py"),
               "--list", str(list_path), "--credentials", str(cred_path)]
        if req.get("update") is True:
            cmd.append("--update")
        # Output not captured: browser prompts ("complete the login by hand")
        # must reach whoever is at the Mac while the job runs.
        proc = subprocess.run(cmd)

        report_path = list_path.with_suffix(".report.json")
        if not report_path.is_file():
            shared.set_status(job_dir, "failed", {}, f"fetch_components terminato con codice {proc.returncode} senza report")
            return
        report = json.loads(report_path.read_text(encoding="utf-8"))

        components = []
        for c in report.get("components", []):
            entry = {k: c.get(k) for k in ("name", "mpn", "refs", "category", "status", "source", "detail")}
            entry["model3d"] = [{k: m.get(k) for k in ("footprint", "status", "messages")} for m in c.get("model3d", [])]
            entry["files"] = {}
            if c["status"] in ("IMPORTED", "PRESENT"):
                z = package_component(c["name"], tmp)
                if z is not None:
                    shutil.copyfile(z, job_dir / z.name)
                    entry["files"]["kicad"] = z.name
            for m in c.get("model3d", []):
                png = Path(m.get("render") or "")
                if png.is_file():
                    shutil.copyfile(png, job_dir / png.name)
                    entry["files"]["render"] = png.name
            components.append(entry)

    failed = [c["name"] for c in components if c["status"] == "FAILED"] + rejected
    result = {"library_check": report.get("library_check", {}), "components": components}
    ok = [c for c in components if c["status"] in ("IMPORTED", "PRESENT")]
    if not report.get("library_check", {}).get("ok", False) or not ok:
        status = "failed"
    else:
        status = "partial" if failed else "done"
    error = f"non importati: {', '.join(failed)}" if failed else ""
    write_library_index(shared)  # before the status: the app re-reads it when the job ends
    shared.set_status(job_dir, status, result, error)
    print(f"  -> {status} {error}")


def shared_dir_from(cfg: dict, cli_value: str | None, cred_path: Path) -> Path:
    value = cli_value or (cfg.get("componentvault") or {}).get("shared_dir", "")
    if fc.is_unset(value):
        raise SystemExit(f"ERROR: {cred_path}: imposta componentvault.shared_dir (o usa --shared-dir)")
    return Path(value).expanduser()


def main() -> int:
    parser = argparse.ArgumentParser(description="ComponentVault KiCad worker (shared folder).")
    parser.add_argument("--credentials", help="credentials.json (default: $MIKILAB_CREDENTIALS or <library>/credentials.json)")
    parser.add_argument("--shared-dir", help="shared folder chosen in ComponentVault (default: componentvault.shared_dir)")
    parser.add_argument("--once", action="store_true", help="process the queued requests and exit")
    parser.add_argument("--interval", type=int, default=60, help="poll interval in seconds (default 60)")
    parser.add_argument("--index-only", action="store_true", help="only write the library index and exit")
    args = parser.parse_args()

    cred_path = fc.credentials_path(args.credentials)
    cfg = fc.load_config(cred_path, need_credentials=not args.index_only)
    shared = SharedFolder(shared_dir_from(cfg, args.shared_dir, cred_path))
    # Name shown in the apps for this library ("componentvault.library_name").
    global LIBRARY_NAME
    name = (cfg.get("componentvault") or {}).get("library_name", "")
    if not fc.is_unset(name):
        LIBRARY_NAME = str(name).strip()[:64]
    print(f"Worker KiCad sulla cartella {shared.root} (Ctrl-C per fermare)")
    write_library_index(shared)
    shared.heartbeat(force=True)
    if args.index_only:
        return 0
    try:
        while True:
            try:
                queue = shared.queued()
            except OSError as e:  # folder unmounted / offline: retry later
                print(f"[{time.strftime('%H:%M:%S')}] cartella non raggiungibile ({e}), riprovo")
                if args.once:
                    return 1
                time.sleep(args.interval)
                continue
            for job_dir in queue:
                if not shared.claim(job_dir):
                    continue
                try:
                    run_job(shared, job_dir, cred_path)
                except Exception as e:  # report and keep serving the queue
                    print(f"  -> failed: {type(e).__name__}: {e}")
                    try:
                        shared.set_status(job_dir, "failed", {}, f"{type(e).__name__}: {e}")
                    except OSError:
                        pass  # folder gone: the job is taken over after 2 h
                shared.heartbeat(force=True)
            try:
                shared.heartbeat()
            except OSError:
                pass
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
