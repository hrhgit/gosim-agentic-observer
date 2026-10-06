#!/usr/bin/env python3
"""Run your agent against a public v4 card the way the platform does, then print the score.

    python3 local_runner.py [--card cards/demo] [--agent agent] [--wallclock 900] [--out run_output]

What happens (same as on the platform):
  * your agent starts as its own process, in its own folder, speaking participant-agent-protocol-v4
    (one JSON object per line on stdin/stdout; stderr goes to <out>/agent.log);
  * it gets one `initialize`, then `decision_request` after `decision_request`, and answers each one;
  * one global wall clock (default 900 s, the platform's cap per card) starts at the first request;
  * at the end it gets one `finish` message, stdin is closed, and it has 30 grace seconds to exit.

Environment: the agent gets a clean environment (PATH, HOME/TMPDIR in <out>/scratch), the `environment`
block of <agent>/observer.project.json, then KEY=VALUE lines from <agent>/.env. On the platform
OPENAI_BASE_URL / OPENAI_API_KEY are injected; put them in <agent>/.env to try the LLM hook locally.

The engine is the platform's own adapter (challenge/v4_workflow.py, vendored unchanged).

The card's truth/ folder (hidden weather, events, stress events) is read by this runner only, to score
the run. Your agent must never read it: on the platform the agent only has its own folder, and a formal
card's truth is never published. Before a run the runner checks the agent folder and refuses to start if
the card lies inside it or a source file names a truth file (override with --allow-truth-refs).

Outputs in --out: decisions.csv, observations.csv, messages.jsonl, score_report.json, workflow_result.json,
actions.jsonl, agent.log. The last stdout line is a JSON summary. Exit code 0 = the run ended normally, 2 = agent error.
Standard library only (Python 3.9+).
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 9):
    sys.stderr.write("local_runner: Python 3.9 or newer is required\n")
    sys.exit(3)

import argparse
import json
import os
import re
import time
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent
if str(KIT_ROOT) not in sys.path:
    sys.path.insert(0, str(KIT_ROOT))

from challenge.local_transport import AgentProcess, load_card, run_card, scenario_path  # noqa: E402
from challenge.v4_workflow import PROTOCOL_VERSION, V4Workflow  # noqa: E402

ENTRY_CANDIDATES = ("baseline_agent.py", "agent.py", "main.py")
SAFE_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
PROTECTED_KEYS = {"PATH", "HOME", "TMPDIR", "LD_PRELOAD", "PYTHONPATH", "PYTHONSTARTUP"}
DEFAULT_WALLCLOCK = 900.0
TRUTH_MARKERS = ("truth/", "truth\\", "v4_weather_truth", "v4_slots.csv", "v4_events.csv",
                 "v4_earthquake_effects", "v4_stress_events")
SKIPPED_DIRS = {"__pycache__", "node_modules", "venv", "site-packages"}
SCANNED_SUFFIXES = {".py", ".sh", ".js", ".ts", ".json", ".toml", ".cfg", ".ini", ".txt", ".yaml", ".yml", ""}


def load_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.is_file():
        return env
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if SAFE_ENV_KEY.match(key):
            env[key] = value
    return env


def find_entry(agent: Path) -> Path:
    if agent.is_file():
        return agent.resolve()
    for name in ENTRY_CANDIDATES:
        if (agent / name).is_file():
            return (agent / name).resolve()
    raise SystemExit(f"no entry script in {agent}: expected one of {', '.join(ENTRY_CANDIDATES)}")


def truth_problems(agent_dir: Path, card_dir: Path) -> list[str]:
    """Why this agent folder could read the card's hidden truth/ files (empty list: none found).

    A static check, not a sandbox: it catches the card inside the agent folder (pack_agent.py would ship
    it) and source files that name a truth file. Reading the truth locally makes a score the platform
    will never reproduce."""
    problems = []
    agent_dir, card_dir = agent_dir.resolve(), card_dir.resolve()
    if card_dir == agent_dir or agent_dir in card_dir.parents:
        problems.append(f"the card folder {card_dir} is inside the agent folder")
    scanned = 0
    for path in sorted(agent_dir.rglob("*")):
        rel = path.relative_to(agent_dir)
        if any(part.startswith(".") or part in SKIPPED_DIRS for part in rel.parts):
            continue
        if not path.is_file() or path.suffix.lower() not in SCANNED_SUFFIXES or path.stat().st_size > 2_000_000:
            continue
        scanned += 1
        if scanned > 2000:
            break
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = sorted({marker for marker in TRUTH_MARKERS if marker in text})
        if hits:
            problems.append(f"{rel.as_posix()} mentions {', '.join(hits)}")
    return problems


def agent_environment(agent_dir: Path, scratch: Path, card: dict, wallclock: float, inherit: bool) -> tuple[dict, list[str]]:
    base = dict(os.environ) if inherit else {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
    if sys.platform == "win32" and not inherit:
        base["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "C:\\Windows")
    env = {**base, "HOME": str(scratch), "TMPDIR": str(scratch), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
           "PARTICIPANT_PROTOCOL": PROTOCOL_VERSION, "SAC_SCENARIO": card["scenario_slug"],
           "SAC_WALLCLOCK_SECONDS": str(int(wallclock)), "SAC_LOCAL_RUNNER": "1"}
    manifest = agent_dir / "observer.project.json"
    if manifest.is_file():
        try:
            for key, value in (json.loads(manifest.read_text(encoding="utf-8")).get("environment") or {}).items():
                if SAFE_ENV_KEY.match(str(key)) and key not in PROTECTED_KEYS and isinstance(value, str):
                    env[key] = value
        except ValueError:
            pass
    dotenv = load_dotenv(agent_dir / ".env")
    for key, value in dotenv.items():
        if key not in PROTECTED_KEYS:
            env[key] = value
    return env, sorted(dotenv)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--card", type=Path, default=KIT_ROOT / "cards" / "demo", help="card folder (default: cards/demo)")
    parser.add_argument("--agent", type=Path, default=KIT_ROOT / "agent",
                        help="entry script, or a folder with baseline_agent.py / agent.py / main.py (default: agent/)")
    parser.add_argument("--wallclock", type=float, default=DEFAULT_WALLCLOCK,
                        help=f"global wall clock in seconds (default {DEFAULT_WALLCLOCK:g}); as on the platform the "
                             "effective budget is min(this, the card's limit, 900)")
    parser.add_argument("--out", type=Path, default=KIT_ROOT / "run_output", help="output folder (default: run_output)")
    parser.add_argument("--python", default=sys.executable, help="interpreter for the agent (default: this one)")
    parser.add_argument("--init-timeout", type=float, default=30.0, help="seconds for the agent to read `initialize`")
    parser.add_argument("--grace", type=float, default=30.0, help="seconds the agent may take to exit after `finish`")
    parser.add_argument("--inherit-env", action="store_true", help="pass your whole shell environment to the agent")
    parser.add_argument("--show-agent-stderr", action="store_true", help="print the agent's stderr here instead of agent.log")
    parser.add_argument("--quiet", action="store_true", help="print only the final JSON summary")
    parser.add_argument("--allow-truth-refs", action="store_true",
                        help="run even if the agent folder contains the card or names a truth/ file "
                             "(the score will not carry over to the platform)")
    args = parser.parse_args(argv)

    card_dir = args.card.resolve()
    if not scenario_path(card_dir).is_file():
        raise SystemExit(f"{card_dir} is not a card folder (missing config/v4_scenario.json)")
    if args.wallclock <= 0:
        raise SystemExit("--wallclock must be positive")
    card = load_card(card_dir)
    args.wallclock = V4Workflow(card_dir).wallclock_budget(args.wallclock)  # min(this, card limit, 900)
    entry = find_entry(args.agent)
    agent_dir = entry.parent
    problems = truth_problems(agent_dir, card_dir)
    if problems and not args.allow_truth_refs:
        raise SystemExit("local_runner: refusing to run, the agent could read the card's hidden truth/ files:\n  - "
                         + "\n  - ".join(problems) + "\nOn the platform your agent only has its own folder and no "
                         "truth. Remove these references (or pass --allow-truth-refs to run anyway).")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    scratch = out / "scratch"
    scratch.mkdir(exist_ok=True)
    env, dotenv_keys = agent_environment(agent_dir, scratch, card, args.wallclock, args.inherit_env)
    say = (lambda text: None) if args.quiet else (lambda text: print(text, file=sys.stderr, flush=True))
    say(f"[local-runner] card={card['card_id']} agent={entry.name} wallclock={args.wallclock:g}s dotenv_keys={dotenv_keys}")
    say(f"[local-runner] note: {card_dir.name}/truth/ (hidden weather and events) is read by this runner only, to "
        "score. Your agent gets bulletins and forecasts during the run; on the platform it has only its own folder.")
    for problem in problems:
        say(f"[local-runner] WARNING (--allow-truth-refs): {problem}; this score will not carry over to the platform")

    started = time.monotonic()
    with (out / "agent.log").open("w", encoding="utf-8") as log:
        log.write(f"[local-runner] entry={entry.name} card={card['card_id']} dotenv_keys={dotenv_keys}\n")
        log.flush()
        agent = AgentProcess([args.python, "-B", str(entry)], cwd=agent_dir, env=env,
                             stderr=None if args.show_agent_stderr else log, initialization_seconds=args.init_timeout)
        try:
            result = run_card(card_dir, agent, out, wallclock_seconds=args.wallclock, grace_seconds=args.grace)
        finally:
            agent.close(force=True)
    report = result["score_report"]
    summary = {
        "card": card["card_id"],
        "termination_reason": result["termination_reason"],
        "total": report["total"],
        "sum_best_scores": report["components"]["sum_best_scores"],
        "required_penalty": report["components"]["required_penalty"],
        "uniformity_penalty": report["components"]["uniformity_penalty"],
        "report_settlement": report["components"]["report_settlement"],
        "observation_request_reward": report["components"]["observation_request_reward"],
        "targets_observed": report["counts"]["targets_observed"],
        "required_missing": report["counts"]["required_missing"],
        "observe_actions": report["counts"]["observe_actions"],
        "observation_requests_completed": report["counts"]["observation_requests_completed"],
        "decision_requests": result["decision_requests"],
        "termination_detail": result["termination_detail"],
        "wall_seconds": result["accounted_wallclock_seconds"],
        "runner_seconds": round(time.monotonic() - started, 3),
        "error": result["error"],
        "outputs": {name: str(out / name) for name in
                    ("decisions.csv", "observations.csv", "score_report.json", "workflow_result.json", "agent.log")},
    }
    if result["termination_reason"] == "agent_error" and not args.quiet:
        say(f"[local-runner] agent error: {result['error']}")
        for line in (out / "agent.log").read_text(encoding="utf-8", errors="replace").splitlines()[-15:]:
            say("    " + line)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 2 if result["termination_reason"] == "agent_error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
