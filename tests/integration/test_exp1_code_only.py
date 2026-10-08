"""T2.6: Exp 1 core with the code-only gates on the hand-made 3-case fixture."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from gbya.cases.handmade import insert_fixture
from gbya.config import REPO_ROOT, Settings
from gbya.data.catalogue import parse_metadata
from gbya.data.normalise import normalise_window
from gbya.experiments.exp1 import CODE_ONLY, comparable, load_case, run_code_only
from gbya.llm.tokens import ApproxCounter
from gbya.policy.engine import PolicyEngine
from gbya.store import db
from gbya.store.models import GateDecisionRow, Run

MINI = Path(__file__).resolve().parents[1] / "fixtures" / "mini_window"
POLICY = PolicyEngine.from_file(REPO_ROOT / "policy" / "rules.yaml")


@pytest.fixture
def setup(tmp_path: Path) -> Any:
    duck = tmp_path / "mini.duckdb"
    meta = MINI / "datasets/atomic/_metadata/SDWIN-MINI-000001.yaml"
    normalise_window(parse_metadata(meta, MINI), MINI, duck)
    app_db = tmp_path / "app.db"
    db.upgrade(app_db)
    factory = db.make_sessionmaker(db.make_engine(app_db))
    with db.session_scope(factory) as s:
        ids = insert_fixture(s, duck)
    return factory, ids, Settings(app_db_path=app_db, data_dir=tmp_path)


def _run(factory: Any, ids: list[str], settings: Settings) -> tuple[int, list[dict[str, Any]]]:
    with db.session_scope(factory) as s:
        run = Run(experiment=1, config={"systems": list(CODE_ONLY)}, config_hash="test")
        s.add(run)
        s.flush()
        cases = [load_case(s, cid, settings) for cid in ids]
        rows = run_code_only(s, cases, policy=POLICY, counter=ApproxCounter(), run_id=run.id)
        return run.id, [comparable(r) for r in rows]


def test_runs_on_the_three_case_fixture(setup: Any) -> None:
    factory, ids, settings = setup
    assert ids == ["hm:E1", "hm:E3", "hm:R_neg"]
    _, rows = _run(factory, ids, settings)
    verdicts = {(r["case_id"], r["system"]): r["verdict"] for r in rows}
    assert verdicts == {
        ("hm:E1", "G0"): "admitted", ("hm:E1", "G1"): "admitted",
        ("hm:E1", "G2"): "admitted", ("hm:E1", "A1"): "admitted",
        # wrong target (activity on the DC): only A1's target match (C3) rejects it
        ("hm:E3", "G0"): "admitted", ("hm:E3", "G1"): "admitted",
        ("hm:E3", "G2"): "admitted", ("hm:E3", "A1"): "rejected",
        # tier-0 host, approver unreachable: every gate with C6 converts it to an approval request
        ("hm:R_neg", "G0"): "admitted", ("hm:R_neg", "G1"): "converted_to_approval",
        ("hm:R_neg", "G2"): "converted_to_approval", ("hm:R_neg", "A1"): "converted_to_approval",
    }  # fmt: skip
    a1_e3 = next(r for r in rows if (r["case_id"], r["system"]) == ("hm:E3", "A1"))
    assert a1_e3["checks"][-1]["code"] == "C3_HOST_MISMATCH"  # no retry in Exp 1: rejected at once


def test_two_runs_give_identical_rows(setup: Any) -> None:
    factory, ids, settings = setup
    _, first = _run(factory, ids, settings)
    _, second = _run(factory, ids, settings)
    assert first == second and len(first) == 12


def test_rows_are_stored(setup: Any) -> None:
    factory, ids, settings = setup
    run_id, _ = _run(factory, ids, settings)
    with db.session_scope(factory) as s:
        stored = s.scalars(select(GateDecisionRow).where(GateDecisionRow.run_id == run_id)).all()
        assert len(stored) == 12 and {r.system for r in stored} == set(CODE_ONLY)
        e1 = next(r for r in stored if r.case_id == "hm:E1" and r.system == "A1")
        assert e1.call == {"tool": "isolate_host", "args": {"host": "WKSTN-01.lab.local"}}
        assert e1.cited == [5, 7] and [c["check"] for c in e1.checks] == [
            "C1",
            "C2",
            "C3",
            "C5",
            "C6",
        ]


def test_every_system_gets_the_identical_package(setup: Any) -> None:
    factory, ids, settings = setup
    _, rows = _run(factory, ids, settings)
    for cid in ids:
        packages = {(str(r["call"]), str(r["cited"])) for r in rows if r["case_id"] == cid}
        assert len(packages) == 1  # §L.4 item 5


def test_systems_with_c4_need_the_verifier(setup: Any) -> None:
    factory, ids, settings = setup
    with db.session_scope(factory) as s, pytest.raises(ValueError, match="verifier"):
        cases = [load_case(s, ids[0], settings)]
        run_code_only(s, cases, policy=POLICY, counter=ApproxCounter(), run_id=None, systems=["G3"])
