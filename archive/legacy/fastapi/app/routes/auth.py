from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request

from ..deps import Ctx, check_csrf, get_ctx
from ..models import AuditLog, Token, User, utcnow
from ..security import (hash_password, login_limiter, password_problems, register_limiter, sign_session, valid_email,
                        verify_password)
from ..services.mailer import send_mail

router = APIRouter()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?"))


def _safe_next(value: str | None) -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return "/dashboard"


def _login_response(ctx: Ctx, user: User, next_url: str):
    resp = ctx.redirect(next_url)
    s = ctx.settings
    resp.set_cookie(s.session_cookie, sign_session(user.id), max_age=s.session_days * 86400, httponly=True, samesite="lax", secure=s.secure_cookies)
    user.last_login_at = utcnow()
    ctx.db.commit()
    return resp


@router.get("/register")
def register_form(ctx: Ctx = Depends(get_ctx), next: str = "/dashboard"):
    if ctx.user:
        return ctx.redirect("/dashboard")
    return ctx.render("auth/register.html", next=next, form={})


@router.post("/register")
async def register(request: Request, ctx: Ctx = Depends(check_csrf)):
    form = await request.form()
    data = {k: (form.get(k) or "").strip() for k in ("name", "email", "password", "password2", "github", "affiliation", "next")}
    data["email"] = data["email"].lower()
    looking = form.get("looking_for_team") == "on"
    errors: list[str] = []
    if not ctx.site_setting("registration_open", ctx.settings.registration_open):
        errors.append(ctx.t("auth.errors.registration_closed"))
    if not register_limiter.check(_client_ip(request)):
        errors.append(ctx.t("auth.errors.rate_limited"))
    if not data["name"] or len(data["name"]) > 120:
        errors.append(ctx.t("auth.errors.name_required"))
    if not valid_email(data["email"]):
        errors.append(ctx.t("auth.errors.email_invalid"))
    problem = password_problems(data["password"])
    if problem:
        errors.append(ctx.t(f"auth.errors.{problem}"))
    if data["password"] != data["password2"]:
        errors.append(ctx.t("auth.errors.password_mismatch"))
    if form.get("agree") != "on":
        errors.append(ctx.t("auth.errors.agree_required"))
    if not errors and ctx.db.query(User).filter(User.email == data["email"]).first():
        errors.append(ctx.t("auth.errors.email_taken"))
    if errors:
        return ctx.render("auth/register.html", status_code=400, errors=errors, form={**data, "looking_for_team": looking}, next=data["next"])
    user = User(email=data["email"], name=data["name"], password_hash=hash_password(data["password"]), github=data["github"][:120],
                affiliation=data["affiliation"][:200], looking_for_team=looking, locale=ctx.locale,
                is_admin=data["email"] in ctx.settings.admin_email_set, is_verified=not ctx.settings.email_enabled)
    ctx.db.add(user)
    ctx.db.flush()
    ctx.db.add(AuditLog(user_id=user.id, action="register", detail={"ip": _client_ip(request)}))
    if ctx.settings.email_enabled:
        tok = Token(user_id=user.id, purpose="verify", expires_at=utcnow() + timedelta(days=3))
        ctx.db.add(tok)
        ctx.db.flush()
        send_mail(user.email, ctx.t("mail.verify_subject"), ctx.t("mail.verify_body", url=f"{ctx.settings.base_url}/verify/{tok.token}"))
    ctx.db.commit()
    return _login_response(ctx, user, _safe_next(data["next"]))


@router.get("/login")
def login_form(ctx: Ctx = Depends(get_ctx), next: str = "/dashboard"):
    if ctx.user:
        return ctx.redirect(_safe_next(next))
    return ctx.render("auth/login.html", next=next)


