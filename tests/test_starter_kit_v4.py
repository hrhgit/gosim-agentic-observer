"""End-to-end checks for the v4 starter kit (archive/starter_kit_v4/).

Runs the kit's scripts as a participant would: the baseline and the idle agent on the public demo card
through local_runner.py (JSON-Lines subprocess, participant-agent-protocol-v4), protocol error handling,
the wall clock, the finish message, the LLM hook fallback, and pack_agent.py output checked by the
platform's own ZIP and manifest readers.
"""
from __future__ import annotations

import hashlib
import http.server
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "archive" / "starter_kit_v4"
DEMO = KIT / "cards" / "demo"
PY = sys.executable
IDLE_TOTAL = -6200.0  # 120 required targets x 50 missing + the full uniformity penalty (200)
BASELINE_TOTAL = 1082.572141


def kit_module(name: str):
    """Import starter_kit_v4/challenge/<name> under its own package name (the platform's challenge/ package
    is already on sys.path under the name `challenge`)."""
    package = "starter_kit_v4_challenge"
    if package not in sys.modules:
        spec = importlib.util.spec_from_file_location(package, KIT / "challenge" / "__init__.py",
                                                      submodule_search_locations=[str(KIT / "challenge")])
        module = importlib.util.module_from_spec(spec)
        sys.modules[package] = module
        spec.loader.exec_module(module)
    return importlib.import_module(f"{package}.{name}")


def run(script: str, *args: str, env: dict | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    full_env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})}
    return subprocess.run([PY, "-B", str(KIT / script), *args], cwd=str(KIT), env=full_env,
                          capture_output=True, text=True, timeout=timeout)


