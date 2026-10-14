"""Readiness endpoint (plan §F.5): API, worker, model and data, for the Home cards (§E.5 step 1).

``GET /health`` → ``{api, worker:{alive, last_heartbeat}, model:{status, backend, model_id,
last_pilot:{in_tps, out_tps, peak_vram_mb}}, data:{windows, index}}``. The worker is alive when
its heartbeat file (written on every poll) is younger than ``worker_stale_s``; the live model is
"ok" when its server answers ``/models`` within a second; the last pilot is the default
profile's report in ``docs/pilot/``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gbya.api.deps import get_session, get_settings
from gbya.config import REPO_ROOT, Settings
from gbya.store.models import Window

router = APIRouter(tags=["health"])


def _worker(settings: Settings) -> dict[str, Any]:
    from gbya.worker import heartbeat_file

    path = heartbeat_file(settings)
    try:
        beat = json.loads(path.read_text())
        at = datetime.fromisoformat(beat["at"])
    except (OSError, ValueError, KeyError):
        return {"alive": False, "last_heartbeat": None}
    at = at if at.tzinfo else at.replace(tzinfo=UTC)
    age = (datetime.now(UTC) - at).total_seconds()
    return {"alive": age <= settings.worker_stale_s, "last_heartbeat": at.isoformat()}


def _model(settings: Settings) -> dict[str, Any]:
    import yaml

    from gbya.llm.factory import _profile

    cfg = yaml.safe_load((REPO_ROOT / "config" / "model_profiles.yaml").read_text())
    prof = _profile()
    out: dict[str, Any] = {"backend": settings.llm_backend if settings.llm_backend != "live"
                           else cfg["default"],
                           "model_id": f"{prof['model_repo']}@{prof['model_revision']}",
                           "last_pilot": None}  # fmt: skip
    out["recording"] = str(settings.record_cassette) if settings.record_cassette else None
    if settings.llm_backend == "live":
        try:
            r = httpx.get(f"{settings.model_base_url}/models", timeout=1.0)
            out["status"] = "ok" if r.status_code == 200 else f"http {r.status_code}"
        except httpx.HTTPError:
            out["status"] = "unreachable"
    else:
        out["status"] = settings.llm_backend  # fake or replay: never research
    report = REPO_ROOT / "docs" / "pilot" / f"{cfg['default']}.json"
    if report.is_file():
        data = json.loads(report.read_text())
        ep = data.get("episodes_20") or {}
        out["last_pilot"] = {"in_tps": ep.get("input_tok_per_s"),
                             "out_tps": ep.get("output_tok_per_s"),
                             "peak_vram_mb": data.get("peak_vram_mb_overall")}  # fmt: skip
    return out


@router.get("/health")
def health(settings: Annotated[Settings, Depends(get_settings)],
           session: Annotated[Session, Depends(get_session)]) -> dict[str, Any]:  # fmt: skip
    windows = int(session.scalar(select(func.count()).select_from(Window)) or 0)
    index = (settings.resolve(settings.data_dir) / "index" / "MANIFEST.json").is_file()
    return {
        "api": "ok",
        "worker": _worker(settings),
        "model": _model(settings),
        "data": {"windows": windows, "index": index},
        "case_set": _case_set(settings),
    }


def _case_set(settings: Settings) -> dict[str, Any]:
    """The frozen case set, if any (header: "Case set: frozen cases-v1" or "draft")."""
    path = settings.resolve(settings.cases_dir) / "FROZEN.json"
    try:
        m = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"frozen": False, "version": None, "case_set_hash": None}
    return {"frozen": True, "version": m.get("version"), "case_set_hash": m.get("case_set_hash")}
