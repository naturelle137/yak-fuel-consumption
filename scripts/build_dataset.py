#!/usr/bin/env python3
"""Flatten all sortie records into spreadsheet-friendly CSVs.

Writes:
    data/master.csv       one row per phase of every sortie
    data/lamp_events.csv  every recorded lamp transition
    data/sorties.csv      one summary row per sortie

Usage: python3 scripts/build_dataset.py
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sortie_lib import ROOT, load_plans

DATA = ROOT / "data"


def nominal(dur, plan):
    if dur == "rest":
        known = sum(nominal(p["dur"], plan) for p in plan["phases"]
                    if p["air"] and p["dur"] != "rest")
        return max(plan["airborne_min"] - known, 0.0)
    if isinstance(dur, (list, tuple)):
        return sum(dur) / 2.0
    return float(dur)


def main():
    cfg, plans = load_plans()
    DATA.mkdir(exist_ok=True)

    with open(DATA / "master.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sortie", "date", "phase", "setting", "airborne",
                    "minutes_nominal", "minutes_spec"])
        for p in plans:
            for i, ph in enumerate(p["phases"]):
                w.writerow([p["id"], p["date"], i, ph["setting"],
                            int(ph["air"]), round(nominal(ph["dur"], p), 1),
                            ph["dur"]])

    with open(DATA / "lamp_events.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sortie", "date", "time_utc", "tank", "lamp_from",
                    "lamp_to", "other_tank"])
        for p in plans:
            for ev in p["raw"].get("lamp_events", []):
                w.writerow([p["id"], p["date"], ev["time"], ev["tank"],
                            ev.get("from"), ev["to"], ev.get("other_tank")])

    with open(DATA / "sorties.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sortie", "date", "from", "to", "target_setting",
                    "airborne_min", "refuel_open_l", "refuel_close_l",
                    "close_brim", "close_lamps", "opens_block",
                    "lamp_events", "remarks"])
        for p in plans:
            raw = p["raw"]
            close = p["close"] or {}
            lamps = close.get("lamps") or {}
            w.writerow([p["id"], p["date"], raw.get("from"), raw.get("to"),
                        p["target"], p["airborne_min"],
                        (raw.get("refuel_open") or {}).get("litres"),
                        close.get("litres"), close.get("brim"),
                        (f"{lamps.get('left')}+{lamps.get('right')}"
                         if lamps else ""), int(p["opens_block"]),
                        len(raw.get("lamp_events", [])),
                        (raw.get("remarks") or "").strip()])

    print(f"wrote {DATA/'master.csv'}, {DATA/'lamp_events.csv'}, "
          f"{DATA/'sorties.csv'}  ({len(plans)} sorties)")


if __name__ == "__main__":
    main()