def summary_of(proc: subprocess.CompletedProcess, code: int = 0) -> dict:
    assert proc.returncode == code, f"exit {proc.returncode}\nstdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-3000:]}"
    return json.loads(proc.stdout)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_agent(path: Path, body: str) -> Path:
    """A tiny scripted agent: `body` runs for each decision_request with `m` (message) and `p` (payload)
    in scope and must set `action` (a dict). Finish messages and EOF are logged to stderr."""
    path.write_text(
        "import json, sys, time\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        "    t = m.get('message_type')\n"
        "    if t == 'decision_request':\n"
        "        p = m['payload']\n"
        + "".join("        " + row + "\n" for row in body.strip().splitlines()) +
        "        print(json.dumps({'protocol_version': m['protocol_version'], 'message_type': 'decision_response',\n"
        "                          'decision_sequence': m['decision_sequence'], **action}), flush=True)\n"
        "    elif t == 'finish':\n"
        "        print('saw finish ' + json.dumps(m['payload'], sort_keys=True), file=sys.stderr, flush=True)\n"
        "print('stdin closed', file=sys.stderr, flush=True)\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def baseline(tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("baseline")
    summary = summary_of(run("local_runner.py", "--out", str(out), "--quiet"))
    return {"out": out, "summary": summary}


@pytest.fixture(scope="module")
def idle(tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("idle")
    summary = summary_of(run("local_runner.py", "--agent", "examples/idle_agent.py", "--out", str(out), "--quiet"))
    return {"out": out, "summary": summary}


def test_baseline_beats_doing_nothing_on_the_demo_card(baseline, idle):
    base, nothing = baseline["summary"], idle["summary"]
    assert nothing["termination_reason"] == "agent_finished"
    assert nothing["total"] == pytest.approx(IDLE_TOTAL)
    assert nothing["required_missing"] == 120 and nothing["targets_observed"] == 0
    assert base["termination_reason"] == "survey_complete"
    assert base["error"] is None
    assert base["total"] == pytest.approx(BASELINE_TOTAL)
    assert base["total"] > 800 and base["total"] > IDLE_TOTAL + 5000, base
    assert base["required_missing"] == 1
    assert base["observation_request_reward"] == 100.0
    assert base["observation_requests_completed"] == 1
    assert base["targets_observed"] > 600
    assert base["wall_seconds"] < 120
    out = baseline["out"]
    for name in ("decisions.csv", "observations.csv", "messages.jsonl", "score_report.json", "workflow_result.json", "agent.log"):
        assert (out / name).is_file(), name
    # Regression guard for the old failure mode: the planner kept re-observing the same hopeless
    # required targets (100+ times) while other required targets were never observed.
    import csv  # noqa: PLC0415
    from collections import Counter  # noqa: PLC0415
    with (out / "observations.csv").open(encoding="utf-8") as handle:
        repeats = Counter(row["target_id"] for row in csv.DictReader(handle))
    assert max(repeats.values()) <= 10, repeats.most_common(3)
    report = json.loads((out / "score_report.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == "v4-score-report-v1" and report["total"] == base["total"]
    workflow = json.loads((out / "workflow_result.json").read_text(encoding="utf-8"))
    assert workflow["termination_reason"] == "survey_complete" and workflow["score_report"]["total"] == base["total"]
    log = (out / "agent.log").read_text(encoding="utf-8")
    assert "llm=off" in log  # the kit manifest keeps the model hook off by default
    assert "baseline finished: termination_reason=survey_complete" in log  # it handled the finish message


def test_baseline_is_deterministic(baseline, tmp_path):
    second = summary_of(run("local_runner.py", "--out", str(tmp_path), "--quiet"))
    assert second["total"] == baseline["summary"]["total"]
    assert sha256(tmp_path / "decisions.csv") == sha256(baseline["out"] / "decisions.csv")


def test_invalid_response_ends_as_agent_error_and_the_score_still_settles(tmp_path):
    agent = write_agent(tmp_path / "agent.py", """
if m['decision_sequence'] == 1:
    action = {'action': 'wait', 'duration_seconds': 600}
else:
    action = {'action': 'observe', 'pointing': {'alt_deg': 60, 'az_deg': 10}, 'assignments': {'0': 'NOT_A_TARGET'},
              'duration_seconds': 600, 'program': 'DARK'}
""")
    summary = summary_of(run("local_runner.py", "--agent", str(agent), "--out", str(tmp_path / "out"), "--quiet"), code=2)
    assert summary["termination_reason"] == "agent_error"
    assert summary["error"].startswith("decision 2: ") and "unknown target_id" in summary["error"]
    assert summary["total"] == pytest.approx(IDLE_TOTAL)
    assert '"termination_reason": "agent_error"' in (tmp_path / "out" / "agent.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("bad, message", [
    ({"action": "observe", "pointing": {"alt_deg": 60, "az_deg": 10}, "assignments": {}, "duration_seconds": 600, "note": "x"},
     "observe needs pointing"),
    ({"action": "observe", "pointing": {"alt_deg": 60, "az_deg": 10}, "assignments": {}, "duration_seconds": 30}, "duration_seconds"),
    ({"action": "observe", "pointing": {"alt_deg": 60, "az_deg": 10}, "assignments": {"5": "V4T000001", "05": "V4T000002"},
      "duration_seconds": 600}, "assigned twice"),
    ({"action": "observe", "pointing": {"alt_deg": 60, "az_deg": 10}, "assignments": {}, "duration_seconds": 600, "program": "GREY"},
     "program must be"),
    ({"action": "wait", "duration_seconds": 7200}, "wait duration_seconds"),
    ({"action": "wait", "until_utc": "2020-01-01T00:00:00Z"}, "later than now_utc"),
    ({"action": "wait", "until_utc": "2026-10-03T00:00:00"}, "ending in Z"),
    ({"action": "report", "why": "x"}, "takes no fields"),
    ({"action": "jump"}, "unknown action"),
])
def test_invalid_actions_are_rejected_like_on_the_platform(bad, message):
    """Actions are checked by the runner's normalize_action (the platform's validator)."""
    from datetime import datetime, timezone  # noqa: PLC0415

    runner = kit_module("v4_runner")
    scenario = runner.load_scenario(DEMO / "config" / "v4_scenario.json")
    targets = {t["target_id"]: t for t in scenario.targets}
    with pytest.raises(runner.InvalidAgentAction, match=message):
        runner.normalize_action(bad, scenario, targets, datetime(2026, 10, 2, 1, tzinfo=timezone.utc))
    ok = runner.normalize_action({"action": "observe", "pointing": {"alt_deg": 60, "az_deg": 10},
                                  "assignments": {"5": "V4T000001"}, "duration_seconds": 600}, scenario, targets,
                                 datetime(2026, 10, 2, 1, tzinfo=timezone.utc))
    assert ok["program"] == "BACKUP"  # program is optional


def test_envelope_checks():
    workflow = kit_module("v4_workflow")
    good = {"protocol_version": workflow.PROTOCOL_VERSION, "message_type": "decision_response", "decision_sequence": 3,
            "reason": "r", "decision_source": "rule", "action": "wait", "duration_seconds": 600}
    assert workflow.V4Workflow.action_from_response(good, 3) == {"action": "wait", "duration_seconds": 600}
    for bad in ({**good, "decision_sequence": 4}, {**good, "decision_sequence": 3.0},
                {**good, "protocol_version": "participant-agent-protocol-v2"}, {**good, "message_type": "decision"},
                {**good, "reason": 5}):
        with pytest.raises(workflow.ProtocolViolation):
            workflow.V4Workflow.action_from_response(bad, 3)


def test_wait_until_batches_messages_and_finish_message_is_sent(tmp_path):
    """until_utc skips the day without round trips; bulletins published meanwhile arrive in one batch."""
    agent = write_agent(tmp_path / "agent.py", """
kinds = [x.get('record_type') for x in p['new_messages']]
print('req', m['decision_sequence'], p['now_utc'], len(kinds), kinds.count('bulletin'), file=sys.stderr, flush=True)
assert p['schema_version'] == 'v4-decision-snapshot-v1' and 'remaining_seconds' in p['wallclock']
action = {'action': 'wait', 'until_utc': '2026-10-04T00:00:00Z'} if m['decision_sequence'] == 1 else {'action': 'finish'}
""")
    summary = summary_of(run("local_runner.py", "--agent", str(agent), "--out", str(tmp_path / "out"), "--quiet"))
    assert summary["termination_reason"] == "agent_finished" and summary["decision_requests"] == 2
    log = (tmp_path / "out" / "agent.log").read_text(encoding="utf-8")
    first, second = [line.split() for line in log.splitlines() if line.startswith("req ")]
    assert first[2] == "2026-10-02T00:00:00Z" and second[2] == "2026-10-04T00:00:00Z"
    assert int(second[4]) >= 60  # two nights of per-slot bulletins in one batch
    finish = json.loads(log.split("saw finish ", 1)[1].splitlines()[0])
    rows = (tmp_path / "out" / "decisions.csv").read_text(encoding="utf-8").strip().splitlines()[1:]
    assert len(rows) == 48 and all(",wait," in row for row in rows)  # 48 h expanded into 3600 s waits
    assert finish == {"decisions": 48, "observe_actions": 0, "schema_version": "v4-finish-v1",
                      "termination_reason": "agent_finished", "last_decision_sequence": 2, "grace_seconds": 30.0}
    assert "stdin closed" in log


def test_global_wallclock_expiry_settles_the_run(tmp_path):
    agent = write_agent(tmp_path / "agent.py", """
time.sleep(0.4)
action = {'action': 'wait', 'duration_seconds': 900}
""")
    summary = summary_of(run("local_runner.py", "--agent", str(agent), "--wallclock", "1.5",
                             "--out", str(tmp_path / "out"), "--quiet"))
    assert summary["termination_reason"] == "global_wallclock_expired"
    assert summary["wall_seconds"] <= 1.6 and summary["total"] == pytest.approx(IDLE_TOTAL)
    # Fair clock: the reply that overran the budget is ignored; the run still settles at the budget.


def test_runner_refuses_an_agent_that_names_the_truth_files(tmp_path):
    """truth/ feeds the local scorer only; an agent that reads it gets a score the platform never gives."""
    (tmp_path / "agent").mkdir()
    agent = write_agent(tmp_path / "agent" / "agent.py", """
open('../cards/demo/truth/v4_weather_truth.csv').close()
action = {'action': 'finish'}
""")
    refused = run("local_runner.py", "--agent", str(agent), "--out", str(tmp_path / "out"), "--quiet")
    assert refused.returncode != 0 and not refused.stdout
    assert "hidden truth/" in refused.stderr and "agent.py mentions truth/, v4_weather_truth" in refused.stderr
    assert not (tmp_path / "out").exists()
    # The override runs (the file does not exist from there) and says the score will not carry over.
    forced = run("local_runner.py", "--agent", str(agent), "--out", str(tmp_path / "out"), "--allow-truth-refs")
    assert forced.returncode == 2 and "WARNING (--allow-truth-refs)" in forced.stderr


def test_runner_refuses_a_card_inside_the_agent_folder(tmp_path):
    agent_dir = tmp_path / "agent"
    shutil.copytree(KIT / "agent", agent_dir)
    shutil.copytree(DEMO, agent_dir / "cards" / "demo")
    refused = run("local_runner.py", "--agent", str(agent_dir), "--card", str(agent_dir / "cards" / "demo"),
                  "--out", str(tmp_path / "out"), "--quiet")
    assert refused.returncode != 0 and "is inside the agent folder" in refused.stderr


def test_runner_explains_truth_before_a_normal_run(baseline):
    proc = run("local_runner.py", "--agent", "examples/idle_agent.py", "--out", str(baseline["out"] / "idle-note"))
    assert proc.returncode == 0
    assert "demo/truth/ (hidden weather and events) is read by this runner only" in proc.stderr
    assert "WARNING" not in proc.stderr


def test_initialize_carries_only_public_data():
    init = kit_module("v4_workflow").V4Workflow(DEMO).initialize_payload(900.0)
    assert init["schema_version"] == "v4-initialize-v1"
    assert set(init) == {"schema_version", "task_card", "site", "survey", "instrument", "scoring", "footprint", "targets", "limits"}
    assert init["site"]["name"] == "Paranal, Chile (virtual)"
    assert init["limits"]["global_wallclock_seconds"] == 900
    assert init["limits"]["max_consecutive_reports"] == 32
    assert init["scoring"]["reporting"]["false_report_free_allowance"] == 2
    assert init["scoring"]["reporting"]["max_consecutive_reports"] == 32
    assert len(init["targets"]["rows"]) == 2400 and len(init["survey"]["nights"]) == 7
    text = json.dumps(init)
    for hidden in ("is_observable", "seeing_arcsec", "transparency\"", "instrument_fault", "instrument_efficiency",
                   "seed", "rocket_launch", "terrain_obstruction", "stress"):
        assert hidden not in text, hidden


def test_demo_card_is_public_small_and_seedless():
    scenario = json.loads((DEMO / "config" / "v4_scenario.json").read_text(encoding="utf-8"))
    assert scenario["task_card"]["card_id"] == "demo"
    assert scenario["task_card"]["card_id"].lower() not in {"alpha", "beta", *"abcdefgh"}
    assert scenario["site"]["name"] == "Paranal, Chile (virtual)" and scenario["limits"]["global_wallclock_seconds"] == 900
    assert sorted(p.name for p in (DEMO / "public").iterdir()) == [
        "footprint.csv", "targets.csv", "v4_bulletins.jsonl", "v4_forecasts.jsonl", "v4_night_calendar.csv"]
    assert not (DEMO / "truth" / "v4_stress_events.csv").exists() and scenario["stress"] == {"enabled": False}
    for path in DEMO.rglob("*.json"):
        assert '"seed"' not in path.read_text(encoding="utf-8"), path
    assert not list(DEMO.rglob("*summary*"))
    assert sum(p.stat().st_size for p in DEMO.rglob("*") if p.is_file()) < 400_000


def test_kit_modules_match_the_platform_copies():
    """Shared helpers are byte copies of challenge/. The v4 simulator modules are compared once the
    platform vendors them (challenge/v4_runner.py); until then this part is skipped."""
    for name in ("__init__.py", "contracts.py", "observing_calendar.py", "project_paths.py", "tile_geometry_simulator.py"):
        assert (KIT / "challenge" / name).read_bytes() == (ROOT / "challenge" / name).read_bytes(), name
    for name in ("v4_fiber_map.py", "v4_scorer.py", "v4_runner.py", "v4_config_check.py", "v4_workflow.py"):
        platform = ROOT / "challenge" / name
        if platform.exists():
            assert (KIT / "challenge" / name).read_bytes() == platform.read_bytes(), f"starter_kit_v4/challenge/{name} differs"


def test_llm_hook_falls_back_without_a_reachable_model(baseline, tmp_path):
    agent = tmp_path / "agent"
    shutil.copytree(KIT / "agent", agent)
    (agent / ".env").write_text("USE_LLM=1\nOPENAI_BASE_URL=http://127.0.0.1:9/v1\nOPENAI_API_KEY=obs_not-a-real-credential\n", encoding="utf-8")
    summary = summary_of(run("local_runner.py", "--agent", str(agent), "--out", str(tmp_path / "out"), "--quiet"))
    assert summary["termination_reason"] == "survey_complete"
    assert summary["total"] == baseline["summary"]["total"]  # the rules decide when the model is unreachable
    log = (tmp_path / "out" / "agent.log").read_text(encoding="utf-8")
    assert "llm=on" in log and "llm: call failed" in log
    assert "obs_not-a-real-credential" not in log


def test_llm_hook_uses_an_openai_compatible_endpoint(tmp_path):
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, self.headers.get("Authorization"), body["model"]))
            reply = {"choices": [{"message": {"content": '{"avoid_directions": ["N"], "duration_scale": 1.1}'}}]}
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        agent = tmp_path / "agent"
        shutil.copytree(KIT / "agent", agent)
        (agent / ".env").write_text(f"USE_LLM=1\nOPENAI_BASE_URL=http://127.0.0.1:{server.server_address[1]}/v1\n"
                                    "OPENAI_API_KEY=obs_test-key\nNO_PROXY=127.0.0.1,localhost\n", encoding="utf-8")
        summary = summary_of(run("local_runner.py", "--agent", str(agent), "--out", str(tmp_path / "out"), "--quiet"))
    finally:
        server.shutdown()
    assert summary["termination_reason"] == "survey_complete" and summary["total"] > 0
    assert seen and seen[0] == ("/v1/chat/completions", "Bearer obs_test-key", "team-model")
    assert len(seen) <= 10  # one short call per night (plus rare report checks), never one per decision
    assert "llm night 2026-10-01: avoid ['N'] duration x1.10" in (tmp_path / "out" / "agent.log").read_text(encoding="utf-8")


def _pack(tmp_path: Path) -> Path:
    out = tmp_path / "my-agent.zip"
    proc = run("pack_agent.py", "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    return out


def test_pack_output_passes_the_platform_zip_and_manifest_checks(tmp_path):
    from project_platform.manifest import ProjectError, ProjectManifest  # noqa: PLC0415
    from project_platform.package import read_project_zip  # noqa: PLC0415

    files = read_project_zip(_pack(tmp_path).read_bytes())
    names = sorted(f.path for f in files)
    assert names == ["baseline_agent.py", "llm_hook.py", "observer.project.json", "planner.py", "skymath.py"]
    raw = json.loads(next(f.data for f in files if f.path == "observer.project.json"))
    assert raw["protocol"] == "jsonl-v4" and raw["run"] == ["python3", "-u", "baseline_agent.py"]
    assert raw["environment"]["USE_LLM"] == "0"
    # Every other field passes the platform's manifest parser as it is today.
    manifest = ProjectManifest.parse({**raw, "protocol": "jsonl-v2"})
    assert manifest.image == "python:3.12-slim" and manifest.build == ()
    try:
        ProjectManifest.parse(raw)
    except ProjectError as exc:
        pytest.skip(f"platform does not accept jsonl-v4 manifests yet (v4 protocol plumbing pending): {exc}")


def test_packed_project_runs_on_the_demo_card_with_platform_style_environment(tmp_path):
    from project_platform.package import extract_project, read_project_zip  # noqa: PLC0415

    transport = kit_module("local_transport")
    AgentProcess, run_card = transport.AgentProcess, transport.run_card

    files = read_project_zip(_pack(tmp_path).read_bytes())
    project = tmp_path / "project"
    extract_project(files, project)
    manifest = json.loads((project / "observer.project.json").read_text(encoding="utf-8"))
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), **manifest["environment"],
           "OPENAI_BASE_URL": "https://platform.invalid/functions/v1/observer-model/v1",
           "OPENAI_API_KEY": "obs_not-a-real-credential"}
    with (tmp_path / "agent.log").open("w", encoding="utf-8") as log:
        agent = AgentProcess([PY, *manifest["run"][1:]], cwd=project, env=env, stderr=log)
        result = run_card(DEMO, agent, tmp_path / "out", wallclock_seconds=300)
    assert result["termination_reason"] == "survey_complete"
    assert result["score_report"]["total"] > 0
    assert "llm=off" in (tmp_path / "agent.log").read_text(encoding="utf-8")


