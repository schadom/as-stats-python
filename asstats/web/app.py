"""FastAPI application: JSON API under /api, the single page app everywhere else."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from .. import __version__
from ..config import get_config
from ..db import get_engine, init_db, session_scope
from ..models import ROLE_ADMIN, User
from ..security import hash_password, random_password
from . import routes_admin, routes_auth, routes_config, routes_stats

log = logging.getLogger(__name__)

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER = "x-requested-with"


def bootstrap_admin() -> None:
    """Create the first admin account if there are no users yet."""
    cfg = get_config()
    with session_scope() as db:
        if db.execute(select(func.count()).select_from(User)).scalar_one() > 0:
            return
        password = cfg.admin_password
        generated = password is None
        if generated:
            password = random_password()
        db.add(
            User(
                username=cfg.admin_user,
                password_hash=hash_password(password),
                role=ROLE_ADMIN,
                must_change_password=generated,
                created_at=int(time.time()),
            )
        )
    if generated:
        banner = "=" * 64
        log.warning(
            "\n%s\n  Created initial admin user '%s' with password: %s\n  You will be asked to change it after the first login.\n%s",
            banner,
            cfg.admin_user,
            password,
            banner,
        )
    else:
        log.info("created initial admin user '%s' from ASSTATS_ADMIN_PASSWORD", cfg.admin_user)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    bootstrap_admin()
    cfg = get_config()
    app.state.scheduler = None
    if cfg.scheduler_enabled:
        from ..services.scheduler import Scheduler

        app.state.scheduler = Scheduler(get_engine())
        app.state.scheduler.start()
    yield
    if app.state.scheduler is not None:
        await app.state.scheduler.stop()


def create_app() -> FastAPI:
    cfg = get_config()
    app = FastAPI(title="AS-Stats", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)

    @app.middleware("http")
    async def csrf_and_headers(request: Request, call_next):
        # Browsers cannot send custom headers cross-site without a CORS preflight,
        # which we never allow. Together with SameSite=Lax cookies this blocks CSRF.
        if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
            if request.headers.get(CSRF_HEADER, "").lower() != "xmlhttprequest":
                return JSONResponse({"detail": "missing X-Requested-With header"}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    for module in (routes_auth, routes_config, routes_stats, routes_admin):
        app.include_router(module.router, prefix="/api")

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def api_not_found(path: str):
        return JSONResponse({"detail": "not found"}, status_code=404)

    static = cfg.static_dir
    if static is not None and (static / "index.html").exists():
        assets = static / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")
        index = static / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            candidate = (static / path).resolve()
            if path and candidate.is_file() and Path(static.resolve()) in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
    else:

        @app.get("/", include_in_schema=False)
        def no_ui():
            return JSONResponse({"detail": "UI not built; the API is available under /api"})

    return app


def main() -> None:
    import uvicorn

    cfg = get_config()
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s [web] %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(
        "asstats.web.app:create_app",
        factory=True,
        host=cfg.web_host,
        port=cfg.web_port,
        proxy_headers=bool(cfg.trusted_proxies),
        forwarded_allow_ips=",".join(cfg.trusted_proxies) or None,
        log_level=cfg.log_level.lower(),
        access_log=cfg.log_level == "DEBUG",
    )
