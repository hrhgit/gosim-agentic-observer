from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from ..deps import Ctx, check_csrf, require_user
from ..models import AuditLog, Evaluation, Phase, Scenario, Submission, Team, User, new_code, utcnow
from ..security import hash_password, password_problems, slugify, verify_password
from ..services import jobs
from ..services.leaderboard import team_daily_submission_count
from ..services.runner import AgentPackageError, prepare_agent_dir
from ..services.scoring import InvalidSubmission, validate_decisions_file

router = APIRouter()


def _team_of(ctx: Ctx) -> Team | None:
    return ctx.user.team if ctx.user and ctx.user.team_id else None


# ------------------------------------------------------------------ overview

@router.get("/dashboard")
def dashboard(ctx: Ctx = Depends(require_user)):
    team = _team_of(ctx)
    subs = []
    if team:
        subs = ctx.db.query(Submission).filter(Submission.team_id == team.id).order_by(Submission.created_at.desc()).limit(8).all()
    phases = ctx.db.query(Phase).filter(Phase.is_active.is_(True)).order_by(Phase.order).all()
    return ctx.render("dashboard/index.html", team=team, submissions=subs, phases_all=phases)


@router.get("/profile")
def profile(ctx: Ctx = Depends(require_user)):
    return ctx.render("dashboard/profile.html")


@router.post("/profile")
async def profile_update(request: Request, ctx: Ctx = Depends(check_csrf)):
    if not ctx.user:
        raise HTTPException(302, headers={"Location": "/login"})
    form = await request.form()
    action = form.get("action", "profile")
    u = ctx.user
    if action == "profile":
        u.name = (form.get("name") or u.name).strip()[:120] or u.name
        u.github = (form.get("github") or "").strip()[:120]
        u.affiliation = (form.get("affiliation") or "").strip()[:200]
        u.role = (form.get("role") or "").strip()[:120]
        u.looking_for_team = form.get("looking_for_team") == "on"
        ctx.db.commit()
        return ctx.redirect("/profile", flash=("success", ctx.t("flash.profile_saved")))
    if action == "password":
        if not verify_password(form.get("current") or "", u.password_hash):
            return ctx.redirect("/profile", flash=("error", ctx.t("auth.errors.bad_credentials")))
        pw, pw2 = form.get("password") or "", form.get("password2") or ""
        problem = password_problems(pw)
        if problem or pw != pw2:
            return ctx.redirect("/profile", flash=("error", ctx.t(f"auth.errors.{problem}") if problem else ctx.t("auth.errors.password_mismatch")))
        u.password_hash = hash_password(pw)
        ctx.db.commit()
        return ctx.redirect("/profile", flash=("success", ctx.t("flash.password_changed")))
    if action == "token":
        u.api_token = secrets.token_hex(24)
        ctx.db.commit()
        return ctx.redirect("/profile", flash=("success", ctx.t("flash.token_rotated")))
    raise HTTPException(400, "unknown action")


# ------------------------------------------------------------------ teams

@router.get("/team")
def team_page(ctx: Ctx = Depends(require_user)):
    team = _team_of(ctx)
    open_teams = []
    if not team:
        open_teams = [t for t in ctx.db.query(Team).filter(Team.is_locked.is_(False), Team.is_hidden.is_(False)).order_by(Team.created_at.desc()).limit(50).all() if len(t.members) < t.max_size]
    return ctx.render("dashboard/team.html", team=team, open_teams=open_teams)


