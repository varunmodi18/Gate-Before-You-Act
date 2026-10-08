"""Blind double annotation (plan §E.1 row 4, T4.7; A-11, A-12).

Roles are chosen locally (A-12); **blindness is enforced here**: an annotator's labels are only
ever returned to that role until both A and B have submitted; then the comparison opens for
everyone (and the adjudicator works from it, T4.8). A submitted annotation is locked.

Each case's workspace shows the standard-variant verifier prompt exactly as C4 receives it (same
records, rendering, retrieval cache and tickets as Exp 1V), the suspicious events with the four
ticket-scope fields, and the policy decision for the package call. Evidence is picked with guarded
SQL on the **case** database (E3-E5 differ from the window).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from gbya.api.deps import get_deps, get_session, get_settings
from gbya.api.routers.windows import QueryRequest, QueryRows, _json_value
from gbya.cases.models import (
    Labels,
    effective_context,
    load_casefile,
    load_scenario,
    scenario_path,
)
from gbya.cases.store import db_path_for
from gbya.cases.validator import CaseView, _command_of, decisive_problems, standard_prompt
from gbya.config import Settings
from gbya.data.connection import open_case_db
from gbya.errors import Conflict, Forbidden, NotFound, Unprocessable
from gbya.experiments.exp1 import load_case, verifier_input
from gbya.experiments.runner import Deps
from gbya.gate.evidence import read_cited, render_cited
from gbya.gate.ticket_scope import acting_user
from gbya.gate.verifier import target_tickets
from gbya.retrieval.index import RetrievalError
from gbya.store.models import Annotation, Case, Scenario, utcnow
from gbya.tools.sql_guard import run_query

router = APIRouter(prefix="/annotate", tags=["annotate"])
Role = Literal["A", "B", "adjudicator"]


def _root(settings: Settings) -> Any:
    return settings.resolve(settings.cases_dir)


def _scenario_row(session: Session, sid: str) -> Scenario:
    row = session.get(Scenario, sid)
    if row is None:
        raise NotFound(
            f"Scenario {sid} is not imported (generate its variants first)",
            code="SCENARIO_NOT_FOUND",
        )
    return row


def _annotation(session: Session, sid: str, role: str) -> Annotation | None:
    return session.scalar(
        select(Annotation).where(Annotation.scenario_id == sid, Annotation.annotator_role == role)
    )


def _both_submitted(session: Session, sid: str) -> bool:
    a, b = _annotation(session, sid, "A"), _annotation(session, sid, "B")
    return bool(a and a.submitted_at and b and b.submitted_at)


def _out(a: Annotation | None) -> dict[str, Any] | None:
    if a is None:
        return None
    return {
        "role": a.annotator_role,
        "labels": a.labels,
        "notes": a.evidence_sets or {},
        "submitted_at": a.submitted_at.isoformat() if a.submitted_at else None,
    }


@router.get("")
def overview(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[dict[str, Any]]:
    """Scenarios with generated cases and where each annotator stands (no labels)."""
    out = []
    for row in session.scalars(select(Scenario).order_by(Scenario.id)):
        n = len(list(session.scalars(select(Case.id).where(Case.scenario_id == row.id))))
        if not n:
            continue
        state = {}
        for role in ("A", "B"):
            a = _annotation(session, row.id, role)
            state[role] = "submitted" if a and a.submitted_at else ("draft" if a else "none")
        out.append({"id": row.id, "status": row.status, "split": row.split, "cases": n, **state})
    return out


def _case_workspace(
    settings: Settings,
    deps: Deps,
    factory: sessionmaker[Session],
    session: Session,
    sid: str,
    variant: str,
) -> dict[str, Any]:
    root = _root(settings)
    sc = load_scenario(scenario_path(root, sid))
    cf = load_casefile(root / sid / "cases" / f"{variant}.json")
    ctx = effective_context(sc.trusted_context, sc.target_host, cf.r_edit)
    db = settings.resolve(db_path_for(settings, cf, sc.window_id))
    case = load_case(session, cf.id, settings)
    reference = True
    try:
        prompt, _, evidence = verifier_input(
            case, deps.verifier("standard", cf.id, factory), deps.counter
        )
    except RetrievalError:  # no index here: the prompt without reference, and say so
        reference = False
        con = open_case_db(db)
        try:
            by_id = read_cited(con, cf.package.cited)
            records = [by_id[i] for i in dict.fromkeys(cf.package.cited) if i in by_id]
        finally:
            con.close()
        prompt = standard_prompt(CaseView(cf, db, ctx), records, deps.counter)
        evidence = render_cited(records, deps.counter)
    con = open_case_db(db)
    try:
        ids = {int(r[0]) for r in con.execute("SELECT record_id FROM raw_events").fetchall()}
        present = [i for i in sc.suspicious_record_ids if i in ids]
        recs = read_cited(con, present)
        suspicious = [
            {
                "record_id": r.record_id,
                "table": r.table,
                "host": r.host,
                "account": acting_user(r),
                "command": _command_of(con, r),
                "ts": r.ts.isoformat() if r.ts else None,
            }
            for r in (recs[i] for i in present if i in recs)
        ]
    finally:
        con.close()
    target = {k: v for k, v in cf.package.args.items()}
    decision = deps.policy.evaluate(cf.package.tool, target, ctx)
    tickets = target_tickets(ctx, {**cf.package.args, "host": sc.target_host})
    return {
        "id": cf.id,
        "variant": cf.variant,
        "request": cf.request.model_dump(mode="json"),
        "package": cf.package.model_dump(mode="json"),
        "prompt": {
            "messages": prompt.messages,
            "manifest": prompt.manifest,
            "reference_available": reference,
        },
        "structured": {str(k): v for k, v in evidence.structured.items()},
        "suspicious": suspicious,
        "tickets": [t.model_dump(mode="json") for t in tickets],
        "policy": decision.model_dump(mode="json"),
        "approval_script": ctx.approval_script.mode,
        "e4_kind": cf.labels.e4_kind,
    }


@router.get("/agreement")
def agreement_report(
    session: Annotated[Session, Depends(get_session)], split: str | None = None
) -> dict[str, Any]:
    """Pooled κ (outcome; candidate actions) and E1 evidence Jaccard over the stored snapshots."""
    from gbya.analysis.agreement import report

    return report(session, split)


@router.get("/{sid}")
def workspace(
    sid: str,
    role: Role,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    deps: Annotated[Deps, Depends(get_deps)],
) -> dict[str, Any]:
    row = _scenario_row(session, sid)
    factory: sessionmaker[Session] = request.app.state.sessionmaker
    variants = [
        c.variant
        for c in session.scalars(select(Case).where(Case.scenario_id == sid).order_by(Case.id))
    ]
    own = _annotation(session, sid, role) if role != "adjudicator" else None
    other = (
        _annotation(session, sid, "B" if role == "A" else "A") if role != "adjudicator" else None
    )
    return {
        "scenario": {
            "id": sid,
            "status": row.status,
            "split": row.split,
            "target_host": row.target_host,
            "window_id": row.window_id,
        },
        "cases": [_case_workspace(settings, deps, factory, session, sid, v) for v in variants],
        "own": _out(own),
        "other_submitted": bool(other and other.submitted_at),
        "both_submitted": _both_submitted(session, sid),
    }


class AnnotationBody(BaseModel):
    labels: dict[str, dict[str, Any]] = Field(default_factory=dict)  # variant → Labels
    notes: dict[str, Any] = Field(default_factory=dict)  # ticket-scope checklist, comments


@router.put("/{sid}/{role}")
def save(
    sid: str,
    role: Literal["A", "B"],
    body: AnnotationBody,
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    row = _scenario_row(session, sid)
    if row.status in ("adjudicated", "frozen"):
        raise Conflict(f"Scenario {sid} is {row.status}", code="SCENARIO_LOCKED")
    variants = {c.variant for c in session.scalars(select(Case).where(Case.scenario_id == sid))}
    errors: list[dict[str, Any]] = []
    clean: dict[str, Any] = {}
    for variant, labels in body.labels.items():
        if variant not in variants:
            errors.append({"case": variant, "msg": "no such case"})
            continue
        try:
            clean[variant] = Labels.model_validate(labels).model_dump(mode="json", by_alias=True)
        except ValidationError as exc:
            errors += [
                {"case": variant, "loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()
            ]
    if errors:
        raise Unprocessable(
            "Labels do not match the schema", code="LABELS_INVALID", details={"errors": errors}
        )
    a = _annotation(session, sid, role)
    if a is not None and a.submitted_at is not None:
        raise Conflict("This annotation is submitted and locked", code="ANNOTATION_LOCKED")
    if a is None:
        a = Annotation(scenario_id=sid, annotator_role=role, labels={})
        session.add(a)
    a.labels = clean
    a.evidence_sets = body.notes
    if row.status == "draft":
        row.status = "annotating"
    session.commit()
    return {"saved": True, "own": _out(a)}


@router.post("/{sid}/{role}/submit")
def submit(
    sid: str,
    role: Literal["A", "B"],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    _scenario_row(session, sid)
    a = _annotation(session, sid, role)
    if a is None:
        raise Unprocessable("Nothing to submit: save labels first", code="ANNOTATION_EMPTY")
    if a.submitted_at is not None:
        raise Conflict("Already submitted", code="ANNOTATION_LOCKED")
    variants = sorted(
        c.variant for c in session.scalars(select(Case).where(Case.scenario_id == sid))
    )
    missing = [v for v in variants if not Labels.model_validate(a.labels.get(v, {})).labelled]
    if missing:
        raise Unprocessable(
            f"Cases without outcome and verifier label: {missing}",
            code="ANNOTATION_INCOMPLETE",
            details={"cases": missing},
        )
    a.submitted_at = utcnow()
    session.commit()
    both = _both_submitted(session, sid)
    if both:
        from gbya.analysis.agreement import store_snapshot

        store_snapshot(session, sid)
        session.commit()
    return {"submitted": True, "both_submitted": both}


@router.get("/{sid}/compare")
def compare(sid: str, session: Annotated[Session, Depends(get_session)]) -> dict[str, Any]:
    """Both annotations and their disagreements — only after both have submitted."""
    _scenario_row(session, sid)
    if not _both_submitted(session, sid):
        raise Forbidden(
            "Annotations stay blind until A and B have both submitted", code="ANNOTATION_BLIND"
        )
    a, b = _annotation(session, sid, "A"), _annotation(session, sid, "B")
    assert a is not None and b is not None
    disagreements = {}
    for variant in sorted(set(a.labels) | set(b.labels)):
        la, lb = a.labels.get(variant, {}), b.labels.get(variant, {})
        fields = sorted(k for k in set(la) | set(lb) if la.get(k) != lb.get(k))
        if fields:
            disagreements[variant] = fields
    return {"A": _out(a), "B": _out(b), "disagreements": disagreements}


class DecisiveCheck(BaseModel):
    entries: list[dict[str, Any]]


@router.post("/{sid}/cases/{variant}/decisive-check")
def decisive_check(
    sid: str,
    variant: str,
    body: DecisiveCheck,
    settings: Annotated[Settings, Depends(get_settings)],
    deps: Annotated[Deps, Depends(get_deps)],
) -> dict[str, Any]:
    """Do these decisive entries hold in the rendered prompt (validator check h)?"""
    root = _root(settings)
    path = root / sid / "cases" / f"{variant}.json"
    if not path.is_file():
        raise NotFound(f"No case {sid}:{variant}", code="CASE_NOT_FOUND")
    try:
        labels = Labels.model_validate({"decisive": body.entries})
    except ValidationError as exc:
        raise Unprocessable(
            "Malformed decisive entry",
            code="DECISIVE_INVALID",
            details={"errors": [e["msg"] for e in exc.errors()]},
        ) from exc
    sc = load_scenario(scenario_path(root, sid))
    cf = load_casefile(path)
    view = CaseView(
        cf,
        settings.resolve(db_path_for(settings, cf, sc.window_id)),
        effective_context(sc.trusted_context, sc.target_host, cf.r_edit),
    )
    con = open_case_db(view.db)
    try:
        ids = {int(r[0]) for r in con.execute("SELECT record_id FROM raw_events").fetchall()}
        by_id = read_cited(con, cf.package.cited)
        records = [by_id[i] for i in dict.fromkeys(cf.package.cited) if i in by_id]
    finally:
        con.close()
    problems = decisive_problems(view, records, ids, list(labels.decisive), deps.counter)
    return {"ok": not problems, "problems": problems}


@router.post("/{sid}/cases/{variant}/query", response_model=QueryRows)
def case_query(
    sid: str,
    variant: str,
    body: QueryRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> QueryRows:
    """Guarded SQL on the case's own database (evidence picker)."""
    root = _root(settings)
    path = root / sid / "cases" / f"{variant}.json"
    if not path.is_file():
        raise NotFound(f"No case {sid}:{variant}", code="CASE_NOT_FOUND")
    sc = load_scenario(scenario_path(root, sid))
    con = open_case_db(settings.resolve(db_path_for(settings, load_casefile(path), sc.window_id)))
    try:
        guarded, result = run_query(con, body.sql)
    finally:
        con.close()
    return QueryRows(
        columns=result.columns,
        sql=guarded.wrapped_sql,
        rows=[[_json_value(v) for v in r] for r in result.rows],
    )


