"""Experiment runs API (plan §F.5, T3.5): create, control, progress (SSE) and Exp 1V results.

The API only writes ``runs`` and ``jobs``; the worker process executes them. Exp 1 only for now
(Exp 2 and Exp 3 arrive in M5 and M6).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker
from sse_starlette.sse import EventSourceResponse

from gbya.api.deps import get_deps, get_session, get_settings
from gbya.config import Settings
from gbya.errors import NotFound, Unprocessable
from gbya.experiments.runner import (
    Deps,
    create_exp1_run,
    load_spec,
    progress,
    variant_availability,
)
from gbya.experiments.runs import research_eligible
from gbya.gate.config import load_configs
from gbya.retrieval.gold import GoldMap
from gbya.retrieval.metrics import RankedCase, retrieval_report
from gbya.scoring.verifier_eval import CaseLabel, EvalRow, GateRow, verifier_report
from gbya.store.models import (
    Case,
    GateDecisionRow,
    Job,
    JobItem,
    RetrievalRanking,
    Run,
    VerifierEval,
)

router = APIRouter(prefix="/runs", tags=["runs"])
TERMINAL = {"completed", "cancelled", "failed"}


class RunCreate(BaseModel):
    experiment: Literal[1] = 1
    purpose: Literal["research", "development", "fixture", "demo"] = "development"
    case_ids: list[str] | None = Field(default=None, max_length=5000)
    config: dict[str, Any] | None = None  # overrides of experiments/exp1.yaml
    start: bool = True


class RunOut(BaseModel):
    id: int
    experiment: int
    purpose: str
    status: str
    replay: bool
    research_eligible: bool
    config: dict[str, Any]
    config_hash: str
    case_set_hash: str | None
    git_sha: str | None
    model_id: str | None
    model_file_sha256: str | None
    backend: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    progress: dict[str, Any]


def _out(session: Session, run: Run) -> RunOut:
    def iso(t: Any) -> str | None:
        return t.isoformat() if t is not None else None

    return RunOut(
        id=run.id, experiment=run.experiment, purpose=run.purpose, status=run.status,
        replay=run.replay, research_eligible=research_eligible(run), config=run.config,
        config_hash=run.config_hash, case_set_hash=run.case_set_hash, git_sha=run.git_sha,
        model_id=run.model_id, model_file_sha256=run.model_file_sha256, backend=run.backend,
        created_at=str(iso(run.created_at)), started_at=iso(run.started_at),
        finished_at=iso(run.finished_at), progress=progress(session, run.id),
    )  # fmt: skip


def _run(session: Session, run_id: int) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise NotFound(f"No run {run_id}", code="RUN_NOT_FOUND")
    return run


def _active_job(session: Session, run_id: int) -> Job | None:
    return session.scalar(select(Job).where(Job.run_id == run_id,
                                            Job.status.in_(("queued", "running"))))  # fmt: skip


@router.post("", response_model=RunOut, status_code=201)
def create(
    body: RunCreate,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RunOut:
    try:
        run = create_exp1_run(session, settings, purpose=body.purpose, case_ids=body.case_ids,
                              overrides=body.config, start=body.start)  # fmt: skip
    except ValueError as exc:  # spec validation
        raise Unprocessable(str(exc), code="INVALID_RUN_CONFIG") from exc
    session.commit()
    return _out(session, run)


@router.get("/exp1-spec")
def exp1_spec(deps: Annotated[Deps, Depends(get_deps)]) -> dict[str, Any]:
    """The default Exp 1 configuration (``experiments/exp1.yaml``), the verifier variant of each
    composed system and which variants cannot run here, for the run-creation form."""
    spec = load_spec()
    unavailable = {k: v for k, v in variant_availability(deps).items() if v is not None}
    variant_of = {sid: deps.configs[sid].verifier_variant for sid in spec.composed}
    return {"spec": spec.model_dump(), "unavailable": unavailable, "variant_of": variant_of}


@router.get("", response_model=list[RunOut])
def list_runs(session: Annotated[Session, Depends(get_session)]) -> list[RunOut]:
    return [_out(session, r) for r in session.scalars(select(Run).order_by(Run.id.desc()))]


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: int, session: Annotated[Session, Depends(get_session)]) -> RunOut:
    return _out(session, _run(session, run_id))


@router.post("/{run_id}/start", response_model=RunOut)
def start(run_id: int, session: Annotated[Session, Depends(get_session)]) -> RunOut:
    run = _run(session, run_id)
    if run.status in TERMINAL:
        raise Unprocessable(f"Run {run_id} is {run.status}; use resume", code="RUN_FINISHED")
    if _active_job(session, run_id) is None:
        session.add(Job(run_id=run_id, status="queued"))
    session.commit()
    return _out(session, run)


@router.post("/{run_id}/cancel", response_model=RunOut)
def cancel(run_id: int, session: Annotated[Session, Depends(get_session)]) -> RunOut:
    run = _run(session, run_id)
    if run.status in ("completed", "failed"):
        raise Unprocessable(f"Run {run_id} is already {run.status}", code="RUN_FINISHED")
    run.status = "cancelled"
    session.execute(update(Job).where(Job.run_id == run_id, Job.status == "queued")
                    .values(status="cancelled"))  # fmt: skip
    session.commit()
    return _out(session, run)


@router.post("/{run_id}/resume", response_model=RunOut)
def resume(run_id: int, session: Annotated[Session, Depends(get_session)]) -> RunOut:
    """Queue the run again; failed units get a fresh set of attempts."""
    run = _run(session, run_id)
    if run.status == "completed":
        raise Unprocessable(f"Run {run_id} is complete", code="RUN_FINISHED")
    session.execute(update(JobItem).where(JobItem.run_id == run_id, JobItem.status == "error")
                    .values(status="queued", attempts=0))  # fmt: skip
    run.status, run.finished_at = "queued", None
    if _active_job(session, run_id) is None:
        session.add(Job(run_id=run_id, status="queued"))
    session.commit()
    return _out(session, run)


@router.get("/{run_id}/progress")
async def progress_stream(run_id: int, request: Request) -> EventSourceResponse:
    """Server-sent events: one ``progress`` event per second until the run is terminal."""
    factory: sessionmaker[Session] = request.app.state.sessionmaker
    with factory() as s:
        _run(s, run_id)

    async def events() -> AsyncIterator[dict[str, str]]:
        while True:
            with factory() as s:
                p = progress(s, run_id)
            yield {"event": "progress", "data": json.dumps(p)}
            if p["status"] in TERMINAL or await request.is_disconnected():
                return
            await asyncio.sleep(1.0)

    return EventSourceResponse(events())


@router.get("/{run_id}/items")
def items(run_id: int, session: Annotated[Session, Depends(get_session)],
          status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:  # fmt: skip
    _run(session, run_id)
    q = select(JobItem).where(JobItem.run_id == run_id)
    if status:
        q = q.where(JobItem.status == status)
    rows = session.scalars(q.order_by(JobItem.id).limit(min(limit, 2000)))
    return [{"case_id": r.case_id, "system": r.system, "run_idx": r.run_idx, "status": r.status,
             "attempts": r.attempts, "error": r.error} for r in rows]  # fmt: skip


@router.get("/{run_id}/verifier-eval")
def verifier_eval(run_id: int, session: Annotated[Session, Depends(get_session)]) -> dict[str, Any]:
    """Exp 1V diagnostic accuracy by verifier variant and case variant (with denominators), and
    C4 invocation counts per gate configuration (§D.7.2)."""
    run = _run(session, run_id)
    evals = [EvalRow(r.case_id, r.variant, r.run_idx, r.verdict) for r in session.scalars(
        select(VerifierEval).where(VerifierEval.run_id == run_id))]  # fmt: skip
    configs = load_configs()
    gates = []
    for r in session.scalars(select(GateDecisionRow).where(GateDecisionRow.run_id == run_id)):
        if r.system not in configs or "C4" not in configs[r.system].checks:
            continue
        c4 = next((c for c in r.checks if c["check"] == "C4"), None)
        out = (r.verifier or {}).get("output") or {}
        gates.append(GateRow(r.case_id, r.system, r.run_idx, c4 is not None,
                             out.get("verdict") if c4 is not None else None))  # fmt: skip
    ids = {e.case_id for e in evals} | {g.case_id for g in gates}
    labels = {}
    for cid in ids:
        case = session.get(Case, cid)
        if case is not None:
            labels[cid] = CaseLabel(case.variant, (case.labels or {}).get("verifier_label"))
    report = verifier_report(evals, gates, labels)
    return {"run_id": run_id, "purpose": run.purpose, "research_eligible": research_eligible(run),
            "replay": run.replay, "backend": run.backend, **report}  # fmt: skip


@router.get("/{run_id}/retrieval")
def retrieval(
    run_id: int,
    session: Annotated[Session, Depends(get_session)],
    deps: Annotated[Deps, Depends(get_deps)],
) -> dict[str, Any]:
    """Retrieval metrics by mode and case variant, from the stored rankings of the run's cases
    (§D.3): Recall@5, Hit@5, nDCG@5, MRR@20, ATT&CK top-1, mean |G|, exclusions."""
    run = _run(session, run_id)
    s = deps.settings
    index_dir = s.resolve(s.data_dir) / "index"
    if not (index_dir / "MANIFEST.json").is_file():
        raise Unprocessable("No retrieval index", code="NO_INDEX", hint="make index")
    index_sha = str(deps.index().manifest["content_sha256"])
    gold = GoldMap.load(index_dir)
    ranked, missing, rerankers = [], [], set()
    for cid in run.config.get("case_ids", []):
        case = session.get(Case, cid)
        if case is None:
            continue
        labels = case.labels or {}
        for mode in ("bm25", "bm25_rerank"):
            row = session.scalars(select(RetrievalRanking).where(
                RetrievalRanking.case_id == cid, RetrievalRanking.mode == mode,
                RetrievalRanking.index_sha256 == index_sha,
            ).order_by(RetrievalRanking.id.desc())).first()  # fmt: skip
            if row is None:
                missing.append({"case_id": cid, "mode": mode})
                continue
            if row.reranker:
                rerankers.add(row.reranker)
            ranked.append(RankedCase(
                cid, case.variant, labels.get("technique_gold"), mode,
                [h["doc_id"] for h in row.sigma_ranking],
                row.attack_ranking[0]["doc_id"] if row.attack_ranking else None,
            ))  # fmt: skip
    return {
        "run_id": run_id, "purpose": run.purpose, "research_eligible": research_eligible(run),
        "index_sha256": index_sha, "rerankers": sorted(rerankers), "missing": missing,
        "modes": retrieval_report(ranked, gold),
        "note": "Technique tags are proxy relevance labels: technique-level retrieval, not "
        "event-level relevance. Proportions.",
    }  # fmt: skip
