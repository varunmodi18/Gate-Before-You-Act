"""Experiment 3: live investigation plus response on the end-to-end windows (plan §A.1, §D.10.4,
FR-19, T6.2).

``experiments/exp3.yaml`` names the systems (G0, G1, G3, two runs each) and the A-4
schedule; fallback F1 leaves Exp 3 unchanged. A work unit is one episode on an end-to-end case
(``<sid>:X``): the investigator works on the window (its queries register the records it shows),
its handover replaces Exp 2's frozen prefix, and the same episode loop, gate and tools respond.
Both phases go into one trace (investigation steps carry ``phase: investigation``) and are written
with the unit's ``done`` status in one transaction.

Metrics: technique precision/recall/F1 of the accepted techniques against the window's OTRF
techniques plus adjudicated incidental techniques (A-15; predicted techniques outside the metadata
are listed in the adjudication queue), cited-evidence precision/recall (cases with a justified
completion) and the outcomes of §D.9.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from gbya.agent.episode import EpisodeRunner, MemoryTrace, prefix_text
from gbya.agent.investigator import Investigation, Investigator, handover
from gbya.agent.proposer import LIVE_LABEL
from gbya.cases.models import Labels, Request
from gbya.config import REPO_ROOT, Settings
from gbya.context.models import TrustedContext
from gbya.data.connection import open_case_db
from gbya.errors import NotFound, Unprocessable
from gbya.experiments.exp2 import Exp2Spec, Slot
from gbya.experiments.runs import Purpose, create_run
from gbya.gate.checks import GateEnv
from gbya.gate.config import GateConfig, load_configs
from gbya.gate.evidence import window_time_range
from gbya.gate.gate import Gate
from gbya.llm.factory import model_provenance
from gbya.scoring import metrics, outcomes
from gbya.store.models import Adjudication, Case, Episode, Job, Run, Scenario, Window
from gbya.tools.names import ALL_TOOLS, STATE_CHANGING
from gbya.tools.registry import ToolEnvironment
from gbya.tools.state import EpisodeState

if TYPE_CHECKING:
    from gbya.experiments.runner import Deps

EXP3_SPEC = REPO_ROOT / "experiments" / "exp3.yaml"


class Exp3Spec(Exp2Spec):
    experiment: Literal[3] = 3  # type: ignore[assignment]

    @model_validator(mode="after")
    def _no_fallback(self) -> Exp3Spec:
        if self.fallback is not None:
            raise ValueError("fallback F1 leaves Exp 3 unchanged")
        return self


def load_spec(path: Path = EXP3_SPEC, overrides: dict[str, Any] | None = None) -> Exp3Spec:
    data = yaml.safe_load(path.read_text())
    return Exp3Spec.model_validate({**data, **(overrides or {})})


def plan_items(systems: dict[str, int], case_ids: Sequence[str]) -> list[tuple[str, str, int]]:
    ids = sorted(set(case_ids))
    return [(c, sid, r) for c in ids for sid, n in systems.items() for r in range(1, n + 1)]


def run_items(run: Run) -> list[tuple[str, str, int]]:
    return plan_items(dict(run.config["systems"]), run.config["case_ids"])


def slot_of(run: Run, run_idx: int) -> Slot:
    schedule = run.config["spec"]["schedule"]
    return Slot.model_validate(schedule.get(str(run_idx)) or schedule[run_idx])


# ---- cases --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Exp3Case:
    case_id: str
    window_id: str
    db_path: Path
    context: TrustedContext
    request: Request
    labels: Labels
    techniques: tuple[str, ...]  # the window's OTRF techniques (ground truth, A-15)


def load_e3_case(session: Session, case_id: str, settings: Settings) -> Exp3Case:
    row = session.get(Case, case_id)
    if row is None or row.set_ != "X":
        raise ValueError(f"{case_id} is not an end-to-end case")
    scenario = session.get(Scenario, row.scenario_id)
    window = session.get(Window, scenario.window_id) if scenario else None
    if scenario is None or window is None or scenario.trusted_context is None:
        raise ValueError(f"{case_id}: scenario or window missing")
    return Exp3Case(
        case_id=row.id, window_id=window.id, db_path=settings.resolve(Path(str(row.case_db_path))),
        context=TrustedContext.model_validate(scenario.trusted_context),
        request=Request.model_validate(row.request), labels=Labels.model_validate(row.labels or {}),
        techniques=tuple(str(t) for t in (window.techniques or [])),
    )  # fmt: skip


# ---- one episode --------------------------------------------------------------------------------


def run_episode(
    case: Exp3Case, config: GateConfig, slot: Slot, deps: Deps,
    factory: sessionmaker[Session] | None = None,
) -> tuple[MemoryTrace, Investigation]:  # fmt: skip
    """Investigation, then the response episode, in memory (one trace)."""
    if config.transport != "in_process":
        raise Unprocessable(f"{config.id}: Exp 3 runs in-process", code="TRANSPORT_UNSUPPORTED")
    from gbya.gate.verifier import live_retriever

    verifier = (deps.verifier(config.verifier_variant, case.case_id, factory)
                if config.verifier_variant is not None else None)  # fmt: skip
    s = deps.settings
    has_index = (s.resolve(s.data_dir) / "index" / "MANIFEST.json").is_file()
    con = open_case_db(case.db_path)
    try:
        state = EpisodeState()
        env = GateEnv(con=con, ctx=case.context, state=state, counter=deps.counter,
                      allowed_tools=ALL_TOOLS, window=window_time_range(con))  # fmt: skip
        tools = ToolEnvironment(con=con, context=case.context, state=state, counter=deps.counter)
        inv = Investigator(client=deps.client, tools=tools, counter=deps.counter,
                           retrieve=live_retriever(deps.index()) if has_index else None,
                           temperature=slot.temperature, seed=slot.seed,
                           request_text=case.request.text).run(con)  # fmt: skip
        trace = MemoryTrace()
        for kind, payload in inv.events:
            trace.step(kind, payload)
        EpisodeRunner(
            client=deps.client, request=case.request,
            prefix=prefix_text(handover(inv, deps.counter)), temperature=slot.temperature,
            seed=slot.seed, trace=trace, counter=deps.counter,
            gate=Gate(config, deps.policy, verifier), env=env, tools=tools,
            prefix_label=LIVE_LABEL,
        ).run()  # fmt: skip
        return trace, inv
    finally:
        con.close()


def cited_by_executed_calls(trace: MemoryTrace) -> list[int]:
    return sorted({int(i) for k, p in trace.kinds()
                   if k == "tool" and p.get("tool") in STATE_CHANGING and "approval_state" in p
                   for i in p.get("cited", [])})  # fmt: skip


def write_episode(
    session: Session, trace: MemoryTrace, inv: Investigation, case: Exp3Case, system: str,
    run_idx: int, slot: Slot, run_id: int | None,
) -> Episode:  # fmt: skip
    ep = Episode(run_id=run_id, case_id=case.case_id, system=system, run_idx=run_idx,
                 temperature=slot.temperature, seed=slot.seed, status="running")  # fmt: skip
    trace.persist(session, ep)
    ep.tokens_in += inv.tokens_in  # the episode's model use: investigation plus response
    ep.tokens_out += inv.tokens_out
    ep.outcome_detail = {**(ep.outcome_detail or {}), "investigation": inv.summary(),
                         "cited": cited_by_executed_calls(trace)}  # fmt: skip
    if case.labels.has_justified_completion is not None:
        outcomes.score_episode(session, ep.id, case.labels)
    session.flush()
    return ep


# ---- runs ---------------------------------------------------------------------------------------


def create_exp3_run(
    session: Session, settings: Settings, *, purpose: Purpose,
    case_ids: Sequence[str] | None = None, overrides: dict[str, Any] | None = None,
    start: bool = True, counter_name: str | None = None,
) -> Run:  # fmt: skip
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
    if case_ids is None:
        case_ids = list(session.scalars(select(Case.id).where(Case.set_ == "X")))
    ids = sorted(set(case_ids))
    if not ids:
        raise Unprocessable("No end-to-end cases to run", code="NO_CASES")
    for cid in ids:
        row = session.get(Case, cid)
        if row is None:
            raise NotFound(f"Unknown case {cid}", code="CASE_NOT_FOUND")
        if row.set_ != "X":
            raise Unprocessable(f"{cid} is not an end-to-end case", code="NOT_END_TO_END")
    prov = model_provenance(settings)
    counter_name = counter_name or _counter_name()
    if purpose == "research":
        refuse_research_tooling(prov["backend"], counter_name)
    systems = dict(spec.systems)
    config = {"spec": spec.model_dump(mode="json"), "systems": systems, "case_ids": ids,
              "configs": {sid: configs[sid].model_dump() for sid in systems}}  # fmt: skip
    run = create_run(
        session, experiment=3, config=config, config_hash=config_hash(config), purpose=purpose,
        case_ids=ids, replay=prov["backend"] == "replay",
        frozen_path=settings.resolve(settings.cases_dir) / "FROZEN.json",
    )  # fmt: skip
    run.git_sha = git_sha()
    run.model_id = prov["model_id"]
    run.model_file_sha256 = prov["model_file_sha256"]
    run.backend = prov["backend"]
    run.backend_flags = {**prov["backend_flags"], "tokenizer": counter_name}
    if start:
        session.add(Job(run_id=run.id, status="queued"))
    session.flush()
    return run


# ---- metrics and the incidental-technique queue -------------------------------------------------


def technique_ref(case_id: str, technique: str) -> str:
    return f"{case_id}::technique:{technique}"


def incidental_decisions(session: Session, case_id: str) -> dict[str, bool]:
    prefix = technique_ref(case_id, "")
    rows = session.scalars(select(Adjudication).where(
        Adjudication.kind == "incidental_technique", Adjudication.ref_id.startswith(prefix))
        .order_by(Adjudication.id))  # fmt: skip
    return {r.ref_id[len(prefix) :]: bool(r.decision.get("incidental")) for r in rows}


def _episodes(session: Session, run_id: int) -> list[Episode]:
    return list(session.scalars(select(Episode).where(Episode.run_id == run_id,
                                                      Episode.status == "done")))  # fmt: skip


def incidental_queue(session: Session, run_id: int | None = None) -> list[dict[str, Any]]:
    """Predicted techniques outside the window's metadata, not yet adjudicated (A-15)."""
    q = select(Episode).where(Episode.status == "done", Episode.case_id.like("%:X"))
    if run_id is not None:
        q = q.where(Episode.run_id == run_id)
    waiting: dict[tuple[str, str], int] = defaultdict(int)
    for ep in session.scalars(q):
        cid = str(ep.case_id)
        _labels, techniques = _truth(session, cid)
        predicted = (ep.outcome_detail or {}).get("investigation", {}).get(
            "predicted_techniques", [])  # fmt: skip
        score = metrics.technique_score(predicted, techniques, incidental_decisions(session, cid))
        for t in score.pending:
            waiting[(cid, t)] += 1
    return [{"ref_id": technique_ref(c, t), "case_id": c, "kind": "technique", "tool": t,
             "item": "predicted technique not in the window's metadata", "episodes": n}
            for (c, t), n in sorted(waiting.items())]  # fmt: skip


