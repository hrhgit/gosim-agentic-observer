"""Request-scoped helpers: current user, locale, CSRF, flash messages, template rendering."""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy.orm import Session

from .config import ROOT, get_settings
from .db import get_db
from .i18n import Translator, negotiate_locale
from .models import Announcement, Phase, SiteSetting, User, utcnow
from .security import csrf_token_for, read_session, verify_csrf

templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))
templates.env.autoescape = True
templates.env.trim_blocks = True
templates.env.lstrip_blocks = True


def _flash_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().secret_key, salt="flash")


class Ctx:
    """Everything a template needs, computed once per request."""

    def __init__(self, request: Request, db: Session):
        self.request = request
        self.db = db
        s = get_settings()
        self.settings = s
        self.session_value = request.cookies.get(s.session_cookie, "")
        uid = read_session(self.session_value)
        self.user: Optional[User] = db.get(User, uid) if uid else None
        if self.user and self.user.is_banned:
            self.user = None
        self.locale = negotiate_locale(request.query_params.get("lang"), request.cookies.get("sac_lang"),
                                       request.headers.get("accept-language"), s.default_locale)
        if self.user and not request.query_params.get("lang") and not request.cookies.get("sac_lang"):
            self.locale = self.user.locale or self.locale
        self.t = Translator(self.locale)
        self.csrf_token = csrf_token_for(self.session_value)
        self.flash: list[dict] = []
        raw = request.cookies.get("sac_flash")
        if raw:
            try:
                self.flash = _flash_serializer().loads(raw)
            except BadSignature:
                self.flash = []
        self._clear_flash = bool(raw)

    @property
    def is_admin(self) -> bool:
        return bool(self.user and self.user.is_admin)

    def site_setting(self, key: str, default: Any = None) -> Any:
        row = self.db.get(SiteSetting, key)
        return row.value.get("v", default) if row else default

    def render(self, name: str, status_code: int = 200, **extra: Any) -> Response:
        phases = self.db.query(Phase).filter(Phase.is_active.is_(True)).order_by(Phase.order, Phase.id).all()
        banner = (self.db.query(Announcement).filter(Announcement.is_published.is_(True), Announcement.is_pinned.is_(True))
                  .order_by(Announcement.created_at.desc()).first())
        ctx = {
            "request": self.request, "t": self.t, "locale": self.locale, "user": self.user, "is_admin": self.is_admin,
            "csrf_token": self.csrf_token, "settings": self.settings, "phases": phases, "banner": banner,
            "flash": self.flash, "now": utcnow(), "registration_open": self.site_setting("registration_open", self.settings.registration_open),
            "pick": self.t.pick, "path": self.request.url.path, "json": json,
        }
        ctx.update(extra)
        resp = templates.TemplateResponse(self.request, name, ctx, status_code=status_code)
        self._finalize(resp)
        return resp

    def _finalize(self, resp: Response) -> None:
        if self._clear_flash:
            resp.delete_cookie("sac_flash")
        if self.request.query_params.get("lang") in ("zh", "en"):
            resp.set_cookie("sac_lang", self.request.query_params["lang"], max_age=365 * 86400, samesite="lax")

    def redirect(self, url: str, *, flash: Optional[tuple[str, str]] = None, status_code: int = 303) -> RedirectResponse:
        resp = RedirectResponse(url, status_code=status_code)
        if flash:
            level, message = flash
            resp.set_cookie("sac_flash", _flash_serializer().dumps([{"level": level, "message": message}]), max_age=60, samesite="lax", httponly=True)
        elif self._clear_flash:
            resp.delete_cookie("sac_flash")
        return resp

    def msg(self, key: str, **fmt: Any) -> str:
        return self.t(key, **fmt)


def get_ctx(request: Request, db: Session = Depends(get_db)) -> Ctx:
    return Ctx(request, db)


def require_user(ctx: Ctx = Depends(get_ctx)) -> Ctx:
    if not ctx.user:
        raise HTTPException(status_code=302, headers={"Location": f"/login?next={ctx.request.url.path}"})
    return ctx


def require_admin(ctx: Ctx = Depends(get_ctx)) -> Ctx:
    if not ctx.user:
        raise HTTPException(status_code=302, headers={"Location": f"/login?next={ctx.request.url.path}"})
    if not ctx.user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return ctx


async def form_data(request: Request) -> dict:
    form = await request.form()
    return {k: (v if isinstance(v, str) else v) for k, v in form.multi_items()}


async def check_csrf(request: Request, ctx: Ctx = Depends(get_ctx)) -> Ctx:
    form = await request.form()
    token = form.get("csrf_token") or request.headers.get("x-csrf-token")
    if not verify_csrf(ctx.session_value, token):
        raise HTTPException(status_code=400, detail="invalid CSRF token")
    return ctx


def api_user(request: Request, db: Session = Depends(get_db)) -> User:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.query_params.get("token", "")
    if token:
        user = db.query(User).filter(User.api_token == token).first()
    else:
        # no token supplied: allow the session cookie for same-origin JS
        uid = read_session(request.cookies.get(get_settings().session_cookie))
        user = db.get(User, uid) if uid else None
    if not user or user.is_banned:
        raise HTTPException(status_code=401, detail="authentication required")
    return user
