"""Private environment process. Import only the checksummed official engine copy.

The trainer receives official initialize/snapshots, never scenario/context objects.
Training uses natural episode termination. Fair-clock acceptance uses run_local.py.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import resource

from .common import ENGINE, verify_engine

sys.path.insert(0, str(ENGINE))
from challenge.v4_workflow import V4Workflow
from challenge.v4_runner import run_scenario


def emit(value):
    print(json.dumps(value, allow_nan=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    from pathlib import Path
    engine_hash = verify_engine()
    started = time.perf_counter()
    workflow = V4Workflow(Path(args.bundle))
    public = workflow.initialize_payload(900, 1)
    # The arbitrary scenario name/seed is not an inference feature.
    emit({"type": "initialize", "payload": public, "engine_manifest": engine_hash})
    callback_seconds = 0.0

    def factory(_private_context):
        def decide(snapshot):
            nonlocal callback_seconds
            before = time.perf_counter()
            emit({"type": "observation", "payload": snapshot})
            line = sys.stdin.readline()
            if not line:
                raise RuntimeError("Trainer disconnected; incomplete episode")
            action = json.loads(line)
            callback_seconds += time.perf_counter() - before
            return action
        return decide

    report = run_scenario(workflow.scenario_path, factory, Path(args.out))
    report.pop("organizer_only", None)
    elapsed = time.perf_counter() - started
    emit({"type": "done", "report": report, "timing": {
        "wall_seconds": elapsed, "engine_seconds": elapsed - callback_seconds,
        "callback_seconds": callback_seconds,
        "engine_cpu_seconds": time.process_time(),
        "engine_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
        "fair_clock": False}})


if __name__ == "__main__":
    main()
