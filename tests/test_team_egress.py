"""Team egress: allow-listed domains only, through the pinned forwarder sidecar."""
from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from project_platform.job_client import JobError  # noqa: E402
from project_platform.team_egress import (  # noqa: E402
    checked_team_egress, render_team_proxy_script, valid_domain, valid_variable_name,
)


def test_domain_rules():
    for good in ("api.kimi.com", "generativelanguage.googleapis.com", "api.deepseek.com", "a-b.example.org"):
        assert valid_domain(good), good
    for bad in ("localhost", "127.0.0.1", "10.0.0.1", "api", "API.KIMI.COM", "a..b", "-a.com", "x.local",
                "svc.internal", "host.lan", "[::1]", "api.kimi.com:443", "https://api.kimi.com", "x.onion",
                "a.home.arpa", "", None, 7):
        assert not valid_domain(bad), bad


def test_variable_names():
    for good in ("OPENAI_API_KEY", "KIMI_KEY", "A", "MY_MODEL_2"):
        assert valid_variable_name(good)
    for bad in ("openai", "1KEY", "OBSERVER_RUN_TOKEN", "SAC_X", "HTTPS_PROXY", "https_proxy", "PATH",
                "LD_PRELOAD", "FTP_PROXY", "A" * 65, "A-B"):
        assert not valid_variable_name(bad), bad


def test_payload_validation():
    good = {"environment": {"OPENAI_API_KEY": "sk-1", "OPENAI_MODEL": "k3"}, "secrets": ["OPENAI_API_KEY"],
            "domains": ["api.kimi.com"]}
    assert checked_team_egress(good) == {**good, "open": False}
    assert checked_team_egress({**good, "open": True})["open"] is True
    for bad in (None, {}, {**good, "extra": 1}, {**good, "open": False}, {**good, "open": "yes"}, {**good, "domains": ["127.0.0.1"]},
                {**good, "domains": ["a.com"] * 2}, {**good, "domains": [f"d{i}.com" for i in range(11)]},
                {**good, "secrets": ["MISSING"]}, {**good, "environment": {"PATH": "/x"}},
                {**good, "environment": {"K": "x" * 8193}}, {**good, "environment": {"K": 1}},
                {**good, "environment": {f"K{i}": "v" for i in range(21)}}):
        with pytest.raises(JobError):
            checked_team_egress(bad)


# --- the sidecar script itself, run as a local process -----------------------------------

def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def tls_stub(tmp_path_factory):
    """A TLS server on loopback that answers "HELLO <server name>" to any request."""
    if not shutil.which("openssl"):
        pytest.skip("openssl is required for the TLS stub")
    root = tmp_path_factory.mktemp("tls")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=stub",
                    "-keyout", str(root / "key.pem"), "-out", str(root / "cert.pem")],
                   check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(root / "cert.pem", root / "key.pem")
    names: list[str] = []
    context.sni_callback = lambda sock, name, ctx: names.append(name)
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)

    def serve():
        while True:
            try:
                raw, _ = listener.accept()
            except OSError:
                return
            try:
                with context.wrap_socket(raw, server_side=True) as conn:
                    conn.recv(4096)
                    conn.sendall(b"HELLO " + (names[-1] if names else "").encode())
            except (OSError, ssl.SSLError):
                pass
    threading.Thread(target=serve, daemon=True).start()
    yield listener.getsockname()[1], names
    listener.close()


