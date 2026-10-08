"""Gate Playground API (plan §E.1 row 5, §F.5, T2.7, T3.6): Exp 1 interactively on one case.

Uses ``gbya.experiments.exp1.decide`` — the same gate, the same package handling and no retries —
so the Playground and the experiments cannot diverge (§L.4 item 1). The nine Exp 1
configurations are offered; those with C4 call the verifier inline (live model, or the fake or
replay backend per ``llm_backend``) and are unavailable when their retrieval cannot run.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from gbya.api.deps import get_deps, get_session, get_settings
from gbya.config import Settings
from gbya.errors import NotFound, Unprocessable
from gbya.experiments.exp1 import decide, load_case
from gbya.experiments.runner import Deps, load_spec, variant_availability
from gbya.gate.config import GateConfig, describe
from gbya.gate.gate import Gate
from gbya.gate.types import VerifierCall
from gbya.llm.tokens import TokenCounter, default_counter
from gbya.retrieval.gold import GoldMap
from gbya.store.models import Case

router = APIRouter(prefix="/playground", tags=["playground"])


def get_counter() -> TokenCounter:
    return default_counter()


@lru_cache(maxsize=2)
def _gold_map(index_dir: Path) -> GoldMap | None:
    return GoldMap.load(index_dir) if (index_dir / "gold_map.json").is_file() else None


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
    verifier_variant: str | None
    retrieval_mode: str | None
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
    verifier_call: dict[str, Any] | None  # output, prompt, manifest and retrieved references
    approval: dict[str, Any] | None
    message: str
    ms: float
    correct: bool | None  # against the case's initial_gate_label (admit / reject)


class GateResponse(BaseModel):
    case_id: str
    expected_label: str | None
    verifier_label: str | None
    decisions: list[DecisionOut]


def _exp1_configs(deps: Deps) -> dict[str, GateConfig]:
    spec = load_spec()
    wanted = set(spec.code_only) | set(spec.composed)
    return {cid: c for cid, c in deps.configs.items() if cid in wanted}


def _reason(cfg: GateConfig, deps: Deps) -> str | None:
    if cfg.verifier_variant is None:
        return None
    return variant_availability(deps)[cfg.verifier_variant]


@router.get("/systems", response_model=list[SystemInfo])
def systems(deps: Annotated[Deps, Depends(get_deps)]) -> list[SystemInfo]:
    out = []
    for cfg in _exp1_configs(deps).values():
        reason = _reason(cfg, deps)
        out.append(SystemInfo(id=cfg.id, checks=describe(cfg), capability=cfg.capability,
                              verifier_variant=cfg.verifier_variant,
                              retrieval_mode=cfg.retrieval_mode,
                              available=reason is None, reason=reason))  # fmt: skip
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


def _references(call: VerifierCall, deps: Deps, technique_gold: str | None) -> list[dict[str, Any]]:
    """Every ranked candidate with display metadata (title, author, link — DRL 1.1 attribution)
    and, for the UI only, whether it is in the case's gold set. Never sent to the verifier."""
    if call.retrieval is None:
        return []
    index = deps.index()
    s = deps.settings
    gold = _gold_map(s.resolve(s.data_dir) / "index")
    gold_rules = gold.gold_rules(technique_gold) if gold and technique_gold else set()
    out = []
    for kind, key, shown in (("sigma", "sigma_ranking", 5), ("attack", "attack_ranking", 1)):
        for hit in call.retrieval[key]:
            doc = index.doc(hit["doc_id"])
            if kind == "sigma":
                is_gold = hit["doc_id"] in gold_rules
            else:
                is_gold = bool(gold and technique_gold
                               and gold.attack_correct(hit["doc_id"], technique_gold))  # fmt: skip
            out.append({**hit, "kind": kind, "title": doc.title, "author": doc.meta.get("author"),
                        "uri": doc.meta.get("uri"), "technique_id": doc.meta.get("technique_id"),
                        "shown": hit["rank"] <= shown, "gold": is_gold})  # fmt: skip
    return out


@router.post("/gate", response_model=GateResponse)
def run_gate(
    body: GateRequest,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    counter: Annotated[TokenCounter, Depends(get_counter)],
    deps: Annotated[Deps, Depends(get_deps)],
) -> GateResponse:
    configs = _exp1_configs(deps)
    unknown = [s for s in body.systems if s not in configs]
    if unknown:
        raise Unprocessable(f"Unknown systems: {unknown}", code="UNKNOWN_SYSTEM",
                            details={"known": sorted(configs)})  # fmt: skip
    for sid in body.systems:
        reason = _reason(configs[sid], deps)
        if reason is not None:
            raise Unprocessable(f"{sid} {reason}", code="VERIFIER_UNAVAILABLE",
                                hint="Choose systems whose verifier can run here")  # fmt: skip
    try:
        case = load_case(session, body.case_id, settings)
    except KeyError as exc:
        raise NotFound(f"No case {body.case_id}", code="CASE_NOT_FOUND") from exc
    except ValueError as exc:
        raise Unprocessable(str(exc), code="CASE_INCOMPLETE") from exc
    labels = (row.labels if (row := session.get(Case, body.case_id)) else None) or {}

    decisions = []
    for sid in dict.fromkeys(body.systems):
        cfg = configs[sid]
        verifier = deps.verifier(cfg.verifier_variant) if cfg.verifier_variant else None
        d, ms = decide(case, Gate(cfg, deps.policy, verifier), counter)
        correct = None
        if case.expected in ("admit", "reject"):
            correct = d.admitted == (case.expected == "admit")
        vc = None
        if d.verifier_call is not None:
            vc = {**d.verifier_call.model_dump(mode="json", exclude={"retrieval"}),
                  "references": _references(d.verifier_call, deps,
                                            labels.get("technique_gold"))}  # fmt: skip
        decisions.append(
            DecisionOut(
                config_id=d.config_id,
                verdict=d.verdict.value,
                admitted=d.admitted,
                failed_check=d.failed_check,
                checks=[c.model_dump(mode="json") for c in d.checks],
                verifier=d.verifier.model_dump(mode="json") if d.verifier else None,
                verifier_call=vc,
                approval=d.approval.model_dump(mode="json") if d.approval else None,
                message=d.message,
                ms=ms,
                correct=correct,
            )
        )
    return GateResponse(case_id=case.case_id, expected_label=case.expected,
                        verifier_label=labels.get("verifier_label"),
                        decisions=decisions)  # fmt: skip
