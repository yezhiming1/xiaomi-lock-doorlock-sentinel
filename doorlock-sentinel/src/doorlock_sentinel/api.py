from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .artifacts import ManifestWorker
from .config import Settings, get_settings
from .db import Database
from .download_status import DownloadStatusWorker
from .ingest import IngestWorker
from .internal_api import router as internal_router
from .logging_setup import configure_logging
from .pipeline import ProcessingPipeline
from .security import SecurityService
from .web_auth import router as auth_router
from .web_data import router as data_router


class Runtime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.database = Database(settings)
        self.security = SecurityService(settings)
        self.pipeline = ProcessingPipeline(settings, self.database)
        self.ingest = IngestWorker(settings, self.database, self.pipeline)
        self.download_status = DownloadStatusWorker(settings, self.database)
        self.manifest = ManifestWorker(settings, self.database)
        self.tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        self.settings.ensure_writable_directories()
        with self.database.session() as session:
            self.security.prune_sessions(session)
        self.tasks = [
            asyncio.create_task(self.ingest.run(), name="ingest-worker"),
            asyncio.create_task(
                self.download_status.run(), name="download-status-worker"
            ),
            asyncio.create_task(self.manifest.run(), name="manifest-worker"),
        ]

    async def stop(self) -> None:
        self.ingest.stop()
        self.download_status.stop()
        self.manifest.stop()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.database.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    runtime = Runtime(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(
        title="门锁证据台",
        version="0.0.2",
        docs_url=None if settings.environment == "production" else "/docs",
        redoc_url=None,
        openapi_url=None if settings.environment == "production" else "/openapi.json",
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    @app.middleware("http")
    async def harden(request: Request, call_next):
        request_id = request.headers.get("x-request-id", "")[:64] or secrets.token_hex(12)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; media-src 'self'; "
            "style-src 'self'; script-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.url.path.startswith("/api/") or request.url.path in {
            "/",
            "/index.html",
            "/app.css",
            "/app.js",
        }:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(404)
    async def not_found(_request: Request, _exc: Exception):
        return JSONResponse(status_code=404, content={"detail": "资源不存在"})

    @app.get("/health/live")
    def live() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "doorlock-sentinel",
            "version": "0.0.2",
            "time": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        database_ok = True
        try:
            with runtime.database.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            database_ok = False
        model_ok = runtime.pipeline.ready
        healthy = database_ok and model_ok
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={
                "status": "ready" if healthy else "degraded",
                "database": database_ok,
                "model": model_ok,
            },
        )

    app.include_router(auth_router)
    app.include_router(data_router)
    app.include_router(internal_router)

    static_dir = Path(__file__).with_name("static")
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="console")
    return app
