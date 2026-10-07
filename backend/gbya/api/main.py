"""FastAPI application: REST under ``/api/v1`` and, when built, the SPA at ``/``."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from gbya.api.routers import health
from gbya.config import Settings, get_settings
from gbya.errors import GbyaError, NotFound

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="GateBench API", version="0.1.0", openapi_url=f"{API_PREFIX}/openapi.json")

    @app.exception_handler(GbyaError)
    async def _domain_error(_: Request, exc: GbyaError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.envelope())

    app.include_router(health.router, prefix=API_PREFIX)

    dist = settings.resolve(settings.frontend_dist)
    if (dist / "index.html").is_file():
        _mount_spa(app, dist)
    return app


def _mount_spa(app: FastAPI, dist: Path) -> None:
    """Serve built assets, and ``index.html`` for client-side routes (never for ``/api``)."""
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    index = dist / "index.html"
    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    async def _spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise NotFound(f"No API route /{path}")
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
