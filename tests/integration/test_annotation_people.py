"""Team lead, 14 Oct 2026: the author is annotator A; the author is never annotator B or the
adjudicator; A, B and the adjudicator are three different people. Names are honour-based
(A-12); blindness is unchanged (B never sees A's labels before both submit)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from gbya.api.deps import get_deps
from gbya.api.main import create_app
from gbya.cases.cli import generate
from gbya.cases.models import load_scenario, scenario_path
from gbya.cases.store import write_scenario
from gbya.experiments.runner import Deps
from gbya.llm.fake import FakeLLMClient
from gbya.llm.tokens import ApproxCounter
from tests.integration.test_annotate_api import labels_a, labels_b
from tests.integration.test_builder import make_env

API = "/api/v1/annotate/mini"


def client_with_author(tmp_path: Path) -> TestClient:
    settings = make_env(tmp_path)
    sc = load_scenario(scenario_path(settings.cases_dir, "mini"))
    write_scenario(settings, sc.model_copy(update={"author": "Varun"}))
    generate(settings, "mini", ApproxCounter())
    app = create_app(settings)
    app.dependency_overrides[get_deps] = lambda: Deps(settings=settings, client=FakeLLMClient(),
                                                      counter=ApproxCounter())  # fmt: skip
    return TestClient(app)


def test_author_is_a_never_b_nor_adjudicator(tmp_path: Path) -> None:
    client = client_with_author(tmp_path)
    ws = client.get(API, params={"role": "A"}).json()
    assert ws["scenario"]["author"] == "Varun"
    ok = client.put(f"{API}/A", json={"labels": labels_a(), "name": "Varun"})  # author = A
    assert ok.status_code == 200 and ok.json()["own"]["name"] == "Varun"
    nameless = client.put(f"{API}/B", json={"labels": labels_b()})
    assert nameless.json()["error"]["code"] == "NAME_REQUIRED"
    author_b = client.put(f"{API}/B", json={"labels": labels_b(), "name": " varun "})
    assert author_b.status_code == 422 and author_b.json()["error"]["code"] == "AUTHOR_NOT_ALLOWED"
    assert client.put(f"{API}/B", json={"labels": labels_b(), "name": "Aayush"}).status_code == 200
    assert client.post(f"{API}/A/submit").status_code == 200
    # blindness unchanged: B sees nothing of A before both submit
    ws_b = client.get(API, params={"role": "B"}).json()
    assert ws_b["own"]["name"] == "Aayush" and ws_b["other_submitted"] is True
    assert "Varun" not in str(ws_b["own"]) and set(ws_b) >= {"own", "other_submitted"}
    assert "other" not in ws_b  # A's labels are never part of B's workspace
    assert client.get(f"{API}/compare").status_code == 403
    assert client.post(f"{API}/B/submit").status_code == 200
    final = labels_a()
    for by, code in (("adjudicator", "NAME_REQUIRED"), ("VARUN", "AUTHOR_NOT_ALLOWED"),
                     ("aayush", "SAME_PERSON")):  # fmt: skip
        res = client.post(f"{API}/adjudicate", json={"labels": final, "by": by})
        assert res.status_code == 422 and res.json()["error"]["code"] == code, (by, res.text)
    res = client.post(f"{API}/adjudicate", json={"labels": final, "by": "Srushti"})
    assert res.status_code == 200, res.text


def test_b_cannot_be_the_same_person_as_a(tmp_path: Path) -> None:
    client = client_with_author(tmp_path)
    client.put(f"{API}/A", json={"labels": labels_a(), "name": "Varun"})
    client.put(f"{API}/B", json={"labels": labels_b(), "name": "Dhruv"})
    clash = client.put(f"{API}/A", json={"labels": labels_a(), "name": "dhruv"})
    assert clash.json()["error"]["code"] == "SAME_PERSON"
