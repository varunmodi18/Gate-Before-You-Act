"""Scenario Studio API (plan §E.1 row 3, §F.5; T4.4 validate, T4.5 studio).

The API reads and writes scenario and case **files**; it never builds or patches a log database.
Variant generation runs the construction-path command line as a short-lived subprocess (§D.1.1).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from gbya.api.deps import get_deps, get_session, get_settings
from gbya.cases.models import scenario_path
from gbya.cases.validator import validate_scenario
from gbya.config import Settings
from gbya.errors import NotFound
from gbya.experiments.runner import Deps
from gbya.store.models import Window

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


def _require(settings: Settings, sid: str) -> None:
    if not scenario_path(settings.resolve(settings.cases_dir), sid).is_file():
        raise NotFound(f"No scenario {sid}", code="SCENARIO_NOT_FOUND")


@router.post("/{sid}/validate")
def validate(
    sid: str,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    deps: Annotated[Deps, Depends(get_deps)],
) -> dict[str, Any]:
    """Checks a-i and Set R (§D.11); label checks are ``pending`` until annotation."""
    _require(settings, sid)
    from gbya.cases.models import load_scenario

    sc = load_scenario(scenario_path(settings.resolve(settings.cases_dir), sid))
    window = session.get(Window, sc.window_id)
    meta = {"techniques": window.techniques, "tactics": window.tactics,
            "split": window.split} if window else {}  # fmt: skip
    report = validate_scenario(settings, sid, deps.counter, window_meta=meta)
    return {**report.to_json(), "tokenizer": deps.counter.name}
