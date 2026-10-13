"""T5.8: MCP tool server and A8 (plan §D.5.3, FR-29) on the generated mini scenario, Fake profile.

FR-29 parity: a fixed call sequence gives identical gate decisions and tool results in-process and
over MCP. The server exposes exactly the registry's tools (no ungated tool), every call goes
through the gate, a server crash fails the unit and the worker's retry redoes it.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from gbya.agent.mcp_client import McpDispatcher, McpServerError
from gbya.api.main import create_app
from gbya.config import Settings
from gbya.experiments import exp2
from gbya.experiments import runner as rn
from gbya.llm.factory import FAKE_HOST, make_client
from gbya.llm.tokens import ApproxCounter
from gbya.store import db
from gbya.store.models import Episode, JobItem, Step
from gbya.tools.dispatcher import CallRequest, InProcessDispatcher, tool_list
from gbya.tools.names import ALL_TOOLS
from tests.integration.test_exp2_runner import build, factory_of

CASE = "mini:E2"
VOLATILE = {"ms", "duration_ms", "call_latency_ms", "transport", "tokens_in", "tokens_out"}

# A fixed sequence covering every tool class and gate verdict reachable on this case.
LSASS_SQL = "SELECT * FROM process_access WHERE target_image LIKE '%lsass%' LIMIT 10"
DC_APPROVAL = {"action": {"tool": "isolate_host", "args": {"host": "DC-01.lab.local"}}}
SEQUENCE = [
    CallRequest("get_context", {"section": "assets"}),
    CallRequest("sql_query", {"sql": "SELECT * FROM process_access LIMIT 5"}),
    # benign citations: the fake verifier answers INSUFFICIENT
    CallRequest("isolate_host", {"host": FAKE_HOST}, [6, 12], "T1003.001", "contain"),
    CallRequest("sql_query", {"sql": LSASS_SQL}),
    CallRequest("isolate_host", {"host": "nope.lab.local"}, [5]),  # C1 unknown host
    CallRequest("request_approval", DC_APPROVAL, [5]),
    CallRequest("delete_logs", {"path": "C:/x"}),  # no such tool: rejected and counted
    CallRequest("kill_process", {"host": FAKE_HOST, "pid": 4100}, [5, 7], "T1003.001", "stop"),
    CallRequest("isolate_host", {"host": FAKE_HOST}, [5, 7], "T1003.001", "contain"),  # admitted
]


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x


@pytest.fixture(scope="module")
def settings(tmp_path_factory: pytest.TempPathFactory) -> Settings:
    return build(tmp_path_factory.mktemp("mcp"))


def deps(settings: Settings) -> rn.Deps:
    return rn.Deps(settings=settings, client=make_client(settings), counter=ApproxCounter())


def case(settings: Settings) -> exp2.Exp2Case:
    with db.session_scope(factory_of(settings)) as s:
        return exp2.load_episode_case(s, CASE, settings)


def start_mcp(settings: Settings, trace: Any = None) -> McpDispatcher:
    from gbya.agent.episode import MemoryTrace

    c = case(settings)
    return McpDispatcher.start(settings, CASE, "A8", trace or MemoryTrace(),
                               counter_name="approx-test-only",
                               initial_retrieved=list(c.prefix["retrieved"]))  # fmt: skip


def test_parity_in_process_and_mcp(settings: Settings) -> None:
    """FR-29: identical gate decisions and tool results for a fixed call sequence."""
    from gbya.agent.episode import MemoryTrace

    d = deps(settings)
    e = exp2.episode_env(case(settings), d.configs["G3"], d, factory_of(settings))
    local = InProcessDispatcher.recording(e.gate, e.env, e.tools)
    mtrace = MemoryTrace()
    remote = start_mcp(settings, mtrace)
    try:
        for req in SEQUENCE:
            a, b = local.call(req), remote.call(req)
            for out in (a, b):
                out.events = []
            assert _clean(a.to_json()) == _clean(b.to_json()), req.tool
        assert local.state() == remote.state()
    finally:
        remote.close()
        e.close()
    # the client replayed the server's trace: decisions, steps tagged with transport and latency
    verdicts = [d.verdict.value for d in mtrace.decisions]  # the state-changing calls
    assert verdicts == ["insufficient", "rejected_retryable", "admitted", "admitted"]
    steps = mtrace.kinds()
    assert all(p["transport"] == "mcp" and p["call_latency_ms"] > 0 for _k, p in steps)


def test_mcp_server_exposes_exactly_the_registry(settings: Settings) -> None:
    remote = start_mcp(settings)
    try:
        tools = remote.list_tools()
    finally:
        remote.close()
    assert [t["name"] for t in tools] == list(ALL_TOOLS)  # no end_episode, no extra tool
    assert tools == tool_list()  # same names, descriptions and model schemas


def test_no_call_bypasses_the_gate(settings: Settings) -> None:
    remote = start_mcp(settings)
    try:
        for name, args in [
            ("isolate_host", {"host": FAKE_HOST}),  # no citations
            ("isolate_host", {"host": FAKE_HOST, "cited": []}),
            ("kill_process", {"host": FAKE_HOST, "pid": 600, "cited": [5]}),  # lsass: target
            ("disable_account", {"account": "nobody", "cited": [5]}),
            ("delete_logs", {}),
        ]:
            res = remote.raw_call(name, args)
            out = res.structured_content
            assert res.is_error and out["executed"] is None, (name, out["feedback"])
            kinds = [e["kind"] for e in out["events"] if e["type"] == "step"]
            assert "gate" in kinds, name  # the gate ran in the server's handler
    finally:
        remote.close()


def test_a8_exp2_run_and_crash_recovery(settings: Settings, monkeypatch: Any) -> None:
    with db.session_scope(factory_of(settings)) as s:
        run = exp2.create_exp2_run(s, settings, purpose="fixture", case_ids=[CASE],
                                   overrides={"systems": {"A8": 1}})  # fmt: skip
        run_id = run.id
    monkeypatch.setenv("GBYA_MCP_CRASH_AFTER", "1")  # the server dies on its second call
    assert rn.execute_run(factory_of(settings), run_id, deps(settings)) == "failed"
    with db.session_scope(factory_of(settings)) as s:
        ji = s.scalars(select(JobItem).where(JobItem.run_id == run_id)).one()
        assert ji.status == "error" and "McpServerError" in (ji.error or "")
        assert s.scalar(select(func.count()).select_from(Episode)
                        .where(Episode.run_id == run_id)) == 0  # no partial episode  # fmt: skip
    monkeypatch.delenv("GBYA_MCP_CRASH_AFTER")
    with db.session_scope(factory_of(settings)) as s:  # resume: failed units get new attempts
        ji = s.scalars(select(JobItem).where(JobItem.run_id == run_id)).one()
        ji.status, ji.attempts = "queued", 0
    assert rn.execute_run(factory_of(settings), run_id, deps(settings)) == "completed"
    with db.session_scope(factory_of(settings)) as s:
        ep = s.scalars(select(Episode).where(Episode.run_id == run_id)).one()
        assert ep.terminal_state == "fulfilled" and ep.outcome_detail["transport"] == "mcp"
        steps = s.scalars(select(Step).where(Step.episode_id == ep.id)).all()
        assert any(st.payload.get("transport") == "mcp" for st in steps)


def test_server_crash_raises_a_clear_error(settings: Settings, monkeypatch: Any) -> None:
    monkeypatch.setenv("GBYA_MCP_CRASH_AFTER", "1")
    remote = start_mcp(settings)
    try:
        remote.call(SEQUENCE[0])
        with pytest.raises(McpServerError):
            remote.call(SEQUENCE[1])
    finally:
        remote.close()


def test_a8_in_the_console(settings: Settings) -> None:
    client = TestClient(create_app(settings))
    systems = {s["id"]: s for s in client.get("/api/v1/episodes/systems").json()}
    assert systems["A8"]["unavailable"] is None
    eid = client.post("/api/v1/episodes", json={"case_id": CASE, "system": "A8"}).json()[
        "episode_id"
    ]
    t0 = time.monotonic()
    while (ep := client.get(f"/api/v1/episodes/{eid}").json())["status"] == "running":
        assert time.monotonic() - t0 < 60
        time.sleep(0.1)
    assert ep["status"] == "done" and ep["terminal_state"] == "fulfilled", ep.get("error")
    assert any(s["payload"].get("transport") == "mcp" for s in ep["steps"])
