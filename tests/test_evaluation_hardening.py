"""Evaluation hardening: model-proxy-only egress and the independent rescore.

Database tests run on embedded Postgres; the forwarder and score-job tests need
nothing else. Docker tests need OBSERVER_TEST_PYTHON_IMAGE, and the end-to-end
test additionally Deno and PostgREST (CI supplies all three).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from test_project_database import database, identity, query, rpc, setup  # noqa: F401
from v4_support import build_bundle

ROOT = Path(__file__).resolve().parents[1]
ORG = "AGENTIC-OBSERVER26-runner-1"
BUNDLE_DIGEST = "b" * 64
RESULT = "github:" + ORG + "/participant-" + "0" * 32 + "@" + "9" * 40
DOCKER = pytest.mark.skipif(not os.environ.get("OBSERVER_TEST_PYTHON_IMAGE"),
                            reason="Explicit resolved container image required")


# ---------------------------------------------------------------------------
# Database: switches, score_check, settlement
# ---------------------------------------------------------------------------

@pytest.fixture
def formal(setup):
    s = setup
    query(s["uri"], """insert into private.observer_installations
        (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
        values(%s,'101',202,'303',%s,true) on conflict(organization) do update set enabled=true""", (ORG, "a" * 40))
    query(s["uri"], "insert into private.observer_scenario_bundles values(%s,%s,%s)",
          (s["scenario"], f"{s['scenario']}/bundle.zip", BUNDLE_DIGEST))
    yield s
    rpc(s["uri"], "observer_set_hardening", True, True)


def formal_run(s):
    """A formal project run brought to 'running' by its colocated engine job."""
    uri = s["uri"]
    rev = rpc(uri, "observer_create_project", "Agent", "repository", "https://github.com/example/agent",
              role="authenticated", user=s["user"])
    query(uri, """update public.observer_revisions set status='reviewable',source_digest=%s,approval_digest=%s,
        manifest=%s,public_test='{"passed":true}' where id=%s""",
          ("c" * 64, "d" * 64, Jsonb({"image": "python@sha256:" + "e" * 64}), rev))
    rpc(uri, "observer_approve_revision", rev, "d" * 64, role="authenticated", user=s["user"])
    query(uri, "insert into private.observer_materializations values(%s,%s,%s)",
          (rev, "github:" + ORG + "/participant-" + s["user"].hex + "@" + "f" * 40, "a" * 64))
    batch = rpc(uri, "observer_create_batch", s["phase"], rev, role="authenticated", user=s["user"])
    run = query(uri, "select id from public.observer_runs where batch_id=%s", (batch,))[0][0]
    participant, engine = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    rpc(uri, "observer_open_session", run, participant, engine)
    rpc(uri, "observer_enqueue_job", uuid.uuid4(), "engine", run, None, ORG, secrets.token_urlsafe(32), "x", "y")
    rpc(uri, "observer_publish_initial", run, engine, Jsonb({"transport_format": "observer-colocated-v1"}))
    rpc(uri, "observer_ready", run, participant)
    rpc(uri, "observer_begin", run, engine)
    return batch, run, engine


def finish(s, run, engine, total, digest, path=RESULT):
    summary = {"schema_version": "observer-run-summary-v1", "gameplay": "v4",
               "score": {"total": total, "sum_best_scores": total},
               "termination_reason": "agent_finished", "committed_action_count": 7}
    rpc(s["uri"], "observer_finish_run", run, engine, Jsonb(summary), digest, path)
    return summary


def run_row(s, run):
    return query(s["uri"], "select status,score,error,score_check from public.observer_runs where id=%s", (run,))[0]


def batch_row(s, batch):
    return query(s["uri"], "select status,score from public.observer_batches where id=%s", (batch,))[0]


def pending(s, run):
    return [r for r in rpc(s["uri"], "observer_pending_score_runs", 20) if r["id"] == str(run)]


def claim_score_job(s, run):
    job, nonce = uuid.uuid4(), secrets.token_urlsafe(32)
    rpc(s["uri"], "observer_enqueue_job", job, "score", run, None, ORG, nonce, "encrypted input", "encrypted nonce")
    rpc(s["uri"], "observer_claim_job", job, nonce, "404", "1", "303", "101", "a" * 40)
    return job


def receipt(run, digest, total):
    return {"run_id": str(run), "decisions_digest": digest, "score": {"total": total, "sum_best_scores": total}}


# Model proxy retirement switches (20261004080000), off by default.
OFF = {"prepare_direct_model": False, "model_proxy_retired": False, "open_egress": False}


def test_switches_default_on_and_are_service_role_only(formal):
    s = formal
    assert rpc(s["uri"], "observer_hardening") == {"restricted_egress": True, "rescore": True, "team_egress": False,
                                                **OFF}
    for role in ("authenticated", "anon"):
        for statement in ("select public.observer_hardening()", "select public.observer_set_hardening(false,false)",
                          "select public.observer_pending_score_runs(5)"):
            with pytest.raises(psycopg.Error, match="permission denied"):
                query(s["uri"], statement, role=role, user=s["user"])
    assert rpc(s["uri"], "observer_set_hardening", False, None) == {"restricted_egress": False, "rescore": True, "team_egress": False,
                                                **OFF}
    assert rpc(s["uri"], "observer_set_hardening", None, False) == {"restricted_egress": False, "rescore": False, "team_egress": False, **OFF}
    assert query(s["uri"], "select detail from public.audit_log where action='observer.hardening' "
                           "order by created_at desc limit 1")[0][0] == {"restricted_egress": False, "rescore": False, "team_egress": False, **OFF}


def test_a_finished_formal_run_is_scored_at_once_and_queued_for_its_rescore(formal):
    s = formal
    batch, run, engine = formal_run(s)
    digest = hashlib.sha256(b"trace").hexdigest()
    finish(s, run, engine, 55.25, digest)
    # Scored immediately, exactly as before; the check runs afterwards.
    assert run_row(s, run) == ("scored", 55.25, "", "pending")
    assert batch_row(s, batch) == ("scored", 55.25)
    [row] = pending(s, run)
    assert row["organization"] == ORG and row["scenario_digest"] == BUNDLE_DIGEST
    assert row["result_path"] == RESULT and row["decisions_digest"] == digest
    assert row["termination_reason"] == "agent_finished"
    # One score job per run.
    claim_score_job(s, run)
    assert pending(s, run) == []


def test_switch_off_leaves_finish_exactly_as_before(formal):
    s = formal
    rpc(s["uri"], "observer_set_hardening", None, False)
    batch, run, engine = formal_run(s)
    finish(s, run, engine, 12.0, hashlib.sha256(b"trace").hexdigest())
    assert run_row(s, run) == ("scored", 12.0, "", None)
    assert pending(s, run) == []
    # Turning it off also drops checks no score job has picked up yet.
    rpc(s["uri"], "observer_set_hardening", None, True)
    batch2, run2, engine2 = formal_run(s)
    finish(s, run2, engine2, 13.0, hashlib.sha256(b"trace2").hexdigest())
    assert len(pending(s, run2)) == 1
    rpc(s["uri"], "observer_set_hardening", None, False)
    assert pending(s, run2) == [] and run_row(s, run2) == ("scored", 13.0, "", None)
    rpc(s["uri"], "observer_set_hardening", None, True)
    assert pending(s, run2) == []


def test_matching_recompute_verifies_and_a_forged_score_is_corrected(formal):
    s = formal
    uri = s["uri"]
    batch, run, engine = formal_run(s)
    digest = hashlib.sha256(b"trace one").hexdigest()
    finish(s, run, engine, 55.25, digest)
    job = claim_score_job(s, run)
    rpc(uri, "observer_finish_job", job, "404", "1", Jsonb(receipt(run, digest, 55.25)), "")
    assert run_row(s, run) == ("scored", 55.25, "", "verified")
    assert query(uri, "select score_summary ? 'rescore' from public.observer_runs where id=%s", (run,)) == [(False,)]

    # A tampered engine inflated its own summary: the recomputed score wins.
    batch2, run2, engine2 = formal_run(s)
    digest2 = hashlib.sha256(b"trace two").hexdigest()
    forged = finish(s, run2, engine2, 999999.0, digest2)
    assert batch_row(s, batch2) == ("scored", 999999.0)
    job2 = claim_score_job(s, run2)
    rpc(uri, "observer_finish_job", job2, "404", "1", Jsonb(receipt(run2, digest2, 55.25)), "")
    assert run_row(s, run2) == ("scored", 55.25, "", "corrected")
    assert batch_row(s, batch2) == ("scored", 55.25)
    summary = query(uri, "select score_summary from public.observer_runs where id=%s", (run2,))[0][0]
    assert summary["score"] == {"total": 55.25, "sum_best_scores": 55.25}
    assert summary["rescore"] == {"reported_total": 999999.0, "recomputed_total": 55.25}
    assert query(uri, "select outcome,reported_score,reported_summary from private.observer_score_checks "
                      "where run_id=%s", (run2,)) == [("corrected", 999999.0, forged)]
    assert query(uri, "select detail->>'recomputed' from public.audit_log where action='observer.score_corrected' "
                      "and detail->>'run_id'=%s", (str(run2),)) == [("55.25",)]
    # The board reads the corrected score.
    assert query(uri, "select score from public.observer_leaderboard(%s) where team_id=%s",
                 (s["phase"], s["team"]), role="anon") == [(55.25,)]
    # A retried finish from the engine still matches its original report.
    rpc(uri, "observer_finish_run", run2, engine2, Jsonb(forged), digest2, RESULT)
    with pytest.raises(psycopg.Error, match="result_conflict"):
        rpc(uri, "observer_finish_run", run2, engine2, Jsonb(summary), digest2, RESULT)
    # The receipt is bound to the committed digest: another trace changes nothing.
    batch3, run3, engine3 = formal_run(s)
    digest3 = hashlib.sha256(b"trace three").hexdigest()
    finish(s, run3, engine3, 7.0, digest3)
    job3 = claim_score_job(s, run3)
    rpc(uri, "observer_finish_job", job3, "404", "1",
        Jsonb(receipt(run3, hashlib.sha256(b"other").hexdigest(), 70.0)), "")
    assert run_row(s, run3) == ("scored", 7.0, "", "unverified")


def test_a_trace_that_is_not_the_committed_one_voids_the_run(formal):
    s = formal
    uri = s["uri"]
    batch, run, engine = formal_run(s)
    finish(s, run, engine, 12.0, hashlib.sha256(b"committed").hexdigest())
    job = claim_score_job(s, run)
    rpc(uri, "observer_finish_job", job, "404", "1",
        Jsonb({"diagnostics": {"stage": "score", "code": "score_trace_mismatch", "log": ""}}), "score_job_failed")
    assert run_row(s, run) == ("failed", None, "score_verification_failed", "rejected")
    assert batch_row(s, batch) == ("failed", None)
    # The team's failure, not the platform's: no refund, no automatic retry.
    assert query(uri, "select quota_refunded from public.observer_batches where id=%s", (batch,)) == [(False,)]
    assert query(uri, "select private.observer_participant_failure(%s)", (run,)) == [(True,)]
    assert query(uri, "select score from public.observer_leaderboard(%s) where team_id=%s",
                 (s["phase"], s["team"]), role="anon") == []
    assert query(uri, "select count(*) from public.audit_log where action='observer.score_rejected' "
                      "and detail->>'run_id'=%s", (str(run),)) == [(1,)]


def test_infrastructure_failures_leave_the_reported_score_unverified(formal):
    s = formal
    uri = s["uri"]
    batch, run, engine = formal_run(s)
    finish(s, run, engine, 12.0, hashlib.sha256(b"committed").hexdigest())
    job = claim_score_job(s, run)
    rpc(uri, "observer_finish_job", job, "404", "1",
        Jsonb({"diagnostics": {"stage": "score", "code": "job_http_503", "log": ""}}), "score_job_failed")
    assert run_row(s, run) == ("scored", 12.0, "", "unverified")
    assert batch_row(s, batch) == ("scored", 12.0)
    # An expired score job is the same.
    batch2, run2, engine2 = formal_run(s)
    finish(s, run2, engine2, 13.0, hashlib.sha256(b"committed 2").hexdigest())
    job2 = claim_score_job(s, run2)
    query(uri, "update private.observer_jobs set expires_at=now()-interval '1 second' where id=%s", (job2,))
    rpc(uri, "observer_reconcile_jobs")
    assert run_row(s, run2) == ("scored", 13.0, "", "unverified")
    assert query(uri, "select status,error from private.observer_jobs where id=%s", (job2,)) == [("failed", "job_expired")]


def test_a_score_job_failing_over_never_moves_the_team(formal):
    s = formal
    uri = s["uri"]
    other = "AGENTIC-OBSERVER26-runner-2"
    query(uri, """insert into private.observer_installations
        (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
        values(%s,'102',203,'304',%s,true) on conflict(organization) do update set enabled=true""", (other, "b" * 40))
    query(uri, """insert into private.observer_placements(user_id,organization) values(%s,%s)
        on conflict(user_id) do update set organization=excluded.organization""", (s["user"], ORG))
    batch, run, engine = formal_run(s)
    finish(s, run, engine, 3.0, hashlib.sha256(b"committed").hexdigest())
    job = uuid.uuid4()
    rpc(uri, "observer_enqueue_job", job, "score", run, None, ORG, secrets.token_urlsafe(32), "x", "y")
    rpc(uri, "observer_failover_job", job, other)
    assert query(uri, "select organization from private.observer_jobs where id=%s", (job,)) == [(other,)]
    assert query(uri, "select organization from private.observer_placements where user_id=%s", (s["user"],)) == [(ORG,)]


def test_switching_rescore_off_mid_flight_never_changes_a_score(formal):
    s = formal
    uri = s["uri"]
    batch, run, engine = formal_run(s)
    digest = hashlib.sha256(b"committed").hexdigest()
    finish(s, run, engine, 500.0, digest)
    job = claim_score_job(s, run)
    rpc(uri, "observer_set_hardening", None, False)
    rpc(uri, "observer_finish_job", job, "404", "1", Jsonb(receipt(run, digest, 5.0)), "")
    assert run_row(s, run) == ("scored", 500.0, "", "unverified")
    assert batch_row(s, batch) == ("scored", 500.0)


# ---------------------------------------------------------------------------
# Score job: recompute from the committed trace (no Docker)
# ---------------------------------------------------------------------------

class Files(BaseHTTPRequestHandler):
    def do_GET(self):
        data = self.server.files.get(self.path)
        self.send_response(200 if data is not None else 404)
        self.send_header("Content-Length", str(len(data or b"")))
        self.end_headers()
        self.wfile.write(data or b"")

    def log_message(self, *_args):
        pass


@pytest.fixture
def files():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Files)
    server.files = {}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.files, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def _zip_tree(root: Path) -> bytes:
    from project_platform.artifacts import pack_files
    from project_platform.package import ProjectFile
    return pack_files(tuple(ProjectFile(p.relative_to(root).as_posix(), p.read_bytes())
                            for p in sorted(root.rglob("*")) if p.is_file()))


def _score(files, base, tmp_path, scenario_zip, result_zip, digest, termination="agent_finished"):
    from project_platform.job_client import Http
    from project_platform.job_runner import score_job
    name = uuid.uuid4().hex
    files["/scenario-" + name], files["/result-" + name] = scenario_zip, result_zip
    root = tmp_path / ("job-" + name)
    root.mkdir()
    return score_job({"kind": "score", "job_id": str(uuid.uuid4()), "run_id": str(uuid.uuid4()),
                      "scenario_url": base + "/scenario-" + name,
                      "scenario_digest": hashlib.sha256(scenario_zip).hexdigest(),
                      "result_url": base + "/result-" + name, "decisions_digest": digest,
                      "termination_reason": termination}, root, Http(local=True))


@pytest.fixture(scope="module")
def card(tmp_path_factory):
    return build_bundle(tmp_path_factory.mktemp("hardening-card") / "bundle", card_id="H", stress=True, seed=91)


@pytest.mark.parametrize("mode,wallclock", [("greedy", 120), ("bad-after:6", 120), ("sleep-after:8", 4)])
def test_v4_score_job_replays_the_committed_actions_exactly(card, files, tmp_path, mode, wallclock):
    from project_platform.artifacts import pack_results
    from project_platform.diagnostics import ProjectJobFailure
    from project_platform.trusted_engine import result_summary
    from test_v4_engine import run_card

    result, digest, *_ = run_card(card, tmp_path / "out", mode, wallclock_seconds=wallclock)
    assert result["committed_action_count"] > 0
    files_, base = files
    scenario_zip, result_zip = _zip_tree(card), pack_results(tmp_path / "out")
    rescored = _score(files_, base, tmp_path, scenario_zip, result_zip, digest, result["termination_reason"])
    # Exactly the engine's own summary score, for every way a run can end.
    assert rescored["score"] == result_summary(result)["score"]
    assert rescored["decisions_digest"] == digest

    def rejected(result_zip, digest=digest):
        with pytest.raises(ProjectJobFailure) as failure:
            _score(files_, base, tmp_path, scenario_zip, result_zip, digest)
        return failure.value.diagnostics["code"]

    # A trace other than the committed one, or actions that do not replay to it.
    assert rejected(result_zip, hashlib.sha256(b"forged").hexdigest()) == "score_trace_mismatch"
    actions = tmp_path / "out" / "actions.jsonl"
    lines = actions.read_text().splitlines()
    actions.write_text("\n".join(lines[1:]) + "\n")
    assert rejected(pack_results(tmp_path / "out")) == "score_trace_mismatch"
    actions.unlink()
    assert rejected(pack_results(tmp_path / "out")) == "score_trace_mismatch"


def test_v3_score_job_rescores_the_committed_decisions(files, tmp_path):
    from challenge.challenge_workflow import ChallengeWorkflow
    from challenge.scenario_builder import generate_scenario
    from project_platform.artifacts import pack_results
    from project_platform.job_client import JobError
    from project_platform.trusted_engine import result_summary

    scenario = tmp_path / "scenario"
    generate_scenario(scenario, scenario_id="hardening-v3", seed=91, days=7, start_date="2026-10-05",
                      global_wallclock_seconds=120)
    workflow = ChallengeWorkflow(scenario)

    def provider(snapshot, _deadline):
        tiles = [t for t in snapshot["candidate_tiles"] if t["effective_weather"]["is_observable"]]
        return {"action": "observe", "tile_id": tiles[0]["tile_id"], "program": "BACKUP"} if tiles else {"action": "wait"}

    result = workflow.run(provider, wallclock_seconds=60)
    output = tmp_path / "out"
    workflow.write_outputs(output, result)
    digest = hashlib.sha256((output / "decisions.csv").read_bytes()).hexdigest()
    files_, base = files
    scenario_zip = _zip_tree(scenario)
    rescored = _score(files_, base, tmp_path, scenario_zip, pack_results(output), digest,
                      result["termination_reason"])
    assert rescored["score"]["total"] == result_summary(result)["score"]["total"]
    assert rescored["score"]["total"] != 0
    # The scenario bundle itself is digest-checked before anything runs.
    from project_platform.job_runner import score_job
    from project_platform.job_client import Http
    files_["/s"], files_["/r"] = scenario_zip, pack_results(output)
    with pytest.raises(JobError, match="archive_digest_mismatch"):
        score_job({"run_id": str(uuid.uuid4()), "scenario_url": base + "/s", "scenario_digest": "0" * 64,
                   "result_url": base + "/r", "decisions_digest": digest, "termination_reason": "survey_complete"},
                  tmp_path / "bad", Http(local=True))


# ---------------------------------------------------------------------------
# Egress forwarder and engine wiring
# ---------------------------------------------------------------------------

class ModelStub(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                     "key": self.headers.get("Idempotency-Key"),
                                     "cookie": self.headers.get("Cookie"), "body": json.loads(body)})
        payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": "OK"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


@pytest.fixture
def model_stub():
    # All interfaces: a forwarder container reaches it through the Docker host gateway.
    server = ThreadingHTTPServer(("0.0.0.0", 0), ModelStub)
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1", server.requests
    server.shutdown()
    server.server_close()


def test_forwarder_relays_only_posts_under_the_run_prefix_to_the_pinned_upstream(model_stub):
    from project_platform.egress import RestrictedEgress, render_proxy_script
    from project_platform.job_client import JobError

    base, requests = model_stub
    egress = RestrictedEgress(base, client_env={}, local=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # The production script, unchanged, with this run's prefix and upstream baked in.
    process = subprocess.Popen([sys.executable, "-u", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    process.stdin.write(render_proxy_script(base, egress.prefix, port))
    process.stdin.close()
    try:
        assert process.stdout.readline() == b"READY\n"
        url = f"http://127.0.0.1:{port}"

        def call(path, method="POST", body=b'{"model":"m"}', headers=None):
            request = urllib.request.Request(url + path, data=body if method == "POST" else None, method=method,
                headers={"Authorization": "Bearer obs_run.credential", "Content-Type": "application/json",
                         "Idempotency-Key": "call-1", "Cookie": "session=x", **(headers or {})})
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())

        status, body = call(egress.prefix + "/chat/completions")
        assert status == 200 and body["choices"][0]["message"]["content"] == "OK"
        assert requests[-1] == {"path": "/v1/chat/completions", "auth": "Bearer obs_run.credential",
                                "key": "call-1", "cookie": None, "body": {"model": "m"}}
        before = len(requests)
        assert call("/v1/chat/completions")[0] == 403                    # no run prefix
        assert call(egress.prefix + "/../../admin")[0] == 403            # traversal
        assert call(egress.prefix + "/a%2F..%2Fb")[0] == 403             # encoded characters
        assert call(egress.prefix + "/chat/completions", "GET")[0] == 405
        assert call(egress.prefix + "/chat/completions", "PUT")[0] == 405
        assert len(requests) == before
        # Chunked request bodies are relayed too.
        with socket.create_connection(("127.0.0.1", port)) as raw:
            raw.sendall((f"POST {egress.prefix}/chat/completions HTTP/1.1\r\nHost: x\r\n"
                         "Authorization: Bearer obs_run.credential\r\nTransfer-Encoding: chunked\r\n"
                         "Connection: close\r\n\r\n4\r\n{\"a\"\r\n3\r\n:1}\r\n0\r\n\r\n").encode())
            assert raw.recv(4096).startswith(b"HTTP/1.1 200")
        assert requests[-1]["body"] == {"a": 1}
    finally:
        process.kill()
        process.wait()
    # The upstream is pinned at construction: HTTPS observer-model base only.
    for value, local in (("http://169.254.169.254/latest/v1", True), ("https://user:pw@models.example/v1", False),
                         ("http://127.0.0.1:8000/v1", False), ("https://models.example/v1?x=1", False),
                         ("https://models.example/chat", False)):
        with pytest.raises(JobError, match="invalid_egress_upstream"):
            RestrictedEgress(value, client_env={}, local=local)
    assert egress.base_url.startswith("http://observer-proxy-") and egress.base_url.endswith(egress.prefix)


@pytest.mark.parametrize("restricted", [True, False])
def test_engine_job_applies_the_egress_switch_from_its_payload(monkeypatch, tmp_path, restricted):
    """The switch only changes the participant run's network and model base URL."""
    from project_platform import job_runner
    from project_platform.docker_runtime import DockerWorkspace

    events, started = [], {}

    class Egress:
        network = "observer-egress-" + "0" * 32
        base_url = "http://observer-proxy-x:8321/token/v1"
        anthropic_base_url = "http://observer-proxy-x:8321/token"

        def __init__(self, upstream, *, client_env, local):
            events.append(("egress", upstream, local))

        def pull(self):
            events.append("forwarder-pull")

        def start(self):
            events.append("forwarder-start")
            return self

        def close(self):
            events.append("forwarder-close")

    class Stop(Exception):
        pass

    def start(self, environment):
        started.update(network=self.network, env=dict(environment),
                       command=self._command(name="x", environment=environment, network=self.network or "bridge"))
        raise Stop()

    run = str(uuid.uuid4())
    manifest = {"schema_version": "observer-project-v1", "image": "python@sha256:" + "e" * 64, "run": ["python3"]}
    participant = {"run_credential": f"obs_{run}." + "p" * 43, "model_base_url": "https://platform.test/m/v1",
                   "source_digest": "d" * 64, "manifest": manifest}
    (tmp_path / "project").mkdir()
    monkeypatch.setattr(job_runner, "RestrictedEgress", Egress)
    monkeypatch.setattr(job_runner, "_participant_runtime", lambda payload, p, root, http, sealed=None: (
        DockerWorkspace(tmp_path / "project", job_runner.ProjectManifest.parse(manifest), manifest["image"]),
        {"OBSERVER_API_URL": "https://platform.test/s", "OBSERVER_RUN_TOKEN": p["run_credential"],
         "OBSERVER_RUN_ID": run, "OPENAI_BASE_URL": p["model_base_url"], "OPENAI_API_KEY": p["run_credential"]}))
    monkeypatch.setattr(job_runner, "download_project", lambda *a: ())
    monkeypatch.setattr(job_runner, "extract_project", lambda files, root: root.mkdir(parents=True))
    monkeypatch.setattr(job_runner, "is_v4_bundle", lambda root: True)
    monkeypatch.setattr(DockerWorkspace, "pull", lambda self: events.append("pull"))
    monkeypatch.setattr(DockerWorkspace, "build", lambda self: events.append("build"))
    monkeypatch.setattr(DockerWorkspace, "start", start)
    payload = {"run_id": run, "run_credential": f"obs_{run}." + "e" * 43, "session_url": "https://platform.test/s",
               "scenario_url": "https://platform.test/c", "scenario_digest": "c" * 64, "runtime_seconds": 900,
               "archive_url": "https://platform.test/a", "colocated": participant,
               **({"restricted_egress": True} if restricted else {})}
    with pytest.raises(job_runner.ProjectJobFailure):
        job_runner.engine_job(payload, tmp_path, job_runner.Http(local=False))
    command = started["command"]
    network = command[command.index("--network") + 1]
    if restricted:
        assert network == Egress.network and started["env"]["OPENAI_BASE_URL"] == Egress.base_url
        assert events[0] == ("egress", "https://platform.test/m/v1", False)
        assert set(events[1:4]) == {"forwarder-pull", "pull", "build"} and events[4:] == ["forwarder-start",
                                                                                         "forwarder-close"]
    else:
        # Unchanged: default bridge, the platform's model URL, no forwarder.
        assert network == "bridge" and started["env"]["OPENAI_BASE_URL"] == "https://platform.test/m/v1"
        assert events == ["pull", "build"]
    assert started["env"]["OPENAI_API_KEY"] == participant["run_credential"]


PROBE = r'''
import json, os, socket, sys, urllib.request
report = {}
for name, (host, port) in {"internet_ip": ("1.1.1.1", 443), "internet_dns": ("example.com", 443),
                           "host_gateway": (os.environ["PROBE_GATEWAY"], int(os.environ["PROBE_PORT"]))}.items():
    try:
        socket.create_connection((host, port), timeout=3).close()
        report[name] = "reachable"
    except OSError as error:
        report[name] = type(error).__name__
request = urllib.request.Request(os.environ["OPENAI_BASE_URL"] + "/chat/completions",
    data=json.dumps({"model": "test-model", "messages": [{"role": "user", "content": "hi"}]}).encode(),
    headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"})
with urllib.request.urlopen(request, timeout=30) as response:
    report["model"] = json.loads(response.read())["choices"][0]["message"]["content"]
print(json.dumps(report))
'''


@DOCKER
def test_restricted_container_reaches_the_model_proxy_and_nothing_else(model_stub):
    from project_platform.docker_runtime import DockerWorkspace  # noqa: F401 - same client environment
    from project_platform.egress import RestrictedEgress

    base, requests = model_stub
    env = {key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT") if key in os.environ}
    egress = RestrictedEgress(base, client_env=env, local=True)
    port = base.split(":")[2].split("/")[0]
    gateway = subprocess.run(["docker", "run", "--rm", "--add-host", "probe:host-gateway",
                              os.environ["OBSERVER_TEST_PYTHON_IMAGE"], "getent", "hosts", "probe"],
                             capture_output=True, text=True, env=env, check=True).stdout.split()[0]
    try:
        egress.start()
        probe = subprocess.run(["docker", "run", "--rm", "--network", egress.network, "--cap-drop=ALL",
                                "--user", "65534:65534", "-e", "OPENAI_BASE_URL=" + egress.base_url,
                                "-e", "OPENAI_API_KEY=obs_run.credential", "-e", "PROBE_GATEWAY=" + gateway,
                                "-e", "PROBE_PORT=" + port, os.environ["OBSERVER_TEST_PYTHON_IMAGE"],
                                "python3", "-c", PROBE], capture_output=True, text=True, env=env, timeout=120)
        assert probe.returncode == 0, probe.stderr + egress.log
        report = json.loads(probe.stdout)
        # No internet, no DNS, and not even the host that serves the model stub
        # directly: only the forwarder, which reaches only the pinned upstream.
        assert report["internet_ip"] != "reachable" and report["internet_dns"] != "reachable"
        assert report["host_gateway"] != "reachable"
        assert report["model"] == "OK"
        assert requests[-1]["path"] == "/v1/chat/completions" and requests[-1]["auth"] == "Bearer obs_run.credential"
        assert egress.network in subprocess.run(["docker", "network", "ls", "--format", "{{.Name}}"],
                                                capture_output=True, text=True, env=env).stdout
    finally:
        egress.close()
    # Nothing is left behind.
    assert egress.network not in subprocess.run(["docker", "network", "ls", "--format", "{{.Name}}"],
                                                capture_output=True, text=True, env=env).stdout
    assert egress.proxy_name not in subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"],
                                                   capture_output=True, text=True, env=env).stdout


# ---------------------------------------------------------------------------
# Anthropic Messages: the same proxy host, env allow-list and egress prefix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("base,expected", [
    ("https://platform.test/observer-model/v1", "https://platform.test/observer-model"),
    ("https://platform.test/observer-model/v1/", "https://platform.test/observer-model"),
    ("https://api.anthropic.com", "https://api.anthropic.com"),  # already has no /v1 suffix
])
def test_anthropic_base_strips_only_the_v1_suffix_the_sdk_appends_itself(base, expected):
    from project_platform.egress import anthropic_base
    assert anthropic_base(base) == expected


