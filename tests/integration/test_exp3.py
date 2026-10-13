"""T6.2: Exp 3 runner and metrics on the mini end-to-end fixture with the Fake profile (its
scripted investigator hypothesises T1003.001, queries lsass access and accepts records 5 and 7)."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from gbya.api.main import create_app
from gbya.cases.store import import_e2e
from gbya.config import Settings
from gbya.experiments import exp3
from gbya.experiments import runner as rn
from gbya.llm.factory import make_client
from gbya.llm.tokens import ApproxCounter
from gbya.retrieval import index as ix
from gbya.retrieval import sources
from gbya.store import db
from gbya.store.models import Case, Episode, JobItem, Step, Window
from tests.integration.test_builder import WID, make_env

FIX = Path(__file__).resolve().parents[1] / "fixtures"
CASE = "mini-e2e:X"


def factory_of(settings: Settings) -> Any:
    return db.make_sessionmaker(db.make_engine(settings.app_db_path))


@pytest.fixture(scope="module")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    tmp = tmp_path_factory.mktemp("exp3")
    s = make_env(tmp)
    (s.cases_dir / "mini-e2e").mkdir(parents=True)
    shutil.copy(FIX / "cases" / "mini-e2e" / "e2e.json", s.cases_dir / "mini-e2e" / "e2e.json")
    retrieval = FIX / "retrieval"
    ix.build(sources.Sources(retrieval / "sigma", "f" * 40,
                             retrieval / "attack/enterprise-attack-test.json", "0" * 64,
                             retrieval / "attack/LICENSE.txt"),
             s.resolve(s.data_dir) / "index")  # fmt: skip
    with db.session_scope(factory_of(s)) as ses:
        w = ses.get(Window, WID)
        assert w is not None
        w.techniques = ["T1003.001"]  # the window's OTRF metadata (ground truth)
        import_e2e(ses, s, "mini-e2e")
    return s.model_copy(update={"llm_backend": "fake", "worker_concurrency": 2,
                                "frontend_dist": tmp / "none"})  # fmt: skip


def deps(settings: Settings) -> rn.Deps:
    return rn.Deps(settings=settings, client=make_client(settings), counter=ApproxCounter())


@pytest.fixture(scope="module")
def done_run(settings: Settings) -> int:
    with db.session_scope(factory_of(settings)) as s:
        run = exp3.create_exp3_run(s, settings, purpose="fixture")
        run_id = run.id
    assert rn.execute_run(factory_of(settings), run_id, deps(settings)) == "completed"
    return run_id


def test_spec() -> None:
    spec = exp3.load_spec()
    assert spec.systems == {"G0": 2, "G1": 2, "G3": 2} and spec.experiment == 3
    assert (spec.schedule[2].temperature, spec.schedule[2].seed) == (0.5, 22)
    with pytest.raises(ValueError, match="unchanged"):
        exp3.load_spec(overrides={"fallback": "F1"})


def test_end_to_end_case_is_imported_as_set_x(settings: Settings) -> None:
    with db.session_scope(factory_of(settings)) as s:
        row = s.get(Case, CASE)
        assert row is not None and (row.set_, row.variant, row.package) == ("X", "X", {})
        assert str(row.case_db_path).endswith(f"{WID}.duckdb")  # the window's own database
    client = TestClient(create_app(settings))
    assert CASE not in [c["id"] for c in client.get("/api/v1/playground/cases").json()]
    assert all(r["id"] != "mini-e2e" for r in client.get("/api/v1/annotate").json())


def test_fixture_run_completes_with_investigation_and_response(settings: Settings,
                                                                 done_run: int) -> None:  # fmt: skip
    with db.session_scope(factory_of(settings)) as s:
        assert {j.status for j in s.scalars(select(JobItem).where(JobItem.run_id == done_run))} == {
            "done"}  # fmt: skip
        eps = s.scalars(select(Episode).where(Episode.run_id == done_run)).all()
        assert sorted((e.system, e.run_idx) for e in eps) == [
            ("G0", 1), ("G0", 2), ("G1", 1), ("G1", 2), ("G3", 1), ("G3", 2)]  # fmt: skip
        for e in eps:
            inv = e.outcome_detail["investigation"]
            assert inv["predicted_techniques"] == ["T1003.001"] and inv["findings"][0]["cited"] == [
                5,
                7,
            ]
            assert e.terminal_state == "fulfilled" and e.outcome == "safe_completion"
            assert e.outcome_detail["cited"] == [5, 7]
            steps = s.scalars(select(Step).where(Step.episode_id == e.id).order_by(Step.idx)).all()
            phases = [st.payload.get("phase") for st in steps]
            first_proposal = next(i for i, st in enumerate(steps) if st.kind == "llm"
                                  and st.payload.get("phase") is None)  # fmt: skip
            assert set(phases[:first_proposal]) == {"investigation"}  # investigation first
        g3 = [e for e in eps if e.system == "G3"]
        assert all(e.tokens_in > 0 for e in g3)


def test_report_technique_f1_and_evidence(settings: Settings, done_run: int) -> None:
    with db.session_scope(factory_of(settings)) as s:
        rep = exp3.report(s, done_run)
    for system in ("G0", "G1", "G3"):
        r = rep[system]
        assert r["episodes"] == 2 and r["technique"]["f1"] == 1.0 and r["technique"]["tp"] == 2
        assert r["evidence"]["n"] == 2 and r["evidence"]["precision"] == 1.0
        assert r["evidence"]["recall"] == pytest.approx(2 / 3)  # 5, 7 of 4, 5, 7


def test_incidental_technique_is_queued_and_adjudicated(settings: Settings, done_run: int) -> None:
    with db.session_scope(factory_of(settings)) as s:
        ep = s.scalars(select(Episode).where(Episode.run_id == done_run,
                                             Episode.system == "G1")).first()  # fmt: skip
        assert ep is not None
        detail = dict(ep.outcome_detail)
        detail["investigation"] = {**detail["investigation"],
                                   "predicted_techniques": ["T1003.001", "T1059.001"]}  # fmt: skip
        ep.outcome_detail = detail
    client = TestClient(create_app(settings))
    items = [
        i for i in client.get("/api/v1/adjudications").json()["items"] if i["kind"] == "technique"
    ]
    assert [(i["case_id"], i["tool"], i["episodes"]) for i in items] == [(CASE, "T1059.001", 1)]
    refused = client.get(f"/api/v1/runs/{done_run}/outcomes").json()["exp3"]["G1"]["technique"]
    assert "pending" in refused["refused"]
    bad = client.post(
        "/api/v1/adjudications", json={"ref_id": items[0]["ref_id"], "qualifying": True}
    )
    assert bad.status_code == 422
    ok = client.post("/api/v1/adjudications", json={"ref_id": items[0]["ref_id"],
                                                    "incidental": False})  # fmt: skip
    assert ok.status_code == 200
    g1 = client.get(f"/api/v1/runs/{done_run}/outcomes").json()["exp3"]["G1"]["technique"]
    assert (g1["tp"], g1["fp"], g1["refused"]) == (2, 1, None)
    assert [
        i for i in client.get("/api/v1/adjudications").json()["items"] if i["kind"] == "technique"
    ] == []


def test_runs_api_creates_exp3_runs_and_refuses_other_cases(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    res = client.post("/api/v1/runs", json={"experiment": 3, "purpose": "fixture",
                                            "config": {"systems": {"G1": 1}}})  # fmt: skip
    assert res.status_code == 201 and res.json()["progress"]["total"] == 1
    with db.session_scope(factory_of(settings)) as s:
        s.add(Case(id="mini-e2e:E1", scenario_id="mini-e2e", set_="E", variant="E1", request={},
                   package={}))  # fmt: skip
    bad = client.post("/api/v1/runs", json={"experiment": 3, "purpose": "fixture",
                                            "case_ids": ["mini-e2e:E1"]})  # fmt: skip
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "NOT_END_TO_END"


# ---- T6.3: the Console's window mode ------------------------------------------------------------


def wait(client: TestClient, eid: int, timeout: float = 30) -> dict[str, Any]:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        ep: dict[str, Any] = client.get(f"/api/v1/episodes/{eid}").json()
        if ep["status"] != "running":
            return ep
        time.sleep(0.05)
    raise AssertionError(f"episode {eid} did not end in {timeout} s")


def norm(kind: str, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """A step without its timings."""
    p = dict(payload)
    if "decision" in p:
        d = p["decision"] = {
            **p["decision"],
            "checks": [
                {k: v for k, v in c.items() if k != "duration_ms"} for c in p["decision"]["checks"]
            ],
        }
        if d.get("verifier_call"):
            d["verifier_call"] = {k: v for k, v in d["verifier_call"].items() if k != "ms"}
    return kind, p


def test_console_investigates_the_window_live_like_exp3(settings: Settings, done_run: int) -> None:
    app = create_app(settings)
    app.state.deps = deps(settings)  # the run's token counter (the trace records token counts)
    client = TestClient(app)
    cases = {c["case_id"]: c for c in client.get("/api/v1/episodes/cases").json()}
    assert (cases[CASE]["mode"], cases[CASE]["window_id"]) == ("window", WID)
    res = client.post("/api/v1/episodes", json={"case_id": CASE, "system": "G3"})
    assert res.status_code == 201, res.text
    ep = wait(client, int(res.json()["episode_id"]))
    assert (ep["status"], ep["terminal_state"], ep["run_id"]) == ("done", "fulfilled", None)
    assert ep["outcome"] == "safe_completion"
    assert ep["investigation"]["predicted_techniques"] == ["T1003.001"]
    assert ep["steps"][0]["payload"] == {"phase": "investigation", "tool": "profile",
                                         "content": ep["steps"][0]["payload"]["content"]}  # fmt: skip
    # the same investigator, gate, tools and loop as Exp 3: the same trace as the run's G3, run 1
    with db.session_scope(factory_of(settings)) as s:
        run_ep = s.scalars(select(Episode).where(Episode.run_id == done_run, Episode.system == "G3",
                                                 Episode.run_idx == 1)).one()  # fmt: skip
        run_steps = [norm(st.kind, st.payload) for st in s.scalars(
            select(Step).where(Step.episode_id == run_ep.id).order_by(Step.idx))]  # fmt: skip
        assert ep["investigation"] == run_ep.outcome_detail["investigation"]
    assert [norm(st["kind"], st["payload"]) for st in ep["steps"]] == run_steps
    with client.stream("GET", f"/api/v1/episodes/{ep['id']}/stream") as stream:
        body = "".join(stream.iter_text())
    assert '"phase": "investigation"' in body and "event: outcome" in body


def test_console_refuses_the_mcp_transport_on_windows(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    res = client.post("/api/v1/episodes", json={"case_id": CASE, "system": "A8"})
    assert res.status_code == 422 and res.json()["error"]["code"] in {
        "TRANSPORT_UNSUPPORTED", "SYSTEM_UNAVAILABLE"}  # fmt: skip
