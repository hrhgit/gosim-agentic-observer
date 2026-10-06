"""Pre-registered gates. No threshold is tuned on final-test outcomes."""
import numpy as np


def compare(candidate, reference):
    baseline = {r["id"]: r for r in reference}
    if len(baseline) != len(reference) or {r["id"] for r in candidate} != set(baseline) or len(candidate) != len(reference):
        raise ValueError("Paired scenarios must match exactly without duplicates")
    changes = [r["total"]-baseline[r["id"]]["total"] for r in candidate]
    mean_baseline = float(np.mean([r["total"] for r in reference]))
    relative = float(np.mean(changes)) / max(abs(mean_baseline), 1.)
    cards = {}
    for card in sorted({r["card"] for r in candidate}):
        rows = [r for r in candidate if r["card"] == card]
        original = np.mean([baseline[r["id"]]["total"] for r in rows])
        cards[card] = float(np.mean([r["total"]-baseline[r["id"]]["total"] for r in rows])) / max(abs(float(original)), 1.)
    complete = all(r["completion"] == "survey_complete" for r in candidate + reference)
    # Resample complete paired scenes, stratified by card, never individual decision rows.
    rng = np.random.default_rng(9143)
    groups = [[r["total"]-baseline[r["id"]]["total"] for r in candidate if r["card"] == card] for card in cards]
    boot = [np.mean([v for group in groups for v in rng.choice(group, len(group), replace=True)]) for _ in range(2000)]
    return dict(relative_improvement=relative, mean_difference=float(np.mean(changes)), by_card=cards,
                interval_95=np.quantile(boot, [.025, .975]).tolist(), complete=complete,
                promotion_passed=bool(complete and relative >= .01 and all(v >= -.01 for v in cards.values())),
                interpretation="Local self-generated whole-scenario comparison; not an online prediction")


def require_stage(protocol, stage):
    if stage > 1 and not protocol.get("gates", {}).get(str(stage-1), {}).get("passed", False):
        raise ValueError(f"Stage {stage-1} has not passed; expansion is forbidden")
