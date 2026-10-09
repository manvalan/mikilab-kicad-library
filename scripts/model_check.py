#!/usr/bin/env python3
"""
model_check.py
==============

Sanity check of a footprint's 3D model placement/orientation, without
KiCad: the STEP body's bounding box, transformed exactly like KiCad does
with the footprint's (offset)(scale)(rotate), is compared with the
footprint's own outline (F.CrtYd, else F.Fab, else the pads).

Detected:
  - model turned 90 deg (body long in X, footprint long in Y or vice versa)
  - model shifted off the footprint (centre too far / sticking out of the outline)
  - model below the board or upside down (top under z=0), SMD body sunk into
    the board (THT parts may legitimately go below z=0)
  - wrong units / scale (body much bigger or smaller than the footprint)
NOT detectable: a 180 deg rotation of a symmetric body (pin 1 at the
wrong corner) -- that still needs a look in KiCad's 3D viewer.

For that, --render DIR writes an isometric KiCad render (kicad-cli pcb
render) of each footprint with its model, to check pin 1 by eye.

Usage:
    python3 scripts/model_check.py footprints/rf/X.pretty/Y.kicad_mod [...] [--render DIR]
    python3 scripts/model_check.py --all            # every MIKILAB footprint with a STEP model
"""

from __future__ import annotations

import argparse
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lib_common as lc

ROOT = lc.LIBRARY_ROOT

# ----------------------------------------------------------- s-expressions

_TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()"]+')


def parse_sexpr(text: str):
    stack: list[list] = [[]]
    for tok in _TOKEN_RE.findall(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            if len(stack) > 1:
                done = stack.pop()
                stack[-1].append(done)
        else:
            stack[-1].append(tok[1:-1] if tok.startswith('"') else tok)
    return stack[0][0] if stack[0] else []


def children(node, name):
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == name]


def child(node, name):
    found = children(node, name)
    return found[0] if found else None


def floats(node, n):
    try:
        return [float(v) for v in node[1:1 + n]]
    except (TypeError, ValueError, IndexError):
        return None


# ------------------------------------------------------------- footprint


def _layer_of(node) -> str | None:
    lay = child(node, "layer")
    return lay[1] if lay and len(lay) > 1 else None


def outline_bbox(fp) -> tuple[str, tuple[float, float, float, float]] | None:
    """(source, (xmin, ymin, xmax, ymax)) in footprint coordinates (y down)."""
    for layer in ("F.CrtYd", "F.Fab"):
        pts = []
        for el in fp[1:]:
            if not isinstance(el, list) or not el or _layer_of(el) != layer:
                continue
            if el[0] == "fp_circle":
                c, e = floats(child(el, "center") or [], 2), floats(child(el, "end") or [], 2)
                if c and e:
                    r = math.dist(c, e)
                    pts += [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)]
                continue
            for key in ("start", "end", "mid"):
                p = floats(child(el, key) or [], 2)
                if p:
                    pts.append(tuple(p))
            pts_node = child(el, "pts")
            if pts_node:
                pts += [tuple(floats(xy, 2)) for xy in children(pts_node, "xy") if floats(xy, 2)]
        if len(pts) >= 2:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            return layer, (min(xs), min(ys), max(xs), max(ys))

    pads = []
    for pad in children(fp, "pad"):
        at, size = floats(child(pad, "at") or [], 2), floats(child(pad, "size") or [], 2)
        if not at or not size:
            continue
        rot = child(pad, "at")
        angle = float(rot[3]) if len(rot) > 3 else 0.0
        w, h = size if round(angle / 90) % 2 == 0 else size[::-1]
        pads += [(at[0] - w / 2, at[1] - h / 2), (at[0] + w / 2, at[1] + h / 2)]
    if pads:
        xs, ys = [p[0] for p in pads], [p[1] for p in pads]
        return "pads", (min(xs), min(ys), max(xs), max(ys))
    return None


def is_tht(fp) -> bool:
    return any(len(p) > 2 and p[2] in ("thru_hole", "np_thru_hole") for p in children(fp, "pad"))


# ------------------------------------------------------------------ STEP

_POINT_RE = re.compile(r"#(\d+)\s*=\s*CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(([^)]*)\)\s*\)", re.I)
_VERTEX_RE = re.compile(r"VERTEX_POINT\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*\)", re.I)


