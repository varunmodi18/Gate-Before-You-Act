"""T2.7: Gate Playground API on the hand-made fixture (code-only systems)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gbya.api.main import create_app
from gbya.api.routers.playground import get_counter
from gbya.cases.handmade import insert_fixture
from gbya.config import Settings
from gbya.data.catalogue import parse_metadata
from gbya.data.normalise import normalise_window
from gbya.llm.tokens import ApproxCounter
from gbya.store import db

MINI = Path(__file__).resolve().parents[1] / "fixtures" / "mini_window"


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    tmp = tmp_path_factory.mktemp("pg")
    duck = tmp / "mini.duckdb"
    normalise_window(
        parse_metadata(MINI / "datasets/atomic/_metadata/SDWIN-MINI-000001.yaml", MINI), MINI, duck
    )
    app_db = tmp / "app.db"
    db.upgrade(app_db)
    with db.session_scope(db.make_sessionmaker(db.make_engine(app_db))) as s:
        insert_fixture(s, duck)
    app = create_app(Settings(app_db_path=app_db, data_dir=tmp, frontend_dist=tmp / "none"))
    app.dependency_overrides[get_counter] = ApproxCounter
    return TestClient(app)


def test_systems_list_marks_c4_systems_unavailable(client: TestClient) -> None:
    systems = {s["id"]: s for s in client.get("/api/v1/playground/systems").json()}
    assert {k for k, v in systems.items() if v["available"]} == {"G0", "G1", "G2", "A1"}
    assert systems["G3"]["reason"] == "needs the C4 verifier (milestone M3)"
    assert systems["A1"]["checks"] == ["C1", "C2", "C3", "C5", "C6"]


def test_cases_list(client: TestClient) -> None:
    cases = client.get("/api/v1/playground/cases").json()
    assert [c["id"] for c in cases] == ["hm:E1", "hm:E3", "hm:R_neg"]
    assert cases[1]["expected_label"] == "reject" and cases[1]["package"]["cited"] == [19, 22]


def test_wrong_target_case_matrix(client: TestClient) -> None:
    r = client.post("/api/v1/playground/gate", json={"case_id": "hm:E3", "systems": ["G1", "A1"]})
    body = r.json()
    assert r.status_code == 200 and body["expected_label"] == "reject"
    g1, a1 = body["decisions"]
    assert (g1["config_id"], g1["verdict"], g1["correct"]) == ("G1", "admitted", False)
    assert [c["check"] for c in g1["checks"]] == ["C1", "C5", "C6"]
    assert (a1["verdict"], a1["failed_check"], a1["correct"]) == ("rejected", "C3", True)
    assert a1["checks"][-1]["code"] == "C3_HOST_MISMATCH" and a1["message"].startswith(
        "C3_HOST_MISMATCH"
    )


def test_approval_conversion_is_shown(client: TestClient) -> None:
    body = client.post(
        "/api/v1/playground/gate", json={"case_id": "hm:R_neg", "systems": ["G0", "A1"]}
    ).json()
    g0, a1 = body["decisions"]
    assert g0["admitted"] and g0["correct"] is False  # G0 admits the tier-0 isolation
    assert a1["verdict"] == "converted_to_approval" and a1["correct"] is True
    assert a1["approval"]["code"] == "NO_RESPONSE" and a1["message"] == "No response from approver"


def test_each_request_starts_a_fresh_state(client: TestClient) -> None:
    payload = {"case_id": "hm:R_neg", "systems": ["A1"]}
    first = client.post("/api/v1/playground/gate", json=payload).json()["decisions"][0]
    second = client.post("/api/v1/playground/gate", json=payload).json()["decisions"][0]
    assert first["verdict"] == second["verdict"] == "converted_to_approval"  # no pending carry-over


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({"case_id": "hm:E1", "systems": ["G3"]}, 422, "VERIFIER_UNAVAILABLE"),
        ({"case_id": "hm:E1", "systems": ["G9"]}, 422, "UNKNOWN_SYSTEM"),
        ({"case_id": "nope", "systems": ["G1"]}, 404, "CASE_NOT_FOUND"),
        ({"case_id": "hm:E1", "systems": []}, 422, "VALIDATION_ERROR"),
    ],
)
def test_errors_use_the_envelope(client: TestClient, payload: dict, status: int, code: str) -> None:  # type: ignore[type-arg]
    r = client.post("/api/v1/playground/gate", json=payload)
    assert r.status_code == status and r.json()["error"]["code"] == code
