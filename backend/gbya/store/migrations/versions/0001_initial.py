"""0001_initial: the app.db tables of plan §F.1 (T0.2).

Revision ID: 0001
Revises:
Create Date: 2026-10-08 02:18:10.020435
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "adjudications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("ref_id", sa.String(), nullable=False),
        sa.Column("decision", sa.JSON(), nullable=False),
        sa.Column("by", sa.String(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('scenario_label', 'unlisted_call')", name=op.f("ck_adjudications_kind")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_adjudications")),
    )
    with op.batch_alter_table("adjudications", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_adjudications_ref_id"), ["ref_id"], unique=False)

    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("experiment", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("config_hash", sa.String(), nullable=False),
        sa.Column("case_set_hash", sa.String(), nullable=True),
        sa.Column("git_sha", sa.String(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("model_file_sha256", sa.String(), nullable=True),
        sa.Column("backend", sa.String(), nullable=True),
        sa.Column("backend_flags", sa.JSON(), nullable=True),
        sa.Column("replay", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'cancelled', 'failed')",
            name=op.f("ck_runs_status"),
        ),
        sa.CheckConstraint("experiment IN (1, 2, 3)", name=op.f("ck_runs_experiment")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
    )
    op.create_table(
        "windows",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("techniques", sa.JSON(), nullable=False),
        sa.Column("tactics", sa.JSON(), nullable=False),
        sa.Column("hosts", sa.JSON(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=True),
        sa.Column("duckdb_path", sa.String(), nullable=True),
        sa.Column("split", sa.String(), nullable=True),
        sa.Column("dedup_group", sa.String(), nullable=True),
        sa.Column("ingest_status", sa.String(), nullable=True),
        sa.CheckConstraint(
            "split IS NULL OR split IN ('dev', 'test', 'e2e', 'unused')",
            name=op.f("ck_windows_split"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_windows")),
    )
    op.create_table(
        "job_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("system", sa.String(), nullable=False),
        sa.Column("run_idx", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_job_items_run_id_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_items")),
        sa.UniqueConstraint(
            "run_id",
            "case_id",
            "system",
            "run_idx",
            name=op.f("uq_job_items_run_id_case_id_system_run_idx"),
        ),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("claimed_by", sa.String(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_jobs_run_id_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_jobs_run_id"), ["run_id"], unique=False)

    op.create_table(
        "scenarios",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("window_id", sa.String(), nullable=False),
        sa.Column("split", sa.String(), nullable=True),
        sa.Column("target_host", sa.String(), nullable=True),
        sa.Column("request_template", sa.JSON(), nullable=True),
        sa.Column("trusted_context", sa.JSON(), nullable=True),
        sa.Column("context_hash", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "split IS NULL OR split IN ('dev', 'test', 'e2e', 'unused')",
            name=op.f("ck_scenarios_split"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'annotating', 'adjudicated', 'frozen')",
            name=op.f("ck_scenarios_status"),
        ),
        sa.ForeignKeyConstraint(
            ["window_id"], ["windows.id"], name=op.f("fk_scenarios_window_id_windows")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenarios")),
    )
    op.create_table(
        "annotations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scenario_id", sa.String(), nullable=False),
        sa.Column("annotator_role", sa.String(), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("evidence_sets", sa.JSON(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "annotator_role IN ('A', 'B')", name=op.f("ck_annotations_annotator_role")
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenarios.id"], name=op.f("fk_annotations_scenario_id_scenarios")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_annotations")),
        sa.UniqueConstraint(
            "scenario_id", "annotator_role", name=op.f("uq_annotations_scenario_id_annotator_role")
        ),
    )
    op.create_table(
        "cases",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("scenario_id", sa.String(), nullable=False),
        sa.Column("set", sa.String(), nullable=False),
        sa.Column("variant", sa.String(), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("package", sa.JSON(), nullable=False),
        sa.Column("db_patch", sa.JSON(), nullable=True),
        sa.Column("case_db_path", sa.String(), nullable=True),
        sa.Column("approval_script", sa.JSON(), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.CheckConstraint("\"set\" IN ('R', 'E')", name=op.f("ck_cases_set")),
        sa.ForeignKeyConstraint(
            ["scenario_id"], ["scenarios.id"], name=op.f("fk_cases_scenario_id_scenarios")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cases")),
    )
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_cases_scenario_id"), ["scenario_id"], unique=False)

    op.create_table(
        "episodes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("case_id", sa.String(), nullable=True),
        sa.Column("system", sa.String(), nullable=False),
        sa.Column("run_idx", sa.Integer(), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("terminal_state", sa.String(), nullable=True),
        sa.Column("approvals", sa.JSON(), nullable=False),
        sa.Column("retrieved", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("outcome_detail", sa.JSON(), nullable=True),
        sa.Column("budget_exhausted", sa.Boolean(), nullable=False),
        sa.Column("unqualified_escalation", sa.Boolean(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('safe_completion', 'justified_escalation', 'unnecessary_deferral', 'unsafe_execution', 'refusal', 'pending_adjudication')",
            name=op.f("ck_episodes_outcome"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'done', 'error')", name=op.f("ck_episodes_status")
        ),
        sa.CheckConstraint(
            "terminal_state IS NULL OR terminal_state IN ('fulfilled', 'handed_off', 'ended', 'cap')",
            name=op.f("ck_episodes_terminal"),
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name=op.f("fk_episodes_case_id_cases")),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_episodes_run_id_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_episodes")),
    )
    with op.batch_alter_table("episodes", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_episodes_case_id"), ["case_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_episodes_run_id"), ["run_id"], unique=False)

    op.create_table(
        "verifier_evals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("variant", sa.String(), nullable=False),
        sa.Column("run_idx", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=True),
        sa.Column("prompt_hash", sa.String(), nullable=True),
        sa.CheckConstraint(
            "variant IN ('standard', 'rationale', 'none', 'rerank')",
            name=op.f("ck_verifier_evals_variant"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_verifier_evals_case_id_cases")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_verifier_evals_run_id_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verifier_evals")),
        sa.UniqueConstraint(
            "run_id",
            "case_id",
            "variant",
            "run_idx",
            name=op.f("uq_verifier_evals_run_id_case_id_variant_run_idx"),
        ),
    )
    with op.batch_alter_table("verifier_evals", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_verifier_evals_case_id"), ["case_id"], unique=False)

    op.create_table(
        "gate_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("system", sa.String(), nullable=False),
        sa.Column("run_idx", sa.Integer(), nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=True),
        sa.Column("call", sa.JSON(), nullable=False),
        sa.Column("cited", sa.JSON(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("verdict", sa.String(), nullable=False),
        sa.Column("verifier", sa.JSON(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("ms", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "verdict IN ('admitted', 'rejected_retryable', 'rejected', 'insufficient', 'blocked', 'converted_to_approval')",
            name=op.f("ck_gate_decisions_verdict"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_gate_decisions_case_id_cases")
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"], ["episodes.id"], name=op.f("fk_gate_decisions_episode_id_episodes")
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_gate_decisions_run_id_runs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gate_decisions")),
    )
    with op.batch_alter_table("gate_decisions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_gate_decisions_case_id"), ["case_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_gate_decisions_episode_id"), ["episode_id"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_gate_decisions_run_id"), ["run_id"], unique=False)

    op.create_table(
        "retrieval_rankings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.String(), nullable=True),
        sa.Column("episode_id", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("query_hash", sa.String(), nullable=False),
        sa.Column("sigma_ranking", sa.JSON(), nullable=False),
        sa.Column("attack_ranking", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mode IN ('none', 'bm25', 'bm25_rerank')", name=op.f("ck_retrieval_rankings_mode")
        ),
        sa.CheckConstraint(
            "case_id IS NOT NULL OR episode_id IS NOT NULL",
            name=op.f("ck_retrieval_rankings_owner"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_retrieval_rankings_case_id_cases")
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"], ["episodes.id"], name=op.f("fk_retrieval_rankings_episode_id_episodes")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_retrieval_rankings")),
    )
    with op.batch_alter_table("retrieval_rankings", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_retrieval_rankings_case_id"), ["case_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_retrieval_rankings_episode_id"), ["episode_id"], unique=False
        )

    op.create_table(
        "steps",
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("ms", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('llm', 'tool', 'gate', 'approval', 'feedback')", name=op.f("ck_steps_kind")
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"], ["episodes.id"], name=op.f("fk_steps_episode_id_episodes")
        ),
        sa.PrimaryKeyConstraint("episode_id", "idx", name=op.f("pk_steps")),
    )
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("episode_id", sa.Integer(), nullable=False),
        sa.Column("tool", sa.String(), nullable=False),
        sa.Column("class", sa.String(), nullable=False),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("cited", sa.JSON(), nullable=False),
        sa.Column("call_class", sa.String(), nullable=True),
        sa.Column("escalation_class", sa.String(), nullable=True),
        sa.Column("admitted", sa.Boolean(), nullable=False),
        sa.Column("gate_decision_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "call_class IS NULL OR call_class IN ('permitted', 'prohibited', 'unapproved', 'unlisted')",
            name=op.f("ck_tool_calls_call_class"),
        ),
        sa.CheckConstraint(
            "escalation_class IS NULL OR escalation_class IN ('qualifying', 'unlisted', 'invalid')",
            name=op.f("ck_tool_calls_escalation_class"),
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"], ["episodes.id"], name=op.f("fk_tool_calls_episode_id_episodes")
        ),
        sa.ForeignKeyConstraint(
            ["gate_decision_id"],
            ["gate_decisions.id"],
            name=op.f("fk_tool_calls_gate_decision_id_gate_decisions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_calls")),
    )
    with op.batch_alter_table("tool_calls", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_tool_calls_episode_id"), ["episode_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("tool_calls", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tool_calls_episode_id"))

    op.drop_table("tool_calls")
    op.drop_table("steps")
    with op.batch_alter_table("retrieval_rankings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_retrieval_rankings_episode_id"))
        batch_op.drop_index(batch_op.f("ix_retrieval_rankings_case_id"))

    op.drop_table("retrieval_rankings")
    with op.batch_alter_table("gate_decisions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_gate_decisions_run_id"))
        batch_op.drop_index(batch_op.f("ix_gate_decisions_episode_id"))
        batch_op.drop_index(batch_op.f("ix_gate_decisions_case_id"))

    op.drop_table("gate_decisions")
    with op.batch_alter_table("verifier_evals", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_verifier_evals_case_id"))

    op.drop_table("verifier_evals")
    with op.batch_alter_table("episodes", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_episodes_run_id"))
        batch_op.drop_index(batch_op.f("ix_episodes_case_id"))

    op.drop_table("episodes")
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cases_scenario_id"))

    op.drop_table("cases")
    op.drop_table("annotations")
    op.drop_table("scenarios")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_jobs_run_id"))

    op.drop_table("jobs")
    op.drop_table("job_items")
    op.drop_table("windows")
    op.drop_table("runs")
    with op.batch_alter_table("adjudications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_adjudications_ref_id"))

    op.drop_table("adjudications")
