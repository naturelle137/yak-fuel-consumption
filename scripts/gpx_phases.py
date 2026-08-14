#!/usr/bin/env python3
"""Merge SkyDemon GPX track logs of one flight and derive flight phases.

SkyDemon writes one .gpx per recording session; a disconnect mid-flight
produces several files. All .gpx files given (or found in a directory)
are merged into one timeline sorted by trackpoint time, then phases are
detected from groundspeed and altitude:

    taxi-out   first movement (> TAXI_SPEED) until takeoff
    airborne   speed > FLY_SPEED sustained, until it drops for good
    climb      takeoff until first sustained level-off
    taxi-in    after landing until last movement

Output (JSON, stdout or -o file):
    engine window, taxi/airborne/climb times [min], track distance [NM],
    per-landing detection, recording gaps.

Only needs Python 3 stdlib.

Usage:
    python3 gpx_phases.py FILE.gpx [FILE2.gpx ...] [-o derived.json]
    python3 gpx_phases.py SORTIE_DIR [-o derived.json]
"""
import json
import math
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

NS = {"g": "http://www.topografix.com/GPX/1/1"}

TAXI_SPEED = 2.0    # m/s (~4 kt) — moving on the ground
FLY_SPEED = 20.6    # m/s (= 40 kt) — airborne threshold
SUSTAIN = 30.0      # s a state must hold to count (kills GPS spikes)
GAP_S = 60.0        # s without fixes = recording gap
CLIMB_MIN_RATE = 1.5  # m/s sustained climb
LEVEL_WINDOW = 60.0   # s of level flight that ends the initial climb

KT_PER_MS = 1.9438445
NM_PER_M = 1 / 1852.0


def load_points(paths):
    """All trackpoints of all files, time-sorted, de-duplicated."""
    pts = []
    for p in paths:
        root = ET.parse(p).getroot()
        for tp in root.iter("{%s}trkpt" % NS["g"]):
            t = tp.find("g:time", NS)
            if t is None:
                continue
            ts = datetime.fromisoformat(t.text.replace("Z", "+00:00"))
            ele = tp.find("g:ele", NS)
            spd = tp.find("g:speed", NS)
            pts.append({
                "t": ts,
                "lat": float(tp.get("lat")),
                "lon": float(tp.get("lon")),
                "ele": float(ele.text) if ele is not None else None,
                "v": float(spd.text) if spd is not None else None,
            })
    pts.sort(key=lambda x: x["t"])
    out = []
    for p in pts:                      # drop duplicate timestamps at joins
        if out and p["t"] <= out[-1]["t"]:
            continue
        out.append(p)
    return out


def haversine_m(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians,
                                 (a["lat"], a["lon"], b["lat"], b["lon"]))
    s = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371000.0 * math.asin(math.sqrt(s))


def speeds(pts):
    """Groundspeed m/s per point; GPX <speed> if present, else derived."""
    v = []
    for i, p in enumerate(pts):
        if p["v"] is not None:
            v.append(p["v"])
        elif i:
            dt = (pts[i]["t"] - pts[i - 1]["t"]).total_seconds()
            v.append(haversine_m(pts[i - 1], pts[i]) / dt if dt > 0 else 0.0)
        else:
            v.append(0.0)
    return v


def sustained_regions(pts, flags, min_hold=SUSTAIN):
    """[(i_start, i_end)] where flag holds for >= min_hold seconds."""
    regions, start = [], None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if (pts[i - 1]["t"] - pts[start]["t"]).total_seconds() >= min_hold:
                regions.append((start, i - 1))
            start = None
    if start is not None and \
            (pts[-1]["t"] - pts[start]["t"]).total_seconds() >= min_hold:
        regions.append((start, len(pts) - 1))
    return regions


def merge_close(pts, regions, max_gap):
    """Join regions separated by less than max_gap seconds."""
    out = []
    for r in regions:
        if out and (pts[r[0]]["t"] - pts[out[-1][1]]["t"]).total_seconds() < max_gap:
            out[-1] = (out[-1][0], r[1])
        else:
            out.append(list(r))
            out[-1] = tuple(out[-1])
    return out


