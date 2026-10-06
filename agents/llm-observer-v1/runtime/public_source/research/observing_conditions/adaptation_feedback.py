"""Public exposure feedback only; censored measurements remain intervals.

No scorer, simulator or weather-truth imports belong in this module.
"""
from __future__ import annotations

import math
import numpy as np

from .features import utc, candidate_features, geometry, age_value

HISTORY_LENGTH = 8
HISTORY_NAMES = (
    'throughput_mid', 'throughput_lower', 'throughput_upper', 'interval_width',
    'matched_fraction', 'mismatch_fraction', 'unknown_fraction', 'saturated_fraction',
    'zero_fraction', 'measured_fraction', 'hit_fraction', 'log_hit_count',
    'altitude', 'azimuth_sin', 'azimuth_cos', 'actual_duration',
    'age_since_end', 'gap_since_previous', 'estimated_efficiency', 'suspected_fault',
    'quake_notice', 'present',
)
STATE_NAMES = ('weather', 'throughput', 'efficiency', 'has_measurement', 'measurement_age',
               'suspected_fault', 'suspected_closure', 'quake_notice', 'history_count')


def decode_target(score_value, target, duration, program, scoring):
    """Return a raw completion interval and an evidence-based band verdict.

    score = weight * min(flux*T*Q/f0T0,1) * multiplier. Where both multipliers
    explain a score, neither declaration match nor Q is claimed known.
    """
    weight = float(target['science_weight'])
    flux = float(target['feature_flux'])
    if weight <= 0 or flux <= 0 or duration <= 0:
        return dict(valid=False, match='unknown', saturated=False)
    obs = float(score_value) / weight
    mu = float(scoring['program']['multipliers'][program])
    mis = float(scoring['program']['mismatch_multiplier'])
    tol = 1.2e-6 / weight + 1e-9
    if obs < -tol or obs > max(mu, mis) + tol:
        raise ValueError('score outside public formula support')
    match = 'unknown'
    possible = []
    for name, multiplier in (('matched', mu), ('mismatch', mis)):
        factor = obs / multiplier
        if factor <= 1 + tol:
            possible.append((name, min(max(factor, 0.), 1.)))
    if len(possible) == 1:
        match = possible[0][0]
    # A saturated mismatch is not identifiable: an unsaturated match can give
    # the same score. Preserve that ambiguity instead of copying a heuristic.
    saturated = any(abs(f - 1) <= tol for _, f in possible)
    lower = min(f for _, f in possible)
    upper = max(f for _, f in possible)
    scale = scoring['flux_zero_point'] * scoring['exposure_zero_point_seconds'] / (flux * duration)
    return dict(valid=True, match=match, saturated=saturated,
                factor_lower=lower, factor_upper=upper,
                quality_lower=lower * scale,
                quality_upper=None if saturated else upper * scale,
                zero=obs <= tol)


