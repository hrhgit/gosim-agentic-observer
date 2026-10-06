"""Information-first numeric model; no ranking, filtering, or action decisions."""
from __future__ import annotations

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from .models import ConditionsNet
from .metrics import StatisticalBaseline, calibrate_fit, calibrate_apply, probability_error, reliability_bins

VERSION = 'conditions-information-v3'
NAMES = ('mean_quality', 'q05', 'q50', 'q95', 'usable_fraction',
         'u05', 'u50', 'u95', 'severe_block_probability', 'p_dark', 'p_bright', 'p_backup')
LEGACY = (0, 1, 2, 3, 4, 8, 9, 10, 11)
LEVELS = np.array([.05, .5, .95])


class InformationNet(ConditionsNet):
    def __init__(self, architecture, context_dim, history_dim, candidate_dim):
        super().__init__(architecture, context_dim, history_dim, candidate_dim)
        self.head = nn.Linear(64, 13)

    def forward(self, context, history, candidate):
        if self.architecture == 'mlp':
            encoded = history.flatten(1)
        elif self.architecture == 'gru':
            encoded = self.encoder(history)[0][:, -1]
        else:
            encoded = self.encoder(history.transpose(1, 2))[:, :, -1]
        shared = self.shared(torch.cat([context, encoded], dim=-1))[:, None, :]
        raw = self.head(self.fusion(shared + self.candidate(candidate)))
        quality = torch.cumsum(F.softplus(raw[..., 1:4]), dim=-1)
        increments = F.softplus(raw[..., 5:9]) + 1e-6
        usable = torch.cumsum(increments[..., :3], dim=-1) / increments.sum(dim=-1, keepdim=True)
        return torch.cat([F.softplus(raw[..., :1]), quality, torch.sigmoid(raw[..., 4:5]),
                          usable, raw[..., 9:]], dim=-1)


def training_loss(output, y, scale):
    levels = output.new_tensor(LEVELS)
    q = y[..., 0] / scale
    dq = q[..., None] - output[..., 1:4]
    du = y[..., 1, None] - output[..., 5:8]
    qloss = torch.maximum(levels * dq, (levels - 1) * dq).mean()
    uloss = torch.maximum(levels * du, (levels - 1) * du).mean()
    blocked = F.binary_cross_entropy_with_logits(output[..., 8], y[..., 2])
    bands = F.cross_entropy(output[..., 9:12].reshape(-1, 3), y[..., 3].long().reshape(-1))
    means = (output[..., 0] - q).square().mean() + (output[..., 4] - y[..., 1]).square().mean()
    return qloss + uloss + blocked + bands + .2 * means


def numeric(output, scale):
    p = output.detach().clone()
    p[..., :4] *= scale
    p[..., 8] = torch.sigmoid(p[..., 8])
    p[..., 9:12] = torch.softmax(p[..., 9:12], dim=-1)
    return p.cpu().numpy()


@torch.inference_mode()
def infer(model, normalized, scale, group_batch=64):
    model.eval()
    result = []
    for first in range(0, len(normalized['context']), group_batch):
        x = [torch.as_tensor(normalized[k][first:first + group_batch])
             for k in ('context', 'history', 'candidate')]
        result.append(numeric(model(*x), scale))
    return np.concatenate(result)


class InformationBaseline:
    """Same lookup inputs, with training-only U quantiles added to the old reference."""
    def __init__(self, constant=False):
        self.base = StatisticalBaseline(constant=constant)

    def fit(self, data):
        self.base.fit(data)
        keys = self.base.keys(data).ravel()
        u = data['y'][..., 1].ravel()
        self.global_u = np.quantile(u, LEVELS)
        self.u_table = {}
        for key in np.unique(keys):
            mask = keys == key
            weight = mask.sum() / (mask.sum() + 100)
            self.u_table[int(key)] = weight * np.quantile(u[mask], LEVELS) + (1 - weight) * self.global_u
        return self

    def predict(self, data):
        old = self.base.predict(data)
        keys = self.base.keys(data)
        u = np.array([self.u_table.get(int(k), self.global_u) for k in keys.ravel()]).reshape(*keys.shape, 3)
        return np.concatenate([old[..., :5], u, old[..., 5:]], axis=-1).astype(np.float32)

    def as_dict(self):
        return dict(base=self.base.as_dict(), global_u=self.global_u.tolist(),
                    u_table={str(k): v.tolist() for k, v in self.u_table.items()})

    @classmethod
    def from_dict(cls, value):
        result = cls(value['base']['constant'])
        result.base.global_values = np.array(value['base']['global'])
        result.base.table = {int(k): np.array(v) for k, v in value['base']['table'].items()}
        result.global_u = np.array(value['global_u'])
        result.u_table = {int(k): np.array(v) for k, v in value['u_table'].items()}
        return result


