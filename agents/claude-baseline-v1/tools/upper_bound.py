#!/usr/bin/env python3
"""How much could a perfect scheduler get?  An optimistic estimate from a linear programme.

    python3 tools/upper_bound.py [--eval-set NAME] [--cards a b c d] [--bin-slots 4]

Needs numpy and scipy (developer analysis only, like inspect_run.py: it reads the
SELF-GENERATED truth of the local evaluation cards; nothing here reaches the agent).

Model. The sky is cut into field-sized zones and the season into time bins. A scheduler that
knows the whole weather in advance chooses what fraction of every bin to spend on every zone
that is above the altitude limit and not behind an obstruction. Time on a zone delivers
"quality-seconds" (exposure seconds x true quality x program multiplier / 1.20), which the
zone turns into score through a concave function V_z:

  loose   any n_fibres targets of the zone can be exposed at the same moment;
  cells   the zone keeps a fixed n x n fibre tiling and every fibre cell exposes one of its
          targets at a time;
  shared  the real exposure rules inside a zone: an exposure has ONE duration for all its
          fibres, takes at most n_fibres targets, and a target keeps only its best exposure.
          V_z is the best of several greedy schedules (different prices of time), so this is
          what a very good scheduler could reach rather than a proven bound; it still knows
          the whole weather and may put any n_fibres targets of a zone into one exposure.
  shared_cells  the same with a fixed fibre tiling (one target per cell and exposure). A real
          schedule shifts the tiling, so the truth should lie between the two.

Both versions let exposures of the same target add up (the real rule keeps only the best
single exposure), do not make the fibres of one exposure share a duration, repair faults the
moment they start and know the band exactly; they ignore requests, reports and penalties.
"loose" is therefore an optimistic ceiling, and a wide one: the shared duration is the main
real constraint. "cells" is NOT a bound: a real schedule may shift the tiling between
exposures, and the v1.0 agent already scores above it on card C. The true optimum lies
somewhere between the agent's score and "loose"; this tool cannot say where.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
from _paths import cards_dir  # noqa: E402
from core.astro import airmass, lst_deg, moon_radec, sun_radec, unit_vector  # noqa: E402
from core.catalog import Grid  # noqa: E402

RAD = math.pi / 180.0


def utc(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def rows(path: Path) -> list[dict]:
    with path.open() as handle:
        return list(csv.DictReader(handle))


def zone_value(density, cap, fibres, taus):
    """V(tau) for the loose model: sum d_i a_i, a_i <= min(cap_i, tau), sum a_i <= fibres * tau."""
    out = np.zeros(len(taus))
    for k, tau in enumerate(taus):
        take = np.minimum(cap, tau)
        cum = np.cumsum(take)
        budget = fibres * tau
        full = cum <= budget
        value = float(np.sum(density[full] * take[full]))
        rest = budget - (cum[full][-1] if full.any() else 0.0)
        nxt = int(full.sum())
        if nxt < len(take) and rest > 0.0:
            value += density[nxt] * min(take[nxt], rest)
        out[k] = value
    return out


def cell_value(density, cap, taus):
    """v(tau) for one fibre cell: its targets one after another, best density first."""
    cum = np.concatenate([[0.0], np.cumsum(cap)])
    val = np.concatenate([[0.0], np.cumsum(density * cap)])
    return np.interp(taus, cum, val, right=val[-1])


GROUP_EDGES = (600.0, 1500.0, 3000.0)       # seconds needed at quality 1


def shared_points(value_full, need, cap_q, fibres, cells=None):
    """Feasible schedules of one zone under the shared-duration rule.

    Returns rows (tau, value, value per need group x4, tau spent in exposures >= 1500 q-s)."""
    n = len(need)
    grid = np.unique(np.minimum(need, cap_q))
    if len(grid) > 14:
        grid = np.unique(np.quantile(grid, np.linspace(0.0, 1.0, 14)))
    full = value_full[None, :] * np.minimum(1.0, grid[:, None] / need[None, :])
    group = np.searchsorted(GROUP_EDGES, need)
    reference = value_full.sum() / max(1.0, need.sum() / fibres)          # value per quality-second if nothing were wasted
    rows_out = [np.zeros(7)]
    for price in (None, 0.0, 0.3, 0.6, 1.0, 1.6):
        best = np.zeros(n)
        tau = 0.0
        long_tau = 0.0
        for _ in range(120):
            gain = np.maximum(0.0, full - best[None, :])
            if cells is not None:                      # fixed tiling: one target per fibre cell
                keep = np.zeros_like(gain)
                for c in np.unique(cells):
                    cols = np.where(cells == c)[0]
                    win = cols[np.argmax(gain[:, cols], axis=1)]
                    keep[np.arange(len(grid)), win] = gain[np.arange(len(grid)), win]
                gain = keep
            elif n > fibres:
                kth = np.partition(gain, n - fibres, axis=1)[:, n - fibres]
                gain = np.where(gain >= kth[:, None], gain, 0.0)
            total = gain.sum(axis=1)
            if total.max() <= 1e-9:
                break
            k = int(np.argmax(total / grid))
            if price is not None:
                net = total - price * reference * grid
                if net.max() > 0.0:
                    k = int(np.argmax(net))
            take = np.where(gain[k] > 0.0)[0]
            if len(take) > fibres:
                take = take[np.argsort(-gain[k][take])[:fibres]]
            best[take] = full[k][take]
            tau += grid[k]
            if grid[k] >= 1500.0:
                long_tau += grid[k]
            rows_out.append(np.array([tau, best.sum(), *np.bincount(group, weights=best, minlength=4), long_tau]))
    return np.array(rows_out)


def upper_hull(points):
    """Vertices of the upper concave envelope of (tau, value) points, starting at the origin."""
    order = np.lexsort((-points[:, 1], points[:, 0]))
    hull = []
    for x, y in points[order][:, :2]:
        if hull and x <= hull[-1][0]:
            continue
        while len(hull) >= 2 and (hull[-1][1] - hull[-2][1]) * (x - hull[-2][0]) <= (y - hull[-2][1]) * (hull[-1][0] - hull[-2][0]):
            hull.pop()
        if not hull or y > hull[-1][1]:
            hull.append((x, y))
    return np.array(hull)


def solve(card_dir: Path, bin_slots: int, model: str, export: Path | None = None, max_bins: int = 0) -> dict:
    score_cfg = json.loads((card_dir / "config" / "v4_score_config.json").read_text())
    fibre_cfg = json.loads((card_dir / "config" / "v4_fiber_config.json").read_text())
    scenario = json.loads((card_dir / "config" / "v4_scenario.json").read_text())
    fibres = int(fibre_cfg["field"]["n_fibers"])
    side_n = int(round(math.sqrt(fibres)))
    pitch = math.sqrt(float(fibre_cfg["field"]["fiber_area_deg2"])) + float(fibre_cfg["field"].get("gap_deg", 0.0))
    fov = pitch * side_n
    lat, lon = float(fibre_cfg["site"]["latitude_deg"]), float(fibre_cfg["site"]["longitude_deg"])
    min_alt = float(scenario.get("minimum_altitude_deg", scenario.get("constraints", {}).get("minimum_altitude_deg", 30.0)))
    q0, beta = float(score_cfg["q0"]), float(score_cfg["airmass_exponent"])
    lunar = score_cfg.get("lunar_model", {})
    bands = score_cfg["program"]["bands"]
    mult = score_cfg["program"]["multipliers"]
    top = float(mult["DARK"])
    need_unit = float(score_cfg["flux_zero_point"]) * float(score_cfg["exposure_zero_point_seconds"])
    max_exposure = float(fibre_cfg["exposure"]["max_duration_seconds"])

    targets = rows(card_dir / "public" / "targets.csv")
    ra = np.array([float(t["ra_deg"]) for t in targets])
    dec = np.array([float(t["dec_deg"]) for t in targets])
    weight = np.array([float(t["science_weight"]) for t in targets])
    need = need_unit / np.array([float(t["feature_flux"]) for t in targets])

    # ---- zones (field-sized) and fibre cells inside them --------------------------------------
    grid = Grid(fov)
    keys_list, lookup, zone_ids = [], {}, []
    for a, d in zip(ra, dec):
        key = grid.locate(float(a), float(d))
        if key not in lookup:
            lookup[key] = len(keys_list)
            keys_list.append(key)
        zone_ids.append(lookup[key])
    keys, zone_of = np.array(keys_list), np.array(zone_ids)
    band_index, ra_index = keys[zone_of, 0], keys[zone_of, 1]
    width = np.array([360.0 / grid.ncell[b] for b in band_index])
    nz = len(keys)
    cell_of = (np.minimum(side_n - 1, np.floor(((dec + 90.0) / fov - band_index) * side_n)).astype(int) * side_n
               + np.minimum(side_n - 1, np.floor((ra / width - ra_index) * side_n)).astype(int))
    members = [np.where(zone_of == z)[0] for z in range(nz)]
    target_vectors = np.array([unit_vector(a, d) for a, d in zip(ra, dec)])
    zvec = np.array([target_vectors[m].sum(axis=0) for m in members])
    zvec /= np.linalg.norm(zvec, axis=1)[:, None]
    zra = np.degrees(np.arctan2(zvec[:, 1], zvec[:, 0])) % 360
    zdec = np.degrees(np.arcsin(np.clip(zvec[:, 2], -1, 1)))

    # ---- truth: slots, faults repaired at once, sector events ---------------------------------
    slots = rows(card_dir / "truth" / "v4_weather_truth.csv")
    events = rows(card_dir / "truth" / "v4_events.csv")
    faults = [(utc(e["actual_start_utc"]), utc(e["actual_end_utc"]), float(e["instrument_efficiency_multiplier"]))
              for e in events if e["event_type"] == "instrument_fault"]
    sectors = [{"start": utc(e["actual_start_utc"]), "end": utc(e["actual_end_utc"]),
                "az0": float(e["azimuth_start_deg"]), "az1": float(e["azimuth_end_deg"]),
                "alt0": float(e["min_altitude_deg"]), "alt1": float(e["max_altitude_deg"]),
                "block": e["force_close"] == "true" or e["zero_score"] == "true",
                "gain": float(e["transparency_multiplier"]) * float(e["sky_quality_multiplier"]) / float(e["seeing_multiplier"])}
               for e in events if e["scope_type"] == "HORIZON_SECTOR"]
    slot_rows = []
    for s in slots:
        start = utc(s["timestamp_utc"])
        length = float(s["duration_seconds"])
        if s["is_observable"] != "true":
            slot_rows.append((s["night_id"], start, length, 0.0, 0.0))
            continue
        band_base = float(s["transparency"]) * float(s["sky_quality"]) / float(s["seeing_arcsec"])
        eff = float(s["instrument_efficiency"])
        for f0, f1, m in faults:
            if f0 <= start + 0.5 * length < f1 and m > 0.0:
                eff = min(1.0, eff / m)
        slot_rows.append((s["night_id"], start, length, band_base, eff))
    bins = []
    current: list = []
    for row in slot_rows:
        if current and (row[0] != current[0][0] or len(current) >= bin_slots):
            bins.append(current)
            current = []
        current.append(row)
    if current:
        bins.append(current)
    if max_bins:
        bins = bins[:max_bins]

    sin_lat, cos_lat = math.sin(lat * RAD), math.cos(lat * RAD)
    pen = float(lunar.get("maximum_penalty", 0.0))
    alt_exp = float(lunar.get("altitude_exponent", 1.0))
    scale = float(lunar.get("angular_decay_scale_deg", 35.0))
    table_alt = np.linspace(0.5, 90.0, 360)
    table_tr = np.array([airmass(a) ** -beta for a in table_alt])

    rows_i, cols_i, vals = [], [], []          # LP matrix in triplets
    var_zone, var_bin, var_qt = [], [], []
    qmax = np.zeros(nz)
    open_seconds = 0.0
    bin_open_seconds = np.zeros(len(bins))
    for b, group in enumerate(bins):
        mid = 0.5 * (group[0][1] + group[-1][1] + group[-1][2])
        lst = lst_deg(mid, lon) * RAD
        zen = np.array([cos_lat * math.cos(lst), cos_lat * math.sin(lst), sin_lat])
        north = np.array([-sin_lat * math.cos(lst), -sin_lat * math.sin(lst), cos_lat])
        east = np.array([-math.sin(lst), math.cos(lst), 0.0])
        sin_alt = zvec @ zen
        alt = np.degrees(np.arcsin(np.clip(sin_alt, -1.0, 1.0)))
        az = np.degrees(np.arctan2(zvec @ east, zvec @ north)) % 360.0
        visible = alt >= min_alt
        gain = np.ones(nz)
        for ev in sectors:
            if not (ev["start"] <= mid < ev["end"]):
                continue
            inside = (az >= ev["az0"]) & (az <= ev["az1"]) if ev["az0"] <= ev["az1"] else (az >= ev["az0"]) | (az <= ev["az1"])
            inside &= (alt >= ev["alt0"]) & (alt <= ev["alt1"])
            if ev["block"]:
                visible &= ~inside
            else:
                gain = np.where(inside, gain * ev["gain"], gain)
        moon = np.array(unit_vector(*moon_radec(mid)))
        sun = np.array(unit_vector(*sun_radec(mid)))
        moon_alt = float(moon @ zen)
        factor = np.ones(nz)
        if moon_alt > 0.0 and pen > 0.0:
            strength = pen * (1.0 - float(np.clip(moon @ sun, -1.0, 1.0))) / 2.0 * moon_alt ** alt_exp
            sep = np.degrees(np.arccos(np.clip(zvec @ moon, -1.0, 1.0)))
            factor = np.clip(1.0 - strength * np.exp(-sep / scale), 0.0, 1.0)
        geometry = factor * np.interp(alt, table_alt, table_tr) / q0
        quality_time = np.zeros(nz)
        length_open = 0.0
        for _, _, length, band_base, eff in group:
            if band_base <= 0.0:
                continue
            length_open += length
            band_q = band_base * geometry
            m = np.where(band_q >= float(bands["DARK"]), float(mult["DARK"]),
                         np.where(band_q >= float(bands["BRIGHT"]), float(mult["BRIGHT"]), float(mult["BACKUP"])))
            quality_time += length * eff * band_base * geometry * gain * m / top
        if length_open <= 0.0:
            continue
        open_seconds += length_open
        bin_open_seconds[b] = length_open
        idx = np.where(visible & (quality_time > 0.0))[0]
        qmax[idx] = np.maximum(qmax[idx], quality_time[idx] / length_open)
        var_zone.extend(idx.tolist())
        var_bin.extend([b] * len(idx))
        var_qt.extend(quality_time[idx].tolist())

    nx = len(var_zone)
    # ---- zone value functions as concave segments ---------------------------------------------
    seg_zone, seg_len, seg_slope = [], [], []
    zone_points: dict = {}
    for z in range(nz):
        m = members[z]
        cap = np.minimum(need[m], max_exposure * qmax[z])
        if qmax[z] <= 0.0 or not len(m):
            continue
        density = top * weight[m] / need[m]
        order = np.argsort(-density)
        if model in ("shared", "shared_cells"):
            points = shared_points(top * weight[m], need[m], max_exposure * qmax[z], fibres,
                                   cell_of[m] if model == "shared_cells" else None)
            zone_points[z] = points
            hull = upper_hull(points)
            taus, values = hull[:, 0], hull[:, 1]
        elif model == "loose":
            tau_sat = max(float(cap.max()), float(cap.sum()) / fibres)
            taus = np.concatenate([[0.0], np.geomspace(min(30.0, tau_sat / 2), tau_sat, 28)])
            values = zone_value(density[order], cap[order], fibres, taus)
        else:
            cells = cell_of[m]
            tau_sat = max(float(cap[cells == c].sum()) for c in np.unique(cells))
            taus = np.concatenate([[0.0], np.geomspace(min(30.0, tau_sat / 2), tau_sat, 28)])
            values = np.zeros(len(taus))
            for c in np.unique(cells):
                pick = order[cells[order] == c]
                values += cell_value(density[pick], cap[pick], taus)
        if len(taus) < 2:
            continue
        slopes = np.minimum.accumulate(np.diff(values) / np.diff(taus))
        for k, slope in enumerate(slopes):
            if slope > 1e-12:
                seg_zone.append(z)
                seg_len.append(taus[k + 1] - taus[k])
                seg_slope.append(slope)
    ns = len(seg_zone)
    nb = len(bins)
    # rows 0..nz-1: sum_k u - sum_s QT x <= 0 ; rows nz..nz+nb-1: sum_z x <= 1
    rows_i = np.concatenate([np.array(var_zone), nz + np.array(var_bin), np.array(seg_zone)])
    cols_i = np.concatenate([np.arange(nx), np.arange(nx), nx + np.arange(ns)])
    vals = np.concatenate([-np.array(var_qt), np.ones(nx), np.ones(ns)])
    a_ub = coo_matrix((vals, (rows_i, cols_i)), shape=(nz + nb, nx + ns)).tocsr()
    b_ub = np.concatenate([np.zeros(nz), np.ones(nb)])
    cost = np.concatenate([np.zeros(nx), -np.array(seg_slope)])
    bounds = [(0.0, None)] * nx + [(0.0, float(v)) for v in seg_len]
    began = time.time()
    result = linprog(cost, A_ub=a_ub, b_ub=b_ub, bounds=bounds, method="highs")
    if result.status != 0:
        raise SystemExit(f"LP failed: {result.message}")
    if export is not None:
        export.mkdir(parents=True, exist_ok=True)
        allocation = np.zeros((nz, nb))
        allocation[np.array(var_zone), np.array(var_bin)] = result.x[:nx]
        quality_seconds = np.bincount(np.array(var_zone), weights=np.array(var_qt) * result.x[:nx], minlength=nz)
        np.savez_compressed(export / "allocation.npz", x=allocation, zone_keys=keys, zone_ra=zra, zone_dec=zdec,
                            bin_start=np.array([group[0][1] for group in bins]),
                            bin_end=np.array([group[-1][1] + group[-1][2] for group in bins]),
                            bin_open_seconds=bin_open_seconds, zone_quality_seconds=quality_seconds,
                            zone_marginals=result.ineqlin.marginals[:nz], bin_marginals=result.ineqlin.marginals[nz:])
        (export / "summary.json").write_text(json.dumps({"model": model, "zone_grid": "planner Grid, target encounter order",
            "fov": fov, "zones": nz, "bins": nb, "variables": nx + ns, "bound": float(-result.fun),
            "truncated": bool(max_bins), "data": "self-generated local truth; perfect-weather guidance is unattainable online",
            "dual_sign": "raw marginals of minimization of negative score; negate for score value"}, indent=2) + "\n", encoding="utf-8")
    detail = None
    if zone_points:
        tau_z = np.bincount(np.array(var_zone), weights=np.array(var_qt) * result.x[:nx], minlength=nz)
        groups = np.zeros(4)
        long_tau = 0.0
        for z, points in zone_points.items():
            ok = points[points[:, 0] <= tau_z[z] + 1e-6]
            pick = ok[np.argmax(ok[:, 1])]
            groups += pick[2:6]
            long_tau += pick[6]
        ceilings = np.bincount(np.searchsorted(GROUP_EDGES, need), weights=top * weight, minlength=4)
        detail = {"group_share": (groups / ceilings).round(3).tolist(), "schedule_value": float(groups.sum()),
                  "long_exposure_share": float(long_tau / max(1.0, tau_z.sum()))}
    return {"detail": detail, "bound": -result.fun, "ceiling": float(weight.sum() * top), "zones": nz, "bins": nb, "variables": nx + ns,
            "open_hours": open_seconds / 3600.0, "seconds": time.time() - began}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--eval-set", default=None)
    parser.add_argument("--cards", nargs="+", default=["a", "b", "c", "d"])
    parser.add_argument("--bin-slots", type=int, default=4, help="weather slots per time bin (4 = one hour)")
    parser.add_argument("--models", nargs="+", default=["loose", "cells"])
    parser.add_argument("--export", type=Path, help="export per-card/model allocations, quality seconds and raw dual marginals")
    parser.add_argument("--max-bins", type=int, default=0, help="small process check only; 0 = full season")
    args = parser.parse_args()
    cards = cards_dir(args.eval_set)
    print("| 卡 | 全部满分 | " + " | ".join(args.models) + " | zones | bins |")
    print("|---|---:|" + "---:|" * (len(args.models) + 2))
    for card in args.cards:
        out = [solve(cards / card, args.bin_slots, model, args.export / card / model if args.export else None, args.max_bins) for model in args.models]
        print(f"| {card.upper()} | {out[0]['ceiling']:,.0f} | " + " | ".join(f"{o['bound']:,.0f}" for o in out)
              + f" | {out[0]['zones']} | {out[0]['bins']} |", flush=True)
        print(f"  ({', '.join(str(o['variables']) + ' vars ' + format(o['seconds'], '.0f') + ' s' for o in out)})", file=sys.stderr, flush=True)
        for model, o in zip(args.models, out):
            if o["detail"]:
                print(f"  {model}: 按所需秒数 <600 / 600-1500 / 1500-3000 / >3000 的实得比例 {o['detail']['group_share']}，"
                      f"可执行方案合计 {o['detail']['schedule_value']:,.0f}，长曝光（>=1500 质量秒）占用时间 {o['detail']['long_exposure_share']:.0%}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
