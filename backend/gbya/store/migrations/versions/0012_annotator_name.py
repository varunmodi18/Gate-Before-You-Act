"""0012_annotator_name: who annotated (team lead, 14 Oct 2026: the author is annotator A; the
author is never annotator B or the adjudicator).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("annotations") as batch:
        batch.add_column(sa.Column("annotator_name", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("annotations") as batch:
        batch.drop_column("annotator_name")
