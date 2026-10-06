from __future__ import annotations

import csv
import hashlib
import io
import json
import secrets
import uuid

import psycopg
import pytest

from challenge.contracts import DECISION_COLUMNS
from project_platform.local import export_decisions
from project_platform.manifest import ProjectError
from project_platform.model_client import ModelClient
from project_platform.session import SessionClient
from test_project_database import database, setup, identity, query, rpc, session  # noqa: F401


def test_local_credential_is_separate_from_engine_and_only_visible_to_matching_team(setup):
    s=setup;uri=s['uri'];other,_=identity(uri)
    batch=rpc(uri,'observer_create_batch',s['phase'],None,role='authenticated',user=s['user'])
    run=query(uri,'select id from public.observer_runs where batch_id=%s',(batch,))[0][0]
    participant,engine=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    ciphertext='encrypted participant capability only'
    rpc(uri,'observer_open_local_session',run,participant,engine,ciphertext)
    assert rpc(uri,'observer_local_access',run,s['user'])['encrypted_credential']==ciphertext
    assert rpc(uri,'observer_local_access',run,other) is None
    with pytest.raises(psycopg.Error,match='permission denied'):
        rpc(uri,'observer_local_access',run,s['user'],role='authenticated',user=other)
    with pytest.raises(psycopg.Error,match='invalid_or_expired_capability'):
        rpc(uri,'observer_abort_local',run,engine)
    rpc(uri,'observer_abort_local',run,participant)
    assert rpc(uri,'observer_local_access',run,s['user']) is None
    assert query(uri,'select status from public.observer_batches where id=%s',(batch,))==[('failed',)]


def test_export_contains_only_committed_rows_after_completion_and_matches_official_csv(setup,tmp_path):
    s=setup;uri=s['uri'];run,participant,engine=session(s)
    with pytest.raises(psycopg.Error,match='session_not_finished'):
        rpc(uri,'observer_export_decisions',run,participant)
    row=dict(zip(DECISION_COLUMNS,['1','slot1','wait','','','', '中文, quote " and newline\n']))
    output=io.StringIO(newline='');writer=csv.DictWriter(output,fieldnames=DECISION_COLUMNS,lineterminator='\n')
    writer.writeheader();writer.writerow(row);raw=output.getvalue().encode();digest=hashlib.sha256(raw).hexdigest()
    from psycopg.types.json import Jsonb
    query(uri,"""insert into private.observer_messages(run_id,sequence,observation,response,committed)
        values(%s,1,'{"private_future":"not for export"}','{"not_committed":"no"}',%s),
              (%s,2,'{}','{"not_committed":"no"}',null)""",(run,Jsonb({'rows':[row]}),run))
    query(uri,"update public.observer_runs set status='awaiting_csv',decisions_digest=%s,finished_at=now() where id=%s",(digest,run))
    with pytest.raises(psycopg.Error,match='invalid_or_expired_capability'):
        rpc(uri,'observer_export_decisions',run,engine)
    data=rpc(uri,'observer_export_decisions',run,participant)
    assert data=={'rows':[row],'decisions_digest':digest}
    class Client:
        def call(self,action):assert action=='decisions';return data
    target=tmp_path/'decisions.csv'
    assert export_decisions(Client(),target)==digest
    assert target.read_bytes()==raw
    with pytest.raises(FileExistsError):export_decisions(Client(),target)
    data['decisions_digest']='a'*64
    with pytest.raises(ProjectError,match='differs from the official trace'):
        export_decisions(Client(),tmp_path/'bad.csv')
    assert not (tmp_path/'bad.csv').exists()
    query(uri,"update public.observer_runs set finished_at=now()-interval '8 days' where id=%s",(run,))
    with pytest.raises(psycopg.Error,match='invalid_or_expired_capability'):
        rpc(uri,'observer_export_decisions',run,participant)


@pytest.mark.parametrize('model',[False,True])
def test_connection_reset_retries_the_same_request_without_reissuing_a_model_call(model):
    from http.client import RemoteDisconnected
    requests=[]
    class Opener:
        def open(self,request,**kwargs):
            requests.append(request)
            if len(requests)==1:raise RemoteDisconnected('Connection reset')
            return io.BytesIO(json.dumps({'data':{'accepted':True},'choices':[]}).encode())
    if model:
        client=ModelClient('http://127.0.0.1/v1','scoped-test-credential');client.opener=Opener()
        assert client({'model':'test-model','messages':[]})['choices']==[]
        assert requests[0].get_header('Idempotency-key')==requests[1].get_header('Idempotency-key')
        assert requests[0].get_header('Idempotency-key')
    else:
        client=SessionClient('http://127.0.0.1/session','scoped-test-credential');client.opener=Opener()
        assert client.call('respond',sequence=1,response={'action':'wait'})=={'accepted':True}
    assert len(requests)==2 and requests[0].data==requests[1].data


