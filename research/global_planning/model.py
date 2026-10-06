from __future__ import annotations

import torch
from torch import nn


class PlanningNet(nn.Module):
    """Permutation-equivariant candidate scores plus an unrestricted scalar value.

Both architectures receive the same full sequence. The MLP control pools all past
encoded public states without order; the GRU processes their actual order.
"""
    def __init__(self, global_dim, candidate_dim, architecture="gru", hidden=64):
        super().__init__()
        if architecture not in ("gru", "mlp"):
            raise ValueError("Unknown architecture")
        self.config = dict(global_dim=global_dim, candidate_dim=candidate_dim, architecture=architecture, hidden=hidden)
        self.architecture, self.hidden = architecture, hidden
        self.global_encoder = nn.Sequential(nn.Linear(global_dim, hidden), nn.Tanh())
        self.candidate_encoder = nn.Sequential(nn.Linear(candidate_dim, hidden), nn.Tanh())
        self.context_encoder = nn.Sequential(nn.Linear(hidden * 2, hidden), nn.Tanh())
        self.memory = nn.GRUCell(hidden, hidden) if architecture == "gru" else nn.Sequential(nn.Linear(hidden * 2, hidden), nn.Tanh())
        self.policy = nn.Sequential(nn.Linear(hidden * 2, hidden), nn.Tanh(), nn.Linear(hidden, 1))
        self.value = nn.Sequential(nn.Linear(hidden, hidden), nn.Tanh(), nn.Linear(hidden, 1))

    def initial(self, device="cpu"):
        return torch.zeros(self.hidden + (1 if self.architecture == "mlp" else 0), device=device)

    def forward(self, x, candidates, memory):
        encoded = self.candidate_encoder(candidates)
        context = self.context_encoder(torch.cat([self.global_encoder(x), encoded.mean(0)]))
        if self.architecture == "gru":
            state = self.memory(context.unsqueeze(0), memory.unsqueeze(0)).squeeze(0)
            next_memory = state
        else:
            count = memory[-1] + 1
            average = memory[:-1] + (context - memory[:-1]) / count
            state = self.memory(torch.cat([context, average]))
            next_memory = torch.cat([average, count.reshape(1)])
        logits = self.policy(torch.cat([encoded, state.expand(len(encoded), -1)], dim=-1)).squeeze(-1)
        return logits, self.value(state).squeeze(-1), next_memory
