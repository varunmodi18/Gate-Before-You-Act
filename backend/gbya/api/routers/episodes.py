"""Agent Console and Traces API (plan §E.1 rows 6 and 10, §F.5, T5.6).

``POST /episodes`` starts one Exp 2-style episode on a case with a frozen prefix, in a thread of
the API process; its steps are committed as they happen (``SqlTrace``), and
``GET /episodes/{id}/stream`` relays them as server-sent events: ``step`` (proposal, tool result,
approval, feedback), ``gate`` (gate decision), ``approval_request`` (demo-interactive mode, a
human must answer), ``outcome`` and ``error``.

On an end-to-end case (set X, T6.3) the Console runs the Exp 3 episode instead: the live
investigation of the window (its steps carry ``phase: investigation``) and then the response from
its handover, with the same investigator, gate, tools and loop as Exp 3 (in-process transport).

Console episodes have no run (``run_id`` NULL), so they never enter an experiment. In
**demo-interactive** mode a human answers approval requests (``POST /episodes/{id}/approval``)
instead of the scripted approver; without an answer within ``APPROVAL_TIMEOUT_S`` the request gets
no response. The same gate, tools and episode loop run as in Exp 2.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from sse_starlette.sse import EventSourceResponse

from gbya.agent.episode import EpisodeRunner, EpisodeStopped, SqlTrace, prefix_text
from gbya.api.deps import get_deps, get_session, get_settings
from gbya.cases.models import Labels
from gbya.config import Settings
from gbya.errors import Conflict, NotFound, Unprocessable
from gbya.experiments import exp2, exp3
from gbya.experiments.runner import Deps, variant_availability
from gbya.logging import get_logger
from gbya.scoring import outcomes
from gbya.store.db import session_scope
from gbya.store.models import (
    ERROR_TAGS,
    Case,
    Episode,
    GateDecisionRow,
    Scenario,
    Step,
    ToolCall,
    TraceTag,
)
from gbya.store.models import utcnow as _now
from gbya.tools.dispatcher import InProcessDispatcher

router = APIRouter(prefix="/episodes", tags=["episodes"])
log = get_logger("gbya.api.episodes")
APPROVAL_TIMEOUT_S = 300.0


@dataclass
class _Live:
    """A Console episode running in this process."""

    stop: threading.Event = field(default_factory=threading.Event)
    pending: dict[str, str] | None = None  # {"call_key", "call"} while a human must answer
    answer: str | None = None
    answered: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def ask(self, key: str, description: str) -> str:
        with self.lock:
            self.pending, self.answer = {"call_key": key, "call": description}, None
            self.answered.clear()
        deadline = APPROVAL_TIMEOUT_S
        while deadline > 0 and not self.answered.wait(0.2) and not self.stop.is_set():
            deadline -= 0.2
        with self.lock:
            answer, self.pending = self.answer, None
        return answer or "unreachable"


def _live(request: Request) -> dict[int, _Live]:
    live: dict[int, _Live] | None = getattr(request.app.state, "console", None)
    if live is None:
        live = {}
        request.app.state.console = live
    return live


class EpisodeStart(BaseModel):
    case_id: str
    system: str
    run_idx: int = Field(default=1, ge=1, le=3)
    interactive_approval: bool = False


class ApprovalAnswer(BaseModel):
    decision: Literal["grant", "deny"]


class Tags(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=len(ERROR_TAGS))
    starred: bool = False
    note: str | None = Field(default=None, max_length=2000)


def _system_reason(deps: Deps, sid: str) -> str | None:
    config = deps.configs.get(sid)
    if config is None:
        return "is not defined"
    if config.verifier_variant is not None:
        return variant_availability(deps).get(config.verifier_variant)
    return None


@router.get("/systems")
def systems(deps: Annotated[Deps, Depends(get_deps)]) -> list[dict[str, Any]]:
    """Every gate configuration, with why it cannot run here (None = it can)."""
    return [{"id": sid, "checks": list(c.checks), "recovery_budget": c.recovery_budget,
             "verifier_variant": c.verifier_variant, "unavailable": _system_reason(deps, sid)}
            for sid, c in deps.configs.items()]  # fmt: skip


@router.get("/cases")
def cases(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[dict[str, Any]]:
    """Cases the Console can run: those with a generated prefix (``mode: case``) and the
    end-to-end cases, whose window is investigated live (``mode: window``)."""
    out = []
    for row in session.scalars(select(Case).where(Case.case_db_path.is_not(None))
                               .order_by(Case.id)):  # fmt: skip
        window = row.set_ == "X"
        if window or exp2.prefix_file(settings, row.id).is_file():
            sc = session.get(Scenario, row.scenario_id) if window else None
            out.append(
                {
                    "case_id": row.id,
                    "variant": row.variant,
                    "request": row.request,
                    "labelled": exp2.scorable(Labels.model_validate(row.labels or {})),
                    "mode": "window" if window else "case",
                    "window_id": sc.window_id if sc else None,
                }
            )
    return out


@router.get("/schedule")
def schedule() -> dict[str, Any]:
    spec = exp2.load_spec()
    return {str(k): v.model_dump() for k, v in spec.schedule.items()}


@router.post("", status_code=201)
def start(
    body: EpisodeStart,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    deps: Annotated[Deps, Depends(get_deps)],
) -> dict[str, Any]:
    reason = _system_reason(deps, body.system)
    if reason is not None:
        raise Unprocessable(f"{body.system} {reason}", code="SYSTEM_UNAVAILABLE")
    if body.interactive_approval and deps.configs[body.system].transport != "in_process":
        raise Unprocessable(
            f"{body.system} runs its tools in an MCP server process; demo-interactive approval "
            "needs the in-process transport",
            code="INTERACTIVE_NEEDS_IN_PROCESS",
        )
    row = session.get(Case, body.case_id)
    if row is None:
        raise NotFound(f"No case {body.case_id}", code="CASE_NOT_FOUND")
    case: exp2.Exp2Case | exp3.Exp3Case
    spec: exp2.Exp2Spec
    if row.set_ == "X":
        if deps.configs[body.system].transport != "in_process":
            raise Unprocessable(f"{body.system}: Exp 3 episodes run in-process",
                                code="TRANSPORT_UNSUPPORTED")  # fmt: skip
        case, spec = exp3.load_e3_case(session, body.case_id, settings), exp3.load_spec()
    else:
        try:
            case = exp2.load_episode_case(session, body.case_id, settings)
        except ValueError as exc:
            raise Unprocessable(str(exc), code="NO_PREFIX") from exc
        spec = exp2.load_spec()
    if body.run_idx not in spec.schedule:
        raise Unprocessable(f"No run {body.run_idx} in the schedule", code="UNKNOWN_RUN")
    slot = spec.schedule[body.run_idx]
    ep = Episode(
        case_id=body.case_id,
        system=body.system,
        run_idx=body.run_idx,
        temperature=slot.temperature,
        seed=slot.seed,
        status="running",
        outcome_detail={"console": {"interactive_approval": body.interactive_approval}},
    )
    session.add(ep)
    session.commit()
    live = _Live()
    _live(request)[ep.id] = live
    factory: sessionmaker[Session] = request.app.state.sessionmaker
    thread = threading.Thread(
        target=_run, args=(factory, deps, case, body, slot, ep.id, live), daemon=True,
        name=f"console-episode-{ep.id}",
    )  # fmt: skip
    thread.start()
    return {"episode_id": ep.id}


def _run(factory: sessionmaker[Session], deps: Deps, case: exp2.Exp2Case | exp3.Exp3Case,
         body: EpisodeStart, slot: exp2.Slot, episode_id: int, live: _Live) -> None:  # fmt: skip
    config = deps.configs[body.system]
    session = factory()
    try:
        ep = session.get(Episode, episode_id)
        assert ep is not None
        # "done" becomes visible (stream, polling) only together with the score
        trace = SqlTrace(session, ep, defer_finish=True)
        if isinstance(case, exp3.Exp3Case):
            _, inv = exp3.run_episode(
                case, config, slot, deps, factory, trace=trace, should_stop=live.stop.is_set,
                approver=live.ask if body.interactive_approval else None,
            )  # fmt: skip
            steps = session.execute(select(Step.kind, Step.payload)
                                    .where(Step.episode_id == episode_id)
                                    .order_by(Step.idx)).all()  # fmt: skip
            ep.outcome_detail = {
                **(ep.outcome_detail or {}),
                "console": {"interactive_approval": body.interactive_approval},
                "transport": config.transport,
            }
            exp3.record_investigation(session, ep, inv, case,
                                      [(k, dict(p)) for k, p in steps])  # fmt: skip
            session.commit()
            return
        if config.transport == "in_process":  # demo-interactive approval needs this process
            approver = live.ask if body.interactive_approval else None
            e = exp2.episode_env(case, config, deps, factory, approver=approver)
            dispatcher: Any = InProcessDispatcher(e.gate, e.env, e.tools, trace)
            close = e.close
        else:
            dispatcher, close = exp2.make_dispatcher(case, config, deps, trace, factory)
        try:
            EpisodeRunner(
                client=deps.client, dispatcher=dispatcher, request=case.request,
                prefix=prefix_text(case.prefix), temperature=slot.temperature, seed=slot.seed,
                trace=trace, counter=deps.counter, should_stop=live.stop.is_set,
            ).run()  # fmt: skip
        finally:
            close()
        ep.outcome_detail = {
            **(ep.outcome_detail or {}),
            "console": {"interactive_approval": body.interactive_approval},
            "transport": config.transport,
        }
        if exp2.scorable(case.labels):
            outcomes.score_episode(session, episode_id, case.labels)
        session.commit()
    except EpisodeStopped as exc:
        _fail(session, episode_id, "stopped", str(exc))
    except Exception as exc:  # recorded on the episode and streamed as an error
        log.warning("console_episode_error", episode=episode_id, error=str(exc))
        _fail(session, episode_id, "error", f"{type(exc).__name__}: {exc}")
    finally:
        session.close()


def _fail(session: Session, episode_id: int, kind: str, message: str) -> None:
    session.rollback()
    ep = session.get(Episode, episode_id)
    if ep is not None:
        ep.status = "error"
        ep.outcome_detail = {**(ep.outcome_detail or {}), kind: message}
        session.commit()


@router.post("/{episode_id}/approval")
def approve(episode_id: int, body: ApprovalAnswer, request: Request) -> dict[str, Any]:
    live = _live(request).get(episode_id)
    if live is None or live.pending is None:
        raise Conflict(
            "No approval request is waiting for this episode", code="NO_PENDING_APPROVAL"
        )
    with live.lock:
        live.answer = body.decision
        call = live.pending["call"]
    live.answered.set()
    return {"episode_id": episode_id, "decision": body.decision, "call": call}


@router.post("/{episode_id}/stop")
def stop(episode_id: int, request: Request,
         session: Annotated[Session, Depends(get_session)]) -> dict[str, Any]:  # fmt: skip
    live = _live(request).get(episode_id)
    ep = session.get(Episode, episode_id)
    if live is None or ep is None or ep.status != "running":
        raise Conflict("The episode is not running in this process", code="NOT_RUNNING")
    live.stop.set()
    return {"episode_id": episode_id, "stopping": True}


def _episode(session: Session, episode_id: int) -> Episode:
    ep = session.get(Episode, episode_id)
    if ep is None:
        raise NotFound(f"No episode {episode_id}", code="EPISODE_NOT_FOUND")
    return ep


def _summary(ep: Episode) -> dict[str, Any]:
    scoring = (ep.outcome_detail or {}).get("scoring") or {}
    return {
        "id": ep.id, "run_id": ep.run_id, "case_id": ep.case_id, "system": ep.system,
        "run_idx": ep.run_idx, "temperature": ep.temperature, "seed": ep.seed,
        "status": ep.status, "terminal_state": ep.terminal_state, "outcome": ep.outcome,
        "rule": scoring.get("rule"), "rule_text": outcomes.RULES.get(str(scoring.get("rule"))),
        "budget_exhausted": ep.budget_exhausted,
        "unqualified_escalation": ep.unqualified_escalation,
        "tokens_in": ep.tokens_in, "tokens_out": ep.tokens_out,
        "interactive_approval": bool(((ep.outcome_detail or {}).get("console") or {})
                                     .get("interactive_approval")),
        "error": (ep.outcome_detail or {}).get("error") or (ep.outcome_detail or {}).get("stopped"),
        "investigation": (ep.outcome_detail or {}).get("investigation"),
    }  # fmt: skip


@router.get("")
def list_episodes(
    session: Annotated[Session, Depends(get_session)],
    run_id: int | None = None,
    case_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    q = select(Episode).order_by(Episode.id.desc()).limit(min(limit, 1000))
    if run_id is not None:
        q = q.where(Episode.run_id == run_id)
    if case_id is not None:
        q = q.where(Episode.case_id == case_id)
    tags = {t.episode_id: t for t in session.scalars(select(TraceTag))}
    out = []
    for ep in session.scalars(q):
        t = tags.get(ep.id)
        out.append(_summary(ep) | {"tags": t.tags if t else [], "starred": bool(t and t.starred)})
    return out


@router.get("/{episode_id}")
def get_episode(episode_id: int, request: Request,
                session: Annotated[Session, Depends(get_session)]) -> dict[str, Any]:  # fmt: skip
    ep = _episode(session, episode_id)
    steps = session.scalars(select(Step).where(Step.episode_id == episode_id).order_by(Step.idx))
    calls = session.scalars(select(ToolCall).where(ToolCall.episode_id == episode_id)
                            .order_by(ToolCall.id))  # fmt: skip
    case = session.get(Case, ep.case_id) if ep.case_id else None
    tag = session.get(TraceTag, episode_id)
    live = _live(request).get(episode_id)
    return _summary(ep) | {
        "steps": [{"idx": s.idx, "kind": s.kind, "payload": s.payload, "ms": s.ms} for s in steps],
        "tool_calls": [{"tool": c.tool, "class": c.class_, "args": c.args, "cited": c.cited,
                        "admitted": c.admitted, "call_class": c.call_class,
                        "escalation_class": c.escalation_class} for c in calls],
        "scoring": (ep.outcome_detail or {}).get("scoring"),
        "summary": (ep.outcome_detail or {}).get("episode"),
        "request": case.request if case else None,
        "labels": case.labels if case else None,
        "pending_approval": live.pending if live else None,
        "tags": tag.tags if tag else [], "starred": bool(tag and tag.starred),
        "note": tag.note if tag else None, "error_tags": ERROR_TAGS,
    }  # fmt: skip


@router.put("/{episode_id}/tags")
def put_tags(episode_id: int, body: Tags,
             session: Annotated[Session, Depends(get_session)]) -> dict[str, Any]:  # fmt: skip
    _episode(session, episode_id)
    unknown = [t for t in body.tags if t not in ERROR_TAGS]
    if unknown:
        raise Unprocessable(f"Unknown tags: {unknown}", code="UNKNOWN_TAG",
                            details={"known": ERROR_TAGS})  # fmt: skip
    row = session.get(TraceTag, episode_id) or TraceTag(episode_id=episode_id)
    row.tags, row.starred, row.note, row.updated_at = sorted(set(body.tags)), body.starred, \
        body.note, _now()  # fmt: skip
    session.add(row)
    session.commit()
    return {"episode_id": episode_id, "tags": row.tags, "starred": row.starred, "note": row.note}


@router.get("/{episode_id}/stream")
async def stream(episode_id: int, request: Request) -> EventSourceResponse:
    """Server-sent events for the episode's steps, from the first one, until it ends."""
    factory: sessionmaker[Session] = request.app.state.sessionmaker
    with session_scope(factory) as s:
        _episode(s, episode_id)
    live_all = _live(request)

    async def events() -> AsyncIterator[dict[str, str]]:
        sent, asked = -1, None
        while True:
            with factory() as s:
                rows = s.scalars(
                    select(Step)
                    .where(Step.episode_id == episode_id, Step.idx > sent)
                    .order_by(Step.idx)
                ).all()
                ep = s.get(Episode, episode_id)
                summary = _summary(ep) if ep is not None else None
                decisions = {
                    d.id: d.verdict
                    for d in s.scalars(
                        select(GateDecisionRow).where(GateDecisionRow.episode_id == episode_id)
                    )
                }
            for st in rows:
                event = "gate" if st.kind == "gate" else "step"
                yield {"event": event, "data": json.dumps({"idx": st.idx, "kind": st.kind,
                                                           "payload": st.payload})}  # fmt: skip
                sent = st.idx
            live = live_all.get(episode_id)
            pending = live.pending if live else None
            if pending and pending != asked:
                yield {"event": "approval_request", "data": json.dumps(pending)}
            asked = pending
            if summary is None or summary["status"] != "running":
                kind = "outcome" if summary and summary["status"] == "done" else "error"
                yield {
                    "event": kind,
                    "data": json.dumps((summary or {}) | {"gate_decisions": len(decisions)}),
                }
                return
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.25)

    return EventSourceResponse(events())
