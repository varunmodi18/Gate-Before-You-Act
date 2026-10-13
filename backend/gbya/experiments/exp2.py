"""Experiment 2: agent episodes from each case's frozen prefix (plan §A.1, §D.6.4, §D.10, T5.4).

``experiments/exp2.yaml`` names the systems with their run counts and the run schedule (A-4);
``fallback: F1`` applies the definition in ``experiments/fallbacks.yaml`` (never restated). The
effective plan (systems after the fallback, schedule) is stored in the run's config, so a later
edit of either file never changes an existing run.

A work unit is one episode (run, case, system, run_idx). The episode runs with an in-memory trace
and is written — episode, steps, gate decisions, tool calls and, when the case is labelled, its
outcome — in the same transaction that marks the unit ``done``; a killed worker leaves no partial
episode and the redo writes it once. Unlisted calls and escalations land in the adjudication
queue (``adjudication_queue``); an adjudication re-scores every episode of that case
(``rescore_case``).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from gbya.agent.episode import EpisodeRunner, MemoryTrace, prefix_text
from gbya.cases.models import Labels, Request
from gbya.config import REPO_ROOT, Settings
from gbya.data.connection import open_case_db
from gbya.errors import NotFound, Unprocessable
from gbya.experiments.exp1 import Exp1Case, load_case
from gbya.experiments.runs import Purpose, create_run
from gbya.gate.checks import GateEnv
from gbya.gate.config import GateConfig, load_configs
from gbya.gate.evidence import window_time_range
from gbya.gate.gate import Gate
from gbya.llm.factory import model_provenance
from gbya.scoring import outcomes
from gbya.store.models import Case, Episode, Job, Run
from gbya.tools.dispatcher import InProcessDispatcher, ToolDispatcher
from gbya.tools.registry import ToolEnvironment
from gbya.tools.state import EpisodeState

if TYPE_CHECKING:
    from gbya.experiments.runner import Deps

EXP2_SPEC = REPO_ROOT / "experiments" / "exp2.yaml"
FALLBACKS = REPO_ROOT / "experiments" / "fallbacks.yaml"


class Slot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    temperature: float = Field(ge=0, le=2)
    seed: int


class Exp2Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment: Literal[2] = 2
    schedule: dict[int, Slot]
    systems: dict[str, int]  # system -> number of runs (run indices 1..n)
    fallback: Literal["F1"] | None = None

    @model_validator(mode="after")
    def _check(self) -> Exp2Spec:
        if sorted(self.schedule) != list(range(1, len(self.schedule) + 1)):
            raise ValueError("schedule run indices must be 1..n")
        for sid, runs in self.systems.items():
            if not 1 <= runs <= len(self.schedule):
                raise ValueError(f"{sid}: {runs} runs, the schedule has {len(self.schedule)}")
        return self


class Fallback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    applies_to: str
    drop_systems: list[str]
    runs: dict[str, int]
    unchanged: list[str] = Field(default_factory=list)


def load_fallbacks(path: Path = FALLBACKS) -> dict[str, Fallback]:
    data = yaml.safe_load(path.read_text())
    return {name: Fallback.model_validate(body) for name, body in data.items()}


def load_spec(path: Path = EXP2_SPEC, overrides: dict[str, Any] | None = None) -> Exp2Spec:
    data = yaml.safe_load(path.read_text())
    return Exp2Spec.model_validate({**data, **(overrides or {})})


def effective_systems(spec: Exp2Spec, fallbacks: dict[str, Fallback] | None = None
                      ) -> dict[str, int]:  # fmt: skip
    """Systems and run counts after the fallback, if one is set."""
    systems = dict(spec.systems)
    if spec.fallback is None:
        return systems
    fb = (fallbacks or load_fallbacks())[spec.fallback]
    if fb.applies_to != "exp2":
        raise ValueError(f"fallback {spec.fallback} applies to {fb.applies_to}, not exp2")
    for sid in fb.drop_systems:
        systems.pop(sid, None)
    for sid, runs in fb.runs.items():
        if sid in systems:
            systems[sid] = runs
    return systems


def plan_items(systems: dict[str, int], case_ids: Iterable[str]) -> list[tuple[str, str, int]]:
    ids = sorted(set(case_ids))
    return [(c, sid, r) for c in ids for sid, n in systems.items() for r in range(1, n + 1)]


# ---- cases --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Exp2Case:
    base: Exp1Case  # database, effective context and toolset (the Exp 1 view of the case)
    request: Request
    prefix: dict[str, Any]
    labels: Labels

    @property
    def case_id(self) -> str:
        return self.base.case_id


def prefix_file(settings: Settings, case_id: str) -> Path:
    sid, variant = case_id.split(":", 1)
    return settings.resolve(settings.cases_dir) / sid / "prefixes" / f"{variant}.json"


def load_episode_case(session: Session, case_id: str, settings: Settings) -> Exp2Case:
    import json

    base = load_case(session, case_id, settings)
    row = session.get(Case, case_id)
    assert row is not None
    path = prefix_file(settings, case_id)
    if not path.is_file():
        raise ValueError(f"{case_id}: no prefix ({path.name}); generate the scenario's variants")
    return Exp2Case(base, Request.model_validate(row.request), json.loads(path.read_text()),
                    Labels.model_validate(row.labels or {}))  # fmt: skip


def scorable(labels: Labels) -> bool:
    return labels.has_justified_completion is not None


# ---- one episode ---------------------------------------------------------------------------------


@dataclass
class EpisodeEnv:
    """The tool side of one episode in this process: the case database, the gate and the tools,
    sharing one EpisodeState seeded with the prefix's registry."""

    con: Any
    gate: Gate
    env: GateEnv
    tools: ToolEnvironment

    def close(self) -> None:
        self.con.close()