def test_docker_runtime_allows_anthropic_env_next_to_openai():
    from project_platform.docker_runtime import _RUNTIME_ENV
    assert {"OPENAI_BASE_URL", "OPENAI_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"} <= _RUNTIME_ENV


def test_restricted_egress_anthropic_base_url_shares_the_run_prefix_and_sidecar(model_stub):
    from project_platform.egress import RestrictedEgress

    base, _requests = model_stub
    egress = RestrictedEgress(base, client_env={}, local=True)
    # Same sidecar host; only the "/v1" segment the Anthropic SDK itself appends differs,
    # so both SDKs' own conventions land the request on the one fixed run prefix.
    assert egress.anthropic_base_url == egress.base_url[: -len("/v1")]
    assert egress.anthropic_base_url + "/v1/messages" == egress.base_url + "/messages"


def test_participant_runtime_injects_matching_anthropic_env_alongside_openai(monkeypatch, tmp_path):
    from project_platform import job_runner
    from project_platform.docker_runtime import DockerWorkspace
    from project_platform.package import ProjectFile, project_digest

    run = str(uuid.uuid4())
    manifest = {"schema_version": "observer-project-v1", "image": "python@sha256:" + "e" * 64, "run": ["python3"]}
    files = (ProjectFile("agent.py", b"print('ok')"),)
    participant = {"run_credential": f"obs_{run}." + "p" * 43, "model_base_url": "https://platform.test/m/v1",
                   "source_digest": project_digest(files), "manifest": manifest}
    monkeypatch.setattr(job_runner, "download_project", lambda *a: files)
    monkeypatch.setattr(job_runner, "extract_project", lambda files, root: root.mkdir(parents=True))
    runtime, environment = job_runner._participant_runtime(
        {"run_id": run, "session_url": "https://platform.test/s", "archive_url": "https://platform.test/a"},
        participant, tmp_path, job_runner.Http(local=True))
    assert isinstance(runtime, DockerWorkspace)
    assert environment["OPENAI_BASE_URL"] == "https://platform.test/m/v1"
    assert environment["OPENAI_API_KEY"] == participant["run_credential"]
    assert environment["ANTHROPIC_BASE_URL"] == "https://platform.test/m"
    assert environment["ANTHROPIC_API_KEY"] == participant["run_credential"]


