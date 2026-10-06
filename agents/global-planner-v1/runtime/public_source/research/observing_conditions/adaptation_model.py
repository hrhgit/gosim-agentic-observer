from __future__ import annotations
import numpy as np
from collections import deque
import copy,time
import torch
from torch import nn
from torch.nn import functional as F
from .adaptation_features import QUALITY_INDEX
from .information_model import numeric

class Normalizer:

    def __init__(self, values):
        self.values = values

    @classmethod
    def fit(cls, data):
        values = {}
        for key in ('x', 'history'):
            a = data[key].reshape(-1, data[key].shape[-1])
            if key == 'history':
                a = a[a[:, -1] > 0]
            values[key + '_mean'] = a.mean(0).astype(np.float32)
            values[key + '_std'] = np.maximum(a.std(0), 0.05).astype(np.float32)
        values['history_mean'][-1] = 0
        values['history_std'][-1] = 1
        values['quality_scale'] = np.array(max(float(data['y'][:, 0].mean()), 1e-06), np.float32)
        return cls(values)

    def apply(self, data):
        result = {k: ((data[k] - self.values[k + '_mean']) / self.values[k + '_std']).astype(np.float32) for k in ('x', 'history')}
        result['history'] *= data['history'][..., -1:]
        result['held'] = data['x'][:, QUALITY_INDEX].astype(np.float32)
        return result

    def save(self, path):
        np.savez(path, **self.values)

    @classmethod
    def load(cls, path):
        with np.load(path) as f:
            return cls({k: f[k].copy() for k in f.files})

class CorrectionNet(nn.Module):

    def __init__(self, x_dim=304, history_dim=22, scale=1.0):
        super().__init__()
        self.scale = float(scale)
        self.encoder = nn.GRU(history_dim, 32, batch_first=True)
        self.shared = nn.Sequential(nn.Linear(x_dim + 32, 64), nn.ReLU(), nn.Dropout(0.1), nn.Linear(64, 32), nn.ReLU())
        self.quality_head = nn.Linear(32, 4)
        self.usable_head = nn.Linear(32, 5)
        self.class_head = nn.Linear(32, 4)
        nn.init.zeros_(self.quality_head.weight)
        nn.init.zeros_(self.quality_head.bias)

    def forward(self, x, history, held):
        encoded = self.encoder(history)[0][:, -1]
        feature = self.shared(torch.cat([x, encoded], dim=-1))
        raw = self.quality_head(feature)
        base = held / self.scale
        mean = F.relu(base + raw[:, 0])
        median = F.relu(base + raw[:, 2])
        q = torch.stack([F.relu(median - F.softplus(raw[:, 1])), median, median + F.softplus(raw[:, 3])], dim=-1)
        u = self.usable_head(feature)
        increments = F.softplus(u[:, 1:]) + 1e-06
        uq = torch.cumsum(increments[:, :3], dim=-1) / increments.sum(-1, keepdim=True)
        return torch.cat([mean[:, None], q, torch.sigmoid(u[:, :1]), uq, self.class_head(feature)], dim=-1)

@torch.inference_mode()
def infer_gru(model, normalizer, data, batch=1024):
    n = normalizer.apply(data)
    model.eval()
    result = []
    for start in range(0, len(n['x']), batch):
        args = [torch.from_numpy(n[k][start:start + batch]) for k in ('x', 'history', 'held')]
        result.append(numeric(model(*args), model.scale))
    return np.concatenate(result)

class OnlineUpdate:
    """Reset each scene. Updates only from public feedback that has become available."""

    def __init__(self, model, normalizer, mode):
        self.mode = mode
        self.model = copy.deepcopy(model).eval()
        self.normalizer = normalizer
        self.buffer = deque(maxlen=64)
        self.new_count = 0
        self.bias = 0.0
        self.updates = 0
        self.cpu_seconds = 0.0
        for p in self.model.parameters():
            p.requires_grad_(False)
        if mode == 'head':
            for p in self.model.quality_head.parameters():
                p.requires_grad_(True)
            self.optimizer = torch.optim.Adam(self.model.quality_head.parameters(), lr=0.0001)

    def predict(self, data):
        p = infer_gru(self.model, self.normalizer, data)
        if self.mode == 'bias':
            p[:, :4] = np.maximum(0, p[:, :4] + self.bias)
        return p

    def observe(self, data):
        """One completed clean exposure, not one target. No hidden quality fields accepted."""
        if set(data) - {'x', 'history', 'lower', 'upper'}:
            raise ValueError('online update accepts only public proxy inputs')
        self.buffer.append(data)
        self.new_count += 1
        if self.mode == 'fixed' or self.new_count < 8:
            return
        started = time.process_time()
        self.new_count = 0
        selected = self.buffer
        arrays = {k: np.concatenate([b[k] for b in selected]) for k in ('x', 'history', 'lower', 'upper')}
        weights = np.concatenate([np.full(len(b['x']), 1 / len(b['x'])) for b in selected]).astype(np.float32)
        weights /= weights.sum()
        if self.mode == 'bias':
            p = self.predict(arrays)[:, 0]
            error = np.maximum(arrays['lower'] - p, 0) - np.maximum(p - arrays['upper'], 0)
            self.bias += 0.05 * float(np.sum(weights * error))
        elif self.mode == 'head':
            n = self.normalizer.apply(arrays)
            args = [torch.from_numpy(n[k]) for k in ('x', 'history', 'held')]
            p = self.model(*args)[:, :4] * self.model.scale
            lo = torch.as_tensor(arrays['lower'], dtype=torch.float32)[:, None]
            hi = torch.as_tensor(arrays['upper'], dtype=torch.float32)[:, None]
            loss = ((F.relu(lo - p).square() + F.relu(p - hi).square()) * torch.from_numpy(weights)[:, None]).sum() / 4
            self.optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(self.model.quality_head.parameters(), 1.0)
            self.optimizer.step()
        self.updates += 1
        self.cpu_seconds += time.process_time() - started
