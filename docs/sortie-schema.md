# Sortie record schema (`sortie.yaml`)

One directory per sortie under `data/sorties/YYYYMMDD-<n>-<slug>/`,
containing:

| File | Content |
|---|---|
| `sortie.yaml` | reviewed kneeboard data — **source of truth** |
| `scan.jpg` / `scan.pdf` | the original scanned kneeboard card |
| `*.gpx`, `*.txt` | SkyDemon logs (only these two types; `.kml`/`.flightlog` are redundant) |
| `derived.json` | output of `scripts/gpx_phases.py` — regenerable, never edited |

## Fields

```yaml
sortie: 20260822-1            # id = date + running number
date: 2026-08-22
from: EDFM
to: EDFM
target_setting: cruise-64     # setting of the steady test block, see names below
oat_gnd_c: 24
qnh_hpa: 1018

refuel_open:                  # fill-to-full BEFORE engine start
  litres: 47.3                # pump reading
  brim: true                  # filled to the visual brim reference
  pump: "EDFM Avgas"
  spot: "GAT row 2"

refuel_close:                 # refuel AFTER shutdown; omit if none
  litres: 61.4                # pump reading
  brim: true                  # filled to the brim reference again
  same_pump: true
  same_spot: true

# Partial top-up variant (hangar policy: plane goes back with 40+40 L):
# not an exact closure — the post-refuel state is only pinned by the
# lamps, i.e. each tank is between its shown lamp's (early) switch point
# and the next one up (~40…50 L until the offsets are calibrated).
# refuel_close: {litres: 38.5, brim: false, lamps: {left: 40, right: 40}}

times:                        # all UTC hh:mm, from the kneeboard
  engine_start: "08:01"
  taxi: "08:04"
  takeoff: "08:11"
  power_reduced: "08:12"      # end of takeoff power
  top_of_climb: "08:15"
  landing: "09:28"
  engine_off: "09:33"

blocks:                       # steady test blocks (usually one)
  - start: "08:20"
    end: "09:10"
    rpm_pct: 64
    map_torr: 735
    alt_ft: 3000
    ias_kmh: 220
    oat_c: 18
    headings: [90, 270]

lamp_events:                  # EVERY lamp transition — the key data
  - {time: "08:42", tank: R, from: 50, to: 45, other_tank: 50}

gauge_reads:                  # optional: combined gauge readings (L+R sum)
  - {time: "08:30", litres: 100}

remarks: ""
review:                       # filled by the ingest workflow
  ocr_confidence: high
  corrected_fields: []
```

## Setting names

`ground`, `takeoff` (99 % · +125), `climb-80` (80 % · 800–850),
`rated-1` (82 % · +95), `rated-2` (70 % · +75),
`cruise-64` (64 % · 735 abs), `cruise-59` (59 % · 670 abs),
`extra-70-700`, `extra-60-600`. Priors and ordering constraints for each
live in `model-config.yaml`.

## How the estimator uses a sortie

`scripts/estimate.py` decomposes each sortie into a time budget
(taxi → takeoff power → climb → cruise segments → descent → taxi-in) and
balances it against the refuel closures:

- **Closure blocks**: a block starts at a sortie whose `refuel_open` is
  brim-full and ends at the next `refuel_close`. A brim close makes the
  pump litres equal the block's total burn (±1.5 L); a partial top-up
  with `lamps:` is a soft anchor (~±5 L) on the post-refuel state.
  Several sorties may share one closure (e.g. no refuel between two
  flights). A sortie chain without a closing refuel is still used — via
  its gauge/lamp observations only.
- **Refuel-only record**: a brim fill done later without a test flight
  still closes the open block *exactly*, provided every flight since the
  block opened is recorded. Minimal file:
  `{type: refuel-closure, sortie: 20260823-R1, date: 2026-08-23,`
  `refuel_close: {litres: 41.0, brim: true}}`
  So: leave the plane at 40+40 after the test, and when you brim it
  before the next flight, write down the pump litres — that one number
  retroactively turns the soft closure into a hard one.
- **`unrecorded_flights_before: true`** on a record means someone else
  flew (and refuelled) in between — the previous block is left unclosed
  rather than corrupted.
- **Lamp transitions** are precise per-tank fixes: at the moment lamp
  `from→to` switches, that tank holds `to + δ(to)` litres, where the
  early-switch offset `δ ∈ [0…10] L` is a calibration parameter *shared
  across all sorties* — the gauge calibration sharpens as data accumulates.
- **Gauge reads** (combined sum S) are soft intervals `[S−10, S+12] L`
  (the lamps switch early, so the gauge is a floor).
- Timing comes from the kneeboard times and `derived.json`
  (taxi, airborne, climb); the steady block minutes come from `blocks`.

### `estimation:` override

For sorties that predate this schema (the Aug 2026 seed trip) or unusual
profiles, an explicit `estimation:` section replaces the automatic
decomposition. `min: rest` assigns the remaining airborne time.
Two-element lists are uniform uncertainty ranges `[lo, hi]` in minutes.

```yaml
estimation:
  airborne_min: 76.3
  taxi_out_min: 9.9
  taxi_in_min: [3, 10]
  takeoff_min: [0.5, 1.5]
  climb_min: 3
  descent_min: [6, 10]
  segments:                       # after top of climb, in order
    - {setting: cruise-59, min: 15, gauge_after: 100}
    - {setting: cruise-59, min: 20}
    - {setting: extra-70-700, min: 3, gauge_after: 80}
    - {setting: cruise-59, min: 11, gauge_after: 70}
    - {setting: cruise-59, min: rest}
  gauge_at_landing: 45
```

Descent burn is modelled as `descent_factor × rate(last segment's
setting)` with `descent_factor ∈ [0.55, 1.0]`.

## Flight programme (short form; details in the analysis deck)

One sortie = one power setting. Fill to full (same pump, spot, brim) →
note pump litres → fly one steady 45–60 min block at constant
alt/rpm/MAP as a closed box or out-and-back → minimal pattern, land →
fill to full again. Note every time to the minute, and **every lamp
transition** (`time · tank · from→to · other tank`). Keep SkyDemon
logging the whole time — phone on ship power, screen lock off.
