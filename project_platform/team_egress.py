"""Team egress: what the participant container can reach during a run.

The participant container joins a per-run internal Docker network (no route
anywhere). Its only neighbour is a small forwarder sidecar: trusted engine code
on a pinned image, dual-homed onto the default bridge. The sidecar accepts

* HTTPS CONNECT on port 3128 (``HTTPS_PROXY``; standard SDKs use it),
* direct TLS on port 443, spliced by the ClientHello's server name, and
* (open mode) plain HTTP on port 80, spliced by its ``Host`` header,

for clients that ignore proxy settings. Two modes:

* **open** (default for teams on open egress): any public destination on port
  443 or 80. The sidecar is also the container's DNS server and answers every
  name with its own address, so every connection by name reaches it.
* **allow-list**: only the team's listed domains on port 443, mapped to the
  sidecar in the participant's ``/etc/hosts`` (the earlier behaviour; rollback).

In both modes the sidecar resolves the destination itself, refuses it unless
every address is public (no private, loopback, link-local, CGNAT, multicast,
benchmarking or metadata range, also for IPv4-mapped IPv6), and connects only to
those checked addresses, so a name that resolves (or rebinds) to an internal
address never reaches an internal service. TLS stays end to end; the sidecar
never sees plaintext or credentials. It records, per destination host and port,
the number of connections (and refusals), bytes each way and the first/last
time (``report``); never any payload. The team's variables (API keys, base URLs,
model names) are injected into the participant container as environment
variables; they never reach the sidecar.

**Egress route** (optional, the team's choice): with ``route`` the sidecar sends every
connection it allows through a local proxy client (``ROUTE_IMAGE``, sing-box) that
runs in the sidecar's own network namespace, never in the participant container.
The node settings arrive only in the job's claim payload (sealed for the public
pool) and reach that client on its standard input; the sidecar script itself only
knows the local SOCKS ports and the node labels (``cn``, ``overseas:node2``). The
sidecar still resolves and checks every destination and hands the client the
checked address, never a name, so the remote end cannot be pointed at an internal
address either. A node that fails a connection is skipped for the next ones; with
``fallback`` the connection then goes direct, otherwise it is refused. Proxied bytes
(both ways) are capped per run (``cap_bytes``). Each record row names the path it
took (``direct``, ``cn``, ``overseas:nodeN``); no node address appears anywhere.
"""
from __future__ import annotations

import json
import os
import random
import re
import select
import subprocess
import threading
import time
import uuid

from .egress import NETWORK_PATTERN, PROXY_IMAGE
from .job_client import JobError

CONNECT_PORT = 3128
TLS_PORT = 443
MAX_DOMAINS = 10
MAX_VARIABLES = 20
MAX_VALUE_BYTES = 8192
VARIABLE_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
RESERVED_PREFIXES = ("OBSERVER_", "SAC_")
# Names the platform sets itself (proxy settings) or that would change how the
# container starts; a team cannot override them.
RESERVED_NAMES = {"HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "NODE_USE_ENV_PROXY",
                  "PATH", "HOME", "HOSTNAME", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "NODE_OPTIONS"}
_LABEL = r"(?!-)[a-z0-9-]{1,63}(?<!-)"
DOMAIN = re.compile(rf"^(?=.{{1,253}}$){_LABEL}(?:\.{_LABEL})+$")
RESERVED_SUFFIXES = ("localhost", "local", "internal", "intranet", "lan", "home.arpa", "arpa", "test",
                     "example", "invalid", "onion")
_DOCKER_TIMEOUT = 30
_PULL_TIMEOUT = 300
# The route client: sing-box 1.14.2, multi-architecture index digest (the digest is the checksum).
ROUTE_IMAGE = "ghcr.io/sagernet/sing-box@sha256:afbffd294c5eb3519cc7b4587299ef189bb0a2ca2f667cb6301fdb6b9bce9558"
ROUTE_NAMES = ("cn", "overseas")
ROUTE_BASE_PORT = 1081
MAX_ROUTE_NODES = 4
DEFAULT_ROUTE_CAP = 500 * 1024 * 1024
PATH_LABEL = re.compile(r"^(?:direct|none|cn|overseas:node[1-4])$")
_NODE_TEXT = re.compile(r"^[\x21-\x7e]{1,512}$")
_NODE_HOST = re.compile(r"^[A-Za-z0-9.:\[\]-]{1,253}$")
# The node settings a route may carry (sing-box outbound fields), per type.
_NODE_FIELDS = {
    "vless": {"type", "server", "server_port", "uuid", "flow", "tls", "packet_encoding"},
    "hysteria2": {"type", "server", "server_port", "password", "tls", "up_mbps", "down_mbps", "obfs"},
}
_TLS_FIELDS = {"enabled", "server_name", "insecure", "alpn", "utls", "reality"}


def valid_domain(value: object) -> bool:
    """A public DNS name: lowercase, at least two labels, not an IP literal or reserved name."""
    if not isinstance(value, str) or not DOMAIN.fullmatch(value):
        return False
    if re.fullmatch(r"[\d.]+", value):
        return False
    return not any(value == suffix or value.endswith("." + suffix) for suffix in RESERVED_SUFFIXES)


