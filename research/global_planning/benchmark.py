from __future__ import annotations

import copy
import time
from concurrent.futures import ProcessPoolExecutor

import torch

from .common import save
from .model import PlanningNet
from .rollout import episode


def model_benchmark(output):
    torch.set_num_threads(1)
    torch.manual_seed(101)
    source = PlanningNet(51, 75)
    x, c = torch.randn(51), torch.randn(16, 75)
    devices = ["cpu"] + (["mps"] if torch.backends.mps.is_available() else [])
    results = {}
    reference = None
    for device in devices:
        model = copy.deepcopy(source).to(device)
        local_x, local_c = x.to(device), c.to(device)
        h = model.initial(device)
        optimizer = torch.optim.Adam(model.parameters(), lr=3e-4)
        with torch.no_grad():
            logits, value, _ = model(local_x, local_c, h)
        values = torch.cat([logits, value.reshape(1)]).cpu()
        if reference is None: reference = values
        difference = float((values-reference).abs().max())
        for _ in range(5):
            logits, value, _ = model(local_x, local_c, h)
        if device == "mps": torch.mps.synchronize()
        start = time.perf_counter()
        for _ in range(30):
            optimizer.zero_grad()
            logits, value, _ = model(local_x, local_c, h)
            loss = torch.nn.functional.cross_entropy(logits.unsqueeze(0), torch.tensor([0], device=device)) + value.square()
            loss.backward(); optimizer.step()
        if device == "mps": torch.mps.synchronize()
        results[device] = dict(seconds=time.perf_counter()-start, max_initial_difference=difference)
    selected = "cpu"
    if "mps" in results and results["mps"]["max_initial_difference"] <= 1e-4 and results["mps"]["seconds"] < results["cpu"]["seconds"] / 1.2:
        selected = "mps"
    save(output / "model-device.json", dict(results=results, selected=selected, batches=30, candidates=16,
        criterion="MPS must be >=20% faster and initial outputs agree within 1e-4; no automatic fallback"))
    return selected


def _measure(args):
    bundle, output, node = args
    value=episode(bundle, output, node=node, models=True, reuse_key="worker-benchmark-v1")
    return dict(total=value["total"], actions=value["actions"], seconds=value["wall_seconds"])


def worker_benchmark(scenarios, entries, output, node=None):
    args=[(scenarios/e["id"],output/"one"/e["id"],node) for e in entries[:2]]
    start=time.perf_counter();single=[_measure(a) for a in args];one=time.perf_counter()-start
    args=[(scenarios/e["id"],output/"two"/e["id"],node) for e in entries[:2]]
    start=time.perf_counter()
    with ProcessPoolExecutor(max_workers=2) as pool: parallel=list(pool.map(_measure,args))
    two=time.perf_counter()-start
    agree=all(a['actions']==b['actions'] and a['total']==b['total'] for a,b in zip(single,parallel))
    # Rollout collection initially remains serial. This measurement is advisory, not an inference limit.
    save(output/"workers.json",dict(one_worker_seconds=one,two_worker_seconds=two,identical=agree,
         recommended_workers=2 if agree and two<one/1.2 else 1, active_workers=1,
         note="Two-process overhead includes spawn; full-card scaling must be measured separately."))
