from __future__ import annotations

import csv
import io
import secrets
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func

from ..deps import Ctx, check_csrf, require_admin
from ..models import Announcement, AuditLog, Evaluation, Job, Phase, Scenario, SiteSetting, Submission, Team, User, utcnow
from ..security import hash_password
from ..services import jobs
from ..services.leaderboard import compute_leaderboard
from ..services.scenarios import ScenarioError, generate_and_register, register_scenario

router = APIRouter(prefix="/admin")


def _admin_post(ctx: Ctx = Depends(check_csrf)) -> Ctx:
    if not ctx.user or not ctx.user.is_admin:
        raise HTTPException(403, "admin only")
    return ctx


def _dt(value: str | None) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        return None


@router.get("")
def index(ctx: Ctx = Depends(require_admin)):
    db = ctx.db
    stats = {
        "users": db.query(User).count(), "teams": db.query(Team).count(),
        "submissions": db.query(Submission).count(),
        "queued": db.query(Submission).filter(Submission.status.in_(["queued", "running"])).count(),
        "scored": db.query(Submission).filter(Submission.status == "scored").count(),
        "failed": db.query(Submission).filter(Submission.status.in_(["failed", "invalid"])).count(),
        "jobs_failed": db.query(Job).filter(Job.status == "failed").count(),
    }
    recent = db.query(Submission).order_by(Submission.created_at.desc()).limit(15).all()
    audit = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(15).all()
    return ctx.render("admin/index.html", stats=stats, recent=recent, audit=audit)


# ------------------------------------------------------------------ phases

@router.get("/phases")
def phases(ctx: Ctx = Depends(require_admin)):
    rows = ctx.db.query(Phase).order_by(Phase.order, Phase.id).all()
    scenarios = ctx.db.query(Scenario).order_by(Scenario.id).all()
    return ctx.render("admin/phases.html", rows=rows, scenarios=scenarios)


@router.post("/phases/save")
async def phase_save(request: Request, ctx: Ctx = Depends(_admin_post)):
    form = await request.form()
    pid = form.get("id")
    phase = ctx.db.get(Phase, int(pid)) if pid else Phase(slug="")
    slug = (form.get("slug") or "").strip().lower()
    if not slug:
        return ctx.redirect("/admin/phases", flash=("error", "slug required"))
    other = ctx.db.query(Phase).filter(Phase.slug == slug).first()
    if other and other is not phase:
        return ctx.redirect("/admin/phases", flash=("error", "slug already used"))
    phase.slug = slug
    phase.name_en = (form.get("name_en") or slug).strip()
    phase.name_zh = (form.get("name_zh") or phase.name_en).strip()
    phase.description_en = (form.get("description_en") or "").strip()
    phase.description_zh = (form.get("description_zh") or "").strip()
    phase.order = int(form.get("order") or 0)
    phase.starts_at = _dt(form.get("starts_at"))
    phase.ends_at = _dt(form.get("ends_at"))
    phase.allow_results = form.get("allow_results") == "on"
    phase.allow_agents = form.get("allow_agents") == "on"
    phase.daily_limit = max(0, int(form.get("daily_limit") or 10))
    phase.leaderboard_mode = form.get("leaderboard_mode") if form.get("leaderboard_mode") in ("live", "frozen", "hidden", "published") else "live"
    phase.counts_for_final = form.get("counts_for_final") == "on"
    phase.is_active = form.get("is_active") == "on"
    phase.scenario_ids = [int(x) for x in form.getlist("scenario_ids")] if hasattr(form, "getlist") else []
    if not pid:
        ctx.db.add(phase)
    ctx.db.add(AuditLog(user_id=ctx.user.id, action="admin.phase.save", detail={"slug": slug}))
    ctx.db.commit()
    return ctx.redirect("/admin/phases", flash=("success", "phase saved"))


# ------------------------------------------------------------------ scenarios

@router.get("/scenarios")
def scenarios(ctx: Ctx = Depends(require_admin)):
    rows = ctx.db.query(Scenario).order_by(Scenario.id).all()
    return ctx.render("admin/scenarios.html", rows=rows)


