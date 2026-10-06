import os
import pytest


@pytest.fixture(scope="session", autouse=True)
def _worker_env(tmp_path_factory):
    os.environ.setdefault("SUPABASE_URL", "http://127.0.0.1:1")
    os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")
    os.environ["SAC_DATA_DIR"] = str(tmp_path_factory.mktemp("worker"))
    os.environ["SAC_AGENT_TIMEOUT_SECONDS"] = "60"
    os.environ["SAC_AGENT_STEP_TIMEOUT_SECONDS"] = "5"
    from worker.config import reset_settings_cache
    reset_settings_cache()
