"""0007_steps_append_only: episode steps can be added, never changed or removed (T5.2).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TRIGGER steps_no_update BEFORE UPDATE ON steps "
        "BEGIN SELECT RAISE(ABORT, 'steps are append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER steps_no_delete BEFORE DELETE ON steps "
        "BEGIN SELECT RAISE(ABORT, 'steps are append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS steps_no_delete")
    op.execute("DROP TRIGGER IF EXISTS steps_no_update")