@router.post("/scenarios/upload")
async def scenario_upload(request: Request, ctx: Ctx = Depends(_admin_post)):
    form = await request.form()
    tmp = ctx.settings.scenarios_dir / "_upload" / secrets.token_hex(4)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        paths = {}
        for key in ("weather", "tiles", "config"):
            f = form.get(key)
            if f is None or not getattr(f, "filename", ""):
                if key == "config":
                    continue
                return ctx.redirect("/admin/scenarios", flash=("error", f"{key} file required"))
            p = tmp / f"{key}{Path(f.filename).suffix}"
            p.write_bytes(f.file.read())
            paths[key] = p
        scn = register_scenario(ctx.db, slug=form.get("slug") or "", name=form.get("name") or form.get("slug") or "", description=form.get("description") or "",
                                weather_src=paths["weather"], tiles_src=paths["tiles"], config_src=paths.get("config"),
                                weather_public=form.get("weather_public") == "on", tiles_public=form.get("tiles_public") == "on")
        ctx.db.add(AuditLog(user_id=ctx.user.id, action="admin.scenario.upload", detail={"slug": scn.slug}))
        ctx.db.commit()
    except ScenarioError as exc:
        return ctx.redirect("/admin/scenarios", flash=("error", f"invalid scenario: {exc}"))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return ctx.redirect("/admin/scenarios", flash=("success", "scenario registered"))


@router.post("/scenarios/generate")
async def scenario_generate(request: Request, ctx: Ctx = Depends(_admin_post)):
    form = await request.form()
    try:
        scn = generate_and_register(
            ctx.db, slug=form.get("slug") or "", name=form.get("name") or form.get("slug") or "", description=form.get("description") or "",
            seed=int(form.get("seed") or 1), n_nights=max(1, int(form.get("n_nights") or 2)), slots_per_night=max(1, int(form.get("slots_per_night") or 12)),
            n_tiles=max(1, int(form.get("n_tiles") or 72)), weather_public=form.get("weather_public") == "on", tiles_public=form.get("tiles_public") == "on",
            first_night=_dt(form.get("first_night")),
        )
        ctx.db.add(AuditLog(user_id=ctx.user.id, action="admin.scenario.generate", detail={"slug": scn.slug, "seed": scn.seed}))
        ctx.db.commit()
    except (ScenarioError, ValueError) as exc:
        return ctx.redirect("/admin/scenarios", flash=("error", f"could not generate: {exc}"))
    return ctx.redirect("/admin/scenarios", flash=("success", f"scenario {scn.slug} generated"))


@router.post("/scenarios/{sid}/update")
async def scenario_update(sid: int, request: Request, ctx: Ctx = Depends(_admin_post)):
    form = await request.form()
    scn = ctx.db.get(Scenario, sid)
    if not scn:
        raise HTTPException(404)
    scn.name = (form.get("name") or scn.name).strip()
    scn.description = (form.get("description") or "").strip()
    scn.weather_public = form.get("weather_public") == "on"
    scn.tiles_public = form.get("tiles_public") == "on"
    scn.is_active = form.get("is_active") == "on"
    ctx.db.commit()
    return ctx.redirect("/admin/scenarios", flash=("success", "scenario updated"))


# ------------------------------------------------------------------ submissions

@router.get("/submissions")
def submissions(ctx: Ctx = Depends(require_admin), status: str | None = None, phase: str | None = None, team: str | None = None):
    q = ctx.db.query(Submission)
    if status:
        q = q.filter(Submission.status == status)
    if phase:
        q = q.join(Phase).filter(Phase.slug == phase)
    if team:
        q = q.join(Team).filter(Team.name.ilike(f"%{team}%"))
    rows = q.order_by(Submission.created_at.desc()).limit(300).all()
    phases_all = ctx.db.query(Phase).order_by(Phase.order).all()
    return ctx.render("admin/submissions.html", rows=rows, phases_all=phases_all, f_status=status or "", f_phase=phase or "", f_team=team or "")


