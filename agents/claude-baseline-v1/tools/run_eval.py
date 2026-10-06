#!/usr/bin/env python3
"""Run an agent on the local evaluation cards and collect the scores.

The cards are formal public inputs, received announcements/requests, and explicitly assumed
hidden conditions (evaluation_assets/formal-v4-observed-timeline-v4 by default). They are not
the hidden competition data; scores here are for comparing agents locally only.

    python3 tools/run_eval.py --tag my-run                      # this repo's agent, cards a b c d
    python3 tools/run_eval.py --tag ref --agent-cwd ../../examples/python-pro --env OBSERVER_MODEL_DISABLED=1

Each card runs through the unmodified local runner (examples/_local/runner/run_local.py), so
the time budget and the scoring are the platform's own code.

Real-time cap. The platform ends a card 1800 s of real time after the first request (twice the
900 s CPU budget on its reference machine). The CPU budget is normalised by the machine's speed
factor but that cap is not, so on a machine that is slower than the reference (or busy) the cap
would bite several times earlier, in work done, than it does on the platform. By default this
script therefore measures the speed factor with the engine's own calibration workload and sets
the engine's documented organizer variable OBSERVER_WALL_CAP_SECONDS to 1800 x factor (never
below 1800, at most 21600), which restores the platform's ratio. `--wall-cap 1800` keeps the
literal cap. The value used is recorded in the summary.
Outputs: results/<tag>/<card>/{score_report.json,summary.json,agent.log,...} and
results/<tag>/summary.json + summary.md.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import DEFAULT_EVAL_SET as EVAL_SET, PROJECT, REPO, RUNNER_DIR, cards_dir  # noqa: E402

RUNNER = RUNNER_DIR / "run_local.py"
PYTHON = os.environ.get("OBSERVER_PYTHON", "python3.12")

# Large per-run artefacts that are reproducible from the agent + card; kept out of git.
BULKY = ("decisions.csv", "observations.csv", "messages.jsonl", "actions.jsonl", "workflow_result.json")


def machine_factor() -> float:
    """Speed factor of this machine right now, measured exactly as the engine measures it."""
    code = ("import statistics,sys;sys.path.insert(0,sys.argv[1]);"
            "from challenge.fair_clock import time_unit,REFERENCE_UNIT_SECONDS;"
            "print(statistics.median(time_unit() for _ in range(5))/REFERENCE_UNIT_SECONDS)")
    proc = subprocess.run([PYTHON, "-c", code, str(RUNNER.parent)], capture_output=True, text=True)
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 1.0


def run_card(card: str, tag: str, agent: str, agent_cwd: Path, env_pairs: list[str], wallclock: float,
             wall_cap: float, cards: Path) -> dict:
    out = REPO / "results" / tag / card
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["OBSERVER_WALL_CAP_SECONDS"] = str(int(wall_cap))
    for pair in env_pairs:
        key, _, value = pair.partition("=")
        env[key] = value
    command = [PYTHON, str(RUNNER), "--card", str(cards / card), "--agent", agent, "--agent-cwd", str(agent_cwd),
               "--out", str(out), "--wallclock", str(wallclock), "--quiet"]
    if env_pairs:
        command.append("--inherit-env")
    started = time.monotonic()
    proc = subprocess.run(command, capture_output=True, text=True, env=env)
    elapsed = time.monotonic() - started
    (out / "runner.stderr.log").write_text(proc.stderr, encoding="utf-8")
    try:
        summary = json.loads(proc.stdout[proc.stdout.index("{"):])
    except (ValueError, json.JSONDecodeError):
        summary = {"card": card, "termination_reason": "runner_failed", "error": proc.stdout[-2000:] + proc.stderr[-2000:]}
    summary["runner_exit_code"] = proc.returncode
    summary["runner_wall_seconds"] = round(elapsed, 1)
    summary.pop("outputs", None)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def markdown(tag: str, rows: dict[str, dict]) -> str:
    head = ("| card | total | sum_best | required_pen (missing) | uniformity | reports | requests (done/issued) | "
            "targets | observes | decisions | budget used | wall s | end |\n"
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|\n")
    lines = []
    total = 0.0
    for card in sorted(rows):
        s = rows[card]
        if "total" not in s:
            lines.append(f"| {card.upper()} | FAILED: {s.get('termination_reason')} | | | | | | | | | | | |")
            continue
        total += s["total"]
        clock = s.get("fair_clock", {})
        lines.append(
            f"| {card.upper()} | {s['total']:.1f} | {s['sum_best_scores']:.1f} | {s['required_penalty']:.0f} ({s['required_missing']}) | "
            f"{s['uniformity_penalty']:.1f} | {s['report_settlement']:.0f} | {s['observation_request_reward']:.0f} "
            f"({s['observation_requests_completed']}/{s['observation_requests_issued']}) | {s['targets_observed']} | "
            f"{s['observe_actions']} | {s['decision_requests']} | {clock.get('charged_seconds', 0):.0f}/{clock.get('budget_seconds', 0):.0f} | "
            f"{clock.get('run_wall_seconds', 0):.0f} | {s['termination_reason']} |")
    n = sum(1 for s in rows.values() if "total" in s)
    mean = total / n if n else float("nan")
    return f"### {tag}\n\n{head}" + "\n".join(lines) + f"\n\n**mean over {n} cards: {mean:.1f}** (sum {total:.1f})\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", required=True, help="results/<tag>/ output folder name")
    parser.add_argument("--cards", nargs="+", default=["a", "b", "c", "d"])
    parser.add_argument("--agent", default=f"{PYTHON} -u agent.py", help="agent command")
    parser.add_argument("--agent-cwd", type=Path, default=REPO / "agent")
    parser.add_argument("--env", action="append", default=[], help="KEY=VALUE passed to the agent (repeatable)")
    parser.add_argument("--jobs", type=int, default=4, help="cards run in parallel")
    parser.add_argument("--wallclock", type=float, default=900.0)
    parser.add_argument("--keep-bulky", action="store_true", help="keep decisions.csv/observations.csv etc.")
    parser.add_argument("--wall-cap", default="auto",
                        help="real-time cap per card in seconds, or 'auto' = 1800 x measured speed factor")
    parser.add_argument("--eval-set", default=EVAL_SET,
                        help="name under the project's evaluation_assets/ or this repo's eval_sets/, or a path "
                             "(default: %(default)s)")
    parser.add_argument("--cap-margin", type=float, default=1.25,
                        help="extra factor on the automatic cap for the load of the parallel runs themselves")
    args = parser.parse_args()

    agent_cwd = args.agent_cwd.resolve()
    cards = cards_dir(args.eval_set)
    factor = machine_factor()
    if args.wall_cap == "auto":
        wall_cap = max(1800.0, min(21600.0, 1800.0 * factor * args.cap_margin * args.wallclock / 900.0))
    else:
        wall_cap = float(args.wall_cap)
    print(f"[{args.tag}] machine speed factor {factor:.2f}, real-time cap {wall_cap:.0f} s per card",
          file=sys.stderr, flush=True)
    rows: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_card, card, args.tag, args.agent, agent_cwd, args.env, args.wallclock, wall_cap,
                               cards): card for card in args.cards}
        for future in concurrent.futures.as_completed(futures):
            card = futures[future]
            rows[card] = future.result()
            s = rows[card]
            print(f"[{args.tag}] card {card}: total={s.get('total')} end={s.get('termination_reason')} "
                  f"wall={s.get('runner_wall_seconds')}s", file=sys.stderr, flush=True)
    out = REPO / "results" / args.tag
    meta = {"tag": args.tag, "agent": args.agent, "agent_cwd": str(agent_cwd), "env": args.env,
            "eval_set": args.eval_set, "python": PYTHON, "jobs": args.jobs,
            "machine_speed_factor_at_start": round(factor, 3), "wall_cap_seconds": wall_cap,
            "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cards": rows}
    (out / "summary.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    text = markdown(args.tag, rows) + (f"\nmachine speed factor at start {factor:.2f}; real-time cap {wall_cap:.0f} s "
                                       f"per card ({'auto' if args.wall_cap == 'auto' else 'fixed'}); evaluation set `{args.eval_set}` "
                                       f"(formal public inputs + self-generated weather, events and requests)\n")
    (out / "summary.md").write_text(text, encoding="utf-8")
    if not args.keep_bulky:
        for card in args.cards:
            for name in BULKY:
                path = out / card / name
                if path.exists():
                    path.unlink()
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
