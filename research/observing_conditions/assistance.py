"""Public prediction postprocessing and scene-based measures of saved decisions.

Only predictions and eligibility enter the decision rule. Answers are used by
the grader, never by the rule. A 90% marginal interval is not a confidence
statement about an entire group or its ranking.
"""
from __future__ import annotations

import numpy as np


def decide(predictions, rule, eligible=None):
    p = np.asarray(predictions)
    if p.ndim != 3 or p.shape[-1] != 9 or not np.isfinite(p).all():
        raise ValueError('expected finite [group, candidate, 9] predictions')
    valid = np.ones(p.shape[:2], bool) if eligible is None else np.asarray(eligible, bool)
    if valid.shape != p.shape[:2]:
        raise ValueError('eligibility shape mismatch')
    best = np.where(valid, p[..., 0], -np.inf).argmax(axis=1)
    rows = np.arange(len(p))
    lower_best = np.where(valid, p[..., 1], -np.inf).max(axis=1)
    blocked = valid & (p[..., 5] >= rule['block_threshold'])
    dominated = valid & (p[..., 3] < rule['dominance_ratio'] * lower_best[:, None])
    removed = blocked | dominated
    # Never discard the mean-quality leader or the last eligible candidate.
    removed[rows, best] = False
    kept = valid & ~removed
    top_mean = p[rows, best, 0]
    others = np.where(valid, p[..., 0], -np.inf).copy()
    others[rows, best] = -np.inf
    second = others.max(axis=1)
    gap = (top_mean - second) / np.maximum(top_mean, 1e-8)
    recommend = ((valid.sum(axis=1) > 0) & (top_mean > 1e-8)
                 & (gap >= rule['recommend_gap'])
                 & (p[rows, best, 5] <= rule['recommend_block_max'])
                 & (p[rows, best, 4] >= rule['recommend_usable_min']))
    if not rule.get('recommend_enabled', True):
        recommend[:] = False
    if not rule.get('exclude_enabled', True):
        removed[:] = False
        blocked[:] = False
        dominated[:] = False
        kept = valid.copy()
    return dict(removed=removed, blocked=blocked & removed,
                dominated=dominated & removed, kept=kept,
                recommended=best, recommend=recommend, valid=valid, gap=gap)


def sufficient(y, p, decision):
    """Per-group counts, enabling bootstrap of whole scenes without row independence."""
    q, u = y[..., 0], y[..., 1]
    valid, kept, removed = (decision[k] for k in ('valid', 'kept', 'removed'))
    best = np.where(valid, q, -np.inf).max(axis=1)
    best = np.maximum(best, 0)
    kept_best = np.where(kept, q, -np.inf).max(axis=1)
    kept_best = np.maximum(kept_best, 0)
    good = valid & (u >= .5)
    near_best = good & (q >= .95 * best[:, None]) & (best[:, None] > 1e-8)
    rankable = near_best.any(axis=1)
    choice = decision['recommended']; rows = np.arange(len(q))
    selected = q[rows, choice]
    rec = decision['recommend']
    # Ties count as retaining a best candidate; all-zero groups have no ranking value.
    retains_best = (kept & (q >= best[:, None] - 1e-6)).any(axis=1)
    return np.column_stack([
        valid.sum(axis=1), removed.sum(axis=1), (removed & good).sum(axis=1),
        (decision['blocked'] & good).sum(axis=1), decision['blocked'].sum(axis=1),
        decision['dominated'].sum(axis=1), (removed & near_best).sum(axis=1),
        valid.shape[1] - valid.sum(axis=1), rankable,
        rankable & ~(kept & near_best).any(axis=1),
        (best > 1e-8) & ~retains_best, best, best - kept_best,
        rec, rec & ((u[rows, choice] < .5) | (selected < .95 * best)),
        np.where(rec, best - selected, 0), np.where(rec, best, 0),
        np.where(rec, valid.sum(axis=1) - 1, removed.sum(axis=1)),
        (best > 1e-8) & (selected < .95 * best), best - selected,
        (removed.any(axis=1) | rec),
    ]).astype(float)


def summarize(stats):
    sums = np.sum(stats, axis=0)
    def ratio(a, b):
        return float(a / b) if b > 0 else None
    n = len(stats)
    return dict(groups=n, candidates=int(sums[0]), excluded=int(sums[1]),
        exclusion_fraction=ratio(sums[1], sums[0]),
        excluded_but_usable=int(sums[2]),
        # Relative exclusion can discard a usable, but worse, candidate.
        excluded_usable_fraction=ratio(sums[2], sums[1]),
        blocked_excluded=int(sums[4]), blocked_false_exclusion=int(sums[3]),
        blocked_false_exclusion_rate=ratio(sums[3], sums[4]),
        dominated_excluded=int(sums[5]), near_best_excluded=int(sums[6]),
        deterministic_ineligible=int(sums[7]),
        rankable_groups=int(sums[8]), lost_all_near_best=int(sums[9]),
        near_best_loss_rate=ratio(sums[9], sums[8]),
        best_loss_groups=int(sums[10]), best_loss_group_rate=ratio(sums[10], n),
        shortlist_quality_loss=ratio(sums[12], sums[11]),
        recommendation_groups=int(sums[13]), recommendation_fraction=ratio(sums[13], n),
        recommendation_failures=int(sums[14]), recommendation_failure_rate=ratio(sums[14], sums[13]),
        recommendation_quality_loss=ratio(sums[15], sums[16]),
        saved_candidate_comparisons=int(sums[17]),
        workload_reduction=ratio(sums[17], sums[0]),
        mean_candidates_left_to_compare=ratio(sums[0]-sums[17], n),
        always_choose_mean_failure_rate=ratio(sums[18], n),
        always_choose_mean_quality_loss=ratio(sums[19], sums[11]),
        actionable_groups=int(sums[20]))


