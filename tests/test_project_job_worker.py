"""Exercise the trusted job transport and artifact boundaries with real HTTP."""
from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from project_platform.artifacts import download_project, pack_files, pack_results, upload_artifact
from project_platform.job_client import GitHubIdentity, Http, JobClient, JobError, checked_url
from project_platform.job_runner import run_claimed
from project_platform.package import ProjectFile, project_digest, read_project_zip

JOB = "00000000-0000-4000-8000-000000000001"
RUN = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def server():
    state = {"requests": [], "claims": 0, "archive": b"", "uploads": []}

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, value):
            raw = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            state["requests"].append({"path": self.path, "auth": self.headers.get("Authorization")})
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/archive")
                self.end_headers()
            else:
                self.reply(200, state["archive"])

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append({"body": body, "auth": self.headers.get("Authorization")})
            if body["action"] == "agent_log":
                state.setdefault("logs", []).append(body)
                self.reply(200, {"data": {"accepted": True}})
            elif body["action"] == "claim":
                state["claims"] += 1
                if state["claims"] == 1:
                    self.reply(503, {"error": "temporary failure with secret URL"})
                else:
                    self.reply(200, {"data": {"kind": "execute", "job_id": JOB}})
            else:
                self.reply(200, {"data": {"accepted": True}})

        def do_PUT(self):
            state["uploads"].append(self.rfile.read(int(self.headers["Content-Length"])))
            state["upload_auth"] = self.headers.get("Authorization")
            self.reply(200, {})

        def log_message(self, *_args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", state
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_claim_retry_receipt_refreshes_identity_without_changing_claim(server, monkeypatch):
    import project_platform.job_client as job_client
    monkeypatch.setattr(job_client.time, "sleep", lambda _s: None)
    base, state = server
    identities = iter(("identity1", "identity2", "identity3"))
    client = JobClient(base + "/job", JOB, "n" * 43, lambda: next(identities), http=Http(local=True))
    assert client.claim() == {"kind": "execute", "job_id": JOB}
    client.complete({"status": "scored"})
    assert state["requests"][0]["body"] == state["requests"][1]["body"]
    assert [r["auth"] for r in state["requests"]] == ["Bearer identity1", "Bearer identity2", "Bearer identity3"]
    assert "nonce" not in state["requests"][2]["body"]


def test_artifact_integrity_redirect_limit_and_no_ambient_credentials(server, monkeypatch):
    base, state = server
    source = (ProjectFile("run.sh", b"#!/bin/sh\n", True), ProjectFile("data.bin", b"\x00\xff"))
    archive = pack_files(source)
    state["archive"] = archive
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "host-secret")
    http = Http(local=True)
    received = download_project(http, base + "/archive", hashlib.sha256(archive).hexdigest())
    assert project_digest(received) == project_digest(source)
    assert all(r["auth"] is None for r in state["requests"])
    with pytest.raises(JobError, match="archive_digest_mismatch"):
        download_project(http, base + "/archive", "a" * 64)
    with pytest.raises(JobError, match="job_response_too_large"):
        http.request(base + "/archive", limit=10)
    before = len(state["requests"])
    with pytest.raises(JobError, match="job_redirect_rejected"):
        http.request(base + "/redirect", headers={"Authorization": "Bearer private-token"})
    assert len(state["requests"]) == before + 1
    descriptor = {"url": base + "/upload", "path": JOB + "/" + RUN + "/result.zip"}
    assert upload_artifact(http, descriptor, archive) == descriptor["path"]
    assert state["uploads"] == [archive] and state["upload_auth"] is None
    with pytest.raises(JobError, match="invalid_artifact_destination"):
        upload_artifact(http, {**descriptor, "headers": {"Authorization": "admin-key"}}, archive)


