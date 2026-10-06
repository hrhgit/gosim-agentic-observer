"""Project contracts and actual language-neutral transports (no production data)."""
from __future__ import annotations

import io
import json
import os
import shutil
import stat
import sys
import time
import zipfile
from pathlib import Path

import pytest

from challenge.challenge_workflow import GlobalDeadlineExpired
from challenge.contracts import PARTICIPANT_PROTOCOL_VERSION
from project_platform.adaptation import AdapterProposal
from project_platform.docker_runtime import DockerWorkspace
from project_platform.manifest import ProjectError, ProjectManifest
from project_platform.package import ProjectFile, extract_project, project_digest, read_project_zip
from project_platform.transport import ExecutionError, JsonlTransport


def manifest(**changes):
    return {"schema_version": "observer-project-v1", "image": "debian:bookworm-slim",
            "run": ["./agent"], **changes}


def archive(entries):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, content in entries:
            z.writestr(path, content)
    return out.getvalue()


def test_complete_non_python_project_round_trip(tmp_path):
    data = archive([("my-project/src/main.rs", "fn main() {}"),
                    ("my-project/Cargo.toml", '[package]\nname="survey"'),
                    ("my-project/observer.project.json", json.dumps(manifest(run=["target/release/survey"]))),
                    ("my-project/.env.example", "MODEL_BASE_URL=example")])
    files = read_project_zip(data)
    dest = tmp_path / "source"
    extract_project(files, dest)
    assert (dest / "src/main.rs").read_text() == "fn main() {}"
    assert len(project_digest(files)) == 64
    assert project_digest(files) == project_digest(reversed(files))
    with pytest.raises(FileExistsError):
        extract_project(files, dest)


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:/drive", "src/../../escape",
                                 "src\\escape", "src//agent", ".git/config",
                                 ".env", "nested/.env.production"])
def test_zip_rejects_unsafe_paths_and_credential_files(path):
    with pytest.raises(ProjectError):
        read_project_zip(archive([(path, "not-a-real-key")]))


def test_zip_rejects_symlinks():
    entry = zipfile.ZipInfo("project/link")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ProjectError, match="links"):
        read_project_zip(archive([(entry, "/etc/passwd")]))


@pytest.mark.parametrize("entries", [
    [("agent.py", "one"), ("AGENT.py", "two")],
    [("project", "one"), ("project/agent", "two")],
])
def test_zip_rejects_colliding_paths(entries):
    with pytest.raises(ProjectError):
        read_project_zip(archive(entries))


def test_zip_expansion_limit(monkeypatch):
    monkeypatch.setattr("project_platform.package.MAX_EXPANDED_BYTES", 1024)
    with pytest.raises(ProjectError, match="Expanded"):
        read_project_zip(archive([("large", b"x" * 4096)]))


def test_content_and_executable_bit_are_part_of_revision():
    a = (ProjectFile("agent", b"one"),)
    assert project_digest(a) != project_digest((ProjectFile("agent", b"two"),))
    assert project_digest(a) != project_digest((ProjectFile("agent", b"one", True),))


@pytest.mark.parametrize("changes", [
    {"run": "python agent.py"}, {"run": []}, {"run": ["-c", "oops"]},
    {"working_directory": "../other"}, {"image": "--privileged"},
    {"protocol": "unknown"}, {"environment": {"SUPABASE_SERVICE_ROLE_KEY": "secret"}},
    {"environment": {"OPENAI_API_KEY": "secret"}}, {"environment": {"OBSERVER_RUN_ID": "another"}},
    {"privileged": True},
])
def test_invalid_or_privileged_manifest_is_rejected(changes):
    with pytest.raises(ProjectError):
        ProjectManifest.parse(manifest(**changes))


def test_manifest_digest_is_canonical_and_immutable():
    one = ProjectManifest.parse(manifest(environment={"B": "two", "A": "one"}))
    two = ProjectManifest.parse(manifest(environment={"A": "one", "B": "two"}))
    assert one.digest == two.digest
    assert ProjectManifest.parse(one.canonical_bytes()) == one


def proposal(source, **changes):
    response = {"manifest": manifest(run=["python", ".observer-adapter/main.py"]),
                "files": [{"path": ".observer-adapter/main.py", "content": "import original"}],
                "explanation": "Wrap the existing decision entry point without changing its strategy.",
                **changes}
    return AdapterProposal.parse(response, source)


