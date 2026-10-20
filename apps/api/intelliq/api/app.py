"""FastAPI application factory."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..config import Settings
from ..connectors import duckdb_connector
from ..platform.ratelimit import RateLimiter
from ..platform.secrets import SecretBox
from ..platform.store import Store
from ..query.executor import QueryExecutor
from ..spec.dashboard import json_schema
from .context import AppContext
from .routes import admin, auth, connections, dashboards, datasets, imports, query

log = logging.getLogger("intelliq")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.warehouse_dir.mkdir(parents=True, exist_ok=True)
    duckdb_connector.EXTRA_ROOTS[:] = [settings.warehouse_dir]

    app = FastAPI(title="Intelliq API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.ctx = AppContext(
        settings=settings,
        store=Store(settings.metadata_path),
        secrets=SecretBox.from_settings(settings.secret_key, settings.data_dir, settings.dev_mode),
        executor=QueryExecutor(),
        login_limiter=RateLimiter(settings.login_rate_per_minute),
        query_limiter=RateLimiter(settings.query_rate_per_minute),
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        body = detail if isinstance(detail, dict) else {"message": str(detail)}
        return JSONResponse(body, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        errors = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse({"message": "Request is invalid", "errors": errors}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(_request: Request, exc: Exception):
        log.exception("Unhandled error", exc_info=exc)
        return JSONResponse({"message": "An unexpected error occurred"}, status_code=500)

    @app.get("/api/health", include_in_schema=False)
    def health():
        return {"status": "ok"}

    @app.get("/api/meta/dashboard-schema")
    def dashboard_schema():
        return json_schema()

    for module in (auth, admin, connections, datasets, query, dashboards, imports):
        app.include_router(module.router)

    if settings.web_dist and settings.web_dist.exists():
        dist: Path = settings.web_dist
        index = dist / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "Not found")
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and dist in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index)

    return app
