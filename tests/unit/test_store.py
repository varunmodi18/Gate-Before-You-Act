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
    "retrieval_rankings",
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