def calibration_fit(y, p):
    settings = calibrate_fit(y, p[..., LEGACY])
    residual = np.maximum(p[..., 5] - y[..., 1], y[..., 1] - p[..., 7]).ravel()
    settings['usable_interval_correction'] = max(0., float(np.quantile(residual, .9, method='higher')))
    settings['usable_point_mass_note'] = 'U contains atoms at 0/1; achievable deterministic coverage is discrete.'
    return settings


def calibration_apply(p, settings):
    result = p.astype(np.float64).copy()
    result[..., LEGACY] = calibrate_apply(p[..., LEGACY], settings)
    result[..., 5] = np.maximum(0., result[..., 5] - settings['usable_interval_correction'])
    result[..., 7] = np.minimum(1., result[..., 7] + settings['usable_interval_correction'])
    return result.astype(np.float32)


def measure(y, p):
    y, p = y.reshape(-1, 4), p.reshape(-1, 12)
    if not len(y):
        raise ValueError('empty evaluation')
    if not np.isfinite(p).all():
        raise ValueError('nonfinite predictions')
    onehot = np.eye(3)[y[:, 3].astype(int)]
    result = dict(samples=len(y))
    for prefix, truth, indices, mean_index in (('quality', y[:, 0], (1, 2, 3), 0),
                                              ('usable', y[:, 1], (5, 6, 7), 4)):
        lo, median, hi = p[:, indices].T
        if np.any(lo > median + 1e-6) or np.any(median > hi + 1e-6):
            raise ValueError('crossed quantiles')
        delta = truth[:, None] - p[:, indices]
        interval = hi - lo + 20 * np.maximum(lo - truth, 0) + 20 * np.maximum(truth - hi, 0)
        result.update({prefix + '_mae': float(np.abs(p[:, mean_index] - truth).mean()),
                       prefix + '_pinball': float(np.maximum(LEVELS * delta, (LEVELS - 1) * delta).mean()),
                       prefix + '_interval_score': float(interval.mean()),
                       prefix + '_coverage': float(((lo <= truth) & (truth <= hi)).mean()),
                       prefix + '_width': float((hi - lo).mean())})
    blocked = np.clip(p[:, 8], 1e-7, 1 - 1e-7)
    band = np.clip(p[:, 9:12], 1e-7, 1)
    result.update(blocked_brier=float(((blocked - y[:, 2]) ** 2).mean()),
                  blocked_log_loss=float(-(y[:, 2] * np.log(blocked) + (1 - y[:, 2]) * np.log1p(-blocked)).mean()),
                  band_brier=float(((band - onehot) ** 2).sum(axis=1).mean()),
                  band_log_loss=float(-np.log(band[np.arange(len(y)), y[:, 3].astype(int)]).mean()),
                  blocked_ece=probability_error(blocked, y[:, 2]),
                  band_ece=float(max(probability_error(band[:, i], onehot[:, i]) for i in range(3))),
                  blocked_frequency=float(y[:, 2].mean()),
                  band_counts=np.bincount(y[:, 3].astype(int), minlength=3).tolist())
    return result


MAIN = ('quality_pinball', 'usable_pinball', 'blocked_brier', 'band_brier')


def selection_score(metrics, reference):
    return float(np.mean([metrics[k] / max(reference[k], 1e-8) for k in MAIN]))


def reliability_gate(m):
    return (max(m['blocked_ece'], m['band_ece']) <= .05
            and .85 <= m['quality_coverage'] <= .95 and .85 <= m['usable_coverage'] <= .95)


def probability_tables(y, p, scene):
    y, p = y.reshape(-1, 4), p.reshape(-1, 12)
    scene = np.broadcast_to(np.asarray(scene)[:, None], (len(scene), len(y) // len(scene))).ravel()
    result = {}
    for name, probabilities, truth in [('blocked', p[:, 8], y[:, 2])] + [
        (name, p[:, 9 + i], (y[:, 3] == i).astype(float)) for i, name in enumerate(('DARK', 'BRIGHT', 'BACKUP'))]:
        bins = reliability_bins(probabilities, truth)
        for index, row in enumerate(bins):
            mask = (probabilities >= index / 10) & ((probabilities < (index + 1) / 10)
                                                   if index < 9 else (probabilities <= 1))
            row['independent_scenes'] = len(np.unique(scene[mask]))
        result[name] = bins
    return result
