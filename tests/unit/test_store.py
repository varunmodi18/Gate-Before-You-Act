"""T0.2: migrations up/down, schema matches the models, WAL mode, unique keys."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from gbya.store import db
from gbya.store.models import (
    Annotation,
    Base,
    Case,
    Episode,
    JobItem,
    RetrievalRanking,
    Run,
    Scenario,
    VerifierEval,
    Window,
)

F1_TABLES = {
    "windows",
    "scenarios",
    "cases",
    "annotations",
    "adjudications",
    "runs",
    "jobs",
    "job_items",
    "gate_decisions",
    "episodes",
    "steps",
    "tool_calls",
    "verifier_evals",
    "agreement_snapshots",
    "retrieval_rankings",
    "trace_tags",
}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "app.db"
    db.upgrade(path)
    return path


def _tables(path: Path) -> set[str]:
    engine = db.make_engine(path)
    try:
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def test_upgrade_creates_all_f1_tables(db_path: Path) -> None:
    assert _tables(db_path) == F1_TABLES


def test_downgrade_then_upgrade_round_trip(db_path: Path) -> None:
    db.downgrade(db_path, "base")
    assert _tables(db_path) == set()
    db.upgrade(db_path)
    assert _tables(db_path) == F1_TABLES


def test_migration_matches_models(db_path: Path) -> None:
    """The migrated schema and the SQLAlchemy models must not drift apart."""
    engine = db.make_engine(db_path)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


def test_wal_and_foreign_keys_enabled(db_path: Path) -> None:
    engine = db.make_engine(db_path)
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    engine.dispose()


def _seed(path: Path) -> tuple[db.sessionmaker[db.Session], int]:
    factory = db.make_sessionmaker(db.make_engine(path))
    with db.session_scope(factory) as s:
        # No ORM relationships are declared, so parents are flushed before children.
        s.add(Window(id="SDWIN-1", title="t"))
        s.flush()
        s.add(Scenario(id="s001", window_id="SDWIN-1"))
        s.flush()
        s.add(
            Case(id="s001:E1", scenario_id="s001", set_="E", variant="E1", request={}, package={})
        )
        run = Run(experiment=1, config={}, config_hash="h")
        s.add(run)
        s.flush()
        run_id = run.id
    return factory, run_id


def test_job_items_idempotency_key(db_path: Path) -> None:
    factory, run_id = _seed(db_path)
    item = {"run_id": run_id, "case_id": "s001:E1", "system": "G1", "run_idx": 1}
    with db.session_scope(factory) as s:
        s.add(JobItem(**item))
    with pytest.raises(IntegrityError), db.session_scope(factory) as s:
        s.add(JobItem(**item))
    # A different run index is a different work unit.
    with db.session_scope(factory) as s:
        s.add(JobItem(**{**item, "run_idx": 2}))
        assert s.query(JobItem).count() == 2


def test_verifier_evals_unique(db_path: Path) -> None:
    factory, run_id = _seed(db_path)
    row = {"run_id": run_id, "case_id": "s001:E1", "variant": "standard", "run_idx": 1}
    with db.session_scope(factory) as s:
        s.add(VerifierEval(**row))
    with pytest.raises(IntegrityError), db.session_scope(factory) as s:
        s.add(VerifierEval(**row))


def test_one_annotation_per_role(db_path: Path) -> None:
    factory, _ = _seed(db_path)
    with db.session_scope(factory) as s:
        s.add(Annotation(scenario_id="s001", annotator_role="A", labels={}))
    with pytest.raises(IntegrityError), db.session_scope(factory) as s:
        s.add(Annotation(scenario_id="s001", annotator_role="A", labels={}))


@pytest.mark.parametrize(
    "bad",
    [
        Run(experiment=4, config={}, config_hash="h"),
        Run(experiment=1, config={}, config_hash="h", status="done"),
        Window(id="SDWIN-2", title="t", split="holdout"),
        RetrievalRanking(mode="bm25", query_hash="q"),  # neither case nor episode
    ],
)
def test_check_constraints_reject_values_outside_the_plan(db_path: Path, bad: Base) -> None:
    factory, _ = _seed(db_path)
    with pytest.raises(IntegrityError), db.session_scope(factory) as s:
        s.add(bad)


def test_foreign_key_enforced(db_path: Path) -> None:
    factory = db.make_sessionmaker(db.make_engine(db_path))
    with pytest.raises(IntegrityError), db.session_scope(factory) as s:
        s.add(Scenario(id="s002", window_id="no-such-window"))


def test_table_rebuilding_migrations_keep_referencing_rows(tmp_path: Path) -> None:
    """0010 rebuilds ``cases`` (batch mode); with episodes referencing a case, the upgrade must
    not fail on the foreign key and the rows must survive (found on the populated demo app.db)."""
    path = tmp_path / "app.db"
    db.upgrade(path, "0009")
    factory = db.make_sessionmaker(db.make_engine(path))  # 0010 changes constraints only
    with db.session_scope(factory) as s:
        s.add(Window(id="W", title="t"))
        s.flush()
        s.add(Scenario(id="s", window_id="W"))
        s.flush()
        s.add(Case(id="s:E1", scenario_id="s", set_="E", variant="E1", request={}, package={}))
        s.flush()
        s.add(Episode(case_id="s:E1", system="G3", run_idx=1, status="done"))
    engine = factory.kw["bind"]
    engine.dispose()
    db.upgrade(path)
    engine = db.make_engine(path)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT case_id FROM episodes")).scalar() == "s:E1"
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
    engine.dispose()


def test_a_failed_migration_leaves_nothing_behind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch
                                                  ) -> None:  # fmt: skip
    """Migrations are transactional: a failure inside 0010's table rebuild rolls back the
    half-built ``_alembic_tmp_cases`` and the version stays at 0009."""
    from alembic.operations import Operations

    path = tmp_path / "app.db"
    db.upgrade(path, "0009")
    real = Operations.batch_alter_table
    calls: list[str] = []

    def failing(self: Operations, table: str, *a: object, **kw: object) -> object:
        calls.append(table)
        if table == "adjudications":  # after "cases" was rebuilt
            raise RuntimeError("boom")
        return real(self, table, *a, **kw)

    monkeypatch.setattr(Operations, "batch_alter_table", failing)
    with pytest.raises(RuntimeError, match="boom"):
        db.upgrade(path)
    assert calls == ["cases", "adjudications"]
    engine = db.make_engine(path)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0009"
        names = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master"))}
        assert not any(n.startswith("_alembic_tmp") for n in names)
        ddl = conn.execute(text("SELECT sql FROM sqlite_master WHERE name = 'cases'")).scalar()
        assert "'X'" not in str(ddl)  # the rebuilt "cases" was rolled back too
    engine.dispose()