def step_bbox(path: Path) -> tuple[tuple[float, ...], bool] | None:
    """(xmin, ymin, zmin, xmax, ymax, zmax) in mm, and whether the file is an
    assembly with placement transforms (bbox then only approximate)."""
    text = path.read_text(encoding="latin-1", errors="replace")
    points = {}
    for pid, coords in _POINT_RE.findall(text):
        try:
            xyz = [float(v) for v in coords.split(",")]
        except ValueError:
            continue
        if len(xyz) == 3:
            points[pid] = xyz
    vertex_ids = _VERTEX_RE.findall(text)
    used = [points[i] for i in vertex_ids if i in points] or list(points.values())
    if not used:
        return None

    scale = 1.0
    if re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'INCH'", text, re.I):
        scale = 25.4
    elif re.search(r"SI_UNIT\s*\(\s*\$\s*,\s*\.METRE\.\s*\)", text, re.I):
        scale = 1000.0
    lo = [min(p[i] for p in used) * scale for i in range(3)]
    hi = [max(p[i] for p in used) * scale for i in range(3)]
    assembly = len(re.findall(r"ITEM_DEFINED_TRANSFORMATION", text, re.I)) > 1
    return (*lo, *hi), assembly


def _rot(p, axis, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    x, y, z = p
    if axis == "x":
        return (x, y * c - z * s, y * s + z * c)
    if axis == "y":
        return (x * c + z * s, y, -x * s + z * c)
    return (x * c - y * s, x * s + y * c, z)


def transform_bbox(bbox, scale, rotate, offset):
    """Apply KiCad's model transform (scale, then rotations X,Y,Z with
    negated angles, then offset in mm) to the 8 bbox corners; return the
    footprint-space bbox (x, y with y down) and z range."""
    xs, ys, zs = [], [], []
    for x in (bbox[0], bbox[3]):
        for y in (bbox[1], bbox[4]):
            for z in (bbox[2], bbox[5]):
                p = (x * scale[0], y * scale[1], z * scale[2])
                p = _rot(p, "x", -rotate[0])
                p = _rot(p, "y", -rotate[1])
                p = _rot(p, "z", -rotate[2])
                p = (p[0] + offset[0], p[1] + offset[1], p[2] + offset[2])
                xs.append(p[0]); ys.append(-p[1]); zs.append(p[2])
    return (min(xs), min(ys), max(xs), max(ys)), (min(zs), max(zs))


# ------------------------------------------------------------------ check


def resolve_model(uri: str) -> Path | None:
    if "${MIKILAB}" in uri or "${KIPRJMOD}" in uri:
        return lc.resolve_uri(uri, ROOT)
    p = Path(uri)
    return p if p.is_absolute() else None


def check_footprint(fp_path: Path) -> tuple[str, list[str]]:
    """Return (status, messages); status is OK / WARN / SKIP."""
    fp = parse_sexpr(fp_path.read_text(encoding="utf-8", errors="replace"))
    model = child(fp, "model")
    if model is None:
        return "SKIP", ["no 3D model"]
    path = resolve_model(model[1])
    if path is None or not path.is_file():
        return "SKIP", [f"model not found locally: {model[1]}"]
    if path.suffix.lower() not in (".step", ".stp"):
        return "SKIP", [f"only STEP models are checked ({path.name})"]

    def xyz(key, default):
        node = child(model, key)
        v = floats(child(node, "xyz") or [], 3) if node else None
        return v or default

    scale, rotate, offset = xyz("scale", [1, 1, 1]), xyz("rotate", [0, 0, 0]), xyz("offset", [0, 0, 0])
    sb = step_bbox(path)
    outline = outline_bbox(fp)
    if sb is None or outline is None:
        return "SKIP", ["could not read model geometry or footprint outline"]
    raw, assembly = sb
    (mx0, my0, mx1, my1), (mz0, mz1) = transform_bbox(raw, scale, rotate, offset)
    src, (fx0, fy0, fx1, fy1) = outline

    issues = []
    mw, mh, fw, fh = mx1 - mx0, my1 - my0, fx1 - fx0, fy1 - fy0
    tol = max(0.5, 0.1 * max(fw, fh))

    if mz1 <= 0.05:
        issues.append(f"model top at z={mz1:.2f} mm: below the board / upside down (check rotate X/Y)")
    elif not is_tht(fp) and mz0 < -0.3:
        issues.append(f"SMD body reaches z={mz0:.2f} mm below the board surface (check offset Z / rotate X)")

    if min(mw, mh) > 0 and min(fw, fh) > 0:
        model_ratio, fp_ratio = mw / mh, fw / fh
        if (model_ratio > 1.3 and fp_ratio < 1 / 1.3) or (model_ratio < 1 / 1.3 and fp_ratio > 1.3):
            issues.append(f"model {mw:.2f}x{mh:.2f} mm vs {src} {fw:.2f}x{fh:.2f} mm: probably turned 90 deg (rotate Z)")
        area = (mw * mh) / (fw * fh)
        if area > 6 or area < 1 / 25:
            issues.append(f"model footprint area is {area:.2g}x the {src} outline: wrong units/scale or wrong model")

    dx, dy = (mx0 + mx1) / 2 - (fx0 + fx1) / 2, (my0 + my1) / 2 - (fy0 + fy1) / 2
    if math.hypot(dx, dy) > tol:
        issues.append(f"model centre off by ({dx:+.2f}, {dy:+.2f}) mm from the {src} centre (check offset)")
    overhang = max(fx0 - mx0, mx1 - fx1, fy0 - my0, my1 - fy1)
    if src == "F.CrtYd" and overhang > tol:
        issues.append(f"model sticks out of the courtyard by {overhang:.2f} mm")

    if not issues:
        return "OK", [f"model inside {src} (z {mz0:.2f}..{mz1:.2f} mm)"]
    if assembly:
        issues.append("(STEP assembly with placement transforms: bbox is approximate)")
    return "WARN", issues


KICAD_CLI_CANDIDATES = (
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    "C:/Program Files/KiCad/bin/kicad-cli.exe",
)

_BOARD_LAYERS = (
    '(layers (0 "F.Cu" signal) (31 "B.Cu" signal) (34 "B.Paste" user) (35 "F.Paste" user) '
    '(36 "B.SilkS" user) (37 "F.SilkS" user) (38 "B.Mask" user) (39 "F.Mask" user) '
    '(44 "Edge.Cuts" user) (46 "B.CrtYd" user) (47 "F.CrtYd" user) (48 "B.Fab" user) (49 "F.Fab" user))'
)


def find_kicad_cli() -> str | None:
    return shutil.which("kicad-cli") or next((c for c in KICAD_CLI_CANDIDATES if Path(c).is_file()), None)


def render_footprint(fp_path: Path, out_png: Path) -> bool:
    """Isometric 3D render of the footprint alone on a small board."""
    cli = find_kicad_cli()
    if cli is None:
        return False
    fp_text = fp_path.read_text(encoding="utf-8", errors="replace")
    fp_text = re.sub(r'\(layer\s+"?F\.Cu"?\)', '(layer "F.Cu") (at 100 100)', fp_text, count=1)
    outline = outline_bbox(parse_sexpr(fp_text))
    half = 5.0
    if outline:
        _, (x0, y0, x1, y1) = outline
        half = max(5.0, abs(x0), abs(x1), abs(y0), abs(y1)) * 1.3
    board = (
        f'(kicad_pcb (version 20240108) (generator "model_check") (general (thickness 1.6)) '
        f'(paper "A4") {_BOARD_LAYERS} (setup) (net 0 "")\n{fp_text}\n'
        f'(gr_rect (start {100 - half} {100 - half}) (end {100 + half} {100 + half}) '
        f'(stroke (width 0.1) (type solid)) (fill none) (layer "Edge.Cuts")))\n'
    )
    out_png.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="model_render_") as tmp:
        pcb = Path(tmp) / "render.kicad_pcb"
        pcb.write_text(board, encoding="utf-8")
        proc = subprocess.run(
            [cli, "pcb", "render", "-D", f"MIKILAB={ROOT}", "--rotate", "-45,0,45",
             "--zoom", "3", "-w", "900", "-h", "600", "-o", str(out_png), str(pcb)],
            capture_output=True, text=True, timeout=120,
        )
    return proc.returncode == 0 and out_png.is_file()


