"""Trusted workflow HTTP transport; credentials stay in process memory."""
from __future__ import annotations

import base64
import json
import os
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping


# A scrubbed participant log, base64 encoded in one bounded request.
AGENT_LOG_MAX_BYTES = 2 * 1024 * 1024 + 65536
AGENT_LOG_MAX_BODY = 3 * 1024 * 1024


class JobError(RuntimeError):
    """A fixed error code, safe to send to the job receipt or Actions log."""


# Every job API action is backend-idempotent for the claiming run, so a
# transient failure (network, 429, 5xx incl. edge 52x, cold starts) is retried
# with the same body for about a minute before a whole evaluation is given up.
RETRY_DELAYS = (2, 5, 10, 20, 30)


def transient(error: JobError) -> bool:
    code = str(error)
    return code == "job_network_error" or code == "job_http_429" or code.startswith("job_http_5")


def retrying(operation: Callable[[], object]):
    """Run an idempotent operation, riding out transient errors (RETRY_DELAYS, jittered)."""
    for delay in (*RETRY_DELAYS, None):
        try:
            return operation()
        except JobError as exc:
            if not transient(exc) or delay is None:
                raise
            time.sleep(delay * random.uniform(0.8, 1.2))


def checked_url(url: str, *, local: bool = False) -> str:
    try:
        parts = urllib.parse.urlsplit(url)
        valid = parts.scheme == "https" and bool(parts.hostname)
        valid |= local and parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost")
        if not valid or parts.username or parts.password or parts.fragment or parts.port not in (None, 443) and not local:
            raise ValueError()
    except (TypeError, ValueError):
        raise JobError("invalid_job_endpoint") from None
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise JobError("job_redirect_rejected")


class Http:
    def __init__(self, *, local: bool = False):
        self.local = local
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, url: str, *, data: bytes | None = None, headers: Mapping[str, str] | None = None,
                method: str = "GET", limit: int = 2 * 1024 * 1024, timeout: int = 60) -> bytes:
        checked_url(url, local=self.local)
        request = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise JobError("job_response_too_large")
                return body
        except urllib.error.HTTPError as exc:
            exc.close()
            # Never include the URL, response, exception message or bearer token.
            raise JobError("job_http_" + str(exc.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise JobError("job_network_error") from None

    def json(self, url: str, *, body: dict | None = None, bearer: str | None = None,
             max_body: int = 1100000, timeout: int = 60) -> dict:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, allow_nan=False, separators=(",", ":")).encode()
            if len(data) > max_body:
                raise JobError("job_request_too_large")
            headers["Content-Type"] = "application/json"
        if bearer is not None:
            headers["Authorization"] = "Bearer " + bearer
        raw = self.request(url, data=data, headers=headers, method="POST" if body is not None else "GET",
                           timeout=timeout)
        try:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (UnicodeError, ValueError):
            raise JobError("invalid_job_response") from None


class GitHubIdentity:
    """Fetch a fresh OIDC identity for claim and again after a potentially long run."""

    def __init__(self, environment: Mapping[str, str] | None = None):
        env = environment if environment is not None else os.environ
        self.url = env.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
        self.token = env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
        checked_url(self.url)
        hostname = urllib.parse.urlsplit(self.url).hostname or ""
        if not hostname.endswith(".actions.githubusercontent.com") or not self.token:
            raise JobError("github_identity_unavailable")

    def __call__(self) -> str:
        parts = urllib.parse.urlsplit(self.url)
        query = [(key, val) for key, val in urllib.parse.parse_qsl(parts.query) if key != "audience"]
        query.append(("audience", "agentic-observer26"))
        url = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))
        response = Http().json(url, bearer=self.token)
        value = response.get("value")
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", value):
            raise JobError("invalid_github_identity")
        return value


class JobClient:
    def __init__(self, url: str, job_id: str, nonce: str | None, identity: Callable[[], str], *,
                 http: Http | None = None, seal_key=None):
        self.http = http or Http()
        checked_url(url, local=self.http.local)
        if not re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", job_id):
            raise JobError("invalid_job_id")
        # A public-pool job has no nonce: its dispatch inputs are public, so the
        # backend binds the claim to the exact GitHub run it dispatched instead,
        # and everything it returns is sealed to this job's in-memory key.
        if (seal_key is None) == (nonce is None):
            raise JobError("invalid_job_nonce")
        if nonce is not None and not re.fullmatch(r"[A-Za-z0-9_-]{40,100}", nonce):
            raise JobError("invalid_job_nonce")
        self.url, self.job_id, self.nonce, self.identity = url, job_id, nonce, identity
        self.seal_key = seal_key

    def _call(self, action: str, *, max_body: int = 1100000, timeout: int = 60, **fields):
        # Every action is backend-idempotent; retry exactly the same body.
        body = {"action": action, "job_id": self.job_id, **fields}

        def once():
            response = self.http.json(self.url, body=body, bearer=self.identity(), max_body=max_body, timeout=timeout)
            value = response.get("data")
            if not isinstance(value, dict):
                raise JobError("invalid_job_response")
            return value

        return retrying(once)

    def claim(self) -> dict:
        if self.seal_key is None:
            return self._call("claim", nonce=self.nonce)
        # The backend fetches, seals and stages the inputs before answering.
        response = self._call("claim", runner_key=self.seal_key.public_text, timeout=150)
        sealed = response.get("sealed")
        if set(response) != {"sealed"} or not isinstance(sealed, str):
            raise JobError("invalid_job_response")
        try:
            raw = base64.b64decode(sealed, validate=True)
            value = json.loads(self.seal_key.open(raw, "claim:" + self.job_id))
        except (ValueError, UnicodeError):
            raise JobError("invalid_job_response") from None
        if not isinstance(value, dict):
            raise JobError("invalid_job_response")
        return value

    def result_upload(self) -> dict:
        # Issued only now: a signed upload URL from claim time could expire
        # during a long evaluation.
        value = self._call("result_upload")
        if set(value) != {"url", "path"} or not all(isinstance(v, str) for v in value.values()):
            raise JobError("invalid_job_response")
        return value

    def store_sealed_result(self) -> str:
        # The backend opens the sealed result it just received and commits it to
        # the team's private repository; this runner never holds a repository token.
        # Storing is idempotent; _call rides out transient failures.
        path = self._call("store_result").get("result_path")
        if not isinstance(path, str) or not path.startswith("github:"):
            raise JobError("invalid_job_response")
        return path

    def artifact_repository(self) -> dict:
        # A fresh OIDC identity and short-lived token are obtained only when the
        # trusted preparation/scoring job needs to write its immutable artifact.
        return self._call("artifact_repository")

    def agent_log(self, text: str) -> None:
        # Sent before the receipt. The nonce lets the backend re-read this
        # claimed job and bind the log to its own run, never a chosen run ID.
        data = text.encode()[-AGENT_LOG_MAX_BYTES:]
        self._call("agent_log", nonce=self.nonce, log=base64.b64encode(data).decode(), max_body=AGENT_LOG_MAX_BODY)

    def complete(self, result: dict, *, error: str = "") -> None:
        self._call("complete", result=result, error=error)
