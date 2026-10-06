#!/usr/bin/env python3
"""Rebuild web/src/content/demo/replay.json — the run the homepage console plays.

The run comes from the real participant-agent-protocol-v4 pipeline: a baseline example
agent against the public local practice card L1 (fully public, offline-scorable), run
through the same `challenge.v4_workflow.V4Workflow` the platform itself runs. This script
does not ship the card or the runner — both live outside this repo, in the organizer-only
`examples/local-cards/` tooling — so it takes their location on disk and fails loudly if
it cannot find them, rather than silently falling back to committed fixtures.

    python3 web/scripts/make_demo_replay.py
    python3 web/scripts/make_demo_replay.py --cosmos-root /path/to/cosmos --nights 7

It runs the example agent with no real API key (an unreachable OPENAI_BASE_URL), so the
agent's LLM-advised planning step always falls back to its rule-based path -- the run is
real, deterministic, and needs no secrets. The first `--nights` nights of that run become
the demo: short enough to keep the web bundle small, long enough that every night is doing
something. Standard library only.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT_DEFAULT = REPO / "web" / "src" / "content" / "demo" / "replay.json"
DEFAULT_COSMOS_ROOT = Path.home() / "projects" / "cosmos"
DEFAULT_NIGHTS = 7


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def run_agent(cosmos_root: Path, run_dir: Path) -> None:
    runner = cosmos_root / "examples" / "local-cards" / "runner" / "run_local.py"
    agent_dir = cosmos_root / "examples" / "python-agent-v4"
    if not runner.exists() or not agent_dir.exists():
        raise SystemExit(
            f"cannot find the organizer-only runner/example agent under {cosmos_root} "
            "-- pass --run-dir with a previously captured run_local.py output instead."
        )
    with tempfile.TemporaryDirectory() as env_tmp:
        env_file = Path(env_tmp) / ".env"
        env_file.write_text("OPENAI_API_KEY=unused-no-key\nOPENAI_BASE_URL=http://127.0.0.1:9\n", encoding="utf-8")
        # run_local.py only reads OPENAI_* from a .env file in the agent's cwd, never from
        # the parent shell -- copy ours in next to the agent for the duration of the run.
        agent_env_file = agent_dir / ".env"
        had_env = agent_env_file.exists()
        previous = agent_env_file.read_bytes() if had_env else None
        agent_env_file.write_bytes(env_file.read_bytes())
        try:
            proc = subprocess.run(
                [sys.executable, str(runner), "--card", "L1", "--agent", "python3 agent.py",
                 "--agent-cwd", str(agent_dir), "--out", str(run_dir)],
                cwd=str(runner.parent), capture_output=True, text=True,
            )
        finally:
            if had_env:
                agent_env_file.write_bytes(previous or b"")
            else:
                agent_env_file.unlink(missing_ok=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit("run_local.py failed")


def circular_mean_ra(ras: list[float]) -> float:
    """Mean right ascension, safe across the 0/360 seam -- a pointing's dozen-odd targets
    sit within a ~2.5 deg field, so an arithmetic mean only misbehaves right at the seam."""
    import math
    s = sum(math.sin(math.radians(r)) for r in ras)
    c = sum(math.cos(math.radians(r)) for r in ras)
    return math.degrees(math.atan2(s, c)) % 360


def build(cosmos_root: Path, run_dir: Path, nights: int) -> dict:
    card = cosmos_root / "examples" / "local-cards" / "L1"
    site_cfg = json.loads((card / "config" / "v4_scenario.json").read_text(encoding="utf-8"))["site"]
    targets_by_id = {t["target_id"]: t for t in rows(card / "public" / "targets.csv")}
    weather_rows = rows(card / "truth" / "v4_weather_truth.csv")

    night_order: list[str] = []
    for w in weather_rows:
        if w["night_id"] not in night_order:
            night_order.append(w["night_id"])
    first_nights = set(night_order[:nights])
    slots = sorted(weather_rows, key=lambda w: w["timestamp_utc"])
    window_slots = [w for w in slots if w["night_id"] in first_nights]
    if not window_slots:
        raise SystemExit("no weather slots found for the requested night window")
    window_start = window_slots[0]["timestamp_utc"]

    def to_bool(v: str) -> bool:
        return v.strip().lower() in ("true", "1", "yes")

    def num(v: str) -> float:
        return round(float(v), 6) if v.strip() else 0.0

    weather = [{
        "slot": w["slot_id"], "night": w["night_id"], "t": w["timestamp_utc"],
        "open": to_bool(w["is_observable"]), "seeing": num(w["seeing_arcsec"]),
        "transp": num(w["transparency"]), "sky": num(w["sky_quality"]), "eff": num(w["instrument_efficiency"]),
    } for w in window_slots]

    # Which 15-minute slot a decision falls in, for the console's night/gap narration --
    # decisions.csv carries only start/end timestamps, not a slot id.
    slot_starts = [s["timestamp_utc"] for s in slots]

    def slot_for(t: str) -> dict:
        lo, hi = 0, len(slot_starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if slot_starts[mid] <= t:
                lo = mid
            else:
                hi = mid - 1
        return slots[lo]

    decisions = rows(run_dir / "decisions.csv")
    observations = rows(run_dir / "observations.csv")
    targets_for_index: dict[str, list[str]] = {}
    for o in observations:
        if o["valid"].strip().lower() == "true":
            targets_for_index.setdefault(o["observe_index"], []).append(o["target_id"])

    used_target_ids: set[str] = set()
    actions = []
    for d in decisions:
        if d["start_utc"] < window_start:
            continue
        slot = slot_for(d["start_utc"])
        if slot["night_id"] not in first_nights:
            continue
        if d["action"] == "wait":
            actions.append({
                "i": d["decision_id"], "slot": slot["slot_id"], "a": "wait", "program": "",
                "targets": [], "center": None, "t": d["start_utc"], "dt": int(d["duration_seconds"]),
                "score": 0.0, "penalty": 0.0, "cls": "wait",
            })
            continue
        hit_ids = targets_for_index.get(d["observe_index"], [])
        used_target_ids.update(hit_ids)
        pts = [targets_by_id[tid] for tid in hit_ids if tid in targets_by_id]
        center = None
        if pts:
            center = {
                "ra": round(circular_mean_ra([float(p["ra_deg"]) for p in pts]), 3),
                "dec": round(sum(float(p["dec_deg"]) for p in pts) / len(pts), 3),
            }
        score = sum(float(o["score"]) for o in observations
                    if o["observe_index"] == d["observe_index"] and o["valid"].strip().lower() == "true")
        cls = "completed" if d["valid"].strip().lower() == "true" and hit_ids else "interrupted"
        actions.append({
            "i": d["decision_id"], "slot": slot["slot_id"], "a": "observe", "program": d["program"],
            "targets": sorted(hit_ids), "center": center, "t": d["start_utc"], "dt": int(d["duration_seconds"]),
            "score": round(score, 4), "penalty": 0.0, "cls": cls,
        })

    targets = [{
        "id": tid, "ra": round(float(targets_by_id[tid]["ra_deg"]), 2), "dec": round(float(targets_by_id[tid]["dec_deg"]), 2),
        "required": to_bool(targets_by_id[tid]["required"]),
    } for tid in sorted(used_target_ids) if tid in targets_by_id]

    total_score = round(sum(a["score"] - a["penalty"] for a in actions), 4)
    return {
        "site": {"lat": site_cfg["latitude_deg"], "lon": site_cfg["longitude_deg"], "min_alt": 30.0},
        "targets": targets,
        "weather": weather,
        "actions": actions,
        "score": {"total": total_score, "base_science": total_score},
        "completed": len(used_target_ids),
        "required_missing": [],
        "nights": len(first_nights),
    }


def summarise(data: dict) -> str:
    per: dict[str, int] = {}
    for a in data["actions"]:
        per[a["slot"].split("-")[0]] = per.get(a["slot"].split("-")[0], 0) + (a["a"] == "observe")
    return (f"{data['nights']} nights, {len(data['targets'])} targets, {len(data['actions'])} actions, "
            f"{data['completed']} completed, score {data['score']['total']:.0f}\nobserves per night: {list(per.values())}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=OUT_DEFAULT)
    p.add_argument("--cosmos-root", type=Path, default=DEFAULT_COSMOS_ROOT, help="parent of examples/local-cards")
    p.add_argument("--run-dir", type=Path, default=None, help="reuse a previously captured run_local.py --out directory")
    p.add_argument("--nights", type=int, default=DEFAULT_NIGHTS)
    opts = p.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        run_dir = opts.run_dir
        if run_dir is None:
            run_dir = Path(tmp) / "run"
            run_agent(opts.cosmos_root, run_dir)
        data = build(opts.cosmos_root, run_dir, opts.nights)

    opts.out.parent.mkdir(parents=True, exist_ok=True)
    opts.out.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {opts.out.relative_to(REPO)} ({opts.out.stat().st_size / 1024:.0f} KiB)")
    print(summarise(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