RISK_KEYS = ('blocked_false_exclusion_rate', 'near_best_loss_rate',
             'best_loss_group_rate', 'recommendation_failure_rate',
             'recommendation_quality_loss', 'shortlist_quality_loss',
             'workload_reduction', 'recommendation_fraction', 'exclusion_fraction')


def scene_intervals(stats, scenes, repetitions=1000, seed=101):
    names = np.unique(scenes)
    counts = np.array([np.sum(scenes == s) for s in names])
    totals = np.array([stats[scenes == s].sum(axis=0) for s in names])
    rng = np.random.default_rng(seed)
    values = {key: [] for key in RISK_KEYS}
    for _ in range(repetitions):
        indices = rng.integers(len(names), size=len(names))
        # summarize needs the true group count; add zero rows only conceptually.
        sampled = totals[indices]
        total_groups = int(counts[indices].sum())
        aggregate = summarize(sampled)
        for key in RISK_KEYS:
            value = aggregate[key]
            if key in ('best_loss_group_rate', 'recommendation_fraction'):
                col = 10 if key == 'best_loss_group_rate' else 13
                value = float(sampled[:, col].sum() / total_groups)
            if value is not None:
                values[key].append(value)
    return {key: [float(np.quantile(v, .025)), float(np.quantile(v, .975))] if v else None
            for key, v in values.items()}


def evaluate(y, p, scenes, rule, bootstrap=True):
    decisions = decide(p, rule)
    stats = sufficient(y, p, decisions)
    result = summarize(stats)
    result['scenes'] = len(np.unique(scenes))
    result['active_scenes'] = len(np.unique(scenes[stats[:, 20] > 0]))
    result['recommended_scenes'] = len(np.unique(scenes[stats[:, 13] > 0]))
    if bootstrap:
        result['scene_95_intervals'] = scene_intervals(stats, scenes)
    return result, decisions, stats


def select_rules(y, p, scenes):
    """Select using validation only; no final test arguments exist here."""
    base = dict(block_threshold=1.01, dominance_ratio=0., recommend_gap=2.,
                recommend_block_max=.05, recommend_usable_min=.9,
                exclude_enabled=True, recommend_enabled=False)
    exclusions = []
    for threshold in (1.01, .995, .99, .975, .95, .9):
        for dominance in (0., .25, .5, .75, 1.):
            rule = base | dict(block_threshold=threshold, dominance_ratio=dominance)
            measured, _, _ = evaluate(y, p, scenes, rule, False)
            safe = ((measured['blocked_false_exclusion_rate'] or 0) <= .01
                    and (measured['near_best_loss_rate'] or 0) <= .01
                    and (measured['shortlist_quality_loss'] or 0) <= .001)
            exclusions.append(dict(rule=rule, metrics=measured, eligible=safe))
    ordered = sorted((r for r in exclusions if r['eligible']),
                     key=lambda r: r['metrics']['workload_reduction'], reverse=True)
    exclusion = base.copy()
    for item in ordered:
        result, _, _ = evaluate(y, p, scenes, item['rule'])
        bounds = result['scene_95_intervals']
        if (result['excluded'] == 0 or (result['active_scenes'] >= 8
            and all(bounds[k] is None or bounds[k][1] <= .02
                    for k in ('blocked_false_exclusion_rate', 'near_best_loss_rate')))):
            exclusion = item['rule']; break
    recommendations = []
    for gap in (0., .005, .01, .02, .05, .1, .2, .5):
        rule = exclusion | dict(recommend_gap=gap, recommend_enabled=True)
        result, _, _ = evaluate(y, p, scenes, rule, False)
        safe = (result['recommendation_groups'] >= 100
                and result['recommended_scenes'] >= 8
                and (result['recommendation_failure_rate'] or 0) <= .01)
        recommendations.append(dict(rule=rule, metrics=result, eligible=safe))
    chosen = exclusion.copy()
    for item in sorted((r for r in recommendations if r['eligible']),
                       key=lambda r:r['metrics']['recommendation_fraction'], reverse=True):
        result, _, _ = evaluate(y, p, scenes, item['rule'])
        interval = result['scene_95_intervals']['recommendation_failure_rate']
        if interval and interval[1] <= .02:
            chosen = item['rule']; break
    result, _, _ = evaluate(y, p, scenes, chosen)
    return dict(rule=chosen, validation=result, exclusion_candidates=exclusions,
                recommendation_candidates=recommendations,
                risk_target={'point_failure_max':.01, 'empirical_scene_upper_max':.02,
                             'near_best_quality_fraction':.95},
                limitation='Empirical validation targets, not guaranteed individual confidence. '
                           'Zero observed errors do not establish zero risk; no strategy costs/values included.')
