#!/usr/bin/env python3
"""Compare native TS against the original baseline using the same engine inputs.

Uses only an explicitly selected formal/self-generated card folder. Search breadth is
fixed at 14 in BOTH offline companions to remove differences caused by measured CPU
time. The submitted agent is not patched. Engine feedback follows Python's actions;
this is a behavior comparison, not an independent TS score or official result.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import subprocess
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[1]
from python_reference import source_directory
PYTHON_SOURCE = source_directory()
RUNNER = PROJECT / "examples" / "_local" / "runner"
sys.path.insert(0, str(PYTHON_SOURCE))
sys.path.insert(0, str(RUNNER))
from agent import Agent  # noqa: E402
from challenge.v4_workflow import V4Workflow  # noqa: E402

def digest(agent):
    sv = agent.sv
    recent = next(reversed(sv.ledger.values())) if sv.ledger else []
    return {"progress": [[j, sv.best[j], sv.gmax[j]] for j, _, _ in recent],
            "required_left": sv.required_left, "band_done": list(sv.band_done.items()),
            "extra": list(sv.extra.items()), "zextra": list(sv.zextra.items()),
            "sky": {name: getattr(sv.sky, name) for name in ("W", "e", "rho", "suspect", "closed_suspect", "low_count", "low_seconds", "free_left", "reports_correct", "reports_false")},
            "offset": {name: getattr(sv.offset, name) for name in ("alt", "az", "margin_alt", "margin_az", "active", "solves", "best_mismatch")},
            "pending": sv.pending if hasattr(sv, "pending") else agent.pending}

def compare(a, b, path="", differences=None):
    if differences is None:
        differences = []
    if isinstance(a, (tuple, list, frozenset, set)):
        a = list(a)
    if isinstance(b, (tuple, list)):
        b = list(b)
    if isinstance(a, bool) or isinstance(b, bool):
        if a != b:
            differences.append((path, a, b))
    elif isinstance(a, (float, int)) and isinstance(b, (float, int)):
        if not math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-8):
            differences.append((path, a, b))
    elif isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            differences.append((path + ".keys", sorted(a), sorted(b)))
        for key in set(a) & set(b):
            compare(a[key], b[key], path + "." + key, differences)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            differences.append((path + ".length", len(a), len(b)))
        for i, (aa, bb) in enumerate(zip(a, b)):
            compare(aa, bb, f"{path}[{i}]", differences)
    elif a != b:
        differences.append((path, a, b))
    return differences

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True)
    parser.add_argument("--cards-root", type=Path, required=True)
    parser.add_argument("--cards", nargs="+", default=list("abcd"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--feedback-only", action="store_true", help="compare state updates on identical exposure metadata; record differing actions without treating them as state errors")
    args = parser.parse_args()
    if any(c not in "abcd" or len(c) != 1 for c in args.cards):
        parser.error("only formal card IDs a, b, c, d are permitted")
    source_root = args.cards_root.resolve()
    if not (source_root.is_relative_to(PROJECT / "evaluation_assets") or source_root == PROJECT / "cards"):
        parser.error("cards must come from this project's formal or self-generated folders")
    args.out.mkdir(parents=True, exist_ok=True)
    summaries = {}
    for card in args.cards:
        out = args.out / card
        out.mkdir(parents=True, exist_ok=True)
        with (out / "python.log").open("w") as py_log, (out / "typescript.log").open("w") as ts_log, (out / "inputs.jsonl").open("w") as inputs:
            saved_stderr, sys.stderr = sys.stderr, py_log
            proc = subprocess.Popen([args.node, str(ROOT / "dist/tools/replay.js")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=ts_log, text=True, env={**os.environ, "PYTHONHASHSEED": "0"})
            holder = [None]
            plans = []
            record = {"card": card, "input_source": str(source_root / card), "search_zones": 14, "feedback_only": args.feedback_only, "decisions_compared": 0, "action_matches": 0, "action_differences": 0, "python_cpu_seconds": 0.0, "typescript_cpu_seconds": 0.0, "differences": [], "message_counts": {}}
            def exchange(message):
                inputs.write(json.dumps(message, default=list, separators=(",", ":")) + "\n")
                proc.stdin.write(json.dumps(message, default=list, separators=(",", ":")) + "\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
                if not line:
                    raise RuntimeError("TS comparison companion exited")
                return json.loads(line)
            def fixed_pace(self, _payload, t, _night):
                self.now, self.k, self.t_floor = t, 14.0, self.sv.t_min
            def initialize(payload):
                holder[0] = Agent(payload)
                holder[0]._pace = types.MethodType(fixed_pace, holder[0])
                original = holder[0].planner._evaluate
                def evaluate(*args):
                    plan = original(*args)
                    plans.append(plan)
                    return plan
                holder[0].planner._evaluate = evaluate
                ts = exchange(V4Workflow.initialize_message(payload))
                differences = compare(digest(holder[0]), ts["state"], "initialize")
                if differences:
                    record["differences"] = differences
                    raise RuntimeError("initial state differs")
            def decide(message, _deadline):
                for item in message["payload"].get("new_messages") or []:
                    kind = item.get("record_type", "unknown")
                    record["message_counts"][kind] = record["message_counts"].get(kind, 0) + 1
                start = time.process_time()
                plans.clear()
                action = holder[0].decide(message["payload"])
                record["python_cpu_seconds"] += time.process_time() - start
                ts = exchange(message)
                record["typescript_cpu_seconds"] += ts["cpu_seconds"]
                action_diff = compare(action, ts["action"], "action")
                record["action_differences" if action_diff else "action_matches"] += 1
                if action_diff and "first_action_difference" not in record:
                    record["first_action_difference"] = {"sequence": message["decision_sequence"], "differences": action_diff}
                py_state, ts_state = digest(holder[0]), ts["state"]
                if args.feedback_only:
                    py_state.pop("pending")
                    ts_state.pop("pending")
                differences = ([] if args.feedback_only else action_diff) + compare(py_state, ts_state, "state")
                if differences:
                    record["differences"] = differences
                    record["first_difference_sequence"] = message["decision_sequence"]
                    (out / "difference-input.json").write_text(json.dumps(message, indent=2))
                    (out / "difference-python.json").write_text(json.dumps({"action": action, "state": digest(holder[0])}, default=list, indent=2))
                    (out / "difference-typescript.json").write_text(json.dumps(ts, indent=2))
                    ts_plans = exchange({"message_type": "inspect_plans"})
                    (out / "difference-plans-python.json").write_text(json.dumps(plans, indent=2))
                    (out / "difference-plans-typescript.json").write_text(json.dumps(ts_plans["plans"], indent=2))
                    raise RuntimeError("migration behavior differs")
                if args.feedback_only:
                    exchange({"message_type": "sync_pending", "pending": holder[0].pending})
                record["decisions_compared"] += 1
                if record["decisions_compared"] % 1000 == 0:
                    print(f"{card}: compared {record['decisions_compared']} decisions", file=saved_stderr, flush=True)
                return {"protocol_version": message["protocol_version"], "message_type": "decision_response", "decision_sequence": message["decision_sequence"], **action}
            try:
                result = V4Workflow(source_root / card).run(decide, out / "engine", initialize=initialize, wallclock_seconds=900)
                record["termination_reason"] = result["termination_reason"]
                record["termination_detail"] = result["termination_detail"]
                record["reference_score"] = result["score_report"]["total"]
                record["passed"] = result["termination_reason"] == "survey_complete" and not record["differences"]
            finally:
                proc.stdin.close()
                proc.wait()
                sys.stderr = saved_stderr
            summaries[card] = record
            (out / "comparison.json").write_text(json.dumps(record, indent=2) + "\n")
            print(json.dumps({k: v for k, v in record.items() if k != "differences"}), flush=True)
            if record["differences"]:
                print(json.dumps(record["differences"][:12]), flush=True)
    (args.out / "summary.json").write_text(json.dumps(summaries, indent=2) + "\n")
    return 0 if all(row["passed"] for row in summaries.values()) else 1

if __name__ == "__main__":
    raise SystemExit(main())