@router.post("/login")
async def login(request: Request, ctx: Ctx = Depends(check_csrf)):
    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    password = form.get("password") or ""
    next_url = _safe_next(form.get("next"))
    if not login_limiter.check(_client_ip(request)) or not login_limiter.check("email:" + email):
        return ctx.render("auth/login.html", status_code=429, errors=[ctx.t("auth.errors.rate_limited")], next=next_url, email=email)
    user = ctx.db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        return ctx.render("auth/login.html", status_code=401, errors=[ctx.t("auth.errors.bad_credentials")], next=next_url, email=email)
    if user.is_banned:
        return ctx.render("auth/login.html", status_code=403, errors=[ctx.t("auth.errors.banned")], next=next_url, email=email)
    ctx.db.add(AuditLog(user_id=user.id, action="login", detail={"ip": _client_ip(request)}))
    return _login_response(ctx, user, next_url)


@router.post("/logout")
async def logout(ctx: Ctx = Depends(check_csrf)):
    resp = ctx.redirect("/")
    resp.delete_cookie(ctx.settings.session_cookie)
    return resp


@router.get("/verify/{token}")
def verify(token: str, ctx: Ctx = Depends(get_ctx)):
    tok = ctx.db.query(Token).filter(Token.token == token, Token.purpose == "verify", Token.used.is_(False)).first()
    if not tok or tok.expires_at < utcnow():
        return ctx.render("auth/message.html", status_code=400, title=ctx.t("auth.verify_failed_title"), message=ctx.t("auth.verify_failed"))
    user = ctx.db.get(User, tok.user_id)
    user.is_verified = True
    tok.used = True
    ctx.db.commit()
    return ctx.render("auth/message.html", title=ctx.t("auth.verify_ok_title"), message=ctx.t("auth.verify_ok"))


@router.get("/forgot")
def forgot_form(ctx: Ctx = Depends(get_ctx)):
    return ctx.render("auth/forgot.html")


@router.post("/forgot")
async def forgot(request: Request, ctx: Ctx = Depends(check_csrf)):
    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    user = ctx.db.query(User).filter(User.email == email).first()
    if user and login_limiter.check("reset:" + email):
        tok = Token(user_id=user.id, purpose="reset", expires_at=utcnow() + timedelta(hours=2))
        ctx.db.add(tok)
        ctx.db.flush()
        send_mail(user.email, ctx.t("mail.reset_subject"), ctx.t("mail.reset_body", url=f"{ctx.settings.base_url}/reset/{tok.token}"))
        ctx.db.commit()
    return ctx.render("auth/message.html", title=ctx.t("auth.forgot_sent_title"),
                      message=ctx.t("auth.forgot_sent") if ctx.settings.email_enabled else ctx.t("auth.forgot_no_email"))


@router.get("/reset/{token}")
def reset_form(token: str, ctx: Ctx = Depends(get_ctx)):
    tok = ctx.db.query(Token).filter(Token.token == token, Token.purpose == "reset", Token.used.is_(False)).first()
    if not tok or tok.expires_at < utcnow():
        return ctx.render("auth/message.html", status_code=400, title=ctx.t("auth.reset_invalid_title"), message=ctx.t("auth.reset_invalid"))
    return ctx.render("auth/reset.html", token=token)


@router.post("/reset/{token}")
async def reset(token: str, request: Request, ctx: Ctx = Depends(check_csrf)):
    form = await request.form()
    tok = ctx.db.query(Token).filter(Token.token == token, Token.purpose == "reset", Token.used.is_(False)).first()
    if not tok or tok.expires_at < utcnow():
        return ctx.render("auth/message.html", status_code=400, title=ctx.t("auth.reset_invalid_title"), message=ctx.t("auth.reset_invalid"))
    pw, pw2 = form.get("password") or "", form.get("password2") or ""
    problem = password_problems(pw)
    if problem or pw != pw2:
        return ctx.render("auth/reset.html", status_code=400, token=token, errors=[ctx.t(f"auth.errors.{problem}") if problem else ctx.t("auth.errors.password_mismatch")])
    user = ctx.db.get(User, tok.user_id)
    user.password_hash = hash_password(pw)
    user.is_verified = True
    tok.used = True
    ctx.db.commit()
    return _login_response(ctx, user, "/dashboard")