def valid_variable_name(name: object) -> bool:
    return (isinstance(name, str) and bool(VARIABLE_NAME.fullmatch(name)) and
            not name.startswith(RESERVED_PREFIXES) and name not in RESERVED_NAMES and
            not name.lower().endswith("_proxy"))


def _checked_node(node: object) -> dict:
    """One node of a route (a sing-box outbound without tag). Only known fields, short printable values."""
    def text(value, pattern=_NODE_TEXT):
        return isinstance(value, str) and bool(pattern.fullmatch(value))

    def flat(value, keys):
        return isinstance(value, dict) and set(value) <= keys and all(
            isinstance(v, (bool, int)) or text(v) for v in value.values())

    if not isinstance(node, dict) or node.get("type") not in _NODE_FIELDS or not set(node) <= _NODE_FIELDS[node["type"]]:
        raise JobError("invalid_team_egress")
    port = node.get("server_port")
    tls = node.get("tls")
    if (not text(node.get("server"), _NODE_HOST) or not isinstance(port, int) or isinstance(port, bool) or
            not 0 < port < 65536 or not isinstance(tls, dict) or not set(tls) <= _TLS_FIELDS or
            tls.get("enabled") is not True or not text(tls.get("server_name", "x"), _NODE_HOST) or
            not all(text(node[k]) for k in ("uuid", "flow", "password", "packet_encoding") if k in node) or
            not all(isinstance(node[k], int) and not isinstance(node[k], bool) and 0 < node[k] < 100000
                    for k in ("up_mbps", "down_mbps") if k in node) or
            ("obfs" in node and not flat(node["obfs"], {"type", "password"})) or
            ("insecure" in tls and not isinstance(tls["insecure"], bool)) or
            ("alpn" in tls and (not isinstance(tls["alpn"], list) or len(tls["alpn"]) > 4 or
                                not all(text(a) for a in tls["alpn"]))) or
            ("utls" in tls and not flat(tls["utls"], {"enabled", "fingerprint"})) or
            ("reality" in tls and not flat(tls["reality"], {"enabled", "public_key", "short_id"}))):
        raise JobError("invalid_team_egress")
    if node["type"] == "vless" and "uuid" not in node or node["type"] == "hysteria2" and "password" not in node:
        raise JobError("invalid_team_egress")
    return json.loads(json.dumps(node))


def checked_route(value: object) -> dict | None:
    """The optional ``route``: {"name": "cn"|"overseas", "fallback": bool, "cap_bytes": int, "nodes": [node]}.
    ``nodes`` is added by the job API at claim time; without it the route has no node (fallback or refuse)."""
    if value is None:
        return None
    if (not isinstance(value, dict) or not {"name", "fallback", "cap_bytes"} <= set(value) <=
            {"name", "fallback", "cap_bytes", "nodes"} or value["name"] not in ROUTE_NAMES or
            not isinstance(value["fallback"], bool) or not isinstance(value["cap_bytes"], int) or
            isinstance(value["cap_bytes"], bool) or not 0 < value["cap_bytes"] <= 2 ** 40):
        raise JobError("invalid_team_egress")
    nodes = value.get("nodes", [])
    if not isinstance(nodes, list) or len(nodes) > MAX_ROUTE_NODES or (value["name"] == "cn" and len(nodes) > 1):
        raise JobError("invalid_team_egress")
    return {"name": value["name"], "fallback": value["fallback"], "cap_bytes": value["cap_bytes"],
            "nodes": [_checked_node(node) for node in nodes]}


def route_plan(route: dict | None, *, choice: random.Random | None = None) -> list[tuple[str, int, dict]]:
    """(label, local SOCKS port, node) in the order the sidecar tries them. ``overseas``: a
    random node first, then the others (fail over); labels follow the configured order."""
    if not route or not route["nodes"]:
        return []
    labelled = [("cn" if route["name"] == "cn" else f"overseas:node{i + 1}", ROUTE_BASE_PORT + i, node)
                for i, node in enumerate(route["nodes"])]
    start = (choice or random.SystemRandom()).randrange(len(labelled))
    return labelled[start:] + labelled[:start]


def route_client_config(plan: list[tuple[str, int, dict]]) -> bytes:
    """The route client's configuration: one loopback SOCKS inbound per node, each bound to its node."""
    config = {"log": {"disabled": True}, "inbounds": [], "outbounds": [], "route": {"rules": []}}
    for label, port, node in sorted(plan, key=lambda item: item[1]):
        tag = label.replace(":", "-")
        config["inbounds"].append({"type": "socks", "tag": "in-" + tag, "listen": "127.0.0.1", "listen_port": port})
        config["outbounds"].append({**node, "tag": tag})
        config["route"]["rules"].append({"inbound": ["in-" + tag], "outbound": tag})
    return json.dumps(config).encode()


