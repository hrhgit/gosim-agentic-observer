"""Egress route: the sidecar sends allowed connections through the route client (SOCKS on
loopback), fails over between nodes, falls back to direct only when the team allows it,
caps proxied bytes and records the path, never a node setting."""
from __future__ import annotations

import json
import random
import socket
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
    ROUTE_BASE_PORT, ROUTE_IMAGE, TeamEgress, checked_report, checked_route, checked_route_summary,
    checked_team_egress, render_team_proxy_script, report_text, route_client_config, route_plan,
)
from test_team_egress import _free_port, _via_connect, tls_stub  # noqa: E402,F401

VLESS = {"type": "vless", "server": "cn-node.invalid-node.example", "server_port": 34567,
         "uuid": "11111111-2222-3333-4444-555555555555", "flow": "xtls-rprx-vision",
         "tls": {"enabled": True, "server_name": "www.example.com",
                 "utls": {"enabled": True, "fingerprint": "chrome"},
                 "reality": {"enabled": True, "public_key": "PUBKEY-canary-0123456789", "short_id": "abcdef12"}}}


def hy2(i):
    return {"type": "hysteria2", "server": f"hy{i}.node-canary.example", "server_port": 40000 + i,
            "password": f"hy2-password-canary-{i}", "tls": {"enabled": True, "server_name": f"hy{i}.node-canary.example"}}


def test_route_validation():
    good = {"name": "overseas", "fallback": True, "cap_bytes": 500 * 2 ** 20, "nodes": [hy2(1), hy2(2), hy2(3)]}
    assert checked_route(good) == good
    assert checked_route({"name": "cn", "fallback": False, "cap_bytes": 1, "nodes": [VLESS]})["nodes"] == [VLESS]
    # The stored job input has no nodes (they are added at claim time).
    assert checked_route({"name": "cn", "fallback": True, "cap_bytes": 1})["nodes"] == []
    assert checked_route(None) is None
    for bad in ({**good, "name": "direct"}, {**good, "fallback": "yes"}, {**good, "cap_bytes": 0},
                {**good, "cap_bytes": True}, {**good, "extra": 1}, {**good, "nodes": [hy2(i) for i in range(5)]},
                {"name": "cn", "fallback": True, "cap_bytes": 1, "nodes": [VLESS, VLESS]},
                {**good, "nodes": [{**hy2(1), "type": "shadowsocks"}]}, {**good, "nodes": [{**hy2(1), "x": 1}]},
                {**good, "nodes": [{**hy2(1), "server": "a b"}]}, {**good, "nodes": [{**hy2(1), "server_port": 0}]},
                {**good, "nodes": [{**hy2(1), "tls": {"enabled": False}}]},
                {**good, "nodes": [{k: v for k, v in hy2(1).items() if k != "password"}]},
                {**good, "nodes": [{**VLESS, "tls": {**VLESS["tls"], "reality": {"x": "y"}}}]}):
        with pytest.raises(JobError):
            checked_route(bad)
    team = {"environment": {}, "secrets": [], "domains": [], "open": True, "route": good}
    assert checked_team_egress(team)["route"] == good
    with pytest.raises(JobError):  # a route needs open egress
        checked_team_egress({**team, "open": False})


def test_plan_labels_nodes_by_position_and_starts_at_a_random_node():
    route = {"name": "overseas", "fallback": True, "cap_bytes": 1, "nodes": [hy2(1), hy2(2), hy2(3)]}
    firsts = {route_plan(route, choice=random.Random(seed))[0][0] for seed in range(40)}
    assert firsts == {"overseas:node1", "overseas:node2", "overseas:node3"}
    plan = route_plan(route, choice=random.Random(1))
    assert sorted(plan, key=lambda p: p[1]) == [(f"overseas:node{i}", ROUTE_BASE_PORT + i - 1, hy2(i)) for i in (1, 2, 3)]
    # Failover order keeps the configured cycle after the random start.
    labels = [p[0] for p in plan]
    start = labels.index("overseas:node1")
    assert labels[start:] + labels[:start] == ["overseas:node1", "overseas:node2", "overseas:node3"]
    assert route_plan({"name": "cn", "fallback": True, "cap_bytes": 1, "nodes": [VLESS]}) == [("cn", ROUTE_BASE_PORT, VLESS)]
    assert route_plan({"name": "cn", "fallback": True, "cap_bytes": 1, "nodes": []}) == []