def episode_env(
    case: Exp2Case, config: GateConfig, deps: Deps,
    factory: sessionmaker[Session] | None = None, approver: Any = None,
) -> EpisodeEnv:  # fmt: skip
    """Exp 2, the Console and the MCP server build an episode's tool side here (one path)."""
    verifier = (
        deps.verifier(config.verifier_variant, case.case_id, factory)
        if config.verifier_variant is not None
        else None
    )
    con = open_case_db(case.base.db_path)
    state = EpisodeState(retrieved={int(i) for i in case.prefix.get("retrieved", [])})
    ctx = case.base.context
    env = GateEnv(con=con, ctx=ctx, state=state, counter=deps.counter,
                  allowed_tools=case.base.toolset, window=window_time_range(con),
                  approver=approver)  # fmt: skip
    tools = ToolEnvironment(con=con, context=ctx, state=state, counter=deps.counter)
    return EpisodeEnv(con, Gate(config, deps.policy, verifier), env, tools)


def make_dispatcher(
    case: Exp2Case, config: GateConfig, deps: Deps, trace: Any,
    factory: sessionmaker[Session] | None = None,
) -> tuple[ToolDispatcher, Callable[[], None]]:  # fmt: skip
    """The dispatcher the configuration's ``transport`` asks for, and how to close it."""
    if config.transport == "mcp":
        from gbya.agent.mcp_client import McpDispatcher

        registry = [int(i) for i in case.prefix.get("retrieved", [])]
        mcp = McpDispatcher.start(deps.settings, case.case_id, config.id, trace,
                                  counter_name=deps.counter.name,
                                  initial_retrieved=registry)  # fmt: skip
        return mcp, mcp.close
    e = episode_env(case, config, deps, factory)
    return InProcessDispatcher(e.gate, e.env, e.tools, trace), e.close


def run_episode(
    case: Exp2Case, config: GateConfig, slot: Slot, deps: Deps,
    factory: sessionmaker[Session] | None = None,
) -> MemoryTrace:  # fmt: skip
    """One episode of ``config`` on the case, in memory. The verifier (if any) retrieves through
    the retrieval cache when a session factory is given."""
    trace = MemoryTrace()
    dispatcher, close = make_dispatcher(case, config, deps, trace, factory)
    try:
        EpisodeRunner(
            client=deps.client, dispatcher=dispatcher, request=case.request,
            prefix=prefix_text(case.prefix), temperature=slot.temperature, seed=slot.seed,
            trace=trace, counter=deps.counter,
        ).run()  # fmt: skip
        trace.transport = dispatcher.transport
        return trace
    finally:
        close()


def write_episode(
    session: Session, trace: MemoryTrace, case: Exp2Case, system: str, run_idx: int,
    slot: Slot, run_id: int | None,
) -> Episode:  # fmt: skip
    """Persist a finished episode and score it when the case is labelled."""
    ep = Episode(run_id=run_id, case_id=case.case_id, system=system, run_idx=run_idx,
                 temperature=slot.temperature, seed=slot.seed, status="running")  # fmt: skip
    trace.persist(session, ep)
    if scorable(case.labels):
        outcomes.score_episode(session, ep.id, case.labels)
    return ep


# ---- runs ---------------------------------------------------------------------------------------