@pytest.mark.parametrize("restricted", [True, False])
def test_engine_job_mirrors_the_egress_switch_onto_anthropic_base_url(monkeypatch, tmp_path, restricted):
    """ANTHROPIC_BASE_URL follows OPENAI_BASE_URL through the same restricted-egress switch."""
    from project_platform import job_runner
    from project_platform.docker_runtime import DockerWorkspace
    from project_platform.egress import anthropic_base
    from project_platform.package import ProjectFile, project_digest

    started = {}

    class Egress:
        network = "observer-egress-" + "1" * 32
        base_url = "http://observer-proxy-y:8321/other-token/v1"
        anthropic_base_url = "http://observer-proxy-y:8321/other-token"

        def __init__(self, upstream, *, client_env, local):
            pass

        def pull(self):
            pass

        def start(self):
            return self

        def close(self):
            pass

    class Stop(Exception):
        pass

    def start(self, environment):
        started.update(env=dict(environment))
        raise Stop()

    run = str(uuid.uuid4())
    manifest = {"schema_version": "observer-project-v1", "image": "python@sha256:" + "e" * 64, "run": ["python3"]}
    files = (ProjectFile("agent.py", b"print('ok')"),)
    participant = {"run_credential": f"obs_{run}." + "p" * 43, "model_base_url": "https://platform.test/m/v1",
                   "source_digest": project_digest(files), "manifest": manifest}
    monkeypatch.setattr(job_runner, "RestrictedEgress", Egress)
    monkeypatch.setattr(job_runner, "download_project", lambda *a: files)
    monkeypatch.setattr(job_runner, "extract_project", lambda files, root: root.mkdir(parents=True))
    monkeypatch.setattr(job_runner, "is_v4_bundle", lambda root: True)
    monkeypatch.setattr(DockerWorkspace, "pull", lambda self: None)
    monkeypatch.setattr(DockerWorkspace, "build", lambda self: None)
    monkeypatch.setattr(DockerWorkspace, "start", start)
    payload = {"run_id": run, "run_credential": f"obs_{run}." + "e" * 43, "session_url": "https://platform.test/s",
               "scenario_url": "https://platform.test/c", "scenario_digest": "c" * 64, "runtime_seconds": 900,
               "archive_url": "https://platform.test/a", "colocated": participant,
               **({"restricted_egress": True} if restricted else {})}
    with pytest.raises(job_runner.ProjectJobFailure):
        job_runner.engine_job(payload, tmp_path, job_runner.Http(local=False))
    if restricted:
        assert started["env"]["ANTHROPIC_BASE_URL"] == Egress.anthropic_base_url
    else:
        assert started["env"]["ANTHROPIC_BASE_URL"] == anthropic_base(participant["model_base_url"])
    assert started["env"]["ANTHROPIC_API_KEY"] == participant["run_credential"]


