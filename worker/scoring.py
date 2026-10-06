"""Scoring wrapper: runs the frozen scorer and derives leaderboard metrics."""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from scoring import scorer


class InvalidSubmission(ValueError):
    pass


def derive_metrics(report: dict, n_tiles: int) -> dict:
    """Metrics shown on the leaderboard. The stage-one `score` is authoritative;
    completion and uniformity are informational."""
    completed = int(report.get("completed_tiles", 0))
    completion = completed / n_tiles if n_tiles else 0.0
    values = [float(v) for v in (report.get("region_completion") or {}).values()]
    uniformity = 1.0
    if len(values) >= 2 and any(values):
        uniformity = max(0.0, min(1.0, 1.0 - 2.0 * statistics.pstdev(values)))
    actions = report.get("actions") or []
    observe = [a for a in actions if a.get("action") == "observe" and a.get("valid")]
    return {
        "score": float(report["score"]),
        "science_score": float(report["science_score"]),
        "waste_penalty": float(report.get("waste_penalty", 0.0)),
        "total_waste_seconds": float(report.get("total_waste_seconds", 0.0)),
        "waste_breakdown_seconds": report.get("waste_breakdown_seconds", {}),
        "unavailable_unpenalized_seconds": float(report.get("unavailable_unpenalized_seconds", 0.0)),
        "completed_tiles": completed,
        "total_tiles": n_tiles,
        "completion": round(completion, 6),
        "uniformity": round(uniformity, 6),
        "invalid_actions": int(report.get("invalid_actions", 0)),
        "n_actions": len(actions),
        "n_observations": len(observe),
        "region_completion": report.get("region_completion", {}),
    }


def score_decisions(weather: Path, tiles: Path, config: Path, decisions: Path, report_out: Path) -> dict:
    """Score a decisions.csv; write the full JSON report; return derived metrics.

    Raises InvalidSubmission for schema violations (the scorer's ScoringError)."""
    try:
        report = scorer.score_files(weather, tiles, decisions, config)
    except scorer.ScoringError as exc:
        report_out.parent.mkdir(parents=True, exist_ok=True)
        report_out.write_text(json.dumps({"status": "invalid_submission", "error": str(exc)}, indent=2, sort_keys=True), encoding="utf-8")
        raise InvalidSubmission(str(exc)) from exc
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    n_tiles = len(scorer.load_tiles(tiles))
    return derive_metrics(report, n_tiles)


def validate_decisions_file(path: Path, max_rows: int = 200_000) -> None:
    """Cheap pre-checks before queueing: header + size."""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            header = fh.readline()
            if not header.strip():
                raise InvalidSubmission("decisions.csv is empty")
            cols = [c.strip().lower() for c in header.strip().split(",")]
            missing = [c for c in ("decision_id", "slot_id", "action", "tile_id", "program", "reason") if c not in cols]
            if missing:
                raise InvalidSubmission("decisions.csv missing columns: " + ", ".join(missing))
            for i, _line in enumerate(fh):
                if i > max_rows:
                    raise InvalidSubmission(f"decisions.csv has more than {max_rows} rows")
    except UnicodeDecodeError as exc:
        raise InvalidSubmission("decisions.csv must be UTF-8 text") from exc
