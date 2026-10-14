"""T8.2: record the demo through the API (any client wrapped by ``record_cassette``), then replay
it without a model: the same Console episode and Playground decisions, ``/health`` reports the
mode, a call outside the recording is a ``REPLAY_MISS``, and runs on replay are flagged."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gbya.api.main import create_app
from gbya.config import Settings
from tests.integration.test_exp2_runner import build

API = "/api/v1"


def episode(client: TestClient, case: str) -> dict[str, Any]:
    eid = client.post(f"{API}/episodes", json={"case_id": case, "system": "G3"}).json()
    for _ in range(600):
        ep: dict[str, Any] = client.get(f"{API}/episodes/{eid['episode_id']}").json()
        if ep["status"] != "running":
            return ep
        time.sleep(0.05)
    raise AssertionError("episode did not end")


def steps(ep: dict[str, Any]) -> list[tuple[str, Any]]:
    out = []
    for s in ep["steps"]:
        p = dict(s["payload"])
        p.pop("prompt_tokens", None)
        d = p.get("decision")
        if d:  # timings differ between runs
            p["decision"] = {k: v for k, v in d.items() if k not in ("checks", "verifier_call")}
        out.append((s["kind"], p))
    return out


@pytest.fixture(scope="module")
def recorded(tmp_path_factory: pytest.TempPathFactory) -> tuple[Settings, Path, dict[str, Any],
                                                                 dict[str, Any]]:  # fmt: skip
    settings = build(tmp_path_factory.mktemp("rec"))
    cassette = settings.data_dir / "demo.jsonl"
    rec = settings.model_copy(update={"llm_backend": "fake", "record_cassette": cassette})
    client = TestClient(create_app(rec))
    assert client.get(f"{API}/health").json()["model"]["recording"] == str(cassette)
    ep = episode(client, "mini:E2")
    gate = client.post(f"{API}/playground/gate", json={"case_id": "mini:E5",
                                                         "systems": ["G1", "G3"]}).json()  # fmt: skip
    return settings, cassette, ep, gate


def test_recording_writes_one_line_per_model_call(recorded: Any) -> None:
    _, cassette, ep, _ = recorded
    lines = cassette.read_text().splitlines()
    llm_steps = sum(s["kind"] == "llm" for s in ep["steps"])
    assert len(lines) >= llm_steps > 0  # proposals plus the verifier calls


def test_replay_reproduces_the_demo_without_a_model(recorded: Any) -> None:
    settings, cassette, ep, gate = recorded
    rep = settings.model_copy(update={"llm_backend": "replay", "replay_cassette": cassette})
    client = TestClient(create_app(rep))
    health = client.get(f"{API}/health").json()
    assert health["model"]["backend"] == "replay" and health["model"]["status"] == "replay"
    again = episode(client, "mini:E2")
    assert (again["status"], again["terminal_state"]) == (ep["status"], ep["terminal_state"])
    assert steps(again) == steps(ep)
    g = client.post(f"{API}/playground/gate", json={"case_id": "mini:E5",
                                                      "systems": ["G1", "G3"]}).json()  # fmt: skip
    assert [d["verdict"] for d in g["decisions"]] == [d["verdict"] for d in gate["decisions"]]
    miss = episode(client, "mini:R_neg")  # never recorded
    assert miss["status"] == "error" and "No recorded response" in (miss["error"] or "")
    run = client.post(f"{API}/runs", json={"experiment": 2, "purpose": "demo",
                                            "case_ids": ["mini:E2"],
                                            "config": {"systems": {"G3": 1}}}).json()  # fmt: skip
    assert run["replay"] is True and run["research_eligible"] is False
