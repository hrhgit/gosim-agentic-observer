from __future__ import annotations
import numpy as np
import math

VERSION = 'fault-report-model-v1'
BAND_NAMES = ['match_fraction', 'mismatch_fraction', 'ambiguous_fraction', 'zero_fraction', 'matched_transmission_min', 'matched_transmission_max', 'mismatched_transmission_median', 'weather_lower_bound', 'weather_upper_bound', 'band_bounds_conflict', 'estimated_weather', 'estimated_efficiency', 'previous_efficiency', 'efficiency_ratio', 'suspect', 'low_count', 'low_seconds', 'closed_suspect', 'band_probe_needed', 'free_reports_left', 'confirmed_false_reports', 'cooldown_remaining', 'remaining_science_estimate', 'correct_reward', 'paid_false_cost']

def band_evidence(rec, hits, sky, score, now, remaining_gain):
    """Infer only identifiable multiplier evidence; never read physical band truth."""
    match, mismatch, ambiguous, zero = ([], [], 0, 0)
    mu = float(score['program']['multipliers'][rec['program']])
    mis = float(score['program']['mismatch_multiplier'])
    for item in rec['items']:
        if item[0] not in hits or item[9] <= 0:
            continue
        observed = hits[item[0]] / item[9]
        tol = 1.2e-06 / item[9] + 1e-09
        if observed <= tol:
            zero += 1
        elif observed >= mu - tol or observed > mis + tol:
            match.append(item[4])
        elif abs(observed - mis) <= tol:
            mismatch.append(item[4])
        else:
            ambiguous += 1
    n = len(match) + len(mismatch) + ambiguous + zero
    lower = upper = math.nan
    if match and (not mismatch):
        if rec['program'] == 'DARK':
            lower = sky.b_dark / min(match)
        elif rec['program'] == 'BRIGHT':
            lower, upper = (sky.b_bright / min(match), sky.b_dark / max(match))
        else:
            upper = sky.b_bright / max(match)
    elif mismatch and (not match):
        a = float(np.median(mismatch))
        if rec['program'] == 'DARK':
            upper = sky.b_dark / a
        elif rec['program'] == 'BACKUP':
            lower = sky.b_bright / a
    conflict = bool(np.isfinite([lower, upper]).all() and lower >= upper)
    if conflict:
        lower = upper = math.nan
    return np.asarray([len(match) / n if n else math.nan, len(mismatch) / n if n else math.nan, ambiguous / n if n else math.nan, zero / n if n else math.nan, min(match) if match else math.nan, max(match) if match else math.nan, float(np.median(mismatch)) if mismatch else math.nan, lower, upper, float(conflict), sky.W, sky.e, sky.e_ref, sky.e / max(sky.e_ref, 1e-08), float(sky.suspect), sky.low_count, sky.low_seconds, float(sky.closed_suspect), float(sky.probe is not None), sky.free_left, sky.reports_false, max(0.0, sky.cooldown_until - now.timestamp()), remaining_gain, score['reporting']['correct_reward'], abs(score['reporting']['false_penalty'])], np.float32)