def main() -> int:
    parser = argparse.ArgumentParser(description="Check 3D model placement/orientation of footprints.")
    parser.add_argument("footprints", nargs="*", help=".kicad_mod files")
    parser.add_argument("--all", action="store_true", help="check every MIKILAB footprint")
    parser.add_argument("--render", metavar="DIR", help="also write an isometric PNG per footprint into DIR")
    args = parser.parse_args()

    paths = [Path(p) for p in args.footprints]
    if args.all:
        paths += sorted(p for d in lc.discover_footprint_libraries(ROOT) for p in d.glob("*.kicad_mod"))
    counts = {"OK": 0, "WARN": 0, "SKIP": 0}
    for p in paths:
        status, msgs = check_footprint(p)
        counts[status] += 1
        if args.render and status != "SKIP":
            png = Path(args.render) / f"{p.stem}.png"
            msgs.append(f"render: {png}" if render_footprint(p, png) else "render: kicad-cli not available/failed")
        if status == "WARN" or not args.all:
            rel = p.resolve().relative_to(ROOT) if p.resolve().is_relative_to(ROOT) else p
            print(f"{status:4} {rel}")
            for m in msgs:
                print(f"       {m}")
    print(f"\nOK {counts['OK']}, WARN {counts['WARN']}, SKIP {counts['SKIP']}")
    return 1 if counts["WARN"] else 0


if __name__ == "__main__":
    sys.exit(main())