def test_node_settings_reach_only_the_route_client_configuration():
    route = {"name": "overseas", "fallback": True, "cap_bytes": 1, "nodes": [hy2(1), hy2(2), hy2(3)]}
    egress = TeamEgress([], client_env={}, open=True, route=route)
    script = egress._script.decode()
    secrets = ["node-canary", "hy2-password-canary", "40001", "40002", "40003"]
    assert not any(secret in script for secret in secrets)
    assert '"overseas"' in script and "overseas:node1" in script
    config = json.loads(route_client_config(egress._plan))
    assert config["log"] == {"disabled": True}
    assert {i["listen"] for i in config["inbounds"]} == {"127.0.0.1"}
    assert [o["password"] for o in config["outbounds"]] == [f"hy2-password-canary-{i}" for i in (1, 2, 3)]
    assert all(r["outbound"] == r["inbound"][0][3:] for r in config["route"]["rules"])
    # The pinned client is an image digest, started without any argument carrying a setting.
    assert "@sha256:" in ROUTE_IMAGE
    # Allow-list mode never takes a route.
    assert TeamEgress(["api.kimi.com"], client_env={}, route=route).route is None
    with pytest.raises(JobError):
        render_team_proxy_script(["api.kimi.com"], route=route, routes=[("overseas:node1", 1081)])
    with pytest.raises(JobError):
        render_team_proxy_script(None, route=route, routes=[("hy1.node-canary.example", 1081)])


# --- the sidecar with a fake route client ---------------------------------------------------

class FakeSocks:
    """A SOCKS5 server like the route client: CONNECT to the requested IP and port, or a
    failure reply when that fails. ``down=True``: every request fails (node unreachable)."""

    def __init__(self, *, down: bool = False):
        self.down = down
        self.requests: list[tuple[str, int]] = []
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(64)
        self.port = self.listener.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client):
        try:
            assert client.recv(3) == b"\x05\x01\x00"
            client.sendall(b"\x05\x00")
            head = client.recv(4)
            size = 4 if head[3] == 1 else 16
            ip = socket.inet_ntop(socket.AF_INET if size == 4 else socket.AF_INET6, client.recv(size))
            port = int.from_bytes(client.recv(2), "big")
            self.requests.append((ip, port))
            if self.down:
                client.sendall(b"\x05\x01\x00\x01" + b"\x00" * 6)
                return
            try:
                remote = socket.create_connection((ip, port), timeout=3)
            except OSError:
                client.sendall(b"\x05\x05\x00\x01" + b"\x00" * 6)
                return
            client.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)

            def pump(a, b):
                try:
                    while data := a.recv(65536):
                        b.sendall(data)
                except OSError:
                    pass
                finally:
                    for s in (a, b):
                        try:
                            s.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
            threading.Thread(target=pump, args=(remote, client), daemon=True).start()
            pump(client, remote)
        except (OSError, AssertionError, IndexError):
            pass
        finally:
            client.close()


