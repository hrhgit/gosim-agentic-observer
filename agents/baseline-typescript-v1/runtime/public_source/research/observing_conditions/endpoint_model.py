from __future__ import annotations
import numpy as np
import torch
from torch import nn
from scipy.special import betainc,betaincinv,softmax

def public_extra(metadata):
    leads = ('15m', '30m', '60m', 'next_night')
    return np.array([[*(float(m['horizon'] == h) for h in leads), m['fresh_history_count'] / 8, m['all_history_count'] / 8, float(m['baseline_measurement_available'])] for m in metadata], np.float32)

@torch.inference_mode()
def features(data, raw, model=None, normalizer=None):
    if model is None:
        normalizer = Normalizer.load(SCALING / 'models/gru64/normalizer.npz')
        model = CorrectionNet(scale=float(normalizer.values['quality_scale']))
        model.load_state_dict(torch.load(SCALING / 'models/gru64/gru.pt', weights_only=True, map_location='cpu'))
    model.eval()
    n = normalizer.apply(data)
    encoded = []
    for start in range(0, len(raw), 1024):
        x = torch.from_numpy(n['x'][start:start + 1024])
        h = torch.from_numpy(n['history'][start:start + 1024])
        sequence = model.encoder(h)[0][:, -1]
        encoded.append(model.shared(torch.cat([x, sequence], dim=-1)).numpy())
    return np.concatenate([np.concatenate(encoded), data['hold'], raw, public_extra(data['meta'])], axis=1).astype(np.float32)

class EndpointHead(nn.Module):

    def __init__(self, dimension):
        super().__init__()
        self.net = nn.Sequential(nn.Linear(dimension, 16), nn.ReLU(), nn.Linear(16, 5))

    def forward(self, x):
        v = self.net(x)
        shapes = 0.2 + 19.8 * torch.sigmoid(v[:, 3:])
        return (v[:, :3], shapes)

def distribution_numeric(logits, shapes):
    return np.concatenate([softmax(logits, axis=1), shapes], axis=1).astype(np.float32)

def infer(head, x):
    head.eval()
    result = []
    with torch.inference_mode():
        for start in range(0, len(x), 1024):
            logits, shapes = head(torch.from_numpy(x[start:start + 1024]))
            result.append(distribution_numeric(logits.numpy(), shapes.numpy()))
    return np.concatenate(result)

def numerical(distribution, raw):
    p = raw.astype(np.float64).copy()
    masses = distribution[:, :3].astype(float)
    a, b = distribution[:, 3:5].T.astype(float)
    p[:, 4] = masses[:, 2] + masses[:, 1] * a / (a + b)
    for column, level in ((5, 0.05), (6, 0.5), (7, 0.95)):
        inner = np.clip((level - masses[:, 0]) / np.maximum(masses[:, 1], 1e-12), 0, 1)
        value = betaincinv(a, b, inner)
        p[:, column] = np.where(level <= masses[:, 0], 0, np.where(level >= 1 - masses[:, 2], 1, value))
    p[:, 8] = masses[:, 0] + masses[:, 1] * betainc(a, b, 0.5)
    return p.astype(np.float32)
