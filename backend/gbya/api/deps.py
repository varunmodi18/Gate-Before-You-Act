"""Shared API dependencies: settings, app.db sessions and the experiment dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from gbya.config import Settings
from gbya.experiments.runner import Deps


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.sessionmaker
    session = factory()
    try:
        yield session
    finally:
        session.close()


def get_deps(request: Request) -> Deps:
    """Gate, verifier and retrieval dependencies (LLM client per ``llm_backend``), built once."""
    deps: Deps | None = getattr(request.app.state, "deps", None)
    if deps is None:
        from gbya.llm.factory import make_client
        from gbya.llm.tokens import default_counter

        settings = get_settings(request)
        deps = Deps(settings=settings, client=make_client(settings), counter=default_counter())
        request.app.state.deps = deps
    return deps
