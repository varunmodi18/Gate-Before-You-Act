"""Run purpose: fixture/development runs can never be research results (team decision 8 Oct)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from gbya.experiments.runs import ResearchRunRefused, create_run, research_eligible
from gbya.store import db
from gbya.store.models import Case, Run, Scenario, Window


@pytest.fixture
def factory(tmp_path: Path) -> Any:
    path = tmp_path / "app.db"
    db.upgrade(path)
    f = db.make_sessionmaker(db.make_engine(path))
    with db.session_scope(f) as s:
        s.add(Window(id="W", title="w"))
        s.flush()
        s.add(Scenario(id="hm", window_id="W"))
        s.flush()
        s.add(Case(id="hm:E1", scenario_id="hm", set_="E", variant="E1", request={}, package={},
                   content_hash="h-e1"))  # fmt: skip
        s.add(Case(id="hm:E3", scenario_id="hm", set_="E", variant="E3", request={}, package={},
                   content_hash="h-e3"))  # fmt: skip
    return f


def _create(s: Any, purpose: str, frozen: Path, **kw: Any) -> Run:
    return create_run(s, experiment=1, config={}, config_hash="c", purpose=purpose,  # type: ignore[arg-type]
                      case_ids=["hm:E1", "hm:E3"], frozen_path=frozen, **kw)  # fmt: skip


def test_fixture_run_is_stored_but_never_research(factory: Any, tmp_path: Path) -> None:
    with db.session_scope(factory) as s:
        run = _create(s, "fixture", tmp_path / "none.json")
        assert run.purpose == "fixture" and not research_eligible(run)


def test_research_refused_before_the_freeze(factory: Any, tmp_path: Path) -> None:
    with (
        db.session_scope(factory) as s,
        pytest.raises(ResearchRunRefused, match="No frozen case set"),
    ):
        _create(s, "research", tmp_path / "none.json")


def test_research_refused_for_cases_outside_the_frozen_set(factory: Any, tmp_path: Path) -> None:
    frozen = tmp_path / "FROZEN.json"
    frozen.write_text(json.dumps({"case_set_hash": "set1", "content_hashes": ["h-e1"]}))
    with db.session_scope(factory) as s, pytest.raises(ResearchRunRefused) as exc:
        _create(s, "research", frozen)
    assert exc.value.details["cases"] == ["hm:E3"]


def test_research_refused_for_replay(factory: Any, tmp_path: Path) -> None:
    frozen = tmp_path / "FROZEN.json"
    frozen.write_text(json.dumps({"case_set_hash": "set1", "content_hashes": ["h-e1", "h-e3"]}))
    with db.session_scope(factory) as s, pytest.raises(ResearchRunRefused, match="Replay"):
        _create(s, "research", frozen, replay=True)


def test_research_accepted_only_when_every_case_is_frozen(factory: Any, tmp_path: Path) -> None:
    frozen = tmp_path / "FROZEN.json"
    frozen.write_text(json.dumps({"case_set_hash": "set1", "content_hashes": ["h-e1", "h-e3"]}))
    with db.session_scope(factory) as s:
        run = _create(s, "research", frozen)
        assert run.case_set_hash == "set1" and research_eligible(run)


def test_purpose_is_constrained_in_the_database(factory: Any) -> None:
    with pytest.raises(IntegrityError), db.session_scope(factory) as s:
        s.add(Run(experiment=1, config={}, config_hash="c", purpose="final-results"))


def test_default_purpose_is_development(factory: Any) -> None:
    with db.session_scope(factory) as s:
        run = Run(experiment=1, config={}, config_hash="c")
        s.add(run)
        s.flush()
        assert run.purpose == "development" and not research_eligible(run)