def checked_team_egress(value: object) -> dict:
    """The job payload's ``team_egress``: {"environment": {NAME: value}, "secrets": [NAME], "domains": [host]},
    plus ``open`` and the optional ``route``."""
    if (not isinstance(value, dict) or not {"environment", "secrets", "domains"} <= set(value) <=
            {"environment", "secrets", "domains", "open", "route"} or value.get("open", True) is not True):
        raise JobError("invalid_team_egress")
    environment, secret_names, domains = value["environment"], value["secrets"], value["domains"]
    if not isinstance(environment, dict) or len(environment) > MAX_VARIABLES:
        raise JobError("invalid_team_egress")
    for name, text in environment.items():
        if (not valid_variable_name(name) or not isinstance(text, str) or "\x00" in text or
                len(text.encode()) > MAX_VALUE_BYTES):
            raise JobError("invalid_team_egress")
    if (not isinstance(secret_names, list) or any(name not in environment for name in secret_names) or
            not isinstance(domains, list) or len(domains) > MAX_DOMAINS or
            len(set(domains)) != len(domains) or not all(valid_domain(d) for d in domains)):
        raise JobError("invalid_team_egress")
    checked = {"environment": dict(environment), "secrets": list(secret_names), "domains": list(domains),
               "open": value.get("open") is True}
    route = checked_route(value.get("route"))
    if route is not None:
        if not checked["open"]:
            raise JobError("invalid_team_egress")
        checked["route"] = route
    return checked


