"""0006_agreement_snapshots: agreement before adjudication (T4.8).

One row per scenario, written when the second annotator submits; never rewritten.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agreement_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("scenario_id", sa.String(), sa.ForeignKey("scenarios.id"), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.UniqueConstraint("scenario_id", name=op.f("uq_agreement_snapshots_scenario_id")),
    )


def downgrade() -> None:
    op.drop_table("agreement_snapshots")