# ---------------------------------------------------------------------------
# End to end: real container, real Edge session + model proxy, rescore
# ---------------------------------------------------------------------------

from test_project_http import edge_stack  # noqa: F401,E402

E2E_AGENT = r'''
import json, os, socket, sys, urllib.request

def probe():
    report = {"observer_env": sorted(k for k in os.environ if k.startswith(("OBSERVER_", "OPENAI_")))}
    for name, (host, port) in {"internet_ip": ("1.1.1.1", 443), "internet_dns": ("example.com", 443)}.items():
        try:
            socket.create_connection((host, port), timeout=3).close()
            report[name] = True
        except OSError:
            report[name] = False
    request = urllib.request.Request(os.environ["OPENAI_BASE_URL"] + "/chat/completions",
        data=json.dumps({"model": "test-model", "messages": [{"role": "user", "content": "Reply OK"}],
                         "max_tokens": 8}).encode(),
        headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            report["model"] = json.loads(response.read())["choices"][0]["message"]["content"]
    except Exception as error:
        report["model"] = repr(error)
    print("PROBE " + json.dumps(report, sort_keys=True), file=sys.stderr, flush=True)

requests_seen = set()

def collect(value):
    # Observation requests (R_request) arrive as protocol records.
    if isinstance(value, dict):
        if value.get("record_type") == "observation_request":
            requests_seen.add(value["request_id"])
        for item in value.values():
            collect(item)
    elif isinstance(value, list):
        for item in value:
            collect(item)

for line in sys.stdin:
    message = json.loads(line)
    kind = message["message_type"]
    if kind == "initialize":
        probe()
        continue
    if kind == "finish":
        print("REQUESTS " + str(len(requests_seen)), file=sys.stderr, flush=True)
        sys.exit(0)
    collect(message["payload"])
    print(json.dumps({"protocol_version": message["protocol_version"], "message_type": "decision_response",
                      "decision_sequence": message["decision_sequence"], "action": "wait",
                      "duration_seconds": 3600}), flush=True)
'''


