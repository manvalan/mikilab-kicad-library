# MIKILAB KiCad Library

Personal, self-contained KiCad library for MIKILAB hardware projects.

This directory is fully autonomous: it does **not** depend on
`kicad-personal-library` or any other external directory. Every path used
inside `sym-lib-table`, `fp-lib-table` and the footprint 3D model
references is relative to this library (via `${KIPRJMOD}`), so the whole
folder can be moved, renamed, zipped, or synced to another machine without
breaking anything.

## Architecture

Every `.kicad_sym` file under `symbols/` is an **independent** KiCad
symbol library (there is deliberately no single monolithic
`MIKILAB.kicad_sym`). Every `.pretty` directory under `footprints/` is
likewise an independent KiCad footprint library. This mirrors how the
official KiCad libraries are structured and keeps components easy to find,
diff, and maintain individually.

```
mikylab_kikad_library/
├── sym-lib-table         # registers every symbols/**/*.kicad_sym
├── fp-lib-table          # registers every footprints/**/*.pretty
├── symbols/<category>/<Name>.kicad_sym
├── footprints/<category>/<Name>.pretty/<Name>.kicad_mod
├── 3dmodels/<category>/<Name>.step
├── docs/                 # reference documentation (incl. upstream KiCad docs)
├── legacy/                # legacy .lib/.dcm sources kept for reference
├── scripts/               # import & check tooling (this README's §5, §6)
├── MANIFEST.csv           # full provenance/status log, one row per file
└── README.md
```

Categories in use: `analog`, `audio`, `display`, `fpga_cpld`, `interface`,
`logic`, `mechanical`, `memory`, `microcontrollers`, `other`, `power`,
`rf`.

Every symbol/footprint library is registered under a `MIKILAB_<name>`
nickname, e.g. `MIKILAB_TPS7A2012PDBVR`, `MIKILAB_TPS7A2018PDBVR`,
`MIKILAB_Amplifier_Operational`, `MIKILAB_altera`. Nicknames are derived
automatically from the file/directory name and are guaranteed unique.

## 1. Installing the library

There are two ways to make this library available in KiCad. Which one you
want depends on whether you use it in one project or in every project.

### Option A -- one project only (no KiCad config changes)

