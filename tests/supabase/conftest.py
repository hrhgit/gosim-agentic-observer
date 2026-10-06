from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Harness, anon_key, service_key  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def hs():
    h = Harness().start()
    # point the worker at the harness
    os.environ["SUPABASE_URL"] = h.url
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = service_key()
    os.environ["SAC_DATA_DIR"] = str(h.dir / "worker")
    os.environ["SAC_AGENT_TIMEOUT_SECONDS"] = "120"
    os.environ["SAC_AGENT_STEP_TIMEOUT_SECONDS"] = "5"
    from worker.config import reset_settings_cache
    reset_settings_cache()
    yield h
    h.stop()


class Client:
    """Tiny supabase-like HTTP client for tests (mirrors what supabase-js does)."""

    def __init__(self, url: str, token: str | None = None):
        self.url = url
        self.token = token or anon_key()

    def call(self, method: str, path: str, body=None, data: bytes | None = None, headers: dict | None = None, raw: bool = False):
        hdrs = {"apikey": anon_key(), "Authorization": f"Bearer {self.token}"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        elif data is not None:
            payload = data
        hdrs.update(headers or {})
        req = urllib.request.Request(self.url + path, data=payload, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = r.read()
                if raw:
                    return r.status, out
                return r.status, (json.loads(out) if out and "json" in r.headers.get("Content-Type", "") else out.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            out = e.read()
            try:
                return e.code, json.loads(out)
            except Exception:
                return e.code, out.decode("utf-8", "replace")

    def rpc(self, name: str, args: dict | None = None):
        return self.call("POST", f"/rest/v1/rpc/{name}", args or {})

    def select(self, table: str, query: str = "select=*"):
        return self.call("GET", f"/rest/v1/{table}?{query}")

    def upload(self, bucket: str, path: str, data: bytes, ctype="application/octet-stream"):
        return self.call("POST", f"/storage/v1/object/{bucket}/{path}", data=data, headers={"Content-Type": ctype})

    def download(self, bucket: str, path: str):
        return self.call("GET", f"/storage/v1/object/{bucket}/{path}", raw=True)


def signup(hs: Harness, email: str, password: str = "password123", name: str = "Tester") -> Client:
    c = Client(hs.url)
    st, sess = c.call("POST", "/auth/v1/signup", {"email": email, "password": password, "data": {"name": name, "locale": "en"}})
    assert st == 200, sess
    return Client(hs.url, sess["access_token"])


@pytest.fixture(scope="session")
def service(hs):
    return Client(hs.url, service_key())