@router.post("/submissions/{sid}/action")
async def submission_action(sid: int, request: Request, ctx: Ctx = Depends(_admin_post)):
    form = await request.form()
    sub = ctx.db.get(Submission, sid)
    if not sub:
        raise HTTPException(404)
    action = form.get("action")
    if action == "rescore":
        sub.status = "queued"
        sub.error = ""
        sub.score = None
        jobs.enqueue(ctx.db, "evaluate_submission", {"submission_id": sub.id})
    elif action == "exclude":
        sub.is_excluded = not sub.is_excluded
    elif action == "cancel":
        if sub.status in ("queued", "running"):
            sub.status = "cancelled"
    ctx.db.add(AuditLog(user_id=ctx.user.id, action=f"admin.submission.{action}", detail={"submission_id": sub.id}))
    ctx.db.commit()
    return ctx.redirect(request.headers.get("referer") or "/admin/submissions", flash=("success", f"{action} done"))


@router.post("/rescore-phase/{pid}")
async def rescore_phase(pid: int, ctx: Ctx = Depends(_admin_post)):
    subs = ctx.db.query(Submission).filter(Submission.phase_id == pid, Submission.status.in_(["scored", "failed", "invalid"])).all()
    for sub in subs:
        sub.status = "queued"
        jobs.enqueue(ctx.db, "evaluate_submission", {"submission_id": sub.id})
    ctx.db.add(AuditLog(user_id=ctx.user.id, action="admin.phase.rescore", detail={"phase_id": pid, "n": len(subs)}))
    ctx.db.commit()
    return ctx.redirect("/admin/phases", flash=("success", f"{len(subs)} submissions re-queued"))


# ------------------------------------------------------------------ users / teams

@router.get("/users")
def users(ctx: Ctx = Depends(require_admin), q: str | None = None):
    query = ctx.db.query(User)
    if q:
        query = query.filter((User.email.ilike(f"%{q}%")) | (User.name.ilike(f"%{q}%")))
    rows = query.order_by(User.created_at.desc()).limit(500).all()
    return ctx.render("admin/users.html", rows=rows, q=q or "")


@router.post("/users/{uid}/action")
async def user_action(uid: int, request: Request, ctx: Ctx = Depends(_admin_post)):
    form = await request.form()
    u = ctx.db.get(User, uid)
    if not u:
        raise HTTPException(404)
    action = form.get("action")
    msg = f"{action} done"
    if action == "toggle_admin" and u.id != ctx.user.id:
        u.is_admin = not u.is_admin
    elif action == "toggle_ban" and u.id != ctx.user.id:
        u.is_banned = not u.is_banned
    elif action == "verify":
        u.is_verified = True
    elif action == "reset_password":
        pw = secrets.token_urlsafe(10)
        u.password_hash = hash_password(pw)
        msg = f"temporary password for {u.email}: {pw}"
    elif action == "remove_from_team":
        u.team_id = None
    ctx.db.add(AuditLog(user_id=ctx.user.id, action=f"admin.user.{action}", detail={"user_id": u.id}))
    ctx.db.commit()
    return ctx.redirect("/admin/users", flash=("success", msg))


@router.get("/teams")
def teams(ctx: Ctx = Depends(require_admin)):
    rows = ctx.db.query(Team).order_by(Team.created_at.desc()).all()
    counts = dict(ctx.db.query(Submission.team_id, func.count(Submission.id)).group_by(Submission.team_id).all())
    return ctx.render("admin/teams.html", rows=rows, counts=counts)


@router.post("/teams/{tid}/action")
async def team_action(tid: int, request: Request, ctx: Ctx = Depends(_admin_post)):
    form = await request.form()
    team = ctx.db.get(Team, tid)
    if not team:
        raise HTTPException(404)
    action = form.get("action")
    if action == "toggle_hidden":
        team.is_hidden = not team.is_hidden
    elif action == "toggle_locked":
        team.is_locked = not team.is_locked
    elif action == "rename":
        name = (form.get("name") or "").strip()
        if name:
            team.name = name[:60]
    ctx.db.add(AuditLog(user_id=ctx.user.id, action=f"admin.team.{action}", detail={"team_id": team.id}))
    ctx.db.commit()
    return ctx.redirect("/admin/teams", flash=("success", f"{action} done"))


# ------------------------------------------------------------------ announcements / settings

@router.get("/announcements")
def announcements(ctx: Ctx = Depends(require_admin)):
    rows = ctx.db.query(Announcement).order_by(Announcement.created_at.desc()).all()
    return ctx.render("admin/announcements.html", rows=rows)


