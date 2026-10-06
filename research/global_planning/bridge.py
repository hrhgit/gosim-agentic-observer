from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .common import AGENT, ROOT

DEFAULT_NODE = Path("/tmp/gosim-node22/node-v22.23.3-darwin-arm64/bin/node")


class PlanningBridge:
    def __init__(self, initialize, output, *, node=None, models=True, rule_only=False):
        executable = str(node or os.environ.get("GLOBAL_PLANNER_NODE", DEFAULT_NODE))
        version = subprocess.check_output([executable, "--version"], text=True).strip()
        if not version.startswith("v22."):
            raise ValueError("Node.js 22 required")
        env = dict(os.environ, BASE_MODEL_PYTHON=str(ROOT / "research/observing_conditions/.venv/bin/python"),
                   BASE_CONDITIONS_MODELS="reliability" if models else "off", BASE_FAULT_MODEL="on" if models else "off")
        self.log = Path(output).open("w")
        self.process = subprocess.Popen([executable, "dist/tools/planning-bridge.js"], cwd=AGENT,
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1)
        self.send(dict(operation="initialize", payload=initialize, rule_only=rule_only))
        if self.read()["type"] != "ready":
            raise RuntimeError("Bridge did not initialize")

    def send(self, value):
        self.process.stdin.write(json.dumps(value, allow_nan=False) + "\n")
        self.process.stdin.flush()

    def read(self):
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError(f"Bridge exited {self.process.wait()}; see {self.log.name}")
        return json.loads(line)

    def close(self):
        if self.process.poll() is None:
            self.send(dict(operation="close"))
        self.process.stdin.close()
        self.process.wait()
        self.process.stdout.close()
        self.log.close()
