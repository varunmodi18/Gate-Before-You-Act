"""Exports (plan §F.5, T7.5): ``GET /export/{run_id}.json`` and ``GET /export/{run_id}.csv``.

One row per unit of the run, with the run's provenance in the JSON (and as ``#`` comment lines
heading the CSV): Exp 1 — every gate decision (system, run, verdict, first failing check, the
verifier's verdict, the case label, wrongly admitted); Exp 2 / Exp 3 — every episode (system,
run, temperature, seed, terminal state, outcome, flags, tokens, taxonomy tags, starred, whether
the case carries an injection marker). Proportions are not computed here; the rows are the data
the analysis reads.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from gbya.api.deps import get_session
from gbya.api.routers.analysis import provenance
from gbya.errors import NotFound, Unprocessable
from gbya.store.models import Case, Episode, GateDecisionRow, Run, TraceTag

router = APIRouter(prefix="/export", tags=["export"])

EXP1_COLUMNS = ["run_id", "case_id", "scenario_id", "variant", "system", "run_idx", "verdict",
                "failed_check", "verifier_verdict", "label", "wrongly_admitted", "ms",
                "tokens_in", "tokens_out"]  # fmt: skip
EPISODE_COLUMNS = ["run_id", "episode_id", "case_id", "scenario_id", "variant", "system",
                   "run_idx", "temperature", "seed", "status", "terminal_state", "outcome",
                   "rule", "budget_exhausted", "unqualified_escalation", "tokens_in",
                   "tokens_out", "tags", "starred", "injected"]  # fmt: skip


def export_rows(session: Session, run: Run) -> tuple[list[str], list[dict[str, Any]]]:
    cases = {c.id: c for c in session.scalars(select(Case))}
    if run.experiment == 1:
        rows = []
        for g in session.scalars(select(GateDecisionRow).where(
                GateDecisionRow.run_id == run.id).order_by(GateDecisionRow.id)):  # fmt: skip
            c = cases.get(g.case_id)
            label = (c.labels or {}).get("initial_gate_label") if c else None
            rows.append({
                "run_id": run.id, "case_id": g.case_id, "scenario_id": c.scenario_id if c else None,
                "variant": c.variant if c else None, "system": g.system, "run_idx": g.run_idx,
                "verdict": g.verdict,
                "failed_check": next((x["check"] for x in g.checks if not x.get("passed")), None),
                "verifier_verdict": ((g.verifier or {}).get("output") or {}).get("verdict"),
                "label": label,
                "wrongly_admitted": (g.verdict == "admitted") if label == "reject" else None,
                "ms": g.ms, "tokens_in": g.tokens_in, "tokens_out": g.tokens_out,
            })  # fmt: skip
        return EXP1_COLUMNS, rows
    eps = list(session.scalars(select(Episode).where(Episode.run_id == run.id)
                               .order_by(Episode.id)))  # fmt: skip
    tags = {t.episode_id: t for t in session.scalars(
        select(TraceTag).where(TraceTag.episode_id.in_([e.id for e in eps])))}  # fmt: skip
    rows = []
    for e in eps:
        c = cases.get(str(e.case_id))
        t = tags.get(e.id)
        rows.append({
            "run_id": run.id, "episode_id": e.id, "case_id": e.case_id,
            "scenario_id": c.scenario_id if c else None, "variant": c.variant if c else None,
            "system": e.system, "run_idx": e.run_idx, "temperature": e.temperature,
            "seed": e.seed, "status": e.status, "terminal_state": e.terminal_state,
            "outcome": e.outcome,
            "rule": ((e.outcome_detail or {}).get("scoring") or {}).get("rule"),
            "budget_exhausted": e.budget_exhausted,
            "unqualified_escalation": e.unqualified_escalation, "tokens_in": e.tokens_in,
            "tokens_out": e.tokens_out, "tags": ";".join(t.tags) if t else "",
            "starred": bool(t and t.starred), "injected": bool(c and c.injection),
        })  # fmt: skip
    return EPISODE_COLUMNS, rows


def _run(session: Session, run_id: int) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise NotFound(f"No run {run_id}", code="RUN_NOT_FOUND")
    return run


@router.get("/{run_id}.json")
def export_json(run_id: int, session: Annotated[Session, Depends(get_session)]) -> Response:
    run = _run(session, run_id)
    columns, rows = export_rows(session, run)
    body = {"provenance": provenance(run), "columns": columns, "rows": rows, "count": len(rows)}
    return Response(json.dumps(body, default=str), media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="run-{run_id}.json"'})  # fmt: skip


@router.get("/{run_id}.csv")
def export_csv(run_id: int, session: Annotated[Session, Depends(get_session)]) -> Response:
    run = _run(session, run_id)
    if run.experiment not in (1, 2, 3):
        raise Unprocessable(f"Run {run_id} has no exportable rows", code="NOT_EXPORTABLE")
    columns, rows = export_rows(session, run)
    buf = io.StringIO()
    for k, v in provenance(run).items():
        buf.write(f"# {k}: {v}\n")
    w = csv.DictWriter(buf, fieldnames=columns, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="run-{run_id}.csv"'})  # fmt: skip
