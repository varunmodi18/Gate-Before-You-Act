"""Experiment runs as resumable work units (plan T3.5, FR-25, §F.1).

A run is planned as ``job_items`` keyed (run, case, system, run_idx) — the idempotency key. A
work unit's results and its ``done`` status are committed in one transaction, so a worker killed
at any point leaves the unit unfinished and a restarted worker redoes it without duplicates.

Exp 1 units (``experiments/exp1.yaml``):

* ``V:<variant>`` for runs 1..n — Exp 1V: the verifier variant judges the package (stored in
  ``verifier_evals``);
* code-only systems for run 1 — deterministic gate decisions (A-13);
* composed systems for runs 1..n — the gate with the stored Exp 1V output of the same run.

Exp 1V units run first (up to ``worker_concurrency`` in parallel, matching the model server's
concurrent sequences); gate units follow. Units with a deterministic failure are marked ``error``
with the reason; a unit is retried by a later job while it has fewer than ``MAX_ATTEMPTS``.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from gbya.config import REPO_ROOT, Settings
from gbya.data.connection import open_case_db
from gbya.errors import NotFound, Unprocessable
from gbya.experiments.exp1 import (
    Exp1Case,
    call_from_row,
    compose,
    decide,
    decision_row,
    eval_row,
    judge,
    load_case,
)
from gbya.experiments.runs import Purpose, create_run
from gbya.gate.config import GateConfig, load_configs
from gbya.gate.evidence import read_cited
from gbya.gate.gate import Gate
from gbya.gate.verifier import VARIANT_MODE, LLMVerifier, Variant, live_retriever
from gbya.llm.client import LLMClient
from gbya.llm.factory import model_provenance
from gbya.llm.tokens import TokenCounter
from gbya.logging import get_logger
from gbya.policy.engine import PolicyEngine, load_evidence_requirements
from gbya.retrieval.cache import RetrievalCache, reranker_name
from gbya.retrieval.index import Reranker, RetrievalIndex
from gbya.retrieval.index import load as load_index
from gbya.retrieval.query import build_query
from gbya.retrieval.rerank import RERANKER_ID, unavailable_reason
from gbya.store.db import session_scope
from gbya.store.models import Case, Job, JobItem, Run, VerifierEval, utcnow

EXP1_SPEC = REPO_ROOT / "experiments" / "exp1.yaml"
V_PREFIX = "V:"
MAX_ATTEMPTS = 3

log = get_logger("gbya.experiments.runner")


class Exp1Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment: Literal[1] = 1
    verifier_variants: list[Variant]
    verifier_runs: int = Field(ge=1, le=3)
    code_only: list[str]
    composed: list[str]

    @model_validator(mode="after")
    def _systems(self) -> Exp1Spec:
        configs = load_configs()
        for sid in self.code_only + self.composed:
            if sid not in configs:
                raise ValueError(f"unknown system {sid}")
        for sid in self.code_only:
            if "C4" in configs[sid].checks:
                raise ValueError(f"{sid} has C4; list it under composed")
        for sid in self.composed:
            variant = configs[sid].verifier_variant
            if variant is None:
                raise ValueError(f"{sid} has no C4; list it under code_only")
            if variant not in self.verifier_variants:
                raise ValueError(f"{sid} needs verifier variant {variant} in verifier_variants")
        return self


def load_spec(path: Path = EXP1_SPEC, overrides: dict[str, Any] | None = None) -> Exp1Spec:
    data = yaml.safe_load(path.read_text())
    return Exp1Spec.model_validate({**data, **(overrides or {})})


def plan_items(spec: Exp1Spec, case_ids: Iterable[str]) -> list[tuple[str, str, int]]:
    """Every work unit of an Exp 1 run, Exp 1V first (composition reads its output)."""
    ids = sorted(set(case_ids))
    runs = range(1, spec.verifier_runs + 1)
    items = [(c, f"{V_PREFIX}{v}", r) for c in ids for v in spec.verifier_variants for r in runs]
    items += [(c, s, 1) for c in ids for s in spec.code_only]
    items += [(c, s, r) for c in ids for s in spec.composed for r in runs]
    return items


# ---- dependencies shared by a worker's units ----------------------------------------------------


@dataclass
class Deps:
    settings: Settings
    client: LLMClient
    counter: TokenCounter
    policy: PolicyEngine = field(
        default_factory=lambda: PolicyEngine.from_file(REPO_ROOT / "policy" / "rules.yaml")
    )
    configs: dict[str, GateConfig] = field(default_factory=load_configs)
    requirements: dict[str, str] = field(
        default_factory=lambda: load_evidence_requirements(
            REPO_ROOT / "policy" / "evidence_requirements.yaml"
        )
    )
    reranker: Reranker | None = None  # injected, or loaded on first use (CPU cross-encoder)
    _index: RetrievalIndex | None = None
    _caches: dict[int, RetrievalCache] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def index(self) -> RetrievalIndex:
        with self._lock:
            if self._index is None:
                s = self.settings
                self._index = load_index(s.resolve(s.data_dir) / "index")
            return self._index

    def get_reranker(self) -> Reranker:
        """The reranker, loaded once; raises ``RerankerUnavailable`` (never a bm25 fallback)."""
        with self._lock:
            if self.reranker is None:
                from gbya.retrieval.rerank import load_reranker

                s = self.settings
                self.reranker = load_reranker(s.resolve(s.reranker_dir))
            return self.reranker

    def reranker_name(self) -> str | None:
        return reranker_name(self.reranker)

    def cache(self, factory: sessionmaker[Session]) -> RetrievalCache:
        with self._lock:
            key = id(factory)
            if key not in self._caches:
                rid = (
                    reranker_name(self.reranker) or RERANKER_ID
                )  # the pinned model, if not injected
                self._caches[key] = RetrievalCache(
                    factory, self.index_unlocked(), self.get_reranker, reranker_id=rid
                )
            return self._caches[key]

    def index_unlocked(self) -> RetrievalIndex:
        if self._index is None:
            s = self.settings
            self._index = load_index(s.resolve(s.data_dir) / "index")
        return self._index

    def verifier(
        self, variant: Variant, case_id: str | None = None,
        factory: sessionmaker[Session] | None = None,
    ) -> LLMVerifier:  # fmt: skip
        """The verifier for ``variant``. With a case and a session factory, retrieval goes
        through the retrieval cache (Exp 1, Playground); otherwise it is live."""
        if VARIANT_MODE[variant] == "none":
            return LLMVerifier(self.client, variant, self.requirements, None)
        if case_id is not None and factory is not None:
            retriever = self.cache(factory).retriever(case_id)
        else:
            mode = VARIANT_MODE[variant]
            rr = self.get_reranker() if mode == "bm25_rerank" else None
            retriever = live_retriever(self.index(), rr)
        return LLMVerifier(self.client, variant, self.requirements, retriever)


def case_query(case: Exp1Case, requirements: Mapping[str, str]) -> str:
    """The retrieval query of a case's package, exactly as C4 builds it (§D.3)."""
    con = open_case_db(case.db_path)
    try:
        cited = list(case.package.cited)
        by_id = read_cited(con, cited)
        records = [by_id[i] for i in dict.fromkeys(cited) if i in by_id]
        return build_query(case.package.tool, records, requirements)
    finally:
        con.close()