def test_catalog_transfer_budget_does_not_extend_decision_deadlines():
    import time
    timeouts=[]
    class Opener:
        def open(self,request,**kwargs):
            timeouts.append(kwargs['timeout'])
            return io.BytesIO(b'{"data":{}}')
    client=SessionClient('http://127.0.0.1/session','scoped-test-credential');client.opener=Opener()
    client.call('initialize',publication={})
    client.call('poll')
    client.call('poll',initialized=True)
    client.call('poll',scope='engine')
    client.call('respond',sequence=1,response={'action':'wait'})
    client.call('initialize',publication={},deadline=time.monotonic()+0.5)
    assert timeouts[:5]==[120,120,30,30,30]
    assert 0<timeouts[-1]<=0.5


def test_large_session_requests_get_the_transfer_timeout():
    from project_platform.session import SessionClient
    seen = []

    class Opener:
        def open(self, request, timeout):
            seen.append(timeout)
            class Response:
                headers = {}
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self, _limit): return b'{"data":{"response":null}}'
            return Response()

    client = SessionClient("https://platform.test/functions/v1/observer-session", "obs_x.y")
    client.opener = Opener()
    client.call("advance", sequence=1, observation={"blob": "x" * 2_000_000})
    client.call("advance", sequence=2, observation={"blob": "x"})
    assert seen == [client.catalog_timeout, client.timeout]


def test_session_requests_and_responses_are_gzipped_when_large():
    import gzip, json
    from project_platform.session import SessionClient
    sent = []
    big = {"data": {"observation": {"tiles": ["T%05d" % i for i in range(20000)]}}}

    class Opener:
        def open(self, request, timeout):
            sent.append((dict(request.header_items()), request.data))
            class Response:
                headers = {"Content-Encoding": "gzip"}
                def __enter__(self): return self
                def __exit__(self, *args): return False
                def read(self, _limit): return gzip.compress(json.dumps(big).encode())
            return Response()

    client = SessionClient("https://platform.test/functions/v1/observer-session", "obs_x.y")
    client.opener = Opener()
    assert client.call("advance", sequence=1, observation={"blob": "x" * 50_000}) == big["data"]
    headers, body = sent[0]
    assert headers["Content-encoding"] == "gzip" and headers["Accept-encoding"] == "gzip"
    assert json.loads(gzip.decompress(body))["observation"]["blob"] == "x" * 50_000
    client.call("poll", scope="engine")
    assert "Content-encoding" not in sent[1][0]


def test_gzipped_error_bodies_keep_their_code():
    import gzip, io, urllib.error
    from challenge.challenge_workflow import GlobalDeadlineExpired
    from project_platform.session import SessionClient

    class Opener:
        def open(self, request, timeout):
            body = gzip.compress(b'{"error":"session_deadline"}')
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized",
                                         {"Content-Encoding": "gzip"}, io.BytesIO(body))

    client = SessionClient("https://platform.test/functions/v1/observer-session", "obs_x.y")
    client.opener = Opener()
    with pytest.raises(GlobalDeadlineExpired):
        client.call("poll", scope="engine")


def test_model_provider_errors_are_explained_and_not_retried():
    import io, json, urllib.error
    from project_platform.model_client import ModelClient
    from project_platform.manifest import ProjectError
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            body = json.dumps({"error": {"type": "observer_error", "code": "model_provider_error",
                                         "message": "model_provider_error", "provider_status": 401}}).encode()
            raise urllib.error.HTTPError(request.full_url, 502, "Bad Gateway", {}, io.BytesIO(body))

    client = ModelClient("https://platform.test/functions/v1/observer-model/v1", "obs_x.y")
    client.opener = Opener()
    with pytest.raises(ProjectError, match=r"rejected the request \(HTTP 401\)"):
        client({"messages": [{"role": "user", "content": "hi"}]})
    assert len(calls) == 1



@pytest.mark.parametrize("statuses,succeeds", [((502,), True), ((429,), True), ((503, 503), False)])
def test_transient_provider_errors_are_retried_once_as_a_new_call(monkeypatch, statuses, succeeds):
    import io, json, time, urllib.error
    from project_platform.model_client import ModelClient, PROVIDER_RETRY_DELAY
    from project_platform.manifest import ProjectError
    sleeps, keys = [], []
    monkeypatch.setattr(time, "sleep", sleeps.append)

    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *_): self.close()

    class Opener:
        def open(self, request, timeout):
            keys.append(request.get_header("Idempotency-key"))
            if len(keys) <= len(statuses):
                body = json.dumps({"error": {"code": "model_provider_error",
                                             "provider_status": statuses[len(keys) - 1]}}).encode()
                raise urllib.error.HTTPError(request.full_url, 502, "Bad Gateway", {}, io.BytesIO(body))
            return Response(b'{"choices":[]}')

    client = ModelClient("https://platform.test/functions/v1/observer-model/v1", "obs_x.y")
    client.opener = Opener()
    if succeeds:
        assert client({"messages": []}) == {"choices": []}
    else:
        with pytest.raises(ProjectError, match=r"rejected the request \(HTTP 503\)"):
            client({"messages": []})
    # One retry only, after a pause, with a fresh idempotency key (never a 409 duplicate).
    assert len(keys) == 2 and keys[0] != keys[1] and sleeps == [PROVIDER_RETRY_DELAY]


