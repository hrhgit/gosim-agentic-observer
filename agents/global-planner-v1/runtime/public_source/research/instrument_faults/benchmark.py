from __future__ import annotations
import numpy as np
from research.observing_conditions.features import CONTEXT_NAMES,CANDIDATE_NAMES
from . import feedback_only as feedback

CONTEXT_INDICES = [i for i, name in enumerate(CONTEXT_NAMES) if not name.startswith('current:')]
BASE_NAMES = feedback.FEATURE_NAMES + ['public_before:' + CONTEXT_NAMES[i] for i in CONTEXT_INDICES] + ['geometry_mean:' + name for name in CANDIDATE_NAMES] + ['geometry_quality_std', 'geometry_minimum_altitude']

def public_exposure_inputs(context, candidates):
    """Raw public forecasts and analytic geometry, with no trained predictor."""
    if len(candidates):
        geometry = np.r_[candidates.mean(0), candidates[:, -1].std(), candidates[:, -2].min()]
    else:
        geometry = np.full(len(CANDIDATE_NAMES) + 2, np.nan)
    return np.r_[np.asarray(context)[CONTEXT_INDICES], geometry].astype(np.float32)