@pytest.fixture(scope="module")
def sidecar(tls_stub):
    port, _ = tls_stub
    connect_port, tls_port = _free_port(), _free_port()
    script = render_team_proxy_script(
        ["allowed.test", "private.test", "rebind.test", "nowhere.test"], connect_port=connect_port,
        tls_port=tls_port, test_port=port,
        test_hosts={"allowed.test": ["127.0.0.1"], "other.test": ["127.0.0.1"], "private.test": ["10.0.0.5"],
                    "rebind.test": ["127.0.0.1", "192.168.0.2"], "metadata.test": ["169.254.169.254"]})
    process = subprocess.Popen([sys.executable, "-u", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    process.stdin.write(script)
    process.stdin.close()
    assert process.stdout.readline().strip() == b"READY"
    yield connect_port, tls_port
    process.kill()
    process.wait()


def _client_context():
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _via_connect(connect_port: int, target: str) -> tuple[bytes, bytes]:
    with socket.create_connection(("127.0.0.1", connect_port), timeout=10) as raw:
        raw.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = raw.recv(1024)
            if not chunk:
                break
            head += chunk
        status = head.split(b"\r\n", 1)[0]
        if b" 200 " not in status:
            return status, b""
        host = target.rsplit(":", 1)[0]
        with _client_context().wrap_socket(raw, server_hostname=host) as tls:
            tls.sendall(b"GET / HTTP/1.1\r\n\r\n")
            return status, tls.recv(1024)


def _via_tls(tls_port: int, name: str) -> bytes:
    with socket.create_connection(("127.0.0.1", tls_port), timeout=10) as raw:
        with _client_context().wrap_socket(raw, server_hostname=name) as tls:
            tls.sendall(b"GET / HTTP/1.1\r\n\r\n")
            return tls.recv(1024)


def test_connect_reaches_only_allowed_public_domains(sidecar):
    connect_port, _ = sidecar
    status, body = _via_connect(connect_port, "allowed.test:443")
    assert b" 200 " in status and body == b"HELLO allowed.test"
    for target in ("other.test:443",          # not on the team's list
                   "allowed.test:80",          # only port 443
                   "allowed.test:22",
                   "127.0.0.1:443",            # IP literal
                   "private.test:443",         # resolves to a private address
                   "rebind.test:443",          # one of its addresses is private
                   "metadata.test:443"):
        assert b" 403 " in _via_connect(connect_port, target)[0], target
    # Anything but CONNECT is refused.
    with socket.create_connection(("127.0.0.1", connect_port), timeout=10) as raw:
        raw.sendall(b"GET http://allowed.test/ HTTP/1.1\r\nHost: allowed.test\r\n\r\n")
        assert b" 403 " in raw.recv(1024)


def test_direct_tls_is_spliced_by_server_name(sidecar):
    _, tls_port = sidecar
    assert _via_tls(tls_port, "allowed.test") == b"HELLO allowed.test"
    for name in ("other.test", "private.test", "rebind.test"):
        with pytest.raises((ssl.SSLError, OSError)):
            _via_tls(tls_port, name)
    # Plain bytes that are not a TLS ClientHello are dropped.
    with socket.create_connection(("127.0.0.1", tls_port), timeout=10) as raw:
        raw.sendall(b"GET / HTTP/1.1\r\n\r\n")
        raw.settimeout(5)
        try:
            assert raw.recv(1024) == b""
        except ConnectionResetError:
            pass


def test_production_script_never_carries_test_settings():
    script = render_team_proxy_script(["api.kimi.com"]).decode()
    assert 'TEST_HOSTS, TEST_PORT = {}, 0' in script and '["api.kimi.com"]' in script
    assert "PORTS = frozenset([443])" in script and "DNS = 0" in script
    opened = render_team_proxy_script(None).decode()
    assert 'TEST_HOSTS, TEST_PORT = {}, 0' in opened and "ALLOWED = None" in opened
    assert "PORTS = frozenset([443, 80])" in opened and "DNS = 1" in opened
    with pytest.raises(JobError):
        render_team_proxy_script(["allowed.test"])
    with pytest.raises(JobError):
        render_team_proxy_script(["10.0.0.1"])


@pytest.fixture(scope="module")
def open_sidecar(tls_stub, tmp_path_factory):
    """Open mode: any public destination on 443/80, DNS answers, per-destination record."""
    port, _ = tls_stub
    http = _http_stub()
    report = tmp_path_factory.mktemp("report") / "egress.json"
    ports = {name: _free_port() for name in ("connect", "tls", "dns")}
    script = render_team_proxy_script(
        None, connect_port=ports["connect"], tls_port=ports["tls"], http_port=http["proxy_port"],
        dns_port=ports["dns"], report=str(report), test_port=port, dns=True,
        test_hosts={"public.test": ["127.0.0.1"], "anything.test": ["127.0.0.1"], "private.test": ["10.0.0.5"],
                    "rebind.test": ["127.0.0.1", "192.168.0.2"], "metadata.test": ["169.254.169.254"],
                    "cgnat.test": ["100.64.1.1"], "v6local.test": ["::1"], "mapped.test": ["::ffff:10.0.0.1"]})
    process = subprocess.Popen([sys.executable, "-u", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    process.stdin.write(script)
    process.stdin.close()
    assert process.stdout.readline().strip() == b"READY"
    yield {**ports, "http": http["proxy_port"], "report": report, "http_stub": http}
    process.kill()
    process.wait()


def _http_stub():
    """A plain HTTP server on the TLS stub's port? No: the open sidecar's port-80 handler
    connects to TEST_PORT as well, so HTTP tests use the TLS stub's port only for refusals."""
    return {"proxy_port": _free_port()}


def test_open_mode_connects_any_public_host_and_refuses_internal_ones(open_sidecar):
    connect = open_sidecar["connect"]
    for target in ("public.test:443", "anything.test:443"):
        status, body = _via_connect(connect, target)
        assert b" 200 " in status and body == b"HELLO " + target.split(":")[0].encode(), target
    for target in ("private.test:443", "rebind.test:443", "metadata.test:443", "cgnat.test:443",
                   "v6local.test:443", "mapped.test:443", "public.test:22", "public.test:8080",
                   "127.0.0.1:443", "10.0.0.1:443", "169.254.169.254:80", "[::1]:443", "unknown.test:443"):
        assert b" 200 " not in _via_connect(connect, target)[0], target
    assert _via_tls(open_sidecar["tls"], "anything.test") == b"HELLO anything.test"
    for name in ("private.test", "metadata.test", "rebind.test"):
        with pytest.raises((ssl.SSLError, OSError)):
            _via_tls(open_sidecar["tls"], name)


def test_open_mode_plain_http_is_routed_by_host_and_refused_for_internal_hosts(open_sidecar):
    def get(host):
        with socket.create_connection(("127.0.0.1", open_sidecar["http"]), timeout=10) as raw:
            raw.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
            raw.settimeout(5)
            try:
                return raw.recv(1024)
            except (ConnectionResetError, socket.timeout):
                return b""
    for host in ("private.test", "metadata.test", "rebind.test", "127.0.0.1", "169.254.169.254"):
        assert b" 403 " in get(host), host


def test_open_mode_dns_answers_every_name_with_the_sidecar():
    import struct
    port = _free_port()
    script = render_team_proxy_script(None, connect_port=_free_port(), tls_port=_free_port(), http_port=_free_port(),
                                      dns_port=port, test_port=1, dns=True, report="/dev/null")
    process = subprocess.Popen([sys.executable, "-u", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    process.stdin.write(script)
    process.stdin.close()
    try:
        assert process.stdout.readline().strip() == b"READY"
        own = socket.gethostbyname(socket.gethostname())
        def ask(name, qtype):
            query = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + b"".join(
                bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00" + struct.pack(">HH", qtype, 1)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(5)
                sock.sendto(query, ("127.0.0.1", port))
                return sock.recv(512)
        answer = ask("api.kimi.com", 1)
        assert answer[:2] == b"\x12\x34" and answer[6:8] == b"\x00\x01" and answer[-4:] == socket.inet_aton(own)
        assert ask("example.org", 28)[6:8] == b"\x00\x00"          # AAAA: no answer
    finally:
        process.kill()
        process.wait()


def test_open_mode_records_each_destination_without_content(open_sidecar):
    import json
    import time
    from project_platform.team_egress import checked_report, report_text
    _via_connect(open_sidecar["connect"], "public.test:443")
    _via_connect(open_sidecar["connect"], "private.test:443")
    time.sleep(1.5)
    rows = checked_report(json.loads(open_sidecar["report"].read_text()))
    by = {(r["host"], r["port"]): r for r in rows}
    ok = by[("public.test", 443)]
    assert ok["connections"] >= 1 and ok["bytes_up"] > 0 and ok["bytes_down"] > 0 and ok["first"] <= ok["last"]
    assert by[("private.test", 443)]["refused"] >= 1 and by[("private.test", 443)]["connections"] == 0
    raw = open_sidecar["report"].read_text()
    assert "HELLO" not in raw and "GET /" not in raw
    text = report_text(rows)
    assert "public.test:443" in text and "no content is recorded" in text
    # Re-validation drops malformed entries.
    assert checked_report([{"host": "x;rm", "port": 1}, "junk", {**ok, "port": -1}]) == []


# --- real Docker: a container on the internal network --------------------------------------

def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0


@pytest.mark.skipif(not _docker_available(), reason="Docker is required")
def test_docker_participant_reaches_only_allowed_domains():
    """Real network: an allowed public domain works both ways (proxy and direct TLS);
    any other domain, an IP literal and the bridge are unreachable."""
    import os
    from project_platform.egress import PROXY_IMAGE
    from project_platform.team_egress import TeamEgress
    env = {k: os.environ[k] for k in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
           if k in os.environ}
    egress = TeamEgress(["api.github.com"], client_env=env)
    probe = r"""
import json, os, socket, ssl, urllib.request
out = {}
def get(url):
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.status
    except Exception as e:
        return type(e).__name__
out["proxy_allowed"] = get("https://api.github.com/zen")
out["proxy_other"] = get("https://www.google.com/")
for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
    os.environ.pop(k, None)
out["direct_allowed"] = get("https://api.github.com/zen")
out["direct_other"] = get("https://www.google.com/")
try:
    socket.create_connection(("1.1.1.1", 443), timeout=5).close(); out["ip"] = "open"
except OSError as e:
    out["ip"] = "blocked"
print(json.dumps(out))
"""
    try:
        egress.start()
        command = ["docker", "run", "--rm", "--network", egress.network, "--cap-drop=ALL", "--user", "65534:65534"]
        for host in egress.hosts:
            command += ["--add-host", host]
        for key, value in egress.environment.items():
            command += ["-e", f"{key}={value}"]
        result = subprocess.run(command + ["--entrypoint", "python3", PROXY_IMAGE, "-c", probe],
                                capture_output=True, text=True, timeout=180, env=env)
        assert result.returncode == 0, result.stderr + egress.log
        import json
        out = json.loads(result.stdout.strip().splitlines()[-1])
        assert out["proxy_allowed"] == 200 and out["direct_allowed"] == 200, out
        assert out["proxy_other"] != 200 and out["direct_other"] != 200 and out["ip"] == "blocked", out
    finally:
        egress.close()
    names = subprocess.run(["docker", "network", "ls", "--format", "{{.Name}}"], capture_output=True, text=True,
                           env=env).stdout
    assert egress.network not in names


# --- job wiring -----------------------------------------------------------------------------

TEAM = {"environment": {"OPENAI_API_KEY": "sk-team-secret-1234", "OPENAI_BASE_URL": "https://api.kimi.com/coding/v1",
                        "OPENAI_MODEL": "k3"},
        "secrets": ["OPENAI_API_KEY"], "domains": ["api.kimi.com", "api.deepseek.com"]}


def _fake_egress(events):
    class Egress:
        network = "observer-egress-" + "1" * 32
        environment = {"HTTPS_PROXY": "http://172.30.0.2:3128", "https_proxy": "http://172.30.0.2:3128"}

        report = [{"host": "api.kimi.com", "port": 443, "connections": 2, "refused": 0, "bytes_up": 10,
                   "bytes_down": 20, "first": "2026-10-04T00:00:00Z", "last": "2026-10-04T00:00:01Z"}]

        def __init__(self, domains, *, client_env, open=False):
            events.append(("team-egress", tuple(domains), open))
            self.hosts = [] if open else [f"{d}:172.30.0.2" for d in domains]
            self.dns = "172.30.0.2" if open else None

        def pull(self):
            events.append("forwarder-pull")

        def start(self):
            events.append("forwarder-start")
            return self

        def close(self):
            events.append("forwarder-close")
    return Egress


def test_engine_job_injects_team_variables_and_joins_only_the_team_network(monkeypatch, tmp_path):
    import uuid
    from project_platform import job_runner
    from project_platform.docker_runtime import DockerWorkspace

    events, started = [], {}

    class Stop(Exception):
        pass

    real_start = DockerWorkspace.start

    def start(self, environment, **kwargs):
        transport = real_start(self, environment, **kwargs)
        started.update(env={k: v for k, v in transport.environment.items()} if hasattr(transport, "environment")
                       else {}, command=list(transport.command) if hasattr(transport, "command") else [],
                       redactions=getattr(transport, "redactions", None), kwargs=kwargs)
        raise Stop()

    run = str(uuid.uuid4())
    manifest = {"schema_version": "observer-project-v1", "image": "python@sha256:" + "e" * 64, "run": ["python3"],
                "environment": {"OPENAI_MODEL": "manifest-default", "OTHER": "1"}}
    participant = {"run_credential": f"obs_{run}." + "p" * 43, "model_base_url": "https://platform.test/m/v1",
                   "source_digest": "d" * 64, "manifest": manifest}
    (tmp_path / "project").mkdir()
    monkeypatch.setattr(job_runner, "TeamEgress", _fake_egress(events))
    monkeypatch.setattr(job_runner, "RestrictedEgress", lambda *a, **k: pytest.fail("model proxy egress used"))
    monkeypatch.setattr(job_runner, "_participant_runtime", lambda payload, p, root, http, sealed=None: (
        DockerWorkspace(tmp_path / "project", job_runner.ProjectManifest.parse(manifest), manifest["image"]),
        {"OBSERVER_API_URL": "https://platform.test/s", "OBSERVER_RUN_TOKEN": p["run_credential"],
         "OBSERVER_RUN_ID": run, "OPENAI_BASE_URL": p["model_base_url"], "OPENAI_API_KEY": p["run_credential"],
         "ANTHROPIC_BASE_URL": "https://platform.test/m", "ANTHROPIC_API_KEY": p["run_credential"]}))
    monkeypatch.setattr(job_runner, "download_project", lambda *a: ())
    monkeypatch.setattr(job_runner, "extract_project", lambda files, root: root.mkdir(parents=True))
    monkeypatch.setattr(job_runner, "is_v4_bundle", lambda root: True)
    monkeypatch.setattr(DockerWorkspace, "pull", lambda self: events.append("pull"))
    monkeypatch.setattr(DockerWorkspace, "build", lambda self: events.append("build"))
    monkeypatch.setattr(DockerWorkspace, "start", start)
    payload = {"run_id": run, "run_credential": f"obs_{run}." + "e" * 43, "session_url": "https://platform.test/s",
               "scenario_url": "https://platform.test/c", "scenario_digest": "c" * 64, "runtime_seconds": 900,
               "archive_url": "https://platform.test/a", "colocated": participant, "restricted_egress": True,
               "team_egress": TEAM}
    with pytest.raises(job_runner.ProjectJobFailure) as failure:
        job_runner.engine_job(payload, tmp_path, job_runner.Http(local=False))
    assert events[0] == ("team-egress", ("api.kimi.com", "api.deepseek.com"), False)
    assert "forwarder-start" in events and events[-1] == "forwarder-close"
    # Even a failed run hands over its destination record (job receipt -> database).
    assert failure.value.egress[0]["host"] == "api.kimi.com"
    assert started["kwargs"]["hosts"] == ("api.kimi.com:172.30.0.2", "api.deepseek.com:172.30.0.2")
    env = started["env"]
    # The team's variables, the run identity and the proxy; never the platform model proxy.
    assert env["OPENAI_API_KEY"] == "sk-team-secret-1234" and env["OPENAI_BASE_URL"] == "https://api.kimi.com/coding/v1"
    assert env["OPENAI_MODEL"] == "k3" and env["HTTPS_PROXY"] == "http://172.30.0.2:3128"
    assert "ANTHROPIC_BASE_URL" not in env and "ANTHROPIC_API_KEY" not in env
    assert env["OBSERVER_RUN_TOKEN"] == participant["run_credential"]
    command = started["command"]
    assert command[command.index("--network") + 1] == "observer-egress-" + "1" * 32
    assert "sk-team-secret-1234" in started["redactions"]


@pytest.mark.parametrize("team", [True, False])
def test_engine_job_without_a_model_sets_the_flag_and_no_model_settings(monkeypatch, tmp_path, team):
    """本次不提供模型: the database already left out the team's model variables; the runtime adds
    OBSERVER_MODEL_DISABLED=1 and never the platform model proxy's settings (team egress on or off)."""
    import uuid
    from project_platform import job_runner
    from project_platform.docker_runtime import DockerWorkspace

    events, started = [], {}

    class Stop(Exception):
        pass

    real_start = DockerWorkspace.start

    def start(self, environment, **kwargs):
        transport = real_start(self, environment, **kwargs)
        started.update(env=dict(transport.environment))
        raise Stop()

    run = str(uuid.uuid4())
    manifest = {"schema_version": "observer-project-v1", "image": "python@sha256:" + "e" * 64, "run": ["python3"]}
    participant = {"run_credential": f"obs_{run}." + "p" * 43, "model_base_url": "https://platform.test/m/v1",
                   "source_digest": "d" * 64, "manifest": manifest}
    (tmp_path / "project").mkdir()
    monkeypatch.setattr(job_runner, "TeamEgress", _fake_egress(events))
    monkeypatch.setattr(job_runner, "_participant_runtime", lambda payload, p, root, http, sealed=None: (
        DockerWorkspace(tmp_path / "project", job_runner.ProjectManifest.parse(manifest), manifest["image"]),
        {"OBSERVER_API_URL": "https://platform.test/s", "OBSERVER_RUN_TOKEN": p["run_credential"],
         "OBSERVER_RUN_ID": run, "OPENAI_BASE_URL": p["model_base_url"], "OPENAI_API_KEY": p["run_credential"],
         "ANTHROPIC_BASE_URL": "https://platform.test/m", "ANTHROPIC_API_KEY": p["run_credential"]}))
    monkeypatch.setattr(job_runner, "download_project", lambda *a: ())
    monkeypatch.setattr(job_runner, "extract_project", lambda files, root: root.mkdir(parents=True))
    monkeypatch.setattr(job_runner, "is_v4_bundle", lambda root: True)
    monkeypatch.setattr(DockerWorkspace, "pull", lambda self: None)
    monkeypatch.setattr(DockerWorkspace, "build", lambda self: None)
    monkeypatch.setattr(DockerWorkspace, "start", start)
    payload = {"run_id": run, "run_credential": f"obs_{run}." + "e" * 43, "session_url": "https://platform.test/s",
               "scenario_url": "https://platform.test/c", "scenario_digest": "c" * 64, "runtime_seconds": 900,
               "archive_url": "https://platform.test/a", "colocated": participant, "model_disabled": True,
               **({"team_egress": {"environment": {"STRATEGY": "rules"}, "secrets": [], "domains": [], "open": True}}
                  if team else {})}
    with pytest.raises(job_runner.ProjectJobFailure):
        job_runner.engine_job(payload, tmp_path, job_runner.Http(local=False))
    env = started["env"]
    assert env["OBSERVER_MODEL_DISABLED"] == "1"
    assert not {"OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"} & set(env)
    assert env["OBSERVER_RUN_ID"] == run
    if team:
        assert env["STRATEGY"] == "rules"
    # Without the flag nothing changes.
    assert job_runner._without_model({}, {"OPENAI_API_KEY": "k"}) == {"OPENAI_API_KEY": "k"}


def test_docker_start_merges_team_variables_over_the_manifest_and_redacts_secrets(tmp_path):
    from project_platform.docker_runtime import DockerWorkspace
    from project_platform.manifest import ProjectManifest, ProjectError
    manifest = {"schema_version": "observer-project-v1", "image": "python@sha256:" + "e" * 64, "run": ["python3"],
                "environment": {"OPENAI_MODEL": "manifest-default", "OTHER": "1"}}
    (tmp_path / "p").mkdir()
    runtime = DockerWorkspace(tmp_path / "p", ProjectManifest.parse(manifest), manifest["image"])
    runtime.network = "observer-egress-" + "1" * 32
    run_env = {"OBSERVER_API_URL": "https://platform.test/s", "OBSERVER_RUN_TOKEN": "obs_x.tok",
               "OBSERVER_RUN_ID": "r", "HTTPS_PROXY": "http://172.30.0.2:3128"}
    transport = runtime.start(run_env, team=TEAM, hosts=("api.kimi.com:172.30.0.2",))
    command = transport.command
    assert "sk-team-secret-1234" in transport.redactions
    assert "--add-host" in command and "api.kimi.com:172.30.0.2" in command
    env = transport.environment
    assert env["OPENAI_MODEL"] == "k3" and env["OTHER"] == "1" and env["OPENAI_API_KEY"] == "sk-team-secret-1234"
    assert env["HTTPS_PROXY"] == "http://172.30.0.2:3128"
    # Values travel by name (docker --env NAME), never on the command line.
    assert "sk-team-secret-1234" not in " ".join(command)
    # Proxy variables and host entries are refused outside team egress.
    with pytest.raises(ProjectError):
        runtime.start(run_env)
    runtime.network = None
    with pytest.raises(ProjectError):
        runtime.start({k: v for k, v in run_env.items() if k != "HTTPS_PROXY"}, team=TEAM,
                      hosts=("api.kimi.com:172.30.0.2",))


def test_engine_job_open_mode_uses_sidecar_dns_and_records_destinations(monkeypatch, tmp_path):
    import json
    import uuid
    from project_platform import job_runner
    from project_platform.docker_runtime import DockerWorkspace

    events, started, published = [], {}, {}
    real_start = DockerWorkspace.start

    def start(self, environment, **kwargs):
        transport = real_start(self, environment, **kwargs)
        started.update(env=dict(transport.environment), command=list(transport.command), kwargs=kwargs)
        return transport

    run = str(uuid.uuid4())
    manifest = {"schema_version": "observer-project-v1", "image": "python@sha256:" + "e" * 64, "run": ["python3"]}
    participant = {"run_credential": f"obs_{run}." + "p" * 43, "model_base_url": "https://platform.test/m/v1",
                   "source_digest": "d" * 64, "manifest": manifest}
    (tmp_path / "project").mkdir()
    monkeypatch.setattr(job_runner, "TeamEgress", _fake_egress(events))
    monkeypatch.setattr(job_runner, "_participant_runtime", lambda payload, p, root, http, sealed=None: (
        DockerWorkspace(tmp_path / "project", job_runner.ProjectManifest.parse(manifest), manifest["image"]),
        {"OBSERVER_API_URL": "https://platform.test/s", "OBSERVER_RUN_TOKEN": p["run_credential"],
         "OBSERVER_RUN_ID": run, "OPENAI_BASE_URL": p["model_base_url"], "OPENAI_API_KEY": p["run_credential"]}))
    monkeypatch.setattr(job_runner, "download_project", lambda *a: ())
    monkeypatch.setattr(job_runner, "extract_project", lambda files, root: root.mkdir(parents=True))
    monkeypatch.setattr(job_runner, "is_v4_bundle", lambda root: True)
    monkeypatch.setattr(DockerWorkspace, "pull", lambda self: None)
    monkeypatch.setattr(DockerWorkspace, "build", lambda self: None)
    monkeypatch.setattr(DockerWorkspace, "start", start)
    monkeypatch.setattr(job_runner, "ColocatedProvider", lambda *a: None)
    monkeypatch.setattr(job_runner, "run_session", lambda scenario, output, *a, **k: (output.mkdir(parents=True,
                        exist_ok=True), ({"status": "scored"}, "f" * 64))[1])

    def publish(payload, client, output, result, digest, http, credentials, sealed=None):
        published.update({p.name: p.read_text() for p in output.iterdir()})
        return {"run_id": payload["run_id"], "result_path": "x", "decisions_digest": digest}
    monkeypatch.setattr(job_runner, "_publish_result", publish)
    payload = {"run_id": run, "run_credential": f"obs_{run}." + "e" * 43, "session_url": "https://platform.test/s",
               "scenario_url": "https://platform.test/c", "scenario_digest": "c" * 64, "runtime_seconds": 900,
               "archive_url": "https://platform.test/a", "colocated": participant,
               "team_egress": {**TEAM, "domains": [], "open": True}}
    receipt = job_runner.engine_job(payload, tmp_path, job_runner.Http(local=False))
    assert events[0] == ("team-egress", (), True)
    command = started["command"]
    assert command[command.index("--dns") + 1] == "172.30.0.2" and "--add-host" not in command
    assert receipt["egress"][0]["host"] == "api.kimi.com"
    assert json.loads(published["egress.json"])[0]["bytes_down"] == 20
    assert "api.kimi.com:443 | direct | 2 | 0 | 10 | 20" in published["agent.log"]


@pytest.mark.skipif(not _docker_available(), reason="Docker is required")
def test_docker_open_mode_reaches_public_hosts_only_and_records_them():
    """Real network, open mode: public hosts work by proxy, by direct TLS and by plain HTTP;
    names resolving to loopback / private / metadata addresses, IP routes and other ports fail;
    the sidecar's record lists the destinations."""
    import json
    import os
    from project_platform.egress import PROXY_IMAGE
    from project_platform.team_egress import TeamEgress
    env = {k: os.environ[k] for k in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
           if k in os.environ}
    egress = TeamEgress([], client_env=env, open=True)
    probe = r"""
import json, os, socket, urllib.request
out = {}
def get(url, proxy=True):
    opener = urllib.request.build_opener() if proxy else urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return type(e).__name__
out["proxy_github"] = get("https://api.github.com/zen")
out["proxy_google"] = get("https://www.google.com/")
out["direct_github"] = get("https://api.github.com/zen", proxy=False)
out["direct_http"] = get("http://example.com/", proxy=False)
out["proxy_loopback_name"] = get("https://localtest.me/")
out["direct_loopback_name"] = get("https://localtest.me/", proxy=False)
out["proxy_private_name"] = get("https://10.0.0.1.nip.io/")
out["proxy_metadata_name"] = get("https://169.254.169.254.nip.io/")
out["direct_metadata_http"] = get("http://169.254.169.254.nip.io/latest/meta-data/", proxy=False)
out["proxy_other_port"] = get("https://portquiz.net:8443/")
for name, (host, port) in {"ip_direct": ("1.1.1.1", 443), "metadata_ip": ("169.254.169.254", 80)}.items():
    try:
        socket.create_connection((host, port), timeout=5).close(); out[name] = "open"
    except OSError:
        out[name] = "blocked"
print(json.dumps(out))
"""
    try:
        egress.start()
        command = ["docker", "run", "--rm", "--network", egress.network, "--cap-drop=ALL", "--user", "65534:65534",
                   "--dns", egress.dns]
        for key, value in egress.environment.items():
            command += ["-e", f"{key}={value}"]
        result = subprocess.run(command + ["--entrypoint", "python3", PROXY_IMAGE, "-c", probe],
                                capture_output=True, text=True, timeout=300, env=env)
        assert result.returncode == 0, result.stderr + egress.log
        out = json.loads(result.stdout.strip().splitlines()[-1])
        assert out["proxy_github"] == 200 and out["direct_github"] == 200, out
        assert out["proxy_google"] == 200 and out["direct_http"] == 200, out
        for key in ("proxy_loopback_name", "direct_loopback_name", "proxy_private_name", "proxy_metadata_name",
                    "direct_metadata_http", "proxy_other_port"):
            assert out[key] not in (200, 301, 302, 404), (key, out)
        assert out["ip_direct"] == "blocked" and out["metadata_ip"] == "blocked", out
        report = egress.collect()
    finally:
        egress.close()
    by = {(r["host"], r["port"]): r for r in egress.report}
    assert by[("api.github.com", 443)]["connections"] >= 2 and by[("api.github.com", 443)]["bytes_down"] > 0
    assert by[("example.com", 80)]["connections"] >= 1
    assert by[("localtest.me", 443)]["refused"] >= 1 and by[("localtest.me", 443)]["connections"] == 0
    assert by[("169.254.169.254.nip.io", 80)]["refused"] >= 1, (egress.report, out)
    assert by[("169.254.169.254.nip.io", 443)]["refused"] >= 1 and by[("portquiz.net", 8443)]["refused"] >= 1
    assert report == egress.report
