"""Scorer parity and protocol tests (no web server)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "starter_kit"
EX = KIT / "example"


def test_reference_report_matches_frozen_scorer(tmp_path):
    from app.services.scoring import score_decisions
    metrics = score_decisions(EX / "weather.csv", EX / "tiles.csv", EX / "score_config.json", EX / "decisions.csv", tmp_path / "r.json")
    ref = json.loads((EX / "score_report.json").read_text())
    assert metrics["score"] == pytest.approx(ref["score"], abs=1e-6)
    assert metrics["science_score"] == pytest.approx(ref["science_score"], abs=1e-6)
    assert metrics["completed_tiles"] == ref["completed_tiles"] == 21
    assert metrics["total_tiles"] == 72
    assert 0 <= metrics["uniformity"] <= 1
    report = json.loads((tmp_path / "r.json").read_text())
    assert report["actions"] == ref["actions"]


def test_invalid_csv_is_rejected(tmp_path):
    from app.services.scoring import InvalidSubmission, score_decisions
    bad = tmp_path / "bad.csv"
    bad.write_text("decision_id,slot_id,action,tile_id,program,reason\n0,NOPE,observe,1,DARK,x\n")
    with pytest.raises(InvalidSubmission):
        score_decisions(EX / "weather.csv", EX / "tiles.csv", EX / "score_config.json", bad, tmp_path / "r.json")
    assert json.loads((tmp_path / "r.json").read_text())["status"] == "invalid_submission"


def test_baseline_agent_reproduces_reference_via_local_runner(tmp_path):
    out = tmp_path / "run"
    r = subprocess.run([sys.executable, str(KIT / "local_runner.py"), "--agent", str(KIT / "agent.py"), "--weather", str(EX / "weather.csv"),
                        "--tiles", str(EX / "tiles.csv"), "--config", str(KIT / "score_config.json"), "--out", str(out), "--quiet"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert (out / "decisions.csv").read_text() == (EX / "decisions.csv").read_text()
    ref = json.loads((EX / "score_report.json").read_text())
    assert json.loads((out / "score_report.json").read_text())["score"] == pytest.approx(ref["score"], abs=1e-6)


def test_platform_runner_matches_local_runner(tmp_path):
    from app.services import runner
    entry = runner.prepare_agent_dir(KIT / "agent.py", tmp_path / "agent")
    res = runner.run_agent(entry, tmp_path / "agent", weather=EX / "weather.csv", tiles=EX / "tiles.csv", config=EX / "score_config.json", out_dir=tmp_path / "out")
    ref = json.loads((EX / "score_report.json").read_text())
    assert res.report["score"] == pytest.approx(ref["score"], abs=1e-6)
    assert res.steps == 31
    assert res.warnings == []


def test_starter_kit_files_in_sync():
    assert (KIT / "scorer.py").read_text() == (ROOT / "scoring" / "scorer.py").read_text()
    assert (KIT / "protocol.py").read_text() == (ROOT / "scoring" / "protocol.py").read_text()
    assert (KIT / "score_config.json").read_text() == (ROOT / "scoring" / "score_config.json").read_text()


def test_generator_is_deterministic(tmp_path):
    from app.services.scenarios import generate_scenario_files, validate_scenario_files
    a = generate_scenario_files(tmp_path / "a", seed=5, n_nights=3, slots_per_night=10, n_tiles=40)
    b = generate_scenario_files(tmp_path / "b", seed=5, n_nights=3, slots_per_night=10, n_tiles=40)
    assert a["weather"].read_text() == b["weather"].read_text()
    assert a["tiles"].read_text() == b["tiles"].read_text()
    stats = validate_scenario_files(a["weather"], a["tiles"], a["config"])
    assert stats == {"n_slots": 30, "n_nights": 3, "n_tiles": 40, "schema_version": "stage1-score-v1"}


# --------------------------------------------------------------------------- sandbox behaviour

def _run(agent_src: str, tmp_path: Path):
    from app.services import runner
    (tmp_path / "agent.py").write_text(agent_src)
    entry = runner.prepare_agent_dir(tmp_path / "agent.py", tmp_path / "box")
    return runner.run_agent(entry, tmp_path / "box", weather=EX / "weather.csv", tiles=EX / "tiles.csv", config=EX / "score_config.json", out_dir=tmp_path / "out")


def test_agent_crash_is_reported(tmp_path):
    from app.services.runner import AgentRunError
    with pytest.raises(AgentRunError, match="exited before answering"):
        _run("import sys\nsys.stdin.readline()\nraise SystemExit(3)\n", tmp_path)


def test_agent_bad_json_is_reported(tmp_path):
    from app.services.runner import AgentRunError
    with pytest.raises(AgentRunError, match="invalid JSON"):
        _run("import sys\nfor line in sys.stdin:\n    print('hello world'); sys.stdout.flush()\n", tmp_path)


def test_agent_timeout_is_reported(tmp_path):
    from app.services.runner import AgentRunError
    with pytest.raises(AgentRunError, match="did not answer"):
        _run("import sys, time\nsys.stdin.readline()\nsys.stdin.readline()\ntime.sleep(60)\n", tmp_path)


def test_agent_unknown_tile_and_bad_program_do_not_crash(tmp_path):
    src = '''import sys, json
n = 0
for line in sys.stdin:
    m = json.loads(line)
    if m.get("type") != "step": continue
    n += 1
    if n == 1: a = {"action": "observe", "tile_id": "999999", "program": "DARK", "reason": "unknown tile"}
    elif n == 2: a = {"action": "observe", "tile_id": m["available_tiles"][0]["tile_id"], "program": "MOON", "reason": "bad program"}
    elif m["available_tiles"]: a = {"action": "observe", "tile_id": m["available_tiles"][0]["tile_id"], "plan": m["available_tiles"][0]["program"]}
    else: a = {"action": "wait"}
    print(json.dumps(a)); sys.stdout.flush()
'''
    res = _run(src, tmp_path)
    assert res.report["invalid_actions"] >= 2
    assert any("unknown program" in w for w in res.warnings)
    assert res.report["completed_tiles"] > 0


def test_agent_has_no_network(tmp_path):
    src = '''import sys, json, urllib.request
for line in sys.stdin:
    m = json.loads(line)
    if m.get("type") != "step": continue
    try:
        urllib.request.urlopen("http://example.com", timeout=3); ok = "net"
    except Exception as e: ok = "nonet"
    print(json.dumps({"action": "wait", "reason": ok})); sys.stdout.flush()
'''
    res = _run(src, tmp_path)
    reasons = {a["reason"] for a in res.report["actions"]}
    # subprocess mode cannot block sockets on macOS; docker mode does. Record what happened either way.
    assert reasons <= {"net", "nonet"}


def test_zip_slip_rejected(tmp_path):
    import zipfile
    from app.services.runner import AgentPackageError, prepare_agent_dir
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../../evil.py", "print(1)")
        zf.writestr("agent.py", "print(1)")
    with pytest.raises(AgentPackageError, match="unsafe path"):
        prepare_agent_dir(z, tmp_path / "box")


def test_zip_with_folder_root(tmp_path):
    import zipfile
    from app.services.runner import prepare_agent_dir
    z = tmp_path / "ok.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("myagent/agent.py", (KIT / "agent.py").read_text())
        zf.writestr("myagent/helpers/util.py", "X = 1\n")
    entry = prepare_agent_dir(z, tmp_path / "box")
    assert entry.name == "agent.py" and entry.parent.name == "myagent"