def test_adapter_requires_exact_source_and_exact_reviewed_version():
    source = (ProjectFile("original.py", b"def decide(): return 'wait'"),)
    p = proposal(source)
    frozen = p.materialize(source, confirmed_digest=p.digest)
    assert next(f.data for f in frozen if f.path == "original.py") == source[0].data
    assert any(f.path == "observer.project.json" for f in frozen)
    with pytest.raises(ProjectError, match="changed"):
        p.materialize((ProjectFile("original.py", b"new strategy"),), confirmed_digest=p.digest)
    with pytest.raises(ProjectError, match="changed"):
        p.materialize(source, confirmed_digest="outdated-approval")


def test_model_cannot_replace_strategy_or_add_workflow():
    source = (ProjectFile("original.py", b"original"),)
    for path in ["original.py", ".github/workflows/run.yml", "../original.py"]:
        with pytest.raises(ProjectError):
            proposal(source, files=[{"path": path, "content": "replacement"}])


def test_model_cannot_shadow_original_adapter_path():
    source = (ProjectFile(".observer-adapter/main.py", b"original"),)
    with pytest.raises(ProjectError):
        proposal(source)


def test_launcher_does_not_pass_platform_secrets_or_privileges(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "do-not-pass")
    monkeypatch.setenv("GH_TOKEN", "do-not-pass")
    image = "debian@sha256:" + "a" * 64
    p = ProjectManifest.parse(manifest(image=image))
    runtime = DockerWorkspace(tmp_path, p, image)
    transport = runtime.start({"OBSERVER_RUN_TOKEN": "scoped-run-token"})
    assert "--privileged" not in transport.command
    assert "--cap-drop=ALL" in transport.command and "--read-only" in transport.command
    assert "docker.sock" not in " ".join(transport.command)
    assert "scoped-run-token" not in " ".join(transport.command)
    assert "SUPABASE_SERVICE_ROLE_KEY" not in transport.environment
    assert "GH_TOKEN" not in transport.environment
    assert transport.environment["OBSERVER_RUN_TOKEN"] == "scoped-run-token"
    with pytest.raises(ProjectError):
        runtime.start({"SUPABASE_SERVICE_ROLE_KEY": "forbidden"})
    with pytest.raises(ProjectError, match="digest"):
        DockerWorkspace(tmp_path, p, "debian:latest")


def python_process(tmp_path, code):
    script = tmp_path / "agent.py"
    script.write_text(code)
    return JsonlTransport([sys.executable, "-u", str(script)], cwd=tmp_path,
                          environment={"PATH": os.environ["PATH"]})


def test_large_public_catalog_roundtrip_and_process_initialization(tmp_path):
    from project_platform.publication import encode_publication, decode_publication
    # Formal catalogs exceed the ordinary 16 MiB decision-message boundary.
    publication = {"target_catalog": [{"target_id": "catalog-row", "value": "x" * 100}] * 140000}
    assert len(json.dumps(publication)) > 16 * 1024 * 1024
    encoded = encode_publication(publication)
    assert decode_publication(encoded) == publication
    assert encode_publication(publication) == encoded  # retries use identical bytes
    with pytest.raises(ValueError, match="integrity"):
        decode_publication({**encoded, "sha256": "0" * 64})
    with pytest.raises(ValueError, match="integrity"):
        decode_publication({**encoded, "uncompressed_bytes": 100})
    with pytest.raises(ValueError, match="invalid"):
        decode_publication({**encoded, "uncompressed_bytes": 1000 * 1024 * 1024})
    p = python_process(tmp_path, """import json,sys
m=json.loads(sys.stdin.readline())
assert len(m['payload']['target_catalog'])==140000
print(json.dumps({'catalog_received':True}),flush=True)
""")
    try:
        p.publish_initial(decode_publication(encoded))
        assert p.receive(time.monotonic() + 5) == {"catalog_received": True}
        # The larger initialization allowance does not increase decision limits.
        with pytest.raises(ExecutionError, match="size limit"):
            p.send(publication, time.monotonic() + 5)
    finally:
        p.close(force=True)


def test_transport_rejects_wrong_sequence(tmp_path):
    code = """import json, sys
for line in sys.stdin:
 m=json.loads(line)
 if m['message_type']=='initialize': continue
 print(json.dumps({'protocol_version':'participant-agent-protocol-v2',
 'message_type':'decision_response','decision_sequence':99,'action':'wait'}), flush=True)
"""
    p = python_process(tmp_path, code)
    try:
        p.publish_initial({})
        with pytest.raises(ExecutionError, match="current protocol"):
            p({"decision_sequence": 1}, time.monotonic() + 5)
    finally:
        p.close(force=True)


def test_transport_cuts_off_stdout_flood(tmp_path):
    p = python_process(tmp_path, "import sys\nsys.stdin.readline()\nsys.stdout.write('x'*700000)\nsys.stdout.flush()")
    try:
        p.send({"test": 1}, time.monotonic() + 5)
        with pytest.raises(ExecutionError, match="size limit"):
            p.receive(time.monotonic() + 5)
    finally:
        p.close(force=True)