def test_pack_agent_rejects_a_v2_manifest_and_leaves_env_out(tmp_path):
    agent = tmp_path / "agent"
    shutil.copytree(KIT / "agent", agent)
    (agent / ".env").write_text("USE_LLM=1\n", encoding="utf-8")
    ok = tmp_path / "ok.zip"
    assert run("pack_agent.py", "--agent", str(agent), "--out", str(ok)).returncode == 0
    assert ".env" not in zipfile.ZipFile(ok).namelist()
    manifest = json.loads((agent / "observer.project.json").read_text(encoding="utf-8"))
    (agent / "observer.project.json").write_text(json.dumps({**manifest, "protocol": "jsonl-v2"}), encoding="utf-8")
    proc = run("pack_agent.py", "--agent", str(agent), "--out", str(tmp_path / "bad.zip"))
    assert proc.returncode != 0 and "jsonl-v4" in proc.stderr
    assert not (tmp_path / "bad.zip").exists()


def test_kit_docs_and_layout():
    for name in ("README.md", "SKILL.md", "local_runner.py", "pack_agent.py", "agent/baseline_agent.py",
                 "agent/llm_hook.py", "agent/observer.project.json", "examples/idle_agent.py"):
        assert (KIT / name).is_file(), name
    readme = (KIT / "README.md").read_text(encoding="utf-8")
    skill = (KIT / "SKILL.md").read_text(encoding="utf-8")
    for text in (readme, skill):
        assert "participant-agent-protocol-v4" in text and "until_utc" in text and "local_runner.py" in text
        assert "Paranal" in text