@pytest.mark.skipif(not os.environ.get("OBSERVER_TEST_PYTHON_IMAGE") or not os.environ.get("OBSERVER_DENO_BIN")
                    or not os.environ.get("SAC_POSTGREST_BIN"), reason="Container image, Deno and PostgREST required")
def test_v4_colocated_run_with_restricted_egress_and_a_forged_score(edge_stack, tmp_path, monkeypatch):  # noqa: F811
    """One v4 card over the real Edge session and model proxy, then its rescore.

    The participant container reaches the model proxy (and through it the
    upstream model) but no other address; observation requests still reach it;
    the engine's inflated self-report is replaced by the recomputed score.
    """
    from project_platform import job_runner
    from project_platform.artifacts import pack_files
    from project_platform.job_client import Http
    from project_platform.manifest import MANIFEST_NAME
    from project_platform.package import ProjectFile, project_digest, read_project_zip

    uri = edge_stack["harness"].db_uri
    user, team = identity(uri)
    phase, scenario_id = uuid.uuid4(), uuid.uuid4()
    query(uri, "insert into public.phases(id,slug,name_en,name_zh) values(%s,%s,'Cards','Cards')", (phase, str(phase)))
    query(uri, """insert into public.observer_phase_settings(phase_id,projects_enabled,runtime_seconds,
        model_token_limit,model_call_limit,model_concurrency,colocated) values(%s,true,900,100000,10,1,true)""", (phase,))
    query(uri, "insert into public.scenarios(id,slug,name) values(%s,%s,'Card')", (scenario_id, str(scenario_id)))
    query(uri, "insert into public.phase_scenarios values(%s,%s)", (phase, scenario_id))
    card = build_bundle(tmp_path / "card", card_id="E", stress=True, seed=17)
    scenario_zip = _zip_tree(card)
    query(uri, "insert into private.observer_scenario_bundles values(%s,%s,%s)",
          (scenario_id, f"{scenario_id}/card.zip", hashlib.sha256(scenario_zip).hexdigest()))
    query(uri, """insert into private.observer_installations
        (organization,organization_id,installation_id,repository_id,approved_sha,enabled)
        values(%s,'101',202,'303',%s,true) on conflict(organization) do update set enabled=true""", (ORG, "a" * 40))

    image = os.environ["OBSERVER_TEST_PYTHON_IMAGE"]
    manifest = {"schema_version": "observer-project-v1", "image": image, "run": ["python3", "-u", "agent.py"],
                "protocol": "jsonl-v4"}
    project = (ProjectFile("agent.py", E2E_AGENT.encode()), ProjectFile(MANIFEST_NAME, json.dumps(manifest).encode()))
    stored = {"/card.zip": scenario_zip, "/project.zip": pack_files(project)}

    class Stub(BaseHTTPRequestHandler):
        def do_GET(self):
            data = stored.get(self.path, b"")
            self.send_response(200 if self.path in stored else 404)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_PUT(self):
            stored[self.path] = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    stub = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{stub.server_port}"
        revision = rpc(uri, "observer_create_project", "Agent", "repository", "https://github.com/example/agent",
                       role="authenticated", user=user)
        query(uri, """update public.observer_revisions set status='reviewable',source_digest=%s,
            approval_digest=%s,manifest=%s,public_test='{"passed":true}' where id=%s""",
              (project_digest(project), "d" * 64, Jsonb(manifest), revision))
        rpc(uri, "observer_approve_revision", revision, "d" * 64, role="authenticated", user=user)
        query(uri, "insert into private.observer_materializations values(%s,%s,%s)",
              (revision, "github:" + ORG + "/participant-" + user.hex + "@" + "f" * 40, project_digest(project)))
        batch = rpc(uri, "observer_create_batch", phase, revision, role="authenticated", user=user)
        run = query(uri, "select id from public.observer_runs where batch_id=%s", (batch,))[0][0]
        participant, engine = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        rpc(uri, "observer_open_session", run, participant, engine)
        rpc(uri, "observer_enqueue_job", uuid.uuid4(), "engine", run, None, ORG, secrets.token_urlsafe(32), "x", "y")
        # Results go to the team's private repository, as in production.
        import project_platform.artifacts as artifacts
        from test_project_repository import LocalSnapshot
        remote = tmp_path / "private-results.git"
        subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)
        repository = LocalSnapshot(remote)
        monkeypatch.setattr(artifacts, "SnapshotRepository", lambda name, token: repository)
        credentials = lambda: {"full_name": repository.full_name, "token": "t", "artifact_id": str(run),  # noqa: E731
                               "kind": "engine"}
        payload = {"kind": "engine", "job_id": str(uuid.uuid4()), "run_id": str(run),
                   "run_credential": f"obs_{run}.{engine}", "session_url": edge_stack["urls"]["observer-session"],
                   "scenario_url": base + "/card.zip", "scenario_digest": hashlib.sha256(scenario_zip).hexdigest(),
                   "runtime_seconds": 120, "artifact_upload": {"kind": "github"},
                   "archive_url": base + "/project.zip", "restricted_egress": True,
                   "colocated": {"run_credential": f"obs_{run}.{participant}",
                                 "model_base_url": edge_stack["urls"]["observer-model"] + "/v1",
                                 "source_digest": project_digest(project), "manifest": manifest}}

        # A compromised engine inflates its self-reported score.
        honest = job_runner.result_summary

        def forged(result):
            summary = honest(result)
            summary["score"] = {**summary["score"], "total": summary["score"]["total"] + 1000}
            return summary

        with monkeypatch.context() as patch:
            patch.setattr(job_runner, "result_summary", forged)
            try:
                engine_receipt = job_runner.engine_job(payload, tmp_path / "engine", Http(local=True),
                                                       repository_credentials=credentials)
            except job_runner.ProjectJobFailure as failure:
                pytest.fail(json.dumps(failure.diagnostics))
        commit = engine_receipt["result_path"].rsplit("@", 1)[1]
        result_zip = subprocess.run(["git", "--git-dir", str(remote), "archive", "--format=zip", commit],
                                    capture_output=True, check=True).stdout
        artifact = {f.path: f.data for f in read_project_zip(result_zip)}
        workflow_result = json.loads(artifact["workflow_result.json"])
        true_total = workflow_result["score_report"]["total"]
        log = artifact["agent.log"].decode()
        probe = json.loads(next(line[6:] for line in log.splitlines() if line.startswith("PROBE ")))
        assert probe["internet_ip"] is False and probe["internet_dns"] is False
        assert probe["model"] == "OK"
        assert "OPENAI_BASE_URL" in probe["observer_env"]
        # The real observer-model Edge function proxied the call upstream, with its own key.
        assert any(r["auth"] == "Bearer " + edge_stack["upstream_key"] for r in edge_stack["requests"])
        # Observation requests reached the agent through the protocol as before.
        issued = workflow_result["score_report"]["counts"]["observation_requests_issued"]
        assert issued > 0 and f"REQUESTS {issued}" in log

        status, score, check = query(uri, "select status,score,score_check from public.observer_runs where id=%s",
                                     (run,))[0]
        assert (status, check) == ("scored", "pending") and score == pytest.approx(true_total + 1000)

        # The independent score job recomputes from the committed trace...
        files_ = {"/score-card": scenario_zip, "/score-result": result_zip}
        server = ThreadingHTTPServer(("127.0.0.1", 0), Files)
        server.files = files_
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            rescored = job_runner.score_job({
                "run_id": str(run), "scenario_url": f"http://127.0.0.1:{server.server_port}/score-card",
                "scenario_digest": hashlib.sha256(scenario_zip).hexdigest(),
                "result_url": f"http://127.0.0.1:{server.server_port}/score-result",
                "decisions_digest": engine_receipt["decisions_digest"], "termination_reason": workflow_result[
                    "termination_reason"]}, tmp_path / "rescore", Http(local=True))
        finally:
            server.shutdown()
            server.server_close()
        assert rescored["score"]["total"] == pytest.approx(true_total)
        # ...and the platform adopts it.
        job, nonce = uuid.uuid4(), secrets.token_urlsafe(32)
        rpc(uri, "observer_enqueue_job", job, "score", run, None, ORG, nonce, "x", "y")
        rpc(uri, "observer_claim_job", job, nonce, "404", "1", "303", "101", "a" * 40)
        rpc(uri, "observer_finish_job", job, "404", "1", Jsonb(rescored), "")
        assert query(uri, "select status,score,score_check from public.observer_runs where id=%s", (run,))[0] == (
            "scored", pytest.approx(true_total), "corrected")
        assert query(uri, "select score from public.observer_batches where id=%s", (batch,))[0][0] == pytest.approx(
            true_total)
    finally:
        stub.shutdown()
        stub.server_close()


