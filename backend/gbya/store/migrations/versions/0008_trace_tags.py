"""0008_trace_tags: error-taxonomy tags and the "annotated trace" star per episode (T5.6, T7.5).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trace_tags",
        sa.Column("episode_id", sa.Integer(), sa.ForeignKey("episodes.id"), primary_key=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("starred", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("trace_tags")