@router.post("/announcements/save")
async def announcement_save(request: Request, ctx: Ctx = Depends(_admin_post)):
    form = await request.form()
    aid = form.get("id")
    a = ctx.db.get(Announcement, int(aid)) if aid else Announcement(title_en="", title_zh="")
    if form.get("action") == "delete" and aid:
        ctx.db.delete(a)
        ctx.db.commit()
        return ctx.redirect("/admin/announcements", flash=("success", "deleted"))
    a.title_en = (form.get("title_en") or "").strip()[:200]
    a.title_zh = (form.get("title_zh") or a.title_en).strip()[:200]
    a.body_en = (form.get("body_en") or "").strip()
    a.body_zh = (form.get("body_zh") or a.body_en).strip()
    a.level = form.get("level") if form.get("level") in ("info", "warning", "success") else "info"
    a.is_pinned = form.get("is_pinned") == "on"
    a.is_published = form.get("is_published") == "on"
    if not aid:
        ctx.db.add(a)
    ctx.db.commit()
    return ctx.redirect("/admin/announcements", flash=("success", "saved"))


@router.get("/settings")
def settings_page(ctx: Ctx = Depends(require_admin)):
    return ctx.render("admin/settings.html", registration_open=ctx.site_setting("registration_open", ctx.settings.registration_open))


@router.post("/settings")
async def settings_save(request: Request, ctx: Ctx = Depends(_admin_post)):
    form = await request.form()
    row = ctx.db.get(SiteSetting, "registration_open") or SiteSetting(key="registration_open", value={})
    row.value = {"v": form.get("registration_open") == "on"}
    ctx.db.merge(row)
    ctx.db.commit()
    return ctx.redirect("/admin/settings", flash=("success", "settings saved"))


@router.get("/jobs")
def jobs_page(ctx: Ctx = Depends(require_admin)):
    rows = ctx.db.query(Job).order_by(Job.created_at.desc()).limit(200).all()
    return ctx.render("admin/jobs.html", rows=rows)


# ------------------------------------------------------------------ exports

def _csv_response(name: str, header: list[str], rows: list[list]) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/export/{what}.csv")
def export(what: str, ctx: Ctx = Depends(require_admin), phase: str | None = None):
    db = ctx.db
    if what == "users":
        rows = [[u.id, u.email, u.name, u.github, u.affiliation, u.team.name if u.team else "", u.is_admin, u.is_verified, u.created_at.isoformat()] for u in db.query(User).all()]
        return _csv_response("users.csv", ["id", "email", "name", "github", "affiliation", "team", "is_admin", "is_verified", "created_at"], rows)
    if what == "teams":
        rows = [[t.id, t.name, t.slug, db.get(User, t.leader_id).email if db.get(User, t.leader_id) else "", len(t.members), t.max_size, t.github_repo, t.created_at.isoformat()] for t in db.query(Team).all()]
        return _csv_response("teams.csv", ["id", "name", "slug", "leader_email", "members", "max_size", "github_repo", "created_at"], rows)
    if what == "submissions":
        rows = [[s.id, s.team.name, s.user.email, s.phase.slug, s.kind, s.scenario.slug if s.scenario else "", s.status, s.score, s.science_score, s.completion, s.uniformity, s.file_sha256, s.created_at.isoformat()] for s in db.query(Submission).all()]
        return _csv_response("submissions.csv", ["id", "team", "user", "phase", "kind", "scenario", "status", "score", "science", "completion", "uniformity", "sha256", "created_at"], rows)
    if what == "leaderboard":
        ph = db.query(Phase).filter(Phase.slug == (phase or "")).first()
        if not ph:
            raise HTTPException(404, "phase required")
        rows = [[e["rank"], e["team_name"], e["total_score"], e["science_score"], e["completion_rate"], e["uniformity_score"], e["submission_count"], e["best_submission_id"]] for e in compute_leaderboard(db, ph, include_hidden=True)]
        return _csv_response(f"leaderboard-{ph.slug}.csv", ["rank", "team", "score", "science", "completion", "uniformity", "submissions", "best_submission_id"], rows)
    raise HTTPException(404)
