"""Readiness endpoint (plan §F.5). Worker, model and data cards are added by later tasks."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"api": "ok"}
