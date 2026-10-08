"""0002_run_purpose: runs.purpose (research / development / fixture / demo).

Team decision of 8 Oct 2026: runs on the hand-made fixture are tagged so that no result produced
on it can be stored as a research result; only "research" runs on the frozen case set count.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("purpose", sa.String(), server_default="development", nullable=False)
        )
        batch_op.create_check_constraint(
            op.f("ck_runs_purpose"),
            "purpose IN ('research', 'development', 'fixture', 'demo')",
        )


def downgrade() -> None:
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("ck_runs_purpose"), type_="check")
        batch_op.drop_column("purpose")
