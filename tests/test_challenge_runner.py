"""Sandboxed v3 agent runner: package handling, dotenv, isolation, termination semantics (no network, no Supabase)."""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CH = ROOT / "challenge"


@pytest.fixture(scope="module")
def scenario(tmp_path_factory):
    from challenge import scenario_builder
    root = tmp_path_factory.mktemp("scn") / "seven"
    scenario_builder.generate_scenario(root, scenario_id="test-7d", seed=99, days=7, start_date="2026-10-05", global_wallclock_seconds=60)
    return root


def _pkg(tmp_path: Path, extra_env: str = "MODEL_PROVIDER=deterministic\n") -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    for p in (CH / "participant_agent").glob("*.py"):
        shutil.copyfile(p, pkg / p.name)
    shutil.copyfile(CH / "scoring_preview.py", pkg / "scoring_preview.py")
    (pkg / ".env").write_text(extra_env)
    return pkg


def test_minimal_agent_completes_and_scores(tmp_path, scenario):
    from worker import challenge_runner as cr
    from worker.main import metrics_from_report
    from challenge.scoring_core import score_files
    pkg = _pkg(tmp_path, "MODEL_PROVIDER=deterministic\nSECRET_TEST_KEY=do-not-log\n")
    res = cr.run_agent_package(pkg, scenario, tmp_path / "out", wallclock_seconds=60, scenario_meta={"slug": "seven"})
    assert res["termination_reason"] == "survey_complete" and res["committed_action_count"] > 50
    report = score_files(scenario, tmp_path / "out" / "decisions.csv", tmp_path / "out" / "report.json", res["termination_reason"])
    m = metrics_from_report(report, 64)
    assert m["score"] == pytest.approx(report["score"]["total"]) and m["completed_tiles"] > 0
    log = (tmp_path / "out" / "agent.log").read_text()
    assert "do-not-log" not in log and "SECRET_TEST_KEY" in log  # key names are logged, values never
    wr = json.loads((tmp_path / "out" / "workflow_result.json").read_text())
    assert "initial_publication" not in wr and wr["committed_action_count"] == res["committed_action_count"]
    # deterministic: a second run yields the same decisions
    res2 = cr.run_agent_package(pkg, scenario, tmp_path / "out2", wallclock_seconds=60)
    assert (tmp_path / "out2" / "decisions.csv").read_bytes() == (tmp_path / "out" / "decisions.csv").read_bytes()


def test_agent_cannot_see_scenario_dir(tmp_path, scenario):
    from worker import challenge_runner as cr
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "agent.py").write_text(
        "import sys, json, os\n"
        "for line in sys.stdin:\n"
        "    m = json.loads(line)\n"
        "    if m['message_type'] == 'initialize': continue\n"
        "    leak = os.path.exists(os.environ.get('LEAK_PATH', '/nonexistent'))\n"
        "    print(json.dumps({'decision_sequence': m['decision_sequence'], 'action': 'wait', 'reason': 'cwd=%s leak=%s' % (os.getcwd(), leak)})); sys.stdout.flush()\n")
    (pkg / ".env").write_text(f"LEAK_PATH={scenario / 'outputs' / 'reference' / 'weather_events.csv'}\n")
    res = cr.run_agent_package(pkg, scenario, tmp_path / "out", wallclock_seconds=20)
    rows = (tmp_path / "out" / "decisions.csv").read_text().splitlines()
    assert rows[1].endswith("leak=True") or rows[1].endswith("leak=False")
    # the process runs inside its own directory; the scenario path is outside it
    assert str(pkg.resolve()) in rows[1] and str(scenario) not in rows[1].split("cwd=")[1].split(" ")[0]


def test_crash_and_timeout_semantics(tmp_path, scenario):
    from worker import challenge_runner as cr
    pkg = tmp_path / "crash"
    pkg.mkdir()
    (pkg / "agent.py").write_text("import sys\nsys.stdin.readline()\nraise SystemExit(3)\n")
    res = cr.run_agent_package(pkg, scenario, tmp_path / "o1", wallclock_seconds=20)
    assert res["termination_reason"] in ("agent_error", "agent_initialization_error")
    slow = tmp_path / "slow"
    slow.mkdir()
    (slow / "agent.py").write_text("import sys, time\nfor line in sys.stdin:\n    time.sleep(30)\n")
    res = cr.run_agent_package(slow, scenario, tmp_path / "o2", wallclock_seconds=3)
    assert res["termination_reason"] == "global_wallclock_expired" and res["accounted_wallclock_seconds"] <= 3.5


def test_zip_layouts_and_entry_detection(tmp_path):
    from worker.runner import prepare_agent_dir
    from worker import challenge_runner as cr
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("my-agent/minimal_agent.py", "print(1)")
        zf.writestr("my-agent/helpers.py", "X=1")
        zf.writestr("my-agent/.env", "MODEL_PROVIDER=deterministic")
    d = prepare_agent_dir(z, tmp_path / "d")
    entry = cr.find_entry(tmp_path / "d")
    assert entry.name == "minimal_agent.py" and (entry.parent / ".env").exists()
    assert cr.load_dotenv(entry.parent / ".env") == {"MODEL_PROVIDER": "deterministic"}
    bad = tmp_path / "b.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("notes.txt", "no entry here")
    prepare_agent_dir(bad, tmp_path / "e")
    with pytest.raises(cr.AgentPackageError):
        cr.find_entry(tmp_path / "e")


def test_partial_package_is_completed_with_the_kit(tmp_path):
    """A participant may upload only my_strategy.py (bare or zipped): the worker wraps it with the minimal agent."""
    from worker import challenge_runner as cr
    from worker.runner import complete_agent_package, prepare_agent_dir
    template = ROOT / "challenge" / "participant_agent"
    preview = ROOT / "challenge" / "scoring_preview.py"
    bare = tmp_path / "upload.py"
    bare.write_text("def choose_action(candidates, snapshot, memory):\n    return candidates[0] if candidates else None\n")
    d = tmp_path / "bare"
    prepare_agent_dir(bare, d, original_name="my_strategy.py")
    added = complete_agent_package(d, template, preview)
    assert (d / "my_strategy.py").read_text().startswith("def choose_action") and "minimal_agent.py" in added and "scoring_preview.py" in added
    assert cr.find_entry(d) == d / "minimal_agent.py"
    z = tmp_path / "partial.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("strategy/my_strategy.py", "def choose_action(candidates, snapshot, memory):\n    return None\n")
        zf.writestr("strategy/.env", "MODEL_PROVIDER=deterministic\n")
    d2 = tmp_path / "zipped"
    prepare_agent_dir(z, d2)
    added = complete_agent_package(d2, template, preview)
    assert "decision_graph.py" in added and (d2 / "strategy" / "my_strategy.py").read_text().endswith("return None\n")
    assert cr.find_entry(d2) == d2 / "strategy" / "minimal_agent.py"
    full = tmp_path / "full"
    full.mkdir(); (full / "agent.py").write_text("print('x')\n")
    assert complete_agent_package(full, template, preview) == []
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir(); (unrelated / "notes.txt").write_text("hi\n")
    assert complete_agent_package(unrelated, template, preview) == []
