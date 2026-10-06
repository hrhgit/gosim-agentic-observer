"""v4 engine path: a fake protocol-v4 agent end to end through the colocated engine (no Docker)."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from challenge import v4_workflow
from project_platform.manifest import ProjectManifest
from project_platform.trusted_engine import ColocatedProvider, V4ColocatedOnly, result_summary, run_session
from project_platform.transport import JsonlTransport
from v4_support import build_bundle

AGENT = Path(__file__).resolve().parent / "fixtures" / "v4_fake_agent.py"
DEMO_V3 = Path(__file__).resolve().parents[1] / "archive" / "starter_kit_v3" / "scenarios" / "demo-week"


class Client:
    """The session API as seen by the colocated engine (initialize / ready / begin / finish)."""

    def __init__(self, deadline_seconds=600):
        self.actions, self.deadline_seconds = [], deadline_seconds
        self.publications = []

    def call(self, action, **kwargs):
        self.actions.append(action)
        if action == "initialize":
            self.publications.append(kwargs["publication"])
        if action == "begin":
            return (datetime.now(timezone.utc) + timedelta(seconds=self.deadline_seconds)).isoformat().replace("+00:00", "Z")
        return {}


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return build_bundle(tmp_path_factory.mktemp("v4") / "bundle", card_id="A")


@pytest.fixture(scope="module")
def stress_bundle(tmp_path_factory):
    return build_bundle(tmp_path_factory.mktemp("v4s") / "bundle", card_id="C", stress=True, seed=77)


def run_card(bundle: Path, out: Path, mode="greedy", *, wallclock_seconds=120, env=None, session_deadline=600):
    transport = JsonlTransport([sys.executable, "-u", str(AGENT), mode], cwd=out.parent,
                               environment={"PATH": os.environ["PATH"], **(env or {})})
    engine = Client(session_deadline)
    provider = ColocatedProvider(transport, engine, Client())
    try:
        result, digest = run_session(bundle, out, engine, wallclock_seconds=wallclock_seconds, provider=provider)
    finally:
        transport.close(force=True)
    lines = transport.log.splitlines()

    def tagged(tag):
        return [json.loads(line[len(tag) + 1:]) for line in lines if line.startswith(tag + " ")]

    return result, digest, tagged, engine


def test_v4_card_runs_end_to_end_through_the_colocated_engine(bundle, tmp_path):
    result, digest, tagged, engine = run_card(bundle, tmp_path / "out")
    assert result["termination_reason"] in ("survey_complete", "agent_finished")
    report = result["score_report"]
    assert report["counts"]["observe_actions"] > 0 and report["counts"]["observations"] > 0
    assert report["components"]["sum_best_scores"] > 0
    # Session bookkeeping is unchanged: only the colocated transport marker reaches the database.
    assert engine.actions[:2] == ["initialize", "begin"]
    assert engine.publications == [{"transport_format": "observer-colocated-v1"}]
    [init] = tagged("INIT")
    assert init["protocol_version"] == "participant-agent-protocol-v4"
    assert init["schema_version"] == "v4-initialize-v1"
    assert init["keys"] == ["footprint", "instrument", "limits", "schema_version", "scoring", "site", "survey",
                            "targets", "task_card"]
    assert init["task_card"] == {"card_id": "A", "scenario_slug": "v4-test-a", "phase": "test"}
    assert init["limits"]["global_wallclock_seconds"] == 120
    [finish] = tagged("FINISH-MSG")
    assert finish["protocol_version"] == "participant-agent-protocol-v4"
    assert finish["payload"]["schema_version"] == "v4-finish-v1"
    assert finish["payload"]["termination_reason"] == result["termination_reason"]
    assert finish["payload"]["decisions"] == report["counts"]["decisions"]
    assert finish["payload"]["last_decision_sequence"] == result["last_decision_sequence"]
    assert finish["payload"]["grace_seconds"] == 30
    out = tmp_path / "out"
    for name in ("decisions.csv", "observations.csv", "messages.jsonl", "score_report.json", "workflow_result.json"):
        assert (out / name).is_file(), name
    assert digest == hashlib.sha256((out / "decisions.csv").read_bytes()).hexdigest()
    # wait until_utc bridged the days without a round trip per hour.
    assert result["decision_requests"] < report["counts"]["decisions"]
    summary = result_summary(result)
    assert summary["gameplay"] == "v4" and summary["score"]["total"] == report["total"]
    assert summary["committed_action_count"] == report["counts"]["decisions"] > 0
    assert json.loads((out / "workflow_result.json").read_text())["score_report"]["total"] == report["total"]


def test_same_agent_twice_gives_the_same_score_and_decisions(bundle, tmp_path):
    first = run_card(bundle, tmp_path / "one")
    second = run_card(bundle, tmp_path / "two")
    assert first[0]["score_report"]["total"] == second[0]["score_report"]["total"]
    assert first[1] == second[1]


def test_nothing_hidden_reaches_the_agent_or_the_result(stress_bundle, tmp_path):
    workflow = v4_workflow.V4Workflow(stress_bundle)
    payload = json.dumps(workflow.initialize_payload(900))
    for forbidden in ("seed", "is_observable", "weather_truth", "V4EV", "V4ST", "pointing_offset",
                      "instrument_fault", "alt_offset", "transparency\":", "stress"):
        assert forbidden not in payload, forbidden
    result, _, _, _ = run_card(stress_bundle, tmp_path / "out")
    out = tmp_path / "out"
    for path in out.iterdir():
        text = path.read_text()
        assert "pointing_offset" not in text and "alt_offset" not in text, path.name
    # Truth files are never copied into the result.
    assert sorted(p.name for p in out.iterdir()) == ["actions.jsonl", "decisions.csv", "messages.jsonl",
                                                     "observations.csv", "score_report.json", "workflow_result.json"]


def test_invalid_action_ends_as_agent_error_and_settles_the_valid_history(bundle, tmp_path):
    good, *_ = run_card(bundle, tmp_path / "good", "finish-after:6")
    bad, _, tagged, _ = run_card(bundle, tmp_path / "bad", "bad-after:6")
    assert good["termination_reason"] == "agent_finished"
    assert bad["termination_reason"] == "agent_error"
    assert "fiber_id 16" in bad["termination_detail"]
    assert bad["score_report"]["total"] == good["score_report"]["total"]
    assert result_summary(bad)["termination_reason"] == "agent_error"
    [finish] = tagged("FINISH-MSG")  # an error still ends with the finish notice
    assert finish["payload"]["termination_reason"] == "agent_error"


def test_non_json_output_is_an_agent_error_with_a_settled_score(bundle, tmp_path):
    result, *_ = run_card(bundle, tmp_path / "garbage", "garbage-after:3")
    assert result["termination_reason"] == "agent_error"
    assert "JSON-Lines" in result["termination_detail"]
    assert result["score_report"]["counts"]["decisions"] >= 3


def test_wall_clock_expiry_settles_and_is_bounded(bundle, tmp_path):
    import time

    started = time.monotonic()
    result, _, tagged, _ = run_card(bundle, tmp_path / "sleep", "sleep", wallclock_seconds=2)
    assert time.monotonic() - started < 20
    assert result["termination_reason"] == "global_wallclock_expired"
    assert result["ignored_in_flight_response"] is True
    assert result["accounted_wallclock_seconds"] <= 2.0
    assert result["score_report"]["termination"]["reason"] == "global_wallclock_expired"


def test_the_card_cap_bounds_every_requested_budget(bundle, tmp_path):
    workflow = v4_workflow.V4Workflow(bundle)
    assert workflow.wallclock_budget(None) == 900
    assert workflow.wallclock_budget(3600) == 900
    assert workflow.wallclock_budget(300) == 300
    capped = build_bundle(tmp_path / "capped", wallclock_seconds=600)
    assert v4_workflow.V4Workflow(capped).wallclock_budget(3600) == 600


def test_v4_bundles_never_run_through_the_session_database(bundle, tmp_path):
    with pytest.raises(V4ColocatedOnly):
        run_session(bundle, tmp_path / "remote", Client(), wallclock_seconds=60)
    with pytest.raises(V4ColocatedOnly):
        run_session(bundle, tmp_path / "instance", Client(), wallclock_seconds=60,
                    instance_record={"instance_digest": "x"}, provider=object())


def test_engine_job_refuses_a_v4_bundle_without_a_colocated_participant(bundle, tmp_path, monkeypatch):
    import shutil

    from project_platform import job_runner
    from project_platform.job_client import JobError

    def fake_extract(files, destination):
        shutil.copytree(bundle, destination)

    monkeypatch.setattr(job_runner, "download_project", lambda *args: ())
    monkeypatch.setattr(job_runner, "extract_project", fake_extract)
    payload = {"scenario_url": "https://x", "scenario_digest": "0" * 64, "session_url": "https://example.org/s",
               "run_credential": "obs_x", "runtime_seconds": 900}
    with pytest.raises(JobError, match="v4_requires_colocated"):
        job_runner.engine_job(payload, tmp_path, http=None)


def test_v3_bundles_keep_the_v3_engine():
    # The v3 run itself is covered by test_project_runtime; here only the dispatch decision.
    assert not v4_workflow.is_v4_bundle(DEMO_V3)


def test_bundle_products_must_stay_inside_the_bundle(tmp_path):
    root = build_bundle(tmp_path / "escape")
    config = json.loads((root / "config" / "v4_scenario.json").read_text())
    outside = tmp_path / "outside.csv"
    outside.write_text((root / "public" / "targets.csv").read_text())
    config["products"]["targets_csv"] = str(outside)
    (root / "config" / "v4_scenario.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="outside the bundle"):
        v4_workflow.V4Workflow(root)


def test_response_envelope_must_answer_the_current_request():
    good = {"protocol_version": "participant-agent-protocol-v4", "message_type": "decision_response",
            "decision_sequence": 3, "reason": "r", "decision_source": "llm", "action": "wait", "duration_seconds": 60}
    assert v4_workflow.V4Workflow.action_from_response(good, 3) == {"action": "wait", "duration_seconds": 60}
    for broken in ({**good, "decision_sequence": 2}, {**good, "protocol_version": "participant-agent-protocol-v2"},
                   {**good, "message_type": "decision"}, {**good, "decision_sequence": True}, {**good, "reason": 5}, []):
        with pytest.raises(v4_workflow.ProtocolViolation):
            v4_workflow.V4Workflow.action_from_response(broken, 3)


def test_manifest_accepts_jsonl_v4_as_the_same_transport():
    base = {"schema_version": "observer-project-v1", "image": "python:3.12-slim", "run": ["python3", "agent.py"]}
    v2 = ProjectManifest.parse({**base, "protocol": "jsonl-v2"})
    v4 = ProjectManifest.parse({**base, "protocol": "jsonl-v4"})
    assert v4.digest == v2.digest and v4.protocol == "jsonl-v2"
    with pytest.raises(Exception):
        ProjectManifest.parse({**base, "protocol": "jsonl-v3"})


def _verify(bundle, result):
    import subprocess

    script = Path(__file__).resolve().parents[1] / "scripts" / "verify-v4-run.py"
    proc = subprocess.run([sys.executable, str(script), "--bundle", str(bundle), "--result", str(result)],
                          capture_output=True, text=True, timeout=300)
    return proc.returncode, json.loads(proc.stdout)


def test_organizer_verification_replays_a_run_exactly(stress_bundle, tmp_path):
    import shutil
    import zipfile

    result, *_ = run_card(stress_bundle, tmp_path / "out", "bad-after:5")
    code, report = _verify(stress_bundle, tmp_path / "out")
    assert code == 0 and report["verified"], report
    assert report["replayed_termination"] == "agent_error" == report["recorded_termination"]
    assert report["replayed_total"] == result["score_report"]["total"]
    # The ZIPs from private storage work the same way.
    for name, source in (("bundle.zip", stress_bundle), ("result.zip", tmp_path / "out")):
        with zipfile.ZipFile(tmp_path / name, "w") as archive:
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(source).as_posix())
    code, report = _verify(tmp_path / "bundle.zip", tmp_path / "result.zip")
    assert code == 0 and report["verified"]
    # A tampered score report is caught.
    shutil.copytree(tmp_path / "out", tmp_path / "tampered")
    tampered = json.loads((tmp_path / "tampered" / "score_report.json").read_text())
    tampered["total"] += 1
    (tmp_path / "tampered" / "score_report.json").write_text(json.dumps(tampered))
    code, report = _verify(stress_bundle, tmp_path / "tampered")
    assert code == 1 and report["checks"]["total"] is False
    assert "pointing_offset" not in json.dumps(report) and "seed" not in json.dumps(report)


def _public_test_card(tmp_path):
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "build-v4-public-test-card.py"
    spec = importlib.util.spec_from_file_location("build_v4_public_test_card", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_test_card_is_small_reproducible_and_fits_the_preview_cap(tmp_path):
    import time

    module = _public_test_card(tmp_path)
    first = module.build_card_bundle(tmp_path / "one", module.PUBLIC_TEST_SPEC)
    second = module.build_card_bundle(tmp_path / "two", module.PUBLIC_TEST_SPEC)
    assert module.pack(first) == module.pack(second)
    workflow = v4_workflow.V4Workflow(first)
    assert workflow.wallclock_budget(None) == 300 and workflow.wallclock_budget(900) == 300
    assert workflow.config["task_card"]["scenario_slug"] == "v4-public-test"
    assert not any(slug in json.dumps(workflow.config) for slug in ("v4-alpha", "v4-beta", "v4-a\"", "v4-e"))
    # A public test with the preview cap: a trivial agent completes the whole season quickly,
    # and an agent that ends its own run with "finish" still has committed decisions.
    started = time.monotonic()
    result, *_ = run_card(first, tmp_path / "greedy", wallclock_seconds=300)
    assert result["termination_reason"] in ("survey_complete", "agent_finished")
    assert time.monotonic() - started < 60
    finished, *_ = run_card(first, tmp_path / "finish", "finish-after:3", wallclock_seconds=300)
    assert finished["termination_reason"] == "agent_finished"
    assert result_summary(finished)["committed_action_count"] >= 3
