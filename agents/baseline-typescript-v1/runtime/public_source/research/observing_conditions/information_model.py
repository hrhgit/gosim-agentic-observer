from __future__ import annotations
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from .models import ConditionsNet
from .metrics import calibrate_apply

VERSION = 'conditions-information-v3'
NAMES = ('mean_quality', 'q05', 'q50', 'q95', 'usable_fraction', 'u05', 'u50', 'u95', 'severe_block_probability', 'p_dark', 'p_bright', 'p_backup')
LEGACY = (0, 1, 2, 3, 4, 8, 9, 10, 11)

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
        increments = F.softplus(raw[..., 5:9]) + 1e-06
        usable = torch.cumsum(increments[..., :3], dim=-1) / increments.sum(dim=-1, keepdim=True)
        return torch.cat([F.softplus(raw[..., :1]), quality, torch.sigmoid(raw[..., 4:5]), usable, raw[..., 9:]], dim=-1)

def numeric(output, scale):
    p = output.detach().clone()
    p[..., :4] *= scale
    p[..., 8] = torch.sigmoid(p[..., 8])
    p[..., 9:12] = torch.softmax(p[..., 9:12], dim=-1)
    return p.cpu().numpy()

def calibration_apply(p, settings):
    result = p.astype(np.float64).copy()
    result[..., LEGACY] = calibrate_apply(p[..., LEGACY], settings)
    result[..., 5] = np.maximum(0.0, result[..., 5] - settings['usable_interval_correction'])
    result[..., 7] = np.minimum(1.0, result[..., 7] + settings['usable_interval_correction'])
    return result.astype(np.float32)
