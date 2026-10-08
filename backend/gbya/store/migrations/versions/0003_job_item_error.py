"""0003_job_item_error: job_items.error and job_items.updated_at (T3.5).

A failed work unit keeps the reason it failed, and progress reporting can tell stalled items apart.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("job_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("error", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("job_items", schema=None) as batch_op:
        batch_op.drop_column("updated_at")
        batch_op.drop_column("error")
