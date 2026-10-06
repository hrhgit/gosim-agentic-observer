#!/usr/bin/env python3
"""Public-input analysis of the four formal cards (no weather, no truth files).

    python3 tools/analyze_cards.py > docs/data/card-analysis.md      # needs numpy

Reads only cards/<a|b|c|d>/{config,public} of the parent project: the official public
package. Prints, per card, the numbers the design in docs/02-design.md is built on:
catalogue composition, how long a target needs to saturate, fibre-time and pointing
supply against demand, and how visibility moves through the season.
"""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
PROJECT = Path(os.environ.get("OBSERVER_PROJECT_ROOT", REPO.parent.parent)).resolve()
CARDS = PROJECT / "cards"


def parse(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def lst_deg(ts, lon):
    d = ts / 86400.0 + 2440587.5 - 2451545.0
    return (280.46061837 + 360.98564736629 * d + lon) % 360.0


def pct(values, points):
    return " / ".join(f"{x:.3g}" for x in np.percentile(values, points))


def analyse(card: str) -> str:
    fiber = json.loads((CARDS / card / "config" / "v4_fiber_config.json").read_text())
    score = json.loads((CARDS / card / "config" / "v4_score_config.json").read_text())
    lat, lon = fiber["site"]["latitude_deg"], fiber["site"]["longitude_deg"]
    n_fib, area = fiber["field"]["n_fibers"], fiber["field"]["fiber_area_deg2"]
    f0, t0 = score["flux_zero_point"], score["exposure_zero_point_seconds"]
    rows = list(csv.DictReader((CARDS / card / "public" / "targets.csv").open()))
    flux = np.array([float(r["feature_flux"]) for r in rows])
    weight = np.array([float(r["science_weight"]) for r in rows])
    required = np.array([r["required"] == "true" for r in rows])
    cls = np.array([r["target_class"] for r in rows])
    ra = np.array([float(r["ra_deg"]) for r in rows])
    dec = np.array([float(r["dec_deg"]) for r in rows])
    nights = list(csv.DictReader((CARDS / card / "public" / "v4_night_calendar.csv").open()))
    start = np.array([parse(n["observing_start_utc"]) for n in nights])
    end = np.array([parse(n["observing_end_utc"]) for n in nights])
    total = float((end - start).sum())
    footprint = list(csv.DictReader((CARDS / card / "public" / "footprint.csv").open()))
    components = len({r["component_id"] for r in footprint})
    side = math.sqrt(area)
    fov = side * math.sqrt(n_fib)
    fov_area = area * n_fib
    # sky area from target density is not needed: the task card states it
    out = [f"## Card {card.upper()} — {fiber['site']['name']}", ""]
    out.append(f"- site latitude {lat:+.2f}, {len(nights)} nights, {total / 3600:.0f} h of darkness "
               f"({total / 900:.0f} slots); night length min/median/max "
               f"{(end - start).min() / 3600:.1f} / {np.median(end - start) / 3600:.1f} / {(end - start).max() / 3600:.1f} h")
    out.append(f"- instrument {n_fib} fibres ({int(math.sqrt(n_fib))}x{int(math.sqrt(n_fib))}), cell {side:.3f} deg, "
               f"field {fov:.2f} deg = {fov_area:.2f} deg2; f0 = {f0}, T0 = {t0} s")
    out.append(f"- {len(rows)} targets in {components} footprint components, {int(required.sum())} required; "
               f"sum of weights {weight.sum():.0f} (x1.2 = {1.2 * weight.sum():.0f} is the ceiling of the science term); "
               f"required penalty at stake {50 * int(required.sum())}")
    out.append("")
    out.append("| class | n | required | weight | flux p5 / p50 / p95 | T_sat at Q=1 (s) p5 / p50 / p95 | weight / T_sat p50 (1e-3/s) |")
    out.append("|---|---:|---:|---:|---|---|---:|")
    for name in sorted(set(cls)):
        m = cls == name
        tsat = f0 * t0 / flux[m]
        out.append(f"| {name} | {m.sum()} | {required[m].sum()} | {weight[m][0]:.2f} | {pct(flux[m], [5, 50, 95])} | "
                   f"{pct(tsat, [95, 50, 5])} | {1e3 * np.median(weight[m] / tsat):.2f} |")
    tsat = f0 * t0 / flux
    treq = 0.5 * tsat
    out.append("")
    out.append(f"- time to saturate (g = 1) at Q = 1, all targets: p25 / p50 / p75 / p95 = {pct(tsat, [25, 50, 75, 95])} s; "
               f"{100 * (tsat > 3600).mean():.1f}% cannot saturate in one 3600 s exposure")
    out.append(f"- required targets needing more than 900 / 1800 / 3600 s to reach g = 0.5 at Q = 1: "
               f"{int((treq[required] > 900).sum())} / {int((treq[required] > 1800).sum())} / {int((treq[required] > 3600).sum())}")
    demand = np.minimum(tsat, 3600).sum()
    out.append(f"- fibre-time: demand {demand / 1e6:.1f} M fibre-s to saturate everything, supply {total * n_fib / 1e6:.1f} M "
               f"fibre-s before weather (ratio {total * n_fib / demand:.2f})")
    density = len(rows) / {"a": 6000, "b": 8000, "c": 4000, "d": 6000}[card]
    per_cell = density * area
    filled = n_fib * (1 - math.exp(-per_cell))
    tiles = {"a": 6000, "b": 8000, "c": 4000, "d": 6000}[card] / fov_area
    out.append(f"- pointing view: {density:.2f} targets/deg2 -> {density * fov_area:.1f} per field, {per_cell:.2f} per cell, "
               f"{filled:.1f} cells occupied for a random pointing; the footprint is {tiles:.0f} field areas, "
               f"so {total / tiles:.0f} s of darkness per field area")
    # visibility through the season (15-minute sampling)
    hmax = np.degrees(np.arccos(np.clip((math.sin(math.radians(30)) - math.sin(math.radians(lat)) * np.sin(np.radians(dec)))
                                        / (math.cos(math.radians(lat)) * np.cos(np.radians(dec))), -1, 1)))
    vis = np.zeros((len(nights), len(rows)), dtype=np.float32)
    for i, (s, e) in enumerate(zip(start, end)):
        ts = np.arange(s, e, 900.0) + 450
        h = (lst_deg(ts, lon)[:, None] - ra[None, :] + 180) % 360 - 180
        vis[i] = (np.abs(h) <= hmax[None, :]).sum(0) * 900.0
    hours = vis.sum(0) / 3600
    up = vis > 0
    first = np.argmax(up, axis=0)
    last = len(nights) - 1 - np.argmax(up[::-1], axis=0)
    culm = 90 - np.abs(dec - lat)
    out.append(f"- culmination altitude p0 / p5 / p50 = {pct(culm, [0, 5, 50])} deg")
    out.append(f"- hours above 30 deg during darkness, per target over the survey: p0 / p5 / p25 / p50 = {pct(hours, [0, 5, 25, 50])}")
    out.append(f"- first night visible p50 / p75 / p95 / max = {pct(first, [50, 75, 95, 100])}; "
               f"last night visible p0 / p5 / p25 / p50 = {pct(last, [0, 5, 25, 50])} (of {len(nights) - 1})")
    bands = {}
    for value in (ra // 10).astype(int):
        bands[value] = bands.get(value, 0) + 1
    small = {f"{10 * b}-{10 * b + 10}": c for b, c in sorted(bands.items()) if c < 150}
    out.append(f"- {len(bands)} RA bands of 10 deg hold targets; bands with fewer than 150 targets: {small or 'none'}")
    out.append("")
    return "\n".join(out)


def main() -> None:
    print("# Formal cards A-D: public-input analysis\n")
    print("Generated by `tools/analyze_cards.py` from the official public package under `cards/` "
          "(no weather, no truth). Q is the exposure quality of the scoring formula; Q = 1 is a good, "
          "moonless sky near the zenith.\n")
    for card in "abcd":
        print(analyse(card))


if __name__ == "__main__":
    main()
