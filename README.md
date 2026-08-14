# Yak-52 Fuel Consumption Data Gathering

Tooling to measure the real fuel burn of a Yak-52 (modified M-14P) per power
setting: a printable kneeboard sheet for in-flight notes, a scan-and-review
ingestion workflow, and a Bayesian estimator that sharpens the numbers with
every sortie flown.

The method: one sortie per power setting, **fill-to-full refuels on both
ends** (same pump, same spot, same brim reference), one steady 45–60 min test
block, and a kneeboard line per event. Every fuel-lamp *transition* is logged —
a lamp switch is a precise fuel-level fix, "which lamp is on" is not.

## Workflow

1. **Print** `kneeboard/kneeboard.pdf` — or `kneeboard/kneeboard.html` from a
   browser (A4 landscape, duplex "flip on **short** edge", 100 % scale, no
   margins). The short-edge flip is what keeps the backs upright behind the
   fronts. Cut along the marks → three 99 × 173 mm kneeboard cards per sheet.
2. **Fly** the sortie per the flight programme (`docs/sortie-schema.md`),
   filling the card as you go. Keep SkyDemon logging the whole time.
3. **Export** the SkyDemon logs. Only `.gpx` and `.txt` are needed
   (`.kml` and `.flightlog` are redundant/unreadable). Multiple files per
   flight (recording disconnects) are handled automatically.
4. **Drop** the scanned/photographed card and the SkyDemon files into
   `incoming/`, then run `/ingest-sortie` in Claude Code. The handwriting is
   machine-read, cross-checked against the GPS log, shown to you for
   correction, and stored as `data/sorties/<id>/sortie.yaml`.
5. The pipeline (`scripts/build_dataset.py` + `scripts/estimate.py`) rebuilds
   `data/master.csv` and `data/results.md` — burn-rate posteriors per power
   setting, lamp calibration, and planning values.

Scripts run on Python 3 with `numpy` and `pyyaml`. Optional:
`weasyprint` to render the kneeboard PDF without a browser
(`python3 scripts/render_kneeboard.py`).

## Target power settings

| Setting   | RPM     | MAP                  |
|-----------|---------|----------------------|
| Takeoff   | 99 ± 1 %| +125 ± 15 torr (above ambient) |
| Rated I   | 82 ± 1 %| +95 ± 15 torr        |
| Rated II  | 70 ± 1 %| +75 ± 15 torr        |
| Cruise I  | 64 ± 1 %| 735 ± 15 torr abs    |
| Cruise II | 59 ± 1 %| 670 ± 15 torr abs    |
| Extra A   | 70 %    | 700 torr abs         |
| Extra B   | 60 %    | 600 torr abs         |

## Kneeboard card size

Cards are 99 mm wide, not 100: three across an A4 landscape sheet
(297 mm) can be at most 99 mm each. Height is 173 mm (within the
170–175 mm kneeboard window).

## Data privacy

No flight data lives in this repository. `data/`, `incoming/`,
scans and personal analysis artifacts are gitignored; only templates,
scripts and documentation are published.

## Licence

- **Software** (`scripts/`, skill definitions): [EUPL-1.2](LICENSE)
- **Documentation and templates** (`docs/`, `kneeboard/`, this README):
  [CC-BY-SA-4.0](LICENSE-docs)
