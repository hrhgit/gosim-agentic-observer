from __future__ import annotations

import copy
import json
import random
import time
from pathlib import Path

import numpy as np
import torch
from torch.distributions import Categorical
from torch.nn import functional as F

from .common import save, digest
from .features import Normalizer
from .model import PlanningNet
from .rollout import episode

RETURN_SCALE = 100.0  # Same fixed unit for every card; never normalize per-card rewards.
CONFIG = dict(learning_rate=3e-4, clip=.2, updates_per_batch=4, gamma=1., value_weight=.5,
              entropy_weight=.01, gradient_clip=.5, history_gradient_steps=64,
              imitation_epochs=20, initial_ppo_rounds=10, seed=101, return_scale=RETURN_SCALE)


class Policy:
    def __init__(self, model, normalizer, *, sample=False, seed=101):
        self.model, self.normalizer, self.sample = model, normalizer, sample
        self.device = next(model.parameters()).device
        self.memory = model.initial(self.device)
        self.generator = torch.Generator().manual_seed(seed)

    def __call__(self, frame):
        start = time.perf_counter()
        x, candidates = self.normalizer.apply(frame)
        prepared = time.perf_counter()
        with torch.no_grad():
            logits, value, self.memory = self.model(torch.as_tensor(x, device=self.device),
                torch.as_tensor(candidates, device=self.device), self.memory)
            probs = logits.softmax(-1)
            selected = int(torch.multinomial(probs.cpu(), 1, generator=self.generator)) if self.sample else int(logits.argmax())
            logp = float(logits.log_softmax(-1)[selected])
        return selected, dict(log_probability=logp, value=float(value) * RETURN_SCALE,
            feature_seconds=prepared-start, inference_seconds=time.perf_counter()-prepared)


