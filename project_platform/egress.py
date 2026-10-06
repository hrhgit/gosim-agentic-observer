"""Model-proxy-only egress for a participant container.

With restricted egress the participant container does not join Docker's default
bridge. It runs on a per-run internal network (no routed access to anything)
whose only other member is a small forwarder container: trusted engine code on a
pinned image, dual-homed onto the default bridge. The participant's single
reachable service is that forwarder, which relays POSTs under a random per-run
path prefix to the pinned observer-model endpoint and nothing else.

The forwarder holds no credentials: the participant's own scoped model
credential travels in each request's Authorization header, untouched. The
guarantee comes from Docker networking alone, so it does not depend on host
firewall rules.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import select
import subprocess
import threading
import time
import urllib.parse
import uuid

from .job_client import JobError

# Pinned runtime of the trusted forwarder container (never participant code):
# the multi-platform python:3.12-slim index. Bump deliberately with a review.
PROXY_IMAGE = "python@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"
PROXY_PORT = 8321
UPSTREAM_TIMEOUT_SECONDS = 300
NETWORK_PATTERN = re.compile(r"^observer-egress-[0-9a-f]{32}$")
_DOCKER_TIMEOUT = 30
_PULL_TIMEOUT = 300
# Local test stubs on the engine host are reached through this alias.
_HOST_ALIAS = "observer-model-host"


def checked_upstream(value: str, *, local: bool = False) -> str:
    """The observer-model base: HTTPS in production, a loopback stub in tests."""
    try:
        parts = urllib.parse.urlsplit(value)
        https = parts.scheme == "https" and bool(parts.hostname) and parts.port in (None, 443)
        loopback = local and parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost")
        if not (https or loopback) or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError()
    except (TypeError, ValueError):
        raise JobError("invalid_egress_upstream") from None
    base = value.rstrip("/")
    if not base.endswith("/v1"):
        raise JobError("invalid_egress_upstream")
    return base


def anthropic_base(openai_base: str) -> str:
    """The official Anthropic SDK appends "/v1/messages" to its own base_url, unlike
    the OpenAI SDK's "/v1" + "/chat/completions" convention. Both point at the same
    proxy; this only strips the "/v1" suffix so the SDK's own "/v1/messages" lands
    on the right path."""
    base = openai_base.rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


# The forwarder running inside the sidecar container (standard library only).
# UPSTREAM and PREFIX are baked in by the trusted engine; unit tests run this
# exact script in a local process.
PROXY_SCRIPT = r"""
import json, re, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
UPSTREAM = %(upstream)s
PREFIX = %(prefix)s
SUFFIX = re.compile(r"[A-Za-z0-9][A-Za-z0-9/_.-]*$")
FORWARDED = ("Authorization", "Content-Type", "Accept", "Idempotency-Key", "X-Api-Key", "Anthropic-Version",
             "Anthropic-Beta")
MAX_REQ, MAX_RESP = 8 * 1024 * 1024, 8 * 1024 * 1024

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None

# No proxy settings from the image environment; only the pinned upstream.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)

