from __future__ import annotations
import numpy as np
from scipy.special import expit,softmax

HORIZONS = ('15m', '30m', '60m', 'next_night')
PHASES = ('none', 'partial', 'eight')

def public_keys(metadata):
    leads = []
    phases = []
    for row in metadata:
        lead = row['horizon']
        count = row['history_count']
        if lead not in HORIZONS:
            raise ValueError('unsupported public horizon')
        if isinstance(count, bool) or not isinstance(count, (int, np.integer)) or count < 0:
            raise ValueError('history count must count completed observations')
        leads.append(lead)
        phases.append('none' if count == 0 else 'partial' if count < 8 else 'eight')
    return (np.array(leads), np.array(phases))

def select_bank(settings, lead, phase):
    scheme = settings['scheme']
    banks = settings['banks']
    requested = lead + '/' + phase if scheme == 'history_horizon_sides' else lead if scheme == 'horizon_sides' else 'global'
    if requested in banks:
        return (requested, banks[requested], False)
    parent = lead if lead in banks else 'global'
    return (parent, banks[parent], True)

def apply(raw, metadata, settings, return_evidence=False):
    raw = np.asarray(raw)
    if raw.shape != (len(metadata), 12) or not np.isfinite(raw).all():
        raise ValueError('invalid frozen predictions')
    leads, phases = public_keys(metadata)
    result = raw.astype(np.float64).copy()
    evidence = [None] * len(raw)
    for lead in np.unique(leads):
        for phase in np.unique(phases[leads == lead]):
            ix = np.flatnonzero((leads == lead) & (phases == phase))
            key, bank, pooled = select_bank(settings, str(lead), str(phase))
            for name, lo, median, hi in [('quality', 1, 2, 3), ('usable', 5, 6, 7)]:
                lower = np.maximum(0, result[ix, lo] + bank['offsets'][name][0])
                upper = np.maximum(0, result[ix, hi] + bank['offsets'][name][1])
                if name == 'usable':
                    lower = np.minimum(lower, 1.0)
                    upper = np.minimum(upper, 1.0)
                result[ix, lo] = np.minimum(lower, result[ix, median])
                result[ix, hi] = np.maximum(upper, result[ix, median])
            probability = np.clip(raw[ix, 8], 1e-07, 1 - 1e-07)
            result[ix, 8] = expit(np.log(probability / (1 - probability)) / bank['blocked_temperature'])
            result[ix, 9:12] = softmax(np.log(np.clip(raw[ix, 9:12], 1e-07, 1)) / bank['band_temperature'], axis=1)
            if return_evidence:
                value = dict(group=key, pooled=pooled, **bank['evidence'], probability_fit_supported=bank['probability_fit_supported'], probability_parent_used=bank['probability_parent_used'])
                for j in ix:
                    evidence[int(j)] = value
    return (result.astype(np.float32), evidence) if return_evidence else result.astype(np.float32)