class AdjudicationBody(BaseModel):
    labels: dict[str, dict[str, Any]]  # variant → final labels
    by: str = Field(default="adjudicator", min_length=1)


@router.post("/{sid}/adjudicate")
def adjudicate(
    sid: str,
    body: AdjudicationBody,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    deps: Annotated[Deps, Depends(get_deps)],
) -> dict[str, Any]:
    """Final labels for every case: recorded in ``adjudications``, written back to the case
    files and app.db, then the scenario is validated. Only after both annotators submitted."""
    from gbya.cases.store import import_scenario, write_case
    from gbya.cases.validator import validate_scenario
    from gbya.store.models import Adjudication

    row = _scenario_row(session, sid)
    if row.status == "frozen":
        raise Conflict(f"Scenario {sid} is frozen", code="SCENARIO_LOCKED")
    if not _both_submitted(session, sid):
        raise Forbidden(
            "Adjudication opens when A and B have both submitted", code="ANNOTATION_BLIND"
        )
    root = _root(settings)
    variants = sorted(
        c.variant for c in session.scalars(select(Case).where(Case.scenario_id == sid))
    )
    missing = [v for v in variants if v not in body.labels]
    if missing:
        raise Unprocessable(f"Final labels missing for {missing}", code="ADJUDICATION_INCOMPLETE")
    final: dict[str, Labels] = {}
    errors: list[dict[str, Any]] = []
    for v in variants:
        try:
            final[v] = Labels.model_validate(body.labels[v])
        except ValidationError as exc:
            errors += [{"case": v, "loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        else:
            if not final[v].labelled:
                errors.append({"case": v, "msg": "outcome and verifier label are required"})
    if errors:
        raise Unprocessable(
            "Final labels do not match the schema",
            code="LABELS_INVALID",
            details={"errors": errors},
        )
    for v in variants:
        cf = load_casefile(root / sid / "cases" / f"{v}.json")
        if (
            final[v].e4_kind is None and cf.labels.e4_kind is not None
        ):  # structural, from the builder
            final[v] = final[v].model_copy(update={"e4_kind": cf.labels.e4_kind})
        write_case(settings, cf.model_copy(update={"labels": final[v]}))
        session.add(
            Adjudication(
                kind="scenario_label",
                ref_id=cf.id,
                by=body.by,
                decision=final[v].model_dump(mode="json", by_alias=True),
            )
        )
    import_scenario(session, settings, sid)
    row.status = "adjudicated"
    session.commit()
    sc_window = row.window_id
    meta = {"split": row.split, "window_id": sc_window}
    report = validate_scenario(settings, sid, deps.counter, window_meta=meta)
    return {"adjudicated": True, "validation": {**report.to_json(), "tokenizer": deps.counter.name}}