@router.post("/team/create")
async def team_create(request: Request, ctx: Ctx = Depends(check_csrf)):
    if not ctx.user:
        raise HTTPException(302, headers={"Location": "/login"})
    if ctx.user.team_id:
        return ctx.redirect("/team", flash=("error", ctx.t("team.errors.already_in_team")))
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not (2 <= len(name) <= 60):
        return ctx.redirect("/team", flash=("error", ctx.t("team.errors.name_length")))
    if ctx.db.query(Team).filter(Team.name == name).first():
        return ctx.redirect("/team", flash=("error", ctx.t("team.errors.name_taken")))
    try:
        max_size = max(1, min(8, int(form.get("max_size") or 4)))
    except ValueError:
        max_size = 4
    slug = slugify(name)
    if ctx.db.query(Team).filter(Team.slug == slug).first():
        slug = f"{slug}-{secrets.token_hex(2)}"
    team = Team(name=name, slug=slug, leader_id=ctx.user.id, project_idea=(form.get("project_idea") or "").strip()[:2000],
                github_repo=(form.get("github_repo") or "").strip()[:255], max_size=max_size)
    ctx.db.add(team)
    ctx.db.flush()
    ctx.user.team_id = team.id
    ctx.user.looking_for_team = False
    ctx.db.add(AuditLog(user_id=ctx.user.id, action="team.create", detail={"team_id": team.id}))
    ctx.db.commit()
    return ctx.redirect("/team", flash=("success", ctx.t("flash.team_created")))


@router.post("/team/join")
async def team_join(request: Request, ctx: Ctx = Depends(check_csrf)):
    if not ctx.user:
        raise HTTPException(302, headers={"Location": "/login"})
    if ctx.user.team_id:
        return ctx.redirect("/team", flash=("error", ctx.t("team.errors.already_in_team")))
    form = await request.form()
    code = (form.get("invite_code") or "").strip().upper()
    team = ctx.db.query(Team).filter(Team.invite_code == code).first() if code else None
    if not team:
        return ctx.redirect("/team", flash=("error", ctx.t("team.errors.bad_code")))
    if team.is_locked:
        return ctx.redirect("/team", flash=("error", ctx.t("team.errors.locked")))
    if len(team.members) >= team.max_size:
        return ctx.redirect("/team", flash=("error", ctx.t("team.errors.full")))
    ctx.user.team_id = team.id
    ctx.user.looking_for_team = False
    ctx.db.add(AuditLog(user_id=ctx.user.id, action="team.join", detail={"team_id": team.id}))
    ctx.db.commit()
    return ctx.redirect("/team", flash=("success", ctx.t("flash.team_joined", name=team.name)))


@router.post("/team/update")
async def team_update(request: Request, ctx: Ctx = Depends(check_csrf)):
    team = _team_of(ctx)
    if not team or team.leader_id != ctx.user.id:
        raise HTTPException(403, "leader only")
    form = await request.form()
    action = form.get("action", "save")
    if action == "save":
        team.project_idea = (form.get("project_idea") or "").strip()[:2000]
        team.github_repo = (form.get("github_repo") or "").strip()[:255]
        try:
            team.max_size = max(len(team.members), min(8, int(form.get("max_size") or team.max_size)))
        except ValueError:
            pass
        team.is_locked = form.get("is_locked") == "on"
    elif action == "regenerate":
        team.invite_code = new_code()
    elif action == "kick":
        member = ctx.db.get(User, int(form.get("user_id") or 0))
        if member and member.team_id == team.id and member.id != team.leader_id:
            member.team_id = None
    elif action == "transfer":
        member = ctx.db.get(User, int(form.get("user_id") or 0))
        if member and member.team_id == team.id:
            team.leader_id = member.id
    elif action == "disband":
        if ctx.db.query(Submission).filter(Submission.team_id == team.id).count():
            return ctx.redirect("/team", flash=("error", ctx.t("team.errors.has_submissions")))
        for m in list(team.members):
            m.team_id = None
        ctx.db.delete(team)
        ctx.db.commit()
        return ctx.redirect("/team", flash=("success", ctx.t("flash.team_disbanded")))
    ctx.db.commit()
    return ctx.redirect("/team", flash=("success", ctx.t("flash.team_saved")))


@router.post("/team/leave")
async def team_leave(ctx: Ctx = Depends(check_csrf)):
    team = _team_of(ctx)
    if not team:
        return ctx.redirect("/team")
    if team.leader_id == ctx.user.id and len(team.members) > 1:
        return ctx.redirect("/team", flash=("error", ctx.t("team.errors.leader_must_transfer")))
    if team.leader_id == ctx.user.id and ctx.db.query(Submission).filter(Submission.team_id == team.id).count():
        return ctx.redirect("/team", flash=("error", ctx.t("team.errors.has_submissions")))
    ctx.user.team_id = None
    if len(team.members) <= 1:
        ctx.db.delete(team)
    ctx.db.commit()
    return ctx.redirect("/team", flash=("success", ctx.t("flash.team_left")))


