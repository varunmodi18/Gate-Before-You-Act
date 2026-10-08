"""FastAPI application: REST under ``/api/v1`` and, when built, the SPA at ``/``."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from gbya.api.routers import health, playground, windows
from gbya.config import Settings, get_settings
from gbya.errors import GbyaError, NotFound
from gbya.store.db import make_engine, make_sessionmaker

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="GateBench API", version="0.1.0", openapi_url=f"{API_PREFIX}/openapi.json")
    app.state.settings = settings
    app.state.sessionmaker = make_sessionmaker(make_engine(settings.resolve(settings.app_db_path)))

    # Every error uses the envelope of plan §F.6.
    @app.exception_handler(GbyaError)
    async def _domain_error(_: Request, exc: GbyaError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.envelope())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        err = GbyaError(
            "Request validation failed",
            code="VALIDATION_ERROR",
            details={"errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]},
        )
        return JSONResponse(status_code=422, content=err.envelope())

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else f"HTTP_{exc.status_code}"
        err = GbyaError(str(exc.detail), code=code)
        return JSONResponse(status_code=exc.status_code, content=err.envelope())

    app.include_router(health.router, prefix=API_PREFIX)
    app.include_router(windows.router, prefix=API_PREFIX)
    app.include_router(playground.router, prefix=API_PREFIX)

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
