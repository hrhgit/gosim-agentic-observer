"""Where the tools find the parent project, the local judge and the evaluation cards."""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROJECT = Path(os.environ.get("OBSERVER_PROJECT_ROOT", REPO.parent.parent)).resolve()
RUNNER_DIR = PROJECT / "examples" / "_local" / "runner"
DEFAULT_EVAL_SET = os.environ.get("OBSERVER_EVAL_SET", "formal-v4-observed-timeline-v4")


def cards_dir(eval_set: str | None = None) -> Path:
    """Cards folder of an evaluation set: a name under the project's evaluation_assets/, a
    name under this repository's eval_sets/, or a path."""
    name = eval_set or DEFAULT_EVAL_SET
    for base in (PROJECT / "evaluation_assets" / name, REPO / "eval_sets" / name, Path(name)):
        if (base / "cards").is_dir():
            return (base / "cards").resolve()
    raise SystemExit(f"evaluation set not found: {name}")
