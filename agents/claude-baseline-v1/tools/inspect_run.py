#!/usr/bin/env python3
"""Diagnose one run folder produced by tools/run_eval.py --keep-bulky.

    python3 tools/inspect_run.py results/<tag>/<card> [--card-dir <self-generated card>]

Reads decisions.csv / observations.csv / score_report.json (the judge's own outputs) and,
for developer analysis only, the SELF-GENERATED truth of the local evaluation card. Nothing
here is available to, or used by, the agent.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import cards_dir  # noqa: E402

CARDS = cards_dir()


def quantiles(values, points=(0, 10, 25, 50, 75, 90, 100)):
    if not values:
        return "-"
    ordered = sorted(values)
    return " / ".join(f"{ordered[min(len(ordered) - 1, int(p / 100 * (len(ordered) - 1) + 0.5))]:.4g}" for p in points)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--card-dir", type=Path, default=None)
    args = parser.parse_args()
    run = args.run
    card = run.name
    if args.card_dir is None and not (CARDS / card).is_dir():
        # quick_sim folders are named freely; the card letter is in the report
        card = json.loads((run / "score_report.json").read_text())["scenario"].split("-")[1]
    card_dir = args.card_dir or CARDS / card
    report = json.loads((run / "score_report.json").read_text())
    decisions = list(csv.DictReader((run / "decisions.csv").open()))
    observations = list(csv.DictReader((run / "observations.csv").open()))
    targets = {r["target_id"]: r for r in csv.DictReader((card_dir / "public" / "targets.csv").open())}

    print(f"== {run}  total {report['total']:.1f}")
    print("components:", json.dumps(report["components"]))
    print("counts:", json.dumps(report["counts"]))
    print("by class:", json.dumps(report["by_class"]))

    observes = [d for d in decisions if d["action"] == "observe"]
    waits = [d for d in decisions if d["action"] == "wait"]
    reports = [d for d in decisions if d["action"] == "report"]
    durations = [int(d["duration_seconds"]) for d in observes]
    print(f"\nobserve {len(observes)}  wait rows {len(waits)}  report {len(reports)}")
    print(f"exposure seconds total {sum(durations) / 3600:.0f} h; quantiles 0/10/25/50/75/90/100: {quantiles(durations)}")
    buckets = Counter(min(3600, (d // 300) * 300) for d in durations)
    print("duration histogram (300 s bins):", " ".join(f"{k}:{v}" for k, v in sorted(buckets.items())))
    assigned = sum(int(d["assigned_count"]) for d in observes)
    hit = sum(int(d["hit_count"]) for d in observes)
    print(f"assigned {assigned}, hits {hit} ({100 * hit / max(1, assigned):.2f}%), "
          f"mean assigned per exposure {assigned / max(1, len(observes)):.2f}")
    programs = Counter(d["program"] for d in observes)
    print("declared programs:", dict(programs))

    valid = [o for o in observations if o["valid"] == "true"]
    mult = Counter(o["prog_mult"][:4] for o in valid)
    print("hit multipliers:", dict(sorted(mult.items())))
    zero = sum(1 for o in valid if float(o["score"]) == 0.0)
    saturated = sum(1 for o in valid if float(o["factor"]) >= 0.999)
    print(f"hits with zero score {zero} ({100 * zero / max(1, len(valid)):.1f}%), saturated {saturated} "
          f"({100 * saturated / max(1, len(valid)):.1f}%)")
    print("hit quality quantiles:", quantiles([float(o["quality"]) for o in valid if float(o["score"]) > 0]))

    # per-target outcome
    best = defaultdict(float)
    factor = defaultdict(float)
    visits = Counter()
    for o in valid:
        tid = o["target_id"]
        visits[tid] += 1
        best[tid] = max(best[tid], float(o["score"]))
        factor[tid] = max(factor[tid], float(o["factor"]))
    wasted = 0.0
    fibre_seconds = 0.0
    dur_of = {d["observe_index"]: int(d["duration_seconds"]) for d in observes}
    by_target = defaultdict(list)
    for o in valid:
        by_target[o["target_id"]].append((float(o["score"]), dur_of.get(o["observe_index"], 0)))
    for tid, rows in by_target.items():
        top = max(rows)[0]
        for score, dur in rows:
            fibre_seconds += dur
            if score < top or (score == top and rows.count((score, dur)) > 1 and False):
                wasted += dur
    print(f"fibre-seconds on hits {fibre_seconds / 1e6:.2f} M, of which superseded by a better exposure of the same "
          f"target {wasted / 1e6:.2f} M ({100 * wasted / max(1.0, fibre_seconds):.1f}%)")
    print("visits per observed target:", dict(sorted(Counter(visits.values()).items())))

    print("\nclass     n   observed  complete(g>=.5)  saturated   score / ceiling(w*1.2)")
    for name in sorted({t["target_class"] for t in targets.values()}):
        ids = [tid for tid, t in targets.items() if t["target_class"] == name]
        w = float(targets[ids[0]]["science_weight"])
        got = sum(best[tid] for tid in ids)
        print(f"{name:5s} {len(ids):6d} {sum(1 for t in ids if visits[t]):8d} {sum(1 for t in ids if factor[t] >= 0.5):10d} "
              f"{sum(1 for t in ids if factor[t] >= 0.999):12d}   {got:9.1f} / {len(ids) * w * 1.2:9.1f} "
              f"({100 * got / (len(ids) * w * 1.2):.1f}%)")
    required = [tid for tid, t in targets.items() if t["required"] == "true"]
    missing = [tid for tid in required if factor[tid] < 0.5]
    print(f"\nrequired {len(required)}, missing {len(missing)}: never hit {sum(1 for t in missing if not visits[t])}, "
          f"hit but below threshold {sum(1 for t in missing if visits[t])}")
    if missing:
        fluxes = sorted(float(targets[t]["feature_flux"]) for t in missing)
        print("  missing required flux quantiles:", quantiles(fluxes))
        print("  missing required best factor quantiles:", quantiles([factor[t] for t in missing]))
        ras = Counter(int(float(targets[t]["ra_deg"]) // 30) * 30 for t in missing)
        decs = Counter(int(float(targets[t]["dec_deg"]) // 20) * 20 for t in missing)
        print("  by RA (30 deg):", dict(sorted(ras.items())), " by Dec (20 deg):", dict(sorted(decs.items())))
    bands = report.get("uniformity_bands", {})
    if bands:
        low = sorted(bands.items(), key=lambda kv: kv[1])[:6]
        print("lowest uniformity bands:", ", ".join(f"{k}={v:.2f}" for k, v in low),
              f"| mean {sum(bands.values()) / len(bands):.3f}")
    for request in report.get("observation_requests", []):
        print(f"request {request['request_id']}: {request['completed_count']}/{request['minimum_completed']} "
              f"needed of {len(request['target_ids'])}, completed={request['completed']} "
              f"({request['issued_at_utc']} -> {request['deadline_utc']})")

    # self-generated truth, for developer analysis only
    events_path = card_dir / "truth" / "v4_events.csv"
    if events_path.exists():
        events = list(csv.DictReader(events_path.open()))
        faults = [e for e in events if e["event_type"] == "instrument_fault"]
        for fault in faults:
            hits = [d["start_utc"] for d in reports if fault["actual_start_utc"] <= d["start_utc"] < fault["actual_end_utc"]]
            print(f"fault {fault['event_id']} x{float(fault['instrument_efficiency_multiplier']):.2f} "
                  f"{fault['actual_start_utc']} -> {fault['actual_end_utc']}: reports inside {hits[:3]}")
        print("reports at:", [d["start_utc"] for d in reports])
        truth = list(csv.DictReader((card_dir / "truth" / "v4_weather_truth.csv").open()))
        closed = sum(1 for r in truth if r["is_observable"] != "true")
        print(f"truth: {len(truth)} slots, {closed} closed ({100 * closed / len(truth):.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
