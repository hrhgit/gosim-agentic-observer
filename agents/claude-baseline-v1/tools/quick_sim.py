#!/usr/bin/env python3
"""In-process development run: the engine's own run loop with this agent as a Python callable.

    python3.12 tools/quick_sim.py --card a --nights 25 [--set kappa=0.5 ...] [--out results/tmp-x]

No subprocess, no JSON pipe and no fair clock, so it is quicker to iterate with than
tools/run_eval.py and can stop after the first N nights. The simulation and the scoring are the
engine's (challenge.v4_runner.run_scenario); a truncated run's `total` is meaningless (every
unobserved required target is charged) but its components compare variants at equal length.
Numbers for the record always come from tools/run_eval.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import DEFAULT_EVAL_SET as EVAL_SET, PROJECT, REPO, RUNNER_DIR as RUNNER, cards_dir  # noqa: E402

CARDS = cards_dir()
sys.path.insert(0, str(RUNNER))
sys.path.insert(0, str(REPO / "agent"))

from challenge.v4_runner import run_scenario  # noqa: E402
from challenge.v4_workflow import V4Workflow, V4_SCENARIO_PATH  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--card", required=True)
    parser.add_argument("--nights", type=int, default=0, help="stop after this many nights (0 = whole survey)")
    parser.add_argument("--set", action="append", default=[], help="knob=value (repeatable)")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    for pair in args.set:
        key, _, value = pair.partition("=")
        os.environ["BASE_" + key.upper()] = value

    import agent as agent_module  # noqa: E402 - after the knobs are in the environment

    card_dir = CARDS / args.card
    workflow = V4Workflow(card_dir)
    payload = json.loads(json.dumps(workflow.initialize_payload(900.0, 1.0)))
    started = time.process_time()
    bot = agent_module.Agent(payload)
    nights = bot.sv.nights
    stop_at = nights[args.nights][0] if 0 < args.nights < len(nights) else None
    spent = {"agent": 0.0, "decisions": 0}

    def factory(_context):
        def step(snapshot):
            if stop_at is not None and agent_module.parse_utc(snapshot["now_utc"]) >= stop_at:
                return None
            began = time.process_time()
            try:
                action = bot.decide(json.loads(json.dumps(snapshot)))
            except Exception:  # noqa: BLE001 - mirror agent.py: one bad decision costs a wait
                import traceback
                traceback.print_exc()
                bot.pending = None
                action = {"action": "wait", "duration_seconds": 900}
            spent["agent"] += time.process_time() - began
            spent["decisions"] += 1
            if not args.quiet and spent["decisions"] % 500 == 0:
                print(bot.status(snapshot), file=sys.stderr, flush=True)
            return action
        return step

    out = args.out or Path(tempfile.mkdtemp(prefix="quick-sim-"))
    report = run_scenario(card_dir / V4_SCENARIO_PATH, factory, out)
    total_cpu = time.process_time() - started
    sky = bot.sv.sky
    summary = {
        "card": args.card, "eval_set": EVAL_SET, "nights": args.nights or len(nights), "knobs": args.set,
        "total": report["total"], **report["components"],
        "targets_observed": report["counts"]["targets_observed"],
        "required_missing": report["counts"]["required_missing"],
        "observe_actions": report["counts"]["observe_actions"],
        "requests": f"{report['counts']['observation_requests_completed']}/{report['counts']['observation_requests_issued']}",
        "agent_cpu_s": round(spent["agent"], 1), "engine_cpu_s": round(total_cpu - spent["agent"], 1),
        "ms_per_decision": round(1000 * spent["agent"] / max(1, spent["decisions"]), 2),
        "misses": f"{bot.misses}/{bot.assigned}", "reports": f"+{sky.reports_correct}/-{sky.reports_false}",
        "offset": [round(bot.sv.offset.alt, 4), round(bot.sv.offset.az, 4)] if bot.sv.offset.active else None,
        "price_capped": f"{bot.planner.stat_negative}/{bot.planner.stat_plans}",
        "gain_vs_predicted": round(bot.realised_gain / max(bot.predicted_gain, 1e-9), 3),
        "pointing_truth": report.get("organizer_only", {}).get("pointing_offset_deg"),
        "out": str(out),
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