# ------------------------------------------------------------------ submissions

def _phase_choices(ctx: Ctx) -> list[Phase]:
    return ctx.db.query(Phase).filter(Phase.is_active.is_(True)).order_by(Phase.order).all()


@router.get("/submit")
def submit_form(ctx: Ctx = Depends(require_user), phase: str | None = None):
    team = _team_of(ctx)
    phases = _phase_choices(ctx)
    scenarios = {p.id: [s for s in (ctx.db.get(Scenario, sid) for sid in (p.scenario_ids or [])) if s and s.is_active] for p in phases}
    quota = {p.id: (p.daily_limit - team_daily_submission_count(ctx.db, team.id, p.id)) if team else 0 for p in phases}
    return ctx.render("dashboard/submit.html", team=team, phases_all=phases, scenarios=scenarios, quota=quota, selected=phase)


@router.post("/submit")
async def submit(request: Request, ctx: Ctx = Depends(check_csrf)):
    if not ctx.user:
        raise HTTPException(302, headers={"Location": "/login"})
    team = _team_of(ctx)
    if not team:
        return ctx.redirect("/team", flash=("error", ctx.t("submit.errors.need_team")))
    form = await request.form()
    try:
        sub = create_submission(ctx, team, form)
    except SubmitError as exc:
        return ctx.redirect("/submit", flash=("error", str(exc)))
    return ctx.redirect(f"/submissions/{sub.id}", flash=("success", ctx.t("flash.submission_queued")))


class SubmitError(ValueError):
    pass