def test_model_retry_after_a_lost_answer_reports_the_timeout_not_the_duplicate(monkeypatch):
    import io, json, time, urllib.error
    from project_platform.model_client import ModelClient
    from project_platform.manifest import ProjectError
    monkeypatch.setattr(time, "sleep", lambda _: None)
    timeouts = []

    class Opener:
        def open(self, request, timeout):
            timeouts.append(timeout)
            if len(timeouts) == 1:
                raise TimeoutError("timed out")
            body = json.dumps({"error": {"type": "observer_error", "code": "model_request_already_received"}}).encode()
            raise urllib.error.HTTPError(request.full_url, 409, "Conflict", {}, io.BytesIO(body))

    client = ModelClient("https://platform.test/functions/v1/observer-model/v1", "obs_x.y")
    client.opener = Opener()
    with pytest.raises(ProjectError, match=r"did not answer within 140 seconds\. Use a faster model") as error:
        client({"messages": [{"role": "user", "content": "hi"}]})
    assert "already received" not in str(error.value)
    # The client outlasts the proxy's own 120-125 s deadlines, below the Edge limit.
    assert timeouts == [140, 140]


@pytest.mark.parametrize("code,status,message", [
    ("model_provider_timeout", 504, r"longer than the two-minute limit.*observer\.project\.json"),
    ("model_provider_unavailable", 502, r"could not be reached or did not answer in time"),
    ("personal_api_not_connected", 503, r"No open Participate page answered.*observer\.project\.json"),
    ("personal_model_failed", 502, r"failed or did not answer in time through the open Participate page"),
])
def test_model_failures_after_acceptance_are_explained_and_not_retried(code, status, message):
    import io, json, urllib.error
    from project_platform.model_client import ModelClient
    from project_platform.manifest import ProjectError
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            body = json.dumps({"error": {"type": "observer_error", "code": code, "message": code}}).encode()
            raise urllib.error.HTTPError(request.full_url, status, "Error", {}, io.BytesIO(body))

    client = ModelClient("https://platform.test/functions/v1/observer-model/v1", "obs_x.y")
    client.opener = Opener()
    with pytest.raises(ProjectError, match=message):
        client({"messages": [{"role": "user", "content": "hi"}]})
    assert len(calls) == 1


def test_model_retry_conflict_reports_the_earlier_server_failure(monkeypatch):
    import io, time, urllib.error
    from project_platform.model_client import ModelClient
    from project_platform.manifest import ProjectError
    monkeypatch.setattr(time, "sleep", lambda _: None)
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            status = 503 if len(calls) == 1 else 409
            raise urllib.error.HTTPError(request.full_url, status, "Error", {}, io.BytesIO(b"{}"))

    client = ModelClient("https://platform.test/functions/v1/observer-model/v1", "obs_x.y")
    client.opener = Opener()
    with pytest.raises(ProjectError, match=r"^Model call failed \(HTTP 503\)\.$"):
        client({"messages": [{"role": "user", "content": "hi"}]})
    assert len(calls) == 2


def test_run_local_injects_anthropic_env_alongside_openai_in_native_mode(monkeypatch, tmp_path):
    """Native mode needs no Docker, so it is the cheapest path to the env dict run_local builds."""
    import project_platform.local as local

    project = tmp_path / "project"
    project.mkdir()
    (project / "observer.project.json").write_text(
        json.dumps({"schema_version": "observer-project-v1", "image": "debian:bookworm-slim", "run": ["./agent"]}))
    captured = {}

    def fake_execute(runtime, client, environment):
        captured.update(environment)
        raise RuntimeError("stop before any real session call")

    monkeypatch.setattr(local, "execute", fake_execute)
    run = str(uuid.uuid4())
    credential = f"obs_{run}." + "c" * 43
    with pytest.raises(RuntimeError, match="stop before any real session call"):
        local.run_local(project, "http://127.0.0.1:1/bogus-session", credential,
                         "https://platform.test/observer-model/v1", tmp_path / "out.csv", native=True)
    assert captured["OPENAI_BASE_URL"] == "https://platform.test/observer-model/v1"
    assert captured["OPENAI_API_KEY"] == credential
    assert captured["ANTHROPIC_BASE_URL"] == "https://platform.test/observer-model"
    assert captured["ANTHROPIC_API_KEY"] == credential