# The forwarder inside the sidecar (standard library only). ALLOWED is baked in by
# the trusted engine; unit tests run this exact script in a local process.
PROXY_SCRIPT = r"""
import ipaddress, json, os, re, socket, sys, threading, time
ALLOWED = %(allowed)s            # list of domains, or None: any public destination
PORTS = frozenset(%(ports)s)     # destination ports
DNS = %(dns)d                    # answer every name with this sidecar's address
REPORT = %(report)s              # where the per-destination record is written
# Tests only: {host: [addresses]} instead of DNS, loopback allowed, and every
# connection goes to TEST_PORT. Always empty/0 in production.
TEST_HOSTS, TEST_PORT = %(test_hosts)s, %(test_port)d
CONNECT_PORT, TLS_PORT, HTTP_PORT, DNS_PORT = %(connect_port)d, %(tls_port)d, %(http_port)d, %(dns_port)d
# Egress route: None (direct), or the route name with [[node label, local SOCKS port]] in the
# order to try them. The node settings live only in the route client, never in this script.
ROUTE, ROUTES, FALLBACK, CAP = %(route)s, %(routes)s, %(fallback)s, %(cap)d
ROUTE_HOST, PROBES = %(route_host)s, %(probes)s
ROUTE_TIMEOUT, PROBE_TIMEOUT, RETRY_AFTER, HEALTHY_FOR = 10, 6, 60, 30
IDLE, CONNECT_TIMEOUT, MAX_CONNECTIONS, MAX_DESTINATIONS = 300, 15, 128, 500
HOST = re.compile(r"^[a-z0-9.:_\[\]-]{1,253}$")
slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
stats, stats_lock, dirty = {}, threading.Lock(), threading.Event()
route_lock = threading.Lock()
warmed = threading.Event()
route = {"next": 0, "retry": 0.0, "failovers": 0, "fallbacks": 0, "proxied": 0, "capped": False, "healthy": {}}

def now():
    return time.strftime("%%Y-%%m-%%dT%%H:%%M:%%SZ", time.gmtime())

def note(host, port, path="none", *, refused=False, up=0, down=0, opened=False):
    host = host if HOST.fullmatch(host or "") else "(invalid)"
    with stats_lock:
        key = (host, port, path)
        if key not in stats and len(stats) >= MAX_DESTINATIONS:
            key = ("(other)", 0, path)
        entry = stats.setdefault(key, {"host": key[0], "port": key[1], "path": path, "connections": 0, "refused": 0,
                                       "bytes_up": 0, "bytes_down": 0, "first": now(), "last": now()})
        entry["connections"] += 1 if opened else 0
        entry["refused"] += 1 if refused else 0
        entry["bytes_up"] += up
        entry["bytes_down"] += down
        entry["last"] = now()
    dirty.set()

def writer():
    while True:
        dirty.wait()
        time.sleep(0.2)
        dirty.clear()
        with stats_lock:
            rows = sorted(stats.values(), key=lambda e: (e["host"], e["port"], e["path"]))
            data = json.dumps({"destinations": rows, "route": summary(rows)})
        try:
            with open(REPORT + ".tmp", "w") as handle:
                handle.write(data)
            os.replace(REPORT + ".tmp", REPORT)
        except OSError:
            pass

def summary(rows):
    if ROUTE is None:
        return None
    paths = {}
    for row in rows:
        if row["path"] != "none":
            entry = paths.setdefault(row["path"], {"connections": 0, "bytes_up": 0, "bytes_down": 0})
            for key in entry:
                entry[key] += row[key]
    with route_lock:
        return {"route": ROUTE, "fallback": FALLBACK, "cap_bytes": CAP, "nodes": len(ROUTES),
                "proxied_bytes": route["proxied"], "capped": route["capped"], "failovers": route["failovers"],
                "fallbacks": route["fallbacks"], "paths": paths}

def public(address):
    ip = ipaddress.ip_address(address.split("%%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if TEST_PORT and ip.is_loopback:
        return True
    return ip.is_global and not ip.is_multicast

def literal(host):
    try:
        return str(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        return None

def resolve(host, port):
    if TEST_PORT:
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                 (a, TEST_PORT)) for a in TEST_HOSTS.get(host, [])]
    address = literal(host)
    if address is not None:
        if ALLOWED is not None:
            return []
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))]
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    return [info for info in infos if info[0] in (socket.AF_INET, socket.AF_INET6)]

def socks(port, address, timeout=ROUTE_TIMEOUT):
    # SOCKS5 CONNECT to the route client on loopback, always with the checked IP address.
    sock = socket.create_connection((ROUTE_HOST, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(b"\x05\x01\x00")
        if read_exact(sock, 2) != b"\x05\x00":
            raise OSError("route")
        ip = address[0].split("%%", 1)[0]
        packed = socket.inet_pton(socket.AF_INET6 if ":" in ip else socket.AF_INET, ip)
        sock.sendall(b"\x05\x01\x00" + (b"\x04" if len(packed) == 16 else b"\x01") + packed +
                     int(address[1]).to_bytes(2, "big"))
        reply = read_exact(sock, 4)
        if reply[1] != 0 or reply[3] not in (1, 3, 4):
            raise OSError("route")
        read_exact(sock, (4 if reply[3] == 1 else 16 if reply[3] == 4 else read_exact(sock, 1)[0]) + 2)
        sock.settimeout(IDLE)
        return sock
    except Exception:
        sock.close()
        raise OSError("route")

def node_ok(index):
    # A node that failed one connection is checked against fixed public probes, so an
    # unreachable destination never takes a working node out of service.
    with route_lock:
        if time.time() - route["healthy"].get(index, -1e9) < HEALTHY_FOR:
            return True
    ok, done = [], threading.Event()
    def one(probe):
        try:
            socks(ROUTES[index][1], probe, PROBE_TIMEOUT).close()
            ok.append(probe)
            done.set()
        except OSError:
            pass
    checks = [threading.Thread(target=one, args=(probe,), daemon=True) for probe in PROBES]
    for check in checks:
        check.start()
    deadline = time.time() + PROBE_TIMEOUT + 1
    while not ok and time.time() < deadline and any(check.is_alive() for check in checks):
        done.wait(0.1)
    if ok:
        with route_lock:
            route["healthy"][index] = time.time()
    return bool(ok)

def warm():
    # Once the route client listens: check every node at once and start with the first
    # healthy one in plan order, so a node that is down costs the project no time.
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            socket.create_connection((ROUTE_HOST, ROUTES[0][1]), 1).close()
            break
        except OSError:
            time.sleep(0.5)
    results = {}
    checks = [threading.Thread(target=lambda i=i: results.__setitem__(i, node_ok(i)), daemon=True)
              for i in range(len(ROUTES))]
    for check in checks:
        check.start()
    for check in checks:
        check.join(PROBE_TIMEOUT + 2)
    with route_lock:
        if route["next"] == 0 and not route["failovers"] and not results.get(0):
            first = next((i for i in range(len(ROUTES)) if results.get(i)), len(ROUTES))
            route["next"], route["failovers"] = first, first
            if first >= len(ROUTES):
                route["retry"] = time.time() + RETRY_AFTER
    warmed.set()

def routed(addresses):
    # The current node first; a failing node is skipped from then on (every node failed:
    # retried after RETRY_AFTER s). No working node, or the cap reached: direct with
    # FALLBACK, otherwise refused. Returns (socket, path) or (None, "direct").
    address = sorted(addresses, key=lambda info: info[0] != socket.AF_INET)[0][4]
    warmed.wait(PROBE_TIMEOUT + 4)
    while True:
        with route_lock:
            if not route["capped"] and route["next"] >= len(ROUTES) and time.time() >= route["retry"]:
                route["next"] = 0
            index = None if route["capped"] or route["next"] >= len(ROUTES) else route["next"]
        if index is None:
            break
        try:
            sock = socks(ROUTES[index][1], address)
            with route_lock:
                route["healthy"][index] = time.time()
            return sock, ROUTES[index][0]
        except OSError:
            if node_ok(index):
                raise
            with route_lock:
                if route["next"] == index:
                    route["next"] += 1
                    route["failovers"] += 1
                    route["healthy"].pop(index, None)
                    if route["next"] >= len(ROUTES):
                        route["retry"] = time.time() + RETRY_AFTER
    if not FALLBACK:
        raise PermissionError("route")
    with route_lock:
        route["fallbacks"] += 1
    return None, "direct"

def upstream(host, port):
    # Resolve here and connect only to the addresses just checked: a name that
    # resolves (or later rebinds) to a private address is refused outright.
    if port not in PORTS or not HOST.fullmatch(host) or (ALLOWED is not None and host not in ALLOWED):
        raise PermissionError(host)
    addresses = resolve(host, port)
    if not addresses or not all(public(info[4][0]) for info in addresses):
        raise PermissionError(host)
    if ROUTE is not None:
        sock, path = routed(addresses)
        if sock is not None:
            return sock, path
    last = None
    for family, kind, proto, _, address in addresses:
        sock = socket.socket(family, kind, proto)
        sock.settimeout(CONNECT_TIMEOUT)
        try:
            sock.connect(address)
            sock.settimeout(IDLE)
            return sock, "direct"
        except OSError as error:
            last = error
            sock.close()
    raise last or OSError(host)

def pipe(source, target, counter, proxied=False):
    try:
        while True:
            data = source.recv(65536)
            if not data:
                break
            target.sendall(data)
            counter[0] += len(data)
            if proxied:
                with route_lock:
                    route["proxied"] += len(data)
                    if route["proxied"] >= CAP:
                        route["capped"] = True
                    stop = route["capped"]
                if stop:
                    break
    except OSError:
        pass
    finally:
        for sock, how in ((target, socket.SHUT_WR), (source, socket.SHUT_RD)):
            try:
                sock.shutdown(how)
            except OSError:
                pass

def splice(client, remote, host, port, first=b""):
    remote, path = remote
    proxied = path != "direct"
    up, down = [len(first)], [0]
    if proxied and first:
        with route_lock:
            route["proxied"] += len(first)
    try:
        if first:
            remote.sendall(first)
        other = threading.Thread(target=pipe, args=(remote, client, down, proxied), daemon=True)
        other.start()
        pipe(client, remote, up, proxied)
        other.join(IDLE)
    finally:
        note(host, port, path, up=up[0], down=down[0])

def open_upstream(host, port):
    try:
        remote, path = upstream(host, port)
    except OSError:
        # Refused (not public, not allowed, other port, no route) or unreachable: never opened.
        note(host, port, refused=True)
        raise
    note(host, port, path, opened=True)
    return remote, path

def read_exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError()
        data += chunk
    return data

def server_name(hello):
    # TLS record (5 bytes) + handshake header (4) + version (2) + random (32).
    pos = 5 + 4 + 2 + 32
    pos += 1 + hello[pos]                                   # session id
    pos += 2 + int.from_bytes(hello[pos:pos + 2], "big")    # cipher suites
    pos += 1 + hello[pos]                                   # compression methods
    end = pos + 2 + int.from_bytes(hello[pos:pos + 2], "big")
    pos += 2
    while pos + 4 <= end:
        kind, size = int.from_bytes(hello[pos:pos + 2], "big"), int.from_bytes(hello[pos + 2:pos + 4], "big")
        pos += 4
        if kind == 0:                                       # server_name
            names, cursor = hello[pos + 2:pos + size], 0
            while cursor + 3 <= len(names):
                length = int.from_bytes(names[cursor + 1:cursor + 3], "big")
                if names[cursor] == 0:
                    return names[cursor + 3:cursor + 3 + length].decode("ascii").lower().rstrip(".")
                cursor += 3 + length
        pos += size
    raise ValueError("no server name")

def handle_tls(client):
    header = read_exact(client, 5)
    if header[0] != 0x16 or not 0 < int.from_bytes(header[3:5], "big") <= 16384:
        raise ValueError("not tls")
    hello = header + read_exact(client, int.from_bytes(header[3:5], "big"))
    if hello[5] != 1:
        raise ValueError("not a client hello")
    host = server_name(hello)
    remote = open_upstream(host, 443)
    try:
        splice(client, remote, host, 443, hello)
    finally:
        remote[0].close()

def read_head(client):
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = client.recv(4096)
        if not chunk or len(head) + len(chunk) > 16384:
            raise ValueError("bad request")
        head += chunk
    return head

def handle_http(client):
    head = read_head(client)
    match = re.search(rb"\r\nhost:[ \t]*([^\r\n]+)", head, re.I)
    if not match:
        raise ValueError("no host")
    host = match.group(1).decode("latin-1").strip().lower().rstrip(".")
    host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    try:
        remote = open_upstream(host, 80)
    except PermissionError:
        client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        return
    try:
        splice(client, remote, host, 80, head)
    finally:
        remote[0].close()

def handle_connect(client):
    head = read_head(client)
    line = head.split(b"\r\n", 1)[0].decode("latin-1").split(" ")
    if len(line) != 3 or line[0] != "CONNECT" or not line[2].startswith("HTTP/1."):
        client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        return
    host, _, port = line[1].rpartition(":")
    host = host.lower().rstrip(".")
    try:
        remote = open_upstream(host, int(port) if port.isdigit() else -1)
    except PermissionError:
        client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        return
    except OSError:
        client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        return
    try:
        client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        splice(client, remote, host, int(port), head.split(b"\r\n\r\n", 1)[1])
    finally:
        remote[0].close()

def serve(port, handler):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", port))
    listener.listen(128)
    def run(client):
        try:
            client.settimeout(IDLE)
            handler(client)
        except Exception as error:
            if isinstance(error, PermissionError):
                print("refused " + str(error)[:253], file=sys.stderr, flush=True)
        finally:
            client.close()
            slots.release()
    while True:
        client, _ = listener.accept()
        if not slots.acquire(blocking=False):
            client.close()
            continue
        threading.Thread(target=run, args=(client,), daemon=True).start()

def serve_dns(address):
    # Every A question is answered with this sidecar's own address (TTL 5 s);
    # other types get an empty answer. The real resolution happens per connection.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", DNS_PORT))
    own = socket.inet_aton(address)
    while True:
        try:
            data, peer = sock.recvfrom(512)
            if len(data) < 17 or data[2] & 0x80 or int.from_bytes(data[4:6], "big") != 1:
                continue
            pos = 12
            while data[pos]:
                pos += 1 + data[pos]
                if pos >= len(data) - 4:
                    raise ValueError()
            qtype, end = int.from_bytes(data[pos + 1:pos + 3], "big"), pos + 5
            answer = b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x05\x00\x04" + own if qtype == 1 else b""
            flags = b"\x81\x80" if data[2] & 0x01 else b"\x80\x80"
            sock.sendto(data[:2] + flags + b"\x00\x01" + (b"\x00\x01" if answer else b"\x00\x00") +
                        b"\x00\x00\x00\x00" + data[12:end] + answer, peer)
        except Exception:
            continue

threading.Thread(target=writer, daemon=True).start()
threading.Thread(target=serve, args=(TLS_PORT, handle_tls), daemon=True).start()
threading.Thread(target=serve, args=(CONNECT_PORT, handle_connect), daemon=True).start()
if 80 in PORTS:
    threading.Thread(target=serve, args=(HTTP_PORT, handle_http), daemon=True).start()
if ROUTES:
    threading.Thread(target=warm, daemon=True).start()
else:
    warmed.set()
if DNS:
    threading.Thread(target=serve_dns, args=(socket.gethostbyname(socket.gethostname()),), daemon=True).start()
print("READY", flush=True)
threading.Event().wait()
"""

