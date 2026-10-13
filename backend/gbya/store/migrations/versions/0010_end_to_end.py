"""0010_end_to_end: end-to-end cases (set X) and incidental-technique adjudications (M6, A-14, A-15).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("cases") as batch:
        batch.drop_constraint(op.f("ck_cases_set"), type_="check")
        batch.create_check_constraint(op.f("ck_cases_set"), "\"set\" IN ('R', 'E', 'X')")
    with op.batch_alter_table("adjudications") as batch:
        batch.drop_constraint(op.f("ck_adjudications_kind"), type_="check")
        batch.create_check_constraint(
            op.f("ck_adjudications_kind"),
            "kind IN ('scenario_label', 'unlisted_call', 'incidental_technique')",
        )


def downgrade() -> None:
    with op.batch_alter_table("adjudications") as batch:
        batch.drop_constraint(op.f("ck_adjudications_kind"), type_="check")
        batch.create_check_constraint(
            op.f("ck_adjudications_kind"), "kind IN ('scenario_label', 'unlisted_call')"
        )
    with op.batch_alter_table("cases") as batch:
        batch.drop_constraint(op.f("ck_cases_set"), type_="check")
        batch.create_check_constraint(op.f("ck_cases_set"), "\"set\" IN ('R', 'E')")
