#!/usr/bin/env python3
"""Bayesian Monte-Carlo fuel-burn estimation over all recorded sorties.

Generalization of the original single-trip script: every sortie in
data/sorties/ is decomposed into a time budget (sortie_lib), fuel-balance
blocks are anchored by fill-to-full refuel closures, and gauge readings /
lamp transitions weight the samples. Burn-rate priors, gauge model and
ordering constraints come from model-config.yaml.

Usage: python3 scripts/estimate.py [-n SAMPLES] [-o data/results.md]
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sortie_lib import ROOT, group_blocks, load_plans


def sample_dur(spec, rng, n):
    if isinstance(spec, (list, tuple)):
        return rng.uniform(spec[0], spec[1], n)
    return float(spec)


class Model:
    def __init__(self, cfg, plans, n, rng):
        self.cfg, self.n, self.rng = cfg, n, rng
        m = cfg["model"]
        # only settings that occur in the data enter the joint model
        active = {"ground", "takeoff"}
        for p in plans:
            for ph in p["phases"]:
                if ph["setting"] != "_descent":
                    active.add(ph["setting"])
            active.add(p["target"])
        for name, prior in cfg["priors"].items():   # dependencies of 'between'
            if name in active and isinstance(prior, dict):
                active.update(prior["between"])
        self.active = active

        self.rates = {}
        for name, prior in cfg["priors"].items():
            if name not in active or isinstance(prior, dict):
                continue
            self.rates[name] = rng.uniform(prior[0], prior[1], n)
        for name, prior in cfg["priors"].items():   # dependent priors
            if name in active and isinstance(prior, dict):
                a, b = prior["between"]
                u = rng.uniform(0, 1, n)
                self.rates[name] = self.rates[a] + u * (self.rates[b] - self.rates[a])
        self.fdesc = rng.uniform(*m["descent_factor"], n)
        self.lamp_delta = {}          # per lamp value, shared calibration
        self.logw = np.zeros(n)
        self.sig_refuel = m["refuel_sigma_l"]
        self.sig_g = m["gauge_sigma_l"]
        self.slack_lo, self.slack_hi = m["gauge_slack_lo"], m["gauge_slack_hi"]
        self.sig_lamp = m["lamp_sigma_l"]
        self.delta_max = m["lamp_delta_max"]
        self.plans = plans

    def full_fuel(self):
        a = self.cfg["aircraft"]
        return np.clip(self.rng.normal(a["full_fuel_l"], a["full_fuel_sigma_l"],
                                       self.n), *a["full_fuel_clip_l"])

    def delta(self, lamp):
        if lamp not in self.lamp_delta:
            self.lamp_delta[lamp] = self.rng.uniform(0, self.delta_max, self.n)
        return self.lamp_delta[lamp]

    def lamp_up(self, lamp):
        """Next value up the per-tank lamp scale (45 for 40); None at top."""
        scale = sorted(self.cfg["aircraft"]["lamp_scale"])
        i = scale.index(lamp)
        return scale[i + 1] if i + 1 < len(scale) else None

    def gauge_pen(self, fuel, s):
        lo, hi = s - self.slack_lo, s + self.slack_hi
        z = np.where(fuel < lo, -0.5 * ((fuel - lo) / self.sig_g) ** 2, 0.0)
        z += np.where(fuel > hi, -0.5 * ((fuel - hi) / self.sig_g) ** 2, 0.0)
        return z

    def soft_interval_pen(self, x, lo, hi, sig):
        z = np.where(x < lo, -0.5 * ((x - lo) / sig) ** 2, 0.0)
        z += np.where(x > hi, -0.5 * ((x - hi) / sig) ** 2, 0.0)
        return z

    def phase_rates_durs(self, plan):
        """Resolve each phase to (rate N-vector, duration scalar/N-vector)."""
        durs, rates = [], []
        last_cruise = None
        rest_idx = None
        air_known = 0.0
        for i, ph in enumerate(plan["phases"]):
            setting = ph["setting"]
            if setting == "_descent":
                base = self.rates[last_cruise or plan["target"]]
                rates.append(self.fdesc * base)
            else:
                rates.append(self.rates[setting])
                if ph["air"] and setting not in ("takeoff", "climb-80"):
                    last_cruise = setting
            if ph["dur"] == "rest":
                rest_idx = i
                durs.append(None)
                continue
            d = sample_dur(ph["dur"], self.rng, self.n)
            durs.append(d)
            if ph["air"]:
                air_known = air_known + d
        if rest_idx is not None:
            rest = np.clip(plan["airborne_min"] - air_known, 0.0, None)
            durs[rest_idx] = rest
        return rates, durs

    def run(self):
        self.per_sortie_burn = {}
        for block in group_blocks(self.plans):
            f0 = self.full_fuel()
            fuel = f0.copy()
            for plan in block["plans"]:
                rates, durs = self.phase_rates_durs(plan)
                starts = []           # cumulative airborne minutes at phase start
                cursor = 0.0
                for ph, d in zip(plan["phases"], durs):
                    if ph["air"]:
                        starts.append(cursor)
                        cursor = cursor + d
                    else:
                        starts.append(None)

                sortie_start_fuel = fuel
                # positional gauge observations need fuel after each phase
                pos_obs = {o["after_phase"]: o for o in plan["obs"]
                           if o["type"] == "gauge"}
                f = fuel
                for i, (r, d) in enumerate(zip(rates, durs)):
                    f = f - r * d / 60.0
                    if i in pos_obs:
                        self.logw += self.gauge_pen(f, pos_obs[i]["litres"])
                fuel_end = f

                # clock-based observations: burn up to minute m after takeoff
                clock_obs = [o for o in plan["obs"] if o["type"] != "gauge"]
                if clock_obs:
                    ground_before_to = 0.0
                    for ph, r, d in zip(plan["phases"], rates, durs):
                        if ph["air"]:
                            break
                        ground_before_to = ground_before_to + r * d / 60.0
                    for o in clock_obs:
                        burn = ground_before_to
                        for ph, r, d, st in zip(plan["phases"], rates, durs, starts):
                            if st is None:
                                continue
                            burn = burn + r * np.clip(o["min"] - st, 0.0, d) / 60.0
                        fobs = sortie_start_fuel - burn
                        if o["type"] == "gauge_clock":
                            self.logw += self.gauge_pen(fobs, o["litres"])
                        elif o["type"] == "lamp":
                            to = o["to"] + self.delta(o["to"])
                            if o.get("other") is not None:
                                lo = to + o["other"] - 5.0
                                hi = to + o["other"] + 11.0
                            else:   # other tank unknown: half of combined slack
                                lo = to + 12.0 - 5.0
                                hi = to + 60.0 + 11.0
                            self.logw += self.soft_interval_pen(
                                fobs, lo, hi, self.sig_lamp)

                self.per_sortie_burn[plan["id"]] = sortie_start_fuel - fuel_end
                fuel = fuel_end
            close = block["close"]
            if close is not None and close["brim"]:
                burn_total = f0 - fuel
                self.logw += -0.5 * ((burn_total - close["litres"])
                                     / self.sig_refuel) ** 2
            elif close is not None:
                # partial top-up: post-refuel state pinned by the lamps.
                # Lamp v showing means the tank is between the (early)
                # v-switch and the next switch up the scale; assumes the
                # filling-direction switch point equals the draining one.
                after = fuel + close["litres"]
                lo, hi = 0.0, 0.0
                for v in (close["lamps"]["left"], close["lamps"]["right"]):
                    lo = lo + v + self.delta(v)
                    up = self.lamp_up(v)
                    if up is None:      # top lamp: bounded by tank capacity
                        hi = hi + self.cfg["aircraft"]["full_fuel_clip_l"][1] / 2
                    else:
                        hi = hi + up + self.delta(up)
                self.logw += self.soft_interval_pen(after, lo, hi, self.sig_g)

        # ordering constraints (only pairs where both settings are active;
        # 'between' priors are ordered by construction)
        ok = np.ones(self.n, dtype=bool)
        for chain in self.cfg["ordering"]:
            present = [s for s in chain if s in self.rates]
            for a, b in zip(present, present[1:]):
                if isinstance(self.cfg["priors"][a], dict) or \
                        isinstance(self.cfg["priors"][b], dict):
                    continue
                ok &= self.rates[a] < self.rates[b]
        self.logw[~ok] = -np.inf
        self.logw -= self.logw.max()
        self.w = np.exp(self.logw)
        self.W = self.w.sum()
        self.ess = self.W ** 2 / np.sum(self.w ** 2)

    def post(self, x):
        m = float(np.sum(self.w * x) / self.W)
        idx = self.rng.choice(self.n, size=20000, p=self.w / self.W)
        q = np.percentile(np.asarray(x)[idx], [5, 50, 95])
        return m, q[1], (q[0], q[2])

    def data_minutes(self):
        """Nominal minutes flown per setting (for the confidence column)."""
        mins = {}
        for plan in self.plans:
            for ph in plan["phases"]:
                s = ph["setting"]
                d = ph["dur"]
                if d == "rest":
                    known = sum(
                        (sum(p2["dur"]) / 2 if isinstance(p2["dur"], list)
                         else p2["dur"])
                        for p2 in plan["phases"]
                        if p2["air"] and p2["dur"] != "rest")
                    d = max(plan["airborne_min"] - known, 0)
                elif isinstance(d, list):
                    d = sum(d) / 2
                mins[s] = mins.get(s, 0.0) + d
        return mins


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=None, help="MC samples")
    ap.add_argument("-o", type=Path, default=ROOT / "data" / "results.md")
    args = ap.parse_args()

    cfg, plans = load_plans()
    n = args.n or cfg["model"]["samples"]
    rng = np.random.default_rng(42)
    model = Model(cfg, plans, n, rng)
    model.run()

    mins = model.data_minutes()
    lines = []
    lines.append("# Yak-52 fuel burn — posterior estimates")
    lines.append("")
    lines.append(f"Generated {date.today()} from {len(plans)} sorties "
                 f"({sum(p['airborne_min'] for p in plans):.0f} airborne min); "
                 f"{n:,} MC samples, effective sample size {model.ess:,.0f}.")
    lines.append("")
    lines.append("## Burn rates per power setting")
    lines.append("")
    lines.append("| Setting | Median L/h | 90 % CI | Data (min) |")
    lines.append("|---|---|---|---|")
    order = ["ground", "extra-60-600", "cruise-59", "cruise-64",
             "extra-70-700", "rated-2", "rated-1", "climb-80", "takeoff"]
    for s in order:
        if s in model.rates:
            m, med, (lo, hi) = model.post(model.rates[s])
            lines.append(f"| {s} | {med:.1f} | {lo:.0f} – {hi:.0f} "
                         f"| {mins.get(s, 0.0):.0f} |")
        elif s in cfg["priors"]:
            p = cfg["priors"][s]
            rng_txt = (f"{p['between'][0]} … {p['between'][1]}"
                       if isinstance(p, dict) else f"{p[0]} – {p[1]}")
            lines.append(f"| {s} | — | {rng_txt} | 0 (prior only, no data yet) |")
    m, med, (lo, hi) = model.post(model.fdesc)
    lines.append(f"| descent factor | {med:.2f} | {lo:.2f} – {hi:.2f} | — |")
    lines.append("")

    if model.lamp_delta:
        lines.append("## Lamp calibration (early-switch offset, L)")
        lines.append("")
        lines.append("| Lamp | Median δ | 90 % CI |")
        lines.append("|---|---|---|")
        for lamp in sorted(model.lamp_delta, reverse=True):
            m, med, (lo, hi) = model.post(model.lamp_delta[lamp])
            lines.append(f"| {lamp} | +{med:.1f} | {lo:.1f} – {hi:.1f} |")
        lines.append("")

    lines.append("## Per-sortie burn (posterior)")
    lines.append("")
    lines.append("| Sortie | Burn L | 90 % CI | L per airborne h |")
    lines.append("|---|---|---|---|")
    for plan in plans:
        if not plan["phases"]:          # refuel-only closure record
            continue
        b = model.per_sortie_burn[plan["id"]]
        m, med, (lo, hi) = model.post(b)
        rate = med / (plan["airborne_min"] / 60.0)
        lines.append(f"| {plan['id']} {plan['raw'].get('from','')}–"
                     f"{plan['raw'].get('to','')} | {med:.1f} "
                     f"| {lo:.1f} – {hi:.1f} | {rate:.1f} |")
    lines.append("")

    text = "\n".join(lines) + "\n"
    args.o.parent.mkdir(parents=True, exist_ok=True)
    args.o.write_text(text)
    print(text)
    print(f"wrote {args.o}")


if __name__ == "__main__":
    main()