# --- planner: observation-request planning values --------------------------------------


def _planner(target_rows):
    agent_dir = str(KIT / "agent")
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)
    from planner import Planner  # noqa: PLC0415

    init = {
        "site": {"latitude_deg": -24.6157, "longitude_deg": -70.3976, "minimum_altitude_deg": 30.0},
        "survey": {
            "end_utc": "2026-10-03T09:00:00Z",
            "slot_seconds": 900,
            "nights": [{"night_id": "N20261001", "night_date": "2026-10-01",
                        "observing_start_utc": "2026-10-02T00:00:00Z",
                        "observing_end_utc": "2026-10-02T09:00:00Z", "slot_count": 36}],
        },
        "instrument": {"n_fibers": 16, "grid_side": 4, "glass_side_deg": 0.632456,
                       "pitch_deg": 0.632456, "fov_side_deg": 2.529822,
                       "exposure": {"min_duration_seconds": 60, "max_duration_seconds": 3600}},
        "scoring": {"flux_zero_point": 0.5, "exposure_zero_point_seconds": 900, "q0": 0.68,
                    "airmass_exponent": 0.6,
                    "program": {"bands": {"DARK": 0.65, "BRIGHT": 0.4},
                                "multipliers": {"DARK": 1.2, "BRIGHT": 1.12, "BACKUP": 1.06},
                                "mismatch_multiplier": 1.0},
                    "lunar_model": {"angular_decay_scale_deg": 35.0, "altitude_exponent": 1.0,
                                    "maximum_penalty": 0.75}},
        "targets": {"columns": ["target_id", "ra_deg", "dec_deg", "target_class", "feature_flux",
                                "science_weight", "required"],
                    "rows": target_rows},
    }
    return Planner(init)


