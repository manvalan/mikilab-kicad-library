#!/usr/bin/env python3
"""
mikilab_lib.py
===============

Public Python API for managing the MIKILAB KiCad library: add, update,
remove and query components. Meant to be imported directly by Python
apps:

    import sys
    sys.path.insert(0, "/path/to/mikylab_kikad_library/scripts")
    import mikilab_lib as mikilab

    result = mikilab.add_component(
        name="TPS7A2018PDBVR",
        symbol="/path/to/TPS7A2018PDBVR.kicad_sym",
        footprint="/path/to/SOT95P280X145-5N.kicad_mod",
        model="/path/to/TPS7A2018PDBVR.step",
        category="power",
    )

Non-Python apps (Swift, C++, ...) should drive this through
mikilab_cli.py instead, which exposes the same operations as a stable
JSON-over-stdout CLI (see that file's docstring).

This module does not reimplement any import/removal logic -- it is a
thin, typed wrapper over import_component.py's existing functions (same
validation, collision handling, and lib-table regeneration used by every
other entry point in this repo: add_component.py, import_batch.py,
import_snapeda.py, import_ultralibrarian.py). It only adds:
  - structured results/exceptions instead of print()+exit code,
  - query functions (get/list/find) that nothing else in this repo
    exposes today.

All mutating calls (add_component, update_component, remove_component)
regenerate sym-lib-table, fp-lib-table, sym-lib-table.global and
fp-lib-table.global before returning, and append rows to MANIFEST.csv --
same guarantees as the CLI scripts. They do NOT touch the real KiCad
global tables under ~/Library/Preferences/kicad/*/ -- if this library is
registered globally in KiCad (README.md section 1), that merge step is
still a separate, deliberate action (see README section 1, step 3).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_common as lc
import import_component as ic

ROOT = lc.LIBRARY_ROOT


class MikilabError(Exception):
    """Raised for any library-management failure: bad component name,
    missing source file, unknown category, component not found (on
    remove/lookup), etc. str(e) is a human-readable message safe to show
    to a user or log as-is."""


@dataclass
class ComponentResult:
    """Result of a successful add_component/update_component/remove_component call."""

    name: str
    category: str
    action: str  # "add" | "update" | "remove"
    symbol_path: str | None = None
    footprint_path: str | None = None
    footprint_status: str | None = None  # NEW / UPDATED / DUPLICATE / RENAMED_COLLISION
    model_path: str | None = None
    messages: list[str] = field(default_factory=list)


@dataclass
class ComponentInfo:
    """One entry as returned by get_component/list_components/find_components.

    Note: this reports one entry per .kicad_sym file under symbols/, which
    is the same unit add_component/remove_component operate on. A few
    library files (e.g. category-wide symbol libraries like LED.kicad_sym)
    contain multiple symbol definitions internally; this API does not look
    inside those, it reports the file as a single entry."""

    name: str
    category: str
    symbol_path: str
    sym_nickname: str
    footprint_path: str | None
    fp_nickname: str | None
    footprint_files: list[str]
    model_paths: list[str]


def _component_info(symbol_path: Path) -> ComponentInfo:
    name = symbol_path.stem
    category = symbol_path.parent.name

    pretty_dir = ROOT / "footprints" / category / f"{name}.pretty"
    footprint_path = None
    fp_nickname = None
    footprint_files: list[str] = []
    if pretty_dir.is_dir():
        footprint_path = str(pretty_dir.relative_to(ROOT))
        fp_nickname = lc.fp_nickname(pretty_dir)
        footprint_files = sorted(p.stem for p in pretty_dir.glob("*.kicad_mod"))

    models_dir = ROOT / "3dmodels" / category
    model_paths = []
    if models_dir.is_dir():
        model_paths = sorted(
            str(p.relative_to(ROOT))
            for p in models_dir.glob(f"{name}.*")
            if p.suffix.lower() in lc.MODEL_EXTENSIONS
        )

    return ComponentInfo(
        name=name,
        category=category,
        symbol_path=str(symbol_path.relative_to(ROOT)),
        sym_nickname=lc.sym_nickname(symbol_path),
        footprint_path=footprint_path,
        fp_nickname=fp_nickname,
        footprint_files=footprint_files,
        model_paths=model_paths,
    )


def _resolve(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    return Path(path).expanduser().resolve()


def _regenerate_tables() -> None:
    lc.write_sym_lib_table(ROOT)
    lc.write_fp_lib_table(ROOT)
    lc.write_global_tables(ROOT)


def _import(
    name: str,
    symbol: str | Path,
    footprint: str | Path | None,
    model: str | Path | None,
    category: str | None,
    update: bool,
    action: str,
) -> ComponentResult:
    name = name.strip()
    if not name:
        raise MikilabError("name must not be empty")

    symbol_path = _resolve(symbol)
    footprint_path = _resolve(footprint)
    model_path = _resolve(model)

    for label, p in (("symbol", symbol_path), ("footprint", footprint_path), ("model", model_path)):
        if p is not None and not p.is_file():
            raise MikilabError(f"{label} does not exist or is not a file: {p}")

    if model_path is not None and footprint_path is None:
        raise MikilabError("model requires footprint (a 3D model needs a footprint to attach to)")

    resolved_category = category or lc.classify(name)
    if resolved_category not in lc.CATEGORIES:
        raise MikilabError(
            f"category '{resolved_category}' is not one of the known categories: "
            + ", ".join(lc.CATEGORIES)
        )

    manifest_rows: list[list[str]] = []
    report: list[str] = []

    try:
        sym_dst = ic.import_symbol(name, resolved_category, symbol_path, manifest_rows, report, update=update)
    except ic.ImportError_ as e:
        lc.append_manifest_rows(ROOT, [["symbol", str(symbol_path), "", "ERROR", "", str(e)]])
        raise MikilabError(str(e)) from e

    fp_dst = None
    fp_status = None
    model_dst = None

    if footprint_path is not None:
        try:
            fp_dst, fp_nick, fp_name, fp_status = ic.import_footprint(
                name, resolved_category, footprint_path, manifest_rows, report, update=update
            )
        except ic.ImportError_ as e:
            lc.append_manifest_rows(ROOT, manifest_rows + [["footprint", str(footprint_path), "", "ERROR", "", str(e)]])
            raise MikilabError(str(e)) from e

        if model_path is not None:
            model_target = fp_dst if fp_dst is not None else None
            ic.import_model(name, resolved_category, model_path, model_target, manifest_rows, report)
            model_dst = ROOT / "3dmodels" / resolved_category / f"{name}{model_path.suffix.lower()}"

        text = sym_dst.read_text(encoding="utf-8")
        new_ref = f"{fp_nick}:{fp_name}"
        new_text, changed = lc.set_symbol_footprint_property(text, new_ref)
        if changed:
            sym_dst.write_text(new_text, encoding="utf-8")
            report.append(f"  Linked symbol Footprint property -> {new_ref}")

    lc.append_manifest_rows(ROOT, manifest_rows)
    _regenerate_tables()

    return ComponentResult(
        name=name,
        category=resolved_category,
        action=action,
        symbol_path=str(sym_dst.relative_to(ROOT)),
        footprint_path=str(fp_dst.relative_to(ROOT)) if fp_dst is not None else None,
        footprint_status=fp_status,
        model_path=str(model_dst.relative_to(ROOT)) if model_dst is not None else None,
        messages=report,
    )


def add_component(
    name: str,
    symbol: str | Path,
    footprint: str | Path | None = None,
    model: str | Path | None = None,
    category: str | None = None,
) -> ComponentResult:
    """Add a new component. Raises MikilabError if a component with this
    name already exists -- use update_component() to replace it in
    place instead."""
    return _import(name, symbol, footprint, model, category, update=False, action="add")


def update_component(
    name: str,
    symbol: str | Path,
    footprint: str | Path | None = None,
    model: str | Path | None = None,
    category: str | None = None,
) -> ComponentResult:
    """Replace an existing component's symbol/footprint in place (upsert:
    if no component with this name exists yet, it is created, same as
    add_component()). `category` should normally match the component's
    current category -- this does not move files between category
    directories; use remove_component() + add_component() to
    recategorize."""
    return _import(name, symbol, footprint, model, category, update=True, action="update")


def remove_component(name: str) -> ComponentResult:
    """Remove a component: its symbol, its footprint library (if any),
    and its 3D model(s) (if any). Raises MikilabError if no component
    with this name exists."""
    name = name.strip()
    if not name:
        raise MikilabError("name must not be empty")

    manifest_rows: list[list[str]] = []
    report: list[str] = []

    try:
        ic.remove_component(name, manifest_rows, report)
    except ic.ImportError_ as e:
        raise MikilabError(str(e)) from e

    category = next(
        (row[5].split("category=", 1)[1] for row in manifest_rows if row[0] == "symbol" and "category=" in row[5]),
        "",
    )

    lc.append_manifest_rows(ROOT, manifest_rows)
    _regenerate_tables()

    return ComponentResult(name=name, category=category, action="remove", messages=report)


def get_component(name: str) -> ComponentInfo | None:
    """Look up a component by exact name. Returns None if not found."""
    path = lc.find_symbol_by_name(ROOT, name.strip())
    if path is None:
        return None
    return _component_info(path)


def list_components(category: str | None = None) -> list[ComponentInfo]:
    """List every symbol library file under symbols/ (optionally filtered
    to one category). See ComponentInfo's docstring for what "one entry"
    means for multi-symbol category libraries."""
    if category is not None and category not in lc.CATEGORIES:
        raise MikilabError(
            f"category '{category}' is not one of the known categories: " + ", ".join(lc.CATEGORIES)
        )

    entries = [_component_info(p) for p in lc.discover_symbol_libraries(ROOT)]
    if category is not None:
        entries = [e for e in entries if e.category == category]
    return entries


def find_components(query: str, category: str | None = None) -> list[ComponentInfo]:
    """Case-insensitive substring search over component names, optionally
    restricted to one category."""
    q = query.strip().lower()
    return [e for e in list_components(category) if q in e.name.lower()]
