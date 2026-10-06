#!/usr/bin/env python3
"""Run both unchanged agents sequentially on the same self-generated A-D cards."""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from python_reference import source_directory

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[1]
RUNNER = PROJECT / "examples" / "_local" / "runner" / "run_local.py"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", required=True)
    parser.add_argument("--cards-root", type=Path, required=True)
    parser.add_argument("--cards", nargs="+", default=list("abcd"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--languages", nargs="+", choices=["python", "typescript"], default=["typescript", "python"])
    parser.add_argument("--reuse-completed", action="store_true")
    args = parser.parse_args()
    python_source = source_directory()
    if any(c not in "abcd" or len(c) != 1 for c in args.cards):
        parser.error("only formal card IDs a, b, c, d are permitted")
    cards = args.cards_root.resolve()
    if not (cards.is_relative_to(PROJECT / "evaluation_assets") or cards == PROJECT / "cards"):
        parser.error("only this project's formal/self-generated folders are permitted")
    all_rows = {}
    for card in args.cards:
        for language in args.languages:
            out = args.out / language / card
            out.mkdir(parents=True, exist_ok=True)
            existing = out / "workflow_result.json"
            if not (args.reuse_completed and existing.exists() and json.loads(existing.read_text()).get("termination_reason") == "survey_complete"):
                cwd = ROOT if language == "typescript" else python_source
                # Runner splits this with shlex, without a shell. Quote exact executable paths.
                import shlex
                command = shlex.join([args.node, "dist/agent.js"] if language == "typescript" else [sys.executable, "-u", "agent.py"])
                print(f"run {language} {card}", flush=True)
                started = time.monotonic()
                proc = subprocess.run([sys.executable, str(RUNNER), "--card", str(cards / card), "--agent", command, "--agent-cwd", str(cwd), "--out", str(out), "--quiet"], capture_output=True, text=True)
                (out / "runner.stdout.json").write_text(proc.stdout)
                (out / "runner.stderr.log").write_text(proc.stderr)
                (out / "execution.json").write_text(json.dumps({"exit_code": proc.returncode, "elapsed_seconds": time.monotonic() - started, "input_source": str(cards / card), "command": command}, indent=2) + "\n")
                if proc.returncode:
                    print(proc.stdout + proc.stderr, file=sys.stderr)
                    return proc.returncode
            workflow = json.loads(existing.read_text())
            report = json.loads((out / "score_report.json").read_text())
            row = {"card": card, "language": language, "input_source": str(cards / card), "termination_reason": workflow["termination_reason"], "total": report["total"], "fair_clock": workflow["fair_clock"], "components": report["components"], "counts": report["counts"], "decision_requests": workflow["decision_requests"]}
            (out / "summary.json").write_text(json.dumps(row, indent=2) + "\n")
            all_rows[f"{language}/{card}"] = row
            print(json.dumps({"card": card, "language": language, "total": row["total"], "cpu_seconds": row["fair_clock"]["cpu_seconds"], "end": row["termination_reason"]}), flush=True)
            (args.out / "comparison.json").write_text(json.dumps(all_rows, indent=2) + "\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