class Refused(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, status, body, ctype="application/json", request_id=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if request_id:
            self.send_header("x-observer-request-id", request_id)
        self.end_headers()
        self.wfile.write(body)

    def refuse(self, status, code):
        self.close_connection = True
        self.reply(status, json.dumps({"error": {"type": "observer_error", "code": code, "message": code}}).encode())

    def do_GET(self):
        self.refuse(405, "method_not_allowed")

    do_PUT = do_DELETE = do_PATCH = do_GET

    def body(self):
        if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
            data = bytearray()
            while True:
                try:
                    size = int(self.rfile.readline(64).split(b";", 1)[0].strip(), 16)
                except ValueError:
                    raise Refused(400, "invalid_egress_request")
                if size == 0:
                    while self.rfile.readline(1024).strip():
                        pass
                    return bytes(data)
                if len(data) + size > MAX_REQ:
                    raise Refused(413, "egress_request_too_large")
                data += self.rfile.read(size)
                self.rfile.readline(8)
        elif self.headers.get("Transfer-Encoding") is not None:
            raise Refused(400, "invalid_egress_request")
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            raise Refused(411, "invalid_egress_request")
        if length < 0 or length > MAX_REQ:
            raise Refused(413, "egress_request_too_large")
        return self.rfile.read(length)

    def do_POST(self):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        try:
            if not path.startswith(PREFIX + "/"):
                raise Refused(403, "egress_denied")
            suffix = path[len(PREFIX) + 1:]
            if not SUFFIX.fullmatch(suffix) or ".." in suffix or "//" in suffix:
                raise Refused(403, "egress_denied")
            data = self.body()
        except Refused as refused:
            return self.refuse(refused.status, refused.code)
        # Only protocol headers travel; client identity and cookies never do.
        headers = {k: self.headers[k] for k in FORWARDED if self.headers.get(k) is not None}
        request = urllib.request.Request(UPSTREAM + "/" + suffix, data=data, headers=headers, method="POST")
        try:
            with OPENER.open(request, timeout=%(timeout)d) as response:
                payload = response.read(MAX_RESP + 1)
                if len(payload) > MAX_RESP:
                    return self.refuse(502, "egress_upstream_error")
                self.reply(response.status, payload, response.headers.get("Content-Type") or "application/json",
                           response.headers.get("x-observer-request-id"))
        except urllib.error.HTTPError as error:
            try:
                if 300 <= error.code < 400:
                    return self.refuse(502, "egress_upstream_error")
                payload = error.read(MAX_RESP + 1)
            finally:
                error.close()
            if len(payload) > MAX_RESP:
                return self.refuse(502, "egress_upstream_error")
            headers = error.headers
            self.reply(error.code, payload, (headers.get("Content-Type") if headers else None) or "application/json",
                       headers.get("x-observer-request-id") if headers else None)
        except Exception:
            self.refuse(502, "egress_upstream_error")

server = ThreadingHTTPServer(("0.0.0.0", %(port)d), Handler)
server.daemon_threads = True
print("READY", flush=True)
server.serve_forever()
"""


def render_proxy_script(upstream: str, prefix: str, port: int = PROXY_PORT) -> bytes:
    """The sidecar's exact source with its pinned upstream and run prefix baked in."""
    return (PROXY_SCRIPT % {"upstream": json.dumps(upstream), "prefix": json.dumps(prefix),
                            "timeout": UPSTREAM_TIMEOUT_SECONDS, "port": port}).encode()


class RestrictedEgress:
    """Per-run internal network plus the pinned forwarder sidecar."""

    def __init__(self, upstream_base: str, *, client_env: dict[str, str], local: bool = False):
        self.upstream = checked_upstream(upstream_base, local=local)
        self.local = local
        self.client_env = dict(client_env)
        self.prefix = "/" + secrets.token_urlsafe(24) + "/v1"
        suffix = uuid.uuid4().hex
        self.network = "observer-egress-" + suffix
        self.proxy_name = "observer-proxy-" + suffix
        self.process: subprocess.Popen | None = None
        self._pulled = False
        self._log = bytearray()
        self._stderr_thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        """Participant-side OPENAI_BASE_URL; resolvable only on this run's network."""
        return f"http://{self.proxy_name}:{PROXY_PORT}{self.prefix}"

    @property
    def anthropic_base_url(self) -> str:
        """Participant-side ANTHROPIC_BASE_URL: the same sidecar and run prefix as
        base_url, minus the "/v1" segment the Anthropic SDK appends itself. Both
        land on the one fixed prefix the sidecar was started with."""
        return f"http://{self.proxy_name}:{PROXY_PORT}" + anthropic_base(self.prefix)

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
            subprocess.run(["docker", *args], env=self.client_env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=_DOCKER_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def pull(self) -> None:
        """Fetch the pinned forwarder image; may overlap the project build."""
        if not self._pulled:
            self._docker("pull", "--quiet", PROXY_IMAGE, timeout=_PULL_TIMEOUT)
            self._pulled = True

    def _target(self) -> tuple[str, list[str]]:
        """Upstream as the sidecar sees it, plus any host alias it needs."""
        parts = urllib.parse.urlsplit(self.upstream)
        if not (self.local and parts.hostname in ("127.0.0.1", "localhost")):
            return self.upstream, []
        # A loopback test stub runs on the engine host. Native Linux reaches it
        # at the bridge gateway (host-gateway); a Docker VM may need its own
        # host address, given by the test environment.
        gateway = os.environ.get("OBSERVER_TEST_HOST_GATEWAY", "host-gateway")
        netloc = _HOST_ALIAS + (":" + str(parts.port) if parts.port else "")
        return urllib.parse.urlunsplit(parts._replace(netloc=netloc)), ["--add-host", _HOST_ALIAS + ":" + gateway]

    def start(self) -> "RestrictedEgress":
        upstream, hosts = self._target()
        try:
            self.pull()
            # An internal network has no gateway, no masquerade, no external route.
            self._docker("network", "create", "--internal", "--label", "observer.project-runtime=true", self.network)
            command = ["docker", "run", "--rm", "--interactive", "--name", self.proxy_name,
                       "--label", "observer.project-runtime=true", "--network", self.network, *hosts,
                       "--user", "65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                       "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=16m",
                       "--memory", "256m", "--pids-limit", "128", "--cpus", "1",
                       "--log-driver", "none", "--entrypoint", "python3", PROXY_IMAGE, "-u", "-"]
            try:
                self.process = subprocess.Popen(command, env=self.client_env, stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            except OSError:
                raise JobError("egress_proxy_failed") from None
            try:
                self.process.stdin.write(render_proxy_script(upstream, self.prefix))
                self.process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            self._stderr_thread = threading.Thread(target=self._drain, args=(self.process.stderr,), daemon=True)
            self._stderr_thread.start()
            self._await_ready()
            # Dual-home the forwarder: participant-facing on the internal network,
            # upstream-facing on the default bridge. The participant never joins it.
            self._docker("network", "connect", "bridge", self.proxy_name)
        except Exception:
            self.close()
            raise
        return self

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