def _sidecar(tls_port, tmp_path, routes, *, fallback, cap=10 ** 9, name="overseas"):
    report = tmp_path / ("egress-%d.json" % random.randrange(10 ** 9))
    connect = _free_port()
    script = render_team_proxy_script(
        None, connect_port=connect, tls_port=_free_port(), http_port=_free_port(), dns_port=_free_port(),
        report=str(report), test_port=tls_port, dns=False,
        test_hosts={"public.test": ["127.0.0.1"], "dead.test": ["127.0.0.3"], "private.test": ["10.0.0.5"]},
        route={"name": name, "fallback": fallback, "cap_bytes": cap, "nodes": []}, routes=routes,
        probes=[("127.0.0.1", tls_port)])
    process = subprocess.Popen([sys.executable, "-u", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    process.stdin.write(script)
    process.stdin.close()
    assert process.stdout.readline().strip() == b"READY"
    return process, connect, report


def _record(report):
    time.sleep(1.2)
    value = json.loads(report.read_text())
    return checked_report(value), checked_route_summary(value)


def test_route_fails_over_to_the_next_node_and_records_the_path(tls_stub, tmp_path):
    port, _ = tls_stub
    dead, good = FakeSocks(down=True), FakeSocks()
    process, connect, report = _sidecar(port, tmp_path, [("overseas:node2", dead.port), ("overseas:node3", good.port)],
                                        fallback=False)
    try:
        for _ in range(2):
            status, body = _via_connect(connect, "public.test:443")
            assert b" 200 " in status and body == b"HELLO public.test"
        # The client got the checked address, never the name; the private name never reached it.
        assert set(good.requests) == {("127.0.0.1", port)}
        assert b" 200 " not in _via_connect(connect, "private.test:443")[0]
        # A destination the healthy node cannot reach does not take the node out of service.
        assert b" 200 " not in _via_connect(connect, "dead.test:443")[0]
        status, _ = _via_connect(connect, "public.test:443")
        assert b" 200 " in status
        rows, summary = _record(report)
        by = {(r["host"], r["path"]): r for r in rows}
        assert by[("public.test", "overseas:node3")]["connections"] == 3
        assert by[("private.test", "none")]["refused"] == 1
        assert summary["route"] == "overseas" and summary["failovers"] == 1 and summary["fallbacks"] == 0
        assert summary["paths"]["overseas:node3"]["connections"] == 3 and summary["proxied_bytes"] > 0
        assert "overseas:node2" not in summary["paths"]
        text = report_text(rows, summary)
        assert "overseas route" in text and "| overseas:node3 |" in text and "node switches 1" in text
    finally:
        process.kill()
        process.wait()


def test_no_working_node_falls_back_to_direct_only_when_allowed(tls_stub, tmp_path):
    port, _ = tls_stub
    dead = FakeSocks(down=True)
    for fallback in (True, False):
        process, connect, report = _sidecar(port, tmp_path, [("cn", dead.port)], fallback=fallback, name="cn")
        try:
            status, body = _via_connect(connect, "public.test:443")
            if fallback:
                assert b" 200 " in status and body == b"HELLO public.test"
            else:
                assert b" 403 " in status
            rows, summary = _record(report)
            assert summary["failovers"] == 1 and summary["fallbacks"] == (1 if fallback else 0)
            assert {r["path"] for r in rows} == ({"direct"} if fallback else {"none"})
        finally:
            process.kill()
            process.wait()


def test_route_without_nodes_and_the_traffic_cap(tls_stub, tmp_path):
    port, _ = tls_stub
    # No node (the backend had none configured): direct with fallback, refused without.
    process, connect, report = _sidecar(port, tmp_path, [], fallback=True, name="cn")
    try:
        assert b" 200 " in _via_connect(connect, "public.test:443")[0]
        rows, summary = _record(report)
        assert summary["nodes"] == 0 and [r["path"] for r in rows] == ["direct"]
        assert "no route node was available" in report_text(rows, summary)
    finally:
        process.kill()
        process.wait()
    good = FakeSocks()
    for fallback in (True, False):
        process, connect, report = _sidecar(port, tmp_path, [("cn", good.port)], fallback=fallback, cap=10, name="cn")
        try:
            try:  # goes over the cap: the connection is cut once the limit is reached
                _via_connect(connect, "public.test:443")
            except OSError:
                pass
            second = _via_connect(connect, "public.test:443")[0]
            assert (b" 200 " in second) == fallback
            rows, summary = _record(report)
            assert summary["capped"] is True and summary["proxied_bytes"] >= 10
            assert {r["path"] for r in rows if r["connections"]} == ({"cn", "direct"} if fallback else {"cn"})
            assert "limit reached" in report_text(rows, summary)
        finally:
            process.kill()
            process.wait()


def test_report_validation_keeps_labels_only():
    row = {"host": "api.kimi.com", "port": 443, "path": "cn", "connections": 1, "refused": 0, "bytes_up": 1,
           "bytes_down": 2, "first": "2026-10-04T00:00:00Z", "last": "2026-10-04T00:00:00Z"}
    assert checked_report({"destinations": [row], "route": None}) == [row]
    assert checked_report([{**row, "path": "hy1.node-canary.example:40001"}]) == []
    old = {k: v for k, v in row.items() if k != "path"}
    assert checked_report([old]) == [row | {"path": "direct"}]
    summary = {"route": "cn", "fallback": True, "cap_bytes": 5, "nodes": 1, "proxied_bytes": 3, "capped": False,
               "failovers": 0, "fallbacks": 0, "paths": {"cn": {"connections": 1, "bytes_up": 1, "bytes_down": 2}}}
    assert checked_route_summary({"route": summary}) == summary
    for bad in ({**summary, "paths": {"10.0.0.1:443": {"connections": 1, "bytes_up": 1, "bytes_down": 2}}},
                {**summary, "route": "x"}, {**summary, "proxied_bytes": -1}, {**summary, "server": "x"} | {"paths": 1}):
        assert checked_route_summary(bad) is None


# --- real Docker: the pinned route client in the sidecar's namespace ------------------------

def _docker_available() -> bool:
    import shutil
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0


@pytest.mark.skipif(not _docker_available(), reason="Docker is required")
def test_docker_route_client_starts_and_an_unreachable_node_falls_back_to_direct():
    """The real route client starts from its digest with the node settings on standard
    input; a node that cannot be reached is skipped and, with fallback, the participant
    still reaches a public host directly. Nothing of the node is visible inside."""
    import os
    from project_platform.egress import PROXY_IMAGE
    env = {k: os.environ[k] for k in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG") if k in os.environ}
    node = {"type": "hysteria2", "server": "192.0.2.1", "server_port": 9, "password": "hy2-password-canary-9",
            "tls": {"enabled": True, "server_name": "node-canary.example"}}
    egress = TeamEgress([], client_env=env, open=True,
                        route={"name": "overseas", "fallback": True, "cap_bytes": 10 ** 8, "nodes": [node]})
    probe = r"""
import json, os, urllib.request
out = {"env": "\n".join(f"{k}={v}" for k, v in os.environ.items())}
try:
    with urllib.request.urlopen("https://api.github.com/zen", timeout=40) as r:
        out["status"] = r.status
except Exception as e:
    out["status"] = type(e).__name__
print(json.dumps(out))
"""
    try:
        egress.pull()
        egress.start()
        command = ["docker", "run", "--rm", "--network", egress.network, "--dns", egress.dns,
                   "--cap-drop=ALL", "--user", "65534:65534"]
        for key, value in egress.environment.items():
            command += ["-e", f"{key}={value}"]
        result = subprocess.run(command + ["--entrypoint", "python3", PROXY_IMAGE, "-c", probe],
                                capture_output=True, text=True, timeout=240, env=env)
        out = json.loads(result.stdout.strip().splitlines()[-1])
        assert out["status"] == 200, out
        assert "canary" not in out["env"] and "192.0.2.1" not in out["env"]
        running = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, env=env).stdout
        assert egress.route_name in running
    finally:
        egress.close()
    summary = egress.route_summary
    assert summary["failovers"] == 1 and summary["fallbacks"] >= 1 and summary["proxied_bytes"] == 0, summary
    assert {r["path"] for r in egress.report if r["connections"]} == {"direct"}
    assert "canary" not in json.dumps(egress.report) + json.dumps(summary) + egress.log
    names = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True, env=env).stdout
    assert egress.route_name not in names and egress.proxy_name not in names
