"""Gate Playground API (plan §E.1 row 5, §F.5, T2.7): Exp 1 interactively on one case.

Uses ``gbya.experiments.exp1.decide`` — the same gate, the same package handling and no retries —
so the Playground and the experiments cannot diverge (§L.4 item 1). Systems that need the C4
verifier are listed but refused until M3.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from gbya.api.deps import get_session, get_settings
from gbya.config import REPO_ROOT, Settings
from gbya.errors import NotFound, Unprocessable
from gbya.experiments.exp1 import decide, load_case
from gbya.gate.config import GateConfig, describe, load_configs
from gbya.gate.gate import Gate
from gbya.llm.tokens import TokenCounter, default_counter
from gbya.policy.engine import PolicyEngine
from gbya.store.models import Case

router = APIRouter(prefix="/playground", tags=["playground"])


@lru_cache
def _policy() -> PolicyEngine:
    return PolicyEngine.from_file(REPO_ROOT / "policy" / "rules.yaml")


@lru_cache
def _configs() -> dict[str, GateConfig]:
    return load_configs()


def get_counter() -> TokenCounter:
    return default_counter()


class PlaygroundCase(BaseModel):
    id: str
    scenario_id: str
    set: str
    variant: str
    request: dict[str, Any]
    package: dict[str, Any]
    expected_label: str | None


class SystemInfo(BaseModel):
    id: str
    checks: list[str]
    capability: str
    available: bool
    reason: str | None = None


class GateRequest(BaseModel):
    case_id: str
    systems: list[str] = Field(min_length=1, max_length=12)


class DecisionOut(BaseModel):
    config_id: str
    verdict: str
    admitted: bool
    failed_check: str | None
    checks: list[dict[str, Any]]
    verifier: dict[str, Any] | None
    approval: dict[str, Any] | None
    message: str
    ms: float
    correct: bool | None  # against the case's initial_gate_label (admit / reject)


class GateResponse(BaseModel):
    case_id: str
    expected_label: str | None
    decisions: list[DecisionOut]


def _available(cfg: GateConfig) -> tuple[bool, str | None]:
    if "C4" in cfg.checks:
        return False, "needs the C4 verifier (milestone M3)"
    return True, None


@router.get("/systems", response_model=list[SystemInfo])
def systems() -> list[SystemInfo]:
    out = []
    for cfg in _configs().values():
        ok, reason = _available(cfg)
        out.append(SystemInfo(id=cfg.id, checks=describe(cfg), capability=cfg.capability,
                              available=ok, reason=reason))  # fmt: skip
    return out


@router.get("/cases", response_model=list[PlaygroundCase])
def cases(session: Annotated[Session, Depends(get_session)]) -> list[PlaygroundCase]:
    rows = session.scalars(select(Case).where(Case.case_db_path.is_not(None)).order_by(Case.id))
    return [
        PlaygroundCase(
            id=r.id,
            scenario_id=r.scenario_id,
            set=r.set_,
            variant=r.variant,
            request=r.request,
            package=r.package,
            expected_label=(r.labels or {}).get("initial_gate_label"),
        )
        for r in rows
    ]


@router.post("/gate", response_model=GateResponse)
def run_gate(
    body: GateRequest,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    counter: Annotated[TokenCounter, Depends(get_counter)],
) -> GateResponse:
    configs = _configs()
    unknown = [s for s in body.systems if s not in configs]
    if unknown:
        raise Unprocessable(f"Unknown systems: {unknown}", code="UNKNOWN_SYSTEM",
                            details={"known": sorted(configs)})  # fmt: skip
    for sid in body.systems:
        ok, reason = _available(configs[sid])
        if not ok:
            raise Unprocessable(f"{sid} {reason}", code="VERIFIER_UNAVAILABLE",
                                hint="Choose code-only systems: G0, G1, G2, A1")  # fmt: skip
    try:
        case = load_case(session, body.case_id, settings)
    except KeyError as exc:
        raise NotFound(f"No case {body.case_id}", code="CASE_NOT_FOUND") from exc
    except ValueError as exc:
        raise Unprocessable(str(exc), code="CASE_INCOMPLETE") from exc

    decisions = []
    for sid in dict.fromkeys(body.systems):
        d, ms = decide(case, Gate(configs[sid], _policy()), counter)
        correct = None
        if case.expected in ("admit", "reject"):
            correct = d.admitted == (case.expected == "admit")
        decisions.append(
            DecisionOut(
                config_id=d.config_id,
                verdict=d.verdict.value,
                admitted=d.admitted,
                failed_check=d.failed_check,
                checks=[c.model_dump(mode="json") for c in d.checks],
                verifier=d.verifier.model_dump(mode="json") if d.verifier else None,
                approval=d.approval.model_dump(mode="json") if d.approval else None,
                message=d.message,
                ms=ms,
                correct=correct,
            )
        )
    return GateResponse(case_id=case.case_id, expected_label=case.expected, decisions=decisions)
