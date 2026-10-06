#!/usr/bin/env python3
"""Run an agent on the local evaluation cards and collect the scores.

The cards are formal public inputs, received announcements/requests, and explicitly assumed
hidden conditions (evaluation_assets/formal-v4-observed-timeline-v4 by default). They are not
the hidden competition data; scores here are for comparing agents locally only.

    python3 tools/run_eval.py --tag my-run                      # this agent, cards a b c d
    python3 tools/run_eval.py --tag ref --agent-cwd ../baseline-typescript-v1 --env BASE_MODEL_PYTHON=/path/to/python

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

`--platform-timing` is for runs where real time matters (an agent that waits for a language model):
the cap is 1800 x factor with no lower bound, so a machine faster than the reference gets a
proportionally shorter cap, and LLM_CACHE_LATENCY (the delay this agent adds to a cached model
answer) is set to --llm-latency x factor. Engine time, model latency and cap then stand in the
platform's proportions. Keep the number of parallel runs small enough that the factor holds.

`--sim-wall` goes one step further and takes real time out of the measurement altogether: this
agent (not the engine) keeps a virtual real-time clock (src/sim/clock.ts). Every decision is
charged the engine time the platform took at the same point of the same card, by the law fitted
below to the status lines of the downloaded formal runs; every model reading is charged
--llm-latency seconds; at 1800 s the agent stops observing, as the platform would end the run.
The engine's own cap is moved out of the way (21600 s). Results then do not depend on how busy
this machine is, so cards can run in parallel. It only works for this agent's code: compare
against the baseline behaviour with `--env HANDOVER=off --env BASE_IDLE_WAIT=0 --env
BASE_ZERO_REWARD_SKIP=0` (the same planner and pacing as ../baseline-typescript-v1).

Each card's agent also gets AGENT_TRACE_FILE=results/<tag>/<card>/trace.log (this agent appends
its log there without the per-decision model records; the runner keeps only the last 64 KiB).
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

# Engine seconds per decision on the platform: base_ms + slope * 1e-6 ms * fibres_assigned * (1 + requests_issued).
# Least-squares fit to the agent status lines (every 250 decisions: eng=, miss=x/assigned) in the formal runs
# downloaded on 2026-10-05 (artifacts/formal-card-refresh-2026-10-05/captured-runs/{8-a1,7-b1,6-c1,5-d1}-v5/agent.log);
# residual 19-77 ms. Cards not listed use the pooled fit.
PLATFORM_ENGINE = {"a": (31.8, 222.6), "b": (17.4, 174.6), "c": (9.2, 174.6), "d": (0.0, 260.1)}
PLATFORM_ENGINE_POOLED = (15.0, 220.0)

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
             wall_cap: float, cards: Path, sim_wall: float | None = None) -> dict:
    out = REPO / "results" / tag / card
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["OBSERVER_WALL_CAP_SECONDS"] = str(int(wall_cap))
    trace = out / "trace.log"
    trace.unlink(missing_ok=True)
    env["AGENT_TRACE_FILE"] = str(trace)
    if sim_wall is not None:
        base_ms, slope = PLATFORM_ENGINE.get(card, PLATFORM_ENGINE_POOLED)
        env.update({"SIM_WALL": "1", "SIM_WALL_CAP": "1800", "SIM_ENGINE_BASE_MS": str(base_ms), "SIM_ENGINE_SLOPE": str(slope),
                    "SIM_LLM_LATENCY": str(sim_wall), "LLM_CACHE_LATENCY": "0"})
    for pair in env_pairs:
        key, _, value = pair.partition("=")
        env[key] = value
    command = [PYTHON, str(RUNNER), "--card", str(cards / card), "--agent", agent, "--agent-cwd", str(agent_cwd),
               "--out", str(out), "--wallclock", str(wallclock), "--quiet", "--inherit-env"]
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
    if trace.exists():
        for line in trace.read_text(encoding="utf-8", errors="replace").splitlines():
            for key, name in (("SURVEY26_SIM_WALL_V1 ", "sim_wall"), ("SURVEY26_HANDOVER_V1 ", "handover")):
                if line.startswith(key):
                    try:
                        summary[name] = json.loads(line[len(key):])
                    except json.JSONDecodeError:
                        pass
    summary.pop("outputs", None)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def sim_text(summary: dict) -> str:
    sim = summary.get("sim_wall")
    if not sim:
        return ""
    text = f"{sim['used']:.0f} ({sim['waited_for_model']:.0f})"
    return text + (f", cap reached at {sim['expired_at']}" if sim.get("expired_at") else "")


def markdown(tag: str, rows: dict[str, dict]) -> str:
    head = ("| card | total | sum_best | required_pen (missing) | uniformity | reports | requests (done/issued) | "
            "targets | observes | decisions | budget used | wall s | end | virtual wall s (waited for model) |\n"
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|\n")
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
            f"{clock.get('run_wall_seconds', 0):.0f} | {s['termination_reason']} | {sim_text(s)} |")
    n = sum(1 for s in rows.values() if "total" in s)
    mean = total / n if n else float("nan")
    return f"### {tag}\n\n{head}" + "\n".join(lines) + f"\n\n**mean over {n} cards: {mean:.1f}** (sum {total:.1f})\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", required=True, help="results/<tag>/ output folder name")
    parser.add_argument("--cards", nargs="+", default=["a", "b", "c", "d"])
    parser.add_argument("--agent", default="node dist/agent.js", help="agent command")
    parser.add_argument("--agent-cwd", type=Path, default=REPO)
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
    parser.add_argument("--platform-timing", action="store_true",
                        help="cap = 1800 x speed factor (no lower bound) and model latency scaled by the same factor")
    parser.add_argument("--llm-latency", type=float, default=8.0,
                        help="seconds one model reading takes on the platform (with --platform-timing or --sim-wall)")
    parser.add_argument("--sim-wall", action="store_true",
                        help="virtual real-time clock inside this agent, calibrated on the formal runs (see above)")
    args = parser.parse_args()

    agent_cwd = args.agent_cwd.resolve()
    cards = cards_dir(args.eval_set)
    factor = machine_factor()
    if args.sim_wall:
        wall_cap = 21600.0
    elif args.platform_timing:
        wall_cap = 1800.0 * factor * args.wallclock / 900.0
        if not any(pair.startswith("LLM_CACHE_LATENCY=") for pair in args.env):
            args.env.append(f"LLM_CACHE_LATENCY={args.llm_latency * factor:.2f}")
    elif args.wall_cap == "auto":
        wall_cap = max(1800.0, min(21600.0, 1800.0 * factor * args.cap_margin * args.wallclock / 900.0))
    else:
        wall_cap = float(args.wall_cap)
    print(f"[{args.tag}] machine speed factor {factor:.2f}, real-time cap {wall_cap:.0f} s per card",
          file=sys.stderr, flush=True)
    rows: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_card, card, args.tag, args.agent, agent_cwd, args.env, args.wallclock, wall_cap,
                               cards, args.llm_latency if args.sim_wall else None): card for card in args.cards}
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
            "platform_timing": args.platform_timing, "sim_wall": args.sim_wall,
            "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cards": rows}
    (out / "summary.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    text = markdown(args.tag, rows) + (f"\nmachine speed factor at start {factor:.2f}; real-time cap {wall_cap:.0f} s "
                                       f"per card ({'virtual clock, cap 1800 s' if args.sim_wall else 'platform timing' if args.platform_timing else 'auto' if args.wall_cap == 'auto' else 'fixed'}); evaluation set `{args.eval_set}` "
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
