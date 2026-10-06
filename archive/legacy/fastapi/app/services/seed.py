"""Bootstrap data: admin accounts, default phases and scenarios (idempotent)."""
from __future__ import annotations

import logging
import secrets
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ..config import ROOT, get_settings
from ..models import Phase, Scenario, SiteSetting, User
from ..security import hash_password
from .scenarios import generate_and_register, register_scenario

log = logging.getLogger("sac.seed")
EXAMPLE_DIR = ROOT / "starter_kit" / "example"


def ensure_admins(db: Session) -> None:
    s = get_settings()
    for email in s.admin_email_set:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            pw = secrets.token_urlsafe(12)
            user = User(email=email, name="Organizer", password_hash=hash_password(pw), is_admin=True, is_verified=True)
            db.add(user)
            log.warning("created admin %s with temporary password: %s", email, pw)
            print(f"[seed] created admin {email} with temporary password: {pw}")
        elif not user.is_admin:
            user.is_admin = True
    db.flush()


def ensure_default_scenarios(db: Session) -> dict[str, Scenario]:
    out: dict[str, Scenario] = {}
    if db.query(Scenario).count() == 0:
        out["dev-example"] = register_scenario(
            db, slug="dev-example", name="Development example (seed 11)",
            description="The published example scenario: 2 nights x 12 slots, 72 tiles. Identical to the starter kit.",
            weather_src=EXAMPLE_DIR / "weather.csv", tiles_src=EXAMPLE_DIR / "tiles.csv", config_src=EXAMPLE_DIR / "score_config.json",
            weather_public=True, tiles_public=True, seed=11,
        )
        out["dev-week"] = generate_and_register(
            db, slug="dev-week", name="Development week (seed 2026)",
            description="A longer public scenario: 7 nights x 24 slots, 240 tiles.", seed=2026, n_nights=7, slots_per_night=24,
            n_tiles=240, weather_public=True, tiles_public=True, first_night=datetime(2026, 10, 5, 2, 0),
        )
        out["eval-a"] = generate_and_register(
            db, slug="eval-a", name="Competition scenario A",
            description="Hidden weather replay for the online competition. Tile catalogue is public.", seed=90210,
            n_nights=7, slots_per_night=24, n_tiles=240, weather_public=False, tiles_public=True, first_night=datetime(2026, 10, 5, 2, 0),
        )
        out["eval-b"] = generate_and_register(
            db, slug="eval-b", name="Competition scenario B",
            description="Second hidden weather replay for the online competition.", seed=41207,
            n_nights=7, slots_per_night=24, n_tiles=240, weather_public=False, tiles_public=True, first_night=datetime(2026, 10, 12, 2, 0),
        )
        db.flush()
    return out


def ensure_default_phases(db: Session) -> None:
    if db.query(Phase).count():
        return
    by_slug = {s.slug: s for s in db.query(Scenario).all()}
    ids = lambda *slugs: [by_slug[s].id for s in slugs if s in by_slug]  # noqa: E731
    db.add_all([
        Phase(slug="practice", name_en="Practice", name_zh="练习赛", order=1,
              description_en="Open now. Practice with public development scenarios; the practice board is informational.",
              description_zh="现已开放。可使用公开开发场景练习；练习榜仅供参考。",
              allow_results=True, allow_agents=True, daily_limit=50, leaderboard_mode="live", scenario_ids=ids("dev-example", "dev-week"),
              starts_at=None, ends_at=None),
        Phase(slug="online", name_en="Online Competition", name_zh="线上比赛", order=2,
              description_en="October 5–7. Agents run on the platform against hidden weather replays A and B; the score is the mean over both scenarios.",
              description_zh="10 月 5–7 日。智能体在平台上对隐藏天气回放 A、B 运行，得分为两个场景的平均值。",
              allow_results=False, allow_agents=True, daily_limit=10, leaderboard_mode="live", counts_for_final=True,
              scenario_ids=ids("eval-a", "eval-b"), starts_at=datetime(2026, 10, 4, 16, 0), ends_at=datetime(2026, 10, 7, 15, 59, 59)),
    ])
    db.flush()


def ensure_settings(db: Session) -> None:
    if db.get(SiteSetting, "registration_open") is None:
        db.add(SiteSetting(key="registration_open", value={"v": get_settings().registration_open}))
    db.flush()


def ensure_bootstrap(db: Session) -> None:
    ensure_admins(db)
    ensure_default_scenarios(db)
    ensure_default_phases(db)
    ensure_settings(db)