OPEN_PORTS = (443, 80)
REPORT_PATH = "/tmp/egress.json"


def render_team_proxy_script(domains: list[str] | None, *, connect_port: int = CONNECT_PORT,
                             tls_port: int = TLS_PORT, http_port: int = 80, dns_port: int = 53,
                             report: str = REPORT_PATH, test_hosts: dict[str, list[str]] | None = None,
                             test_port: int = 0, dns: bool | None = None, route: dict | None = None,
                             routes: list[tuple[str, int]] = (), route_host: str = "127.0.0.1",
                             probes: list[tuple[str, int]] = (("1.1.1.1", 443), ("223.5.5.5", 443))) -> bytes:
    """The sidecar's exact source. ``domains=None`` is open mode (any public destination on
    port 443 or 80, sidecar DNS); a list is the allow-list mode (port 443 only).
    ``route`` (open mode only; checked_route) with ``routes``, the (node label, local SOCKS
    port) pairs in the order to try: never any node setting.
    ``test_hosts``/``test_port`` are for tests only: names resolve from that map (loopback
    allowed) and connections go to ``test_port``."""
    if domains is not None and not all(valid_domain(d) or (test_port and d.endswith(".test")) for d in domains):
        raise JobError("invalid_team_egress")
    open_mode = domains is None
    if route is not None and (not open_mode or not all(PATH_LABEL.fullmatch(label) and label not in ("direct", "none")
                                                       and isinstance(port, int) for label, port in routes)):
        raise JobError("invalid_team_egress")
    return (PROXY_SCRIPT % {
        "allowed": "None" if open_mode else json.dumps(sorted(domains)),
        "ports": json.dumps(list(OPEN_PORTS) if open_mode else [443]),
        "dns": int(open_mode if dns is None else dns), "report": json.dumps(report),
        "test_hosts": json.dumps(test_hosts or {}), "test_port": test_port, "connect_port": connect_port,
        "tls_port": tls_port, "http_port": http_port, "dns_port": dns_port,
        "route": json.dumps(route["name"]) if route else "None",
        "routes": json.dumps([[label, port] for label, port in routes] if route else []),
        "fallback": "True" if route and route["fallback"] else "False",
        "cap": route["cap_bytes"] if route else 0, "route_host": json.dumps(route_host),
        "probes": json.dumps([[host, port] for host, port in probes])}).encode()