def summarize_exposure(record, targets, site, scoring):
    start, end = utc(record['start_utc']), utc(record['end_utc'])
    if end <= start:
        raise ValueError('empty exposure')
    duration = (end - start).total_seconds()
    values, lower_bounds, altitudes, azimuths = [], [], [], []
    counts = dict(matched=0, mismatch=0, unknown=0, saturated=0, zero=0, measured=0)
    for hit in record['hits']:
        target = targets[hit['target_id']]
        decoded = decode_target(hit['score'], target, duration, record['program'], scoring)
        if not decoded['valid']:
            continue
        counts[decoded['match']] += 1
        counts['saturated'] += decoded['saturated']
        counts['zero'] += decoded['zero']
        ra, dec = np.array([target['ra_deg']]), np.array([target['dec_deg']])
        geom = [geometry(ra, dec, start + (end-start)*f, site, scoring)
                for f in (1/6, .5, 5/6)]
        # geometry[-1] contains 1/q0; baseline W is already divided by q0.
        a = float(np.mean([g[-1][0] for g in geom])) * scoring['q0']
        altitudes.append(float(geom[1][1][0])); azimuths.append(float(geom[1][2][0]))
        if a > 1e-6:
            lower_bounds.append(decoded['quality_lower']/a)
            if decoded['quality_upper'] is not None:
                values.append((decoded['quality_lower']/a, decoded['quality_upper']/a))
                counts['measured'] += 1
    n = max(1, sum(counts[k] for k in ('matched', 'mismatch', 'unknown')))
    low = float(np.median([v[0] for v in values])) if values else float(np.median(lower_bounds)) if lower_bounds else 0.
    high = float(np.median([v[1] for v in values])) if values else low
    angles = np.radians(azimuths)
    angle = math.atan2(float(np.sin(angles).mean()), float(np.cos(angles).mean())) if azimuths else math.radians(record['pointing']['az_deg'])
    return dict(start_utc=start.isoformat(), end_utc=end.isoformat(), duration=duration,
                throughput_mid=(low+high)/2, throughput_lower=low, throughput_upper=high,
                interval_width=high-low, counts=counts, n=n,
                hit_fraction=len(record['hits'])/max(1,record['assigned_count']),
                altitude=(float(np.median(altitudes)) if altitudes else record['pointing']['alt_deg'])/90,
                azimuth_sin=math.sin(angle), azimuth_cos=math.cos(angle),
                estimated_efficiency=float(record.get('estimated_efficiency', .95)),
                suspected_fault=bool(record.get('suspected_fault',False)),
                quake_notice=bool(record.get('quake_notice',False)))


def history_array(records, now):
    now = utc(now)
    past = [r for r in records if utc(r['end_utc']) <= now][-HISTORY_LENGTH:]
    result = np.zeros((HISTORY_LENGTH,len(HISTORY_NAMES)),np.float32)
    for i,r in enumerate(past):
        gap = (utc(r['start_utc'])-utc(past[i-1]['end_utc'])).total_seconds() if i else 0.
        c,n=r['counts'],r['n']
        result[-len(past)+i]=[
            r['throughput_mid'],r['throughput_lower'],r['throughput_upper'],r['interval_width'],
            c['matched']/n,c['mismatch']/n,c['unknown']/n,c['saturated']/n,c['zero']/n,c['measured']/n,
            r['hit_fraction'],math.log1p(n)/math.log(101),r['altitude'],r['azimuth_sin'],r['azimuth_cos'],
            r['duration']/3600,age_value((now-utc(r['end_utc'])).total_seconds()),age_value(max(0,gap)),
            r['estimated_efficiency'],r['suspected_fault'],r['quake_notice'],1]
    return result


def state_array(state, now, history_count):
    now = utc(now)
    measured = state.get('last_measurement_utc')
    return np.array([state['weather'],state['throughput'],state['efficiency'],state['has_measurement'],
                     age_value((now-utc(measured)).total_seconds()) if measured else -1,
                     state['suspected_fault'],state['suspected_closure'],state['quake_notice'],
                     min(history_count,HISTORY_LENGTH)/HISTORY_LENGTH],np.float32)


def public_online_bounds(record, targets, scoring):
    """Public proxy labels, NOT environment truth. Saturated/ambiguous/fault rows excluded."""
    if record.get('suspected_fault') or record.get('quake_notice'):
        return []
    efficiency = float(record.get('estimated_efficiency',.95))
    if efficiency < .8:
        return []
    duration = (utc(record['end_utc'])-utc(record['start_utc'])).total_seconds()
    result=[]
    for hit in record['hits']:
        target=targets[hit['target_id']]
        d=decode_target(hit['score'],target,duration,record['program'],scoring)
        if not d['valid'] or d['saturated'] or d['zero'] or d['match']=='unknown':
            continue
        result.append(dict(target_id=hit['target_id'],quality_lower=d['quality_lower']/efficiency,
                           quality_upper=d['quality_upper']/efficiency,
                           label_source='public score inversion divided by ESTIMATED efficiency'))
    return result