def test_result_archive_excludes_outside_files_and_rejects_links(tmp_path):
    root = tmp_path / "output"
    root.mkdir()
    (root / "decisions.csv").write_bytes(b"night,action\n")
    (tmp_path / "secret").write_text("hidden scenario")
    assert [f.path for f in read_project_zip(pack_results(root))] == ["decisions.csv"]
    (root / "link").symlink_to(tmp_path / "secret")
    with pytest.raises(JobError, match="artifact_link_rejected"):
        pack_results(root)


def test_failed_worker_receipt_does_not_expose_exception_or_arbitrary_score(monkeypatch, tmp_path):
    import project_platform.job_runner as runner
    class Client:
        job_id = JOB
        http = Http(local=True)
        receipts = []
        def claim(self):
            return {"kind": "execute", "job_id": JOB}
        def complete(self, result, *, error=""):
            self.receipts.append({"result": result, "error": error})
    def fail(*_args):
        raise ValueError("https://private.test/?token=real-secret")
    monkeypatch.setattr(runner, "execute_job", fail)
    client = Client()
    with pytest.raises(JobError, match="^execute_job_failed$"):
        run_claimed("execute", client, tmp_path)
    assert client.receipts == [{"result": {'diagnostics':{'stage':'execute','code':'project_operation_failed','log':''}}, "error": "execute_job_failed"}]


def test_agent_log_is_sent_with_the_claim_nonce_and_bounded(server):
    import base64
    from project_platform.job_client import AGENT_LOG_MAX_BYTES
    base, state = server
    client = JobClient(base + "/job", JOB, "n" * 43, lambda: "identity", http=Http(local=True))
    text = "é" + "z" * (AGENT_LOG_MAX_BYTES + 10) + "\nlast line\n"
    client.agent_log(text)
    [body] = state["logs"]
    assert set(body) == {"action", "job_id", "nonce", "log"} and body["nonce"] == "n" * 43
    data = base64.b64decode(body["log"])
    assert len(data) == AGENT_LOG_MAX_BYTES and data.endswith(b"\nlast line\n")


class ReceiptClient:
    job_id = JOB
    http = Http(local=True)

    def __init__(self, fail_log=False):
        self.events = []
        self.fail_log = fail_log

    def claim(self):
        return {"kind": "execute", "job_id": JOB}

    def agent_log(self, text):
        self.events.append(("agent_log", text))
        if self.fail_log:
            raise JobError("job_http_503")

    def complete(self, result, *, error=""):
        self.events.append(("complete", result, error))


def test_executor_log_precedes_receipt_and_never_enters_it(monkeypatch, tmp_path):
    import project_platform.job_runner as runner
    from project_platform.diagnostics import ProjectJobFailure
    monkeypatch.setattr(runner, "execute_job", lambda *_: {"run_id": RUN, "status": "scored",
        "diagnostics": {"stage": "execute", "code": "completed", "log": "tail"}, runner.AGENT_LOG_KEY: "full log"})
    client = ReceiptClient()
    run_claimed("execute", client, tmp_path)
    assert client.events == [("agent_log", "full log"), ("complete", {"run_id": RUN, "status": "scored",
        "diagnostics": {"stage": "execute", "code": "completed", "log": "tail"}}, "")]
    # A failing project still delivers its log; a failing log upload changes nothing.
    def crash(*_):
        failure = ProjectJobFailure({"stage": "execute", "code": "project_operation_failed", "log": "tail"})
        failure.agent_log = "Traceback: crash"
        raise failure
    monkeypatch.setattr(runner, "execute_job", crash)
    client = ReceiptClient(fail_log=True)
    with pytest.raises(JobError, match="^execute_job_failed$"):
        run_claimed("execute", client, tmp_path)
    assert client.events == [("agent_log", "Traceback: crash"), ("complete", {"diagnostics":
        {"stage": "execute", "code": "project_operation_failed", "log": "tail"}}, "execute_job_failed")]


