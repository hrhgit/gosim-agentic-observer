"""Compare baseline observed time and omniscient allocation on the planner's 5-degree grid."""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
from core.astro import RAD, lst_deg  # noqa: E402
from core.catalog import Grid  # noqa: E402


def equatorial(alt, az, t, lat, lon):
    sa, ca = math.sin(alt * RAD), math.cos(alt * RAD)
    sl, cl = math.sin(lat * RAD), math.cos(lat * RAD)
    dec = math.asin(max(-1, min(1, sa * sl + ca * math.cos(az * RAD) * cl)))
    ha = math.atan2(-ca * math.sin(az * RAD), sa * cl - ca * math.cos(az * RAD) * sl)
    return (lst_deg(t, lon) - ha / RAD) % 360, dec / RAD


def compare(allocation: Path, baseline: Path, card_dir: Path):
    from datetime import datetime
    def ts(s):
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    data = np.load(allocation)
    grid = Grid(5.0)
    lp, actual = {}, {}
    for i, (ra, dec) in enumerate(zip(data["zone_ra"], data["zone_dec"])):
        key = grid.locate(float(ra), float(dec))
        lp[key] = lp.get(key, 0) + float((data["x"][i] * data["bin_open_seconds"]).sum())
    # observations.csv is one row per fibre. It must not multiply the exposure's time.
    with (baseline / "observations.csv").open(encoding="utf-8") as handle:
        observed = {int(r["observe_index"]) for r in csv.DictReader(handle)}
    site = json.loads((card_dir / "config/v4_fiber_config.json").read_text())["site"]
    with (baseline / "decisions.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["action"] != "observe" or not row["observe_index"] or int(row["observe_index"]) not in observed:
                continue
            ra, dec = equatorial(float(row["alt_deg"]), float(row["az_deg"]),
                                 (ts(row["start_utc"]) + ts(row["end_utc"])) / 2,
                                 float(site["latitude_deg"]), float(site["longitude_deg"]))
            key = grid.locate(ra, dec)
            actual[key] = actual.get(key, 0) + float(row["duration_seconds"])
    if not lp or not actual or sum(lp.values()) <= 0 or sum(actual.values()) <= 0:
        raise ValueError("cannot compare empty allocations")
    keys = sorted(lp.keys() | actual.keys())
    total_lp, total_actual = sum(lp.values()), sum(actual.values())
    bins = [dict(key=list(k), oracle_seconds=lp.get(k, 0), baseline_seconds=actual.get(k, 0),
                 oracle_share=lp.get(k, 0) / total_lp, baseline_share=actual.get(k, 0) / total_actual) for k in keys]
    distance = sum(abs(r["oracle_share"] - r["baseline_share"]) for r in bins) / 2
    return dict(total_variation=distance, proceed=distance >= 0.1, oracle_hours=total_lp / 3600,
                baseline_hours=total_actual / 3600, bin_details=bins,
                limitation="allocations differ in time spent behind closures and within-field target feasibility; distance is only a screening gate")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allocation", type=Path, required=True)
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--card-dir", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    result = compare(a.allocation, a.baseline, a.card_dir)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "bin_details"}, indent=2))


if __name__ == "__main__":
    main()
