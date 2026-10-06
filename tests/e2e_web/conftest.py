"""Browser end-to-end tests for the Vue site against the local Supabase-like harness.

Builds web/ with VITE_SUPABASE_URL pointing at the harness, serves dist/ with `vite preview`, drives it with Playwright.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
sys.path.insert(0, str(ROOT / "tests" / "supabase"))
from harness import Harness, anon_key, service_key  # noqa: E402

NODE_BIN = Path(os.environ.get("SAC_NODE_BIN", "/private/tmp/claude-501/-Users-mac-Library-Application-Support-CindyGlobal-owners-ae98ca1d7b2b6ae48f15-dialogues-2026-09-09-f286d255-69dd-408c-a206-ec1ca3e39544/55e5a0de-c283-422f-848b-e4005e339509/scratchpad/tools/node-v22.12.0-darwin-arm64/bin"))
SHOTS = ROOT / "artifacts" / "screenshots-web"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def hs():
    h = Harness().start()
    os.environ["SUPABASE_URL"] = h.url
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = service_key()
    os.environ["SAC_DATA_DIR"] = str(h.dir / "worker")
    os.environ["SAC_AGENT_TIMEOUT_SECONDS"] = "120"
    from worker import main as wm
    from worker.config import reset_settings_cache
    reset_settings_cache()
    wm.seed(wm.client())
    h.sql("update public.phases set starts_at = now() - interval '1 hour', ends_at = now() + interval '1 day' where slug = 'online'")
    h.sql("update public.site_settings set value = '[\"admin@e2e.org\"]'::jsonb where key = 'admin_emails'")
    yield h
    h.stop()


@pytest.fixture(scope="session")
def site(hs):
    port = _free_port()
    env = {**os.environ, "PATH": f"{NODE_BIN}:{os.environ['PATH']}", "VITE_SUPABASE_URL": hs.url, "VITE_SUPABASE_ANON_KEY": anon_key(),
           "VITE_SITE_URL": f"http://127.0.0.1:{port}", "VITE_BASE_PATH": "/"}
    build = subprocess.run(["npm", "run", "build"], cwd=str(WEB), env=env, capture_output=True, text=True, timeout=600)
    assert build.returncode == 0, build.stdout[-3000:] + build.stderr[-3000:]
    proc = subprocess.Popen(["npx", "vite", "preview", "--host", "127.0.0.1", "--port", str(port), "--strictPort"], cwd=str(WEB), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("vite preview did not start")
    SHOTS.mkdir(parents=True, exist_ok=True)
    yield {"base": base, "hs": hs}
    proc.terminate()


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "viewport": {"width": 1440, "height": 900}, "locale": "en-US"}


def run_worker_once() -> int:
    from worker import main as wm
    return wm.run_loop(once=True)
