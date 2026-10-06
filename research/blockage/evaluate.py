"""Weighted reliability, paired reference comparisons, and preregistered gates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .common import write_json


def weights(frame):
    """Equal cards; equal baseline/probe mass within a card; prescribed within-source weights."""
    w = np.zeros(len(frame))
    for _, ix in frame.groupby(["card", "source"], sort=False).indices.items():
        ix = np.asarray(ix)
        values = frame.iloc[ix].f_duration.to_numpy() if frame.iloc[ix[0]].source == "baseline" else np.ones(len(ix))
        w[ix] = values / values.sum() / frame[frame.card == frame.iloc[ix[0]].card].source.nunique()
    return w * len(frame) / max(w.sum(), 1e-12)


def reliability(y, p, w):
    bins = []
    ids = np.minimum((p * 10).astype(int), 9)
    for b in range(10):
        mask = ids == b
        n = int(mask.sum())
        pred = float(np.average(p[mask], weights=w[mask])) if n else None
        actual = float(np.average(y[mask], weights=w[mask])) if n else None
        bins.append(dict(bin=b, lo=b / 10, hi=(b + 1) / 10, count=n,
                         weight=float(w[mask].sum()), predicted=pred, observed=actual,
                         bias=None if not n else pred - actual,
                         qualifying=n >= 50, passes=None if n < 50 else abs(pred - actual) <= 0.05))
    eligible = [b for b in bins if b["qualifying"]]
    return dict(bin_details=bins, eligible_bins=len(eligible), passes=bool(eligible) and all(b["passes"] for b in eligible))


def metrics(frame, p, threshold):
    y = frame.zero.to_numpy(dtype=float)
    w = weights(frame)
    pc = np.clip(p, 1e-6, 1 - 1e-6)
    brier = float(np.average((p - y) ** 2, weights=w))
    logloss = float(np.average(-y * np.log(pc) - (1 - y) * np.log1p(-pc), weights=w))
    # Operational gate uses baseline hours; probe frequency has no hours interpretation.
    base = frame.source.to_numpy() == "baseline"
    flagged = p >= threshold
    seconds = frame.f_duration.to_numpy()
    avoid = frame.avoidable_hint.to_numpy(dtype=bool)
    net = float(np.sum(seconds[base & flagged & avoid]) - np.sum(seconds[base & flagged & (y == 0)])) / max(float(seconds[base].sum()), 1)
    detail = {}
    for name, mask in (("first_zero", (y == 1) & (frame.first_zero.to_numpy() == 1)),
                       ("subsequent_zero", (y == 1) & (frame.first_zero.to_numpy() == 0)),
                       ("scored", y == 0)):
        detail[name] = dict(count=int(mask.sum()), flagged=int((mask & flagged).sum()),
                            mean_probability=float(np.average(p[mask], weights=w[mask])) if mask.any() else None,
                            baseline_flagged_hours=float(seconds[mask & base & flagged].sum() / 3600))
    return dict(brier=brier, log_loss=logloss, reliability=reliability(y, p, w), threshold=float(threshold),
                net_saving=net, avoidable_flagged_hours=float(seconds[base & flagged & avoid].sum() / 3600),
                scored_flagged_hours=float(seconds[base & flagged & (y == 0)].sum() / 3600),
                baseline_total_hours=float(seconds[base].sum() / 3600), sequence_detail=detail)


def choose_threshold(frame, p):
    """Only inner held-out training cards; tie breaks towards flagging fewer exposures."""
    candidates = np.r_[np.linspace(0, 1, 101), 1.000001]
    base = frame.source.to_numpy() == "baseline"
    duration = frame.f_duration.to_numpy()
    signed = duration * (frame.avoidable_hint.to_numpy() - (frame.zero.to_numpy() == 0))
    scores = [float(signed[base & (p >= t)].sum()) for t in candidates]
    return float(candidates[len(scores) - 1 - int(np.argmax(scores[::-1]))])


def report(predictions, fold_metadata):
    result = dict(metric_weighting="equal sources within each card; baseline duration, probe count; net saving baseline only",
                  net_saving_is_proxy=True, no_causal_rescue_claim=True, models={})
    for model in predictions.model.unique():
        cards = {}
        for card in sorted(predictions.card.unique()):
            sub = predictions[(predictions.card == card) & (predictions.model == model)].reset_index(drop=True)
            met = metrics(sub, sub.probability.to_numpy(), sub.threshold.iloc[0])
            ref = predictions[(predictions.card == card) & (predictions.model == "R2")].reset_index(drop=True)
            reference = metrics(ref, ref.probability.to_numpy(), ref.threshold.iloc[0])
            met["brier_ratio_r2"] = met["brier"] / max(reference["brier"], 1e-12)
            met["log_loss_change_r2"] = met["log_loss"] - reference["log_loss"]
            met["gates"] = dict(reliability=met["reliability"]["passes"], brier=met["brier_ratio_r2"] <= 0.9, net_saving=met["net_saving"] >= 0.01)
            met["passes_all"] = all(met["gates"].values())
            met["by_source"] = {source: metrics(group.reset_index(drop=True), group.probability.to_numpy(), group.threshold.iloc[0]) for source, group in sub.groupby("source")}
            cards[card] = met
        all_count = sum(c["passes_all"] for c in cards.values())
        probability_count = sum(c["gates"]["reliability"] and c["gates"]["brier"] for c in cards.values())
        result["models"][model] = dict(cards=cards, macro_mean={k: float(np.mean([c[k] for c in cards.values()])) for k in ("brier", "log_loss", "net_saving", "brier_ratio_r2")},
                                       passing_cards=all_count, probability_passing_cards=probability_count,
                                       verdict="proceed_to_closed_loop" if all_count >= 3 else "probability_only" if probability_count >= 3 else "stop_keep_R2")
    result["folds"] = fold_metadata
    eligible = [m for m in ("M1", "M2", "M3") if result["models"].get(m, {}).get("verdict") in ("proceed_to_closed_loop", "probability_only")]
    result["overall_verdict"] = "model_gate_passed" if eligible else "stop_keep_R2"
    # Do not pick the deployable model by outer-test scores; explicit further validation is required.
    result["eligible_models"] = eligible
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    write_json(args.out / "report.json", report(pd.read_parquet(args.out / "predictions.parquet"), json.loads((args.out / "folds.json").read_text())))


if __name__ == "__main__":
    main()
