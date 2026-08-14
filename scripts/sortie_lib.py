"""Shared loading/normalization for the Yak-52 fuel data pipeline.

Turns each data/sorties/*/sortie.yaml into a normalized *plan*:

    plan = {
      'id', 'date', 'target',
      'phases': [ {'setting': <name>|'_descent', 'air': bool, 'dur': spec} ],
      'obs':    [ {'type': 'gauge', 'litres': S, 'after_phase': i}
                | {'type': 'gauge_clock', 'litres': S, 'min': m}
                | {'type': 'lamp', 'to': v, 'other': w, 'min': m} ],
      'opens_block': bool,       # brim-full refuel before engine start
      'close_litres': float|None # closing fill-to-full = block burn anchor
    }

A duration spec is a float (minutes), a [lo, hi] uniform range, or the
string 'rest' (remaining airborne time). Clock-based observations ('min')
are minutes after takeoff. '_descent' burns descent_factor x the rate of
the last cruise segment.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "model-config.yaml"
SORTIES = ROOT / "data" / "sorties"


def load_config():
    return yaml.safe_load(CONFIG.read_text())


def load_sorties():
    """[(dir, yaml-dict)] sorted by sortie id."""
    out = []
    for d in sorted(SORTIES.iterdir()):
        f = d / "sortie.yaml"
        if f.is_file():
            out.append((d, yaml.safe_load(f.read_text())))
    out.sort(key=lambda x: str(x[1].get("sortie", x[0].name)))
    return out


def _hhmm(s):
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def _delta_min(t0, t1):
    d = _hhmm(t1) - _hhmm(t0)
    return d + 1440 if d < 0 else d


def _plan_from_estimation(s, cfg):
    e = s["estimation"]
    phases = [
        {"setting": "ground", "air": False, "dur": e.get("taxi_out_min", [3, 12])},
        {"setting": "takeoff", "air": True,
         "dur": e.get("takeoff_min", cfg["model"]["takeoff_min"])},
    ]
    if e.get("climb_min"):
        phases.append({"setting": "climb-80", "air": True, "dur": e["climb_min"]})
    obs = []
    for seg in e.get("segments", []):
        phases.append({"setting": seg["setting"], "air": True, "dur": seg["min"]})
        if "gauge_after" in seg:
            obs.append({"type": "gauge", "litres": seg["gauge_after"],
                        "after_phase": len(phases) - 1})
    phases.append({"setting": "_descent", "air": True,
                   "dur": e.get("descent_min", cfg["model"]["descent_min"])})
    if "gauge_at_landing" in e:
        obs.append({"type": "gauge", "litres": e["gauge_at_landing"],
                    "after_phase": len(phases) - 1})
    phases.append({"setting": "ground", "air": False,
                   "dur": e.get("taxi_in_min", [3, 10])})
    return phases, obs, e["airborne_min"]


def _plan_from_times(s, d, cfg):
    """Automatic decomposition from kneeboard times + derived.json."""
    t = s["times"]
    target = s.get("target_setting", "cruise-59")
    t_to, t_ldg = t["takeoff"], t["landing"]
    airborne = d.get("airborne_min") if d else None
    if airborne is None:
        airborne = _delta_min(t_to, t_ldg)

    taxi_out = (_delta_min(t["taxi"], t_to) if "taxi" in t
                else (d or {}).get("taxi_out_min")) or [3, 12]
    taxi_in = (_delta_min(t_ldg, t["engine_off"]) if "engine_off" in t
               else (d or {}).get("taxi_in_min")) or [3, 10]

    # clock-anchored airborne phases (minutes after takeoff)
    to_min = (_delta_min(t_to, t["power_reduced"]) if "power_reduced" in t
              else cfg["model"]["takeoff_min"])
    phases = [{"setting": "ground", "air": False, "dur": taxi_out},
              {"setting": "takeoff", "air": True, "dur": to_min}]
    cursor = to_min if isinstance(to_min, (int, float)) else sum(to_min) / 2
    if "top_of_climb" in t:
        toc = _delta_min(t_to, t["top_of_climb"])
        phases.append({"setting": "climb-80", "air": True,
                       "dur": max(toc - cursor, 0.5)})
        cursor = toc
    desc_lo, desc_hi = cfg["model"]["descent_min"]
    desc_start = airborne - (desc_lo + desc_hi) / 2
    for b in s.get("blocks", []):
        b0, b1 = _delta_min(t_to, b["start"]), _delta_min(t_to, b["end"])
        if b0 > cursor:
            phases.append({"setting": target, "air": True, "dur": b0 - cursor})
        phases.append({"setting": target, "air": True, "dur": b1 - b0})
        cursor = b1
    if desc_start > cursor:
        phases.append({"setting": target, "air": True, "dur": desc_start - cursor})
    phases.append({"setting": "_descent", "air": True,
                   "dur": cfg["model"]["descent_min"]})
    phases.append({"setting": "ground", "air": False, "dur": taxi_in})

    obs = []
    for g in s.get("gauge_reads", []):
        obs.append({"type": "gauge_clock", "litres": g["litres"],
                    "min": _delta_min(t_to, g["time"])})
    for ev in s.get("lamp_events", []):
        obs.append({"type": "lamp", "to": ev["to"],
                    "other": ev.get("other_tank"),
                    "min": _delta_min(t_to, ev["time"])})
    return phases, obs, airborne


def normalize(s, sortie_dir, cfg):
    if s.get("type") == "refuel-closure":
        phases, obs, airborne = [], [], 0.0
    else:
        d = None
        dj = sortie_dir / "derived.json"
        if dj.is_file():
            import json
            d = json.loads(dj.read_text())
        if "estimation" in s:
            phases, obs, airborne = _plan_from_estimation(s, cfg)
        else:
            phases, obs, airborne = _plan_from_times(s, d, cfg)

    rc = s.get("refuel_close") or {}
    close = None
    if rc.get("litres") is not None:
        close = {"litres": rc["litres"],
                 "brim": bool(rc.get("brim", False)),
                 "lamps": rc.get("lamps")}
        if not close["brim"] and not close["lamps"]:
            raise ValueError(
                f"sortie {s.get('sortie')}: refuel_close needs either "
                "brim: true or lamps: {left: .., right: ..}")
    return {
        "id": str(s["sortie"]),
        "date": str(s["date"]),
        "target": s.get("target_setting", "cruise-59"),
        "phases": phases,
        "obs": obs,
        "airborne_min": airborne,
        "opens_block": bool(s.get("refuel_open", {}).get("brim")),
        "unrecorded_before": bool(s.get("unrecorded_flights_before")),
        "close": close,
        "raw": s,
    }


def load_plans():
    cfg = load_config()
    return cfg, [normalize(s, d, cfg) for d, s in load_sorties()]


def group_blocks(plans):
    """Group consecutive sorties into refuel-closed blocks.

    A block starts at a brim-full refuel_open and ends at the next
    refuel_close (brim => exact anchor, else lamp-interval anchor).
    A new brim-open or an 'unrecorded_flights_before' flag leaves the
    previous block unclosed (its gauge/lamp observations still count).
    """
    blocks, cur = [], []
    for p in plans:
        if (p["opens_block"] or p["unrecorded_before"]) and cur:
            blocks.append({"plans": cur, "close": None})
            cur = []
        cur.append(p)
        if p["close"] is not None:
            blocks.append({"plans": cur, "close": p["close"]})
            cur = []
    if cur:
        blocks.append({"plans": cur, "close": None})
    return blocks
