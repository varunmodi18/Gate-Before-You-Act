"""T2.7/T3.6: Gate Playground API on the hand-made fixture (code-only systems and C4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gbya.api.deps import get_deps
from gbya.api.main import create_app
from gbya.api.routers.playground import get_counter
from gbya.cases.handmade import insert_fixture
from gbya.config import Settings
from gbya.data.catalogue import parse_metadata
from gbya.data.normalise import normalise_window
from gbya.experiments.runner import Deps
from gbya.llm.fake import FakeLLMClient, FakeRule
from gbya.llm.tokens import ApproxCounter
from gbya.retrieval import index as ix
from gbya.retrieval import sources
from gbya.store import db

MINI = Path(__file__).resolve().parents[1] / "fixtures" / "mini_window"
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "retrieval"
SUPPORTS = {"verdict": "SUPPORTS", "unmet_requirement": None,
            "ticket_scope": {"applies": False, "matches": {"host": True, "account": False,
                                                           "command": False, "time": False}},
            "reason": "Records 5 and 7 show dumper.exe opening lsass."}  # fmt: skip


def make_client(tmp: Path, *, with_index: bool) -> TestClient:
    duck = tmp / "mini.duckdb"
    normalise_window(
        parse_metadata(MINI / "datasets/atomic/_metadata/SDWIN-MINI-000001.yaml", MINI), MINI, duck
    )
    app_db = tmp / "app.db"
    db.upgrade(app_db)
    with db.session_scope(db.make_sessionmaker(db.make_engine(app_db))) as s:
        insert_fixture(s, duck)
    if with_index:
        ix.build(sources.Sources(FIX / "sigma", "f" * 40, FIX / "attack/enterprise-attack-test.json",
                                 "0" * 64, FIX / "attack/LICENSE.txt"), tmp / "index")  # fmt: skip
    settings = Settings(
        app_db_path=app_db,
        data_dir=tmp,
        frontend_dist=tmp / "none",
        reranker_dir=tmp / "no-reranker",
    )
    app = create_app(settings)
    app.dependency_overrides[get_counter] = ApproxCounter
    fake = FakeLLMClient([FakeRule([SUPPORTS])])
    deps = Deps(settings=settings, client=fake, counter=ApproxCounter())
    app.dependency_overrides[get_deps] = lambda: deps
    app.state.fake = fake
    return TestClient(app)


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    return make_client(tmp_path_factory.mktemp("pg"), with_index=False)


@pytest.fixture(scope="module")
def c4_client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    return make_client(tmp_path_factory.mktemp("pg4"), with_index=True)


def test_systems_are_the_nine_exp1_configurations(client: TestClient) -> None:
    systems = {s["id"]: s for s in client.get("/api/v1/playground/systems").json()}
    assert list(systems) == ["G0", "G1", "G2", "G3", "A1", "A2", "A3", "A4", "A6"]  # no A5
    # without an index only the code-only systems and the no-reference verifier (A4) can run
    assert {k for k, v in systems.items() if v["available"]} == {"G0", "G1", "G2", "A1", "A4"}
    assert systems["G3"]["reason"] == "needs the retrieval index (make index)"
    assert systems["A1"]["checks"] == ["C1", "C2", "C3", "C5", "C6"]
    assert systems["A3"]["verifier_variant"] == "rationale"


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
        ({"case_id": "hm:E1", "systems": ["A5"]}, 422, "UNKNOWN_SYSTEM"),  # Exp 2 only
        ({"case_id": "hm:E1", "systems": ["G9"]}, 422, "UNKNOWN_SYSTEM"),
        ({"case_id": "nope", "systems": ["G1"]}, 404, "CASE_NOT_FOUND"),
        ({"case_id": "hm:E1", "systems": []}, 422, "VALIDATION_ERROR"),
    ],
)
def test_errors_use_the_envelope(client: TestClient, payload: dict, status: int, code: str) -> None:  # type: ignore[type-arg]
    r = client.post("/api/v1/playground/gate", json=payload)
    assert r.status_code == status and r.json()["error"]["code"] == code


# ---- with C4 (fixture index, scripted Fake LLM) ---------------------------------------------------


def test_c4_systems_available_with_an_index_except_rerank(c4_client: TestClient) -> None:
    systems = {s["id"]: s for s in c4_client.get("/api/v1/playground/systems").json()}
    assert {k for k, v in systems.items() if not v["available"]} == {"A6"}
    reason = systems["A6"]["reason"]  # libraries or model missing, depending on the machine
    assert reason.startswith("needs the") and "reranker" in reason


def test_g3_runs_c4_and_returns_the_verifier_panel_data(c4_client: TestClient) -> None:
    body = c4_client.post("/api/v1/playground/gate",
                          json={"case_id": "hm:E1", "systems": ["G1", "A1", "G3"]}).json()  # fmt: skip
    assert body["verifier_label"] == "SUPPORTS"
    g1, a1, g3 = body["decisions"]
    assert g1["verifier_call"] is None and a1["verifier_call"] is None
    assert g3["verdict"] == "admitted" and g3["correct"] is True
    vc = g3["verifier_call"]
    assert vc["output"]["verdict"] == "SUPPORTS" and vc["variant"] == "standard"
    assert vc["output"]["ticket_scope"]["matches"]["host"] is True
    system, user = vc["messages"]
    assert system["role"] == "system" and "CITED_RECORDS" in user["content"]
    assert vc["manifest"]["prompt_hash"] == vc["prompt_hash"]
    refs = vc["references"]
    sigma = [r for r in refs if r["kind"] == "sigma"]
    assert [r["rank"] for r in sigma] == list(range(1, len(sigma) + 1))
    assert all(r["shown"] == (r["rank"] <= 5) for r in sigma)
    assert all(r["author"] == "Test Author" and r["uri"] for r in sigma)  # DRL 1.1 attribution
    gold = {r["title"] for r in refs if r["gold"]}  # hm:E1 technique_gold T1003.001
    assert gold == {"Test LSASS Memory Access", "LSASS Memory"}


def test_c4_not_called_when_c3_rejects(c4_client: TestClient) -> None:
    fake = c4_client.app.state.fake  # type: ignore[attr-defined]
    before = len(fake.calls)
    g3 = c4_client.post("/api/v1/playground/gate",
                        json={"case_id": "hm:E3", "systems": ["G3"]}).json()["decisions"][0]  # fmt: skip
    assert g3["failed_check"] == "C3" and g3["verifier_call"] is None and len(fake.calls) == before


def test_a6_is_refused_without_the_reranker(c4_client: TestClient) -> None:
    r = c4_client.post("/api/v1/playground/gate", json={"case_id": "hm:E1", "systems": ["A6"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VERIFIER_UNAVAILABLE"
