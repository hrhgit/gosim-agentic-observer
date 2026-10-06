from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_code(n: int = 8) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    github: Mapped[str] = mapped_column(String(120), default="")
    affiliation: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(120), default="")
    looking_for_team: Mapped[bool] = mapped_column(Boolean, default=False)
    locale: Mapped[str] = mapped_column(String(8), default="zh")
    api_token: Mapped[str] = mapped_column(String(64), default=lambda: secrets.token_hex(24), unique=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    team: Mapped[Optional["Team"]] = relationship("Team", back_populates="members", foreign_keys=[team_id])


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    slug: Mapped[str] = mapped_column(String(140), unique=True)
    leader_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    invite_code: Mapped[str] = mapped_column(String(16), unique=True, default=new_code)
    project_idea: Mapped[str] = mapped_column(Text, default="")
    github_repo: Mapped[str] = mapped_column(String(255), default="")
    max_size: Mapped[int] = mapped_column(Integer, default=4)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)  # hidden from leaderboard (e.g. organizers)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    members: Mapped[list["User"]] = relationship("User", back_populates="team", foreign_keys=[User.team_id])
    submissions: Mapped[list["Submission"]] = relationship("Submission", back_populates="team")


class Scenario(Base):
    """A frozen evaluation scenario: weather + tiles + score config."""

    __tablename__ = "scenarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    dir_name: Mapped[str] = mapped_column(String(120))  # under data/scenarios
    weather_public: Mapped[bool] = mapped_column(Boolean, default=True)
    tiles_public: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    n_slots: Mapped[int] = mapped_column(Integer, default=0)
    n_nights: Mapped[int] = mapped_column(Integer, default=0)
    n_tiles: Mapped[int] = mapped_column(Integer, default=0)
    seed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    checksum: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Phase(Base):
    __tablename__ = "phases"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    name_en: Mapped[str] = mapped_column(String(160))
    name_zh: Mapped[str] = mapped_column(String(160))
    description_en: Mapped[str] = mapped_column(Text, default="")
    description_zh: Mapped[str] = mapped_column(Text, default="")
    order: Mapped[int] = mapped_column(Integer, default=0)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    allow_results: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_agents: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_limit: Mapped[int] = mapped_column(Integer, default=10)
    # live: leaderboard visible & updating; frozen: visible but not updating (organizer snapshot);
    # hidden: scores hidden from participants; published: final results released
    leaderboard_mode: Mapped[str] = mapped_column(String(16), default="live")
    counts_for_final: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    scenario_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def is_open(self) -> bool:
        now = utcnow()
        if not self.is_active:
            return False
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True

    @property
    def status(self) -> str:
        now = utcnow()
        if not self.is_active:
            return "disabled"
        if self.starts_at and now < self.starts_at:
            return "upcoming"
        if self.ends_at and now > self.ends_at:
            return "closed"
        return "open"


class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    phase_id: Mapped[int] = mapped_column(ForeignKey("phases.id"), index=True)
    scenario_id: Mapped[Optional[int]] = mapped_column(ForeignKey("scenarios.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(16))  # results | agent
    title: Mapped[str] = mapped_column(String(160), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(String(400))
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    file_sha256: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|scored|invalid|failed|cancelled
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    science_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    completion: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    uniformity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    is_excluded: Mapped[bool] = mapped_column(Boolean, default=False)  # organizer exclusion from leaderboard
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    team: Mapped["Team"] = relationship("Team", back_populates="submissions")
    user: Mapped["User"] = relationship("User")
    phase: Mapped["Phase"] = relationship("Phase")
    scenario: Mapped[Optional["Scenario"]] = relationship("Scenario")
    evaluations: Mapped[list["Evaluation"]] = relationship("Evaluation", back_populates="submission", cascade="all, delete-orphan")


class Evaluation(Base):
    """One scenario run/score for a submission."""

    __tablename__ = "evaluations"
    __table_args__ = (UniqueConstraint("submission_id", "scenario_id", name="uq_eval_submission_scenario"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"), index=True)
    scenario_id: Mapped[int] = mapped_column(ForeignKey("scenarios.id"))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    science_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    completion: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    uniformity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    report_path: Mapped[str] = mapped_column(String(400), default="")
    decisions_path: Mapped[str] = mapped_column(String(400), default="")
    log_path: Mapped[str] = mapped_column(String(400), default="")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    runtime_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    submission: Mapped["Submission"] = relationship("Submission", back_populates="evaluations")
    scenario: Mapped["Scenario"] = relationship("Scenario")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # evaluate_submission
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    locked_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Announcement(Base):
    __tablename__ = "announcements"
    id: Mapped[int] = mapped_column(primary_key=True)
    title_en: Mapped[str] = mapped_column(String(200))
    title_zh: Mapped[str] = mapped_column(String(200))
    body_en: Mapped[str] = mapped_column(Text, default="")
    body_zh: Mapped[str] = mapped_column(Text, default="")
    level: Mapped[str] = mapped_column(String(16), default="info")  # info | warning | success
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Token(Base):
    """Email verification / password reset tokens."""

    __tablename__ = "tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(16))  # verify | reset
    token: Mapped[str] = mapped_column(String(64), unique=True, default=lambda: secrets.token_urlsafe(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SiteSetting(Base):
    __tablename__ = "site_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
