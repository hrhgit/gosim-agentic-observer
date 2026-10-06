from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import markdown

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response

from ..config import ROOT
from ..deps import Ctx, get_ctx
from ..models import Announcement, Phase, Scenario, Submission, Team
from ..services.leaderboard import leaderboard_payload
from ..services.scenarios import scenario_paths
from ..services.starter_kit import build_starter_kit_zip

router = APIRouter()
DOCS_DIR = ROOT / "app" / "content"


@lru_cache(maxsize=16)
def _render_doc(path: str, mtime: float) -> str:
    text = Path(path).read_text(encoding="utf-8")
    return markdown.markdown(text, extensions=["tables", "fenced_code", "toc"], output_format="html5")


def _read_doc(name: str, locale: str) -> str:
    p = DOCS_DIR / f"{name}.{locale}.md"
    if not p.exists():
        p = DOCS_DIR / f"{name}.en.md"
    if not p.exists():
        return ""
    return _render_doc(str(p), p.stat().st_mtime)


@router.get("/")
def home(ctx: Ctx = Depends(get_ctx)):
    phases = ctx.db.query(Phase).filter(Phase.is_active.is_(True)).order_by(Phase.order).all()
    board_phase = next((p for p in phases if p.counts_for_final and p.status in ("open", "closed")), None) or next((p for p in phases if p.status == "open"), None) or (phases[0] if phases else None)
    board = leaderboard_payload(ctx.db, board_phase, limit=10, is_admin=ctx.is_admin) if board_phase else None
    stats = {
        "teams": ctx.db.query(Team).filter(Team.is_hidden.is_(False)).count(),
        "submissions": ctx.db.query(Submission).filter(Submission.status == "scored").count(),
    }
    announcements = ctx.db.query(Announcement).filter(Announcement.is_published.is_(True)).order_by(Announcement.created_at.desc()).limit(3).all()
    return ctx.render("home.html", board=board, board_phase=board_phase, stats=stats, announcements=announcements)


@router.get("/brief")
def brief(ctx: Ctx = Depends(get_ctx)):
    return ctx.render("brief.html")


@router.get("/rules")
def rules(ctx: Ctx = Depends(get_ctx)):
    phases = ctx.db.query(Phase).filter(Phase.is_active.is_(True)).order_by(Phase.order).all()
    return ctx.render("rules.html", doc=_read_doc("rules", ctx.locale), phases_all=phases)


@router.get("/docs")
def docs(ctx: Ctx = Depends(get_ctx)):
    return ctx.render("docs.html", doc=_read_doc("docs", ctx.locale))


@router.get("/faq")
def faq(ctx: Ctx = Depends(get_ctx)):
    return ctx.render("faq.html")


@router.get("/resources")
def resources(ctx: Ctx = Depends(get_ctx)):
    scenarios = ctx.db.query(Scenario).filter(Scenario.is_active.is_(True)).order_by(Scenario.id).all()
    return ctx.render("resources.html", scenarios=scenarios)


@router.get("/announcements")
def announcements(ctx: Ctx = Depends(get_ctx)):
    rows = ctx.db.query(Announcement).filter(Announcement.is_published.is_(True)).order_by(Announcement.is_pinned.desc(), Announcement.created_at.desc()).all()
    return ctx.render("announcements.html", rows=rows)


@router.get("/leaderboard")
@router.get("/leaderboard/{slug}")
def leaderboard(slug: str | None = None, ctx: Ctx = Depends(get_ctx)):
    phases = ctx.db.query(Phase).filter(Phase.is_active.is_(True)).order_by(Phase.order).all()
    if not phases:
        return ctx.render("leaderboard.html", phase=None, board=None, phases_all=[])
    phase = next((p for p in phases if p.slug == slug), None) if slug else None
    if slug and phase is None:
        raise HTTPException(404, "phase not found")
    if phase is None:
        phase = next((p for p in phases if p.counts_for_final and p.status in ("open", "closed")), None) or next((p for p in phases if p.status == "open"), None) or phases[0]
    board = leaderboard_payload(ctx.db, phase, is_admin=ctx.is_admin)
    scenarios = [ctx.db.get(Scenario, sid) for sid in (phase.scenario_ids or [])]
    return ctx.render("leaderboard.html", phase=phase, board=board, phases_all=phases, scenarios=[s for s in scenarios if s])


@router.get("/lang/{locale}")
def set_lang(locale: str, next: str = "/", ctx: Ctx = Depends(get_ctx)):
    if locale not in ("zh", "en"):
        locale = "zh"
    if not next.startswith("/") or next.startswith("//"):
        next = "/"
    resp = RedirectResponse(next, status_code=303)
    resp.set_cookie("sac_lang", locale, max_age=365 * 86400, samesite="lax")
    if ctx.user:
        ctx.user.locale = locale
        ctx.db.commit()
    return resp


# ----------------------------- downloads -----------------------------------

@router.get("/download/starter-kit.zip")
def download_starter_kit(ctx: Ctx = Depends(get_ctx)):
    skill = (ROOT / "starter_kit" / "SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "starter_kit" / "README.md").read_text(encoding="utf-8")
    data = build_starter_kit_zip(skill, readme)
    return Response(data, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="agent-observer-starter-kit.zip"'})


@router.get("/download/scorer.py")
def download_scorer():
    return FileResponse(ROOT / "scoring" / "scorer.py", media_type="text/x-python", filename="scorer.py")


@router.get("/download/protocol.py")
def download_protocol():
    return FileResponse(ROOT / "scoring" / "protocol.py", media_type="text/x-python", filename="protocol.py")


@router.get("/skill.md")
def skill_md(ctx: Ctx = Depends(get_ctx)):
    text = (ROOT / "starter_kit" / "SKILL.md").read_text(encoding="utf-8").replace("{{BASE_URL}}", ctx.settings.base_url.rstrip("/"))
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


@router.get("/download/scenario/{slug}/{filename}")
def download_scenario_file(slug: str, filename: str, ctx: Ctx = Depends(get_ctx)):
    scn = ctx.db.query(Scenario).filter(Scenario.slug == slug, Scenario.is_active.is_(True)).first()
    if scn is None:
        raise HTTPException(404, "scenario not found")
    paths = scenario_paths(scn)
    allowed = {"weather.csv": ("weather", scn.weather_public), "tiles.csv": ("tiles", scn.tiles_public), "score_config.json": ("config", True)}
    if filename not in allowed:
        raise HTTPException(404, "file not found")
    key, public = allowed[filename]
    if not public and not ctx.is_admin:
        raise HTTPException(403, "this file is not public")
    path: Path = paths[key]
    if not path.exists():
        raise HTTPException(404, "file missing on server")
    media = "text/csv" if filename.endswith(".csv") else "application/json"
    return FileResponse(path, media_type=media, filename=f"{slug}-{filename}")


@router.get("/robots.txt")
def robots():
    return PlainTextResponse("User-agent: *\nDisallow: /admin\nDisallow: /dashboard\nDisallow: /submissions\n")
