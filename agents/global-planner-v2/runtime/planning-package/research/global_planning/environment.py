from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

from .common import ROOT


class OfficialEnvironment:
    """A reset/step adapter over the original callback-driven runner, not a simulator."""

    def __init__(self, bundle, output):
        self.bundle, self.output = Path(bundle).resolve(), Path(output).resolve()
        self.process = None
        self.log = None

    def _read(self):
        line = self.process.stdout.readline()
        if not line:
            code = self.process.wait()
            raise RuntimeError(f"Engine exited {code}; see {self.output / 'engine.log'}")
        return json.loads(line)

    def reset(self):
        if self.process is not None:
            raise RuntimeError("Use a fresh environment for each episode")
        self.output.mkdir(parents=True, exist_ok=True)
        self.log = (self.output / "engine.log").open("w")
        self.process = subprocess.Popen([sys.executable, "-m", "research.global_planning.engine_worker",
            "--bundle", str(self.bundle), "--out", str(self.output)], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1)
        initial = self._read()
        if initial["type"] != "initialize":
            raise RuntimeError("Missing initialize")
        return initial["payload"], self._read()

    def step(self, action):
        self.process.stdin.write(json.dumps(action, allow_nan=False) + "\n")
        self.process.stdin.flush()
        return self._read()

    def close(self):
        if self.process is not None:
            self.process.stdin.close()
            self.process.wait()
            self.process.stdout.close()
            self.log.close()
            self.process = None


def rewards_from_episode(snapshots, report):
    """Public best-score changes, followed by exact official terminal settlement.

No approximate completion factors become reward labels. Reports/requests/penalties
are settled once at terminal, including actions after the last decision callback.
Negative deltas caused by revocation remain negative. gamma=1 makes the sum exact.
"""
    if not snapshots:
        raise ValueError("An empty episode cannot train a policy")
    if report["termination"]["reason"] != "survey_complete":
        raise ValueError("Incomplete/failed episode is not a successful training sample")
    rewards = [float(b["running_total"]) - float(a["running_total"])
               for a, b in zip(snapshots, snapshots[1:])]
    rewards.append(float(report["total"]) - math.fsum(rewards))
    if abs(math.fsum(rewards) - float(report["total"])) > 1e-6:
        raise AssertionError("Reward conservation failed")
    return rewards


def returns_to_go(rewards):
    result = [0.0] * len(rewards)
    total = 0.0
    for i in range(len(rewards) - 1, -1, -1):
        total += rewards[i]
        result[i] = total
    return result
