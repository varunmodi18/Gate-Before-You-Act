"""0004_ranking_provenance: retrieval_rankings.index_sha256 and .reranker (T3.7).

A cached ranking is reused only for the same index content and reranker revision, so a rebuilt
index or another reranker can never be served from a stale cache.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("retrieval_rankings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("index_sha256", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("reranker", sa.String(), nullable=True))
        batch_op.create_index(
            "ix_retrieval_rankings_lookup", ["case_id", "mode", "query_hash", "index_sha256"]
        )


def downgrade() -> None:
    with op.batch_alter_table("retrieval_rankings", schema=None) as batch_op:
        batch_op.drop_index("ix_retrieval_rankings_lookup")
        batch_op.drop_column("reranker")
        batch_op.drop_column("index_sha256")