def test_public_pool_score_job_opens_sealed_inputs_and_matches_the_private_rescore(card, files, tmp_path):
    """A rescore in a public repository receives the scenario and the result sealed to
    its in-memory key (never plaintext on a URL) and recomputes the same score."""
    pytest.importorskip("cryptography")
    from project_platform.artifacts import pack_results
    from project_platform.job_client import Http, JobClient, JobError
    from project_platform.job_runner import SealedTransfer, score_job
    from project_platform.sealing import SealKey, seal
    from test_v4_engine import run_card

    result, digest, *_ = run_card(card, tmp_path / "out", "greedy", wallclock_seconds=120)
    files_, base = files
    scenario_zip, result_zip = _zip_tree(card), pack_results(tmp_path / "out")
    plain = _score(files_, base, tmp_path, scenario_zip, result_zip, digest, result["termination_reason"])
    job, key = str(uuid.uuid4()), SealKey()
    name = uuid.uuid4().hex
    files_["/sealed-scenario-" + name] = seal(key.public, scenario_zip, "scenario:" + job)
    files_["/sealed-trace-" + name] = seal(key.public, result_zip, "trace:" + job)
    assert scenario_zip not in files_["/sealed-scenario-" + name] and b"decisions.csv" not in files_["/sealed-trace-" + name]
    client = JobClient("https://jobs.test/job", job, None, lambda: "identity", http=Http(local=True), seal_key=key)
    payload = {"kind": "score", "job_id": job, "run_id": plain["run_id"],
               "scenario_url": base + "/sealed-scenario-" + name, "scenario_digest": hashlib.sha256(scenario_zip).hexdigest(),
               "result_url": base + "/sealed-trace-" + name, "decisions_digest": digest,
               "termination_reason": result["termination_reason"]}
    root = tmp_path / "sealed-job"
    root.mkdir()
    sealed = score_job(payload, root, Http(local=True), sealed=SealedTransfer(client))
    assert sealed["score"] == plain["score"]
    # A plaintext (unsealed) input or one sealed for another purpose is refused.
    other = tmp_path / "other-job"
    other.mkdir()
    with pytest.raises(JobError):
        score_job({**payload, "result_url": base + "/sealed-scenario-" + name}, other, Http(local=True),
                  sealed=SealedTransfer(client))
