"""Alembic environment for ``app.db``. Uses the WAL/foreign-key engine from ``gbya.store.db``."""

from __future__ import annotations

from pathlib import Path

from alembic import context
from sqlalchemy.engine import make_url

from gbya.store.db import make_engine
from gbya.store.models import Base

config = context.config
target_metadata = Base.metadata


def _db_path() -> Path:
    url = make_url(config.get_main_option("sqlalchemy.url") or "sqlite:///data/app.db")
    return Path(url.database or "data/app.db")


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(_db_path())
    with engine.connect() as connection:
        # Batch mode lets later migrations alter SQLite tables (copy-and-move).
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
