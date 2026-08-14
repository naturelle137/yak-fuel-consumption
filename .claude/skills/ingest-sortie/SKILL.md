---
name: ingest-sortie
description: Ingest a flown fuel-test sortie — read the scanned kneeboard card, cross-check against SkyDemon logs, review with the user, store the sortie record and refresh the statistics. Use when the user says /ingest-sortie or asks to process a new sortie / scanned kneeboard sheet.
---

# Ingest a fuel-test sortie

Input: the user has dropped into `incoming/` a scan/photo of the filled
kneeboard card (front and back — may be one file or two) plus that
flight's SkyDemon `.gpx` and `.txt` files. If `incoming/` is empty, ask
where the files are. Ignore `.kml` and `.flightlog` files; they carry
nothing extra.

Schema reference: `docs/sortie-schema.md`. Setting names and the model
configuration: `model-config.yaml`.

## Steps

1. **List** `incoming/`. If several sorties' files are mixed, group them
   by date/time in the filenames and confirm the grouping with the user
   before proceeding. Process one sortie at a time.

2. **Read the scan image(s)** with the Read tool and transcribe every
   field into a draft `sortie.yaml` (see schema). Note per-field
   confidence; mark anything you cannot read confidently as `?`.
   Handwriting rules: times are UTC hh:mm; pump litres use a decimal
   comma. The back card has one pre-printed transition list per tank
   (60→50 … 15→12) — a filled time next to a transition is that tank's
   lamp event; the third cell is what the OTHER tank showed at that
   moment. Empty rows are simply transitions that didn't occur. The
   circled 60s at the top confirm full tanks before engine start
   (record as `refuel_open.brim: true` confirmation, not an event).

3. **Run the GPX analysis**:
   `python3 scripts/gpx_phases.py incoming/ -o incoming/derived.json`
   (or pass the specific .gpx files if incoming/ holds several sorties).

4. **Cross-check** scan vs logs, and list every disagreement:
   - takeoff/landing times: kneeboard vs `derived.json` (GPX truth, ±1 min)
   - engine on/off: kneeboard vs the first/last `.txt` summary
   - block start/end must lie inside the airborne window
   - closing pump litres vs a rough burn estimate
     (`data/results.md` rates × phase times) — flag if off by > 15 L
   - lamp transition times must be strictly increasing, lamp values must
     descend along the tank's scale (60 50 45 40 35 30 25 20 15 12)

5. **Review with the user**: show one table — field, transcribed value,
   confidence, cross-check result. Ask for corrections (AskUserQuestion
   for genuinely ambiguous readings, otherwise plain conversation).
   Do not proceed until the user confirms.

6. **Store**: create `data/sorties/YYYYMMDD-<n>-<from>-<to>/` (n = next
   free number that date), write the confirmed `sortie.yaml` (fill the
   `review:` section: ocr_confidence, corrected_fields), move the scan
   (rename to `scan.<ext>`), the `.gpx`/`.txt` files and `derived.json`
   into it. `incoming/` must be empty afterwards.

7. **Refresh statistics**:
   `python3 scripts/build_dataset.py && python3 scripts/estimate.py`

8. **Report**: summarize the new sortie (burn, L/h at the target setting)
   and how the posterior for that setting moved (median and CI before vs
   after — the previous values are in git history or the old results.md;
   quote them from the table you saw before re-running). Mention new lamp
   calibration points if the sortie logged transitions.

## Rules

- Never guess an unreadable value silently — always surface it in the
  review table.
- Times on the kneeboard are UTC; if a time is obviously local
  (offset ≈ +1/+2 h vs GPX), ask instead of assuming.
- If the flight has no closing fill-to-full refuel, still ingest it —
  set no `refuel_close`; the estimator uses its lamp/gauge events.
- If the GPX shows recording gaps, note them in `remarks`.
