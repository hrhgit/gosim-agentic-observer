"""Real-browser end-to-end tests: boot uvicorn on a free port with a scratch data dir, drive it with Playwright."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "artifacts" / "screenshots"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server():
    data = Path(tempfile.mkdtemp(prefix="sac-e2e-"))
    port = _free_port()
    env = {**os.environ, "SAC_DATA_DIR": str(data), "SAC_DATABASE_URL": f"sqlite:///{data}/e2e.db", "SAC_SECRET_KEY": "e2e-secret",
           "SAC_ADMIN_EMAILS": "admin@e2e.org", "SAC_INLINE_WORKER": "true", "SAC_BASE_URL": f"http://127.0.0.1:{port}",
           "SAC_AGENT_TIMEOUT_SECONDS": "120", "SAC_DEFAULT_LOCALE": "en"}
    log = open(data / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)], cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("server did not start: " + (data / "server.log").read_text()[-2000:])
    SHOTS.mkdir(parents=True, exist_ok=True)
    yield {"base": base, "data": data, "proc": proc}
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()
    shutil.rmtree(data, ignore_errors=True)


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    return {**browser_context_args, "viewport": {"width": 1440, "height": 900}, "locale": "en-US"}
