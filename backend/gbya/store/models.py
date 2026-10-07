"""SQLAlchemy models for ``app.db`` (plan §F.1).

Column names follow §F.1. CHECK constraints are added only where the plan enumerates the
allowed values. Columns beyond §F.1 are marked with the plan section that requires them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def _in(column: str, values: list[str] | list[int], *, nullable: bool = False) -> str:
    items = ", ".join(repr(v) if isinstance(v, str) else str(v) for v in values)
    expr = f"{column} IN ({items})"
    return f"{column} IS NULL OR {expr}" if nullable else expr


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSON, list[Any]: JSON}


SPLITS = ["dev", "test", "e2e", "unused"]
SCENARIO_STATUS = ["draft", "annotating", "adjudicated", "frozen"]
RUN_STATUS = ["queued", "running", "completed", "cancelled", "failed"]  # §F.7
EPISODE_STATUS = ["running", "done", "error"]
TERMINAL_STATES = ["fulfilled", "handed_off", "ended", "cap"]  # §D.10.1 T1-T4
OUTCOMES = [
    "safe_completion",
    "justified_escalation",
    "unnecessary_deferral",
    "unsafe_execution",
    "refusal",
    "pending_adjudication",
]  # §D.9
STEP_KINDS = ["llm", "tool", "gate", "approval", "feedback"]
CALL_CLASSES = ["permitted", "prohibited", "unapproved", "unlisted"]  # §D.9 step A
ESCALATION_CLASSES = ["qualifying", "unlisted", "invalid"]  # §D.9 step B
GATE_VERDICTS = [
    "admitted",
    "rejected_retryable",
    "rejected",
    "insufficient",
    "blocked",
    "converted_to_approval",
]  # §D.6.1
VERIFIER_VARIANTS = ["standard", "rationale", "none", "rerank"]  # §D.7.2
RETRIEVAL_MODES = ["none", "bm25", "bm25_rerank"]  # §D.3


class Window(Base):
    __tablename__ = "windows"
    __table_args__ = (CheckConstraint(_in("split", SPLITS, nullable=True), name="split"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)  # SDWIN id
    title: Mapped[str] = mapped_column(String)
    techniques: Mapped[list[Any]] = mapped_column(default=list)
    tactics: Mapped[list[Any]] = mapped_column(default=list)
    hosts: Mapped[list[Any]] = mapped_column(default=list)
    event_count: Mapped[int | None] = mapped_column(Integer)
    duckdb_path: Mapped[str | None] = mapped_column(String)
    split: Mapped[str | None] = mapped_column(String)
    dedup_group: Mapped[str | None] = mapped_column(String)
    ingest_status: Mapped[str | None] = mapped_column(String)


class Scenario(Base):
    __tablename__ = "scenarios"
    __table_args__ = (
        CheckConstraint(_in("split", SPLITS, nullable=True), name="split"),
        CheckConstraint(_in("status", SCENARIO_STATUS), name="status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    window_id: Mapped[str] = mapped_column(ForeignKey("windows.id"))
    split: Mapped[str | None] = mapped_column(String)
    target_host: Mapped[str | None] = mapped_column(String)
    request_template: Mapped[dict[str, Any] | None] = mapped_column()
    trusted_context: Mapped[dict[str, Any] | None] = mapped_column()
    context_hash: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (CheckConstraint(_in('"set"', ["R", "E"]), name="set"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)  # "<scenario>:<variant>"
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"), index=True)
    set_: Mapped[str] = mapped_column("set", String)
    variant: Mapped[str] = mapped_column(String)
    request: Mapped[dict[str, Any]] = mapped_column()
    package: Mapped[dict[str, Any]] = mapped_column()
    db_patch: Mapped[dict[str, Any] | None] = mapped_column()
    case_db_path: Mapped[str | None] = mapped_column(String)
    approval_script: Mapped[dict[str, Any] | None] = mapped_column()
    labels: Mapped[dict[str, Any] | None] = mapped_column()
    content_hash: Mapped[str | None] = mapped_column(String)


class Annotation(Base):
    __tablename__ = "annotations"
    __table_args__ = (
        UniqueConstraint("scenario_id", "annotator_role"),
        CheckConstraint(_in("annotator_role", ["A", "B"]), name="annotator_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"))
    annotator_role: Mapped[str] = mapped_column(String)
    labels: Mapped[dict[str, Any]] = mapped_column()
    evidence_sets: Mapped[dict[str, Any] | None] = mapped_column()
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Adjudication(Base):
    __tablename__ = "adjudications"
    __table_args__ = (
        CheckConstraint(_in("kind", ["scenario_label", "unlisted_call"]), name="kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String)
    ref_id: Mapped[str] = mapped_column(String, index=True)
    decision: Mapped[dict[str, Any]] = mapped_column()
    by: Mapped[str] = mapped_column(String)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(_in("experiment", [1, 2, 3]), name="experiment"),
        CheckConstraint(_in("status", RUN_STATUS), name="status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column()
    config_hash: Mapped[str] = mapped_column(String)
    case_set_hash: Mapped[str | None] = mapped_column(String)
    git_sha: Mapped[str | None] = mapped_column(String)
    model_id: Mapped[str | None] = mapped_column(String)
    model_file_sha256: Mapped[str | None] = mapped_column(String)
    backend: Mapped[str | None] = mapped_column(String)
    backend_flags: Mapped[dict[str, Any] | None] = mapped_column()
    replay: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String, default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    status: Mapped[str] = mapped_column(String, default="queued")
    claimed_by: Mapped[str | None] = mapped_column(String)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobItem(Base):
    __tablename__ = "job_items"
    # Idempotency key (§F.1, FR-25): a restarted worker can never write a work unit twice.
    __table_args__ = (UniqueConstraint("run_id", "case_id", "system", "run_idx"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    case_id: Mapped[str] = mapped_column(String)
    system: Mapped[str] = mapped_column(String)
    run_idx: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class Episode(Base):
    __tablename__ = "episodes"
    __table_args__ = (
        CheckConstraint(_in("status", EPISODE_STATUS), name="status"),
        CheckConstraint(_in("terminal_state", TERMINAL_STATES, nullable=True), name="terminal"),
        CheckConstraint(_in("outcome", OUTCOMES, nullable=True), name="outcome"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"), index=True)
    system: Mapped[str] = mapped_column(String)
    run_idx: Mapped[int] = mapped_column(Integer)
    temperature: Mapped[float | None] = mapped_column(Float)
    seed: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="running")
    terminal_state: Mapped[str | None] = mapped_column(String)
    approvals: Mapped[dict[str, Any]] = mapped_column(default=dict)
    retrieved: Mapped[list[Any]] = mapped_column(default=list)
    outcome: Mapped[str | None] = mapped_column(String)
    outcome_detail: Mapped[dict[str, Any] | None] = mapped_column()
    budget_exhausted: Mapped[bool] = mapped_column(Boolean, default=False)
    unqualified_escalation: Mapped[bool] = mapped_column(Boolean, default=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)


class GateDecisionRow(Base):
    __tablename__ = "gate_decisions"
    __table_args__ = (CheckConstraint(_in("verdict", GATE_VERDICTS), name="verdict"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    system: Mapped[str] = mapped_column(String)
    run_idx: Mapped[int] = mapped_column(Integer)
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("episodes.id"), index=True)
    call: Mapped[dict[str, Any]] = mapped_column()
    cited: Mapped[list[Any]] = mapped_column(default=list)
    checks: Mapped[list[Any]] = mapped_column(default=list)
    verdict: Mapped[str] = mapped_column(String)
    verifier: Mapped[dict[str, Any] | None] = mapped_column()
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    ms: Mapped[float | None] = mapped_column(Float)


class Step(Base):
    """Append-only episode trace."""

    __tablename__ = "steps"
    __table_args__ = (CheckConstraint(_in("kind", STEP_KINDS), name="kind"),)

    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    payload: Mapped[dict[str, Any]] = mapped_column()
    ms: Mapped[float | None] = mapped_column(Float)


class ToolCall(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        CheckConstraint(_in("call_class", CALL_CLASSES, nullable=True), name="call_class"),
        CheckConstraint(
            _in("escalation_class", ESCALATION_CLASSES, nullable=True), name="escalation_class"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    tool: Mapped[str] = mapped_column(String)
    class_: Mapped[str] = mapped_column("class", String)
    args: Mapped[dict[str, Any]] = mapped_column()
    cited: Mapped[list[Any]] = mapped_column(default=list)
    call_class: Mapped[str | None] = mapped_column(String)
    escalation_class: Mapped[str | None] = mapped_column(String)
    admitted: Mapped[bool] = mapped_column(Boolean, default=False)
    gate_decision_id: Mapped[int | None] = mapped_column(ForeignKey("gate_decisions.id"))


class VerifierEval(Base):
    """Exp 1V diagnostic verifier pass (§D.7.2)."""

    __tablename__ = "verifier_evals"
    __table_args__ = (
        UniqueConstraint("run_id", "case_id", "variant", "run_idx"),
        CheckConstraint(_in("variant", VERIFIER_VARIANTS), name="variant"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"))
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    variant: Mapped[str] = mapped_column(String)
    run_idx: Mapped[int] = mapped_column(Integer)
    verdict: Mapped[str | None] = mapped_column(String)  # NULL when the output did not parse
    output: Mapped[dict[str, Any] | None] = mapped_column()
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    # §D.7.1: the rendering manifest and a hash of the rendered prompt are stored with every row.
    manifest: Mapped[dict[str, Any] | None] = mapped_column()
    prompt_hash: Mapped[str | None] = mapped_column(String)


class RetrievalRanking(Base):
    """Full candidate rankings, so every retrieval metric can be recomputed (§D.3)."""

    __tablename__ = "retrieval_rankings"
    __table_args__ = (
        CheckConstraint("case_id IS NOT NULL OR episode_id IS NOT NULL", name="owner"),
        CheckConstraint(_in("mode", RETRIEVAL_MODES), name="mode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"), index=True)
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("episodes.id"), index=True)
    mode: Mapped[str] = mapped_column(String)
    query_hash: Mapped[str] = mapped_column(String)
    sigma_ranking: Mapped[list[Any]] = mapped_column(default=list)  # <=20, with scores
    attack_ranking: Mapped[list[Any]] = mapped_column(default=list)  # <=10, with scores
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
