"""SQLite ``app.db`` engine and sessions (plan §C.3, §F.1).

Every connection runs in WAL mode with foreign keys enforced and a busy timeout, so the API
and the worker can share the file. Schema changes go through Alembic only.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from gbya.config import REPO_ROOT, get_settings

ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


def default_db_path() -> Path:
    settings = get_settings()
    return settings.resolve(settings.app_db_path)


def _on_connect(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def make_engine(path: Path | None = None) -> Engine:
    path = path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(sqlite_url(path))
    event.listen(engine, "connect", _on_connect)
    return engine


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def alembic_config(path: Path | None = None) -> Any:
    """Alembic config pointing at ``path`` (default: the configured ``app.db``)."""
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", sqlite_url(path or default_db_path()))
    return cfg


def upgrade(path: Path | None = None, revision: str = "head") -> None:
    from alembic import command

    path = path or default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(path), revision)


def downgrade(path: Path | None = None, revision: str = "base") -> None:
    from alembic import command

    command.downgrade(alembic_config(path), revision)


if __name__ == "__main__":  # `make db`
    upgrade()
    print(f"app.db at {default_db_path()} is at the latest migration")
