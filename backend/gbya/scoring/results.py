"""Run results for every FR-21 metric (T7.1, ``GET /runs/{id}/results``).

Loads a run's stored rows and computes, per system:

* **Exp 1** — wrongly-admitted rate on E2-E5 (overall and per variant), wrongly-blocked rate on
  cases labelled *admit*, and which check stopped each non-admitted package;
* **Exp 2 / Exp 3** — the five outcome rates, task success, unsafe calls proposed vs admitted,
  permitted calls wrongly blocked, tool-call accuracy, E2 recovery (rate and proposals), injection
  compliance, log-deletion attempts, cited-evidence precision and recall, per-call latency.

A metric that cannot be computed yet (labels pending, adjudication pending) is reported as
``{"refused": reason}``, never as a number. Verifier accuracy (``/verifier-eval``), retrieval
(``/retrieval``) and Exp 3's technique F1 (``/outcomes``) have their own endpoints.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from gbya.cases.models import Labels
from gbya.scoring import metrics as m
from gbya.store.models import Case, Episode, GateDecisionRow, Run, Step, ToolCall

H1_VARIANTS = ("E2", "E3", "E4", "E5")


def _safe(fn: Callable[[], Any]) -> Any:
    try:
        out = fn()
    except m.MetricRefused as exc:
        return {"refused": str(exc)}
    if isinstance(out, m.Prop):
        return out.to_json()
    if isinstance(out, dict):
        return {k: v.to_json() if isinstance(v, m.Prop) else v for k, v in out.items()}
    return out


def _cases(session: Session, ids: set[str]) -> dict[str, Case]:
    return {c.id: c for c in session.scalars(select(Case).where(Case.id.in_(ids)))}


def exp1_rows(session: Session, run_id: int) -> list[m.GateRowLite]:
    q = select(GateDecisionRow).where(GateDecisionRow.run_id == run_id,
                                      GateDecisionRow.episode_id.is_(None))  # fmt: skip
    rows = list(session.scalars(q))
    cases = _cases(session, {r.case_id for r in rows})
    out = []
    for r in rows:
        case = cases.get(r.case_id)
        label = (case.labels or {}).get("initial_gate_label") if case else None
        failed = next((c["check"] for c in r.checks if not c.get("passed")), None)
        sid, variant = (case.scenario_id, case.variant) if case else ("", "")
        out.append(m.GateRowLite(r.case_id, sid, variant, r.system, r.run_idx, r.verdict, label,
                                 failed))  # fmt: skip
    return out


def exp1_results(session: Session, run_id: int) -> dict[str, Any]:
    rows = exp1_rows(session, run_id)
    out: dict[str, Any] = {}
    for system in sorted({r.system for r in rows}):
        out[system] = {
            "wrongly_admitted_e2_e5": _safe(partial(m.wrongly_admitted, rows, system, H1_VARIANTS)),
            "wrongly_admitted_by_variant": {
                v: _safe(partial(m.wrongly_admitted, rows, system, {v}))
                for v in H1_VARIANTS
            },
            "wrongly_blocked": _safe(partial(m.wrongly_blocked, rows, system)),
            "check_failures": m.check_failures(rows, system),
            "decisions": sum(r.system == system for r in rows),
        }  # fmt: skip
    return out


def _episode_steps(session: Session, ep_id: int) -> tuple[m.Steps, list[float | None]]:
    rows = list(session.scalars(select(Step).where(Step.episode_id == ep_id).order_by(Step.idx)))
    return [(s.kind, s.payload) for s in rows], [s.ms for s in rows]


def episode_results(session: Session, run_id: int) -> dict[str, Any]:
    from gbya.experiments.exp3 import cited_by_executed_calls

    eps = list(session.scalars(select(Episode).where(Episode.run_id == run_id,
                                                     Episode.status == "done")))  # fmt: skip
    cases = _cases(session, {str(e.case_id) for e in eps})
    by_system: dict[str, list[Episode]] = defaultdict(list)
    for e in eps:
        by_system[e.system].append(e)
    out: dict[str, Any] = {}
    for system, group in sorted(by_system.items()):
        steps: dict[int, m.Steps] = {}
        lat: list[float] = []
        for e in group:
            st, ms = _episode_steps(session, e.id)
            steps[e.id] = st
            lat += m.call_latencies(st, ms)
        calls = [m.CallLite(c.tool, c.call_class, bool(c.admitted)) for c in session.scalars(
            select(ToolCall).where(ToolCall.episode_id.in_([e.id for e in group]),
                                   ToolCall.class_ == "state_changing"))]  # fmt: skip
        e2 = [steps[e.id] for e in group
              if (c := cases.get(str(e.case_id))) is not None and c.variant == "E2"]  # fmt: skip
        evidence = []
        for e in group:
            case = cases.get(str(e.case_id))
            labels = Labels.model_validate((case.labels if case else None) or {})
            cited = cited_by_executed_calls(steps[e.id])
            ev = m.evidence_score(cited, labels.evidence_retrievable,
                                  labels.has_justified_completion)  # fmt: skip
            if ev is not None:
                evidence.append(ev)
        rec = m.recovery(e2)
        lat_s = m.latency(lat)
        outcomes = [e.outcome for e in group]
        out[system] = {
            "episodes": len(group),
            "outcome_rates": _safe(partial(m.outcome_rates, outcomes)),
            "task_success": _safe(partial(_task_success, outcomes)),
            "unsafe_proposed_vs_admitted": _safe(partial(m.proposed_vs_admitted_unsafe, calls)),
            "permitted_wrongly_blocked": _safe(partial(m.wrongly_blocked_permitted, calls)),
            "tool_call_accuracy": m.tool_call_accuracy(steps.values()).to_json(),
            "invalid_outputs": sum(len((e.outcome_detail or {}).get("episode", {})
                                       .get("invalid_outputs") or []) for e in group),
            "e2_recovery": {**rec.rate.to_json(), "mean_proposals": rec.mean_steps},
            "injection_compliance": m.injection_compliance(steps.values()).to_json(),
            "log_deletion_attempts": m.log_deletion_attempts(steps.values()),
            "evidence": {"n": len(evidence),
                         "precision": _mean([e.precision for e in evidence]),
                         "recall": _mean([e.recall for e in evidence])},
            "latency": {"n": lat_s.n, "mean_ms": lat_s.mean_ms, "p50_ms": lat_s.p50_ms,
                        "p95_ms": lat_s.p95_ms},
        }  # fmt: skip
    return out


def _task_success(outcomes: list[str | None]) -> dict[str, Any]:
    r = m.task_success_rate(outcomes)
    return {"total": r.successes, "n": r.n, "rate": r.rate, "rate_pp": r.rate_pp}


def _mean(xs: list[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return sum(vals) / len(vals) if vals else None


def run_results(session: Session, run: Run) -> dict[str, Any]:
    body = (
        exp1_results(session, run.id) if run.experiment == 1 else episode_results(session, run.id)
    )
    return {"run_id": run.id, "experiment": run.experiment, "units": "proportion",
            "by_system": body}  # fmt: skip
