"""0005_case_files: scenarios.spec and cases.r_edit (T4.1).

``scenarios.spec`` holds the scenario authoring file (``scenario.json``); ``cases.r_edit`` the one
field a Set R case changes (tier, approval script or toolset).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("scenarios", schema=None) as batch_op:
        batch_op.add_column(sa.Column("spec", sa.JSON(), nullable=True))
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.add_column(sa.Column("r_edit", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.drop_column("r_edit")
    with op.batch_alter_table("scenarios", schema=None) as batch_op:
        batch_op.drop_column("spec")
