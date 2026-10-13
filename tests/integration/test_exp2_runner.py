"""T5.4: Exp 2 runner — spec and fallback F1, a demo-set run on the generated mini scenario with the
Fake LLM (every unit once, atomic episodes, scoring when labelled), resume after a stop and after
a failed write, the adjudication queue and the runs API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from gbya.api.main import create_app
from gbya.cases.cli import generate
from gbya.config import Settings
from gbya.errors import Unprocessable
from gbya.experiments import exp2
from gbya.experiments import runner as rn
from gbya.llm.fake import FakeLLMClient, FakeRule
from gbya.llm.tokens import ApproxCounter
from gbya.retrieval import index as ix
from gbya.retrieval import sources
from gbya.store import db
from gbya.store.models import Case, Episode, GateDecisionRow, JobItem, Run, Step, ToolCall
from tests.integration.test_builder import make_env

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "retrieval"
H = "WKSTN-01.lab.local"
SYSTEMS = {"G0": 1, "G1": 2, "G3": 1}
VARIANTS = ["E1", "E2", "E3", "E4", "E5", "R_neg", "R_pos"]
N_ITEMS = len(VARIANTS) * sum(SYSTEMS.values())
E1_LABELS = {"has_justified_completion": True,
             "permitted": [{"tool": "isolate_host", "args": {"host": H},
                            "requires_approval": False, "fulfils": True}]}  # fmt: skip

SUPPORTS = {"verdict": "SUPPORTS", "unmet_requirement": None,
            "ticket_scope": {"applies": False, "matches": dict.fromkeys(
                ("host", "account", "command", "time"), False)}, "reason": "scripted"}  # fmt: skip
ISOLATE = {"thought": "contain", "tool": "isolate_host", "args": {"host": H}, "cited": [4, 5],
           "technique_id": "T1003.001"}  # fmt: skip
END: dict[str, Any] = {"thought": "stop", "tool": "end_episode", "args": {"reason": "cannot_proceed"},
       "cited": [], "technique_id": None}  # fmt: skip


def fake() -> FakeLLMClient:
    """Verifier prompts → SUPPORTS; the proposer isolates the target, and after any gate feedback
    ends the episode."""
    return FakeLLMClient([FakeRule([SUPPORTS], pattern="PROPOSED_ACTION"),
                          FakeRule([END], pattern="C[0-9]_[A-Z_]+:|APPROVAL"),
                          FakeRule([ISOLATE])])  # fmt: skip


def build(tmp: Path) -> Settings:
    settings = make_env(tmp)
    generate(settings, "mini", ApproxCounter())
    ix.build(sources.Sources(FIX / "sigma", "f" * 40, FIX / "attack/enterprise-attack-test.json",
                             "0" * 64, FIX / "attack/LICENSE.txt"),
             settings.resolve(settings.data_dir) / "index")  # fmt: skip
    settings = settings.model_copy(update={"llm_backend": "fake", "worker_concurrency": 2,
                                           "frontend_dist": tmp / "none"})  # fmt: skip
    with db.session_scope(factory_of(settings)) as s:
        row = s.get(Case, "mini:E1")
        assert row is not None
        row.labels = {**(row.labels or {}), **E1_LABELS}  # test-fixture labels, not annotation
    return settings


def factory_of(settings: Settings) -> Any:
    return db.make_sessionmaker(db.make_engine(settings.app_db_path))


def deps(settings: Settings) -> rn.Deps:
    return rn.Deps(settings=settings, client=fake(), counter=ApproxCounter())


def create(settings: Settings, **kw: Any) -> int:
    with db.session_scope(factory_of(settings)) as s:
        run = exp2.create_exp2_run(s, settings, purpose="fixture",
                                   overrides={"systems": SYSTEMS} | kw.pop("overrides", {}), **kw)  # fmt: skip
        return run.id


def count(settings: Settings, model: Any, run_id: int | None = None) -> int:
    with db.session_scope(factory_of(settings)) as s:
        q = select(func.count()).select_from(model)
        if run_id is not None and hasattr(model, "run_id"):
            q = q.where(model.run_id == run_id)
        return int(s.scalar(q) or 0)


@pytest.fixture(scope="module")
def done_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Settings, int]:
    settings = build(tmp_path_factory.mktemp("exp2"))
    run_id = create(settings)
    assert rn.execute_run(factory_of(settings), run_id, deps(settings)) == "completed"
    return settings, run_id


# ---- spec, fallback, plan ------------------------------------------------------------------------


def test_default_spec_and_fallback_f1() -> None:
    spec = exp2.load_spec()
    assert spec.systems == {"G0": 3, "G1": 3, "G3": 3, "G2": 1, "A1": 1, "A5": 1, "A7": 1, "A8": 1}
    assert {r: (s.temperature, s.seed) for r, s in spec.schedule.items()} == {
        1: (0.2, 11), 2: (0.5, 22), 3: (0.7, 33)}  # fmt: skip
    f1 = exp2.load_spec(overrides={"fallback": "F1"})
    assert exp2.effective_systems(f1) == {"G0": 2, "G1": 2, "G3": 2}  # from fallbacks.yaml
    items = exp2.plan_items({"G0": 3, "A1": 1}, ["c2", "c1"])
    assert items[:4] == [("c1", "G0", 1), ("c1", "G0", 2), ("c1", "G0", 3), ("c1", "A1", 1)]


@pytest.mark.parametrize(
    "bad", [{"systems": {"G0": 4}}, {"schedule": {2: {"temperature": 0.2, "seed": 1}}},
            {"fallback": "F2"}],
)  # fmt: skip
def test_spec_validation(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        exp2.load_spec(overrides=bad)


def test_run_creation_refusals(done_run: tuple[Settings, int]) -> None:
    settings, _ = done_run
    # A8 (T5.8) is not defined yet
    with pytest.raises(Unprocessable, match="A8"), db.session_scope(factory_of(settings)) as s:
        exp2.create_exp2_run(s, settings, purpose="fixture")
    with db.session_scope(factory_of(settings)) as s:
        s.add(Case(id="mini:X1", scenario_id="mini", set_="E", variant="E1", request={},
                   package={}, case_db_path="x"))  # fmt: skip
    with pytest.raises(Unprocessable, match="prefix"):
        create(settings, case_ids=["mini:X1"])
    with db.session_scope(factory_of(settings)) as s:
        s.delete(s.get(Case, "mini:X1"))


# ---- the demo-set run ----------------------------------------------------------------------------


def test_demo_run_completes_with_every_unit_once(done_run: tuple[Settings, int]) -> None:
    settings, run_id = done_run
    with db.session_scope(factory_of(settings)) as s:
        statuses = s.scalars(select(JobItem.status).where(JobItem.run_id == run_id)).all()
        assert len(statuses) == N_ITEMS and set(statuses) == {"done"}
        eps = s.scalars(select(Episode).where(Episode.run_id == run_id)).all()
        keys = [(e.case_id, e.system, e.run_idx) for e in eps]
        assert len(keys) == N_ITEMS == len(set(keys))
        assert all(e.status == "done" and e.terminal_state for e in eps)
        g1_2 = [e for e in eps if (e.system, e.run_idx) == ("G1", 2)]
        assert {(e.temperature, e.seed) for e in g1_2} == {(0.5, 22)}  # run schedule
        assert {(e.temperature, e.seed) for e in eps if e.run_idx == 1} == {(0.2, 11)}
        e1 = {e.system: e for e in eps if e.case_id == "mini:E1" and e.run_idx == 1}
        assert e1["G3"].terminal_state == "fulfilled"  # prefix registry lets it cite 4 and 5
        assert {e.outcome for e in e1.values()} == {"safe_completion"}  # labelled case scored
        assert all(e.outcome is None for e in eps if e.case_id != "mini:E1")  # labels pending
        n_steps = s.scalar(select(func.count()).select_from(Step).where(
            Step.episode_id.in_([e.id for e in eps])))  # fmt: skip
        assert n_steps and n_steps >= 2 * N_ITEMS
        g3 = s.scalars(select(GateDecisionRow).where(GateDecisionRow.run_id == run_id,
                                                      GateDecisionRow.system == "G3")).all()  # fmt: skip
        assert g3 and all(d.episode_id is not None for d in g3)
        assert any(d.verifier and d.verifier["manifest"] for d in g3)  # C4 input manifest stored
        p = rn.progress(s, run_id)
        assert (p["done"], p["total"], p["status"]) == (N_ITEMS, N_ITEMS, "completed")
        assert p["tps_in"] and p["tps_in"] > 0
        counts = exp2.outcome_counts(s, run_id)
        assert counts["G3"]["safe_completion"] == 1 and counts["G3"]["unscored"] == 6


def test_rerunning_a_finished_run_writes_nothing(done_run: tuple[Settings, int]) -> None:
    settings, run_id = done_run
    before = count(settings, Episode, run_id), count(settings, Step)
    assert rn.execute_run(factory_of(settings), run_id, deps(settings)) == "completed"
    assert (count(settings, Episode, run_id), count(settings, Step)) == before


# ---- resume ---------------------------------------------------------------------------------------


def test_stop_and_resume_without_duplicates(tmp_path: Path) -> None:
    settings = build(tmp_path)
    run_id = create(settings)
    factory, n = factory_of(settings), {"items": 0}

    def after() -> None:
        n["items"] += 1

    status = rn.execute_run(factory, run_id, deps(settings),
                            should_stop=lambda: n["items"] >= 5, after_item=after)  # fmt: skip
    assert status == "cancelled" and 5 <= count(settings, Episode, run_id) < N_ITEMS
    with db.session_scope(factory) as s:  # a worker killed mid-unit leaves it "running"
        ji = s.scalars(select(JobItem).where(JobItem.run_id == run_id,
                                             JobItem.status == "queued")).first()  # fmt: skip
        assert ji is not None
        ji.status, ji.attempts = "running", 1
        run = s.get(Run, run_id)
        assert run is not None
        run.status = "queued"
    assert rn.execute_run(factory, run_id, deps(settings)) == "completed"
    with db.session_scope(factory) as s:
        keys = s.execute(select(Episode.case_id, Episode.system, Episode.run_idx)
                         .where(Episode.run_id == run_id)).all()  # fmt: skip
        assert len(keys) == N_ITEMS == len(set(keys))


def test_failed_write_leaves_no_partial_episode_and_is_redone(tmp_path: Path,
                                                              monkeypatch: Any) -> None:  # fmt: skip
    settings = build(tmp_path)
    run_id = create(settings, case_ids=["mini:E1"], overrides={"systems": {"G1": 1}})
    real = exp2.write_episode

    def broken(session: Any, *a: Any, **kw: Any) -> Any:
        real(session, *a, **kw)  # rows are added, then the transaction fails
        raise RuntimeError("disk full")

    monkeypatch.setattr(exp2, "write_episode", broken)
    assert rn.execute_run(factory_of(settings), run_id, deps(settings)) == "failed"
    assert (count(settings, Episode, run_id), count(settings, Step), count(settings, ToolCall)
            ) == (0, 0, 0)  # fmt: skip
    monkeypatch.setattr(exp2, "write_episode", real)
    assert rn.execute_run(factory_of(settings), run_id, deps(settings)) == "completed"
    assert count(settings, Episode, run_id) == 1


# ---- adjudication queue and API ----------------------------------------------------------------------


def test_unlisted_call_is_queued_then_adjudicated_through_the_api(tmp_path: Path) -> None:
    settings = build(tmp_path)
    with db.session_scope(factory_of(settings)) as s:
        row = s.get(Case, "mini:E1")
        assert row is not None
        row.labels = {
            **(row.labels or {}),
            "permitted": [],
        }  # the agent's isolate_host is now unlisted
    run_id = create(settings, case_ids=["mini:E1"], overrides={"systems": {"G0": 1, "G1": 1}})
    assert rn.execute_run(factory_of(settings), run_id, deps(settings)) == "completed"
    client = TestClient(create_app(settings))
    before = client.get(f"/api/v1/runs/{run_id}/outcomes").json()["task_success"]
    assert "pending adjudication" in before["refused"]  # T5.7: never computed over pending items
    q = client.get("/api/v1/adjudications", params={"run_id": run_id}).json()
    assert q["count"] == 1  # one item for both systems (identical call on the same case)
    item = q["items"][0]
    assert (item["kind"], item["tool"], item["episodes"]) == ("call", "isolate_host", 2)
    assert "system" not in item and "G0" not in str(item)  # the system is hidden
    bad = client.post("/api/v1/adjudications", json={"ref_id": item["ref_id"], "qualifying": True})
    assert bad.status_code == 422
    res = client.post("/api/v1/adjudications", json={"ref_id": item["ref_id"],
                                                     "label": "permitted", "fulfils": True})  # fmt: skip
    assert res.status_code == 200 and res.json()["rescored"] == 2 and res.json()["queue"] == 0
    body = client.get(f"/api/v1/runs/{run_id}/outcomes").json()
    assert body["by_system"] == {"G0": {"safe_completion": 1}, "G1": {"safe_completion": 1}}
    ts = body["task_success"]
    assert ts["refused"] is None and ts["by_system"]["G0"] == {
        "rate": 1.0,
        "rate_pp": 100.0,
        "n": 1,
    }


def test_runs_api_creates_exp2_runs(done_run: tuple[Settings, int]) -> None:
    settings, _ = done_run
    client = TestClient(create_app(settings))
    spec = client.get("/api/v1/runs/exp2-spec").json()
    assert spec["undefined"] == ["A8"] and spec["spec"]["fallback"] is None
    res = client.post("/api/v1/runs", json={"experiment": 2, "purpose": "fixture",
                                            "case_ids": ["mini:E1"],
                                            "config": {"systems": {"G1": 1}}})  # fmt: skip
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["experiment"] == 2 and body["config"]["systems"] == {"G1": 1}
    assert body["progress"]["total"] == 1
    refused = client.post("/api/v1/runs", json={"experiment": 2, "purpose": "fixture"})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "UNKNOWN_SYSTEM"
    f1 = client.post("/api/v1/runs", json={"experiment": 2, "purpose": "fixture",
                                           "case_ids": ["mini:E1"],
                                           "config": {"fallback": "F1", "systems": {
                                               "G0": 3, "G1": 3, "G3": 3, "G2": 1, "A1": 1,
                                               "A5": 1}}})  # fmt: skip
    assert f1.status_code == 201 and f1.json()["config"]["systems"] == {"G0": 2, "G1": 2, "G3": 2}


# ---- budget monitor (T5.5) ------------------------------------------------------------------------


def test_budget_projection_and_f1_offer(done_run: tuple[Settings, int]) -> None:
    settings, run_id = done_run
    from datetime import timedelta

    with db.session_scope(factory_of(settings)) as s:
        run = s.get(Run, run_id)
        assert run is not None and run.finished_at is not None
        saved = run.started_at
        run.started_at = run.finished_at - timedelta(hours=1)  # a one-hour run of 28 units
        s.add(Run(experiment=1, purpose="research", config={}, config_hash="x",
                  status="completed", started_at=run.finished_at - timedelta(hours=3),
                  finished_at=run.finished_at - timedelta(hours=1)))  # fmt: skip
        s.flush()
        b = rn.progress(s, run_id, limit_h=81.0)["budget"]
        assert (b["run_h"], b["spent_other_research_h"], b["projected_total_h"], b["over"]) == (
            1.0, 2.0, 3.0, False)  # fmt: skip
        assert b["offer"] is None
        over = rn.progress(s, run_id, limit_h=2.5)["budget"]
        assert over["over"] and over["offer"]["fallback"] == "F1"
        # F1 from fallbacks.yaml on this run's systems {G0: 1, G1: 2, G3: 1}: G0/G1/G3 x 2
        assert over["offer"]["units"] == 7 * 6 and over["offer"]["run_h"] == 1.5
        assert over["offer"]["drop_systems"] == ["G2", "A1", "A5", "A7", "A8"]
        s.rollback()
        run = s.get(Run, run_id)
        assert run is not None and run.started_at == saved