def test_transport_deadline_terminates_nonresponsive_process(tmp_path):
    p = python_process(tmp_path, "import sys,time\nsys.stdin.readline()\ntime.sleep(2)")
    try:
        started = time.monotonic()
        p.send({"test": 1}, started + 0.2)
        with pytest.raises(GlobalDeadlineExpired):
            p.receive(started + 0.2)
        assert time.monotonic() - started < 1.5
    finally:
        p.close(force=True)


def test_stderr_is_bounded_and_scoped_token_is_redacted(tmp_path):
    p = python_process(tmp_path, "import sys\nsys.stderr.write('x'*200000+'test-run-secret')\nsys.stderr.flush()")
    p.redactions = ("test-run-secret",)
    p.start()
    p.process.wait(timeout=5)
    p.close()
    assert len(p.log.encode()) <= 65536
    assert "test-run-secret" not in p.log and p.log.endswith("[REDACTED]")
    assert p.log_truncated


def test_project_stderr_keeps_a_two_megabyte_tail_for_agent_log(tmp_path):
    from project_platform.transport import AGENT_LOG_BYTES
    body = "import sys\nfor i in range(40000): sys.stderr.write(f'line {i:06d} ' + 'y'*80 + '\\n')\nsys.stderr.flush()"
    p = python_process(tmp_path, body)
    p.log_limit = AGENT_LOG_BYTES
    p.start()
    p.process.wait(timeout=10)
    p.close()
    assert AGENT_LOG_BYTES - 200 <= len(p.log.encode()) <= AGENT_LOG_BYTES
    assert p.log.endswith("line 039999 " + "y" * 80 + "\n") and "line 000000" not in p.log
    assert p.log_truncated
    small = python_process(tmp_path, "import sys\nsys.stderr.write('hello\\n')")
    small.start(); small.process.wait(timeout=5); small.close()
    assert small.log == "hello\n" and not small.log_truncated


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is needed only for the cross-language test")
def test_python_runner_exchanges_messages_with_a_javascript_project(tmp_path):
    script = tmp_path / "agent.cjs"
    script.write_text("""
const rl = require('node:readline').createInterface({input:process.stdin});
rl.on('line', line => {
 const m=JSON.parse(line);
 if(m.message_type==='initialize')return;
 console.log(JSON.stringify({protocol_version:m.protocol_version,message_type:'decision_response',
 decision_sequence:m.decision_sequence,action:'wait',reason:'JavaScript project'}));
});
""")
    p = JsonlTransport([shutil.which("node"), str(script)], cwd=tmp_path,
                       environment={"PATH": os.environ["PATH"]})
    try:
        p.publish_initial({"schema_version": "initial-publication-v2"})
        for sequence in [1, 2, 3]:
            # Generous per-response budget: node startup on a loaded CI runner can take seconds.
            response = p({"decision_sequence": sequence}, time.monotonic() + 30)
            assert response["action"] == "wait" and response["reason"] == "JavaScript project"
    finally:
        p.close()


# --- Graceful finish -------------------------------------------------------

DEMO_SCENARIO = Path(__file__).resolve().parents[1] / "archive" / "starter_kit_v3" / "scenarios" / "demo-week"

_WAIT_AGENT_HEAD = """import json,sys
for line in sys.stdin:
 m=json.loads(line)
 t=m['message_type']
 if t=='initialize': continue
 if t=='finish':
"""
_WAIT_AGENT_TAIL = """ else:
  print(json.dumps({'protocol_version':m['protocol_version'],'message_type':'decision_response',
  'decision_sequence':m['decision_sequence'],'action':'wait','reason':'test agent'}),flush=True)
"""

# (a) handles finish: records the message on stderr and exits 0 on its own
FINISH_AWARE_AGENT = _WAIT_AGENT_HEAD + """  sys.stderr.write('FINISH-MSG '+json.dumps(m,sort_keys=True)+'\\n')
  sys.stderr.flush()
  sys.exit(0)
""" + _WAIT_AGENT_TAIL

# (b) ignores finish and keeps running until the platform stops it
FINISH_IGNORING_AGENT = _WAIT_AGENT_HEAD + """  sys.stderr.write('IGNORING-FINISH\\n')
  sys.stderr.flush()
  import time
  time.sleep(120)
""" + _WAIT_AGENT_TAIL

# (c) crashes on the unknown message, with junk on stdout that must be ignored
FINISH_CRASHING_AGENT = _WAIT_AGENT_HEAD + """  sys.stdout.write('junk after finish is ignored\\n')
  sys.stdout.flush()
  raise SystemExit(3)
""" + _WAIT_AGENT_TAIL


