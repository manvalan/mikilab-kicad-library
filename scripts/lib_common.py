"""
lib_common.py
=============

Shared helpers for the MIKILAB KiCad library scripts
(check_library.py, import_component.py, add_component.py, import_batch.py).

This module knows nothing about any external/source repository. It only
operates on the MIKILAB library rooted at LIBRARY_ROOT (the directory that
contains sym-lib-table, fp-lib-table, symbols/, footprints/, 3dmodels/).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

LIBRARY_ROOT = Path(__file__).resolve().parent.parent

SYMBOL_EXT = ".kicad_sym"
FOOTPRINT_EXT = ".kicad_mod"
MODEL_EXTENSIONS = {".step", ".stp", ".wrl", ".wrz"}

CATEGORIES = [
    "analog",
    "audio",
    "display",
    "fpga_cpld",
    "interface",
    "logic",
    "mechanical",
    "memory",
    "microcontrollers",
    "other",
    "power",
    "rf",
]

CATEGORY_RULES = [
    ("microcontrollers", [
        "esp32", "esp8266", "esp32-s31", "mcu", "microcontroller",
        "cpu", "processor",
    ]),
    ("audio", [
        "adau", "si4684", "codec", "audio", "dsp", "amplifier_audio",
    ]),
    ("power", [
        "power", "regulator", "converter", "battery", "charger",
        "bq25896", "bq27441", "ap63203", "tps7a", "tps22918",
        "ina218", "tca9555",
    ]),
    ("rf", [
        "bluetooth", "bt1035", "bt1058", "wifi", "wlan", "antenna",
        "gps", "gnss", "nfc", "transceiver", "phy", "ethernet",
        "rf",
    ]),
    ("interface", [
        "interface", "connector", "usb", "uart", "spi", "i2c", "i3c",
        "can", "hdmi", "displayport", "line_driver",
    ]),
    ("memory", [
        "memory", "eeprom", "flash", "sram", "dram", "nand", "nor",
    ]),
    ("logic", [
        "logic", "74xx", "4xxx", "buffer", "gate", "timer",
        "comparator", "mux", "demux", "flipflop", "counter",
    ]),
    ("analog", [
        "analog", "sensor", "diode", "transistor", "fet", "mosfet",
        "opamp", "operational", "reference", "filter", "switch",
        "relay",
    ]),
    ("fpga_cpld", [
        "fpga", "cpld", "altera", "xilinx", "lattice",
    ]),
    ("display", [
        "display", "lcd", "oled", "led_display", "driver_display",
    ]),
    ("mechanical", [
        "mechanical", "mount", "bracket", "jumper",
    ]),
]


def norm(value: str) -> str:
    """Normalize a string into a safe KiCad library-table nickname token."""
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_")


_MIKILAB_PREFIX_RE = re.compile(r"(?i)^mikilab[_\-]?")


def check_component_name(name: str) -> str | None:
    """Every library nickname gets 'MIKILAB_' prepended automatically by
    sym_nickname()/fp_nickname() when the lib-tables are generated. A
    --name that already starts with 'MIKILAB' therefore does not collide
    with the existing component (so it's invisible to duplicate/collision
    checks) -- it silently creates a second, double-prefixed orphan
    library (e.g. MIKILAB_MIKILAB_FOO) alongside the real one. Returns an
    error message if `name` should be rejected, else None."""
    if _MIKILAB_PREFIX_RE.match(name):
        return (
            f"--name '{name}' must not start with 'MIKILAB' -- that prefix is added "
            f"automatically to every library nickname when sym-lib-table/fp-lib-table "
            f"are generated. Baking it into the component name creates a double-"
            f"prefixed, orphaned duplicate library instead of updating the existing "
            f"one. Use the bare part name (e.g. 'FOO', not 'MIKILAB_FOO')."
        )
    return None


def classify(name: str) -> str:
    """Classify a component/library name into a MIKILAB category."""
    text = norm(name).lower()

    for category, tokens in CATEGORY_RULES:
        for token in tokens:
            if norm(token).lower() in text:
                return category

    return "other"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


def sym_nickname(path: Path) -> str:
    """Nickname for a symbol library file (one .kicad_sym == one library)."""
    return "MIKILAB_" + norm(path.stem)


def fp_nickname(pretty_dir: Path) -> str:
    """Nickname for a footprint library directory (one .pretty == one library)."""
    name = pretty_dir.name
    if name.endswith(".pretty"):
        name = name[: -len(".pretty")]
    return "MIKILAB_" + norm(name)


def unique_nickname(base: str, used: set[str]) -> str:
    nickname = base
    index = 2

    while nickname in used:
        nickname = f"{base}_{index}"
        index += 1

    used.add(nickname)
    return nickname


def discover_symbol_libraries(root: Path) -> list[Path]:
    return sorted((root / "symbols").glob("**/*.kicad_sym"))


def discover_footprint_libraries(root: Path) -> list[Path]:
    return sorted(
        p for p in (root / "footprints").glob("**/*.pretty") if p.is_dir()
    )


@dataclass
class LibEntry:
    nickname: str
    uri: str
    descr: str = ""
    lib_type: str = "KiCad"
    options: str = ""


def build_sym_table_entries(root: Path, env_var: str = "KIPRJMOD") -> list[LibEntry]:
    used: set[str] = set()
    entries = []

    for path in discover_symbol_libraries(root):
        relative = path.relative_to(root)
        nickname = unique_nickname(sym_nickname(path), used)
        uri = "${" + env_var + "}/" + str(relative)
        entries.append(LibEntry(nickname=nickname, uri=uri, descr=path.stem))

    return entries


def build_fp_table_entries(root: Path, env_var: str = "KIPRJMOD") -> list[LibEntry]:
    used: set[str] = set()
    entries = []

    for path in discover_footprint_libraries(root):
        relative = path.relative_to(root)
        nickname = unique_nickname(fp_nickname(path), used)
        uri = "${" + env_var + "}/" + str(relative)
        entries.append(LibEntry(nickname=nickname, uri=uri, descr=path.name))

    return entries


def render_lib_table(kind: str, entries: list[LibEntry]) -> str:
    """kind is 'sym_lib_table' or 'fp_lib_table'."""
    lines = [f"({kind}", "  (version 7)"]

    for e in entries:
        if kind == "sym_lib_table":
            lines.append(
                f'  (lib (name "{e.nickname}")(type "{e.lib_type}")'
                f'(uri "{e.uri}")(options "{e.options}")(descr "{e.descr}"))'
            )
        else:
            lines.append(
                f'  (lib (name "{e.nickname}")(type "{e.lib_type}")'
                f'(uri "{e.uri}")(options "{e.options}")(descr "{e.descr}"))'
            )

    lines.append(")")
    return "\n".join(lines) + "\n"


def write_sym_lib_table(root: Path) -> list[LibEntry]:
    entries = build_sym_table_entries(root)
    (root / "sym-lib-table").write_text(
        render_lib_table("sym_lib_table", entries), encoding="utf-8"
    )
    return entries


def write_fp_lib_table(root: Path) -> list[LibEntry]:
    entries = build_fp_table_entries(root)
    (root / "fp-lib-table").write_text(
        render_lib_table("fp_lib_table", entries), encoding="utf-8"
    )
    return entries


def write_global_tables(root: Path, env_var: str = "MIKILAB") -> tuple[list[LibEntry], list[LibEntry]]:
    """Regenerate sym-lib-table.global / fp-lib-table.global (the
    ${MIKILAB}-based variants used when this library is registered
    globally in KiCad, across every project). Mirrors write_sym_lib_table
    / write_fp_lib_table, which only cover the ${KIPRJMOD} project
    tables -- those alone are not enough for components to show up in
    KiCad when this library is used globally (see README.md section 1)."""
    sym_entries = build_sym_table_entries(root, env_var=env_var)
    fp_entries = build_fp_table_entries(root, env_var=env_var)

    (root / "sym-lib-table.global").write_text(
        render_lib_table("sym_lib_table", sym_entries), encoding="utf-8"
    )
    (root / "fp-lib-table.global").write_text(
        render_lib_table("fp_lib_table", fp_entries), encoding="utf-8"
    )
    return sym_entries, fp_entries


LIB_ENTRY_RE = re.compile(
    r'\(lib\s*\(name\s*"([^"]*)"\)\s*\(type\s*"([^"]*)"\)\s*'
    r'\(uri\s*"([^"]*)"\)\s*\(options\s*"([^"]*)"\)\s*'
    r'\(descr\s*"([^"]*)"\)\)'
)

# Also accept the legacy/alternate KiCad lib-table syntax:
#   (lib "NICK" "URI" (descr "..."))
LIB_ENTRY_RE_LEGACY = re.compile(
    r'\(lib\s+"([^"]*)"\s+"([^"]*)"(?:\s*\(descr\s*"([^"]*)"\))?\s*\)'
)


def parse_lib_table(path: Path) -> tuple[list[LibEntry], int]:
    """Return (entries, version_count). Tolerates either KiCad lib-table
    syntax variant. version_count is the number of top-level (version N)
    occurrences found -- must be exactly 1 for a valid table."""

    if not path.exists():
        return [], 0

    text = path.read_text(encoding="utf-8")

    version_count = len(re.findall(r"\(version\s+\d+\)", text))

    entries: list[LibEntry] = []

    for m in LIB_ENTRY_RE.finditer(text):
        nickname, lib_type, uri, options, descr = m.groups()
        entries.append(
            LibEntry(
                nickname=nickname,
                uri=uri,
                descr=descr,
                lib_type=lib_type,
                options=options,
            )
        )

    if not entries:
        for m in LIB_ENTRY_RE_LEGACY.finditer(text):
            nickname, uri, descr = m.groups()
            entries.append(
                LibEntry(nickname=nickname, uri=uri, descr=descr or "")
            )

    return entries, version_count


def resolve_uri(uri: str, root: Path) -> Path:
    """Resolve a lib-table / 3D model URI (using ${KIPRJMOD} or ${MIKILAB}) to an absolute path."""
    resolved = uri.replace("${KIPRJMOD}", str(root)).replace("${MIKILAB}", str(root))
    return Path(resolved)


# ---------------------------------------------------------------------------
# Helpers shared by import_component.py / add_component.py / import_batch.py
# ---------------------------------------------------------------------------


def find_symbol_by_name(root: Path, name: str) -> Path | None:
    """Any symbols/**/<name>.kicad_sym, regardless of category."""
    for path in discover_symbol_libraries(root):
        if path.stem == name:
            return path
    return None


def find_footprint_by_hash(root: Path, digest: str) -> Path | None:
    for pretty in discover_footprint_libraries(root):
        for mod in pretty.glob("*.kicad_mod"):
            if sha256_file(mod) == digest:
                return mod
    return None


def find_footprints_by_basename(root: Path, basename: str) -> list[Path]:
    matches = []
    for pretty in discover_footprint_libraries(root):
        candidate = pretty / basename
        if candidate.exists():
            matches.append(candidate)
    return matches


_FOOTPRINT_PROP_SINGLELINE_RE = re.compile(
    r'(\(property\s+"Footprint"\s+)"([^"]*)"'
)
_FOOTPRINT_PROP_MULTILINE_RE = re.compile(
    r'(\(property\s*\n\s*"Footprint"\s*\n\s*)"([^"]*)"'
)


def set_symbol_footprint_property(text: str, new_value: str) -> tuple[str, bool]:
    """Replace the value of the (first) 'Footprint' property in a symbol
    file's text, supporting both single-line and multi-line KiCad property
    syntax. Returns (new_text, changed)."""

    if _FOOTPRINT_PROP_SINGLELINE_RE.search(text):
        new_text, n = _FOOTPRINT_PROP_SINGLELINE_RE.subn(
            lambda m: f'{m.group(1)}"{new_value}"', text, count=1
        )
        return new_text, n > 0

    if _FOOTPRINT_PROP_MULTILINE_RE.search(text):
        new_text, n = _FOOTPRINT_PROP_MULTILINE_RE.subn(
            lambda m: f'{m.group(1)}"{new_value}"', text, count=1
        )
        return new_text, n > 0

    return text, False


def unique_destination(dst: Path, digest: str) -> tuple[Path, str]:
    """Given a desired destination path and the sha256 of the source file,
    return (final_path, status) where status is one of NEW / DUPLICATE /
    RENAMED_COLLISION. Mirrors the collision policy used across the
    library: identical content is deduplicated, differing content gets a
    distinct suffixed name rather than silently overwriting."""

    if not dst.exists():
        return dst, "NEW"

    if sha256_file(dst) == digest:
        return dst, "DUPLICATE"

    stem = dst.stem
    suffix = dst.suffix
    index = 2

    while True:
        candidate = dst.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate, "RENAMED_COLLISION"
        if sha256_file(candidate) == digest:
            return candidate, "DUPLICATE"
        index += 1


MANIFEST_HEADER = ["type", "source", "destination", "status", "hash", "notes"]


def append_manifest_rows(root: Path, rows: list[list[str]]):
    import csv

    manifest = root / "MANIFEST.csv"
    is_new = not manifest.exists()

    with manifest.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(MANIFEST_HEADER)
        for row in rows:
            writer.writerow(row)
