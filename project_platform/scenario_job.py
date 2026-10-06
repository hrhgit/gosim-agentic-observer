"""Bound private calibration work before the participant timer can start."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from .scenario_instances import InstanceError, prepare_instance


def prepare_bounded(template: Path, destination: Path, *, seed: str, profile: dict,
                    max_candidates: int = 32, timeout_seconds: float = 1200) -> tuple[Path, dict]:
    # Keep the key off process arguments and disk logs. A child lets the parent
    # enforce a real deadline even during expensive geometry calculations. The
    # participant has a 1500-second startup window; its scoring clock has not
    # begun. Timeout fails the attempt without serving an uncalibrated scenario.
    request = {"template": str(template.resolve()), "destination": str(destination.resolve()),
               "seed": seed, "profile": profile, "max_candidates": max_candidates}
    try:
        result = subprocess.run([sys.executable, "-m", "project_platform.scenario_job"],
            input=json.dumps(request), text=True, capture_output=True,
            cwd=Path(__file__).resolve().parents[1], timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        raise InstanceError("scenario_preparation_timeout") from None
    if result.returncode:
        # Never surface a Python traceback, private request or generated data.
        raise InstanceError("scenario_preparation_failed")
    data = json.loads(result.stdout)
    return Path(data["scenario"]), data["record"]


def main() -> int:
    try:
        request = json.load(sys.stdin)
        scenario, record = prepare_instance(Path(request["template"]), Path(request["destination"]),
            seed=request["seed"], profile=request["profile"], max_candidates=request["max_candidates"])
        # stdout is a private parent-child pipe, never a workflow log.
        sys.stdout.write(json.dumps({"scenario": str(scenario), "record": record}))
        return 0
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
