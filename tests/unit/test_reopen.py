"""Team-lead reopen of a submitted annotation (decision 2026-11-08): refusals and audit entry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gbya.cases.reopen import AUDIT_FILE, ReopenRefused, labels_hash, reopen_annotation
from gbya.config import Settings
from gbya.store import db
from gbya.store.models import Adjudication, Annotation, Case, Scenario, Window, utcnow

LABELS = {"E1": {"verifier_label": "SUPPORTS", "correct_outcome": "safe_completion"}}


@pytest.fixture
def env(tmp_path: Path) -> tuple[Settings, db.sessionmaker[db.Session]]:
    path = tmp_path / "app.db"
    db.upgrade(path)
    (tmp_path / "cases").mkdir()
    settings = Settings(app_db_path=path, data_dir=tmp_path, cases_dir=tmp_path / "cases",
                        log_dir=tmp_path / "logs")  # fmt: skip
    factory = db.make_sessionmaker(db.make_engine(path))
    with db.session_scope(factory) as s:
        s.add(Window(id="SDWIN-1", title="t"))
        s.flush()
        s.add(Scenario(id="s001", window_id="SDWIN-1", status="annotating"))
        s.flush()
        s.add(Case(id="s001:E1", scenario_id="s001", set_="E", variant="E1", request={},
                   package={}))  # fmt: skip
        s.add(Annotation(scenario_id="s001", annotator_role="A", annotator_name="agent-A",
                         labels=LABELS, submitted_at=utcnow()))  # fmt: skip
    return settings, factory


def _reopen(env: tuple[Settings, db.sessionmaker[db.Session]], **kw: str) -> dict[str, object]:
    settings, factory = env
    with db.session_scope(factory) as s:
        return reopen_annotation(s, settings, kw.get("sid", "s001"), kw.get("role", "A"),
                                 by=kw.get("by", "Varun Modi (team lead)"),
                                 reason=kw.get("reason", "E4 restored"))  # fmt: skip


def test_reopen_sets_draft_and_writes_the_audit_entry(env: tuple[Settings, db.sessionmaker[db.Session]]) -> None:  # fmt: skip
    entry = _reopen(env)
    settings, factory = env
    with db.session_scope(factory) as s:
        a = s.query(Annotation).filter_by(scenario_id="s001", annotator_role="A").one()
        assert a.submitted_at is None and a.labels == LABELS  # labels untouched
    log = json.loads((settings.cases_dir / AUDIT_FILE).read_text())
    assert log == [entry]
    assert entry["by"] == "Varun Modi (team lead)" and entry["scenario_id"] == "s001"
    assert entry["role"] == "A" and entry["reason"] == "E4 restored" and entry["at"]
    assert entry["annotator_name"] == "agent-A" and entry["submitted_at_before"]
    assert entry["labels_sha256_before"] == labels_hash(LABELS)


def test_refused_when_a_role_b_annotation_exists(env: tuple[Settings, db.sessionmaker[db.Session]]) -> None:  # fmt: skip
    _, factory = env
    with db.session_scope(factory) as s:
        s.add(Annotation(scenario_id="s001", annotator_role="B", labels={}))  # even a draft
    with pytest.raises(ReopenRefused, match="role-B"):
        _reopen(env)
    assert not (env[0].cases_dir / AUDIT_FILE).exists()


def test_refused_when_an_adjudication_exists(env: tuple[Settings, db.sessionmaker[db.Session]]) -> None:  # fmt: skip
    _, factory = env
    with db.session_scope(factory) as s:
        s.add(Adjudication(kind="scenario_label", ref_id="s001:E1", decision={}, by="adj"))
    with pytest.raises(ReopenRefused, match="adjudication"):
        _reopen(env)


def test_refused_without_name_reason_or_submission(env: tuple[Settings, db.sessionmaker[db.Session]]) -> None:  # fmt: skip
    with pytest.raises(ReopenRefused, match="BY"):
        _reopen(env, by=" ")
    with pytest.raises(ReopenRefused, match="REASON"):
        _reopen(env, reason="")
    _reopen(env)
    with pytest.raises(ReopenRefused, match="no submitted annotation"):
        _reopen(env)  # already a draft
    with pytest.raises(ReopenRefused, match="no scenario"):
        _reopen(env, sid="nope")