def save_checkpoint(path, model, optimizer, normalizer, **state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    torch.save(dict(model_config=model.config, model=model.state_dict(), optimizer=optimizer.state_dict(),
                    normalizer=normalizer.state, torch_rng=torch.get_rng_state(), python_rng=random.getstate(),
                    training_config=CONFIG, **state), tmp)
    tmp.replace(path)


def load_checkpoint(path, device="cpu"):
    state = torch.load(path, map_location="cpu", weights_only=True)
    if state["training_config"] != CONFIG:
        raise ValueError("Training configuration changed; start another version")
    model = PlanningNet(**state["model_config"]).to(device)
    model.load_state_dict(state["model"])
    optimizer = torch.optim.Adam(model.parameters(), lr=CONFIG["learning_rate"])
    optimizer.load_state_dict(state["optimizer"])
    torch.set_rng_state(state["torch_rng"])
    random.setstate(state["python_rng"])
    return model, optimizer, Normalizer(state["normalizer"]), state


def train_episode(model, optimizer, normalizer, trajectory, *, imitation, advantage_stats=None):
    device = next(model.parameters()).device
    memory = model.initial(device)
    choices = trajectory["choices"]
    if not choices:
        return dict(policy=0., value=0., count=0)
    totals = dict(policy=0., value=0., count=0)
    optimizer.zero_grad()
    losses = []
    for i, row in enumerate(choices):
        x, c = normalizer.apply(row["frame"])
        logits, value, memory = model(torch.as_tensor(x, device=device), torch.as_tensor(c, device=device), memory)
        target = torch.tensor(row["return"] / RETURN_SCALE, device=device)
        value_loss = F.mse_loss(value, target)
        if imitation:
            policy_loss = F.cross_entropy(logits.unsqueeze(0), torch.tensor([row["frame"]["teacher"]], device=device))
        else:
            old_logp = row["log_probability"]
            advantage = (row["return"] - row["value"]) / RETURN_SCALE
            mean, std = advantage_stats
            advantage = (advantage - mean) / max(std, 1e-6)
            distribution = Categorical(logits=logits)
            ratio = (distribution.log_prob(torch.tensor(row["selected"], device=device)) - old_logp).exp()
            policy_loss = -torch.minimum(ratio * advantage, ratio.clamp(1-CONFIG["clip"], 1+CONFIG["clip"]) * advantage)
            policy_loss -= CONFIG["entropy_weight"] * distribution.entropy()
        losses.append(policy_loss + CONFIG["value_weight"] * value_loss)
        totals["policy"] += float(policy_loss.detach())
        totals["value"] += float(value_loss.detach())
        totals["count"] += 1
        # Truncate gradients, NEVER returns or environment history. The hidden state carries on.
        if len(losses) == CONFIG["history_gradient_steps"] or i == len(choices)-1:
            (torch.stack(losses).sum() / len(choices)).backward()
            memory = memory.detach()
            losses = []
    torch.nn.utils.clip_grad_norm_(model.parameters(), CONFIG["gradient_clip"])
    optimizer.step()
    return totals


def evaluate(model, normalizer, entries, scenarios, output, *, node=None, models=True, key="evaluation"):
    rows = []
    for entry in entries:
        policy = Policy(model, normalizer)
        trace = episode(scenarios / entry["id"], output / entry["id"], policy, node=node, models=models,
                        reuse_key=digest(dict(key=key, entry=entry)))
        rows.append(dict(id=entry["id"], kind=entry.get("kind"), card=entry["card"], total=trace["total"],
                         wall_seconds=trace["wall_seconds"], completion=trace["termination"]["reason"]))
    save(output / "summary.json", dict(rows=rows, mean=float(np.mean([r["total"] for r in rows]))))
    return rows


def fit(architecture, training, validation, scenarios, output, *, node=None, models=True, seed=101, rounds=10, device="cpu"):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(1)
    torch.manual_seed(seed)
    random.seed(seed)
    checkpoint = output / "resume.pt"
    if checkpoint.exists():
        model, optimizer, normalizer, state = load_checkpoint(checkpoint, device)
        if state["seed"] != seed:
            raise ValueError("Resume seed mismatch")
    else:
        frames = [c["frame"] for t in training for c in t["choices"]]
        normalizer = Normalizer.fit(frames)
        model = PlanningNet(len(normalizer.state["global"]["mean"]), len(normalizer.state["candidate"]["mean"]), architecture).to(device)
        optimizer = torch.optim.Adam(model.parameters(), lr=CONFIG["learning_rate"])
        state = dict(phase="imitation", epoch=0, round=0, seed=seed, best_mean=-float("inf"))
    history_path = output / "history.json"
    history = json.loads(history_path.read_text()) if history_path.exists() else []
    if state["phase"] == "imitation":
        for epoch in range(state["epoch"], CONFIG["imitation_epochs"]):
            order = list(range(len(training)))
            random.shuffle(order)
            losses = [train_episode(model, optimizer, normalizer, training[i], imitation=True) for i in order]
            history.append(dict(phase="imitation", epoch=epoch+1, losses=losses))
            state["epoch"] = epoch + 1
            save_checkpoint(checkpoint, model, optimizer, normalizer, **state)
            save(history_path, history)
        state["phase"] = "ppo"
        save_checkpoint(output / "imitation.pt", model, optimizer, normalizer, **state)
        imitation_rows = evaluate(model, normalizer, validation, scenarios, output / "imitation-validation", node=node,
                                   models=models, key=digest(output / "imitation.pt"))
        state["best_mean"] = float(np.mean([r["total"] for r in imitation_rows]))
        state["best_phase"] = "imitation"
        save_checkpoint(output / "best.pt", model, optimizer, normalizer, **state)
        save_checkpoint(checkpoint, model, optimizer, normalizer, **state)
    entries = [t["entry"] for t in training]
    for iteration in range(state["round"], rounds):
        # Full A-D uses one per card; micro uses four distinct scenarios in a deterministic rotation.
        if set(e["card"] for e in entries) == set("abcd"):
            chosen = [[e for e in entries if e["card"] == card][iteration % len([e for e in entries if e["card"] == card])] for card in "abcd"]
        else:
            chosen = [entries[(iteration*4+i) % len(entries)] for i in range(4)]
        batch = []
        identity = digest({k: v.detach().cpu().tolist() for k, v in model.state_dict().items()})
        for i, entry in enumerate(chosen):
            policy = Policy(model, normalizer, sample=True, seed=seed + iteration*4+i)
            batch.append(episode(scenarios / entry["id"], output / "rollouts" / f"round-{iteration+1:03}" / entry["id"],
                policy, node=node, models=models, reuse_key=digest(dict(weights=identity, entry=entry, iteration=iteration))))
        advantages = [(r["return"]-r["value"])/RETURN_SCALE for t in batch for r in t["choices"]]
        if not advantages:
            raise ValueError("No learnable decisions in batch")
        advantage_stats = float(np.mean(advantages)), float(np.std(advantages))
        losses = []
        for _ in range(CONFIG["updates_per_batch"]):
            order = list(range(len(batch)))
            random.shuffle(order)
            losses += [train_episode(model, optimizer, normalizer, batch[i], imitation=False, advantage_stats=advantage_stats) for i in order]
        history.append(dict(phase="ppo", round=iteration+1, scores=[t["total"] for t in batch], losses=losses))
        state["round"] = iteration+1
        if (iteration+1) % 5 == 0:
            weights_file = output / f"round-{iteration+1:03}.pt"
            save_checkpoint(weights_file, model, optimizer, normalizer, **state)
            rows = evaluate(model, normalizer, validation, scenarios, output / f"validation-{iteration+1:03}",
                            node=node, models=models, key=digest(weights_file))
            mean = float(np.mean([r["total"] for r in rows]))
            history[-1]["validation_mean"] = mean
            if mean > state["best_mean"]:
                state["best_mean"], state["best_phase"] = mean, "ppo"
                save_checkpoint(output / "best.pt", model, optimizer, normalizer, **state)
        save_checkpoint(checkpoint, model, optimizer, normalizer, **state)
        save(history_path, history)
        print(json.dumps(dict(architecture=architecture, round=iteration+1, scores=history[-1]["scores"])), flush=True)
    save(output / "selection.json", dict(architecture=architecture, seed=seed, rounds=rounds,
         best_validation_mean=state["best_mean"], best_phase=state["best_phase"], selected=digest(output / "best.pt")))
    return output / "best.pt"
