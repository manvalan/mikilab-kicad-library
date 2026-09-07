# Source

Imported from [kicad-castellated-breakouts](https://github.com/coddingtonbear/kicad-castellated-breakouts)
(zip snapshot, 2017-12-22).

This folder is reference material only -- full KiCad board projects meant
to be sent to a fab to produce ready-made castellated breakout adapters
for fine-pitch QFP/SSOP packages. It is not part of this library's
symbol/footprint/3D-model structure (no `sym-lib-table`/`fp-lib-table`
entries reference anything here).

The three footprints extracted from this project's `.pretty` library
(`QFP48-1.27MM-CASTELLATED`, `QFP64-1.27MM-CASTELLATED`,
`SSOP8-1.27MM-CASTELLATED`) were imported separately into
`footprints/other/castellated_breakouts.pretty/` and are registered as
`MIKILAB_castellated_breakouts` in `fp-lib-table`. See `readme.md` in
this folder (the upstream project's own README) for pin-pitch/package
compatibility details and OshPark links for each board.
