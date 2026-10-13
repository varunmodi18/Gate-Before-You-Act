"""Alembic environment for ``app.db``. Uses the WAL/foreign-key engine from ``gbya.store.db``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import context
from sqlalchemy import event
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

    # Batch mode rebuilds SQLite tables (copy, drop, rename). Two things make that safe:
    # (1) foreign keys are off while migrating, else dropping a table other rows reference fails
    # (SQLite's documented table-rebuild procedure; the result is checked before commit);
    # (2) DDL runs inside the transaction: pysqlite otherwise commits each DDL statement, so a
    # failed migration would leave half-built tables behind.
    @event.listens_for(engine, "connect")
    def _migrating(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.isolation_level = None  # SQLAlchemy emits BEGIN itself (below)
        dbapi_conn.execute("PRAGMA foreign_keys=OFF")

    @event.listens_for(engine, "begin")
    def _begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN")

    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
            broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise RuntimeError(f"migration left foreign-key violations: {broken[:5]}")
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
