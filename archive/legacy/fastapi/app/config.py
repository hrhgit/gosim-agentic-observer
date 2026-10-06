"""Application settings (env-driven, prefix SAC_)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SAC_", env_file=ROOT / ".env", extra="ignore")

    secret_key: str = "dev-secret-change-me"
    database_url: str = f"sqlite:///{ROOT / 'data' / 'app.db'}"
    data_dir: Path = ROOT / "data"
    base_url: str = "http://localhost:8000"
    site_name: str = "Agent Observer"
    admin_emails: str = ""
    registration_open: bool = True
    daily_submission_limit: int = 10
    max_upload_mb: int = 20
    default_locale: str = "zh"

    # Background worker
    inline_worker: bool = True
    worker_concurrency: int = 1

    # Agent sandbox
    sandbox_mode: str = "subprocess"  # subprocess | docker
    agent_python: str = "python3"
    agent_timeout_seconds: int = 600
    agent_step_timeout_seconds: int = 20
    agent_memory_mb: int = 1024
    agent_max_steps: int = 50000
    docker_image: str = "python:3.12-slim"

    # Email (optional)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@example.org"
    smtp_tls: bool = True

    # Cookies
    session_cookie: str = "sac_session"
    session_days: int = 14
    secure_cookies: bool = False

    @property
    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    @property
    def scenarios_dir(self) -> Path:
        return self.data_dir / "scenarios"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.data_dir, s.scenarios_dir, s.uploads_dir, s.runs_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s


def reset_settings_cache() -> None:
    get_settings.cache_clear()