def initial_climb_end(pts, i_to):
    """Index where the first sustained level-off after takeoff begins."""
    level_since = None
    for i in range(i_to + 1, len(pts)):
        dt = (pts[i]["t"] - pts[i - 1]["t"]).total_seconds()
        if dt <= 0 or pts[i]["ele"] is None or pts[i - 1]["ele"] is None:
            continue
        rate = (pts[i]["ele"] - pts[i - 1]["ele"]) / dt
        if rate < CLIMB_MIN_RATE:
            if level_since is None:
                level_since = i
            elif (pts[i]["t"] - pts[level_since]["t"]).total_seconds() >= LEVEL_WINDOW:
                return level_since
        else:
            level_since = None
    return None


def mins(a, b):
    return round((b - a).total_seconds() / 60.0, 1)


def iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def analyse(paths):
    pts = load_points(paths)
    if len(pts) < 2:
        raise SystemExit("no trackpoints found")
    v = speeds(pts)

    gaps = []
    for i in range(1, len(pts)):
        dt = (pts[i]["t"] - pts[i - 1]["t"]).total_seconds()
        if dt > GAP_S:
            gaps.append({"from": iso(pts[i - 1]["t"]), "to": iso(pts[i]["t"]),
                         "minutes": round(dt / 60.0, 1)})

    fly = merge_close(pts, sustained_regions(pts, [x > FLY_SPEED for x in v]),
                      max_gap=120)
    moving = sustained_regions(pts, [x > TAXI_SPEED for x in v], min_hold=10)

    flights = []
    airborne_total = 0.0
    dist_total = 0.0
    for (i0, i1) in fly:
        d = sum(haversine_m(pts[i - 1], pts[i]) for i in range(i0 + 1, i1 + 1))
        climb_i = initial_climb_end(pts, i0)
        seg = {
            "takeoff": iso(pts[i0]["t"]),
            "landing": iso(pts[i1]["t"]),
            "airborne_min": mins(pts[i0]["t"], pts[i1]["t"]),
            "distance_nm": round(d * NM_PER_M, 1),
            "top_of_climb": iso(pts[climb_i]["t"]) if climb_i else None,
            "climb_min": mins(pts[i0]["t"], pts[climb_i]["t"]) if climb_i else None,
            "max_alt_ft": round(max(p["ele"] for p in pts[i0:i1 + 1]
                                    if p["ele"] is not None) * 3.28084),
            "max_gs_kt": round(max(v[i0:i1 + 1]) * KT_PER_MS),
        }
        airborne_total += seg["airborne_min"]
        dist_total += seg["distance_nm"]
        flights.append(seg)

    taxi_out = taxi_in = None
    if fly and moving:
        first_move = pts[moving[0][0]]["t"]
        t_takeoff = pts[fly[0][0]]["t"]
        if first_move < t_takeoff:
            taxi_out = mins(first_move, t_takeoff)
        t_land = pts[fly[-1][1]]["t"]
        last_move = pts[moving[-1][1]]["t"]
        if last_move > t_land:
            taxi_in = mins(t_land, last_move)

    return {
        "files": [Path(p).name for p in paths],
        "log_start": iso(pts[0]["t"]),
        "log_end": iso(pts[-1]["t"]),
        "log_total_min": mins(pts[0]["t"], pts[-1]["t"]),
        "gaps": gaps,
        "flights": flights,
        "airborne_min": round(airborne_total, 1),
        "distance_nm": round(dist_total, 1),
        "taxi_out_min": taxi_out,
        "taxi_in_min": taxi_in,
    }


def main(argv):
    out = None
    if "-o" in argv:
        i = argv.index("-o")
        out = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if not argv:
        raise SystemExit(__doc__)
    paths = []
    for a in argv:
        p = Path(a)
        paths += sorted(p.glob("*.gpx")) if p.is_dir() else [p]
    if not paths:
        raise SystemExit("no .gpx files found")
    result = analyse(paths)
    text = json.dumps(result, indent=2)
    if out:
        out.write_text(text + "\n")
        print(f"wrote {out}")
    else:
        print(text)


if __name__ == "__main__":
    main(sys.argv[1:])
