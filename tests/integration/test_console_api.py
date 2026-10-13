"""T5.6: Agent Console and Traces API on the generated mini scenario with the Fake profile (its
scripted agent recovers on E2: INSUFFICIENT → recovery query → re-cite → admitted)."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gbya.api.main import create_app
from gbya.store.models import ERROR_TAGS
from tests.integration.test_exp2_runner import build

API = "/api/v1/episodes"


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    return TestClient(create_app(build(tmp_path_factory.mktemp("console"))))


def wait(client: TestClient, eid: int, until: Any = None, timeout: float = 30) -> dict[str, Any]:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        ep: dict[str, Any] = client.get(f"{API}/{eid}").json()
        if until(ep) if until else ep["status"] != "running":
            return ep
        time.sleep(0.05)
    raise AssertionError(f"episode {eid} did not reach the state in {timeout} s")


def start(client: TestClient, case: str, system: str, **kw: Any) -> int:
    res = client.post(API, json={"case_id": case, "system": system, **kw})
    assert res.status_code == 201, res.text
    return int(res.json()["episode_id"])


def test_cases_systems_and_schedule(client: TestClient) -> None:
    cases = {c["case_id"]: c for c in client.get(f"{API}/cases").json()}
    assert "mini:E2" in cases and cases["mini:E1"]["labelled"] and not cases["mini:E2"]["labelled"]
    systems = {s["id"]: s for s in client.get(f"{API}/systems").json()}
    assert systems["G3"]["unavailable"] is None and systems["A5"]["recovery_budget"] == 0
    assert client.get(f"{API}/schedule").json()["2"] == {"temperature": 0.5, "seed": 22}


def test_recovery_episode_streams_and_is_traced(client: TestClient) -> None:
    eid = start(client, "mini:E2", "G3")
    ep = wait(client, eid)
    assert (ep["status"], ep["terminal_state"], ep["run_id"]) == ("done", "fulfilled", None)
    assert (ep["temperature"], ep["seed"]) == (0.2, 11) and ep["outcome"] is None  # unlabelled
    verdicts = [s["payload"]["decision"]["verdict"] for s in ep["steps"] if s["kind"] == "gate"]
    assert verdicts == ["insufficient", "admitted", "admitted"]  # isolate, query, re-cited isolate
    assert [c["tool"] for c in ep["tool_calls"]] == ["isolate_host", "sql_query", "isolate_host"]

    with client.stream("GET", f"{API}/{eid}/stream") as res:
        body = "".join(res.iter_text())
    events = [line.split(": ", 1)[1] for line in body.splitlines() if line.startswith("event:")]
    assert events.count("gate") == 3 and events[-1] == "outcome"
    last = json.loads([ln for ln in body.splitlines() if ln.startswith("data:")][-1][6:])
    assert last["terminal_state"] == "fulfilled" and last["gate_decisions"] == 2  # state-changing

    listed = client.get(API, params={"case_id": "mini:E2"}).json()
    assert listed[0]["id"] == eid and listed[0]["tags"] == []


def test_labelled_case_is_scored_with_the_rule(client: TestClient) -> None:
    eid = start(client, "mini:E1", "G1")  # the fake cites E2's records: G1 has no C2 or C3
    ep = wait(client, eid)
    assert ep["terminal_state"] == "fulfilled" and ep["outcome"] == "safe_completion"
    assert ep["rule"] == "3" and "safe completion" in ep["rule_text"]


def test_interactive_approval_by_a_human(client: TestClient) -> None:
    eid = start(client, "mini:R_neg", "G1", interactive_approval=True)
    ep = wait(client, eid, until=lambda e: e["pending_approval"] is not None)
    assert "isolate_host" in ep["pending_approval"]["call"] and ep["interactive_approval"]
    res = client.post(f"{API}/{eid}/approval", json={"decision": "grant"})
    assert res.status_code == 200
    ep = wait(client, eid)
    approvals = [s["payload"] for s in ep["steps"] if s["kind"] == "approval"]
    assert [(a["via"], a["code"], a["state"]) for a in approvals] == [("c6", "GRANTED", "granted")]
    assert client.post(f"{API}/{eid}/approval", json={"decision": "deny"}).status_code == 409


def test_stop_while_waiting_for_approval(client: TestClient) -> None:
    eid = start(client, "mini:R_neg", "G1", interactive_approval=True)
    wait(client, eid, until=lambda e: e["pending_approval"] is not None)
    assert client.post(f"{API}/{eid}/stop").status_code == 200
    ep = wait(client, eid)
    assert ep["status"] == "error" and "stopped" in ep["error"] and ep["outcome"] is None
    approvals = [s["payload"] for s in ep["steps"] if s["kind"] == "approval"]
    assert [a["code"] for a in approvals] == ["NO_RESPONSE"]  # no answer: no response


def test_non_interactive_episode_uses_the_script(client: TestClient) -> None:
    ep = wait(client, start(client, "mini:R_neg", "G1"))
    approvals = [s["payload"]["code"] for s in ep["steps"] if s["kind"] == "approval"]
    assert approvals == ["NO_RESPONSE"] and not ep["interactive_approval"]  # script: unreachable


def test_tags_and_refusals(client: TestClient) -> None:
    eid = wait(client, start(client, "mini:E2", "G1"))["id"]
    res = client.put(f"{API}/{eid}/tags", json={"tags": ["wrong_target", "over_deferral"],
                                                "starred": True, "note": "n"})  # fmt: skip
    assert res.json()["tags"] == ["over_deferral", "wrong_target"]
    ep = client.get(f"{API}/{eid}").json()
    assert ep["starred"] and ep["error_tags"] == ERROR_TAGS
    bad = client.put(f"{API}/{eid}/tags", json={"tags": ["typo"]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "UNKNOWN_TAG"
    a8 = client.post(API, json={"case_id": "mini:E2", "system": "A8",
                                "interactive_approval": True})  # fmt: skip
    assert a8.status_code == 422 and a8.json()["error"]["code"] == "INTERACTIVE_NEEDS_IN_PROCESS"
    assert client.post(API, json={"case_id": "mini:E2", "system": "A9"}).status_code == 422
    assert client.post(API, json={"case_id": "nope:E1", "system": "G1"}).status_code == 404
    assert client.get(f"{API}/999999").status_code == 404
    assert client.post(f"{API}/{eid}/stop").status_code == 409  # not running


def test_console_episodes_never_belong_to_a_run(client: TestClient) -> None:
    assert all(e["run_id"] is None for e in client.get(API).json())


@pytest.fixture(autouse=True)
def _no_leftover_threads() -> Any:
    yield
    import threading

    deadline = time.monotonic() + 10
    while any(t.name.startswith("console-episode-") for t in threading.enumerate()):
        assert time.monotonic() < deadline, "a console episode thread is still running"
        time.sleep(0.05)