def _truth(session: Session, case_id: str) -> tuple[Labels, tuple[str, ...]]:
    row = session.get(Case, case_id)
    sc = session.get(Scenario, row.scenario_id) if row else None
    w = session.get(Window, sc.window_id) if sc else None
    labels = Labels.model_validate((row.labels if row else None) or {})
    return labels, tuple(str(t) for t in ((w.techniques if w else None) or []))


def _mean(xs: list[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return sum(vals) / len(vals) if vals else None


def report(session: Session, run_id: int) -> dict[str, Any]:
    """Per system: pooled technique precision/recall/F1 (refused while incidentals are pending)
    and mean cited-evidence precision/recall over cases with a justified completion."""
    scores: dict[str, list[metrics.TechniqueScore]] = defaultdict(list)
    evidence: dict[str, list[metrics.EvidenceScore]] = defaultdict(list)
    for ep in _episodes(session, run_id):
        cid = str(ep.case_id)
        labels, techniques = _truth(session, cid)
        detail = ep.outcome_detail or {}
        predicted = detail.get("investigation", {}).get("predicted_techniques", [])
        scores[ep.system].append(metrics.technique_score(
            predicted, techniques, incidental_decisions(session, cid)))  # fmt: skip
        ev = metrics.evidence_score(detail.get("cited", []), labels.evidence_retrievable,
                                    labels.has_justified_completion)  # fmt: skip
        if ev is not None:
            evidence[ep.system].append(ev)
    out: dict[str, Any] = {}
    for system in sorted(scores):
        tech: dict[str, Any]
        try:
            pooled = metrics.micro_f1(scores[system])
            tech = {"precision": pooled.precision, "recall": pooled.recall, "f1": pooled.f1,
                    "tp": pooled.tp, "fp": pooled.fp, "fn": pooled.fn, "refused": None}  # fmt: skip
        except metrics.MetricRefused as exc:
            tech = {"refused": str(exc)}
        evs = evidence[system]
        out[system] = {"episodes": len(scores[system]), "technique": tech,
                       "evidence": {"n": len(evs), "precision": _mean([e.precision for e in evs]),
                                    "recall": _mean([e.recall for e in evs])}}  # fmt: skip
    return out
