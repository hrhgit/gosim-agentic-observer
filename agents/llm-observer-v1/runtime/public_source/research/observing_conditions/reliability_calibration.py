from __future__ import annotations
import numpy as np
from scipy.special import softmax
from . import conditional_calibration as old

PHASES = ('never', 'stale', 'partial', 'eight')

def public_keys(metadata):
    leads, _ = old.public_keys(metadata)
    for m in metadata:
        total = m['all_history_count']
        fresh = m['fresh_history_count']
        if isinstance(total, bool) or not isinstance(total, (int, np.integer)) or (not 0 <= fresh <= total <= 8):
            raise ValueError('history freshness must count completed public observations')
    return (leads, np.array(['never' if m['all_history_count'] == 0 else 'stale' if m['fresh_history_count'] == 0 else 'partial' if m['fresh_history_count'] < 8 else 'eight' for m in metadata]))

def apply(raw, metadata, settings, return_evidence=False):
    leads, phases = public_keys(metadata)
    if np.asarray(raw).shape != (len(metadata), 12) or not np.isfinite(raw).all():
        raise ValueError('invalid predictions')
    result = np.asarray(raw, dtype=np.float64).copy()
    evidence = [None] * len(raw)
    for lead in np.unique(leads):
        for phase in np.unique(phases[leads == lead]):
            ix = np.flatnonzero((leads == lead) & (phases == phase))
            key, bank, pooled = old.select_bank(settings, str(lead), str(phase))
            tmp = dict(settings, scheme='global_sides', banks={'global': bank})
            result[ix] = old.apply(raw[ix], [metadata[int(i)] for i in ix], tmp)
            result[ix, 9:12] = softmax(np.log(np.clip(raw[ix, 9:12], 1e-07, 1)) / bank['band_temperature'] + np.array(bank.get('band_bias', [0.0, 0.0, 0.0])), axis=1)
            if return_evidence:
                value = dict(group=key, pooled=pooled, **bank['evidence'], probability_fit_supported=bank['probability_fit_supported'], band_adjustment=bank.get('band_fit', 'temperature only'), band_parent_prior=bank.get('band_parent_prior', False), probability_parent_used=bank['probability_parent_used'])
                for i in ix:
                    evidence[int(i)] = value
    return (result.astype(np.float32), evidence) if return_evidence else result.astype(np.float32)
