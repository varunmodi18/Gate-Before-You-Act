"""Analysis API (plan §F.5, §D.12, T7.2).

* ``POST /analysis/hypotheses`` — H1, H1-C4 (Exp 1 run) and H2 (Exp 2 run) with research
  verdicts. **Refused (422)** unless both runs are completed research runs on the current frozen
  case set and not replays, and nothing is pending (labels, adjudication). Includes the
  exploratory code-ticket table, labelled as such.
* ``POST /analysis/illustrative`` — estimates and intervals for any run (demo, unfrozen): no
  verdict field exists in its response.
* ``POST /analysis/compare`` — a capability comparison ``a - b`` on one run; 422 when the systems
  have different run counts and ``matched_run`` is missing.
* ``POST /analysis/code-ticket`` — the exploratory code-ticket re-scoring for one Exp 1 run.

Every response carries the runs' provenance (FR-23, NFR-12). Proportions are authoritative;
``*_pp`` fields are display only.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from gbya.analysis import hypotheses as hy
from gbya.api.deps import get_session, get_settings
from gbya.cases.models import Labels
from gbya.config import Settings
from gbya.errors import NotFound, Unprocessable
from gbya.experiments.runs import research_eligible
from gbya.gate.config import load_configs
from gbya.scoring.metrics import MetricRefused
from gbya.scoring.results import exp1_rows
from gbya.store.models import Case, Episode, GateDecisionRow, Run, VerifierEval

router = APIRouter(prefix="/analysis", tags=["analysis"])


class Boot(BaseModel):
    B: int = Field(default=10_000, ge=100, le=100_000)
    seed: int = 2026


class HypothesesBody(Boot):
    run_ids: dict[str, int]  # {"exp1": id, "exp2": id}


class RunBody(Boot):
    run_id: int


class CompareBody(RunBody):
    a: str
    b: str
    metric: str = "task_success"
    matched_run: int | None = Field(default=None, ge=1, le=3)
    variants: list[str] | None = None


def provenance(run: Run) -> dict[str, Any]:
    spec = (run.config or {}).get("spec") or {}
    return {"run_id": run.id, "experiment": run.experiment, "purpose": run.purpose,
            "status": run.status, "replay": run.replay, "git_sha": run.git_sha,
            "config_hash": run.config_hash, "case_set_hash": run.case_set_hash,
            "model_id": run.model_id, "model_file_sha256": run.model_file_sha256,
            "backend": run.backend, "backend_flags": run.backend_flags,
            "schedule": spec.get("schedule"), "research_eligible": research_eligible(run),
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None}  # fmt: skip


def _run(session: Session, run_id: int, experiment: int | None = None) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise NotFound(f"No run {run_id}", code="RUN_NOT_FOUND")
    if experiment is not None and run.experiment != experiment:
        raise Unprocessable(f"Run {run_id} is Exp {run.experiment}, not Exp {experiment}",
                            code="WRONG_EXPERIMENT")  # fmt: skip
    return run


def _research_ready(run: Run, settings: Settings) -> None:
    """NFR-12: research verdicts only from completed, live runs on the current frozen set."""
    if run.replay:
        raise Unprocessable(f"Run {run.id} is a replay", code="REPLAY_RUN")
    if not research_eligible(run):
        raise Unprocessable(f"Run {run.id} is not a research run on the frozen case set "
                            f"(purpose {run.purpose}); use /analysis/illustrative",
                            code="NOT_RESEARCH")  # fmt: skip
    if run.status != "completed":
        raise Unprocessable(f"Run {run.id} is {run.status}", code="RUN_NOT_COMPLETE")
    frozen = settings.resolve(settings.cases_dir) / "FROZEN.json"
    current = json.loads(frozen.read_text()).get("case_set_hash") if frozen.is_file() else None
    if current is None:
        raise Unprocessable("No frozen case set", code="NOT_FROZEN")


def episode_rows(session: Session, run_id: int) -> list[hy.EpisodeLite]:
    eps = list(session.scalars(select(Episode).where(Episode.run_id == run_id,
                                                     Episode.status == "done")))  # fmt: skip
    cases = {c.id: c for c in session.scalars(select(Case).where(
        Case.id.in_({str(e.case_id) for e in eps})))}  # fmt: skip
    out = []
    for e in eps:
        c = cases.get(str(e.case_id))
        hjc = Labels.model_validate((c.labels if c else None) or {}).has_justified_completion
        out.append(
            hy.EpisodeLite(
                str(e.case_id),
                c.scenario_id if c else "",
                c.variant if c else "",
                e.system,
                e.run_idx,
                e.outcome,
                hjc,
            )
        )
    return out


def ticket_rows(session: Session, run_id: int) -> list[hy.TicketRow]:
    variant = load_configs()["G3"].verifier_variant
    evals = {
        (v.case_id, v.run_idx): v
        for v in session.scalars(
            select(VerifierEval).where(
                VerifierEval.run_id == run_id, VerifierEval.variant == variant
            )
        )
    }
    gates = session.scalars(select(GateDecisionRow).where(
        GateDecisionRow.run_id == run_id, GateDecisionRow.system == "G3",
        GateDecisionRow.episode_id.is_(None)))  # fmt: skip
    cases = {c.id: c for c in session.scalars(select(Case))}
    out = []
    for g in gates:
        ev = evals.get((g.case_id, g.run_idx))
        out_json = (ev.output or {}) if ev else {}
        code = out_json.get("ticket_scope_code")
        model = (out_json.get("output") or {}).get("ticket_scope")
        if ev is not None and (ev.manifest or {}).get("ticket_scope_constrained"):
            model = None  # fixed by the schema: not the verifier's judgement
        c = cases.get(g.case_id)
        out.append(hy.TicketRow(
            g.case_id, c.scenario_id if c else "", c.variant if c else "", g.run_idx, g.verdict,
            any(ch["check"] == "C4" for ch in g.checks),
            (c.labels or {}).get("initial_gate_label") if c else None,
            None if code is None else bool(code.get("applies")),
            None if model is None else bool(model.get("applies"))))  # fmt: skip
    return out


def _refusing(fn: Any) -> Any:
    try:
        return fn()
    except MetricRefused as exc:
        raise Unprocessable(str(exc), code="ANALYSIS_PENDING") from exc
    except hy.MatchedRunRequired as exc:
        raise Unprocessable(str(exc), code="MATCHED_RUN_REQUIRED") from exc
    except ValueError as exc:
        raise Unprocessable(str(exc), code="ANALYSIS_INVALID") from exc


@router.post("/hypotheses")
def hypotheses(
    body: HypothesesBody,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    if set(body.run_ids) != {"exp1", "exp2"}:
        raise Unprocessable("run_ids needs exp1 and exp2", code="RUN_IDS_REQUIRED")
    r1, r2 = _run(session, body.run_ids["exp1"], 1), _run(session, body.run_ids["exp2"], 2)
    for run in (r1, r2):
        _research_ready(run, settings)
    rows, eps = exp1_rows(session, r1.id), episode_rows(session, r2.id)
    kw = {"B": body.B, "seed": body.seed}
    return {
        "units": "proportion",
        "H1": _refusing(lambda: hy.h1(rows, **kw).to_json()),
        "H1_C4": _refusing(lambda: hy.h1_c4(rows, **kw).to_json()),
        "H2": _refusing(lambda: hy.h2(eps, **kw).to_json()),
        "exploratory": {"code_ticket": _refusing(
            lambda: hy.code_ticket_rescoring(ticket_rows(session, r1.id), **kw))},
        "bootstrap": {"B": body.B, "seed": body.seed, "unit": "scenario", "ci": "percentile 95%"},
        "provenance": {"exp1": provenance(r1), "exp2": provenance(r2)},
    }  # fmt: skip


@router.post("/illustrative")
def illustrative(body: RunBody,
                 session: Annotated[Session, Depends(get_session)]) -> dict[str, Any]:  # fmt: skip
    run = _run(session, body.run_id)
    rows: list[Any] = exp1_rows(session, run.id) if run.experiment == 1 else episode_rows(
        session, run.id)  # fmt: skip
    s = hy.illustrative_summary(rows, run.experiment, B=body.B, seed=body.seed)
    return {
        **dataclasses.asdict(s),
        "units": "proportion",
        "provenance": provenance(run),
        "note": "Illustrative: estimates and intervals only, never a research verdict.",
    }


@router.post("/compare")
def compare(body: CompareBody,
            session: Annotated[Session, Depends(get_session)]) -> dict[str, Any]:  # fmt: skip
    run = _run(session, body.run_id)
    rows: list[Any] = exp1_rows(session, run.id) if run.experiment == 1 else episode_rows(
        session, run.id)  # fmt: skip
    res = _refusing(lambda: hy.compare(rows, body.a, body.b, body.metric,
                                       matched_run=body.matched_run, variants=body.variants,
                                       B=body.B, seed=body.seed))  # fmt: skip
    return {**res.to_json(), "units": "proportion", "provenance": provenance(run),
            "illustrative": not research_eligible(run)}  # fmt: skip


@router.post("/code-ticket")
def code_ticket(body: RunBody,
                session: Annotated[Session, Depends(get_session)]) -> dict[str, Any]:  # fmt: skip
    run = _run(session, body.run_id, 1)
    out = _refusing(lambda: hy.code_ticket_rescoring(ticket_rows(session, run.id), B=body.B,
                                                     seed=body.seed))  # fmt: skip
    return {**out, "provenance": provenance(run), "illustrative": not research_eligible(run)}


class SensitivityBody(BaseModel):
    run_ids: dict[str, int]  # {"exp1": id, "exp2": id}: an early partial run on dev scenarios


@router.post("/sensitivity")
def sensitivity(body: SensitivityBody,
                session: Annotated[Session, Depends(get_session)]) -> dict[str, Any]:  # fmt: skip
    """Preliminary sensitivity (T7.3): pilot discordance and correlation, and the expected
    half-widths at the full design size. Shown as preliminary sensitivity, never as power."""
    from gbya.analysis.sensitivity import pilot_report

    r1 = _run(session, body.run_ids["exp1"], 1) if "exp1" in body.run_ids else None
    r2 = _run(session, body.run_ids["exp2"], 2) if "exp2" in body.run_ids else None
    rows = exp1_rows(session, r1.id) if r1 else []
    eps = episode_rows(session, r2.id) if r2 else []
    prov = {k: provenance(r) for k, r in (("exp1", r1), ("exp2", r2)) if r is not None}
    return {**pilot_report(rows, eps), "provenance": prov}
