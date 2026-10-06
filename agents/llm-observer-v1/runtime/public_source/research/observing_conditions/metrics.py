from __future__ import annotations
import numpy as np
from scipy.special import expit,softmax

def calibrate_apply(p, settings):
    result = p.astype(np.float64).copy()
    probability = np.clip(result[..., 5], 1e-07, 1 - 1e-07)
    result[..., 5] = expit(np.log(probability / (1 - probability)) / settings['blocked_temperature'])
    result[..., 6:9] = softmax(np.log(np.clip(result[..., 6:9], 1e-07, 1)) / settings['band_temperature'], axis=-1)
    result[..., 1] = np.maximum(0, result[..., 1] - settings['interval_correction'])
    result[..., 3] += settings['interval_correction']
    return result.astype(np.float32)
