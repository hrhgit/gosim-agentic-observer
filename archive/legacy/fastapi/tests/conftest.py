from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def data_dir():
    d = Path(tempfile.mkdtemp(prefix="sac-test-"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="session", autouse=True)
def _settings(data_dir):
    os.environ.update({
        "SAC_DATA_DIR": str(data_dir), "SAC_DATABASE_URL": f"sqlite:///{data_dir}/test.db", "SAC_SECRET_KEY": "test-secret",
        "SAC_ADMIN_EMAILS": "admin@test.org", "SAC_INLINE_WORKER": "false", "SAC_AGENT_TIMEOUT_SECONDS": "60",
        "SAC_AGENT_STEP_TIMEOUT_SECONDS": "5", "SAC_BASE_URL": "http://testserver", "SAC_DEFAULT_LOCALE": "en",
    })
    from app.config import reset_settings_cache
    from app.db import reset_engine
    reset_settings_cache()
    reset_engine()
    yield


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin_client():
    """Separate client with its own cookie jar, logged in as the seeded admin."""
    from fastapi.testclient import TestClient
    from app.db import session_scope
    from app.main import app
    from app.models import User
    from app.security import hash_password
    with session_scope() as db:
        u = db.query(User).filter_by(email="admin@test.org").first()
        u.password_hash = hash_password("adminpass123")
    c = TestClient(app)
    csrf = get_csrf(c, "/login")
    r = c.post("/login", data={"csrf_token": csrf, "email": "admin@test.org", "password": "adminpass123"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c


def get_csrf(client, path="/login") -> str:
    import re
    html = client.get(path).text
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "csrf token not found"
    return m.group(1)


@pytest.fixture
def csrf():
    return get_csrf
