#!/usr/bin/env python3
"""Compare evaluation runs card by card: score parts, fault-report delays, wasted exposures, requests.

    python3 tools/analyze_runs.py --eval-set formal-v5-new-four-note-aligned-v1 aligned-ref aligned-llm

Needs runs made with `run_eval.py --keep-bulky` (decisions.csv and observations.csv are read).
Report sources and withheld reports come from the agent's trace.log (this agent only).
Fault onsets come from the evaluation set's assumed conditions, so the delays are relative to that
assumption, not to official hidden data.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PROJECT = HERE.parent.parent


def parse(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


def faults(card_dir: Path) -> list[tuple[dt.datetime, dt.datetime]]:
    with (card_dir / "truth" / "v4_events.csv").open(encoding="utf-8") as handle:
        return sorted((parse(row["actual_start_utc"]), parse(row["actual_end_utc"]))
                      for row in csv.DictReader(handle) if row["event_type"] == "instrument_fault")


def analyse(run: Path, card_dir: Path) -> dict:
    summary = json.loads((run / "summary.json").read_text())
    out = {key: summary.get(key) for key in (
        "total", "sum_best_scores", "report_settlement", "observation_request_reward", "required_missing",
        "uniformity_penalty", "targets_observed", "observe_actions", "decision_requests", "termination_reason")}
    out["wall_seconds"] = (summary.get("fair_clock") or {}).get("run_wall_seconds")
    decisions = run / "decisions.csv"
    if decisions.exists():
        with decisions.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        reports = [parse(row["start_utc"]) for row in rows if row["action"] == "report"]
        delays, missed = [], 0
        for start, end in faults(card_dir):
            inside = [when for when in reports if start <= when < end]
            if inside:
                delays.append(round((inside[0] - start).total_seconds() / 3600, 1))
            else:
                missed += 1
        out["fault_report_delay_hours"] = delays
        out["faults_never_reported"] = missed
        out["reports_total"] = len(reports)
        observes = [row for row in rows if row["action"] == "observe"]
        out["exposure_hours"] = round(sum(float(row["duration_seconds"]) for row in observes) / 3600, 1)
    observations = run / "observations.csv"
    if observations.exists():
        with observations.open(encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            score_column = next((name for name in reader.fieldnames or [] if name in ("score", "contribution", "target_score")), None)
            per_exposure: dict[str, list[float]] = {}
            if score_column:
                for row in reader:
                    per_exposure.setdefault(row.get("observe_index") or row.get("decision_id") or "", []).append(float(row[score_column] or 0))
        if score_column and decisions.exists():
            duration = {row["observe_index"]: float(row["duration_seconds"]) for row in observes}
            empty = [index for index, scores in per_exposure.items() if scores and max(scores) <= 0]
            out["all_zero_exposures"] = len(empty)
            out["all_zero_exposure_hours"] = round(sum(duration.get(index, 0) for index in empty) / 3600, 1)
    trace = run / "trace.log"
    if trace.exists():
        # "report at <utc>: correct=true|false ... source=rules/model | note <id> (<kind>)" and "report withheld at ..."
        by_source: dict[str, list[int]] = {}
        held = 0
        for line in trace.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("report withheld"):
                held += 1
            elif line.startswith("report at ") and " source=" in line:
                source = "note" if " source=note " in line else "detector"
                by_source.setdefault(source, [0, 0])[0 if "correct=true" in line else 1] += 1
        if by_source or held:
            out["reports_by_source (right/wrong)"] = ", ".join(f"{name} {right}/{wrong}" for name, (right, wrong) in sorted(by_source.items()))
            out["detector_reports_withheld"] = held
    report = run / "score_report.json"
    if report.exists():
        requests = json.loads(report.read_text()).get("observation_requests") or []
        rows = requests if isinstance(requests, list) else requests.get("requests") or requests.get("results") or []
        if isinstance(rows, list) and rows:
            paid = [row for row in rows if float(row.get("completion_reward", row.get("reward", 0)) or 0) > 0]
            done = [row for row in paid if row.get("completed") or row.get("status") == "completed"]
            out["rewarded_requests_done"] = f"{len(done)}/{len(paid)}"
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tags", nargs="+")
    parser.add_argument("--eval-set", default="formal-v5-new-four-note-aligned-v1")
    parser.add_argument("--cards", nargs="+", default=["a", "b", "c", "d"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    cards = PROJECT / "evaluation_assets" / args.eval_set / "cards"
    table: dict[str, dict[str, dict]] = {}
    for tag in args.tags:
        for card in args.cards:
            run = HERE / "results" / tag / card
            if (run / "summary.json").exists():
                table.setdefault(card, {})[tag] = analyse(run, cards / card)
    if args.json:
        print(json.dumps(table, indent=1))
        return 0
    for card, runs in table.items():
        print(f"== card {card.upper()}")
        keys = []
        for run in runs.values():
            keys += [key for key in run if key not in keys]
        for key in keys:
            print(f"  {key:28s} " + " | ".join(f"{tag}: {runs[tag].get(key)}" for tag in runs))
    means = {tag: [runs[tag]["total"] for runs in table.values() if tag in runs and runs[tag].get("total") is not None] for tag in args.tags}
    print("== mean total " + " | ".join(f"{tag}: {sum(values) / len(values):.1f} ({len(values)} cards)" for tag, values in means.items() if values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
