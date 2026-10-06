from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import ROOT, get_settings
from .db import init_db, session_scope
from .deps import Ctx
from .routes import admin, api, auth, dashboard, public
from .services import jobs, seed

log = logging.getLogger("sac")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    init_db()
    with session_scope() as db:
        seed.ensure_bootstrap(db)
    if s.inline_worker:
        jobs.start_workers()
    yield
    if s.inline_worker:
        jobs.stop_workers()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.site_name, lifespan=lifespan, docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.mount("/static", StaticFiles(directory=str(ROOT / "app" / "static")), name="static")
    app.include_router(public.router)
    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(admin.router)
    app.include_router(api.router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code in (301, 302, 303, 307) and exc.headers and "Location" in exc.headers:
            return RedirectResponse(exc.headers["Location"], status_code=exc.status_code)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
        from .db import SessionLocal
        db = SessionLocal()
        try:
            ctx = Ctx(request, db)
            return ctx.render("error.html", status_code=exc.status_code, status=exc.status_code, detail=exc.detail)
        finally:
            db.close()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    return app


app = create_app()