def wait_agent(tmp_path, name, code):
    script = tmp_path / name
    script.write_text(code)
    return JsonlTransport([sys.executable, "-u", str(script)], cwd=tmp_path,
                          environment={"PATH": os.environ["PATH"]})


def run_demo(transport):
    from challenge.challenge_workflow import ChallengeWorkflow
    workflow = ChallengeWorkflow(DEMO_SCENARIO)
    result = workflow.run(transport)
    rows = [item.csv_row() for item in workflow.committed]
    return result, rows


def test_finish_message_lets_the_agent_write_a_summary_and_exit(tmp_path):
    from project_platform.trusted_engine import last_committed_sequence
    control = wait_agent(tmp_path, "control.py", FINISH_AWARE_AGENT)
    control_result, control_rows = run_demo(control)
    control.close()
    assert control_result["termination_reason"] == "survey_complete"

    transport = wait_agent(tmp_path, "aware.py", FINISH_AWARE_AGENT)
    result, rows = run_demo(transport)
    proc = transport.process
    started = time.monotonic()
    transport.finish(result["termination_reason"], last_committed_sequence(result), grace_seconds=10)
    assert time.monotonic() - started < 10  # the agent exited well inside the grace period
    assert proc.returncode == 0 and transport.process is None
    markers = [line for line in transport.log.splitlines() if line.startswith("FINISH-MSG ")]
    assert len(markers) == 1  # exactly one finish message, and its stderr reached the log
    message = json.loads(markers[0][len("FINISH-MSG "):])
    assert message["protocol_version"] == PARTICIPANT_PROTOCOL_VERSION
    assert message["message_type"] == "finish"
    assert message["payload"] == {"termination_reason": "survey_complete",
                                  "last_decision_sequence": last_committed_sequence(result),
                                  "grace_seconds": 10}
    # The run result and the score are identical to a run without the finish message.
    assert result["score_report"] == control_result["score_report"]
    assert result["termination_reason"] == control_result["termination_reason"]
    assert rows == control_rows


def test_finish_message_ignoring_agent_is_stopped_after_the_grace_period(tmp_path):
    control = wait_agent(tmp_path, "control.py", FINISH_AWARE_AGENT)
    control_result, control_rows = run_demo(control)
    control.close()

    transport = wait_agent(tmp_path, "ignoring.py", FINISH_IGNORING_AGENT)
    result, rows = run_demo(transport)
    proc = transport.process
    started = time.monotonic()
    transport.finish("survey_complete", 7, grace_seconds=1)
    elapsed = time.monotonic() - started
    assert 1 <= elapsed < 10  # waited out the grace period, then terminate/kill
    assert proc.returncode != 0 and transport.process is None
    assert "IGNORING-FINISH" in transport.log
    assert result["score_report"] == control_result["score_report"] and rows == control_rows


def test_finish_message_crashing_agent_keeps_the_scored_result(tmp_path):
    control = wait_agent(tmp_path, "control.py", FINISH_AWARE_AGENT)
    control_result, control_rows = run_demo(control)
    control.close()

    transport = wait_agent(tmp_path, "crashing.py", FINISH_CRASHING_AGENT)
    result, rows = run_demo(transport)
    proc = transport.process
    transport.finish("survey_complete", 7, grace_seconds=5)  # must not raise
    assert proc.returncode == 3 and transport.process is None
    assert result["score_report"] == control_result["score_report"] and rows == control_rows


def test_finish_message_reports_global_wallclock_expiry(tmp_path):
    from challenge.challenge_workflow import ChallengeWorkflow
    transport = wait_agent(tmp_path, "aware.py", FINISH_AWARE_AGENT)
    workflow = ChallengeWorkflow(DEMO_SCENARIO)
    started = time.monotonic()
    budget = 300.0
    calls = []

    def clock():
        # One real decision, then the global clock jumps past the deadline, so
        # the loop ends between decisions with the agent still alive.
        calls.append(1)
        return started if len(calls) <= 3 else started + budget + 1

    workflow.clock = clock
    result = workflow.run(transport, wallclock_seconds=budget)
    assert result["termination_reason"] == "global_wallclock_expired"
    proc = transport.process
    transport.finish(result["termination_reason"], 1, grace_seconds=5)
    assert proc.returncode == 0
    markers = [line for line in transport.log.splitlines() if line.startswith("FINISH-MSG ")]
    assert len(markers) == 1
    message = json.loads(markers[0][len("FINISH-MSG "):])
    assert message["payload"]["termination_reason"] == "global_wallclock_expired"
    assert message["payload"]["last_decision_sequence"] == 1