`sym-lib-table` / `fp-lib-table` at the root of this repo use
`${KIPRJMOD}`, which KiCad automatically resolves to *the currently open
project's directory*. So if a `.kicad_pro` project lives directly inside
`mikylab_kikad_library/` (or you copy these two table files into your
project's directory), KiCad picks them up automatically -- no extra
configuration needed. This is the setup `check_library.py` and the
`scripts/` tooling assume.

### Option B -- available in every project (recommended for a personal library)

This is the practical setup for a library you want in every schematic you
open, not just one project. Tested against the KiCad 10.0 install on this
machine (`/Applications/KiCad/KiCad.app`, config at
`~/Library/Preferences/kicad/10.0/`).

1. **Define an environment variable pointing at this library.**
   KiCad → Preferences → Configure Paths... → add a new entry:
   - Name: `MIKILAB`
   - Path: `/Users/michelebigi/Development/mikylab_kikad_library`

2. **Generate the "global" table variant** (uses `${MIKILAB}` instead of
   `${KIPRJMOD}`; regenerate any time after adding components):
   ```
   python3 scripts/generate_global_tables.py
   ```
   This writes `sym-lib-table.global` and `fp-lib-table.global` at the
   library root.

3. **Merge those into KiCad's global tables.** The simplest way is to
   append their `(lib ...)` lines into your existing global tables (back
   them up first):
   ```
   cp ~/Library/Preferences/kicad/10.0/sym-lib-table ~/Library/Preferences/kicad/10.0/sym-lib-table.bak
   cp ~/Library/Preferences/kicad/10.0/fp-lib-table  ~/Library/Preferences/kicad/10.0/fp-lib-table.bak

   python3 - <<'EOF'
   import re
   from pathlib import Path

   kicad_dir = Path.home() / "Library/Preferences/kicad/10.0"
   lib_root = Path("/Users/michelebigi/Development/mikylab_kikad_library")

   for kind, global_file, generated in (
       ("sym_lib_table", "sym-lib-table", "sym-lib-table.global"),
       ("fp_lib_table", "fp-lib-table", "fp-lib-table.global"),
   ):
       target = kicad_dir / global_file
       new_libs = (lib_root / generated).read_text().splitlines()
       new_libs = [l for l in new_libs if l.strip().startswith("(lib")]

       text = target.read_text()
       # insert the new (lib ...) lines just before the final closing paren
       idx = text.rstrip().rfind(")")
       text = text.rstrip()[:idx] + "\n" + "\n".join(new_libs) + "\n" + text.rstrip()[idx:] + "\n"
       target.write_text(text)
       print(f"Merged {len(new_libs)} libraries into {target}")
   EOF
   ```
   (Or do it by hand: open both `.global` files and copy each `(lib
   ...)` line into the corresponding file under
   `~/Library/Preferences/kicad/10.0/`, just before the final closing
   `)`.)

4. Restart KiCad. Every `MIKILAB_*` symbol and footprint library is now
   available in any project, resolved via `${MIKILAB}`.

**Known issue on this machine, to clean up before deleting
`kicad-personal-library`:** the current global `sym-lib-table` already
has a handful of entries pointing directly at
`/Users/michelebigi/Development/kicad-personal-library/...` with absolute
paths (added before this library existed, e.g. libraries named `ti`,
`TPS63020DSJT`). Once the `MIKILAB_*` libraries above are installed and
working, remove those old absolute-path entries from
`~/Library/Preferences/kicad/10.0/sym-lib-table` -- otherwise you'll have
duplicate/stale libraries, and deleting `kicad-personal-library` will
leave KiCad with broken references. This library's own tables never
contain absolute paths (verified by `check_library.py` and by `grep -R
"/Users/michelebigi"`), so this cleanup is only about your existing
global KiCad config, not about anything in this repo.

## 2. Using the symbols

In the schematic editor, symbols are available as
`MIKILAB_<LibraryName>:<SymbolName>`, e.g.
`MIKILAB_TPS7A2012PDBVR:TPS7A2012PDBVR` or
`MIKILAB_Amplifier_Operational:LM358`.

## 3. Using the footprints

In the footprint assignment tool / PCB editor, footprints are available as
`MIKILAB_<LibraryName>:<FootprintName>`, e.g.
`MIKILAB_SOT95P280X145_5N:SOT95P280X145-5N`.

## 4. How 3D models are resolved

Footprints reference 3D models with `${MIKILAB}/3dmodels/<category>/<Name>.step`.
`MIKILAB` is the path variable defined in KiCad → Preferences → Configure
Paths (see §1, Option B), so models resolve from any project — no absolute
paths anywhere. (`${KIPRJMOD}` is *not* used for 3D models: it resolves to
the currently open project's folder, not to this library.)

**Standard KiCad 3D models:** footprints that use a body from KiCad's own
bundled 3D library reference it via `${KICAD10_3DMODEL_DIR}/...`, the
variable every KiCad 10 install defines. `check_library.py` verifies each
of those references against the local KiCad install
(`/Applications/KiCad/KiCad.app/Contents/SharedSupport/3dmodels`) when
it is present. References inherited from vendor libraries that used
obsolete variables (`${KICAD6_3DMODEL_DIR}`, `${KISYS3DMOD}`, ...) or
obsolete KiCad 5 folder names (`Housings_QFP`, `Socket_Strips`, ...) were
repointed at the current equivalent model -- these may need a small
offset/rotation tweak in the 3D viewer.

**Standard models matched by pad geometry:** 78 vendor footprints that
had no 3D model were given a body from KiCad's bundled library. Each
match was found by fitting the footprint's pads (number + position,
0/90/180/270° rotation) against KiCad's own footprints; only same-package
matches whose model file actually ships with KiCad were kept, with the
offset/rotation computed from the fit and checked visually with
`kicad-cli pcb render`. A few use a near-equivalent body (e.g. Altera
EQFP-144 → LQFP-144, TI DGN → MSOP-8-EP, Taiyo NR6028 → FNR6028S,
Coilcraft LPS4018 → TechFuse SL0420). The remaining footprints without a
model are listed in `docs/footprint_senza_modello_3d.csv`.

**Removed dead 3D references:** a set of vendor-imported footprints
referenced 3D files that were never present locally (`${KISBLIB}/...`,
`${KICAD8_3RD_PARTY}/...`, relative paths, or models no longer shipped
with KiCad). Those `(model ...)` entries were removed, since they could
never resolve; the footprints themselves (pads, courtyard, silkscreen)
are unchanged and fully usable. The original references are listed in
`docs/removed_3d_model_refs.csv` should you want to source the models
and re-add them.

**Special case — shared footprint, distinct 3D bodies:** `SOT95P280X145-5N`
is used by both `MIKILAB_TPS7A2012PDBVR` and `MIKILAB_TPS7A2018PDBVR`.
The footprint itself was verified byte-identical between the two parts
(SHA256 comparison showed the only difference was KiCad's internal
`tedit` timestamp), so a single shared footprint library is used. Their
3D bodies are genuinely different STEP files, though
(`3dmodels/power/TPS7A2012PDBVR.step` vs. `.../TPS7A2018PDBVR.step`), and
a `.kicad_mod` can only carry one embedded `(model ...)` reference — so
neither is embedded by default. If you want a 3D render for one of these
parts, assign the STEP file manually per footprint instance (PCB editor →
right-click footprint → Properties → 3D Models).

## 5. Adding a component

Simplest path — one command:

```
python3 scripts/add_component.py \
    --name TPS7A2018PDBVR \
    --symbol /path/to/TPS7A2018PDBVR.kicad_sym \
    --footprint /path/to/SOT95P280X145-5N.kicad_mod \
    --model /path/to/TPS7A2018PDBVR.step \
    --category power
```

`--footprint` and `--model` are optional — you can import a symbol-only
component, or symbol+footprint without a 3D model. `--category` is
optional too; it's auto-detected from `--name` using the same rules used
throughout this library (falls back to `other`).

The importer:
- refuses to overwrite a component that already exists (by name), with a
  clear error and no changes made;
- deduplicates footprints by content (SHA256), not filename — if the
  footprint you're importing is byte-identical to one already in the
  library, the existing one is reused instead of creating a duplicate;
- if a *different* footprint happens to share a filename with an existing
  one, it is imported under a distinct, semantically-derived name and the
  collision is recorded in `MANIFEST.csv`;
- rewrites the symbol's `Footprint` property to point at the correct new
  `MIKILAB_<lib>:<name>` reference;
- links the 3D model into the footprint (unless the footprint was reused
  from an existing shared library — see §4's shared-footprint case);
- regenerates `sym-lib-table` and `fp-lib-table` from scratch by scanning
  the directory tree, so there is never more than one `(version 7)` entry
  and every library on disk is registered exactly once;
- appends a row per file to `MANIFEST.csv` (columns: `type`, `source`,
  `destination`, `status`, `hash`, `notes`; status is one of `NEW`,
  `DUPLICATE`, `RENAMED_COLLISION`, `ERROR`, `UNCHANGED`).

`import_component.py` is the same tool with a more explicit/verbose CLI —
`add_component.py` just calls into it. For importing many components at
once, lay them out one subdirectory per component and run:

```
python3 scripts/import_batch.py --source /path/to/batch_dir [--category power]
```

(subdirectory name = component `--name`; exactly one `.kicad_sym` per
subdirectory required, footprint/model optional — same collision and
lib-table rules as a single import, applied per component).

To import directly from a SnapEDA (SnapMagic Search) "Download KiCad"
zip, without unzipping it by hand first:

```
python3 scripts/import_snapeda.py --zip ~/Downloads/PARTNUMBER.zip [--name ...] [--category ...]
```

It unzips to a temp directory, locates the `.kicad_sym` / `.kicad_mod` /
3D model inside by extension (SnapEDA's internal folder layout varies
between downloads), and hands off to the same
`import_component.py` core -- identical validation, collision handling,
and lib-table regeneration as every other import path here.

## 6. Running the check

```
python3 scripts/check_library.py
```

Verifies: directory structure; symbol/footprint syntax and duplicates;
real filename collisions (by content hash, not just name); 3D model
reference validity and portability; `sym-lib-table`/`fp-lib-table`
syntax, single `(version 7)`, no missing/duplicate/unregistered entries;
and symbol → footprint cross-references for every MIKILAB-owned library.
Exits 0 (`RESULT: OK`) iff there are no errors. Warnings are pre-existing,
documented, non-fatal gaps (see §4).

Note: many symbols mirrored from the official KiCad symbol libraries
reference *standard* KiCad footprint libraries (e.g. `Package_SO`,
`RF_Module`) by their upstream nickname — those are outside MIKILAB's
scope (they ship with every KiCad install) and are not checked or
reported as errors.

### TS3425UA-3x4x2.5-160 (Chuangdou SMD tactile switch)

Symbol + footprint reconstructed the same way as FSC-BT1035, from the
same `manvalan/DigiRadio` EasyEDA project (4 `PAD` + `POLY` records for
the footprint, 4 `PIN` records for the symbol). Cross-checked against
the manufacturer's own datasheet (found via the same project's
`Hardware/DATASHEET/TS3425UA.pdf`): body 4.2 x 3.4mm, actuator 3.0 x 2.5
x 1.9mm, 160gf actuation, and critically its own circuit diagram, which
shows the 4 legs as **two independent, mechanically-ganged contact
pairs** (pins ①-② and ③-④, both closing together on a single press) --
the symbol here reproduces that same two-switch depiction rather than
inventing a simplified SPST. Pin numbers were preserved exactly as
extracted from the source (kept internally consistent between the
symbol and footprint, i.e. schematic pin "N" always nets to footprint
pad "N" -- their absolute position on the page doesn't need to match
the datasheet's own circle-number diagram for this to be electrically
correct, since a symmetric ganged switch has no "wrong side"). No 3D
model included.

### FSC-BT1035 (Feasycom Bluetooth module)

`symbols/rf/FSC-BT1035.kicad_sym` (Feasycom Bluetooth 5.2 dual-mode
stereo audio module, Qualcomm QCC3056) was hand-authored from the
official [Feasycom datasheet](https://www.feasycom.com/datasheet/fsc-bt1035.pdf)
Table 3-2 (Pin definition) -- all 52 pins, names and electrical types
transcribed directly from that table and validated with `kicad-cli sym
export`.

`footprints/rf/FSC-BT1035.pretty/FSC-BT1035.kicad_mod` was reconstructed
from the actual PCB footprint in `manvalan/DigiRadio`'s EasyEDA Pro
project (a fabricated, gerber-verified design) by extracting the raw pad
records from that project's internal document format (JSON-Lines with
`PAD`/`POLY` records per document; the relevant footprint document was
located, isolated, and parsed directly -- KiCad's own `epro2kicad`
converter couldn't be used as-is, since this newer EasyEDA Pro export
bundles all documents into one multi-record file rather than the
per-document files it expects). Notably, that project's component was
originally sourced from a SnapEDA export for the mechanically-compatible
**FSC-BT806** (same manufacturer, same 13 x 26.9mm 52-pad castellated
family package used across BT806/BT1026/BT1035/BT1038) and relabeled
`FSC-BT1035` by the designer; the extracted pad size (1.6 x 0.6mm) and
pitch (1.0mm) match the official FSC-BT1035 datasheet's own mechanical
spec exactly, and the pad numbering follows a single clean perimeter
walk (left column top-to-bottom, bottom row left-to-right, right column
bottom-to-top) identical to the datasheet's own Table 3-2 pin order, so
`pad N` = `pin N` throughout. Rounded body corners were simplified to
straight lines in the silkscreen (cosmetic only, no pad geometry
affected). No 3D model is included.

## 7. Programmatic API (for integrating into other apps)

For apps that want to add/update/remove/query components without
shelling out to `add_component.py` and parsing its human-readable
report, there are two entry points on top of the exact same
`import_component.py` core logic (same validation, collision handling,
and lib-table regeneration as every path in section 5):

- **`scripts/mikilab_lib.py`** — a typed Python API, for Python apps to
  import directly:

  ```python
  import sys
  sys.path.insert(0, "/path/to/mikylab_kikad_library/scripts")
  import mikilab_lib as mikilab

  result = mikilab.add_component(
      name="TPS7A2018PDBVR",
      symbol="/path/TPS7A2018PDBVR.kicad_sym",
      footprint="/path/SOT95P280X145-5N.kicad_mod",   # optional
      model="/path/TPS7A2018PDBVR.step",               # optional
      category="power",                                 # optional, auto-detected if omitted
  )
  # -> ComponentResult(name=..., category=..., action="add", symbol_path=..., ...)

  mikilab.update_component(name="TPS7A2018PDBVR", symbol=..., footprint=...)  # upsert: replaces in place
  mikilab.remove_component(name="TPS7A2018PDBVR")

  mikilab.get_component("TPS7A2018PDBVR")        # -> ComponentInfo | None
  mikilab.list_components(category="power")       # -> list[ComponentInfo]
  mikilab.find_components("tps22")                # -> list[ComponentInfo], substring match
  ```

  Failures raise `mikilab.MikilabError` (a single exception type, message
  safe to show to a user or log as-is) instead of printing and exiting.

- **`scripts/mikilab_cli.py`** — the same operations exposed as a stable
  JSON-over-stdout CLI, for apps in Swift, C++, or anything else that
  can spawn a subprocess:

  ```
  python3 scripts/mikilab_cli.py add --name TPS7A2018PDBVR \
      --symbol /path/TPS7A2018PDBVR.kicad_sym \
      --footprint /path/SOT95P280X145-5N.kicad_mod --category power

  python3 scripts/mikilab_cli.py update --name TPS7A2018PDBVR --symbol /path/...
  python3 scripts/mikilab_cli.py remove --name TPS7A2018PDBVR
  python3 scripts/mikilab_cli.py get    --name TPS7A2018PDBVR
  python3 scripts/mikilab_cli.py list   [--category power]
  python3 scripts/mikilab_cli.py find   --query tps22 [--category power]
  ```

  Every invocation prints exactly one JSON object to stdout —
  `{"ok": true, "data": ...}` or `{"ok": false, "error": "..."}` — and
  exits 0 iff `"ok"` is `true`. From Swift: run it via `Process`, decode
  stdout with `JSONDecoder`. From C++: `popen`/`posix_spawn` plus any
  JSON library (e.g. `nlohmann::json`).

Both entry points regenerate `sym-lib-table`, `fp-lib-table`,
`sym-lib-table.global` and `fp-lib-table.global`, and append to
`MANIFEST.csv`, on every mutating call — same guarantees as
`add_component.py`. Neither touches KiCad's real global tables under
`~/Library/Preferences/kicad/*/` — if this library is registered
globally (section 1), merging new entries there is still a separate,
deliberate step (section 1, step 3), so that an app driving this API
never silently rewrites your live KiCad configuration.

`update_component()`/`update` does not move a component between
categories — recategorizing means `remove` + `add` under the new
category.

## Provenance

`MANIFEST.csv` has one row per file in the library (`type`, `source`,
`destination`, `status`, `hash`, `notes`). Rows from the initial bulk
import are marked `UNCHANGED`/baseline; rows added by
`import_component.py` / `add_component.py` / `import_batch.py` record
exactly what happened during that import (new file, deduplicated,
renamed due to a real collision, or error).

### Components imported from FreeDSP

The following `audio`/`analog` category chips were imported from
[FreeDSP_ki-CAD_Libraries](https://github.com/freeDSP/FreeDSP_ki-CAD_Libraries)
(legacy KiCad `.lib`/`.dcm` format, converted to modern `.kicad_sym` via
`kicad-cli sym upgrade`): `ADAU1467WBCPZ300RL`, `PCM1808QPWRQ1`,
`PCM9211PTR`, `Combo384`, `MW-1466CORE` (FreeDSP's ADAU1466 core module),
`LME49720MA`, `CS8421-CZZ`, `PCM1861DBT`, `FDC608PZ`, `IMN10T108`,
`AK5384`. `PCM1681-Q1` was skipped (it's a KiCad `extends` variant of
`PCM1681` and can't be split into a standalone file without duplicating
`PCM1681`'s graphics -- import `PCM1681` if you need the automotive
variant, base symbol is identical for schematic purposes).

Notable fixes applied during that import (see `MANIFEST.csv` notes for
the affected files): `PCM1808QPWRQ1`'s footprint had an absolute 3D model
path pointing at the upstream maintainer's own machine
(`/Users/HILO/...`); `ADAU1467WBCPZ300RL` and `PCM9211PTR`'s footprints
had two dead 3D model references each (`${KICAD_USER_TEMPLATE_DIR}` and a
bare filename). All three were corrected to `${KIPRJMOD}`-relative
references pointing at the STEP files now under `3dmodels/audio/`.
`LME49720MA` was imported symbol-only, with its `Footprint` property
repointed at this library's existing `MIKILAB_ipc_soic:IPC_SOIC127P600X175-8N`
(an exact IPC-standard match already present) instead of duplicating a
generic SOIC-8 footprint. Several FreeDSP parts already covered by the
official mirrored libraries (`PCM5102A`, `INA194`, `AZ1117-3.3`,
`ADAU1452`) were intentionally *not* re-imported.

### Si4684-A10-GM (unmerged upstream MR, use with care)

`symbols/audio/Si4684-A10-GM.kicad_sym` (Silicon Labs/Skyworks
single-chip FM/DAB/DAB+ radio receiver) was extracted from
[kicad-symbols MR !4782](https://gitlab.com/kicad/libraries/kicad-symbols/-/merge_requests/4782),
which as of this import is **open and unmerged** (`cannot_be_merged`,
has conflicts, failing CI pipeline, tagged "needs v9 format upgrade").
It is not part of the official kicad-symbols release this library
otherwise mirrors. Before importing it here it was independently
verified with `kicad-cli sym export` (parses correctly) and checked for
48 unique, non-duplicate pins matching its QFN-48 package. Symbol only
-- its `Footprint` property points at the standard
`Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.3x5.3mm` (resolved via your
global KiCad footprint table, same as other official-mirror symbols),
no MIKILAB-local footprint or 3D model exists for it. Given its
provenance, its pin *names* were cross-checked against the public
[Skyworks product summary](https://www.skyworksinc.com/-/media/SkyWorks/SL/documents/public/product-summaries/Si4684-A10_PS.pdf)
-- all 24 signal names in that document's block diagram (RFREF, VHFI,
VHFSW, LOUT, ROUT, DCLK, DFS, DOUT, SCLK, SSB, MISO, MOSI, VIO, VA,
VCORE, VMEM, XTALI, XTALO, NVSCLK, NVSSB, NVMOSI, NVMISO, INTB, RSTB)
are present with sensible pin numbers, and the remaining pins
(ABYP/DACREF/DBYP bypass, SMODE, 4x GNDD, several NC) are consistent
with a 48-pin QFN. That 4-page public summary does **not** include a
full numbered pinout table, though (Skyworks gates the full Si468x
datasheet behind an NDA). As a second, independent cross-check, the pin
numbering was compared against the `SI4684`/`SI4684 SMD` symbols in
[PE5PVB/SI4684-DAB-Receiver](https://github.com/PE5PVB/SI4684-DAB-Receiver)
(a built, gerber-fabricated real project) -- of the 44 pin
numbers directly comparable (PE5PVB's symbol also carries an unrelated
second IC's pins under duplicate numbers, and its own naming has 4 clear
typos: `NVCLK`/`DSF`/`MVMISO`/`NVMOS` vs. the grammatically-consistent
`NVSCLK`/`DFS`/`NVMISO`/`NVMOSI`), **all 44 agree exactly** with the
symbol imported here. That two independent, unrelated sources converge
on the same pin map is reasonably strong (if still not authoritative)
confirmation; verify against the full NDA'd datasheet if you have access
before finalizing a schematic around this part.

### Espressif modules

The **complete** official
[espressif/kicad-libraries](https://github.com/espressif/kicad-libraries)
library (snapshot `Espressif.kicad_sym` version `20251024`) is mirrored
here: all 49 symbols, all 54 footprints, and every 3D model any of
those footprints references (29 of upstream's 30 STEP files -- see
"3D model references" below). Following this library's
one-library-per-part architecture, the
single upstream `Espressif.kicad_sym` was split into 49 independent
`symbols/microcontrollers/<part>.kicad_sym` files rather than imported
whole. Parts whose name contains a `/` upstream use `_` on disk
(`ESP32-C6-MINI-1/U` -> `ESP32-C6-MINI-1_U.kicad_sym`).

The import covers three kinds of part:

- **Modules** (`ESP32-S3-WROOM-1`, `ESP32-C6-MINI-1/U`, `ESP32-P4`,
  `ESP32-PICO-MINI-02`, `ESP8685-WROOM-06`, ...) -- symbol + its own
  footprint library, plus a STEP model where upstream provides one.
- **DevKit boards** (`ESP32-DevKitC`, `ESP32-S3-DevKitC`,
  `ESP32-S2-Saola-1`, ...) -- symbol + the board-outline/header
  footprint you'd use to carry the devkit on a motherboard. Upstream
  ships no 3D models for these.
- **Bare SoCs/dies** (`ESP32`, `ESP32-C3`, `ESP8266`, `ESP32-C6FH4`,
  ...) -- symbol only. These 12 deliberately keep their upstream
  `Package_DFN_QFN:QFN-...` footprint reference, which resolves against
  the *stock* KiCad footprint library that ships with every install;
  duplicating those QFN footprints into MIKILAB would serve no purpose
  (see the note in section 6 about external library references).

Two details worth knowing:

- `ESP32-P4X` shares `ESP32-P4`'s footprint byte-for-byte, so the
  importer's content-hash deduplication reused it: both symbols point at
  `MIKILAB_ESP32_P4:ESP32-P4` and there is no `ESP32-P4X.pretty`.
- `ESP8685-Wroom-05` is a footprint with no symbol upstream. It is kept
  as a footprint-only library, `MIKILAB_ESP8685_WROOM_05`.

`ESP32-S31-WROOM-3` **replaces** an earlier `easyeda2kicad.py`-exported
version that had a fatal unquoted-URL syntax error in `(generator ...)`
(see the earlier commit fixing that bug) -- the official symbol/footprint
is used now instead.

#### Footprint variants

Upstream ships several footprints per module family, not just one. All
of them are present, sitting together in the `.pretty` of the part they
belong to (a `.pretty` is a footprint *library*, so it holds as many
footprints as the part has variants):

- **`...U` antenna variants** -- the U-suffixed part number (external
  antenna connector instead of a PCB antenna), e.g.
  `MIKILAB_ESP32_C3_MINI_1` contains both `ESP32-C3-MINI-1` and
  `ESP32-C3-MINI-1U`.
- **`..._HandSoldering`** -- same part, pads lengthened for hand
  assembly (`ESP32-C3-MINI-1`, `ESP32-S2-MINI-1`, `ESP32-PICO-MINI-02`).
- **`ESP32-WROVER-E_ThermalVias`** -- WROVER-E with the thermal via
  array under the module.
- **`ESP32-S2-SOLO-2U`** and **`ESP32-WROOM-32UE`** -- the U variants of
  `ESP32-S2-SOLO` / `ESP32-WROOM-E`, which live in those parts'
  libraries.

#### 3D model references

Every upstream footprint references its 3D model via
`${KICAD8_3RD_PARTY}` / `${KICAD9_3RD_PARTY}` -- the path KiCad's Plugin
and Content Manager uses when a library is installed through it. This
library is not PCM-installed, so that variable is never defined here and
every such reference was repointed to
`${KIPRJMOD}/3dmodels/microcontrollers/` alongside the STEP files copied
in, so models resolve with no PCM and no extra KiCad configuration.
Models shared between variants (e.g. a HandSoldering footprint uses the
same body as its normal-pad sibling) are stored once and reused, not
duplicated.

The one exception is `ESP32-H2`, whose upstream footprint is a copy of
the generic `QFN-32-1EP_4x4mm_P0.4mm_EP2.9x2.9mm` package and points at
KiCad's own bundled model. That reference was rewritten to
`${KISYS3DMOD}/Package_DFN_QFN.3dshapes/...`, the standard KiCad
variable for stock 3D models, rather than copying a stock asset in.

Two upstream 3D assets are deliberately **not** mirrored, because no
footprint references them and every model in `3dmodels/` is otherwise
guaranteed to be reachable from a footprint:

- `ESP32-WROOM-32E_No-Cover.STEP` -- a cutaway render of the WROOM-32E
  with its shield lid removed, for illustration rather than assembly.
- the 8 `.wrl` files, which are lower-fidelity duplicates of STEP bodies
  already imported (upstream's own footprints point at the `.STEP`).

Copy either in by hand and add the `(model ...)` line if you want them.

## Source safety

This library was built by copying (never moving) from external source
repositories; none of those repositories are modified by anything in
`scripts/`, and nothing in this library references them. In particular,
this directory does not depend on `kicad-personal-library` in any way —
verify at any time with:

```
grep -R "kicad-personal-library" .
```

which is expected to return no matches outside of historical mentions in
this README/docs.
