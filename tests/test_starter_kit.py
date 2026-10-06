"""End-to-end checks for the participant starter kit (archive/starter_kit_v3/).

Runs the kit's own scripts as a participant would: baseline run on the public reference scenario, re-scoring,
scenario generation, packaging. Also guards that the vendored environment copies stay identical to challenge/.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "archive" / "starter_kit_v3"
PY = sys.executable
BASELINE_TOTAL = 12287.478365  # deterministic minimal agent, scenarios/dev-reference, survey_complete


def run(script: str, *args: str, cwd: Path = KIT, timeout: int = 240) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([PY, "-B", str(KIT / script), *args], cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)


def summary_of(proc: subprocess.CompletedProcess) -> dict:
    assert proc.returncode == 0, f"exit {proc.returncode}\nstdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-3000:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1] if proc.stdout.strip().startswith("{") is False else proc.stdout)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def baseline(tmp_path_factory) -> dict:
    out = tmp_path_factory.mktemp("baseline")
    proc = run("local_runner.py", "--scenario", "scenarios/dev-reference", "--agent", "agent/minimal_agent.py",
               "--wallclock", "240", "--out", str(out), "--quiet")
    summary = summary_of(proc)
    return {"out": out, "summary": summary}


def test_baseline_completes_survey(baseline):
    summary = baseline["summary"]
    out = baseline["out"]
    assert summary["termination_reason"] == "survey_complete"
    assert summary["total"] > 12000
    assert summary["total"] == pytest.approx(BASELINE_TOTAL, abs=1.0)
    assert summary["required_missing"] == 0
    assert summary["wall_seconds"] < 240
    for name in ("decisions.csv", "workflow_result.json", "score_report.json", "agent.log"):
        assert (out / name).is_file(), name
    report = json.loads((out / "score_report.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == "score-report-v3"
    assert report["score"]["total"] == summary["total"]
    assert report["termination_reason"] == "survey_complete"
    workflow = json.loads((out / "workflow_result.json").read_text(encoding="utf-8"))
    assert "initial_publication" not in workflow  # stripped like the platform does
    assert workflow["score_report"]["score"]["total"] == summary["total"]
    agent_log = (out / "agent.log").read_text(encoding="utf-8")
    assert "provider=deterministic" in agent_log
    # The kit agent handles the platform's finish message: one stderr summary, then a self-exit.
    assert "minimal-agent finished: termination_reason=survey_complete" in agent_log
    if (KIT / "challenge" / "replay.py").exists():
        html = out / "decision_replay.html"
        assert html.is_file() and html.stat().st_size > 10_000


def test_baseline_is_deterministic(baseline, tmp_path):
    proc = run("local_runner.py", "--wallclock", "240", "--out", str(tmp_path), "--quiet", "--no-replay")
    second = summary_of(proc)
    assert second["total"] == baseline["summary"]["total"]
    assert sha256(tmp_path / "decisions.csv") == sha256(baseline["out"] / "decisions.csv")


def test_score_decisions_reproduces_runner_report(baseline, tmp_path):
    report_path = tmp_path / "report.json"
    proc = run("score_decisions.py", "--scenario", "scenarios/dev-reference", "--decisions", str(baseline["out"] / "decisions.csv"),
               "--out", str(report_path))
    assert proc.returncode == 0, proc.stderr
    printed = json.loads(proc.stdout)
    assert printed["termination_reason"] == "survey_complete"  # picked up from workflow_result.json
    rescored = json.loads(report_path.read_text(encoding="utf-8"))
    original = json.loads((baseline["out"] / "score_report.json").read_text(encoding="utf-8"))
    assert rescored["score"] == original["score"]
    assert rescored["completion"] == original["completion"]
    assert rescored["input_sha256"]["decisions"] == sha256(baseline["out"] / "decisions.csv")


def test_score_decisions_rejects_hidden_truth(baseline, tmp_path):
    hidden = tmp_path / "hidden"
    shutil.copytree(KIT / "scenarios" / "dev-reference", hidden)
    (hidden / "outputs" / "reference" / "weather_events.csv").unlink()
    proc = run("score_decisions.py", "--scenario", str(hidden), "--decisions", str(baseline["out"] / "decisions.csv"))
    assert proc.returncode != 0
    assert "weather_events.csv" in proc.stderr


def test_make_scenario_produces_valid_scenario(tmp_path):
    scenario = tmp_path / "mine"
    proc = run("make_scenario.py", "--out", str(scenario), "--seed", "7", "--days", "7")
    assert proc.returncode == 0, proc.stderr
    info = json.loads(proc.stdout)
    assert info["n_nights"] == 7 and info["n_tiles"] > 0 and info["n_slots"] > 0
    assert info["contract"] == "challenge-score-v3"
    for rel in ("config/scenario_config.json", "config/score_config.json", "outputs/reference/weather.csv",
                "outputs/reference/weather_events.csv", "outputs/reference/scenario_manifest.json"):
        assert (scenario / rel).is_file(), rel
    check = run("make_scenario.py", "--out", str(scenario), "--validate-only")
    assert check.returncode == 0, check.stderr
    assert json.loads(check.stdout)["scenario_id"] == info["scenario_id"]
    # refuses to overwrite, and the scenario is runnable end to end
    assert run("make_scenario.py", "--out", str(scenario), "--seed", "8").returncode != 0
    result = summary_of(run("local_runner.py", "--scenario", str(scenario), "--wallclock", "60", "--out", str(tmp_path / "run"),
                            "--quiet", "--no-replay"))
    assert result["termination_reason"] == "survey_complete"
    assert result["committed_actions"] > 0


def test_local_runner_finish_message_eof_and_grace(tmp_path):
    """The runner mirrors the platform: one finish line, stdin EOF, then a grace period.
    An agent that ignores the message but exits on EOF ends quickly; the run and score stand."""
    agent = tmp_path / "agent.py"
    agent.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        "    t = m.get('message_type')\n"
        "    if t == 'decision_request':\n"
        "        print(json.dumps({'protocol_version': m['protocol_version'], 'message_type': 'decision_response',\n"
        "                          'decision_sequence': m['decision_sequence'], 'action': 'wait',\n"
        "                          'reason': 'finish test'}), flush=True)\n"
        "    elif t == 'finish':\n"
        "        print('saw finish: ' + m['payload']['termination_reason'], file=sys.stderr, flush=True)\n"
        "print('stdin closed', file=sys.stderr, flush=True)\n"
    )
    started = time.monotonic()
    proc = run("local_runner.py", "--scenario", "scenarios/demo-week", "--agent", str(agent),
               "--out", str(tmp_path / "out"), "--quiet", "--no-replay")
    assert time.monotonic() - started < 120  # no 30 s grace wait: the agent exited on EOF
    summary = summary_of(proc)
    assert summary["termination_reason"] == "survey_complete"
    agent_log = (tmp_path / "out" / "agent.log").read_text(encoding="utf-8")
    assert "saw finish: survey_complete" in agent_log and "stdin closed" in agent_log


def test_pack_agent_builds_zip_with_entry_at_root(tmp_path):
    agent = tmp_path / "agent"
    shutil.copytree(KIT / "agent", agent)
    (agent / "__pycache__").mkdir()
    (agent / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\x00")
    (agent / ".env").write_text("MODEL_PROVIDER=deterministic\n", encoding="utf-8")
    (agent / "helpers.py").write_text("VALUE = 1\n", encoding="utf-8")
    out = tmp_path / "my-agent.zip"
    proc = run("pack_agent.py", "--agent", str(agent), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    names = zipfile.ZipFile(out).namelist()
    assert "minimal_agent.py" in names and "decision_graph.py" in names and "scoring_preview.py" in names
    assert "helpers.py" in names and "requirements.txt" in names and "observer.project.json" in names
    assert ".env" not in names, ".env stays out by default: the platform rejects ZIPs with credentials"
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
    assert all("/" not in name for name in names)  # flat: entry and manifest at the zip root
    with_env = tmp_path / "local-only.zip"
    proc = run("pack_agent.py", "--agent", str(agent), "--out", str(with_env), "--include-env")
    assert proc.returncode == 0 and "WARNING" in proc.stderr
    assert ".env" in zipfile.ZipFile(with_env).namelist()
    legacy = tmp_path / "legacy.zip"
    assert run("pack_agent.py", "--agent", str(agent), "--out", str(legacy), "--no-env").returncode == 0
    assert ".env" not in zipfile.ZipFile(legacy).namelist()


def test_packed_kit_is_a_complete_project_the_platform_runs_without_a_model_key(tmp_path):
    """The default kit ZIP carries its own manifest, so preparation never needs the model adapter, and the
    agent runs deterministically even though the platform injects its model-proxy variables."""
    sys.path.insert(0, str(ROOT))
    from challenge.challenge_workflow import ChallengeWorkflow  # noqa: PLC0415
    from project_platform.local import NativeWorkspace  # noqa: PLC0415
    from project_platform.package import extract_project, read_manifest, read_project_zip  # noqa: PLC0415

    out = tmp_path / "my-agent.zip"
    proc = run("pack_agent.py", "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    files = read_project_zip(out.read_bytes())
    manifest = read_manifest(files)
    assert manifest is not None, "observer.project.json must be at the ZIP root"
    assert manifest.image == "python:3.12-slim" and manifest.run == ("python3", "-u", "minimal_agent.py")
    assert manifest.build == () and dict(manifest.environment)["MODEL_PROVIDER"] == "deterministic"
    project = tmp_path / "project"
    extract_project(files, project)
    # Same argv as the container, run natively with this interpreter (Docker is not needed here).
    from dataclasses import replace  # noqa: PLC0415
    runtime = NativeWorkspace(project, replace(manifest, run=(PY, *manifest.run[1:])))
    runtime.build()
    transport = runtime.start({"OPENAI_BASE_URL": "https://platform.invalid/functions/v1/observer-model/v1",
                               "OPENAI_API_KEY": "obs_not-a-real-credential"})
    try:
        result = ChallengeWorkflow(root=KIT / "scenarios" / "demo-week").run(transport, wallclock_seconds=240)
    finally:
        runtime.close()
    assert result["termination_reason"] == "survey_complete"
    assert result["committed_action_count"] > 0
    assert "provider=deterministic" in transport.log


def test_pack_agent_generates_a_manifest_and_rejects_an_invalid_one(tmp_path):
    agent = tmp_path / "agent"
    shutil.copytree(KIT / "agent", agent)
    (agent / "observer.project.json").unlink()
    out = tmp_path / "generated.zip"
    proc = run("pack_agent.py", "--agent", str(agent), "--out", str(out))
    assert proc.returncode == 0 and "(generated)" in proc.stdout
    manifest = json.loads(zipfile.ZipFile(out).read("observer.project.json"))
    assert manifest["run"] == ["python3", "-u", "minimal_agent.py"]
    (agent / "observer.project.json").write_text(json.dumps({**manifest, "environment": {"MY_API_KEY": "x"}}), encoding="utf-8")
    proc = run("pack_agent.py", "--agent", str(agent), "--out", str(tmp_path / "bad.zip"))
    assert proc.returncode != 0 and "MY_API_KEY" in proc.stderr
    assert not (tmp_path / "bad.zip").exists()


def test_pack_agent_rejects_uninstallable_requirements(tmp_path):
    agent = tmp_path / "agent"
    shutil.copytree(KIT / "agent", agent)
    (agent / "requirements.txt").write_text("langchain>=1.0\n-e ../my-local-package\n", encoding="utf-8")
    proc = run("pack_agent.py", "--agent", str(agent), "--out", str(tmp_path / "bad.zip"))
    assert proc.returncode != 0
    assert "requirements.txt" in proc.stderr and "line 2" in proc.stderr
    assert not (tmp_path / "bad.zip").exists()
    (agent / "requirements.txt").write_text("# pinned\nlangchain-openai>=1.0,<2\npython-dotenv[cli]==1.0.1 ; python_version >= '3.10'\n", encoding="utf-8")
    assert run("pack_agent.py", "--agent", str(agent), "--out", str(tmp_path / "ok.zip")).returncode == 0


def test_kit_environment_matches_vendored_modules():
    """The kit ships copies of challenge/; they must not drift (project_paths.py is intentionally adapted)."""
    kit_modules = sorted(p.name for p in (KIT / "challenge").glob("*.py"))
    assert "scoring_core.py" in kit_modules and "challenge_workflow.py" in kit_modules and "scenario_builder.py" in kit_modules
    for name in kit_modules:
        if name == "project_paths.py":
            continue
        assert (ROOT / "challenge" / name).read_bytes() == (KIT / "challenge" / name).read_bytes(), f"archive/starter_kit_v3/challenge/{name} differs from challenge/{name}"
    for template in (ROOT / "challenge" / "templates").glob("*.html"):
        assert (KIT / "challenge" / "templates" / template.name).read_bytes() == template.read_bytes()
    for path in (ROOT / "challenge" / "participant_agent").iterdir():
        if not path.is_file():
            continue
        kit_bytes = (KIT / "agent" / path.name).read_bytes()
        if path.name == "README_ZH.md":
            # the kit README carries a short preface about the kit layout, followed by the upstream text verbatim
            assert path.read_bytes() in kit_bytes, "archive/starter_kit_v3/agent/README_ZH.md no longer embeds the upstream README"
            continue
        assert kit_bytes == path.read_bytes(), f"archive/starter_kit_v3/agent/{path.name} differs"
    assert (KIT / "agent" / "scoring_preview.py").read_bytes() == (ROOT / "challenge" / "scoring_preview.py").read_bytes()
    # challenge/reference is the finals-generation template (anomaly mechanics on); the kit's
    # dev-reference stays frozen on the pre-anomaly scenario the platform's practice phase stores.
    # The pinned digest guards against an accidental regeneration of the shipped copy.
    frozen = hashlib.sha256((KIT / "scenarios" / "dev-reference" / "outputs/reference/scenario_manifest.json").read_bytes()).hexdigest()
    assert frozen == "62db767360818a750ca9154ade11b1baf9b2aa3e2cd03c6408c7533a089535e8", "archive/starter_kit_v3/scenarios/dev-reference must stay the frozen pre-anomaly scenario"
    kit_score_config = json.loads((KIT / "scenarios" / "dev-reference" / "config/score_config.json").read_text(encoding="utf-8"))
    assert not any(key in kit_score_config for key in ("repeat_observation", "reporting", "anomaly_tags", "fault_response"))


def test_finals_preview_runs_the_anomaly_mechanics(tmp_path):
    """The rehearsal scenario exercises the finals rules end to end: the unmodified kit
    finds and reports the instrument fault, and report rows ride inside decisions.csv."""
    out = tmp_path / "finals_preview_output"
    proc = run("local_runner.py", "--scenario", "scenarios/finals-preview", "--agent", "agent/minimal_agent.py",
               "--wallclock", "240", "--out", str(out), "--quiet")
    summary = summary_of(proc)
    assert summary["termination_reason"] == "survey_complete"
    assert summary["total"] == pytest.approx(8214.257133, abs=1.0)
    report = json.loads((out / "score_report.json").read_text(encoding="utf-8"))
    assert report["reports"]["fault_correct_reports"] == 1
    assert report["reports"]["fault_misreports"] == 0
    decisions = (out / "decisions.csv").read_text(encoding="utf-8")
    assert "report_instrument_failure" in decisions
    assert (KIT / "scenarios" / "finals-preview" / "outputs" / "reference" / "tile_anomalies.csv").is_file()


def test_demo_week_runs_and_renders_a_replay(tmp_path):
    """The seven-night demo: same pipeline and scorer as the reference run, short enough to open and read."""
    out = tmp_path / "demo_week_output"
    proc = run("local_runner.py", "--scenario", "scenarios/demo-week", "--agent", "agent/minimal_agent.py",
               "--wallclock", "900", "--out", str(out), "--quiet")
    summary = summary_of(proc)
    assert summary["termination_reason"] == "survey_complete"
    assert summary["wall_seconds"] < 60  # ~2 s in practice; the point of the demo is that it is quick
    report = json.loads((out / "score_report.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == "score-report-v3"
    assert report["score"]["total"] == summary["total"]
    nights = (KIT / "scenarios" / "demo-week" / "outputs" / "reference" / "night_calendar.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(nights) - 1 == 7, "the demo scenario is the one-week one"
    html = out / "decision_replay.html"
    assert html.is_file() and html.stat().st_size > 10_000, "participants get the replay visualization from a demo run"


def test_demo_week_matches_the_scenario_the_platform_seeds(tmp_path):
    """The kit demo-week and the platform's stored copy are one frozen pre-anomaly scenario.

    The generation template moved to the anomaly mechanics, so regenerating demo-week
    from the template no longer reproduces the frozen bytes (and the platform never
    re-seeds its stored scenarios). Two guards replace the old regeneration equality:
    the shipped copy is pinned by digest, and the generator stays deterministic."""
    frozen = hashlib.sha256((KIT / "scenarios" / "demo-week" / "outputs/reference/scenario_manifest.json").read_bytes()).hexdigest()
    assert frozen == "4e3aa9b965186159e75fec76540da2cbc6858f8ad8181f8518ff5222d8fb605e", "archive/starter_kit_v3/scenarios/demo-week must stay the frozen pre-anomaly scenario"
    assert not (KIT / "scenarios" / "demo-week" / "outputs/reference/tile_anomalies.csv").exists()

    sys.path.insert(0, str(ROOT))
    from challenge import scenario_builder  # noqa: PLC0415 - import here to keep the kit tests standalone

    once = tmp_path / "gen-a"
    twice = tmp_path / "gen-b"
    for target in (once, twice):
        scenario_builder.generate_scenario(target, scenario_id="demo-week", seed=20261005, days=7,
                                           start_date="2026-10-05", global_wallclock_seconds=900)
    files_a = sorted(p.relative_to(once) for p in once.rglob("*") if p.is_file())
    assert files_a == sorted(p.relative_to(twice) for p in twice.rglob("*") if p.is_file())
    for rel in files_a:
        assert (once / rel).read_bytes() == (twice / rel).read_bytes(), rel
    assert (once / "outputs/reference/tile_anomalies.csv").is_file(), "the template now ships the anomaly mechanics"


def test_kit_docs_and_layout():
    for name in ("README.md", "SKILL.md", "local_runner.py", "score_decisions.py", "make_scenario.py", "pack_agent.py", "sac_submit.py",
                 "run_demo_week.sh", "run_demo_week.command", "run_demo_week.bat"):
        assert (KIT / name).is_file(), name
    skill = (KIT / "SKILL.md").read_text(encoding="utf-8")
    for placeholder in ("{{BASE_URL}}", "{{SUPABASE_URL}}", "{{SUPABASE_ANON_KEY}}"):
        assert placeholder in skill
    readme = (KIT / "README.md").read_text(encoding="utf-8")
    assert "agent/README_ZH.md" in readme and "participant-agent-protocol-v2" in readme
    assert not (KIT / "agent" / ".env").exists(), "never ship a real .env in the kit"
    assert (KIT / "agent" / ".env.example").is_file()
    assert not (KIT / "scenarios" / "dev-reference" / "outputs" / "reference" / "score_report.json").exists()
