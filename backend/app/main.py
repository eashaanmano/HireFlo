"""HireFlo (DriveOps) API application factory."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .core.config import get_settings
from .core.database import SessionLocal, create_all
from .core.security import CSRF_HEADER, SESSION_COOKIE
from .routers import assistant, auth, cell, cell_drives, panel, recruiter, student, ws
from .services import live, nudges, tracing
from .services.events import manager

log = logging.getLogger("driveops")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


async def _monitor(interval: float) -> None:
    """Background drive-day monitor: flags overruns and late arrivals and drafts re-plan proposals."""
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(_monitor_once)
        except Exception:  # noqa: BLE001 — never let the monitor die
            log.exception("drive monitor tick failed")


def _monitor_once() -> None:
    with SessionLocal() as db:
        live.monitor_tick(db)
        nudges.deadline_nudges(db)
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    create_all()
    tracing.configure(settings)  # LangSmith, when LANGSMITH_TRACING is on
    manager.bind_loop(asyncio.get_running_loop())
    task = asyncio.create_task(_monitor(settings.monitor_interval_sec))
    if settings.secret_key.startswith("dev-insecure") and not settings.demo_mode:
        log.warning("SECRET_KEY is the development default. Set a real secret before deploying.")
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await asyncio.to_thread(tracing.flush)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="HireFlo (DriveOps) API", version="2.0.0", lifespan=lifespan,
                  description="Operations layer for campus placement drives: students, placement cell, recruiters.")

    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", CSRF_HEADER, "X-Access-Token"],
    )

    @app.middleware("http")
    async def security(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # CSRF: cookie-authenticated writes must carry a custom header, which cross-site forms can't send
        # and cross-origin scripts can't add without passing CORS. Magic-link calls use a header token instead.
        if (request.method in UNSAFE_METHODS and request.url.path.startswith("/api/")
                and request.cookies.get(SESSION_COOKIE) and request.headers.get(CSRF_HEADER) != "1"
                and not request.headers.get("X-Access-Token")):
            return JSONResponse({"detail": "Missing CSRF header."}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")  # keeps magic-link tokens out of referrers
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'self'",
        )
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    for r in (auth.router, student.router, cell.router, cell_drives.router, recruiter.router, panel.router,
              assistant.router, ws.router):
        app.include_router(r)

    @app.get("/api/health", tags=["ops"])
    def health() -> dict[str, Any]:
        """Liveness plus a one-row database check (useful with Neon, whose compute sleeps when idle)."""
        from sqlalchemy import text

        from .core.database import backend_name
        from .services import llm

        db_ok = True
        try:
            with SessionLocal() as db:
                db.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            db_ok = False
        return {"status": "ok" if db_ok else "degraded", "database": backend_name(), "database_ok": db_ok,
                "llm": [p["name"] for p in llm.status()["configured"]]}

    _mount_frontend(app, settings.static_dir)
    return app


def _mount_frontend(app: FastAPI, static_dir: str) -> None:
    """Optionally serve the built React app (single-container deploys). SPA routes fall back to index.html."""
    if not static_dir:
        return
    root = Path(static_dir).resolve()
    index = root / "index.html"
    if not index.is_file():
        log.warning("STATIC_DIR %s has no index.html; frontend not mounted", root)
        return
    if (root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith(("api/", "ws")):
            raise HTTPException(status_code=404, detail="Not found.")
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
