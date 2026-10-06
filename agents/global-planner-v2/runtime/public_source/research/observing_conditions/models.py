from __future__ import annotations
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

class Normalizer:

    def __init__(self, values):
        self.values = values

    @classmethod
    def fit(cls, data):
        values = {}
        for key in ('context', 'history', 'candidate'):
            x = data[key].reshape(-1, data[key].shape[-1])
            if key == 'history':
                x = x[x[:, -1] > 0]
            if not len(x):
                values[key + '_mean'] = np.zeros(data[key].shape[-1], dtype=np.float32)
                values[key + '_std'] = np.ones(data[key].shape[-1], dtype=np.float32)
                continue
            values[key + '_mean'] = x.mean(axis=0).astype(np.float32)
            values[key + '_std'] = np.maximum(x.std(axis=0), 0.05).astype(np.float32)
        values['history_mean'][-1] = 0
        values['history_std'][-1] = 1
        values['quality_scale'] = np.array(max(float(data['y'][..., 0].mean()), 1e-06), dtype=np.float32)
        return cls(values)

    def apply(self, data):
        result = {}
        for key in ('context', 'history', 'candidate'):
            result[key] = ((data[key] - self.values[key + '_mean']) / self.values[key + '_std']).astype(np.float32)
            if key == 'history':
                result[key] *= data[key][..., -1:]
        return result

    def save(self, path):
        np.savez(path, **self.values)

    @classmethod
    def load(cls, path):
        with np.load(path) as values:
            return cls({k: values[k].copy() for k in values.files})

class CausalBlock(nn.Module):

    def __init__(self, input_channels, channels, dilation):
        super().__init__()
        self.padding = 2 * dilation
        self.first = nn.Conv1d(input_channels, channels, 3, dilation=dilation)
        self.second = nn.Conv1d(channels, channels, 3, dilation=dilation)
        self.skip = nn.Conv1d(input_channels, channels, 1) if input_channels != channels else nn.Identity()
        self.dropout = nn.Dropout(0.1)

    def forward(self, x):
        y = self.dropout(F.relu(self.first(F.pad(x, (self.padding, 0)))))
        y = self.dropout(F.relu(self.second(F.pad(y, (self.padding, 0)))))
        return F.relu(y + self.skip(x))

class ConditionsNet(nn.Module):

    def __init__(self, architecture, context_dim, history_dim, candidate_dim):
        super().__init__()
        self.architecture = architecture
        if architecture == 'mlp':
            self.shared = nn.Linear(context_dim + 32 * history_dim, 128)
            self.candidate = nn.Linear(candidate_dim, 128, bias=False)
            self.fusion = nn.Sequential(nn.ReLU(), nn.Dropout(0.1), nn.Linear(128, 128), nn.ReLU(), nn.Dropout(0.1), nn.Linear(128, 64), nn.ReLU())
        else:
            if architecture == 'gru':
                self.encoder = nn.GRU(history_dim, 64, num_layers=2, batch_first=True, dropout=0.1)
            elif architecture == 'tcn':
                self.encoder = nn.Sequential(*[CausalBlock(history_dim if i == 0 else 64, 64, d) for i, d in enumerate((1, 2, 4, 8))])
            else:
                raise ValueError(f'unknown architecture {architecture}')
            self.shared = nn.Linear(context_dim + 64, 128)
            self.candidate = nn.Linear(candidate_dim, 128, bias=False)
            self.fusion = nn.Sequential(nn.ReLU(), nn.Dropout(0.1), nn.Linear(128, 64), nn.ReLU())
        self.head = nn.Linear(64, 9)

    def forward(self, context, history, candidate):
        if self.architecture == 'mlp':
            encoded = history.flatten(1)
        elif self.architecture == 'gru':
            encoded = self.encoder(history)[0][:, -1]
        else:
            encoded = self.encoder(history.transpose(1, 2))[:, :, -1]
        shared = self.shared(torch.cat([context, encoded], dim=-1))[:, None, :]
        raw = self.head(self.fusion(shared + self.candidate(candidate)))
        quantiles = torch.cumsum(F.softplus(raw[..., 1:4]), dim=-1)
        return torch.cat([F.softplus(raw[..., :1]), quantiles, torch.sigmoid(raw[..., 4:5]), raw[..., 5:]], dim=-1)
