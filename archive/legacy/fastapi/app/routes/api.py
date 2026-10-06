"""JSON API. Public: leaderboard, phases, announcements. Token-authenticated: submissions."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import Ctx, api_user, get_ctx
from ..models import Announcement, Evaluation, Phase, Scenario, Submission, User, utcnow
from ..services.leaderboard import leaderboard_payload
from .dashboard import SubmitError, create_submission

router = APIRouter(prefix="/api")


def _phase(db: Session, slug: str | None) -> Phase:
    q = db.query(Phase).filter(Phase.is_active.is_(True)).order_by(Phase.order)
    if slug:
        p = q.filter(Phase.slug == slug).first()
        if not p:
            raise HTTPException(404, "phase not found")
        return p
    phases = q.all()
    if not phases:
        raise HTTPException(404, "no phases")
    return next((p for p in phases if p.counts_for_final and p.status in ("open", "closed")), None) or next((p for p in phases if p.status == "open"), None) or phases[0]


@router.get("/health")
def health():
    return {"status": "ok", "time": utcnow().isoformat() + "Z"}


@router.get("/phases")
def phases(db: Session = Depends(get_db)):
    rows = db.query(Phase).filter(Phase.is_active.is_(True)).order_by(Phase.order).all()
    return {"phases": [{
        "slug": p.slug, "name_en": p.name_en, "name_zh": p.name_zh, "status": p.status, "starts_at": p.starts_at.isoformat() + "Z" if p.starts_at else None,
        "ends_at": p.ends_at.isoformat() + "Z" if p.ends_at else None, "allow_results": p.allow_results, "allow_agents": p.allow_agents,
        "daily_limit": p.daily_limit, "leaderboard_mode": p.leaderboard_mode, "counts_for_final": p.counts_for_final,
        "scenarios": [{"slug": s.slug, "name": s.name, "weather_public": s.weather_public, "tiles_public": s.tiles_public, "n_slots": s.n_slots, "n_nights": s.n_nights, "n_tiles": s.n_tiles}
                       for s in (db.get(Scenario, sid) for sid in (p.scenario_ids or [])) if s and s.is_active],
    } for p in rows]}


@router.get("/leaderboard")
def leaderboard(phase: str | None = None, limit: int = 20, ctx: Ctx = Depends(get_ctx)):
    p = _phase(ctx.db, phase)
    limit = max(1, min(500, limit))
    payload = leaderboard_payload(ctx.db, p, limit=limit, is_admin=ctx.is_admin)
    # keep the shape the reference site expects: top-level `entries` + `data` alias
    payload["data"] = payload["entries"]
    return payload


@router.get("/announcements")
def announcements(db: Session = Depends(get_db)):
    rows = db.query(Announcement).filter(Announcement.is_published.is_(True)).order_by(Announcement.is_pinned.desc(), Announcement.created_at.desc()).limit(50).all()
    return {"announcements": [{"id": a.id, "title_en": a.title_en, "title_zh": a.title_zh, "body_en": a.body_en, "body_zh": a.body_zh, "level": a.level, "pinned": a.is_pinned, "created_at": a.created_at.isoformat() + "Z"} for a in rows]}


def _sub_json(s: Submission, evals: list[Evaluation] | None = None) -> dict:
    d = {
        "id": s.id, "kind": s.kind, "phase": s.phase.slug, "scenario": s.scenario.slug if s.scenario else None, "status": s.status,
        "title": s.title, "score": s.score, "science_score": s.science_score, "completion": s.completion, "uniformity": s.uniformity,
        "error": s.error or None, "created_at": s.created_at.isoformat() + "Z", "finished_at": s.finished_at.isoformat() + "Z" if s.finished_at else None,
        "team": s.team.name, "sha256": s.file_sha256, "url": f"/submissions/{s.id}",
    }
    if evals is not None:
        d["evaluations"] = [{
            "id": e.id, "scenario": e.scenario.slug, "status": e.status, "score": e.score, "science_score": e.science_score, "completion": e.completion,
            "uniformity": e.uniformity, "error": e.error or None, "runtime_seconds": e.runtime_seconds, "summary": e.summary,
        } for e in evals]
    return d


@router.get("/me")
def me(user: User = Depends(api_user)):
    return {"id": user.id, "email": user.email, "name": user.name, "team": {"id": user.team.id, "name": user.team.name} if user.team else None, "is_admin": user.is_admin}


@router.get("/submissions")
def list_submissions(user: User = Depends(api_user), db: Session = Depends(get_db)):
    if not user.team_id:
        return {"submissions": []}
    rows = db.query(Submission).filter(Submission.team_id == user.team_id).order_by(Submission.created_at.desc()).limit(200).all()
    return {"submissions": [_sub_json(s) for s in rows]}


@router.post("/submissions")
async def create(request: Request, user: User = Depends(api_user), ctx: Ctx = Depends(get_ctx)):
    ctx.user = user  # token auth
    if not user.team_id:
        raise HTTPException(400, "join or create a team first")
    form = await request.form()
    try:
        sub = create_submission(ctx, user.team, form, via_api=True)
    except SubmitError as exc:
        raise HTTPException(400, str(exc))
    return _sub_json(sub)


@router.get("/submissions/{sid}")
def get_submission(sid: int, user: User = Depends(api_user), db: Session = Depends(get_db)):
    s = db.get(Submission, sid)
    if not s or (not user.is_admin and s.team_id != user.team_id):
        raise HTTPException(404, "submission not found")
    evals = db.query(Evaluation).filter(Evaluation.submission_id == s.id).order_by(Evaluation.id).all()
    return _sub_json(s, evals)


@router.get("/submissions/{sid}/evaluations/{eid}/{what}")
def get_artifact(sid: int, eid: int, what: str, user: User = Depends(api_user), db: Session = Depends(get_db)):
    s = db.get(Submission, sid)
    if not s or (not user.is_admin and s.team_id != user.team_id):
        raise HTTPException(404, "submission not found")
    e = db.get(Evaluation, eid)
    if not e or e.submission_id != s.id:
        raise HTTPException(404)
    mapping = {"report": (e.report_path, "application/json"), "decisions": (e.decisions_path, "text/csv"), "log": (e.log_path, "text/plain")}
    if what not in mapping or not mapping[what][0] or not Path(mapping[what][0]).exists():
        raise HTTPException(404)
    return FileResponse(mapping[what][0], media_type=mapping[what][1])
