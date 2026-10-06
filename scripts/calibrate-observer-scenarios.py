#!/usr/bin/env python3
"""Offline calibration study. Uses PUBLIC experiment seeds, never official runs.

Fit bounds on the training split and evaluate an independent strategy and seed
split. The result is a review artifact, not automatic approval to enable a phase.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
from statistics import fmean, median, pstdev, quantiles
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from project_platform.scenario_instances import (
    PANEL_VERSION, POLICIES, HOLDOUT_POLICY, acceptance_reasons, benchmark_policy,
    calibrated_score, directory_digest, generate_candidate, measure_difficulty,
)


def sample(args):
    template, index = args
    seed = hashlib.sha256(f"public-calibration-experiment-v1/{index}".encode()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="observer-calibration-") as temp:
        scenario = Path(temp) / "scenario"
        record = generate_candidate(template, scenario, seed=seed, candidate=0)
        # The study already runs one sample per worker process.
        difficulty = measure_difficulty(scenario, workers=1)
        holdout = benchmark_policy(scenario, HOLDOUT_POLICY)
    return {"index": index, "instance_digest": record["instance_digest"],
            "difficulty": difficulty, "holdout": holdout,
            "adjusted_holdout_score": calibrated_score(holdout["score"], difficulty)}


def fit_profile(rows, template_digest):
    values = {"span": [r["difficulty"]["span"] for r in rows],
              "open_fraction": [r["difficulty"]["open_fraction"] for r in rows]}
    for name in POLICIES:
        values[name] = [calibrated_score(r["difficulty"]["policies"][name]["score"],
                                         r["difficulty"]) for r in rows]
    bounds = {}
    for name, samples in values.items():
        cuts = quantiles(samples, n=20, method="inclusive")
        bounds[name] = [cuts[0], cuts[-1]]
    return {"schema_version": "observer-calibration-profile-v1", "panel_version": PANEL_VERSION,
            "template_digest": template_digest, "training_samples": len(rows), "bounds": bounds}


def summarize(rows, scale):
    if not rows:
        return {"count": 0}
    raw = [r["holdout"]["score"] for r in rows]
    # Both series are in the same units. The fixed scale cannot correct the
    # individual seed; it is the control for measuring the adjustment's effect.
    fixed = [10000 * (r["holdout"]["score"] - r["difficulty"]["wait_score"]) / scale for r in rows]
    adjusted = [r["adjusted_holdout_score"] for r in rows]
    return {"count": len(rows), "raw_holdout_range": [min(raw), max(raw)],
            "fixed_scale_holdout_sd": pstdev(fixed), "adjusted_holdout_sd": pstdev(adjusted),
            "adjusted_holdout_mean": fmean(adjusted),
            "required_missing_range": [min(r["holdout"]["required_missing"] for r in rows),
                                       max(r["holdout"]["required_missing"] for r in rows)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", type=Path)
    parser.add_argument("--train", type=int, default=20)
    parser.add_argument("--validate", type=int, default=10)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.train < 20 or args.validate < 10 or not 1 <= args.workers <= 4:
        parser.error("use at least 20 training / 10 validation seeds and 1–4 workers")
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows = []
        for row in pool.map(sample, ((args.template, i) for i in range(args.train + args.validate))):
            rows.append(row)
            print(f"Completed sample {len(rows)}/{args.train + args.validate}", flush=True)
    profile = fit_profile(rows[:args.train], directory_digest(args.template))
    validation = rows[args.train:]
    for row in rows:
        row["rejections"] = acceptance_reasons(row["difficulty"], profile)
    scale = median(r["difficulty"]["span"] for r in rows[:args.train])
    summary = {"training_seeds": args.train, "validation_seeds": args.validate,
               "all_validation": summarize(validation, scale),
               "accepted_validation": summarize([r for r in validation if not r["rejections"]], scale),
               "production_ready": False}
    result = {"summary": summary, "candidate_profile": profile, "samples": rows,
              "notice": "Public experiment seeds; review on private competition templates before activation."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