def create_exp2_run(
    session: Session,
    settings: Settings,
    *,
    purpose: Purpose,
    case_ids: Sequence[str] | None = None,
    overrides: dict[str, Any] | None = None,
    start: bool = True,
    counter_name: str | None = None,
) -> Run:
    from gbya.experiments.runner import (
        _counter_name,
        config_hash,
        git_sha,
        refuse_research_tooling,
    )

    spec = load_spec(overrides=overrides)
    configs = load_configs()
    unknown = [s for s in spec.systems if s not in configs]
    if unknown:
        raise Unprocessable(f"Systems not defined in gate/configs.yaml: {unknown}",
                            code="UNKNOWN_SYSTEM")  # fmt: skip
    systems = effective_systems(spec)
    if not systems:
        raise Unprocessable("No systems to run", code="NO_SYSTEMS")
    if case_ids is None:
        case_ids = list(
            session.scalars(select(Case.id).where(Case.case_db_path.is_not(None), Case.set_ != "X"))
        )
    ids = sorted(set(case_ids))
    if not ids:
        raise Unprocessable("No cases to run", code="NO_CASES")
    missing = [c for c in ids if session.get(Case, c) is None]
    if missing:
        raise NotFound(f"Unknown cases: {missing[:10]}", code="CASE_NOT_FOUND")
    no_prefix = [c for c in ids if not prefix_file(settings, c).is_file()]
    if no_prefix:
        raise Unprocessable(f"Cases without a prefix: {no_prefix[:10]}", code="NO_PREFIX",
                            hint="Generate the scenarios' variants first")  # fmt: skip
    prov = model_provenance(settings)
    counter_name = counter_name or _counter_name()
    if purpose == "research":
        refuse_research_tooling(prov["backend"], counter_name)
    config = {
        "spec": spec.model_dump(mode="json"),
        "systems": systems,  # effective, after the fallback
        "fallback": spec.fallback,
        "case_ids": ids,
        "configs": {sid: configs[sid].model_dump() for sid in systems},
    }
    run = create_run(
        session, experiment=2, config=config, config_hash=config_hash(config), purpose=purpose,
        case_ids=ids, replay=prov["backend"] == "replay",
        frozen_path=settings.resolve(settings.cases_dir) / "FROZEN.json",
    )  # fmt: skip
    run.git_sha = git_sha()
    run.model_id = prov["model_id"]
    run.model_file_sha256 = prov["model_file_sha256"]
    run.backend = prov["backend"]
    run.backend_flags = {**prov["backend_flags"], "tokenizer": counter_name,
                         "fallback": spec.fallback}  # fmt: skip
    if start:
        session.add(Job(run_id=run.id, status="queued"))
    session.flush()
    return run


def run_items(run: Run) -> list[tuple[str, str, int]]:
    return plan_items(dict(run.config["systems"]), run.config["case_ids"])


def slot_of(run: Run, run_idx: int) -> Slot:
    schedule = run.config["spec"]["schedule"]  # JSON object keys are strings once stored
    return Slot.model_validate(schedule.get(str(run_idx)) or schedule[run_idx])


# ---- adjudication queue -------------------------------------------------------------------------


def adjudication_queue(session: Session, run_id: int | None = None) -> list[dict[str, Any]]:
    """Unlisted items still awaiting a decision, one entry per (case, item) whatever system
    produced it (the system is not shown, §E.1 row 9), with the number of episodes waiting."""
    q = select(Episode).where(Episode.outcome == "pending_adjudication")
    if run_id is not None:
        q = q.where(Episode.run_id == run_id)
    waiting: dict[tuple[str, str], int] = defaultdict(int)
    for ep in session.scalars(q):
        for key in (ep.outcome_detail or {}).get("scoring", {}).get("pending", []):
            waiting[(str(ep.case_id), key)] += 1
    out = []
    for (case_id, key), n in sorted(waiting.items()):
        tool, _, rest = key.partition(":")
        kind = "call" if tool not in ("request_approval", "ask_analyst", "draft_report") else (
            "escalation")  # fmt: skip
        out.append({"ref_id": outcomes.adjudication_ref(case_id, key), "case_id": case_id,
                    "kind": kind, "tool": tool, "item": rest, "episodes": n})  # fmt: skip
    return out


def rescore_case(session: Session, case_id: str, settings: Settings) -> int:
    """Re-score every finished episode of a case (after an adjudication); returns the count."""
    row = session.get(Case, case_id)
    if row is None:
        raise NotFound(f"No case {case_id}", code="CASE_NOT_FOUND")
    labels = Labels.model_validate(row.labels or {})
    if not scorable(labels):
        return 0
    eps = list(session.scalars(select(Episode.id).where(Episode.case_id == case_id,
                                                        Episode.status == "done")))  # fmt: skip
    for eid in eps:
        outcomes.score_episode(session, eid, labels)
    return len(eps)


def outcome_counts(session: Session, run_id: int) -> dict[str, dict[str, int]]:
    """Outcome counts per system (``unscored`` while the case's labels are pending)."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for system, outcome in session.execute(select(Episode.system, Episode.outcome)
                                           .where(Episode.run_id == run_id,
                                                  Episode.status == "done")):  # fmt: skip
        counts[system][outcome or "unscored"] += 1
    return {s: dict(v) for s, v in counts.items()}