def _request_message(request_id, target_ids, *, reward, threshold, remaining, completed=(),
                     minimum_completed=1):
    return {
        "request_id": request_id,
        "target_ids": target_ids,
        "minimum_completed": minimum_completed,
        "completion_factor_threshold": threshold,
        "completion_reward": reward,
        "completed_target_ids": list(completed),
        "remaining_count": remaining,
    }


def test_planner_ignores_a_request_that_is_already_fulfilled():
    planner = _planner([["V4T000001", 337.0, -5.0, "BGS", 1.0, 1.0, False]])
    open_request = _request_message("V4RQ0001", ["V4T000001"], reward=100.0, threshold=0.5, remaining=1)
    planner.on_requests([open_request])
    assert planner.request_bonus[0] == pytest.approx(150.0)  # 1.5 * reward / remaining
    fulfilled = _request_message("V4RQ0001", ["V4T000001"], reward=100.0, threshold=0.5,
                                 remaining=0, completed=["V4T000001"])
    planner.on_requests([fulfilled])
    assert planner.request_bonus == {} and planner.request_threshold == {}


def test_planner_sums_marginal_value_for_overlapping_requests():
    planner = _planner([["V4T000001", 337.0, -5.0, "BGS", 1.0, 1.0, False]])
    first = _request_message("V4RQ0001", ["V4T000001"], reward=100.0, threshold=0.5, remaining=1)
    second = _request_message("V4RQ0002", ["V4T000001"], reward=200.0, threshold=0.8, remaining=1)
    planner.on_requests([first, second])
    assert planner.request_bonus[0] == pytest.approx(150.0 + 300.0)  # marginal rewards add up
    assert planner.request_threshold[0] == pytest.approx(0.8)  # collectible at the higher threshold