def checked_report(value: object) -> list[dict]:
    """The sidecar's record, re-validated on the trusted side (it is still trusted code,
    but the participant chose every host name in it). ``value`` is the sidecar's report
    ({"destinations": [...], "route": {...}}) or, from earlier sidecars, the list itself."""
    if isinstance(value, dict):
        value = value.get("destinations")
    if not isinstance(value, list):
        return []
    rows = []
    for entry in value[:501]:
        if not isinstance(entry, dict):
            continue
        host, port = entry.get("host"), entry.get("port")
        numbers = [entry.get(k) for k in ("connections", "refused", "bytes_up", "bytes_down")]
        if (not isinstance(host, str) or not re.fullmatch(r"[a-z0-9.:_\[\]()-]{1,253}", host) or
                not isinstance(port, int) or not 0 <= port <= 65535 or
                not all(isinstance(n, int) and 0 <= n < 2 ** 53 for n in numbers) or
                not all(isinstance(entry.get(k), str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ",
                                                                       entry[k]) for k in ("first", "last"))):
            continue
        path = entry.get("path", "direct")
        if not isinstance(path, str) or not PATH_LABEL.fullmatch(path):
            continue
        rows.append({"host": host, "port": port, "path": path, "connections": numbers[0], "refused": numbers[1],
                     "bytes_up": numbers[2], "bytes_down": numbers[3], "first": entry["first"],
                     "last": entry["last"]})
    return rows


def checked_route_summary(value: object) -> dict | None:
    """The sidecar's route summary (labels and counters only), re-validated."""
    if isinstance(value, dict):
        value = value.get("route")
    if not isinstance(value, dict):
        return None
    count = lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 2 ** 53  # noqa: E731
    paths = value.get("paths")
    if (value.get("route") not in ROUTE_NAMES or not isinstance(value.get("fallback"), bool) or
            not isinstance(value.get("capped"), bool) or
            not all(count(value.get(k)) for k in ("cap_bytes", "nodes", "proxied_bytes", "failovers", "fallbacks")) or
            not isinstance(paths, dict) or len(paths) > MAX_ROUTE_NODES + 1 or
            not all(isinstance(label, str) and PATH_LABEL.fullmatch(label) and isinstance(entry, dict) and
                    set(entry) == {"connections", "bytes_up", "bytes_down"} and all(count(n) for n in entry.values())
                    for label, entry in paths.items())):
        return None
    return {key: value[key] for key in ("route", "fallback", "cap_bytes", "nodes", "proxied_bytes", "capped",
                                        "failovers", "fallbacks")} | {"paths": {k: dict(v) for k, v in paths.items()}}