def create_submission(ctx: Ctx, team: Team, form, *, via_api: bool = False) -> Submission:
    """Shared by the HTML form and the JSON API. `form` is a starlette FormData."""
    t = ctx.t
    phase = ctx.db.query(Phase).filter(Phase.slug == (form.get("phase") or ""), Phase.is_active.is_(True)).first()
    if not phase:
        raise SubmitError(t("submit.errors.bad_phase"))
    if not phase.is_open and not ctx.is_admin:
        raise SubmitError(t("submit.errors.phase_closed"))
    kind = form.get("kind") or ""
    if kind not in ("results", "agent"):
        raise SubmitError(t("submit.errors.bad_kind"))
    if kind == "results" and not phase.allow_results:
        raise SubmitError(t("submit.errors.results_not_allowed"))
    if kind == "agent" and not phase.allow_agents:
        raise SubmitError(t("submit.errors.agents_not_allowed"))
    scenario = None
    if kind == "results":
        scenario = ctx.db.query(Scenario).filter(Scenario.slug == (form.get("scenario") or ""), Scenario.is_active.is_(True)).first()
        if not scenario or scenario.id not in (phase.scenario_ids or []):
            raise SubmitError(t("submit.errors.bad_scenario"))
        if not scenario.weather_public and not ctx.is_admin:
            raise SubmitError(t("submit.errors.scenario_hidden"))
    if team_daily_submission_count(ctx.db, team.id, phase.id) >= phase.daily_limit and not ctx.is_admin:
        raise SubmitError(t("submit.errors.daily_limit", n=phase.daily_limit))
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        raise SubmitError(t("submit.errors.file_required"))
    filename = Path(upload.filename).name
    suffix = Path(filename).suffix.lower()
    if kind == "results" and suffix != ".csv":
        raise SubmitError(t("submit.errors.results_csv"))
    if kind == "agent" and suffix not in (".py", ".zip"):
        raise SubmitError(t("submit.errors.agent_file"))
    max_bytes = ctx.settings.max_upload_mb * 1024 * 1024
    dest_dir = ctx.settings.uploads_dir / f"team-{team.id}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{utcnow().strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(3)}{suffix}"
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                out.close()
                dest.unlink(missing_ok=True)
                raise SubmitError(t("submit.errors.too_large", mb=ctx.settings.max_upload_mb))
            out.write(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise SubmitError(t("submit.errors.file_required"))
    try:
        if kind == "results":
            validate_decisions_file(dest)
        else:
            probe = ctx.settings.runs_dir / "_probe" / secrets.token_hex(4)
            prepare_agent_dir(dest, probe)
            import shutil
            shutil.rmtree(probe, ignore_errors=True)
    except (InvalidSubmission, AgentPackageError) as exc:
        dest.unlink(missing_ok=True)
        raise SubmitError(f"{t('submit.errors.invalid_file')}: {exc}")
    sub = Submission(team_id=team.id, user_id=ctx.user.id, phase_id=phase.id, scenario_id=scenario.id if scenario else None, kind=kind,
                     title=(form.get("title") or "").strip()[:160], notes=(form.get("notes") or "").strip()[:2000],
                     file_path=str(dest), original_filename=filename[:255], file_sha256=jobs.file_sha256(dest))
    ctx.db.add(sub)
    ctx.db.flush()
    jobs.enqueue(ctx.db, "evaluate_submission", {"submission_id": sub.id})
    ctx.db.add(AuditLog(user_id=ctx.user.id, action="submission.create", detail={"submission_id": sub.id, "kind": kind, "phase": phase.slug, "api": via_api}))
    ctx.db.commit()
    return sub


@router.get("/submissions")
def submissions(ctx: Ctx = Depends(require_user)):
    team = _team_of(ctx)
    rows = ctx.db.query(Submission).filter(Submission.team_id == team.id).order_by(Submission.created_at.desc()).all() if team else []
    return ctx.render("dashboard/submissions.html", team=team, submissions=rows)


def _load_submission(ctx: Ctx, sid: int) -> Submission:
    sub = ctx.db.get(Submission, sid)
    if not sub:
        raise HTTPException(404, "submission not found")
    if not ctx.is_admin and (not ctx.user.team_id or sub.team_id != ctx.user.team_id):
        raise HTTPException(403, "not your submission")
    return sub


@router.get("/submissions/{sid}")
def submission_detail(sid: int, ctx: Ctx = Depends(require_user)):
    sub = _load_submission(ctx, sid)
    evals = ctx.db.query(Evaluation).filter(Evaluation.submission_id == sub.id).order_by(Evaluation.id).all()
    reports = {}
    for ev in evals:
        if ev.report_path and Path(ev.report_path).exists():
            try:
                import json
                reports[ev.id] = json.loads(Path(ev.report_path).read_text(encoding="utf-8"))
            except Exception:
                pass
    return ctx.render("dashboard/submission_detail.html", sub=sub, evals=evals, reports=reports)


@router.get("/submissions/{sid}/eval/{eid}/{what}")
def submission_artifact(sid: int, eid: int, what: str, ctx: Ctx = Depends(require_user)):
    sub = _load_submission(ctx, sid)
    ev = ctx.db.get(Evaluation, eid)
    if not ev or ev.submission_id != sub.id:
        raise HTTPException(404)
    mapping = {"report.json": (ev.report_path, "application/json"), "decisions.csv": (ev.decisions_path, "text/csv"), "agent.log": (ev.log_path, "text/plain")}
    if what not in mapping or not mapping[what][0] or not Path(mapping[what][0]).exists():
        raise HTTPException(404)
    path, media = mapping[what]
    return FileResponse(path, media_type=media, filename=f"submission-{sub.id}-{ev.scenario.slug}-{what}")


@router.post("/submissions/{sid}/cancel")
async def submission_cancel(sid: int, ctx: Ctx = Depends(check_csrf)):
    if not ctx.user:
        raise HTTPException(302, headers={"Location": "/login"})
    sub = _load_submission(ctx, sid)
    if sub.status == "queued":
        sub.status = "cancelled"
        sub.finished_at = utcnow()
        ctx.db.commit()
        return ctx.redirect(f"/submissions/{sid}", flash=("success", ctx.t("flash.submission_cancelled")))
    return ctx.redirect(f"/submissions/{sid}", flash=("error", ctx.t("submit.errors.cannot_cancel")))


@router.get("/teams/{slug}")
def team_public(slug: str, ctx: Ctx = Depends(require_user)):
    team = ctx.db.query(Team).filter(Team.slug == slug).first()
    if not team:
        raise HTTPException(404)
    return ctx.render("dashboard/team_public.html", team=team)
