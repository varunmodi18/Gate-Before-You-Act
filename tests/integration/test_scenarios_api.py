"""T4.5: Scenario Studio API — create from a window, edit, generate (CLI subprocess), validate,
case detail with prefix and database diff."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gbya.api.deps import get_deps
from gbya.api.main import create_app
from gbya.config import Settings
from gbya.experiments.runner import Deps
from gbya.llm.fake import FakeLLMClient
from gbya.llm.tokens import ApproxCounter
from gbya.store import db
from gbya.store.models import Scenario, Window
from tests.integration.test_builder import WID, make_env


@pytest.fixture
def api(tmp_path: Path) -> tuple[TestClient, Settings]:
    settings = make_env(tmp_path)
    factory = db.make_sessionmaker(db.make_engine(settings.app_db_path))
    with db.session_scope(factory) as s:
        w = s.get(Window, WID)
        assert w is not None
        w.hosts = ["WKSTN-01.lab.local", "DC-01.lab.local", "HR001.lab.local"]
        w.techniques = ["T1003.001"]
    app = create_app(settings)
    app.dependency_overrides[get_deps] = lambda: Deps(settings=settings, client=FakeLLMClient(),
                                                      counter=ApproxCounter())  # fmt: skip
    return TestClient(app), settings


def test_create_from_window_gives_a_valid_starter(api: tuple[TestClient, Settings]) -> None:
    client, settings = api
    r = client.post("/api/v1/scenarios", json={"id": "s900", "window_id": WID})
    assert r.status_code == 201, r.text
    sc = r.json()["scenario"]
    assert sc["target_host"] == "WKSTN-01.lab.local" and sc["e1"]["cited"] == [1]
    assert sc["e1"]["technique_claimed"] == "T1003.001"
    assert [a["host"] for a in sc["trusted_context"]["assets"]] == [
        "WKSTN-01.lab.local",
        "DC-01.lab.local",
        "HR001.lab.local",
    ]
    assert (settings.cases_dir / "s900" / "scenario.json").is_file()
    assert (
        client.post("/api/v1/scenarios", json={"id": "s900", "window_id": WID}).json()["error"][
            "code"
        ]
        == "SCENARIO_EXISTS"
    )
    assert (
        client.post("/api/v1/scenarios", json={"id": "s901", "window_id": "NOPE"}).status_code
        == 404
    )
    assert (
        client.post("/api/v1/scenarios", json={"id": "Bad Id", "window_id": WID}).status_code == 422
    )
    ids = [s["id"] for s in client.get("/api/v1/scenarios").json()]
    assert ids == ["mini", "s900"]


def test_save_validates_against_the_schema(api: tuple[TestClient, Settings]) -> None:
    client, settings = api
    sc = client.get("/api/v1/scenarios/mini").json()["scenario"]
    bad = {**sc, "target_host": "NOPE"}
    r = client.put("/api/v1/scenarios/mini", json=bad)
    assert r.status_code == 422 and r.json()["error"]["code"] == "SCENARIO_INVALID"
    assert "not in the asset inventory" in json.dumps(r.json()["error"]["details"])
    good = {**sc, "notes": "edited"}
    assert client.put("/api/v1/scenarios/mini", json=good).status_code == 200
    assert (
        json.loads((settings.cases_dir / "mini" / "scenario.json").read_text())["notes"] == "edited"
    )


def test_generate_validate_and_case_detail(api: tuple[TestClient, Settings]) -> None:
    client, _ = api
    rep = client.post("/api/v1/scenarios/mini/generate")
    assert rep.status_code == 200, rep.text
    assert rep.json()["variants"] == ["E1", "E2", "E3", "E4", "E5", "R_pos", "R_neg"]
    detail = client.get("/api/v1/scenarios/mini").json()
    assert [c["variant"] for c in detail["cases"]] == [
        "E1",
        "E2",
        "E3",
        "E4",
        "E5",
        "R_neg",
        "R_pos",
    ]
    val = client.post("/api/v1/scenarios/mini/validate").json()
    assert val["valid"] is True and val["complete"] is False
    e3 = client.get("/api/v1/scenarios/mini/cases/E3").json()
    changed = {c["record_id"]: c["fields"] for c in e3["diff"]["changed"]}
    assert set(changed) == {1, 4, 5, 7, 8, 10, 13, 14, 17, 18}
    assert changed[5]["host"] == ["WKSTN-01.lab.local", "HR001.lab.local"]
    e4 = client.get("/api/v1/scenarios/mini/cases/E4").json()
    assert e4["diff"]["removed"] == [5, 7] and e4["diff"]["added"] == []
    e1 = client.get("/api/v1/scenarios/mini/cases/E1").json()
    assert e1["diff"] is None and set(e1["case"]["package"]["cited"]) <= set(
        e1["prefix"]["retrieved"]
    )
    assert client.get("/api/v1/scenarios/mini/cases/E9").status_code == 404


def test_generate_failure_is_reported_and_frozen_scenarios_are_read_only(
    api: tuple[TestClient, Settings],
) -> None:
    client, settings = api
    sc = client.get("/api/v1/scenarios/mini").json()["scenario"]
    client.put(
        "/api/v1/scenarios/mini", json={**sc, "e1": {**sc["e1"], "cited": list(range(1, 26))}}
    )
    r = client.post("/api/v1/scenarios/mini/generate")  # E2 cannot find enough benign records
    assert r.status_code == 422 and r.json()["error"]["code"] == "GENERATE_FAILED"
    assert any("benign records" in line for line in r.json()["error"]["details"]["output"])
    factory = db.make_sessionmaker(db.make_engine(settings.app_db_path))
    with db.session_scope(factory) as s:
        s.add(Scenario(id="mini", window_id=WID, status="frozen"))
    assert (
        client.put("/api/v1/scenarios/mini", json=sc).json()["error"]["code"] == "SCENARIO_FROZEN"
    )
    assert (
        client.post("/api/v1/scenarios/mini/generate").json()["error"]["code"] == "SCENARIO_FROZEN"
    )
    shutil.rmtree(settings.cases_dir / "mini")
    assert client.get("/api/v1/scenarios/mini").status_code == 404