ROUTE_TITLES = {"cn": "China route", "overseas": "overseas route"}


def report_text(rows: list[dict], route: dict | None = None) -> str:
    """The team-facing summary appended to its own run log (labels only, never a node address)."""
    lines = []
    if route is not None:
        lines += ["", f"--- Egress route: {ROUTE_TITLES[route['route']]} (automatic fallback to direct: "
                      f"{'on' if route['fallback'] else 'off'}) ---",
                  f"proxied bytes {route['proxied_bytes']} of {route['cap_bytes']}"
                  f"{' (limit reached: later connections ' + ('went direct' if route['fallback'] else 'were refused') + ')' if route['capped'] else ''}"
                  f"; node switches {route['failovers']}; connections sent direct {route['fallbacks']}"
                  f"{'; no route node was available' if not route['nodes'] else ''}"]
        for label, entry in sorted(route["paths"].items()):
            lines.append(f"path {label}: {entry['connections']} connections, sent {entry['bytes_up']} bytes, "
                         f"received {entry['bytes_down']} bytes")
    if not rows:
        return "\n".join(lines + ["", "--- Network connections during this run (platform record) ---", "none"]) + "\n"
    lines += ["", "--- Network connections during this run (platform record; no content is recorded) ---",
              "destination | path | connections | refused | sent bytes | received bytes | first | last (UTC)"]
    for row in sorted(rows, key=lambda r: (-(r["bytes_up"] + r["bytes_down"]), r["host"], r["port"], r.get("path", "direct"))):
        lines.append(f"{row['host']}:{row['port']} | {row.get('path', 'direct')} | {row['connections']} | {row['refused']} | "
                     f"{row['bytes_up']} | {row['bytes_down']} | {row['first']} | {row['last']}")
    return "\n".join(lines) + "\n"


