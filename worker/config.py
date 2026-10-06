"""Worker settings (env, prefix SAC_ for sandbox limits; SUPABASE_* for the backend)."""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Settings:
    def __init__(self) -> None:
        env = os.environ
        self.supabase_url = env.get("SUPABASE_URL", "").rstrip("/")
        self.service_key = env.get("SUPABASE_SERVICE_ROLE_KEY", "") or env.get("SUPABASE_SECRET_KEY", "")
        self.worker_id = env.get("SAC_WORKER_ID", f"worker-{os.uname().nodename}-{os.getpid()}")
        self.data_dir = Path(env.get("SAC_DATA_DIR", ROOT / "data" / "worker"))
        self.runs_dir = self.data_dir / "runs"
        self.kinds = [k.strip() for k in env.get("SAC_WORKER_KINDS", "agent,results").split(",") if k.strip()]
        self.poll_seconds = float(env.get("SAC_POLL_SECONDS", "3"))
        # idle backoff: doubles from poll_seconds up to this cap while the queue is empty, resets on a claim
        self.poll_backoff_max_seconds = float(env.get("SAC_POLL_BACKOFF_MAX_SECONDS", "30"))
        self.stale_minutes = int(env.get("SAC_STALE_MINUTES", "30"))
        # sandbox
        self.sandbox_mode = env.get("SAC_SANDBOX_MODE", "subprocess")
        # participants need Python 3.11+; default to the interpreter running the worker
        self.agent_python = env.get("SAC_AGENT_PYTHON") or sys.executable
        self.agent_timeout_seconds = int(env.get("SAC_AGENT_TIMEOUT_SECONDS", "600"))
        self.agent_step_timeout_seconds = int(env.get("SAC_AGENT_STEP_TIMEOUT_SECONDS", "20"))
        self.agent_memory_mb = int(env.get("SAC_AGENT_MEMORY_MB", "1024"))
        self.agent_max_steps = int(env.get("SAC_AGENT_MAX_STEPS", "50000"))
        self.docker_image = env.get("SAC_DOCKER_IMAGE", "ghcr.io/bh3gei/agent-observer-runtime:latest")
        # v3 challenge: one global wall clock per scenario (seconds); scenario rows may override
        self.default_wallclock_seconds = int(env.get("SAC_WALLCLOCK_SECONDS", "7200"))
        self.agent_init_timeout_seconds = float(env.get("SAC_AGENT_INIT_TIMEOUT_SECONDS", "30"))
        self.agent_cpu_seconds = int(env.get("SAC_AGENT_CPU_SECONDS", "7200"))
        self.install_timeout_seconds = int(env.get("SAC_INSTALL_TIMEOUT_SECONDS", "900"))
        self.agent_network = env.get("SAC_AGENT_NETWORK", "all")  # all | none (docker mode enforces)
        self.agent_proxy = env.get("SAC_AGENT_PROXY", "")  # optional egress proxy (allow-list) handed to the agent
        self.runs_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
