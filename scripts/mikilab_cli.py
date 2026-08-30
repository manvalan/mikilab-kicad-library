#!/usr/bin/env python3
"""
mikilab_cli.py
===============

Stable JSON-over-stdout CLI for mikilab_lib.py, for apps that are not
Python (Swift, C++, ...) or that otherwise want to drive the MIKILAB
library out-of-process instead of importing it directly.

Every invocation prints exactly one JSON object to stdout and nothing
else (diagnostics, if any, go to stderr) -- safe to pipe straight into a
JSON parser. Exit code is 0 iff "ok" is true.

On success:
    {"ok": true, "data": <command-specific object or array>}

On failure:
    {"ok": false, "error": "<human-readable message>"}

Commands:

    add     --name NAME --symbol PATH [--footprint PATH] [--model PATH] [--category CAT]
    update  --name NAME --symbol PATH [--footprint PATH] [--model PATH] [--category CAT]
    remove  --name NAME
    get     --name NAME
    list    [--category CAT]
    find    --query TEXT [--category CAT]

Examples:

    python3 scripts/mikilab_cli.py add --name TPS7A2018PDBVR \\
        --symbol /path/TPS7A2018PDBVR.kicad_sym \\
        --footprint /path/SOT95P280X145-5N.kicad_mod --category power

    python3 scripts/mikilab_cli.py get --name TPS7A2018PDBVR

    python3 scripts/mikilab_cli.py find --query tps22

From Swift: run as a subprocess (Process), read stdout, decode with
JSONDecoder. From C++: popen()/posix_spawn + any JSON library (e.g.
nlohmann::json). In both cases, check the process exit code as well as
"ok" -- a non-zero exit always means "ok": false was printed (or, if the
process crashed before printing anything, stdout will be empty).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mikilab_lib as mikilab


def _emit(payload: dict) -> int:
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("ok") else 1


def _ok(data) -> dict:
    if dataclasses.is_dataclass(data):
        data = dataclasses.asdict(data)
    elif isinstance(data, list):
        data = [dataclasses.asdict(x) if dataclasses.is_dataclass(x) else x for x in data]
    return {"ok": True, "data": data}


def _err(message: str) -> dict:
    return {"ok": False, "error": message}


def cmd_add(args) -> dict:
    result = mikilab.add_component(
        name=args.name, symbol=args.symbol, footprint=args.footprint,
        model=args.model, category=args.category,
    )
    return _ok(result)


def cmd_update(args) -> dict:
    result = mikilab.update_component(
        name=args.name, symbol=args.symbol, footprint=args.footprint,
        model=args.model, category=args.category,
    )
    return _ok(result)


def cmd_remove(args) -> dict:
    result = mikilab.remove_component(args.name)
    return _ok(result)


def cmd_get(args) -> dict:
    result = mikilab.get_component(args.name)
    if result is None:
        return _err(f"no component named '{args.name}' found")
    return _ok(result)


def cmd_list(args) -> dict:
    return _ok(mikilab.list_components(category=args.category))


def cmd_find(args) -> dict:
    return _ok(mikilab.find_components(args.query, category=args.category))


def main() -> int:
    parser = argparse.ArgumentParser(description="JSON CLI over mikilab_lib.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Add a new component")
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--symbol", required=True)
    p_add.add_argument("--footprint")
    p_add.add_argument("--model")
    p_add.add_argument("--category", choices=mikilab.lc.CATEGORIES)
    p_add.set_defaults(func=cmd_add)

    p_update = sub.add_parser("update", help="Replace an existing component in place (or create it)")
    p_update.add_argument("--name", required=True)
    p_update.add_argument("--symbol", required=True)
    p_update.add_argument("--footprint")
    p_update.add_argument("--model")
    p_update.add_argument("--category", choices=mikilab.lc.CATEGORIES)
    p_update.set_defaults(func=cmd_update)

    p_remove = sub.add_parser("remove", help="Remove a component")
    p_remove.add_argument("--name", required=True)
    p_remove.set_defaults(func=cmd_remove)

    p_get = sub.add_parser("get", help="Look up one component by exact name")
    p_get.add_argument("--name", required=True)
    p_get.set_defaults(func=cmd_get)

    p_list = sub.add_parser("list", help="List components, optionally filtered by category")
    p_list.add_argument("--category", choices=mikilab.lc.CATEGORIES)
    p_list.set_defaults(func=cmd_list)

    p_find = sub.add_parser("find", help="Case-insensitive substring search by name")
    p_find.add_argument("--query", required=True)
    p_find.add_argument("--category", choices=mikilab.lc.CATEGORIES)
    p_find.set_defaults(func=cmd_find)

    args = parser.parse_args()

    try:
        payload = args.func(args)
    except mikilab.MikilabError as e:
        payload = _err(str(e))
    except Exception as e:  # last-resort guard so callers always get valid JSON, never a traceback on stdout
        payload = _err(f"unexpected error: {e}")

    return _emit(payload)


if __name__ == "__main__":
    sys.exit(main())