class TeamEgress:
    """Per-run internal network plus the pinned forwarder (open or allow-list mode)."""

    def __init__(self, domains: list[str], *, client_env: dict[str, str], open: bool = False,
                 route: dict | None = None):
        self.open = open
        self.domains = [] if open else list(domains)
        self.report: list[dict] = []
        self.route = route if open else None
        self.route_summary: dict | None = None
        # (label, local port, node): a random node first for "overseas", then the others.
        self._plan = route_plan(self.route)
        self.client_env = dict(client_env)
        suffix = uuid.uuid4().hex
        self.network = "observer-egress-" + suffix
        self.proxy_name = "observer-proxy-" + suffix
        self.route_name = "observer-route-" + suffix
        self.address: str | None = None
        self.process: subprocess.Popen | None = None
        self._pulled = False
        self._log = bytearray()
        self._stderr_thread: threading.Thread | None = None
        self._script = render_team_proxy_script(None if open else self.domains, route=self.route,
                                                routes=[(label, port) for label, port, _ in self._plan])
        self._route_process: subprocess.Popen | None = None

    @property
    def environment(self) -> dict[str, str]:
        """Proxy settings for the participant container (lower- and upper-case spellings)."""
        proxy = f"http://{self.address}:{CONNECT_PORT}"
        if self.open:
            # Plain http:// needs no proxy: the sidecar's DNS sends it to port 80, spliced by Host.
            return {"HTTPS_PROXY": proxy, "https_proxy": proxy, "NO_PROXY": "", "no_proxy": "",
                    "NODE_USE_ENV_PROXY": "1"}
        return {"HTTPS_PROXY": proxy, "https_proxy": proxy, "HTTP_PROXY": proxy, "http_proxy": proxy,
                "NO_PROXY": "", "no_proxy": "", "NODE_USE_ENV_PROXY": "1"}

    @property
    def hosts(self) -> list[str]:
        """``--add-host`` values (allow-list mode): every allowed domain points at the sidecar."""
        return [f"{domain}:{self.address}" for domain in self.domains]

    @property
    def dns(self) -> str | None:
        """The participant's DNS server (open mode): the sidecar answers every name with itself."""
        return self.address if self.open else None

    def collect(self) -> list[dict]:
        """The sidecar's per-destination record (best effort; empty if it cannot be read)."""
        if self.process is None:
            return self.report
        time.sleep(1)  # the sidecar writes its record within 0.2 s of the last change
        try:
            result = subprocess.run(["docker", "exec", self.proxy_name, "cat", REPORT_PATH], env=self.client_env,
                                    capture_output=True, text=True, timeout=_DOCKER_TIMEOUT)
            if result.returncode == 0 and len(result.stdout) < 2_000_000:
                value = json.loads(result.stdout)
                self.report = checked_report(value)
                self.route_summary = checked_route_summary(value) if self.route else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
        return self.report

    @property
    def log(self) -> str:
        return bytes(self._log[-4096:]).decode(errors="replace")

    def _docker(self, *args: str, timeout: int = _DOCKER_TIMEOUT) -> str:
        try:
            result = subprocess.run(["docker", *args], env=self.client_env,
                                    capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise JobError("egress_network_unavailable") from exc
        if result.returncode:
            raise JobError("egress_network_unavailable")
        return result.stdout.strip()

    def _docker_quiet(self, *args: str) -> None:
        try:
            subprocess.run(["docker", *args], env=self.client_env, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=_DOCKER_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def pull(self) -> None:
        if not self._pulled:
            self._docker("pull", "--quiet", PROXY_IMAGE, timeout=_PULL_TIMEOUT)
            if self._plan:
                try:
                    self._docker("pull", "--quiet", ROUTE_IMAGE, timeout=_PULL_TIMEOUT)
                except JobError:
                    pass  # no route client: the sidecar falls back (or refuses), the run goes on
            self._pulled = True

    def start(self) -> "TeamEgress":
        try:
            self.pull()
            self._docker("network", "create", "--internal", "--label", "observer.project-runtime=true", self.network)
            command = ["docker", "run", "--rm", "--interactive", "--name", self.proxy_name,
                       "--label", "observer.project-runtime=true", "--network", self.network,
                       # Public resolvers: a host resolver that answers with placeholder
                       # (non-public) addresses would otherwise refuse every domain.
                       "--dns", "1.1.1.1", "--dns", "8.8.8.8",
                       # Port 443 without any capability: only this namespaced sysctl.
                       "--sysctl", "net.ipv4.ip_unprivileged_port_start=0",
                       "--user", "65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                       "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=16m",
                       "--memory", "256m", "--pids-limit", "256", "--cpus", "1",
                       "--log-driver", "none", "--entrypoint", "python3", PROXY_IMAGE, "-u", "-"]
            try:
                self.process = subprocess.Popen(command, env=self.client_env, stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            except OSError:
                raise JobError("egress_proxy_failed") from None
            try:
                self.process.stdin.write(self._script)
                self.process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            self._stderr_thread = threading.Thread(target=self._drain, args=(self.process.stderr,), daemon=True)
            self._stderr_thread.start()
            self._await_ready()
            address = self._docker("inspect", "--format",
                                   "{{(index .NetworkSettings.Networks \"" + self.network + "\").IPAddress}}",
                                   self.proxy_name)
            if not re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", address):
                raise JobError("egress_proxy_failed")
            self.address = address
            # Upstream-facing on the default bridge; the participant never joins it.
            self._docker("network", "connect", "bridge", self.proxy_name)
            self._start_route()
        except Exception:
            self.close()
            raise
        return self

    def _start_route(self) -> None:
        """The route client in the sidecar's network namespace (loopback SOCKS ports only).
        Its settings go to its standard input: never a file, argument, environment or log.
        A client that does not come up leaves the sidecar to fall back (or refuse)."""
        if not self._plan:
            return
        command = ["docker", "run", "--rm", "--interactive", "--name", self.route_name,
                   "--label", "observer.project-runtime=true", "--network", "container:" + self.proxy_name,
                   "--user", "65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only",
                   "--memory", "256m", "--pids-limit", "256", "--cpus", "1", "--log-driver", "none",
                   "--entrypoint", "sing-box", ROUTE_IMAGE, "run", "-c", "stdin", "--disable-color"]
        try:
            self._route_process = subprocess.Popen(command, env=self.client_env, stdin=subprocess.PIPE,
                                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._route_process.stdin.write(route_client_config(self._plan))
            self._route_process.stdin.close()
        except OSError:
            return
        probe = "import socket;socket.create_connection(('127.0.0.1',%d),2).close()" % self._plan[0][1]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and self._route_process.poll() is None:
            try:
                if subprocess.run(["docker", "exec", self.proxy_name, "python3", "-c", probe], env=self.client_env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=_DOCKER_TIMEOUT).returncode == 0:
                    return
            except (OSError, subprocess.TimeoutExpired):
                pass
            time.sleep(0.5)

    def _await_ready(self) -> None:
        deadline = time.monotonic() + 60
        stdout = self.process.stdout if self.process is not None else None
        buffer = b""
        while stdout is not None and time.monotonic() < deadline:
            ready, _, _ = select.select([stdout], [], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                break
            chunk = os.read(stdout.fileno(), 64)
            if not chunk:
                break
            buffer += chunk
            if b"\n" in buffer:
                if buffer.split(b"\n", 1)[0] == b"READY":
                    return
                break
        raise JobError("egress_proxy_failed")

    def _drain(self, stream) -> None:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                return
            self._log.extend(chunk)
            if len(self._log) > 8192:
                del self._log[:-4096]

    def close(self) -> None:
        self.collect()
        route, self._route_process = self._route_process, None
        if route is not None:
            self._docker_quiet("rm", "--force", self.route_name)
            try:
                route.wait(timeout=5)
            except subprocess.TimeoutExpired:
                route.kill()
                route.wait()
        self._docker_quiet("rm", "--force", self.proxy_name)
        process, self.process = self.process, None
        if process is not None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    stream.close()
                except (OSError, AttributeError):
                    pass
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=2)
            self._stderr_thread = None
        self._docker_quiet("network", "rm", self.network)


assert NETWORK_PATTERN.fullmatch("observer-egress-" + "0" * 32)