def test_job_rejects_insecure_and_forged_identity_destinations():
    for value in ("http://public.test/", "https://user:password@github.com/", "file:///tmp/config", "https://github.com:8443/x"):
        with pytest.raises(JobError):
            checked_url(value)
    for url in ("https://actions.githubusercontent.com.evil.test/id", "https://example.com/id", ""):
        with pytest.raises(JobError):
            GitHubIdentity({"ACTIONS_ID_TOKEN_REQUEST_URL": url, "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "secret"})


def test_control_export_contains_no_scenarios_secrets_or_participant_projects(tmp_path):
    import importlib.util
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("observer_export", root / "scripts/build-observer-control.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    destination = tmp_path / "control"
    inventory = module.export_control(destination)
    assert "project_platform/job_runner.py" in inventory
    assert "challenge/challenge_workflow.py" in inventory
    assert ".github/workflows/observer-execute.yml" in inventory
    assert all("reference/" not in path and ".secrets" not in path for path in inventory)
    assert not list(destination.rglob("*.csv"))
    assert not list(destination.rglob(".env*"))
    for path, digest in inventory.items():
        assert hashlib.sha256((destination / path).read_bytes()).hexdigest() == digest
    result = subprocess.run([sys.executable, "-m", "project_platform.job_runner", "--help"],
                            cwd=destination, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with pytest.raises(ValueError, match="existing files will not be overwritten"):
        module.export_control(destination)


def test_preparation_preserves_source_and_requires_public_test_before_approval(server,monkeypatch):
    from project_platform.manifest import MANIFEST_NAME, ProjectManifest
    from project_platform.adaptation import AdapterProposal
    import project_platform.preparation as prepare
    base,state=server
    manifest=ProjectManifest.parse({"schema_version":"observer-project-v1","image":"python:3.12-slim",
        "run":["python","agent.py"],"build":[["touch","/must-never-execute-on-host"]]})
    files=(ProjectFile(MANIFEST_NAME,manifest.canonical_bytes()),ProjectFile("agent.py",b"raise RuntimeError('source only')"))
    state["archive"]=pack_files(files)
    stored=[]
    class Repository:
        def __init__(self,name,token):
            assert name.endswith("/participant-"+"a"*32) and token=="scoped-repository-token"
        def store_revision(self,revision,source):
            stored.append((revision,source))
            return "b"*40,project_digest(source)
    monkeypatch.setattr(prepare,"SnapshotRepository",Repository)
    monkeypatch.setattr(prepare,"resolve_image",lambda image:"python@sha256:"+"c"*64)
    monkeypatch.setattr(prepare,"propose_adapter",lambda *_:pytest.fail("A supplied manifest must not trigger a model call"))
    result=prepare.prepare_project({"revision_id":JOB,"archive_url":base+"/archive",
        "source_digest":hashlib.sha256(state["archive"]).hexdigest(),
        "repository":{"full_name":"AGENTIC-OBSERVER26-runner-1/participant-"+"a"*32,"token":"scoped-repository-token"},
        "artifact_upload":{"url":base+"/upload","path":RUN+"/"+JOB+"/preview.zip"}},Http(local=True))
    assert result["status"]=="awaiting_public_test" and "public_test" not in result
    assert result["source_commit"]=="b"*40 and result["source_digest"]==project_digest(files)
    assert project_digest(stored[0][1])==project_digest(files)
    assert "token" not in json.dumps(result)
    materialized=read_project_zip(state["uploads"][0])
    assert next(f.data for f in materialized if f.path=="agent.py")==files[1].data
    proposal=AdapterProposal(project_digest(files),ProjectManifest.parse(result["manifest"]),(),result["explanation"])
    assert proposal.digest==result["approval_digest"]
    assert project_digest(proposal.materialize(files,confirmed_digest=proposal.digest))==result["materialized_digest"]


def test_image_resolution_binds_requested_repository_and_never_runs_it(monkeypatch):
    import project_platform.preparation as prepare
    from types import SimpleNamespace
    calls=[]
    digest="sha256:"+"c"*64
    def command(args,**kwargs):
        calls.append(args)
        assert kwargs["env"].get("SUPABASE_SERVICE_ROLE_KEY") is None
        return SimpleNamespace(returncode=0,stdout=json.dumps(["other@"+digest,"python@"+digest]).encode())
    monkeypatch.setattr(prepare.subprocess,"run",command)
    assert prepare.resolve_image("docker.io/library/python:3.12-slim")=="python@"+digest
    assert all(call[1] in ("pull","image") for call in calls)
    before=len(calls)
    for image in ("localhost:5000/project:latest","internal.corp/project:latest"):
        with pytest.raises(JobError,match="container_registry_not_supported"):
            prepare.resolve_image(image)
    assert len(calls)==before


def test_failed_initialization_preserves_transport_error_without_scoring(tmp_path):
    from challenge.scenario_builder import generate_scenario
    from project_platform.session import SessionError
    from project_platform.trusted_engine import run_session
    scenario=tmp_path/'scenario'
    generate_scenario(scenario,scenario_id='startup-failure',seed=31,days=7,start_date='2026-10-05')
    calls=[]
    class Client:
        def call(self,action,**kwargs):
            calls.append(action)
            raise SessionError('session_service_unavailable',503)
    output=tmp_path/'result'
    with pytest.raises(SessionError,match='session_service_unavailable'):
        run_session(scenario,output,Client(),wallclock_seconds=1)
    assert calls==['initialize']
    assert not output.exists()


def test_preparation_project_error_reaches_the_team_as_fixed_wording(monkeypatch, tmp_path):
    import project_platform.job_runner as runner
    from project_platform.manifest import ProjectError
    class Client:
        job_id = JOB
        http = Http(local=True)
        artifact_repository = None
        receipts = []
        def claim(self):
            return {"kind": "prepare", "job_id": JOB}
        def complete(self, result, *, error=""):
            self.receipts.append({"result": result, "error": error})
    def fail(*_args, **_kwargs):
        raise ProjectError("Automatic adaptation could not identify the entry point.")
    monkeypatch.setattr(runner, "prepare_project", fail)
    client = Client()
    with pytest.raises(JobError, match="^prepare_job_failed$"):
        run_claimed("prepare", client, tmp_path)
    assert client.receipts == [{"result": {'diagnostics': {'stage': 'prepare', 'code': 'project_error',
        'log': 'Automatic adaptation could not identify the entry point.'}}, "error": "prepare_job_failed"}]


@pytest.mark.parametrize("gameplay", [None, "v3", "v4", "v5"])
def test_preparation_passes_the_public_test_gameplay_to_the_adapter(server, monkeypatch, gameplay):
    import project_platform.preparation as prepare
    base, state = server
    state["archive"] = pack_files((ProjectFile("agent.py", b"def choose(s):\n    return {}\n"),))
    seen = []

    class Repository:
        def __init__(self, name, token):
            pass

        def store_revision(self, revision, source):
            return "b" * 40, project_digest(source)

    class Stop(Exception):
        pass

    def propose(source, model, client, *, gameplay="v3"):
        seen.append(gameplay)
        raise Stop()
    monkeypatch.setattr(prepare, "SnapshotRepository", Repository)
    monkeypatch.setattr(prepare, "propose_adapter", propose)
    payload = {"revision_id": JOB, "archive_url": base + "/archive",
               "source_digest": hashlib.sha256(state["archive"]).hexdigest(),
               "repository": {"full_name": "AGENTIC-OBSERVER26-runner-1/participant-" + "a" * 32, "token": "t"},
               "artifact_upload": {"url": base + "/upload", "path": RUN + "/" + JOB + "/preview.zip"},
               "model": "m", "model_base_url": "https://model.test/v1", "run_credential": "obs_x"}
    if gameplay is not None:
        payload["gameplay"] = gameplay
    if gameplay == "v5":
        with pytest.raises(JobError, match="invalid_job_payload"):
            prepare.prepare_project(payload, Http(local=True))
        assert seen == []
    else:
        with pytest.raises(Stop):
            prepare.prepare_project(payload, Http(local=True))
        assert seen == [gameplay or "v3"]