def variant_availability(deps: Deps) -> dict[str, str | None]:
    """Why each verifier variant cannot run here (None = it can): retrieval variants need the
    index (``make index``); ``rerank`` also needs the CPU reranker (never a fallback to bm25)."""
    s = deps.settings
    has_index = (s.resolve(s.data_dir) / "index" / "MANIFEST.json").is_file()
    out: dict[str, str | None] = {}
    for variant, mode in VARIANT_MODE.items():
        if mode != "none" and not has_index:
            out[variant] = "needs the retrieval index (make index)"
        elif mode == "bm25_rerank" and deps.reranker is None:
            out[variant] = unavailable_reason(s.resolve(s.reranker_dir))
        else:
            out[variant] = None
    return out


# ---- creating runs ------------------------------------------------------------------------------


def git_sha() -> str | None:
    """HEAD, with ``+dirty`` when tracked files have uncommitted changes (run provenance)."""
    try:
        head = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return f"{head}+dirty" if head and dirty else head or None


def config_hash(config: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def create_exp1_run(
    session: Session,
    settings: Settings,
    *,
    purpose: Purpose,
    case_ids: Sequence[str] | None = None,
    overrides: dict[str, Any] | None = None,
    start: bool = True,
    counter_name: str | None = None,
) -> Run:
    """Validate the spec and the cases, record provenance, and queue a job when ``start``.

    ``counter_name`` is the token counter the worker will use (default: ``default_counter()``);
    research runs are refused with the test-only counter, as with a fake or replay backend."""
    spec = load_spec(overrides=overrides)
    if case_ids is None:
        case_ids = list(session.scalars(select(Case.id).where(Case.case_db_path.is_not(None))))
    ids = sorted(set(case_ids))
    if not ids:
        raise Unprocessable("No cases to run", code="NO_CASES")
    missing = [c for c in ids if session.get(Case, c) is None]
    if missing:
        raise NotFound(f"Unknown cases: {missing[:10]}", code="CASE_NOT_FOUND")
    prov = model_provenance(settings)
    if counter_name is None:
        counter_name = _counter_name()
    if purpose == "research":
        refuse_research_tooling(prov["backend"], counter_name)
    configs = load_configs()
    config = {
        "spec": spec.model_dump(),
        "case_ids": ids,
        "configs": {sid: configs[sid].model_dump() for sid in spec.code_only + spec.composed},
    }
    run = create_run(session, experiment=1, config=config, config_hash=config_hash(config),
                     purpose=purpose, case_ids=ids,
                     replay=prov["backend"] == "replay")  # fmt: skip
    run.git_sha = git_sha()
    run.model_id = prov["model_id"]
    run.model_file_sha256 = prov["model_file_sha256"]
    run.backend = prov["backend"]
    run.backend_flags = {**prov["backend_flags"], "tokenizer": counter_name}
    if start:
        session.add(Job(run_id=run.id, status="queued"))
    session.flush()
    return run


def ensure_items(session: Session, run: Run) -> int:
    """Insert missing work units (INSERT OR IGNORE on the idempotency key)."""
    spec = Exp1Spec.model_validate(run.config["spec"])
    items = plan_items(spec, run.config["case_ids"])
    rows = [{"run_id": run.id, "case_id": c, "system": s, "run_idx": r, "status": "queued",
             "attempts": 0} for c, s, r in items]  # fmt: skip
    for i in range(0, len(rows), 500):
        session.execute(sqlite_insert(JobItem).values(rows[i : i + 500]).on_conflict_do_nothing())
    return len(items)


# ---- executing units ----------------------------------------------------------------------------


class _CaseCache:
    def __init__(self, factory: sessionmaker[Session], settings: Settings) -> None:
        self.factory, self.settings = factory, settings
        self._cases: dict[str, Exp1Case] = {}
        self._lock = Lock()

    def get(self, case_id: str) -> Exp1Case:
        with self._lock:
            if case_id not in self._cases:
                with session_scope(self.factory) as s:
                    self._cases[case_id] = load_case(s, case_id, self.settings)
            case: Exp1Case = self._cases[case_id]
            return case


def _start_item(factory: sessionmaker[Session], item_id: int) -> bool:
    with session_scope(factory) as s:
        ji = s.get(JobItem, item_id)
        if ji is None or ji.status == "done":
            return False
        ji.status, ji.attempts, ji.updated_at = "running", ji.attempts + 1, utcnow()
    return True


def _fail_item(factory: sessionmaker[Session], item_id: int, exc: BaseException) -> None:
    with session_scope(factory) as s:
        ji = s.get(JobItem, item_id)
        if ji is not None:
            ji.status, ji.updated_at = "error", utcnow()
            ji.error = f"{type(exc).__name__}: {exc}"[:2000]


def _finish_item(factory: sessionmaker[Session], item_id: int, rows: list[Any]) -> None:
    """Results and the ``done`` status in one transaction (the idempotency guarantee)."""
    with session_scope(factory) as s:
        ji = s.get(JobItem, item_id)
        assert ji is not None
        if ji.status == "done":
            return
        s.add_all(rows)
        ji.status, ji.error, ji.updated_at = "done", None, utcnow()


def run_item(
    factory: sessionmaker[Session], deps: Deps, cases: _CaseCache, run_id: int, item: JobItem
) -> None:
    if not _start_item(factory, item.id):
        return
    try:
        case = cases.get(item.case_id)
        if item.system.startswith(V_PREFIX):
            variant: Variant = item.system[len(V_PREFIX) :]  # type: ignore[assignment]
            call = judge(case, deps.verifier(variant, case.case_id, factory), deps.counter)
            rows: list[Any] = [eval_row(run_id, case.case_id, item.run_idx, call)]
        else:
            config = deps.configs[item.system]
            if config.verifier_variant is None:
                decision, ms = decide(case, Gate(config, deps.policy), deps.counter)
            else:
                with session_scope(factory) as s:
                    stored = s.scalar(select(VerifierEval).where(
                        VerifierEval.run_id == run_id, VerifierEval.case_id == case.case_id,
                        VerifierEval.variant == config.verifier_variant,
                        VerifierEval.run_idx == item.run_idx))  # fmt: skip
                    if stored is None:
                        raise RuntimeError(f"no Exp 1V output for {config.verifier_variant} "
                                           f"run {item.run_idx}")  # fmt: skip
                    call = call_from_row(stored)
                decision, ms = compose(case, config, deps.policy, call, deps.counter)
            rows = [decision_row(decision, case, run_id, item.run_idx, ms)]
        _finish_item(factory, item.id, rows)
    except IntegrityError as exc:  # a unit's result already exists: never write it twice
        _fail_item(factory, item.id, exc)
    except Exception as exc:
        log.warning("item_error", run=run_id, case=item.case_id, system=item.system,
                    run_idx=item.run_idx, error=str(exc))  # fmt: skip
        _fail_item(factory, item.id, exc)


APPROX_COUNTER = "approx-test-only"


def _counter_name() -> str:
    from gbya.llm.tokens import TokenizerUnavailable, default_counter

    try:
        return default_counter().name
    except TokenizerUnavailable:
        return "unavailable"


def refuse_research_tooling(backend: str | None, counter_name: str) -> None:
    """Research runs need the live model and the model's own tokenizer (§D.10.3, §L.4 item 7)."""
    from gbya.experiments.runs import ResearchRunRefused

    if backend in ("fake", "replay"):
        raise ResearchRunRefused(f"A {backend} LLM backend can never produce research runs")
    if counter_name == APPROX_COUNTER or not counter_name.startswith("model:"):
        raise ResearchRunRefused(
            f"Research runs need the model tokenizer; the token counter is {counter_name!r}",
            hint="Run outside GBYA_ENV=test with the model files present",
        )


def execute_run(
    factory: sessionmaker[Session],
    run_id: int,
    deps: Deps,
    *,
    should_stop: Callable[[], bool] = lambda: False,
    after_item: Callable[[], None] = lambda: None,
) -> str:
    """Run every unfinished unit of ``run_id``; returns the run's final status."""
    with session_scope(factory) as s:
        run = s.get(Run, run_id)
        if run is None:
            raise NotFound(f"No run {run_id}", code="RUN_NOT_FOUND")
        if run.purpose == "research":  # checked again where the work happens
            refuse_research_tooling(run.backend, deps.counter.name)
        run.status = "running"
        run.started_at = run.started_at or utcnow()
        ensure_items(s, run)
    with session_scope(factory) as s:
        pending = select(JobItem).where(
            JobItem.run_id == run_id, JobItem.status != "done", JobItem.attempts < MAX_ATTEMPTS
        )
        todo = list(s.scalars(pending.order_by(JobItem.id)))
    cases = _CaseCache(factory, deps.settings)
    v_items = [i for i in todo if i.system.startswith(V_PREFIX)]
    g_items = [i for i in todo if not i.system.startswith(V_PREFIX)]
    stopped = False

    def one(item: JobItem) -> None:
        nonlocal stopped
        if stopped or should_stop():
            stopped = True
            return
        run_item(factory, deps, cases, run_id, item)
        after_item()

    with ThreadPoolExecutor(max_workers=max(1, deps.settings.worker_concurrency)) as pool:
        list(pool.map(one, v_items))
    for item in g_items:
        one(item)

    with session_scope(factory) as s:
        run = s.get(Run, run_id)
        assert run is not None
        if stopped or run.status == "cancelled":
            run.status = "cancelled"
        else:
            p = progress(s, run_id)
            run.status = "completed" if p["done"] == p["total"] else "failed"
            run.finished_at = utcnow()
        return run.status


def progress(session: Session, run_id: int) -> dict[str, Any]:
    rows = session.execute(select(JobItem.status, func.count()).where(JobItem.run_id == run_id)
                           .group_by(JobItem.status)).all()  # fmt: skip
    counts: dict[str, int] = {str(st): int(n) for st, n in rows}
    run = session.get(Run, run_id)
    if run is None:
        raise NotFound(f"No run {run_id}", code="RUN_NOT_FOUND")
    total = sum(counts.values()) or len(plan_items(Exp1Spec.model_validate(run.config["spec"]),
                                                   run.config["case_ids"]))  # fmt: skip
    done = counts.get("done", 0)
    tok = session.execute(select(func.coalesce(func.sum(VerifierEval.tokens_in), 0),
                                 func.coalesce(func.sum(VerifierEval.tokens_out), 0))
                          .where(VerifierEval.run_id == run_id)).one()  # fmt: skip
    elapsed = None
    if run.started_at is not None:
        end = run.finished_at or utcnow()
        elapsed = max((_aware(end) - _aware(run.started_at)).total_seconds(), 1e-9)
    eta = None
    if elapsed and done and run.status == "running":
        eta = round(elapsed / done * (total - done), 1)
    return {
        "run_id": run_id, "status": run.status, "done": done, "total": total,
        "errors": counts.get("error", 0), "running": counts.get("running", 0), "eta_s": eta,
        "tps_in": round(tok[0] / elapsed, 1) if elapsed else None,
        "tps_out": round(tok[1] / elapsed, 1) if elapsed else None,
    }  # fmt: skip


def _aware(t: datetime) -> datetime:
    from datetime import UTC

    return t if t.tzinfo else t.replace(tzinfo=UTC)


# ---- jobs (claimed by the worker) ---------------------------------------------------------------


def claim_job(factory: sessionmaker[Session], worker_id: str, stale_s: int) -> Job | None:
    """Claim the oldest queued job, or a running one whose heartbeat is stale. One UPDATE ...
    RETURNING statement, which SQLite executes under its write lock (no two workers can win)."""
    from datetime import timedelta

    now = utcnow()
    stale = now - timedelta(seconds=stale_s)
    pick = (select(Job.id).where(or_(Job.status == "queued", (Job.status == "running") & or_(
        Job.heartbeat_at.is_(None), Job.heartbeat_at < stale))).order_by(Job.id).limit(1)
        .scalar_subquery())  # fmt: skip
    with session_scope(factory) as s:
        row = s.execute(update(Job).where(Job.id == pick)
                        .values(status="running", claimed_by=worker_id, heartbeat_at=now)
                        .returning(Job.id)).first()  # fmt: skip
        if row is None:
            return None
        return s.get(Job, row[0])


def heartbeat(factory: sessionmaker[Session], job_id: int, worker_id: str) -> bool:
    """Refresh the job's heartbeat; False if another worker has taken the job over."""
    with session_scope(factory) as s:
        res = s.execute(update(Job).where(Job.id == job_id, Job.claimed_by == worker_id)
                        .values(heartbeat_at=utcnow()))  # fmt: skip
        return bool(res.rowcount)  # type: ignore[attr-defined]


def finish_job(factory: sessionmaker[Session], job_id: int, status: str) -> None:
    with session_scope(factory) as s:
        job = s.get(Job, job_id)
        if job is not None:
            job.status = status
