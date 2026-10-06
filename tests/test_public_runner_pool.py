"""Public-repository runner pool: sealed transfers, runner client and export."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from project_platform.artifacts import pack_files  # noqa: E402
from project_platform.job_client import JobClient, JobError  # noqa: E402
from project_platform.job_runner import SealedTransfer, engine_job  # noqa: E402
from project_platform.package import ProjectFile  # noqa: E402
from project_platform.sealing import SealKey, decode_key, encode_key, seal  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
JOB = "00000000-0000-4000-8000-000000000001"
FIXED = bytes(range(1, 33))
# supabase/functions/_shared/observer-seal.ts, recipient private key bytes 1..32
# (the reverse direction is in observer-public-pool_test.ts).
FROM_BACKEND = ("T1NCMf0A2F5Ygpbj3dTpETZ2twAFvgmedcHjyxPcu7qf7JgZ+RWDYiniOfF6xSpX9jhDtpoWO57jqjA0cFjpEl4D/"
                "oNsH+q/uw==")


def test_sealing_round_trip_context_binding_and_backend_interop():
    key = SealKey()
    sealed = seal(key.public, b"result bytes", "result:" + JOB)
    assert key.open(sealed, "result:" + JOB) == b"result bytes"
    for context in ("scenario:" + JOB, "result:" + "0" * 36):
        with pytest.raises(JobError, match="invalid_sealed_input"):
            key.open(sealed, context)
    with pytest.raises(JobError, match="invalid_sealed_input"):
        SealKey().open(sealed, "result:" + JOB)
    with pytest.raises(JobError, match="invalid_sealed_input"):
        key.open(sealed[:-1] + bytes([sealed[-1] ^ 1]), "result:" + JOB)
    fixed = SealKey(FIXED)
    assert fixed.public_text == "B6N8vBQgk8i3VdwbEOhstCY3StFqqFPtC9_AsrhtHHw"
    assert fixed.open(base64.b64decode(FROM_BACKEND), "scenario:" + JOB) == b"sealed by the backend"
    assert decode_key(encode_key(key.public)) == key.public
    for bad in ("", "a" * 42, "a" * 44, None, "+" * 43):
        with pytest.raises(JobError):
            decode_key(bad)


class FakeHttp:
    local = False

    def __init__(self, responses=None):
        self.requests, self.responses = [], dict(responses or {})

    def json(self, url, *, body=None, bearer=None, max_body=0, timeout=0):
        self.requests.append(("json", url, body))
        return self.responses[body["action"]](body)

    def request(self, url, *, data=None, headers=None, method="GET", limit=0, timeout=0):
        self.requests.append((method, url, data))
        return self.responses[url] if method == "GET" else b"{}"


def test_public_client_claims_without_a_nonce_and_opens_its_sealed_payload():
    key = SealKey()
    payload = {"kind": "engine", "job_id": JOB, "secret": "run credential"}
    http = FakeHttp({"claim": lambda body: {"data": {"sealed": base64.b64encode(
        seal(decode_key(body["runner_key"]), json.dumps(payload).encode(), "claim:" + JOB)).decode()}},
        "store_result": lambda body: {"data": {"result_path": "github:org/participant@" + "a" * 40}}})
    client = JobClient("https://jobs.test/job", JOB, None, lambda: "identity", http=http, seal_key=key)
    assert client.claim() == payload
    assert http.requests[0][2] == {"action": "claim", "job_id": JOB, "runner_key": key.public_text}
    assert "nonce" not in http.requests[0][2]
    assert client.store_sealed_result() == "github:org/participant@" + "a" * 40
    # A claim not sealed to this job's key is refused.
    other = FakeHttp({"claim": lambda body: {"data": {"sealed": base64.b64encode(
        seal(SealKey().public, b"{}", "claim:" + JOB)).decode()}}})
    with pytest.raises(JobError):
        JobClient("https://jobs.test/job", JOB, None, lambda: "identity", http=other, seal_key=key).claim()
    # Exactly one of nonce (private pool) and runner key (public pool).
    for nonce, seal_key in ((None, None), ("n" * 43, key)):
        with pytest.raises(JobError, match="invalid_job_nonce"):
            JobClient("https://jobs.test/job", JOB, nonce, lambda: "identity", http=http, seal_key=seal_key)


def test_sealed_transfer_opens_inputs_and_seals_the_result_for_the_backend():
    key, backend = SealKey(), SealKey()
    project = pack_files((ProjectFile("agent.py", b"print('hi')"),))
    http = FakeHttp({"https://storage.test/project": seal(key.public, project, "project:" + JOB),
                     "result_upload": lambda body: {"data": {"url": "https://storage.test/upload",
                                                             "path": "sealed/" + JOB + "/result.zip"}},
                     "store_result": lambda body: {"data": {"result_path": "github:org/participant@" + "b" * 40}}})
    client = JobClient("https://jobs.test/job", JOB, None, lambda: "identity", http=http, seal_key=key)
    transfer = SealedTransfer(client)
    files = transfer.download(http, "https://storage.test/project", "project", hashlib.sha256(project).hexdigest())
    assert [f.path for f in files] == ["agent.py"]
    with pytest.raises(JobError, match="archive_digest_mismatch"):
        transfer.download(http, "https://storage.test/project", "project", "0" * 64)
    with pytest.raises(JobError, match="invalid_sealed_input"):
        transfer.download(http, "https://storage.test/project", "scenario")
    payload = {"artifact_upload": {"kind": "sealed", "path": "sealed/" + JOB + "/result.zip"},
               "result_key": backend.public_text}
    assert transfer.publish(http, payload, b"result zip") == "github:org/participant@" + "b" * 40
    assert [r[2]["action"] for r in http.requests if r[0] == "json"] == ["result_upload", "store_result"]
    with pytest.raises(JobError, match="invalid_artifact_destination"):
        transfer.publish(http, {**payload, "artifact_upload": {"kind": "sealed", "path": "sealed/other"}}, b"x")
    method, url, body = [r for r in http.requests if r[0] == "PUT"][0]
    assert url == "https://storage.test/upload" and b"result zip" not in body
    assert backend.open(body, "result:" + JOB) == b"result zip"


def test_engine_refuses_a_sealed_mismatch_before_downloading(tmp_path):
    payload = {"artifact_upload": {"kind": "sealed", "path": "y"}}
    with pytest.raises(JobError, match="sealed_transfer_mismatch"):
        engine_job(payload, tmp_path, FakeHttp())


def test_public_export_has_engine_and_score_workflows_with_only_an_opaque_input(tmp_path):
    import importlib.util
    import subprocess
    import sys
    spec = importlib.util.spec_from_file_location("observer_export", ROOT / "scripts/build-observer-control.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    destination = tmp_path / "public"
    inventory = module.export_public(destination)
    workflows = [p for p in inventory if p.startswith(".github/")]
    assert workflows == [".github/workflows/observer-engine.yml", ".github/workflows/observer-score.yml"]
    assert "project_platform/sealing.py" in inventory and "public-pool-requirements.txt" in inventory
    assert not any(p.startswith("tests/") for p in inventory)
    for path, digest in inventory.items():
        assert hashlib.sha256((destination / path).read_bytes()).hexdigest() == digest
    for name in ("observer-engine.yml", "observer-score.yml"):
        workflow = (destination / ".github/workflows" / name).read_text()
        for forbidden in ("job_nonce", "upload-artifact", "actions/cache", "GITHUB_STEP_SUMMARY", "secrets.",
                          "pull_request", "push:", "refs/heads/main", "cache:"):
            assert forbidden not in workflow
        assert workflow.count("> /dev/null 2>&1") == 2 and "OBSERVER_POOL: public" in workflow
        assert "job_runner " + name.split("-")[1].split(".")[0] + " > /dev/null" in workflow
    requirements = (destination / "public-pool-requirements.txt").read_text()
    pins = [line.split("==")[0] for line in requirements.splitlines() if "==" in line]
    assert pins == ["cryptography", "cffi", "pycparser"] and requirements.count("--hash=sha256:") == 5
    result = subprocess.run([sys.executable, "-m", "project_platform.job_runner", "--help"],
                            cwd=destination, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_sealed_result_upload_retries_an_edge_520_with_a_fresh_signed_url(monkeypatch):
    # 2026-10-04 run b9285236: Storage answered the result PUT with a Cloudflare 520.
    import project_platform.job_client as job_client
    monkeypatch.setattr(job_client.time, "sleep", lambda _s: None)
    key, backend = SealKey(), SealKey()
    issued = iter(range(10))
    http = FakeHttp({"result_upload": lambda body: {"data": {"url": "https://storage.test/upload/" + str(next(issued)),
                                                             "path": "sealed/" + JOB + "/result.zip"}},
                     "store_result": lambda body: {"data": {"result_path": "github:org/participant@" + "b" * 40}}})
    puts = []

    def request(url, *, data=None, headers=None, method="GET", limit=0, timeout=0):
        puts.append(url)
        if len(puts) == 1:
            raise JobError("job_http_520")
        return b"{}"

    http.request = request
    client = JobClient("https://jobs.test/job", JOB, None, lambda: "identity", http=http, seal_key=key)
    payload = {"artifact_upload": {"kind": "sealed", "path": "sealed/" + JOB + "/result.zip"},
               "result_key": backend.public_text}
    assert SealedTransfer(client).publish(http, payload, b"result zip") == "github:org/participant@" + "b" * 40
    assert puts == ["https://storage.test/upload/0", "https://storage.test/upload/1"]


def test_storing_a_sealed_result_rides_out_transient_backend_errors(monkeypatch):
    import project_platform.job_client as job_client
    slept = []
    monkeypatch.setattr(job_client.time, "sleep", slept.append)
    errors = [JobError(code) for code in ("job_http_520", "job_network_error", "job_http_429", "job_http_503")]
    answers = iter(errors + [{"data": {"result_path": "github:org/participant@" + "c" * 40}}])
    bodies = []

    def store(body):
        bodies.append(body)
        value = next(answers)
        if isinstance(value, Exception):
            raise value
        return value

    class Flaky(FakeHttp):
        def json(self, url, *, body=None, bearer=None, max_body=0, timeout=0):
            return store(body)

    client = JobClient("https://jobs.test/job", JOB, None, lambda: "identity", http=Flaky(), seal_key=SealKey())
    assert client.store_sealed_result() == "github:org/participant@" + "c" * 40
    assert all(body == bodies[0] for body in bodies) and len(bodies) == 5
    assert 25 < sum(slept) < 60
    # About a minute of retries, then the transient error is reported.
    slept.clear()
    down = JobClient("https://jobs.test/job", JOB, None, lambda: "identity", seal_key=SealKey(),
                     http=type("H", (FakeHttp,), {"json": lambda self, url, **kw: (_ for _ in ()).throw(
                         JobError("job_http_520"))})())
    with pytest.raises(JobError, match="job_http_520"):
        down.result_upload()
    assert len(slept) == len(job_client.RETRY_DELAYS) and 50 < sum(slept) < 90
    # A refusal is final at once.
    slept.clear()
    hopeless = JobClient("https://jobs.test/job", JOB, None, lambda: "identity", seal_key=SealKey(),
                         http=type("H", (FakeHttp,), {"json": lambda self, url, **kw: (_ for _ in ()).throw(
                             JobError("job_http_403"))})())
    with pytest.raises(JobError, match="job_http_403"):
        hopeless.store_sealed_result()
    assert slept == []
