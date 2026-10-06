from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Phase, Submission, Team, utcnow


def phase_visible_to(phase: Phase, is_admin: bool) -> bool:
    if is_admin:
        return True
    return phase.leaderboard_mode in ("live", "frozen", "published")


def compute_leaderboard(db: Session, phase: Phase, *, limit: Optional[int] = None, include_hidden: bool = False) -> list[dict]:
    q = (
        db.query(Submission)
        .join(Team, Team.id == Submission.team_id)
        .filter(Submission.phase_id == phase.id, Submission.status == "scored", Submission.is_excluded.is_(False), Submission.score.isnot(None))
    )
    if not include_hidden:
        q = q.filter(Team.is_hidden.is_(False))
    best: dict[int, Submission] = {}
    counts: dict[int, int] = {}
    for sub in q.order_by(Submission.created_at.asc()).all():
        counts[sub.team_id] = counts.get(sub.team_id, 0) + 1
        cur = best.get(sub.team_id)
        if cur is None or sub.score > cur.score:
            best[sub.team_id] = sub
    rows = sorted(best.values(), key=lambda s: (-s.score, s.created_at))
    out = []
    rank = 0
    prev_score = None
    for i, sub in enumerate(rows, start=1):
        if prev_score is None or sub.score < prev_score:
            rank = i
            prev_score = sub.score
        out.append({
            "rank": rank, "team_id": sub.team_id, "team_name": sub.team.name, "team_slug": sub.team.slug,
            "total_score": round(sub.score, 3), "science_score": round(sub.science_score or 0.0, 3),
            "completion_rate": round(sub.completion or 0.0, 4), "uniformity_score": round(sub.uniformity or 0.0, 4),
            "submission_count": counts.get(sub.team_id, 0), "best_submission_id": sub.id,
            "kind": sub.kind, "scored_at": (sub.finished_at or sub.created_at).isoformat() + "Z",
        })
        if limit and len(out) >= limit:
            break
    return out


def leaderboard_payload(db: Session, phase: Phase, *, limit: Optional[int] = None, is_admin: bool = False) -> dict:
    visible = phase_visible_to(phase, is_admin)
    entries = compute_leaderboard(db, phase, limit=limit, include_hidden=is_admin) if visible else []
    last = db.query(func.max(Submission.finished_at)).filter(Submission.phase_id == phase.id, Submission.status == "scored").scalar()
    return {
        "phase": {"slug": phase.slug, "name_en": phase.name_en, "name_zh": phase.name_zh, "leaderboard_mode": phase.leaderboard_mode, "status": phase.status},
        "visible": visible, "updated_at": (last.isoformat() + "Z") if isinstance(last, datetime) else None,
        "generated_at": utcnow().isoformat() + "Z", "entries": entries, "count": len(entries),
    }


def team_daily_submission_count(db: Session, team_id: int, phase_id: int) -> int:
    now = utcnow()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(func.count(Submission.id))
        .filter(Submission.team_id == team_id, Submission.phase_id == phase_id, Submission.created_at >= start, Submission.status != "cancelled")
        .scalar()
    ) or 0
